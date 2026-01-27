from __future__ import annotations

from parsers.base import ParserPlugin, ParseContext, ParserError
import re


class ArtemisParser:
    plugin_id = "artemis.ast"
    name = "Artemis AST (.ast)"
    extensions = {".ast"}

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------
    def detect(self, ctx: ParseContext, text: str) -> float:
        if "astver" in text and "ast =" in text and "block_" in text:
            return 0.9
        return 0.0

    # --------------------------------------------------
    # Parse
    # --------------------------------------------------
    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        project = ctx.project
        src_lang = (project.get("source_language") or "ja").strip()
        tgt_lang = (project.get("target_language") or "pt-BR").strip()

        entries: list[dict] = []

        # captura blocos text = { ... }
        for m in re.finditer(r"text\s*=\s*{", text):
            start = m.end()
            end = self._find_matching_brace(text, start - 1)
            if end is None:
                continue

            block = text[start:end]

            lang_block = self._extract_lang_block(block, src_lang)
            if not lang_block and src_lang != "ja":
                lang_block = self._extract_lang_block(block, "ja")

            if not lang_block:
                continue

            for s, span in lang_block:
                entries.append({
                    "entry_id": f"{m.start()}:{span[0]}",
                    "original": s,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "meta": {
                        "lang": src_lang,
                        "span": span,
                        "block_start": start,
                        "block_end": end,
                    }
                })

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        text = ctx.original_text

        # aplica de trás pra frente para não quebrar offsets
        for e in sorted(entries, key=lambda x: x["meta"]["span"][0], reverse=True):
            tr = e.get("translation")
            if not tr:
                continue

            a, b = e["meta"]["span"]
            text = text[:a] + f'"{tr}"' + text[b:]

        return text

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------
    def _extract_lang_block(self, block: str, lang: str):
        """
        Retorna lista de (string, (start, end))
        """
        results = []

        m = re.search(rf"{re.escape(lang)}\s*=\s*{{", block)
        if not m:
            return results

        start = m.end()
        end = self._find_matching_brace(block, start - 1)
        if end is None:
            return results

        lang_content = block[start:end]

        for sm in re.finditer(r'"([^"]+)"', lang_content):
            s = sm.group(1)
            span = sm.span()
            # ajusta para posição absoluta no arquivo
            abs_start = span[0] + (ctx := 0)
            results.append((s, span))

        return results

    def _find_matching_brace(self, text: str, open_pos: int):
        depth = 0
        for i in range(open_pos, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
        return None


plugin = ArtemisParser()
