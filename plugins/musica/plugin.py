from __future__ import annotations

import re
from typing import Dict, List

try:
    from parsers.base import ParseContext
except Exception:
    from sekai_ui.parsers.base import ParseContext  # type: ignore


plugin_id = "musica.sc"
name = "Musica (.sc)"
extensions = {".sc"}


_RX_MESSAGE = re.compile(r"^(\s*)\.message(\s+)(\d+)(\s+)(.*?)(\r?\n)?$")


def _split_suffix(text: str) -> tuple[str, str]:
    m = re.search(r"(?s)^(.*?)(\\(?:v\\a|a|v))+(\s*)$", text)
    if not m:
        return text, ""
    body = m.group(1)
    suf = text[len(body):]
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

    sp = toks[1]

    if sp.startswith("#"):
        sp = sp[1:]

    sp = sp.rstrip("@")

    return sp.strip()



def _strip_dialog_quotes(text: str) -> tuple[str, tuple[str, str]]:
    t = text.strip()

    pairs = [
        ("“", "”"),
        ('"', '"'),
        ("「", "」"),
        ("『", "』"),
    ]

    for left, right in pairs:
        if t.startswith(left) and t.endswith(right) and len(t) >= 2:
            inner = t[len(left):-len(right)]
            return inner, (left, right)

    return text, ("", "")


class MusicaSCParser:
    def detect(self, ctx: ParseContext, text: str) -> float:
        ext = getattr(getattr(ctx, "path", None), "suffix", "")
        if isinstance(ext, str) and ext.lower() == ".sc":
            return 0.9
        return 0.0

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

            visible, quote_pair = _strip_dialog_quotes(body)
            if visible.strip() == "":
                continue

            speaker = _guess_speaker(rest)

            entries.append(
                {
                    "entry_id": f"{i}",
                    "speaker": speaker,
                    "original": visible,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "meta": {
                        "line_index": i,
                        "msgno": msgno,
                        "ws": ws,
                        "prefix": prefix,
                        "suffix": suf,
                        "newline": nl or "",
                        "quote_left": quote_pair[0],
                        "quote_right": quote_pair[1],
                    },
                }
            )

        return entries

    def rebuild(self, ctx: ParseContext, entries: List[dict]) -> bytes:
        data = self._read_bytes(ctx)
        enc = getattr(ctx, "encoding", None) or "utf-8"
        try:
            text = data.decode(enc, errors="strict")
        except Exception:
            text = data.decode(enc, errors="replace")

        lines = text.splitlines(keepends=True)

        by_line: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            try:
                li = int(meta.get("line_index"))
            except Exception:
                try:
                    li = int(str(e.get("entry_id", "")).strip())
                except Exception:
                    continue
            if 0 <= li < len(lines):
                by_line[li] = e

        for li, e in by_line.items():
            meta = e.get("meta") or {}
            m = _RX_MESSAGE.match(lines[li])
            if not m:
                continue

            ws, sp1, msgno, sp2, rest, nl = m.groups()
            prefix = str(meta.get("prefix") or "")
            suf = str(meta.get("suffix") or "")
            newline = str(meta.get("newline") or (nl or ""))

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                body = tr
            else:
                body = e.get("original") or ""

            ql = meta.get("quote_left") or ""
            qr = meta.get("quote_right") or ""
            if ql and qr:
                body = f"{ql}{body}{qr}"

            lines[li] = f"{ws}.message{sp1}{msgno}{sp2}{prefix}{body}{suf}{newline}"

        out_text = "".join(lines)
        return out_text.encode(enc, errors="replace")

    def _read_bytes(self, ctx: ParseContext) -> bytes:
        p = getattr(ctx, "file_path", None) or getattr(ctx, "path", None)
        if not p:
            raise RuntimeError("ParseContext não tem file_path/path.")
        with open(p, "rb") as f:
            return f.read()


class _MusicaSCPlugin:
    plugin_id = "musica.sc"
    name = "Musica (.sc)"
    extensions = {".sc"}

    def __init__(self) -> None:
        self._impl = MusicaSCParser()

    def detect(self, ctx: ParseContext, text: str) -> float:
        return self._impl.detect(ctx, text)

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        return self._impl.parse(ctx, text)

    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> bytes:
        return self._impl.rebuild(ctx, entries)


def get_plugin():
    return _MusicaSCPlugin()

