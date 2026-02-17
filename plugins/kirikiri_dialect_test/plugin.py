# plugins/kirikiri/plugin.py
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext


# ----------------------------
# Kirikiri/KAG patterns
# ----------------------------

_RX_COMMENT = re.compile(r"^\s*;")           # ; comment
_RX_LABEL = re.compile(r"^\s*\*")           # *label or *|
_RX_INLINE_CMD = re.compile(r"^\s*@")       # @font etc (keep as code)
_RX_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+$")  # line is only [tags]

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


def _extract_prefix_and_body(before_cr: str) -> Tuple[str, str]:
    """
    before_cr = line[:idx_cr] (everything before the FIRST "[cr]")

    Dialect rule:
      - Lines may start with ';;' and STILL be active text.
        We keep ';;' + immediate spaces in the prefix, and translate only the rest.

    Returns:
      (prefix, body)
    """
    lead_ws, rest = _split_leading_ws(before_cr)

    if rest.startswith(";;"):
        j = 2
        while j < len(rest) and rest[j] in (" ", "\t"):
            j += 1
        prefix = lead_ws + rest[:j]  # includes ';;' and following spaces
        body = rest[j:]
        return prefix, body

    return lead_ws, rest


def _is_translatable_body(body: str) -> bool:
    if body is None:
        return False
    if body.strip() == "":
        return False

    # Avoid weird cases where a tag line accidentally includes [cr]
    if _RX_TAG_ONLY.match(body):
        return False

    # If body becomes empty after removing tags => not real text
    tmp = _RX_ANY_TAG.sub("", body)
    if tmp.strip() == "":
        return False

    return True


class KirikiriParser:
    plugin_id = "kirikiri.ks"
    name = "Kirikiri KAG (.ks)"
    extensions = {".ks"}

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        # Prefer extension signal when available
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".ks":
                return 0.95
        except Exception:
            pass

        fp = str(getattr(ctx, "file_path", "") or "")
        if fp.lower().endswith(".ks"):
            return 0.95

        # Heuristic
        head = "\n".join(text.splitlines()[:160])
        score = 0.0
        if "[cr]" in head:
            score += 0.25
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

        for i, line in enumerate(lines):
            # Track speaker tag, do not emit entry for it
            msp = _RX_SPEAKER.search(line)
            if msp:
                current_speaker = (msp.group(1) or "").strip()
                continue

            # Skip structural / non-dialogue lines
            if _RX_COMMENT.match(line):
                continue
            if _RX_LABEL.match(line):
                continue
            if _RX_INLINE_CMD.match(line):
                continue

            idx_cr = line.find("[cr]")
            if idx_cr < 0:
                continue

            before_cr = line[:idx_cr]
            after_cr = line[idx_cr:]  # includes [cr] and everything after, including newline

            prefix, body = _extract_prefix_and_body(before_cr)
            if not _is_translatable_body(body):
                continue

            # Keep EXACT body (no stripping)
            entries.append(
                {
                    "entry_id": f"{i}",
                    "original": body,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": current_speaker,
                    "meta": {
                        "line_index": i,
                        "prefix": prefix,
                        "after_cr": after_cr,
                    },
                }
            )

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        by_line: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            li: Optional[int] = None

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
            idx_cr = line.find("[cr]")
            if idx_cr < 0:
                continue

            meta = e.get("meta") or {}
            prefix = str(meta.get("prefix") or "")
            after_cr = str(meta.get("after_cr") or line[idx_cr:])

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                body_txt = tr  # preserve exactly what user typed
            else:
                body_txt = str(e.get("original") or "")

            lines[li] = f"{prefix}{body_txt}{after_cr}"

        return "".join(lines)


plugin = KirikiriParser()
