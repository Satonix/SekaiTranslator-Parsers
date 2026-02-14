# plugins/diesel/plugin.py
from __future__ import annotations

import struct
from typing import List

from parsers.base import ParseContext


STRING_PREFIX = b"\x10\x00\x00\x08"
TEXT_ENCODING = "cp932"   # Shift-JIS


class DieselNutParser:
    plugin_id = "diesel.nut"
    name = "Diesel NUT strings (.nut)"
    extensions = {".nut"}

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        """
        O loader do SekaiTranslator (como no Artemis) passa `text`.
        Para arquivos binários, assumimos que `text` é uma visão 1:1 dos bytes (latin-1).
        Detecta pelo prefixo nas primeiras ~4KB.
        """
        try:
            data = text.encode("latin1", errors="strict")
        except Exception:
            return 0.0

        if STRING_PREFIX in data[:4096]:
            return 0.8
        return 0.0

    # --------------------------------------------------
    # Parse
    # --------------------------------------------------
    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        data = text.encode("latin1", errors="strict")

        offsets: List[int] = []
        strings: List[str] = []

        i = 0
        n = len(data)
        while i < n:
            if data[i:i+4] != STRING_PREFIX:
                i += 1
                continue

            idx = i + 4
            if idx + 4 > n:
                break

            size = struct.unpack_from("<I", data, idx)[0]
            end = idx + 4 + size
            if end > n:
                i += 1
                continue

            raw = data[idx+4:end]

            # Heurística do NUTEditor: evita lixo binário
            if any(b < 0x0A for b in raw):
                i += 1
                continue

            s = raw.decode(TEXT_ENCODING, errors="ignore")
            strings.append(s)
            offsets.append(idx)

            i += 4

        entries: list[dict] = []
        for k, (s, off) in enumerate(zip(strings, offsets)):
            # meta guarda offset e tamanho original para rebuild (tamanho será recalculado)
            entry_id = f"{off}:{k}"
            entries.append(
                {
                    "entry_id": entry_id,
                    "original": s,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "meta": {
                        "offset": off,
                    },
                }
            )

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        """
        Reconstrói o binário aplicando as traduções.
        Retorna `str` (latin-1) para preservar bytes no canal do loader.
        """
        # `ctx.original_text` deve ser o conteúdo original passado pelo loader (como no Artemis).
        data = ctx.original_text.encode("latin1", errors="strict")
        out = bytearray(data)

        # Recalcula offsets lendo novamente, garantindo coerência mesmo sem depender de parse anterior.
        scan_entries = self.parse(ctx, ctx.original_text)
        offsets = [int(e["meta"]["offset"]) for e in scan_entries]

        # Traduções na mesma ordem
        # Se o número de entries mudou, aplica até o mínimo para não quebrar.
        count = min(len(entries), len(offsets))

        # Substitui em ordem reversa (para não deslocar offsets antes do tempo)
        for i in range(count - 1, -1, -1):
            off = offsets[i]
            if off + 4 > len(out):
                continue

            orig_size = struct.unpack_from("<I", out, off)[0]
            block_end = off + 4 + orig_size
            if block_end > len(out):
                continue

            tr = entries[i].get("translation")
            if not isinstance(tr, str) or not tr.strip():
                continue
            tr = tr.strip()

            new_bytes = tr.encode(TEXT_ENCODING)
            new_block = struct.pack("<I", len(new_bytes)) + new_bytes

            # remove e insere
            del out[off:block_end]
            out[off:off] = new_block

        diff = len(out) - len(data)

        # Atualiza offsets do header (assunção do NUTEditor)
        self._update_offset(out, 0x08, diff)
        self._update_offset(out, 0x0C, diff)

        return bytes(out).decode("latin1", errors="strict")

    def _update_offset(self, buf: bytearray, index: int, diff: int) -> None:
        if index + 4 > len(buf):
            return
        val = struct.unpack_from("<I", buf, index)[0]
        struct.pack_into("<I", buf, index, (val + diff) & 0xFFFFFFFF)


plugin = DieselNutParser()
