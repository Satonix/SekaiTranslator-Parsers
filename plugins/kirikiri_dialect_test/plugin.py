from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext


# ----------------------------
# Kirikiri (KAG) dialect helpers
# ----------------------------

_RX_LABEL = re.compile(r"^\s*\*")  # *label or *|
_RX_COMMENT = re.compile(r"^\s*;")  # real comment
_RX_INLINE_CMD = re.compile(r"^\s*@")  # @font, etc (do not translate)
_RX_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+$")  # only [tags] on the line

# Speaker lines: [P_NAME s_cn="Subaru"] (variant spacing tolerated)
_RX_SPEAKER = re.compile(
    r"""\[\s*P_NAME\b[^]]*?\bs_cn\s*=\s*"([^"]+)"[^]]*]""",
    re.IGNORECASE,
)

# Any bracket tag (used for some heuristics)
_RX_ANY_TAG = re.compile(r"\[[^\]]+\]")


def _split_leading_ws(s: str) -> Tuple[str, str]:
    i = 0
    n = len(s)
    while i < n and s[i] in (" ", "\t"):
        i += 1
    return s[:i], s[i:]


def _extract_translatable_from_pre(pre: str) -> Tuple[str, str]:
    """
    pre = line[:idx_cr] (everything before the first [cr])

    Returns:
      (prefix, body)
      - prefix: leading whitespace + optional ';;' markers kept verbatim
      - body: the translatable text (kept verbatim)
    """
    lead_ws, rest = _split_leading_ws(pre)

    # Dialect: lines can begin with ';;' and still be active text.
    if rest.startswith(";;"):
        # Keep ';;' and any immediate spaces as part of prefix.
        j = 2
        while j < len(rest) and rest[j] in (" ", "\t"):
            j += 1
        prefix = lead_ws + rest[:j]
        body = rest[j:]
        return prefix, body

    prefix = lead_ws
    body = rest
    return prefix, body


def _is_translatable_body(body: str) -> bool:
    if body is None:
        return False

    # If empty/whitespace => not translatable
    if body.strip() == "":
        return False

    # Pure tag line (rare, but safe)
    if _RX_TAG_ONLY.match(body):
        return False

    # If body is only tags + whitespace => not translatable
    # (e.g., "[cm]" accidentally paired with [cr])
    tmp = _RX_ANY_TAG.sub("", body)
    if tmp.strip() == "":
        return False

    return True


# ----------------------------
# Parser
# ----------------------------

class KirikiriDialectParser:
    # IMPORTANT: plugin_id should match the folder name used in your plugin manager
    # (commonly used as directory key). Adjust if your installer expects another convention.
    plugin_id = "kirikiri_dialect_test"
    name = "Kirikiri Dialect Test (.ks)"
    extensions = {".ks"}

    def detect(self, ctx: ParseContext, text: str) -> float:
        # Strong signal: .ks file
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".ks":
                return 0.95
        except Exception:
            pass

        fp = str(getattr(ctx, "file_path", "") or "")
        if fp.lower().endswith(".ks"):
            return 0.95

        # Heuristic: KAG tags and [cr] presence
        head = "\n".join(text.splitlines()[:120])
        if "[cr]" in head and ("[cm]" in head or "[P_NAME" in head or "[playbgm" in head):
            return 0.65

        return 0.0

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        current_speaker: str = ""

        for i, line in enumerate(lines):
            # Track speaker (do not create an entry for speaker tags)
            msp = _RX_SPEAKER.search(line)
            if msp:
                current_speaker = (msp.group(1) or "").strip()
                continue

            # Skip non-text structural lines
            if _RX_COMMENT.match(line):
                continue
            if _RX_LABEL.match(line):
                # includes *| and *labels
                continue
            if _RX_INLINE_CMD.match(line):
                continue

            idx_cr = line.find("[cr]")
            if idx_cr < 0:
                continue

            pre = line[:idx_cr]
            post = line[idx_cr:]  # includes [cr] and any trailing stuff + newline

            # Extract prefix/body from the part before [cr]
            prefix, body = _extract_translatable_from_pre(pre)

            if not _is_translatable_body(body):
                continue

            # Keep EXACT text (no strip) for stability
            original_text = body

            # Meta stores how to rebuild the line exactly
            entries.append(
                {
                    "entry_id": f"{i}",
                    "original": original_text,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": current_speaker,
                    "meta": {
                        "line_index": i,
                        "prefix": prefix,   # includes ws + optional ';; ' prefix
                        "post": post,       # begins with [cr] (kept exact)
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
            post = str(meta.get("post") or line[idx_cr:])

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                body_txt = tr  # preserve exact spacing/tags user typed
            else:
                body_txt = str(e.get("original") or "")

            # Rebuild line exactly: prefix + body + post
            lines[li] = f"{prefix}{body_txt}{post}"

        return "".join(lines)


plugin = KirikiriDialectParser()
