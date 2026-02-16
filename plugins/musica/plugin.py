from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

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

_RX_MESSAGE = re.compile(r"^(\s*)\.message(\s+)(\d+)(\s+)(.*?)(\r?\n)?$")


def _decode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_DECODE.get(ch, ch) for ch in s)


def _encode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_ENCODE.get(ch, ch) for ch in s)


def _strip_outer_quotes(s: str) -> str:
    if not s:
        return s
    t = s.strip()
    if len(t) >= 2:
        pairs = {
            ('"', '"'),
            ("“", "”"),
            ("「", "」"),
            ("『", "』"),
        }
        a, b = t[0], t[-1]
        if (a, b) in pairs:
            return t[1:-1]
    return t


def _split_suffix(text: str) -> Tuple[str, str]:
    m = re.search(r"(?s)^(.*?)(\\(?:v\\a|a|v))+(\s*)$", text)
    if not m:
        return text, ""
    body = m.group(1)
    suf = text[len(body) :]
    return body, suf


def _is_id_like(tok: str) -> bool:
    if not tok:
        return False
    if "-" not in tok:
        return False
    return any(ch.isdigit() for ch in tok)


def _guess_speaker(rest: str) -> str:
    s = rest.strip()
    if not s:
        return ""
    toks = s.split()
    if len(toks) < 2:
        return ""
    if not _is_id_like(toks[0]):
        return ""
    sp = toks[1].strip()
    if sp.startswith("#"):
        sp = sp[1:]
    if sp.startswith("@"):
        sp = sp[1:]
    sp = sp.replace("@", "").strip()
    return sp


def _find_text_region(rest: str) -> Tuple[str, str, str]:
    rest = rest.rstrip("\r\n")
    lead_ws = rest[: len(rest) - len(rest.lstrip(" "))]
    rest_strip = rest.lstrip(" ")

    qpos: Optional[int] = None
    for ch in ("“", '"', "「", "『"):
        i = rest_strip.find(ch)
        if i != -1 and (qpos is None or i < qpos):
            qpos = i

    if qpos is not None:
        prefix_strip = rest_strip[:qpos]
        text_plus = rest_strip[qpos:]
        body, suf = _split_suffix(text_plus)
        return lead_ws + prefix_strip, body, suf

    toks = rest_strip.split()
    if len(toks) >= 3 and _is_id_like(toks[0]):
        m = re.match(rf"^{re.escape(toks[0])}\s+{re.escape(toks[1])}\s+(.*)$", rest_strip)
        if m:
            prefix_strip = rest_strip[: m.start(1)]
            text_plus = m.group(1)
            body, suf = _split_suffix(text_plus)
            return lead_ws + prefix_strip, body, suf

    body, suf = _split_suffix(rest_strip)
    return lead_ws, body, suf


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

            ws, sp1, msgno, sp2, rest, nl = m.groups()
            prefix, body_raw, suf = _find_text_region(rest)

            visible = _decode_table(body_raw)
            visible = _strip_outer_quotes(visible)

            if visible.strip() == "":
                continue

            speaker = _guess_speaker(rest)

            entries.append(
                {
                    "entry_id": f"{i}",
                    "original": visible,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": speaker,
                    "meta": {
                        "line_index": i,
                        "ws": ws,
                        "sp1": sp1,
                        "msgno": msgno,
                        "sp2": sp2,
                        "prefix": prefix,
                        "suffix": suf,
                        "newline": nl or "",
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

            ws, sp1, msgno, sp2, _, nl = m.groups()

            meta = e.get("meta") or {}
            prefix = str(meta.get("prefix") or "")
            suf = str(meta.get("suffix") or "")
            newline = str(meta.get("newline") or (nl or ""))

            tr = e.get("translation")
            if isinstance(tr, str) and tr.strip():
                body = tr.strip()
            else:
                body = str(e.get("original") or "").strip()

            body = _strip_outer_quotes(body)
            body = _encode_table(body)

            lines[li] = f"{ws}.message{sp1}{msgno}{sp2}{prefix}{body}{suf}{newline}"

        return "".join(lines)


plugin = MusicaParser()
