# plugins/kirikiri_dialect_test/plugin.py
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, List

from parsers.base import ParseContext

_RX_COMMENT = re.compile(r"^\s*;")          # ; comment (inclui ;;)
_RX_LABEL = re.compile(r"^\s*\*")           # *label or *|
_RX_INLINE_CMD = re.compile(r"^\s*@")       # @font etc
_RX_TAG_ONLY = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+$")  # only [tags] on the line

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


def _is_translatable_body(body: str) -> bool:
    if body is None or body.strip() == "":
        return False
    if _RX_TAG_ONLY.match(body):
        return False
    tmp = _RX_ANY_TAG.sub("", body)
    return tmp.strip() != ""


def _find_first_break_tag(line: str) -> tuple[int, str] | tuple[int, str]:
    """Retorna (idx, tag) para o primeiro [r]/[cr] mais cedo; (-1, '') se não tiver."""
    idx_r = line.find("[r]")
    idx_cr = line.find("[cr]")
    if idx_r < 0 and idx_cr < 0:
        return -1, ""
    if idx_r >= 0 and (idx_cr < 0 or idx_r < idx_cr):
        return idx_r, "[r]"
    return idx_cr, "[cr]"


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
        Dialeto do jogo:
        - Texto pode estar em múltiplas linhas antes de aparecer [r]/[cr].
        - Pode haver linhas @font/@... no meio da fala.
        - Um entry == um terminador encontrado ([r] ou [cr]).
        """
        entries: list[dict] = []
        lines = text.splitlines(keepends=True)

        current_speaker: str = ""

        # Buffer do "bloco" atual até achar [r]/[cr]
        buf_span: list[dict] = []   # elementos: {"kind":"text"/"cmd", ...}
        buf_text_parts: list[str] = []
        buf_text_line_count = 0
        buf_start_line: Optional[int] = None

        def flush_if_break(end_tag_line_index: int, end_tag: str, after_tag: str) -> None:
            nonlocal buf_span, buf_text_parts, buf_text_line_count, buf_start_line, entries
            if buf_start_line is None:
                # não começou bloco, nada a flush
                buf_span = []
                buf_text_parts = []
                buf_text_line_count = 0
                return

            original = "\n".join(buf_text_parts).rstrip("\n")

            if _is_translatable_body(original):
                entry_id = f"{buf_start_line}-{end_tag_line_index}"
                entries.append(
                    {
                        "entry_id": entry_id,
                        "original": original,
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "speaker": current_speaker,
                        "meta": {
                            "start_line": buf_start_line,
                            "end_line": end_tag_line_index,
                            "text_line_count": buf_text_line_count,
                            "span": buf_span,   # round-trip
                            "end_tag": end_tag,
                            "after_tag": after_tag,
                        },
                    }
                )

            # reset buffer
            buf_span = []
            buf_text_parts = []
            buf_text_line_count = 0
            buf_start_line = None

        for i, line in enumerate(lines):
            # Speaker (linha separada)
            msp = _RX_SPEAKER.search(line)
            if msp:
                current_speaker = (msp.group(1) or "").strip()
                continue

            # Ignorar label e comentário
            if _RX_LABEL.match(line):
                continue
            if _RX_COMMENT.match(line):
                continue

            # Se for linha @cmd, ela pode estar dentro de um bloco de fala.
            if _RX_INLINE_CMD.match(line):
                # só guarda se já estamos dentro de um bloco (buf_start_line != None)
                if buf_start_line is not None:
                    buf_span.append({"kind": "cmd", "line_index": i, "raw": line})
                continue

            # Procura terminador na linha
            idx_tag, tag = _find_first_break_tag(line)
            if idx_tag >= 0:
                # Parte antes do terminador é texto (mesmo que vazio)
                before_tag = line[:idx_tag]
                after_tag = line[idx_tag:]  # inclui [r]/[cr] e o resto (incl \n)

                prefix, body = _split_leading_ws(before_tag)

                # Inicia bloco se necessário
                if buf_start_line is None:
                    buf_start_line = i

                # Adiciona a última linha de texto do bloco
                buf_span.append(
                    {
                        "kind": "text",
                        "line_index": i,
                        "prefix": prefix,
                        "suffix": after_tag,     # última linha carrega o after_tag
                    }
                )
                buf_text_parts.append(body.rstrip("\n"))
                buf_text_line_count += 1

                # Fecha bloco
                flush_if_break(i, tag, after_tag)
                continue

            # Linha de texto sem terminador (continuação do bloco OU ignora se fora de bloco)
            # Regra: se tem conteúdo útil, entra no bloco.
            lead_ws, rest = _split_leading_ws(line)
            text_no_nl = rest.rstrip("\n")

            # Se for vazia, só mantém se já estamos no bloco (preservar espaçamento)
            if text_no_nl.strip() == "":
                if buf_start_line is not None:
                    buf_span.append({"kind": "raw", "line_index": i, "raw": line})
                continue

            # Inicia bloco se necessário
            if buf_start_line is None:
                buf_start_line = i

            # Guarda como linha de texto intermediária (suffix = "\n" original)
            buf_span.append(
                {
                    "kind": "text_mid",
                    "line_index": i,
                    "prefix": lead_ws,
                    "suffix": "\n" if line.endswith("\n") else "",
                }
            )
            buf_text_parts.append(text_no_nl)
            buf_text_line_count += 1

        return entries

    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text
        lines = out.splitlines(keepends=True)

        # Indexar por start_line para evitar sobreposição
        by_start: Dict[int, dict] = {}
        for e in entries:
            meta = e.get("meta") or {}
            try:
                sl = int(meta.get("start_line"))
            except Exception:
                continue
            if 0 <= sl < len(lines):
                by_start[sl] = e

        # Rebuild em ordem crescente
        for sl in sorted(by_start.keys()):
            e = by_start[sl]
            meta = e.get("meta") or {}
            span: list[dict] = list(meta.get("span") or [])
            if not span:
                continue

            tr = e.get("translation")
            if isinstance(tr, str) and tr != "":
                full_txt = tr
            else:
                full_txt = str(e.get("original") or "")

            # Split da tradução em linhas para casar com o número de linhas de texto originais
            text_line_count = int(meta.get("text_line_count") or 1)
            tr_lines = full_txt.split("\n")

            if len(tr_lines) < text_line_count:
                tr_lines = tr_lines + [""] * (text_line_count - len(tr_lines))
            elif len(tr_lines) > text_line_count:
                # junta excesso na última linha (não cria linhas físicas novas)
                head = tr_lines[: text_line_count - 1]
                tail = "\n".join(tr_lines[text_line_count - 1 :])
                tr_lines = head + [tail]

            # Percorre o span e reescreve só as linhas de texto, preservando @cmd/raw
            tpos = 0
            for item in span:
                li = int(item.get("line_index"))
                if not (0 <= li < len(lines)):
                    continue

                kind = item.get("kind")
                if kind == "cmd":
                    # mantém raw original (já está em ctx.original_text)
                    continue
                if kind == "raw":
                    continue

                if kind in ("text_mid", "text"):
                    prefix = str(item.get("prefix") or "")
                    suffix = str(item.get("suffix") or "")
                    content = tr_lines[tpos] if tpos < len(tr_lines) else ""
                    tpos += 1
                    lines[li] = f"{prefix}{content}{suffix}"

        return "".join(lines)


plugin = KirikiriDialectTestParser()
