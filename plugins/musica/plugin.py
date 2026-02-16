from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from parsers.base import ParseContext


CHAR_MAP_DECODE = {
    "ﾁ": "Á",
    "ﾉ": "É",
    "ﾍ": "Í",
    "ﾓ": "Ó",
    "ﾚ": "Ú",
    "$": "á",
    "^": "ã",
    "<": "à",
    ">": "â",
    "&": "ç",
    "%": "é",
    "(": "ú",
    ")": "ó",
    "*": "õ",
}

CHAR_MAP_ENCODE = {v: k for k, v in CHAR_MAP_DECODE.items()}


def decode_text(s: str) -> str:
    if not s:
        return s
    for src, dst in CHAR_MAP_DECODE.items():
        s = s.replace(src, dst)
    return s


def encode_text(s: str) -> str:
    if not s:
        return s
    for src, dst in CHAR_MAP_ENCODE.items():
        s = s.replace(src, dst)
    return s


_RX_MESSAGE = re.compile(r"^(\s*)\.message(\s+)(\d+)(\s+)(.*?)(\r?\n)?$")


_OPEN_QUOTES = {'"', "“", "「", "『"}
_CLOSE_QUOTES = {'"', "”", "」", "』"}


def _split_suffix(text: str) -> tuple[str, str]:
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


def _find_text_region(rest: str) -> tuple[str, str, str]:
    rest = rest.rstrip("\r\n")
    lead_ws = rest[: len(rest) - len(rest.lstrip(" "))]
    rest_strip = rest.lstrip(" ")

    qpos = None
    for ch in ("“", '"', "「", "『", "（", "(", "＜", "<"):
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


def _guess_speaker(rest: str) -> str:
    s = rest.strip()
    if not s:
        return ""
    toks = s.split()
    if len(toks) < 2:
        return ""
    if not _is_id_like(toks[0]):
        return ""
    sp = toks[1].lstrip("#@").strip()
    return sp


def _strip_dialog_quotes(text: str) -> tuple[str, str, str]:
    if not text:
        return "", "", ""
    t = text.strip()
    if len(t) >= 2 and t[0] in _OPEN_QUOTES and t[-1] in _CLOSE_QUOTES:
        return t[0], t[1:-1], t[-1]
    if len(t) >= 1 and t[0] in _OPEN_QUOTES:
        return t[0], t[1:], ""
    return "", t, ""


class MusicaScPlugin:
    plugin_id = "musica.sc"
    name = "Musica (.sc)"
    extensions = {".sc"}

    def detect(self, ctx: ParseContext, text: str) -> float:
        ext = ctx.path.suffix.lower()
        if ext != ".sc":
            return 0.0
        head = "\n".join(text.splitlines()[:80])
        if ".message" in head:
            return 0.9
        return 0.6

    def parse(self, ctx: ParseContext, text: str) -> List[dict]:
        entries: List[dict] = []
        lines = text.splitlines(keepends=True)

        for i, line in enumerate(lines):
            s = line.lstrip()
            if s.startswith(";") or s.startswith("//"):
                continue

            m = _RX_MESSAGE.match(line)
            if not m:
                continue

            ws, _, msgno, _, rest, nl = m.groups()
            prefix, body, suf = _find_text_region(rest)

            qopen, inner, qclose = _strip_dialog_quotes(body)
            inner = decode_text(inner)

            if inner.strip() == "":
                continue

            speaker = _guess_speaker(rest)

            entries.append(
                {
                    "entry_id": f"{i}",
                    "speaker": speaker,
                    "original": inner,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "meta": {
                        "line_index": i,
                        "msgno": msgno,
                        "ws": ws,
                        "prefix": prefix,
                        "qopen": qopen,
                        "qclose": qclose,
                        "suffix": suf,
                        "newline": nl or "",
                    },
                }
            )

        return entries

    def rebuild(self, ctx: ParseContext, entries: List[dict]) -> str:
        enc = getattr(ctx, "encoding", None) or "utf-8"
        with open(ctx.path, "rb") as f:
            data = f.read()
        try:
            text = data.decode(enc, errors="strict")
        except Exception:
            text = data.decode(enc, errors="replace")

        lines = text.splitlines(keepends=True)

        by_line: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            li = meta.get("line_index")
            if isinstance(li, int) and 0 <= li < len(lines):
                by_line[li] = e
                continue
            try:
                li2 = int(str(e.get("entry_id", "")).strip())
            except Exception:
                continue
            if 0 <= li2 < len(lines):
                by_line[li2] = e

        for li, e in by_line.items():
            meta = e.get("meta") or {}
            m = _RX_MESSAGE.match(lines[li])
            if not m:
                continue

            ws, sp1, msgno, sp2, rest, nl = m.groups()
            prefix = str(meta.get("prefix") or "")
            qopen = str(meta.get("qopen") or "")
            qclose = str(meta.get("qclose") or "")
            suf = str(meta.get("suffix") or "")
            newline = str(meta.get("newline") or (nl or ""))

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                inner = tr
            else:
                inner = e.get("original") or ""

            inner = encode_text(inner)
            body = f"{qopen}{inner}{qclose}"

            lines[li] = f"{ws}.message{sp1}{msgno}{sp2}{prefix}{body}{suf}{newline}"

        return "".join(lines)


def get_plugin():
    return MusicaScPlugin()
