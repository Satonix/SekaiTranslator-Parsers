# plugins/diesel/plugin.py
from __future__ import annotations

import re
import struct
from typing import Dict, List, Tuple, Optional

from parsers.base import ParseContext

STRING_PREFIX = b"\x10\x00\x00\x08"

ENC_UTF8 = "utf-8"
ENC_CP932 = "cp932"

HEADER_OFFSETS_TO_UPDATE = (0x08, 0x0C)

MAX_STRING_SIZE = 0x20000
SCAN_LIMIT = 0x200000

# -------------------------
# Tag / pattern helpers
# -------------------------
RE_TAG_ANY = re.compile(r"<[^>]*>")
RE_CENTER = re.compile(r"<\s*center\s*>(.*?)<\s*/\s*center\s*>", re.IGNORECASE | re.DOTALL)

RE_VOICE_LINE = re.compile(r"^\s*<\s*voice\b[^>]*>\s*$", re.IGNORECASE)
RE_VOICE_NAME = re.compile(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)

RE_CTRL_TAG_LINE = re.compile(
    r"^\s*<\s*/?\s*(voice|wait|ruby|font|color|size|img|image|br)\b", re.IGNORECASE
)

RE_LOOKS_PATH = re.compile(r"(^|/)(media|script|lang)(/|$)", re.IGNORECASE)
RE_LOOKS_FUNC = re.compile(r"^(TransText|TransAddText|TransChoice|TransLog|TransVoice)\b")

RE_HAS_LETTER = re.compile(r"[A-Za-zÀ-ÿ\u3040-\u30ff\u4e00-\u9fff]")

# Quote pairs (external)
QUOTE_PAIRS: list[tuple[str, str]] = [
    ('"', '"'),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("〝", "〟"),
    ("«", "»"),
    ("《", "》"),
]

def normalize_fullwidth(s: str) -> str:
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)

def strip_outer_quotes_same_line(s: str) -> tuple[str, str, str]:
    """Removes quote pair only if both are on the same line."""
    t = s.strip()
    for q1, q2 in QUOTE_PAIRS:
        if t.startswith(q1) and t.endswith(q2) and len(t) >= len(q1) + len(q2) + 1:
            core = t[len(q1): -len(q2)].strip()
            return core, q1, q2
    return s, "", ""

def strip_opening_quote_if_any(s: str) -> tuple[str, str, str]:
    """If line starts with an opening quote, strip it (even if not closed here)."""
    t = s.lstrip()
    for q1, q2 in QUOTE_PAIRS:
        if t.startswith(q1) and len(t) >= len(q1) + 1:
            left_ws = s[: len(s) - len(t)]
            core = t[len(q1):].lstrip()
            return left_ws + core, q1, q2
    return s, "", ""

def strip_closing_quote_if_matches(s: str, q2: str) -> tuple[str, bool]:
    """If line ends with q2, strip it."""
    t = s.rstrip()
    if t.endswith(q2) and len(t) >= len(q2) + 1:
        right_ws = s[len(t):]
        core = t[: -len(q2)].rstrip()
        return core + right_ws, True
    return s, False


