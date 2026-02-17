from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext


MAP_ENCODE: Dict[str, str] = {
    "Á": "ﾁ",
    "É": "ﾉ",
    "Í": "ﾍ",
    "Ó": "ﾓ",
    "Ú": "ﾚ",
    "á": "$",
    "ã": "^",
    "à": "<",
    "â": ">",
    "ç": "&",
    "é": "%",
    "ú": "(",
    "ó": ")",
    "õ": "*",
}

MAP_DECODE: Dict[str, str] = {v: k for k, v in MAP_ENCODE.items()}

# Supports optional channel prefix like "[e]." / "[j]." before ".message"
_RX_MESSAGE = re.compile(
    r"^(\s*)"
    r"(?:(\[[^\]]+\]\.)\s*)?"
    r"\.message(\s+)(\d+)(\s+)(.*?)(\r?\n)?$"
)

_RE_WS_LEAD = re.compile(r"^\s*")
_RE_WS_TAIL = re.compile(r"\s*$")

# Suffix control sequences at END of message text.
# Covers: \a, \v, \v\a, \w, \w\w..., and also \something123 style.
_RX_SUFFIX = re.compile(r"(?s)^(.*?)(\\(?:[A-Za-z]+[0-9]*))(\\(?:[A-Za-z]+[0-9]*))*?(\s*)$")

# Control-only body like "\a" or "\w\w\w\a" (optionally with whitespace)
_RX_CONTROL_ONLY = re.compile(r"^\s*(?:\\[A-Za-z]+[0-9]*)+\s*$")


def _decode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_DECODE.get(ch, ch) for ch in s)


def _encode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_ENCODE.get(ch, ch) for ch in s)


def _split_suffix(text: str) -> Tuple[str, str]:
    """
    Split trailing control sequences (like \a, \v, \v\a, \w...) from the end.
    Keeps suffix EXACTLY as in input (including any trailing spaces).
    """
    if not text:
        return text, ""

    m = _RX_SUFFIX.match(text)
    if not m:
        return text, ""

    body = m.group(1) or ""
    # suffix starts right after body
    suf = text[len(body):]
    # Ensure suffix truly ends with backslash-sequences (avoid false positives)
    # If body is identical to text, don't split.
    if not suf:
        return text, ""
    return body, suf


def _is_id_like(tok: str) -> bool:
    if not tok:
        return False
    if "-" not in tok:
        return False
    return any(ch.isdigit() for ch in tok)


def _split_lead_tail_ws(s: str) -> Tuple[str, str, str]:
    lead = _RE_WS_LEAD.match(s).group(0) if s else ""
    tail = _RE_WS_TAIL.search(s).group(0) if s else ""
    core = s[len(lead): len(s) - len(tail)]
    return lead, core, tail


def _parse_rest_prefix_speaker_and_body(rest: str) -> Tuple[str, str, str, str]:
    """
    Returns (prefix, speaker, body_raw, suffix).

    - prefix: everything (including spaces) before the body
    - speaker: best-effort speaker name (without @/#), or ""
    - body_raw: message body WITHOUT trailing suffix sequences
    - suffix: trailing suffix sequences (e.g. "\v\a", "\w\w\a") and any trailing spaces
    """
    rest_no_nl = rest.rstrip("\r\n")

    # Preserve the exact indentation inside the ".message" payload
    lead_ws = rest_no_nl[: len(rest_no_nl) - len(rest_no_nl.lstrip(" "))]
    s = rest_no_nl.lstrip(" ")

    if not s:
        return lead_ws, "", "", ""

    # Case A: id-like + speaker token + body
    #   yuk-100_01-0005 @Yuuko g...h
    #   miy-... #Miyako g...h
    m = re.match(r"^([^\s]+)(\s+)([@#]?[^\s]+)(\s+)(.*)$", s)
    if m and _is_id_like(m.group(1)):
        _id = m.group(1)
        who = m.group(3) or ""
        sp = who
        if sp.startswith("#") or sp.startswith("@"):
            sp = sp[1:]
        prefix = lead_ws + s[: m.start(5)]
        body_plus = m.group(5) or ""
        body_raw, suf = _split_suffix(body_plus)
        return prefix, sp.strip(), body_raw, suf

    # Case B: plain speaker token + body starting with engine marker (...) or quote
    #   Hiro gC-c-c-cold...h
    # (We only do this when the remainder clearly looks like a quoted/marked line.)
    m2 = re.match(r"^([A-Za-z0-9_]+)(\s+)(.*)$", s)
    if m2:
        who = m2.group(1) or ""
        rest2 = m2.group(3) or ""
        if rest2.startswith("") or rest2.startswith('"') or rest2.startswith("“") or rest2.startswith("「") or rest2.startswith("『"):
            prefix = lead_ws + s[: m2.start(3)]
            body_plus = rest2
            body_raw, suf = _split_suffix(body_plus)
            return prefix, who.strip(), body_raw, suf

    # Case C: no reliable header; body begins immediately (narration, etc.)
    prefix = lead_ws
    body_plus = s
    body_raw, suf = _split_suffix(body_plus)
    return prefix, "", body_raw, suf


