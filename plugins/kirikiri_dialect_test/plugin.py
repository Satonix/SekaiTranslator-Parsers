# plugins/kirikiri_dialect_test/plugin.py
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from parsers.base import ParseContext


# ----------------------------
# KiriKiri / KAG (dialect rules)
# ----------------------------
_RX_COMMENT = re.compile(r"^\s*;")                 # ; or ;; comment
_RX_LABEL = re.compile(r"^\s*\*")                  # *label or *|
_RX_INLINE_CMD = re.compile(r"^\s*@")              # @font etc
_RX_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+$")  # only [tags] on the line

# Speaker tag used in your scripts:
# [P_NAME s_cn="Subaru"]
_RX_SPEAKER = re.compile(
    r"""\[\s*P_NAME\b[^]]*?\bs_cn\s*=\s*"([^"]+)"[^]]*]""",
    re.IGNORECASE,
)

_RX_ANY_TAG = re.compile(r"\[[^\]]+\]")


def _split_leading_ws(s: str) -> Tuple[str, str]:
    i = 0
    n = len(s)
    while i < n and s[i] in (" ", "\t"):
        i += 1
    return s[:i], s[i:]


def _extract_prefix_and_body(before_marker: str) -> Tuple[str, str]:
    """
    before_marker = line[:idx_marker] (everything before the FIRST [r]/[cr])

    For this dialect, lines starting with ';' are already filtered out.
    We keep leading whitespace in prefix and translate the rest.
    """
    lead_ws, rest = _split_leading_ws(before_marker)
    return lead_ws, rest


def _is_translatable_body(body: str) -> bool:
    if body is None:
        return False
    if body.strip() == "":
        return False

    # avoid tag-only cases
    if _RX_TAG_ONLY.match(body):
        return False

    # if removing tags leaves nothing, it's not real text
    tmp = _RX_ANY_TAG.sub("", body)
    if tmp.strip() == "":
        return False

    return True


def _find_first_marker(line: str) -> Tuple[int, str] | Tuple[int, None]:
    """
    Returns (index, marker) where marker is "[r]" or "[cr]".
    Chooses the earliest occurrence among them.
    """
    i_r = line.find("[r]")
    i_cr = line.find("[cr]")

    if i_r < 0 and i_cr < 0:
        return -1, None

    if i_r < 0:
        return i_cr, "[cr]"
    if i_cr < 0:
        return i_r, "[r]"

    # both exist, pick earliest
    if i_r <= i_cr:
        return i_r, "[r]"
    return i_cr, "[cr]"


class KirikiriDialectTestParser:
    plugin_id = "kirikiri_dialect_test.ks"
    name = "KiriKiri Dialect Test (.ks)"
    extensions = {".ks"}

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".ks":
                return 0.95
        except Exception:
            pass

        fp = str(getattr(ctx, "file_path", "") or "")
        if fp.lower().endswith(".ks"):
            return 0.95

        head = "\n".join(text.splitlines()[:200])
        score = 0.0
        if "[cr]" in head or "[r]" in head:
            score += 0.30
        if "[cm]" in head:
            score += 0.25
        if "[P_NAME" in head or "[P_FACE" in head:
            score += 0.25
        if "[playbgm" in head or "[playse" in head or "[jump" in head:
            score += 0.15

        return min(0.9, score)

    # --------------------------------------------------
    # Parse
    # --------------------------------------------------
    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        current_speaker: str = ""

        # Buffer for multi-line message (joined by \n in editor)
        buf_line_idxs: List[int] = []
        buf_prefixes: List[str] = []
        buf_after_markers: List[str] = []
        buf_bodies: List[str] = []
        buf_speaker: str = ""

        def _flush_buffer() -> None:
            nonlocal buf_line_idxs, buf_prefixes, buf_after_markers, buf_bodies, buf_speaker

            if not buf_line_idxs:
                return

            # Build editor text as lines joined by '\n'
            original_joined = "\n".join(buf_bodies)

            # entry_id anchored at first line index (stable)
            first_i = buf_line_idxs[0]
            entries.append(
                {
                    "entry_id": f"{first_i}",
                    "original": original_joined,  # EXACT bodies, joined
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": buf_speaker,
                    "meta": {
                        "line_indexes": list(buf_line_idxs),
                        "prefixes": list(buf_prefixes),
                        "after_markers": list(buf_after_markers),
                    },
                }
            )

            buf_line_idxs = []
            buf_prefixes = []
            buf_after_markers = []
            buf_bodies = []
            buf_speaker = ""

        for i, line in enumerate(lines):
            # Track speaker tag
            msp = _RX_SPEAKER.search(line)
            if msp:
                current_speaker = (msp.group(1) or "").strip()
                continue

            # Skip structural/non-dialogue lines
            if _RX_COMMENT.match(line):
                continue
            if _RX_LABEL.match(line):
                continue
            if _RX_INLINE_CMD.match(line):
                continue

            idx_marker, marker = _find_first_marker(line)
            if idx_marker < 0 or marker is None:
                # If we were buffering a message and hit a non-text line, flush.
                _flush_buffer()
                continue

            before = line[:idx_marker]
            after = line[idx_marker:]  # includes marker and everything after, including newline

            prefix, body = _extract_prefix_and_body(before)

            if not _is_translatable_body(body):
                # Not a real text line. If buffer open, flush to avoid swallowing.
                _flush_buffer()
                continue

            # Start buffer if empty
            if not buf_line_idxs:
                buf_speaker = current_speaker

            # If speaker changed mid-buffer, flush and start a new one
            if buf_line_idxs and buf_speaker != current_speaker:
                _flush_buffer()
                buf_speaker = current_speaker

            buf_line_idxs.append(i)
            buf_prefixes.append(prefix)
            buf_after_markers.append(after)
            buf_bodies.append(body)

            # End of message block on [cr]
            if marker == "[cr]":
                _flush_buffer()

        # flush at end
        _flush_buffer()

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        for e in entries:
            meta = e.get("meta") or {}
            idxs = meta.get("line_indexes")
            prefixes = meta.get("prefixes")
            afters = meta.get("after_markers")

            if not (isinstance(idxs, list) and isinstance(prefixes, list) and isinstance(afters, list)):
                continue
            if not (len(idxs) == len(prefixes) == len(afters) and len(idxs) > 0):
                continue

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                txt = tr
            else:
                txt = str(e.get("original") or "")

            # Split editor text back into per-line bodies
            parts = txt.split("\n")

            # Normalize parts length to match original line count
            n = len(idxs)
            if len(parts) < n:
                parts = parts + [""] * (n - len(parts))
            elif len(parts) > n:
                # Join extra lines into the last part to avoid losing data
                parts = parts[: n - 1] + ["\n".join(parts[n - 1 :])]

            for j in range(n):
                li = idxs[j]
                if not isinstance(li, int):
                    try:
                        li = int(li)
                    except Exception:
                        continue
                if not (0 <= li < len(lines)):
                    continue

                # Safety: only rewrite if the target line still has [r]/[cr]
                idx_marker, marker = _find_first_marker(lines[li])
                if idx_marker < 0 or marker is None:
                    continue

                prefix = str(prefixes[j] or "")
                after = str(afters[j] or lines[li][idx_marker:])

                body_txt = parts[j]
                lines[li] = f"{prefix}{body_txt}{after}"

        return "".join(lines)


plugin = KirikiriDialectTestParser()
