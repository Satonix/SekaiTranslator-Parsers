from __future__ import annotations

import re
from parsers.base import ParseContext


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

        # Procura por blocos "text = { ... }" dentro do arquivo inteiro
        for m in re.finditer(r"\btext\s*=\s*{", text):
            block_open_abs = m.end() - 1  # posição do "{"
            block_close_abs = self._find_matching_brace(text, block_open_abs)
            if block_close_abs is None:
                continue

            # conteúdo dentro do { ... } (sem as chaves externas)
            inner_start_abs = block_open_abs + 1
            inner_end_abs = block_close_abs
            block_inner = text[inner_start_abs:inner_end_abs]

            # tenta pegar o idioma configurado; se não existir, cai pra ja
            found = self._extract_lang_strings_abs(
                full_text=text,
                block_inner=block_inner,
                block_inner_start_abs=inner_start_abs,
                lang=src_lang,
            )
            if not found and src_lang != "ja":
                found = self._extract_lang_strings_abs(
                    full_text=text,
                    block_inner=block_inner,
                    block_inner_start_abs=inner_start_abs,
                    lang="ja",
                )

            if not found:
                continue

            for original_str, span_abs in found:
                # span_abs cobre o token com aspas:  "...."
                entry_id = f"{inner_start_abs}:{span_abs[0]}:{span_abs[1]}"
                entries.append({
                    "entry_id": entry_id,
                    "original": original_str,
                    "translation": "",
                    "status": "untranslated",
                    "is_translatable": True,
                    "meta": {
                        "span_abs": span_abs,  # (a,b) ABSOLUTO em ctx.original_text
                        "src_lang": src_lang,
                        "tgt_lang": tgt_lang,
                    }
                })

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text

        # Substitui de trás pra frente para não invalidar offsets
        def _key(e: dict) -> int:
            meta = e.get("meta") or {}
            span = meta.get("span_abs")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                return int(span[0])
            return -1

        for e in sorted(entries, key=_key, reverse=True):
            tr = e.get("translation")
            if not isinstance(tr, str) or not tr.strip():
                continue

            meta = e.get("meta") or {}
            span = meta.get("span_abs")
            if not (isinstance(span, (list, tuple)) and len(span) == 2):
                continue

            a, b = int(span[0]), int(span[1])
            if not (0 <= a < b <= len(out)):
                continue

            # span cobre o token COM aspas: "..."
            escaped = self._escape_lua_string(tr.strip())
            out = out[:a] + f"\"{escaped}\"" + out[b:]

        return out

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------
    def _extract_lang_strings_abs(
        self,
        *,
        full_text: str,
        block_inner: str,
        block_inner_start_abs: int,
        lang: str,
    ) -> list[tuple[str, tuple[int, int]]]:
        """
        Encontra o sub-bloco:
            <lang> = { ... }
        e retorna lista de:
            (conteúdo_da_string, (span_abs_inicio, span_abs_fim))
        onde span_abs cobre o token com aspas no arquivo inteiro.
        """
        results: list[tuple[str, tuple[int, int]]] = []

        # acha "<lang> = {"
        m = re.search(rf"\b{re.escape(lang)}\s*=\s*{{", block_inner)
        if not m:
            return results

        lang_open_rel = m.end() - 1  # posição do "{" relativo a block_inner
        lang_open_abs = block_inner_start_abs + lang_open_rel
        lang_close_abs = self._find_matching_brace(full_text, lang_open_abs)
        if lang_close_abs is None:
            return results

        # conteúdo dentro do { ... } do idioma
        lang_inner_start_abs = lang_open_abs + 1
        lang_inner_end_abs = lang_close_abs
        lang_inner = full_text[lang_inner_start_abs:lang_inner_end_abs]

        # captura tokens "...." (match inteiro inclui aspas)
        for sm in re.finditer(r"\"([^\"\\]*(?:\\.[^\"\\]*)*)\"", lang_inner):
            token_rel_a, token_rel_b = sm.span(0)
            token_abs_a = lang_inner_start_abs + token_rel_a
            token_abs_b = lang_inner_start_abs + token_rel_b

            # conteúdo sem aspas, com escapes resolvidos minimamente
            raw_inside = sm.group(1)
            original = self._unescape_lua_string(raw_inside)

            results.append((original, (token_abs_a, token_abs_b)))

        return results

    def _find_matching_brace(self, text: str, open_pos: int) -> int | None:
        """
        Retorna o índice ABSOLUTO do '}' que fecha a '{' em open_pos.
        Tenta ignorar chaves dentro de strings "..."
        """
        depth = 0
        in_str = False
        esc = False

        for i in range(open_pos, len(text)):
            ch = text[i]

            if in_str:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == "\"":
                    in_str = False
                continue

            # fora de string
            if ch == "\"":
                in_str = True
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i

        return None

    def _escape_lua_string(self, s: str) -> str:
        # básico e suficiente para maioria dos scripts
        return (
            s.replace("\\", "\\\\")
             .replace("\"", "\\\"")
             .replace("\r", "\\r")
             .replace("\n", "\\n")
        )

    def _unescape_lua_string(self, s: str) -> str:
        # mínimo: desfaz \" \\ \n \r
        s = s.replace("\\n", "\n").replace("\\r", "\r")
        s = s.replace("\\\"", "\"").replace("\\\\", "\\")
        return s


plugin = ArtemisParser()
