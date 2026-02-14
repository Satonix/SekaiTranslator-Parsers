from __future__ import annotations
import struct
from parsers.base import ParseContext

STRING_PREFIX = b"\x10\x00\x00\x08"
ENCODING = "cp932"

class DieselNutParser:
    plugin_id = "diesel.nut"
    name = "Diesel NUT strings"
    extensions = {".nut"}

    def detect(self, ctx: ParseContext, text: str) -> float:
        # binário via latin1 (1:1 bytes)
        data = text.encode("latin1", errors="strict")
        return 0.8 if STRING_PREFIX in data[:4096] else 0.0

    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        data = text.encode("latin1", errors="strict")
        entries = []
        i = 0
        k = 0
        while i < len(data):
            if data[i:i+4] != STRING_PREFIX:
                i += 1
                continue
            idx = i + 4
            if idx + 4 > len(data):
                break
            size = struct.unpack_from("<I", data, idx)[0]
            end = idx + 4 + size
            if end > len(data):
                i += 1
                continue
            raw = data[idx+4:end]
            if any(b < 0x0A for b in raw):
                i += 1
                continue
            s = raw.decode(ENCODING, errors="ignore")
            entries.append({
                "entry_id": f"{idx}:{k}",
                "original": s,
                "translation": "",
                "status": "untranslated",
                "is_translatable": True,
                "meta": {"offset": idx},
            })
            k += 1
            i += 4
        return entries

    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        data = ctx.original_text.encode("latin1", errors="strict")
        out = bytearray(data)

        # rescana offsets para não depender do parse anterior
        scan = self.parse(ctx, ctx.original_text)
        offsets = [e["meta"]["offset"] for e in scan]
        count = min(len(entries), len(offsets))

        for i in range(count - 1, -1, -1):
            tr = entries[i].get("translation", "")
            if not isinstance(tr, str) or not tr.strip():
                continue
            off = offsets[i]
            orig_size = struct.unpack_from("<I", out, off)[0]
            end = off + 4 + orig_size

            newb = tr.strip().encode(ENCODING)
            block = struct.pack("<I", len(newb)) + newb

            del out[off:end]
            out[off:off] = block

        diff = len(out) - len(data)
        self._upd(out, 0x08, diff)
        self._upd(out, 0x0C, diff)

        return bytes(out).decode("latin1", errors="strict")

    def _upd(self, buf: bytearray, idx: int, diff: int):
        if idx + 4 > len(buf):
            return
        v = struct.unpack_from("<I", buf, idx)[0]
        struct.pack_into("<I", buf, idx, (v + diff) & 0xFFFFFFFF)

plugin = DieselNutParser()
