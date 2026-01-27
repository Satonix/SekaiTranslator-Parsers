# plugins/artemis/plugin.py
from __future__ import annotations

import re
from typing import List, Dict, Any, Optional, Tuple

from parsers.base import ParseContext, ParserError


plugin_id = "artemis.ast"
name = "Artemis (.ast/.txt)"
extensions = {".ast", ".txt"}


# ----------------------------
# Regexes
# ----------------------------

_RE_TEXT_BLOCK = re.compile(
    r"""
    \btext\s*=\s*\{          # text = {
        (?P<body>.*?)        #   ... (non-greedy)
    \}\s*,                   # },
    """,
    re.DOTALL | re.VERBOSE,
)

def _re_lang_section(lang: str) -> re.Pattern:
    return re.compile(
        rf"""
        \b{re.escape(lang)}\s*=\s*\{{     # <lang> = {{
            (?P<body>.*?)                 #   ...
        \}}\s*,                           # }},
        """,
        re.DOTALL | re.VERBOSE,
    )

_RE_LANG_KEYS = re.compile(
    r"""
    \b(?P<key>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*\{   # key = {
    """,
    re.VERBOSE,
)

_RE_STRING = re.compile(
    r'"(?P<s>(?:[^"\\]|\\.)*)"',  # " ... " with escapes
    re.DOTALL,
)


# ----------------------------
# Escapes
# ----------------------------

def _unescape_lua(s: str) -> str:
    # best-effort
    try:
        return bytes(s, "utf-8").decode("unicode_escape")
    except Exception:
        return s.replace(r"\\", "\\").replace(r"\"", '"').replace(r"\n", "\n")


def _escape_lua(s: str) -> str:
    s = s.replace("\\", r"\\")
    s = s.replace('"', r"\"")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", r"\n")
    return s


# ----------------------------
# Core helpers
# ----------------------------

def _mk_entry(entry_id: str, original: str, meta: dict) -> Dict[str, Any]:
    return {
        "entry_id": entry_id,
        "speaker": "",
        "original": original,
        "translation": "",
        "status": "untranslated",
        "is_translatable": True,
        "meta": meta,
    }


def _get_project_lang(ctx: ParseContext) -> str:
    proj = getattr(ctx, "project", None) or {}
    if isinstance(proj, dict):
        v = (proj.get("source_language") or proj.get("source_lang") or "").strip()
        if v:
            return v
    return "ja"


def _pick_lang_for_textblock(text_body: str, preferred: str) -> str:
    keys: List[str] = []
    for m in _RE_LANG_KEYS.finditer(text_body):
        k = (m.group("key") or "").strip()
        if not k:
            continue
        # chaves que não são idiomas
        if k in {"vo", "pagebreak"}:
            continue
        keys.append(k)

    keyset = set(keys)
    if preferred in keyset:
        return preferred
    if "ja" in keyset:
        return "ja"
    if "en" in keyset:
        return "en"
    if keys:
        return keys[0]
    return preferred or "ja"


def _extract_replacements(entries: List[dict]) -> List[Tuple[int, int, str]]:
    """
    Converte entries -> lista de (start, end, replacement_text_escaped)
    start/end são offsets absolutos no ctx.original_text do CONTEÚDO dentro das aspas.
    """
    reps: List[Tuple[int, int, str]] = []
    for e in entries or []:
        meta = e.get("meta") if isinstance(e, dict) else None
        if not isinstance(meta, dict):
            continue
        if meta.get("format") != "artemis_ast_v2":
            continue

        start = meta.get("abs_start")
        end = meta.get("abs_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end < 0 or end < start:
            continue

        # decide texto final: translation se tiver, senão original
        tr = e.get("translation")
        if isinstance(tr, str) and tr.strip():
            final_text = tr
        else:
            final_text = e.get("original") if isinstance(e.get("original"), str) else ""
        reps.append((start, end, _escape_lua(final_text)))

    # importante: substituir de trás pra frente
    reps.sort(key=lambda t: t[0], reverse=True)
    return reps


# ----------------------------
# Parser
# ----------------------------

class ArtemisParser:
    plugin_id = plugin_id
    name = name
    extensions = extensions

    def detect(self, ctx: ParseContext, text: str) -> float:
        t = text or ""
        score = 0.0
        if "astver" in t and "astname" in t and "ast =" in t:
            score += 0.55
        if "block_" in t and "text =" in t:
            score += 0.25
        if re.search(r"\b(ja|en|cn)\s*=\s*\{", t):
            score += 0.20
        return min(1.0, score)

    def parse(self, ctx: ParseContext, text: str) -> List[dict]:
        entries: List[dict] = []
        if not text:
            return entries

        preferred_lang = _get_project_lang(ctx)
        entry_i = 0

        for tb in _RE_TEXT_BLOCK.finditer(text):
            text_body = tb.group("body")
            text_body_start = tb.start("body")

            lang = _pick_lang_for_textblock(text_body, preferred_lang)

            lang_re = _re_lang_section(lang)
            lang_match = lang_re.search(text_body)

            if not lang_match:
                # fallback explícito extra
                for fb in ("ja", "en"):
                    if fb == lang:
                        continue
                    fb_re = _re_lang_section(fb)
                    m2 = fb_re.search(text_body)
                    if m2:
                        lang_match = m2
                        lang = fb
                        break

            if not lang_match:
                continue

            lang_body = lang_match.group("body")
            lang_body_start = text_body_start + lang_match.start("body")

            for sm in _RE_STRING.finditer(lang_body):
                raw = sm.group("s")
                original = _unescape_lua(raw)

                abs_start = lang_body_start + sm.start("s")
                abs_end = lang_body_start + sm.end("s")

                meta = {
                    "format": "artemis_ast_v2",
                    "lang": lang,
                    "abs_start": abs_start,
                    "abs_end": abs_end,
                }

                entries.append(_mk_entry(f"artemis:{entry_i}", original, meta))
                entry_i += 1

        return entries

    def rebuild(self, ctx: ParseContext, entries: List[dict]) -> str:
        base = getattr(ctx, "original_text", None)
        if not isinstance(base, str) or not base:
            raise ParserError("Artemis rebuild requer ctx.original_text (Opção A).")

        reps = _extract_replacements(entries)
        if not reps:
            return base

        out = base
        for start, end, rep in reps:
            if end > len(out) or start > len(out):
                # arquivo mudou de tamanho/offsets inválidos
                # não aborta tudo, só ignora essa substituição
                continue
            out = out[:start] + rep + out[end:]

        return out


def get_plugin():
    return ArtemisParser()
