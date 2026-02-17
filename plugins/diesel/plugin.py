# plugins/diesel/plugin.py
from __future__ import annotations

import re
import struct
from typing import Dict, List, Tuple, Optional, Any

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

RE_WS_LEAD = re.compile(r"^\s*")
RE_WS_TAIL = re.compile(r"\s*$")


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


# -------------------------
# Whitespace-preserving quote helpers
# -------------------------
def _split_lead_tail_ws(s: str) -> tuple[str, str, str]:
    lead = RE_WS_LEAD.match(s).group(0) if s else ""
    tail = RE_WS_TAIL.search(s).group(0) if s else ""
    core = s[len(lead): len(s) - len(tail)]
    return lead, core, tail


def strip_outer_quotes_same_line_keep_ws(s: str) -> tuple[str, str, str]:
    """
    Remove quote pair only if both are on the same line, preserving outer whitespace.
    Example: '  " hi  "  ' => ('   hi    ', '"', '"')  (quotes removed, spaces preserved)
    """
    lead, core, tail = _split_lead_tail_ws(s)
    for q1, q2 in QUOTE_PAIRS:
        if core.startswith(q1) and core.endswith(q2) and len(core) >= len(q1) + len(q2) + 1:
            inner = core[len(q1): -len(q2)]
            return lead + inner + tail, q1, q2
    return s, "", ""


def strip_opening_quote_if_any_keep_ws(s: str) -> tuple[str, str, str]:
    """
    If (after leading whitespace) starts with opening quote, remove it.
    Preserve all whitespace after the quote (do NOT lstrip).
    """
    lead, core, tail = _split_lead_tail_ws(s)
    for q1, q2 in QUOTE_PAIRS:
        if core.startswith(q1) and len(core) >= len(q1) + 1:
            inner = core[len(q1):]
            return lead + inner + tail, q1, q2
    return s, "", ""


def strip_closing_quote_if_matches_keep_ws(s: str, q2: str) -> tuple[str, bool]:
    """
    If (before trailing whitespace) ends with q2, remove it.
    Preserve outer whitespace.
    """
    lead, core, tail = _split_lead_tail_ws(s)
    if core.endswith(q2) and len(core) >= len(q2) + 1:
        inner = core[: -len(q2)]
        return lead + inner + tail, True
    return s, False


# -------------------------
# Tag-template helpers (preserve tags + spaces)
# -------------------------
def build_tag_template(line: str) -> dict:
    """
    Splits a line into segments of tags and non-tags, preserving everything.
    Template is JSON-serializable.
    """
    segs: list[dict] = []
    last = 0
    for m in RE_TAG_ANY.finditer(line):
        if m.start() > last:
            segs.append({"t": "txt", "v": line[last:m.start()]})
        segs.append({"t": "tag", "v": line[m.start():m.end()]})
        last = m.end()
    if last < len(line):
        segs.append({"t": "txt", "v": line[last:]})
    return {"segs": segs}


def template_visible_text(template: dict) -> str:
    """Concatenate non-tag segments exactly (spaces preserved)."""
    out: list[str] = []
    for s in template.get("segs", []):
        if s.get("t") == "txt":
            out.append(s.get("v", ""))
    return "".join(out)


