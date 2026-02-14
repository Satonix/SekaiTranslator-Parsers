
import struct

STRING_PREFIX = b"\x10\x00\x00\x08"
ENCODING = "cp932"


class DieselNutParser:

    def __init__(self):
        self.string_offsets = []

    # -------------------------
    # Internal scan
    # -------------------------
    def _scan_strings(self, data: bytes):
        self.string_offsets = []
        strings = []

        i = 0
        while i < len(data):
            if data[i:i+4] != STRING_PREFIX:
                i += 1
                continue

            index = i + 4

            if index + 4 > len(data):
                break

            size = struct.unpack_from("<I", data, index)[0]

            if index + 4 + size > len(data):
                i += 1
                continue

            raw = data[index+4:index+4+size]

            # Basic validation (avoid binary junk)
            if any(b < 0x0A for b in raw):
                i += 1
                continue

            try:
                text = raw.decode(ENCODING, errors="ignore")
            except Exception:
                i += 1
                continue

            strings.append(text)
            self.string_offsets.append(index)

            i += 4

        return strings

    # -------------------------
    # Public API
    # -------------------------
    def parse(self, file_path: str):
        with open(file_path, "rb") as f:
            data = f.read()

        return self._scan_strings(data)

    def build(self, file_path: str, entries):
        with open(file_path, "rb") as f:
            data = f.read()

        # IMPORTANT: rescan to rebuild offsets for this file
        self._scan_strings(data)

        output = bytearray(data)

        # Replace in reverse order to avoid offset shifting issues
        for i in reversed(range(len(entries))):
            index = self.string_offsets[i]
            orig_size = struct.unpack_from("<I", output, index)[0]

            # Remove old block
            del output[index:index+4+orig_size]

            # Encode new text
            encoded = entries[i].encode(ENCODING)
            new_block = struct.pack("<I", len(encoded)) + encoded

            # Insert new block
            output[index:index] = new_block

        diff = len(output) - len(data)

        # Update header offsets (engine-specific assumption)
        self._update_offset(output, 0x8, diff)
        self._update_offset(output, 0xC, diff)

        return bytes(output)

    def _update_offset(self, buffer, index, diff):
        val = struct.unpack_from("<I", buffer, index)[0]
        struct.pack_into("<I", buffer, index, val + diff)
