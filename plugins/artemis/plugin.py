from __future__ import annotations

import re
from dataclasses import dataclass
from parsers.base import ParseContext


@dataclass(frozen=True)
class _Token:
    kind: str  # "dq" | "long"
    span_abs: tuple[int, int]  # cobre o token inteiro no arquivo (inclui delimitadores)
    text_for_editor: str       # o que vai para entry["original"]
    # metadados para rebuild
    long_eq: int = 0
    long_wrapped_quotes: bool = False  # True se era [["..."]] (ou [=[ "..." ]=])
    # para debug/diagnóstico
    raw_inner: str = ""


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

        # Procura por blocos "text = { ... }"
        for m in re.finditer(r"\btext\s*=\s*{", text):
            block_open_abs = m.end() - 1  # posição do "{"
            block_close_abs = self._find_matching_brace(text, block_open_abs)
            if block_close_abs is None:
                continue

            inner_start_abs = block_open_abs + 1
            inner_end_abs = block_close_abs
            block_inner = text[inner_start_abs:inner_end_abs]

            tokens = self._extract_lang_tokens_abs(
                full_text=text,
                block_inner=block_inner,
                block_inner_start_abs=inner_start_abs,
                lang=src_lang,
            )

            # fallback para ja (se o idioma escolhido não existir)
            if not tokens and src_lang != "ja":
                tokens = self._extract_lang_tokens_abs(
                    full_text=text,
                    block_inner=block_inner,
                    block_inner_start_abs=inner_start_abs,
                    lang="ja",
                )

            if not tokens:
                continue

            for t in tokens:
                entry_id = f"{inner_start_abs}:{t.span_abs[0]}:{t.span_abs[1]}"
                entries.append(
                    {
                        "entry_id": entry_id,
                        "original": t.text_for_editor,
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "meta": {
                            "span_abs": t.span_abs,  # (a,b) cobre token inteiro no arquivo
                            "kind": t.kind,
                            "long_eq": t.long_eq,
                            "long_wrapped_quotes": t.long_wrapped_quotes,
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

            kind = (meta.get("kind") or "dq").strip()
            tr_clean = tr.strip()

            if kind == "dq":
                escaped = self._escape_lua_string(tr_clean)
                repl = f"\"{escaped}\""
            else:
                # long bracket
                wrapped = bool(meta.get("long_wrapped_quotes"))
                if wrapped:
                    # preserva estilo [["..."]]
                    eq = int(meta.get("long_eq") or 0)
                    open_delim = "[" + ("=" * eq) + "["
                    close_delim = "]" + ("=" * eq) + "]"
                    inner = self._escape_lua_string(tr_clean)
                    repl = f'{open_delim}"{inner}"{close_delim}'
                else:
                    # preserva [[...]] mas escolhe delimitador seguro se necessário
                    open_delim, close_delim = self._make_safe_long_brackets(tr_clean)
                    repl = f"{open_delim}{tr_clean}{close_delim}"

            out = out[:a] + repl + out[b:]

        return out

    # --------------------------------------------------
    # Helpers: extract tokens por idioma
    # --------------------------------------------------
    def _extract_lang_tokens_abs(
        self,
        *,
        full_text: str,
        block_inner: str,
        block_inner_start_abs: int,
        lang: str,
    ) -> list[_Token]:
        """
        Encontra o sub-bloco:
            <lang> = { ... }
        e retorna os tokens (strings) dentro dele (quoted e long-bracket),
        com spans ABSOLUTOS no arquivo inteiro.
        """
        # acha "<lang> = {"
        m = re.search(rf"\b{re.escape(lang)}\s*=\s*{{", block_inner)
        if not m:
            return []

        lang_open_rel = m.end() - 1
        lang_open_abs = block_inner_start_abs + lang_open_rel
        lang_close_abs = self._find_matching_brace(full_text, lang_open_abs)
        if lang_close_abs is None:
            return []

        lang_inner_start_abs = lang_open_abs + 1
        lang_inner_end_abs = lang_close_abs
        lang_inner = full_text[lang_inner_start_abs:lang_inner_end_abs]

        tokens: list[_Token] = []
        for tok in self._iter_lua_string_tokens(lang_inner, base_abs=lang_inner_start_abs):
            tokens.append(tok)

        return tokens

    def _iter_lua_string_tokens(self, s: str, *, base_abs: int) -> list[_Token]:
        """
        Tokeniza strings Lua-like dentro de `s`:
        - "...." com escapes
        - [[....]] / [=[....]=] etc (qualquer nível)
        Retorna spans ABSOLUTOS (base_abs + offsets).
        """
        out: list[_Token] = []
        i = 0
        n = len(s)

        while i < n:
            ch = s[i]

            # quoted "..."
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
                        end = i + 1  # exclusivo
                        raw_inside = s[start + 1 : i]
                        text_for_editor = self._unescape_lua_string(raw_inside)
                        out.append(
                            _Token(
                                kind="dq",
                                span_abs=(base_abs + start, base_abs + end),
                                text_for_editor=text_for_editor,
                                raw_inner=raw_inside,
                            )
                        )
                        i = end
                        break
                    i += 1
                else:
                    # string quebrada; aborta
                    break
                continue

            # long bracket: [=*[ ... ]=*]
            if ch == "[":
                eq = 0
                j = i + 1
                while j < n and s[j] == "=":
                    eq += 1
                    j += 1
                if j < n and s[j] == "[":
                    open_len = 2 + eq  # "[" + "="*eq + "["
                    close_delim = "]" + ("=" * eq) + "]"
                    start = i
                    content_start = i + open_len

                    k = content_start
                    # procura close_delim
                    pos = s.find(close_delim, k)
                    if pos != -1:
                        end = pos + len(close_delim)  # exclusivo
                        raw_inner = s[content_start:pos]

                        # caso [["..."]] => raw_inner começa/termina com aspas e é "simples"
                        wrapped_quotes = False
                        text_for_editor = raw_inner
                        if len(raw_inner) >= 2 and raw_inner[0] == '"' and raw_inner[-1] == '"':
                            # só remove se for “uma string quoted simples” (sem outras aspas)
                            if raw_inner.count('"') == 2:
                                wrapped_quotes = True
                                inner_quoted = raw_inner[1:-1]
                                text_for_editor = self._unescape_lua_string(inner_quoted)

                        out.append(
                            _Token(
                                kind="long",
                                span_abs=(base_abs + start, base_abs + end),
                                text_for_editor=text_for_editor,
                                long_eq=eq,
                                long_wrapped_quotes=wrapped_quotes,
                                raw_inner=raw_inner,
                            )
                        )
                        i = end
                        continue

            i += 1

        return out

    # --------------------------------------------------
    # Brace matching
    # --------------------------------------------------
    def _find_matching_brace(self, text: str, open_pos: int) -> int | None:
        """
        Retorna o índice ABSOLUTO do '}' que fecha a '{' em open_pos.
        Ignora chaves dentro de strings "..." e long brackets.
        """
        depth = 0
        i = open_pos
        n = len(text)

        in_dq = False
        dq_esc = False

        # long bracket state
        in_long = False
        long_close = ""

        while i < n:
            ch = text[i]

            # dentro de long bracket
            if in_long:
                if long_close and text.startswith(long_close, i):
                    i += len(long_close)
                    in_long = False
                    long_close = ""
                    continue
                i += 1
                continue

            # dentro de double-quote
            if in_dq:
                if dq_esc:
                    dq_esc = False
                    i += 1
                    continue
                if ch == "\\":
                    dq_esc = True
                    i += 1
                    continue
                if ch == '"':
                    in_dq = False
                i += 1
                continue

            # fora de string: inicia long bracket?
            if ch == "[":
                eq = 0
                j = i + 1
                while j < n and text[j] == "=":
                    eq += 1
                    j += 1
                if j < n and text[j] == "[":
                    in_long = True
                    long_close = "]" + ("=" * eq) + "]"
                    i = j + 1
                    continue

            # inicia double-quote?
            if ch == '"':
                in_dq = True
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
    # Long bracket safe builder
    # --------------------------------------------------
    def _make_safe_long_brackets(self, s: str) -> tuple[str, str]:
        """
        Escolhe [=*[ e ]=*] que não conflite com o conteúdo.
        """
        for eq in range(0, 8):
            close = "]" + ("=" * eq) + "]"
            if close not in s:
                open_ = "[" + ("=" * eq) + "["
                return open_, close
        # fallback extremo: usa aspas
        return '"', '"'

    # --------------------------------------------------
    # Escapes
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


plugin = ArtemisParser()
