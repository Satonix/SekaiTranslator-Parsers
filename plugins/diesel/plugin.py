
from sekai_translator.plugins.types.structural import StructuralParserPlugin
from .diesel_nut_parser import DieselNutParser


class DieselPlugin(StructuralParserPlugin):

    id = "diesel.nut"
    name = "Diesel Engine (NUT)"
    extensions = [".nut"]

    def detect(self, file_path: str, data: bytes) -> bool:
        # Heuristic: look for Diesel NUT string prefix in first 4KB
        return b"\x10\x00\x00\x08" in data[:4096]

    def parse(self, file_path: str):
        parser = DieselNutParser()
        return parser.parse(file_path)

    def build(self, file_path: str, entries):
        parser = DieselNutParser()
        return parser.build(file_path, entries)
