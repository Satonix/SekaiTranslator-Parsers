# plugins/artemis/plugin.py
from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

from parsers.base import ParseContext


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

# captura uma seção genérica: <lang> = { ... },
def _re_lang_section(lang: str) -> re.Pattern:
    return re.compile(
        rf"""
        \b{re.escape(lang)}\s*=\s*\{{     # <lang> = {{
            (?P<body>.*?)                 #   ...
        \}}\s*,                           # }},
        """,
        re.DOTALL | re.VERBOSE,
    )

# tenta descobrir chaves de idioma existentes dentro de text = { ... }
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


def _unescape_lua(s: str) -> str:
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
    """
    Pega o idioma de origem do projeto.
    Aceita project['source_language'] ou project['source_lang'].
    Fallback: 'ja'
    """
    proj = getattr(ctx, "project", None) or {}
    if isinstance(proj, dict):
        v = (proj.get("source_language") or proj.get("source_lang") or "").strip()
        if v:
            return v
    return "ja"


def _pick_lang_for_textblock(text_body: str, preferred: str) -> str:
    """
    Decide qual idioma usar dentro de um text = { ... }.
    Ordem:
      1) preferred (source_language do projeto)
      2) 'ja'
      3) 'en'
      4) primeiro idioma encontrado no bloco
    """
    keys = []
    for m in _RE_LANG_KEYS.finditer(text_body):
        k = (m.group("key") or "").strip()
        if not k:
            continue
        # ignora sub-blocos que não são idiomas
        # (vo/pagebreak etc podem aparecer como keys)
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
        # qualquer presença de ja/en/cn aumenta confiança
        if re.search(r"\b(ja|en|cn)\s*=\s*\{", t):
            score += 0.20
        return min(1.0, score)

    def parse(self, ctx: ParseContext, text: str) -> List[dict]:
        """
        Extrai strings dentro de text = { ... <lang> = { ... "..." ... }, ... }
        onde <lang> = source_language do projeto (fallbacks automáticos).
        """
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
                # tenta fallback explícito se o chosen não existir por algum motivo
                for fb in ("ja", "en"):
                    if fb == lang:
                        continue
                    fb_re = _re_lang_section(fb)
                    lang_match = fb_re.search(text_body)
                    if lang_match:
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
        raise RuntimeError(
            "ArtemisParser.rebuild ainda depende do texto base.\n"
            "Se seu ParseContext já tiver ctx.original_text, eu ajusto para usar isso."
        )


def get_plugin():
    return ArtemisParser()
