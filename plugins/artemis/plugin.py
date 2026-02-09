# plugins/artemis/plugin.py
from __future__ import annotations

import re
from typing import Iterator, Tuple, Optional

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

        # Procura por blocos "text = { ... }" no arquivo inteiro
        for m in re.finditer(r"\btext\s*=\s*{", text):
            block_open_abs = m.end() - 1  # posição do "{"
            block_close_abs = self._find_matching_brace(text, block_open_abs)
            if block_close_abs is None:
                continue

            inner_start_abs = block_open_abs + 1
            inner_end_abs = block_close_abs
            block_inner = text[inner_start_abs:inner_end_abs]

            found = self._extract_lang_strings_abs(
                full_text=text,
                block_inner=block_inner,
                block_inner_start_abs=inner_start_abs,
                lang=src_lang,
            )

            # fallback: se idioma escolhido não existir, tenta "ja"
            if not found and src_lang != "ja":
                found = self._extract_lang_strings_abs(
                    full_text=text,
                    block_inner=block_inner,
                    block_inner_start_abs=inner_start_abs,
                    lang="ja",
                )

            if not found:
                continue

            for token in found:
                original_str = token["text_for_editor"]
                span_abs = token["span_abs"]

                entry_id = f"{inner_start_abs}:{span_abs[0]}:{span_abs[1]}"
                entries.append(
                    {
                        "entry_id": entry_id,
                        "original": original_str,
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "meta": {
                            "span_abs": span_abs,              # (a,b) ABS no arquivo
                            "token_kind": token["kind"],       # "quoted" | "long"
                            "wrapped_quotes": token["wrapped_quotes"],  # bool (só para long)
                            "src_lang": src_lang,
                            "tgt_lang": tgt_lang,
                        },
                    }
                )

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text

        def _key(e: dict) -> int:
            meta = e.get("meta") or {}
            span = meta.get("span_abs")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                return int(span[0])
            return -1

        # substitui de trás pra frente para não invalidar offsets
        for e in sorted(entries, key=_key, reverse=True):
            tr = e.get("translation")
            if not isinstance(tr, str):
                continue
            tr = tr.strip()
            if not tr:
                continue

            meta = e.get("meta") or {}
            span = meta.get("span_abs")
            if not (isinstance(span, (list, tuple)) and len(span) == 2):
                continue
            a, b = int(span[0]), int(span[1])
            if not (0 <= a < b <= len(out)):
                continue

            kind = (meta.get("token_kind") or "").strip()

            if kind == "quoted":
                escaped = self._escape_lua_string(tr)
                replacement = f"\"{escaped}\""

            elif kind == "long":
                wrapped = bool(meta.get("wrapped_quotes"))
                if wrapped:
                    # preserva o estilo [[ "..." ]] (com aspas dentro)
                    escaped = self._escape_lua_string(tr)
                    inner = f"\"{escaped}\""
                    open_, close = self._make_safe_long_brackets(inner)
                    replacement = f"{open_}{inner}{close}"
                else:
                    # string literal em long-bracket sem escapes; só escolhe delimitador seguro
                    inner = tr
                    open_, close = self._make_safe_long_brackets(inner)
                    replacement = f"{open_}{inner}{close}"

            else:
                # fallback seguro: escreve como string quoted normal
                escaped = self._escape_lua_string(tr)
                replacement = f"\"{escaped}\""

            out = out[:a] + replacement + out[b:]

        return out

    # --------------------------------------------------
    # Extract helpers
    # --------------------------------------------------
    def _extract_lang_strings_abs(
        self,
        *,
        full_text: str,
        block_inner: str,
        block_inner_start_abs: int,
        lang: str,
    ) -> list[dict]:
        """
        Encontra o sub-bloco:
            <lang> = { ... }
        e retorna tokens de string encontrados dentro dele.
        Cada token:
          {
            "kind": "quoted"|"long",
            "span_abs": (a,b)  # cobre o token inteiro incluindo delimitadores
            "text_for_editor": str,   # texto "limpo" p/ editor
            "wrapped_quotes": bool,   # só em long
          }
        """
        results: list[dict] = []

        m = re.search(rf"\b{re.escape(lang)}\s*=\s*{{", block_inner)
        if not m:
            return results

        lang_open_rel = m.end() - 1
        lang_open_abs = block_inner_start_abs + lang_open_rel
        lang_close_abs = self._find_matching_brace(full_text, lang_open_abs)
        if lang_close_abs is None:
            return results

        lang_inner_start_abs = lang_open_abs + 1
        lang_inner_end_abs = lang_close_abs
        lang_inner = full_text[lang_inner_start_abs:lang_inner_end_abs]

        for tok in self._iter_lua_string_tokens(lang_inner, lang_inner_start_abs):
            results.append(tok)

        return results

    def _iter_lua_string_tokens(self, s: str, base_abs: int) -> Iterator[dict]:
        """
        Itera por tokens string no trecho:
        - quoted: "...." com escapes
        - long brackets: [[...]], [=[...]=], [==[...]==], etc.

        Retorna dicts com spans ABS e versão para o editor.
        """
        i = 0
        n = len(s)

        while i < n:
            ch = s[i]

            # -----------------------
            # Quoted: " ... "
            # -----------------------
            if ch == '"':
                start = i
                i += 1
                esc = False
                while i < n:
                    c = s[i]
                    if esc:
                        esc = False
                        i += 1
                        continue
                    if c == "\\":
                        esc = True
                        i += 1
                        continue
                    if c == '"':
                        i += 1  # inclui aspas finais
                        end = i
                        inner = s[start + 1 : end - 1]
                        text_for_editor = self._unescape_lua_string(inner)
                        yield {
                            "kind": "quoted",
                            "span_abs": (base_abs + start, base_abs + end),
                            "text_for_editor": text_for_editor,
                            "wrapped_quotes": False,
                        }
                        break
                    i += 1
                else:
                    # string quebrada; aborta
                    return
                continue

            # -----------------------
            # Long bracket: [=*[ ... ]=*]
            # -----------------------
            if ch == "[":
                lb = self._try_parse_long_bracket(s, i)
                if lb is not None:
                    start, end, raw_inner = lb

                    # Detecta caso [[ "..." ]] (com espaços/newlines ao redor)
                    trim = raw_inner.strip()
                    wrapped_quotes = False
                    text_for_editor = raw_inner

                    if len(trim) >= 2 and trim[0] == '"' and trim[-1] == '"':
                        if trim.count('"') == 2:
                            wrapped_quotes = True
                            inner_quoted = trim[1:-1]
                            text_for_editor = self._unescape_lua_string(inner_quoted)

                    yield {
                        "kind": "long",
                        "span_abs": (base_abs + start, base_abs + end),
                        "text_for_editor": text_for_editor,
                        "wrapped_quotes": wrapped_quotes,
                    }

                    i = end
                    continue

            i += 1

    def _try_parse_long_bracket(self, s: str, i: int) -> Optional[Tuple[int, int, str]]:
        """
        Se s[i:] começa com long-bracket opener, retorna:
          (start_index, end_index, inner_string)
        onde end_index é EXCLUSIVO.
        """
        n = len(s)
        if i >= n or s[i] != "[":
            return None

        j = i + 1
        eq = 0
        while j < n and s[j] == "=":
            eq += 1
            j += 1
        if j >= n or s[j] != "[":
            return None

        open_len = 2 + eq  # '[' + '='* + '['
        open_end = i + open_len

        close = "]" + ("=" * eq) + "]"

        k = s.find(close, open_end)
        if k == -1:
            return None

        inner = s[open_end:k]
        end = k + len(close)
        return (i, end, inner)

    # --------------------------------------------------
    # Brace matcher
    # --------------------------------------------------
    def _find_matching_brace(self, text: str, open_pos: int) -> int | None:
        """
        Retorna o índice ABSOLUTO do '}' que fecha a '{' em open_pos.
        Ignora chaves dentro de strings "..." e long-brackets [=[...]=].
        """
        depth = 0
        in_q = False
        esc = False

        i = open_pos
        n = len(text)

        while i < n:
            ch = text[i]

            # dentro de string quoted
            if in_q:
                if esc:
                    esc = False
                    i += 1
                    continue
                if ch == "\\":
                    esc = True
                    i += 1
                    continue
                if ch == '"':
                    in_q = False
                i += 1
                continue

            # fora de quoted: tenta pular long-brackets inteiros
            if ch == "[":
                lb = self._try_parse_long_bracket(text, i)
                if lb is not None:
                    _, end, _inner = lb
                    i = end
                    continue

            if ch == '"':
                in_q = True
                i += 1
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i

            i += 1

        return None

    # --------------------------------------------------
    # String escaping
    # --------------------------------------------------
    def _escape_lua_string(self, s: str) -> str:
        return (
            s.replace("\\", "\\\\")
             .replace("\"", "\\\"")
             .replace("\r", "\\r")
             .replace("\n", "\\n")
        )

    def _unescape_lua_string(self, s: str) -> str:
        s = s.replace("\\n", "\n").replace("\\r", "\r")
        s = s.replace("\\\"", "\"").replace("\\\\", "\\")
        return s

    def _make_safe_long_brackets(self, inner: str) -> tuple[str, str]:
        """
        Escolhe [=*[ ... ]=*] que NÃO conflite com o conteúdo.
        Sem limite fixo.
        """
        eq = 0
        while True:
            close = "]" + ("=" * eq) + "]"
            if close not in inner:
                open_ = "[" + ("=" * eq) + "["
                return open_, close
            eq += 1


plugin = ArtemisParser()
