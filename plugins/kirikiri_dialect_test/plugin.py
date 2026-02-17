# plugins/kirikiri_dialect_test/plugin.py
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from parsers.base import ParseContext


# ----------------------------
# KiriKiri / KAG (dialect rules)
# ----------------------------
_RX_COMMENT = re.compile(r"^\s*;")          # ; comment
_RX_LABEL = re.compile(r"^\s*\*")           # *label or *|
_RX_INLINE_CMD = re.compile(r"^\s*@")       # @font etc
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


def _extract_prefix_and_body(before_tag: str) -> Tuple[str, str]:
    """
    before_tag = line[:idx_tag] (everything before the FIRST "[r]" or "[cr]")

    Dialect rule:
    - Lines may start with ';;' and STILL be active text.
      We keep ';;' + immediate spaces in the prefix, and translate only the rest.

    Returns: (prefix, body)
    """
    lead_ws, rest = _split_leading_ws(before_tag)

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

    # Avoid cases where a tag-only line accidentally has [r]/[cr]
    if _RX_TAG_ONLY.match(body):
        return False

    # If body becomes empty after removing [tags] => not real text
    tmp = _RX_ANY_TAG.sub("", body)
    if tmp.strip() == "":
        return False

    return True


class KirikiriDialectTestParser:
    plugin_id = "kirikiri_dialect_test.ks"
    name = "KiriKiri Dialect Test (.ks)"
    extensions = {".ks"}

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        # Prefer extension signal
        try:
            if getattr(ctx, "path", None) is not None and ctx.path.suffix.lower() == ".ks":
                return 0.95
        except Exception:
            pass

        fp = str(getattr(ctx, "file_path", "") or "")
        if fp.lower().endswith(".ks"):
            return 0.95

        # Heuristic
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

        buffer_bodies: list[str] = []
        buffer_lines: list[dict] = []

        def flush_buffer() -> None:
            if not buffer_bodies:
                return

            # Editor: juntar em uma linha (evita aparecer como 2 linhas no editor)
            # Mantém o texto “humano” sem os tags [r]/[cr].
            joined = " ".join(b.strip() for b in buffer_bodies).strip()
            if not joined:
                buffer_bodies.clear()
                buffer_lines.clear()
                return

            first = buffer_lines[0]
            entries.append(
                {
                    "entry_id": str(first["line_index"]),
                    "original": joined,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "speaker": current_speaker,
                    "meta": {
                        # lista de linhas que compõem este bloco (cada uma termina em [r] ou [cr])
                        "lines": buffer_lines.copy(),
                    },
                }
            )

            buffer_bodies.clear()
            buffer_lines.clear()

        for i, line in enumerate(lines):
            # Track speaker tag, do not emit entry for it
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

            # Find earliest [r] or [cr]
            idx_r = line.find("[r]")
            idx_cr = line.find("[cr]")

            idx = -1
            tag = ""

            if idx_r >= 0 and (idx_cr < 0 or idx_r < idx_cr):
                idx = idx_r
                tag = "[r]"
            elif idx_cr >= 0:
                idx = idx_cr
                tag = "[cr]"

            if idx < 0:
                continue

            before_tag = line[:idx]
            after_tag = line[idx:]  # includes [r]/[cr] and everything after (incl newline)

            prefix, body = _extract_prefix_and_body(before_tag)

            if not _is_translatable_body(body):
                continue

            buffer_bodies.append(body)
            buffer_lines.append(
                {
                    "line_index": i,
                    "prefix": prefix,
                    "after_tag": after_tag,
                }
            )

            # Finaliza bloco somente em [cr]
            if tag == "[cr]":
                flush_buffer()

        # Se arquivo terminar no meio, flush mesmo assim (seguro)
        flush_buffer()

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        # Para cada entry, reescreve as linhas do bloco.
        for e in entries:
            meta = e.get("meta") or {}
            line_infos = meta.get("lines") or []
            if not isinstance(line_infos, list) or not line_infos:
                continue

            # Texto escolhido (tradução se existir, senão original)
            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                joined_text = tr  # preserva exatamente o que o usuário digitou
            else:
                joined_text = str(e.get("original") or "")

            # Reaplica o texto inteiro na primeira linha do bloco
            # e limpa o corpo das linhas seguintes (mantendo prefix e tags).
            for idx, info in enumerate(line_infos):
                try:
                    li = int(info.get("line_index"))
                except Exception:
                    continue
                if not (0 <= li < len(lines)):
                    continue

                prefix = str(info.get("prefix") or "")
                after_tag = str(info.get("after_tag") or "")

                if idx == 0:
                    lines[li] = f"{prefix}{joined_text}{after_tag}"
                else:
                    # Mantém estrutura, remove corpo duplicado
                    lines[li] = f"{prefix}{after_tag}"

        return "".join(lines)


plugin = KirikiriDialectTestParser()