def apply_translation_to_template(template: dict, translated_txt: str) -> str:
    """
    Reconstruct line by keeping all tags, but replacing ALL non-tag text with:
      (original leading ws) + translated_txt + (original trailing ws)
    placed into the first non-tag segment; other txt segments become empty.
    This preserves tag placement and preserves original outer indentation/trailing spaces.
    """
    segs = template.get("segs", [])
    orig_txt = template_visible_text(template)

    lead = RE_WS_LEAD.match(orig_txt).group(0) if orig_txt else ""
    tail = RE_WS_TAIL.search(orig_txt).group(0) if orig_txt else ""

    new_txt = f"{lead}{translated_txt}{tail}"

    rebuilt: list[str] = []
    placed = False
    for s in segs:
        if s.get("t") == "tag":
            rebuilt.append(s.get("v", ""))
            continue
        # txt segment
        if not placed:
            rebuilt.append(new_txt)
            placed = True
        else:
            rebuilt.append("")
    return "".join(rebuilt)


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

            # Preserve newline style (used in rebuild)
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

    def _extract_visible_text_and_meta(self, raw_line: str) -> tuple[Optional[str], dict]:
        """
        Returns (visible_text_preserving_spaces, line_meta).
        visible_text is whitespace-preserving (no strip()).
        line_meta stores info needed to rebuild without losing tags/spaces.
        """
        # Fast emptiness check without altering content
        if not raw_line.strip():
            return None, {}

        s_stripped = raw_line.strip()

        # skip comments
        if s_stripped.startswith("//"):
            return None, {}

        # voice-only line
        if RE_VOICE_LINE.match(s_stripped):
            return None, {}

        # control-tag-only line (unless it's <center>...</center>)
        if RE_CTRL_TAG_LINE.match(s_stripped) and not RE_CENTER.search(raw_line):
            return None, {}

        # path-ish or function-ish (avoid junk entries)
        if RE_LOOKS_PATH.search(s_stripped):
            return None, {}
        if RE_LOOKS_FUNC.match(s_stripped):
            return None, {}

        # center: extract inner but preserve its spaces (remove nested tags, but don't strip)
        m = RE_CENTER.search(raw_line)
        if m:
            inner_raw = m.group(1)
            inner_plain = RE_TAG_ANY.sub("", inner_raw)
            if not inner_plain.strip():
                return None, {}
            if inner_plain.strip().lower() in {"main", "this"}:
                return None, {}
            meta = {
                "line_kind": "center",
                "center": True,
            }
            return inner_plain, meta

        # Non-center: build template to preserve tags + original whitespace
        template = build_tag_template(raw_line)
        plain = template_visible_text(template)  # exact, includes indentation/trailing spaces

        if not plain.strip():
            return None, {}

        # filter out trivial identifiers (use stripped copy for the test only)
        plain_chk = plain.strip()
        if (
            " " not in plain_chk
            and not any(c in plain_chk for c in ".!?\"'、。")
            and plain_chk.isidentifier()
        ):
            return None, {}

        # must have some letters (check stripped; but keep original)
        if not RE_HAS_LETTER.search(plain_chk):
            return None, {}

        meta = {
            "line_kind": "template",
            "template": template,  # JSON-serializable
        }
        return plain, meta

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        try:
            data = self._read_bytes(ctx)
        except Exception:
            return 0.0

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
            nl = b.get("newline") or ("\r\n" if "\r\n" in b["text"] else "\n")

            # Split preserving the original line boundaries for indexing
            lines = b["text"].split(nl)

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

                visible, line_meta = self._extract_visible_text_and_meta(raw_line)
                if visible is None:
                    continue

                # --------
                # Quotes: remove from editor text BUT preserve surrounding whitespace
                # --------
                q_pre = ""
                q_suf = ""

                if pending_quote is not None:
                    _, q2 = pending_quote
                    visible2, closed = strip_closing_quote_if_matches_keep_ws(visible, q2)
                    if closed:
                        visible = visible2
                        q_suf = q2
                        pending_quote = None

                if pending_quote is None:
                    visible2, qp, qs = strip_outer_quotes_same_line_keep_ws(visible)
                    if qp and qs:
                        visible = visible2
                        q_pre = qp
                        q_suf = qs
                    else:
                        visible3, qp2, qs2 = strip_opening_quote_if_any_keep_ws(visible)
                        if qp2:
                            visible = visible3
                            q_pre = qp2
                            pending_quote = (qp2, qs2)

                meta: dict[str, Any] = {
                    "offset": off,
                    "line_index": line_index,
                    "enc": enc,
                    "speaker": current_speaker or "",
                    "quote_pre": q_pre,
                    "quote_suf": q_suf,
                    "newline": nl,
                }
                meta.update(line_meta)

                entries.append(
                    {
                        "entry_id": f"{off}:{line_index}",
                        "speaker": current_speaker or "",
                        "original": visible,  # whitespace preserved
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "meta": meta,
                    }
                )

        return entries

    # --------------------------------------------------
    # Rebuild (returns bytes)
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> bytes:
        data = self._read_bytes(ctx)
        out = bytearray(data)

        # translations by (offset, line_index) => (text, q_pre, q_suf, line_kind, template?)
        tr_map: Dict[Tuple[int, int], Tuple[str, str, str, str, Optional[dict]]] = {}

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
                line_kind = str(meta.get("line_kind") or "plain")
                template = meta.get("template") if isinstance(meta.get("template"), dict) else None
                tr_map[(off, li)] = (tr, q_pre, q_suf, line_kind, template)

        blocks = self._scan_blocks(data, limit=min(len(data), SCAN_LIMIT))

        # Reverse to keep offsets stable while replacing variable-size blocks
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

                new_text_only, q_pre, q_suf, line_kind, template = tr_map[key]

                # restore quotes if the original had them
                if q_pre:
                    new_text_only = f"{q_pre}{new_text_only}"
                if q_suf:
                    new_text_only = f"{new_text_only}{q_suf}"

                # center: replace inner content only, keep everything else (including spaces)
                if RE_CENTER.search(orig_line):
                    new_lines.append(
                        RE_CENTER.sub(
                            lambda _m: f"<center>{new_text_only}</center>",
                            orig_line,
                            count=1,
                        )
                    )
                    continue

                # template-tagged line: keep tags, preserve original outer ws from txt segments
                if line_kind == "template" and isinstance(template, dict):
                    new_lines.append(apply_translation_to_template(template, new_text_only))
                    continue

                # fallback: preserve original line's leading/trailing whitespace
                # (useful if something wasn't templated)
                lead = RE_WS_LEAD.match(orig_line).group(0) if orig_line else ""
                tail = RE_WS_TAIL.search(orig_line).group(0) if orig_line else ""
                new_lines.append(f"{lead}{new_text_only}{tail}")

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