class DieselNutParser:
    plugin_id = "diesel.nut"
    name = "Diesel NUT (.nut)"
    extensions = {".nut"}

    def _read_bytes(self, ctx: ParseContext) -> bytes:
        with open(ctx.path, "rb") as f:
            return f.read()

    def _decode_with_tag(self, raw: bytes) -> Tuple[str, str]:
        try:
            return raw.decode(ENC_UTF8, errors="strict"), ENC_UTF8
        except Exception:
            pass
        try:
            return raw.decode(ENC_CP932, errors="strict"), ENC_CP932
        except Exception:
            return raw.decode(ENC_CP932, errors="ignore"), ENC_CP932

    def _scan_blocks(self, data: bytes, limit: int | None = None) -> List[dict]:
        n_total = len(data)
        n = min(n_total, limit) if limit is not None else n_total

        out: List[dict] = []
        i = 0
        while i + 8 <= n:
            if data[i:i + 4] != STRING_PREFIX:
                i += 1
                continue

            off = i + 4
            if off + 4 > n:
                break

            size = struct.unpack_from("<I", data, off)[0]
            if size <= 0 or size > MAX_STRING_SIZE:
                i += 1
                continue

            end = off + 4 + size
            if end > n:
                i += 1
                continue

            raw = data[off + 4:end]
            text, enc = self._decode_with_tag(raw)

            newline = "\r\n" if "\r\n" in text else "\n"
            out.append({"offset": off, "text": text, "enc": enc, "newline": newline})
            i += 4

        return out

    def _speaker_from_voice_line(self, line: str) -> Optional[str]:
        m = RE_VOICE_NAME.search(line)
        if not m:
            return None
        name = normalize_fullwidth(m.group(1).strip())
        if "／" in name:
            name = name.split("／")[-1].strip()
        if "/" in name:
            name = name.split("/")[-1].strip()
        return name or None

    def _extract_visible_text(self, line: str) -> Optional[str]:
        s = line.strip()
        if not s:
            return None
        if s.startswith("//"):
            return None
        if RE_VOICE_LINE.match(s):
            return None
        if RE_CTRL_TAG_LINE.match(s) and not RE_CENTER.search(s):
            return None
        if RE_LOOKS_PATH.search(s):
            return None
        if RE_LOOKS_FUNC.match(s):
            return None

        m = RE_CENTER.search(s)
        if m:
            inner = RE_TAG_ANY.sub("", m.group(1)).strip()
            if not inner or inner.lower() in {"main", "this"}:
                return None
            return inner

        plain = RE_TAG_ANY.sub("", s).strip()
        if not plain:
            return None
        if plain.lower() in {"main", "this"}:
            return None

        if (
            " " not in plain
            and not any(c in plain for c in ".!?\"'、。")
            and plain.isidentifier()
        ):
            return None

        if RE_HAS_LETTER.search(plain):
            return plain
        return None

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        try:
            data = self._read_bytes(ctx)
        except Exception:
            return 0.0

        # Diesel NUT usually contains this prefix in first chunk
        if STRING_PREFIX not in data[:65536]:
            return 0.0

        blocks = self._scan_blocks(data, limit=min(len(data), 0x10000))
        c = len(blocks)
        if c >= 5:
            return 0.85
        if c >= 1:
            return 0.35
        return 0.0

    # --------------------------------------------------
    # Parse
    # --------------------------------------------------
    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        data = self._read_bytes(ctx)
        blocks = self._scan_blocks(data, limit=min(len(data), SCAN_LIMIT))

        entries: List[dict] = []

        for b in blocks:
            off = int(b["offset"])
            enc = b["enc"]

            # parse-side can split by '\n'; rebuild uses stored newline style
            lines = b["text"].split("\n")

            current_speaker: Optional[str] = None
            pending_quote: Optional[Tuple[str, str]] = None  # (q_pre, q_suf)

            for line_index, raw_line in enumerate(lines):
                if not raw_line.strip():
                    continue

                # voice tag updates speaker
                if RE_VOICE_LINE.match(raw_line.strip()):
                    sp = self._speaker_from_voice_line(raw_line)
                    if sp:
                        current_speaker = sp
                    continue

                visible = self._extract_visible_text(raw_line)
                if visible is None:
                    continue

                # --------
                # Quotes: ALWAYS remove from editor (even without speaker)
                # --------
                q_pre = ""
                q_suf = ""

                if pending_quote is not None:
                    _, q2 = pending_quote
                    visible2, closed = strip_closing_quote_if_matches(visible, q2)
                    if closed:
                        visible = visible2.strip()
                        q_suf = q2
                        pending_quote = None

                if pending_quote is None:
                    visible2, qp, qs = strip_outer_quotes_same_line(visible)
                    if qp and qs:
                        visible = visible2
                        q_pre = qp
                        q_suf = qs
                    else:
                        visible3, qp2, qs2 = strip_opening_quote_if_any(visible)
                        if qp2:
                            visible = visible3.strip()
                            q_pre = qp2
                            pending_quote = (qp2, qs2)

                entries.append(
                    {
                        "entry_id": f"{off}:{line_index}",
                        "speaker": current_speaker or "",
                        "original": visible,
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "meta": {
                            "offset": off,
                            "line_index": line_index,
                            "enc": enc,
                            "speaker": current_speaker or "",
                            "quote_pre": q_pre,
                            "quote_suf": q_suf,
                        },
                    }
                )

        return entries

    # --------------------------------------------------
    # Rebuild (returns bytes)
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> bytes:
        data = self._read_bytes(ctx)
        out = bytearray(data)

        # translations by (offset, line_index) => (text, quote_pre, quote_suf)
        tr_map: Dict[Tuple[int, int], Tuple[str, str, str]] = {}
        for e in entries:
            meta = e.get("meta") or {}
            try:
                off = int(meta.get("offset"))
                li = int(meta.get("line_index"))
            except Exception:
                try:
                    p = str(e.get("entry_id", "")).split(":")
                    off = int(p[0])
                    li = int(p[1])
                except Exception:
                    continue

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                q_pre = str(meta.get("quote_pre") or "")
                q_suf = str(meta.get("quote_suf") or "")
                tr_map[(off, li)] = (tr, q_pre, q_suf)

        blocks = self._scan_blocks(data, limit=min(len(data), SCAN_LIMIT))

        # reverse to keep offsets stable while replacing variable-size blocks
        for b in reversed(blocks):
            off = int(b["offset"])
            enc = b["enc"]
            nl = b.get("newline") or ("\r\n" if "\r\n" in b["text"] else "\n")

            if off + 4 > len(out):
                continue

            orig_size = struct.unpack_from("<I", out, off)[0]
            end = off + 4 + orig_size
            if end > len(out):
                continue

            orig_lines = b["text"].split(nl)
            new_lines: List[str] = []

            for line_index, orig_line in enumerate(orig_lines):
                key = (off, line_index)
                if key not in tr_map:
                    new_lines.append(orig_line)
                    continue

                new_text_only, q_pre, q_suf = tr_map[key]

                # restore quotes if the original had them
                if q_pre:
                    new_text_only = f"{q_pre}{new_text_only}"
                if q_suf:
                    new_text_only = f"{new_text_only}{q_suf}"

                m = RE_CENTER.search(orig_line)
                if m:
                    new_lines.append(
                        RE_CENTER.sub(lambda _m: f"<center>{new_text_only}</center>", orig_line, count=1)
                    )
                else:
                    new_lines.append(new_text_only)

            new_block_text = nl.join(new_lines)

            try:
                new_bytes = new_block_text.encode(enc, errors="strict")
            except Exception:
                new_bytes = new_block_text.encode(ENC_UTF8, errors="strict")

            new_block = struct.pack("<I", len(new_bytes)) + new_bytes
            del out[off:end]
            out[off:off] = new_block

        diff = len(out) - len(data)
        for hdr_off in HEADER_OFFSETS_TO_UPDATE:
            self._update_offset(out, hdr_off, diff)

        return bytes(out)

    def _update_offset(self, buf: bytearray, index: int, diff: int) -> None:
        if index + 4 > len(buf):
            return
        val = struct.unpack_from("<I", buf, index)[0]
        struct.pack_into("<I", buf, index, (val + diff) & 0xFFFFFFFF)


plugin = DieselNutParser()