class MusicaParser:
    plugin_id = "musica.sc"
    name = "Musica (.sc)"
    extensions = {".sc"}

    def detect(self, ctx: ParseContext, text: str) -> float:
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".sc":
                return 0.9
        except Exception:
            pass
        fp = str(getattr(ctx, "file_path", "") or "")
        if fp.lower().endswith(".sc"):
            return 0.9
        head = "\n".join(text.splitlines()[:80])
        if ".message" in head and ".stage" in head:
            return 0.55
        return 0.0

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        for i, line in enumerate(lines):
            s = line.lstrip()
            if s.startswith(";") or s.startswith("//"):
                continue

            m = _RX_MESSAGE.match(line)
            if not m:
                continue

            ws, chan, sp1, msgno, sp2, rest, nl = m.groups()

            prefix, speaker, body_raw, suf = _parse_rest_prefix_speaker_and_body(rest)

            # Decode exactly (do NOT strip quotes or tags/markers)
            visible = _decode_table(body_raw)

            # Skip empty or control-only messages (e.g. "\a" / "\w\w\a")
            if visible == "" or visible.strip() == "":
                continue
            if _RX_CONTROL_ONLY.match(visible):
                continue

            # Preserve body outer whitespace separately to allow stable rebuild
            body_lead, body_core, body_tail = _split_lead_tail_ws(body_raw)

            entries.append(
                {
                    "entry_id": f"{i}",
                    "original": _decode_table(body_raw),  # keep EXACT formatting/tags/spaces
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": speaker,
                    "meta": {
                        "line_index": i,
                        "ws": ws,
                        "chan": chan or "",
                        "sp1": sp1,
                        "msgno": msgno,
                        "sp2": sp2,
                        "prefix": prefix,
                        "suffix": suf,
                        "newline": nl or "",
                        "body_lead": body_lead,
                        "body_tail": body_tail,
                    },
                }
            )

        return entries

    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        by_line: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            li = None
            try:
                li = int(meta.get("line_index"))
            except Exception:
                try:
                    li = int(str(e.get("entry_id", "")).strip())
                except Exception:
                    li = None
            if li is not None and 0 <= li < len(lines):
                by_line[li] = e

        for li, e in by_line.items():
            line = lines[li]
            m = _RX_MESSAGE.match(line)
            if not m:
                continue

            ws, chan, sp1, msgno, sp2, _rest, nl = m.groups()

            meta = e.get("meta") or {}
            prefix = str(meta.get("prefix") or "")
            suf = str(meta.get("suffix") or "")
            newline = str(meta.get("newline") or (nl or ""))
            body_lead = str(meta.get("body_lead") or "")
            body_tail = str(meta.get("body_tail") or "")

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                body_txt = tr  # do NOT strip; user may want exact spacing/tags
            else:
                body_txt = str(e.get("original") or "")

            # Encode only the body text; keep original lead/tail whitespace around it
            body_txt_enc = _encode_table(body_txt)
            body_txt_enc = f"{body_lead}{body_txt_enc}{body_tail}"

            chan_s = str(meta.get("chan") or (chan or ""))

            lines[li] = f"{ws}{chan_s}.message{sp1}{msgno}{sp2}{prefix}{body_txt_enc}{suf}{newline}"

        return "".join(lines)


plugin = MusicaParser()
