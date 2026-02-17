# plugins/kirikiri_dialect_test/plugin.py
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext


_RX_COMMENT = re.compile(r"^\s*;")          # ; comment
_RX_LABEL = re.compile(r"^\s*\*")           # *label or *|
_RX_INLINE_CMD = re.compile(r"^\s*@")       # @font etc
_RX_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+$")  # only [tags] on the line

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


def _extract_prefix_and_body(before_tag: str) -> Tuple[str, str]:
    lead_ws, rest = _split_leading_ws(before_tag)

    # dialect: ';;' can still be active text
    if rest.startswith(";;"):
        j = 2
        while j < len(rest) and rest[j] in (" ", "\t"):
            j += 1
        prefix = lead_ws + rest[:j]
        body = rest[j:]
        return prefix, body

    return lead_ws, rest


def _is_translatable_body(body: str) -> bool:
    if body is None or body.strip() == "":
        return False

    if _RX_TAG_ONLY.match(body):
        return False

    tmp = _RX_ANY_TAG.sub("", body)
    if tmp.strip() == "":
        return False

    return True


class KirikiriDialectTestParser:
    plugin_id = "kirikiri_dialect_test.ks"
    name = "KiriKiri Dialect Test (.ks)"
    extensions = {".ks"}

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

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        """
        IMPORTANT CHANGE (per your request):
        - DO NOT merge consecutive [r]/[cr] lines into one entry.
        - Each line that contains [r] or [cr] becomes its own entry.
        - This preserves "one entry == one on-screen line" behavior.
        """
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        current_speaker: str = ""

        for i, line in enumerate(lines):
            msp = _RX_SPEAKER.search(line)
            if msp:
                current_speaker = (msp.group(1) or "").strip()
                continue

            if _RX_COMMENT.match(line):
                continue
            if _RX_LABEL.match(line):
                continue
            if _RX_INLINE_CMD.match(line):
                continue

            # earliest [r] or [cr]
            idx_r = line.find("[r]")
            idx_cr = line.find("[cr]")

            if idx_r < 0 and idx_cr < 0:
                continue

            if idx_r >= 0 and (idx_cr < 0 or idx_r < idx_cr):
                idx_tag = idx_r
                tag = "[r]"
            else:
                idx_tag = idx_cr
                tag = "[cr]"

            before_tag = line[:idx_tag]
            after_tag = line[idx_tag:]  # includes the tag + anything after (incl newline)

            prefix, body = _extract_prefix_and_body(before_tag)

            if not _is_translatable_body(body):
                continue

            entries.append(
                {
                    "entry_id": f"{i}",
                    "original": body,  # exact
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": current_speaker,
                    "meta": {
                        "line_index": i,
                        "prefix": prefix,
                        "after_tag": after_tag,
                        "tag": tag,
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

            idx_r = line.find("[r]")
            idx_cr = line.find("[cr]")
            if idx_r < 0 and idx_cr < 0:
                continue

            if idx_r >= 0 and (idx_cr < 0 or idx_r < idx_cr):
                idx_tag = idx_r
            else:
                idx_tag = idx_cr

            meta = e.get("meta") or {}
            prefix = str(meta.get("prefix") or line[:idx_tag])
            after_tag = str(meta.get("after_tag") or line[idx_tag:])

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                body_txt = tr
            else:
                body_txt = str(e.get("original") or "")

            lines[li] = f"{prefix}{body_txt}{after_tag}"

        return "".join(lines)


plugin = KirikiriDialectTestParser()
