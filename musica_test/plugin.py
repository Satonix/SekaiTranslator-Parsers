from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext

# ============================================================
# Shared helpers (round-trip safe)
# ============================================================

@dataclass(frozen=True)
class TextRegion:
    head: str  # everything before editable text (exact)
    text: str  # editable text (decoded for UI)
    tail: str  # everything after editable text (exact)


def split_suffix_controls(text: str) -> Tuple[str, str]:
    """
    Split trailing control sequences at END of text:
    \a, \v, \v\a, \w, \w\w..., \something123 etc.

    Returns: (body, suffix) where suffix keeps original spacing.
    """
    if not text:
        return text, ""

    rx = re.compile(r"(?s)^(.*?)(\\(?:[A-Za-z]+[0-9]*))(\\(?:[A-Za-z]+[0-9]*))*?(\s*)$")
    m = rx.match(text)
    if not m:
        return text, ""

    body = m.group(1) or ""
    suf = text[len(body):]
    if not suf:
        return text, ""
    return body, suf


_RX_CONTROL_ONLY = re.compile(r"^\s*(?:\\[A-Za-z]+[0-9]*)+\s*$")

# ============================================================
# Base class for line-command parsers
# ============================================================

class LineCommandParserBase:
    plugin_id: str = "base"
    name: str = "Base"
    extensions: set[str] = set()

    def detect(self, ctx: ParseContext, text: str) -> float:
        return 0.0

    def _match_line(self, line: str) -> Optional[re.Match]:
        raise NotImplementedError

    def _extract_region(self, m: re.Match) -> TextRegion:
        raise NotImplementedError

    def _make_entry(self, m: re.Match, region: TextRegion, line_index: int) -> dict:
        raise NotImplementedError

    def _apply_translation(self, entry: dict) -> str:
        tr = entry.get("translation")
        if isinstance(tr, str) and tr != "":
            return tr
        return str(entry.get("original") or "")

    def _encode_text(self, s: str) -> str:
        return s

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        for i, line in enumerate(lines):
            s = line.lstrip()
            if s.startswith(";") or s.startswith("//"):
                continue

            m = self._match_line(line)
            if not m:
                continue

            region = self._extract_region(m)

            if region.text == "" or region.text.strip() == "":
                continue
            if _RX_CONTROL_ONLY.match(region.text):
                continue

            entries.append(self._make_entry(m, region, i))

        return entries

    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        by_line: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            try:
                li = int(meta.get("line_index"))
            except Exception:
                continue
            if 0 <= li < len(lines):
                by_line[li] = e

        for li, e in by_line.items():
            line = lines[li]
            m = self._match_line(line)
            if not m:
                continue

            meta = e.get("meta") or {}
            head = str(meta.get("head") or "")
            tail = str(meta.get("tail") or "")

            text_out = self._apply_translation(e)
            text_out = self._encode_text(text_out)

            lines[li] = f"{head}{text_out}{tail}"

        return "".join(lines)

# ============================================================
# MusicaTest (.sc)
# ============================================================

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


def decode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_DECODE.get(ch, ch) for ch in s)


def encode_table(s: str) -> str:
    if not s:
        return s
    return "".join(MAP_ENCODE.get(ch, ch) for ch in s)


class MusicaTest(LineCommandParserBase):
    plugin_id = "musica_test.sc"
    name = "MusicaTest (.sc)"
    extensions = {".sc"}

    _RX = re.compile(
        r"^(\s*)"              # ws
        r"(?:(\[[^\]]+\]\.)\s*)?"  # optional channel prefix like [e].
        r"\.message(\s+)"      # after .message
        r"(\d+)"               # msgno
        r"(\s+)"               # spacing after msgno
        r"(.*?)"               # rest
        r"(\r?\n)?$"           # newline
    )

    def detect(self, ctx: ParseContext, text: str) -> float:
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".sc":
                return 0.9
        except Exception:
            pass
        return 0.0

    def _match_line(self, line: str) -> Optional[re.Match]:
        return self._RX.match(line)

    def _extract_region(self, m: re.Match) -> TextRegion:
        ws, chan, sp1, msgno, sp2, rest, nl = m.groups()
        nl = nl or ""

        # Keep exact spaces that exist before the actual text in "rest"
        rest_no_nl = rest.rstrip("\r\n")
        lead_ws = rest_no_nl[: len(rest_no_nl) - len(rest_no_nl.lstrip(" "))]
        s = rest_no_nl.lstrip(" ")

        if not s:
            # nothing editable
            head = f"{ws}{chan or ''}.message{sp1}{msgno}{sp2}{rest_no_nl}{nl}"
            return TextRegion(head=head, text="", tail="")

        raw_body, suf = split_suffix_controls(s)
        head = f"{ws}{(chan or '')}.message{sp1}{msgno}{sp2}{lead_ws}"
        tail = f"{suf}{nl}"

        decoded = decode_table(raw_body)
        return TextRegion(head=head, text=decoded, tail=tail)

    def _make_entry(self, m: re.Match, region: TextRegion, line_index: int) -> dict:
        return {
            "entry_id": str(line_index),
            "original": region.text,
            "translation": "",
            "status": "untranslated",
            "is_translatable": True,
            "speaker": "",
            "meta": {
                "line_index": line_index,
                "head": region.head,
                "tail": region.tail,
            },
        }

    def _encode_text(self, s: str) -> str:
        return encode_table(s)


plugin = MusicaTest()