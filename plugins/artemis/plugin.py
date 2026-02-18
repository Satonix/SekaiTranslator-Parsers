# plugins/artemis/plugin.py
from __future__ import annotations

import re
from typing import Iterator, Optional, Tuple

from parsers.base import ParseContext
from parsers.entries import make_entry


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
        project = getattr(ctx, "project", None) or {}
        src_lang = (project.get("source_language") or "ja").strip() or "ja"

        entries: list[dict] = []

        for m in re.finditer(r"\btext\s*=\s*{", text):
            block_open_abs = m.end() - 1
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

            if not found and src_lang != "ja":
                found = self._extract_lang_strings_abs(
                    full_text=text,
                    block_inner=block_inner,
                    block_inner_start_abs=inner_start_abs,
                    lang="ja",
                )

            if not found:
                continue

            for tok in found:
                span = tok["span_abs"]
                entry_id = f"{inner_start_abs}:{span[0]}:{span[1]}"

                entries.append(
                    make_entry(
                        entry_id=entry_id,
                        original=tok["text_for_editor"],
                        speaker="",
                        meta={
                            "span_abs": span,
                            "token_kind": tok["kind"],               # "quoted" | "long"
                            "long_style": tok.get("long_style", ""), # "plain"|"wrapped"|"leading_quote"
                        },
                    )
                )

        return entries

    # --------------------------------------------------
    # Rebuild
    # --------------------------------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        out = ctx.original_text

        def _key(e: dict) -> int:
            span = (e.get("meta") or {}).get("span_abs")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                return int(span[0])
            return -1

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
                replacement = f"\"{self._escape_lua_string(tr)}\""

            elif kind == "long":
                style = (meta.get("long_style") or "plain").strip() or "plain"

                if style == "wrapped":
                    # exporta [[ "..." ]] (aspas externas)
                    inner = f"\"{self._escape_lua_string(tr)}\""
                    open_, close = self._make_safe_long_brackets(inner)
                    replacement = f"{open_}{inner}{close}"
                else:
                    # plain OU leading_quote: exporta sem aspas externas
                    inner = tr
                    open_, close = self._make_safe_long_brackets(inner)
                    replacement = f"{open_}{inner}{close}"

            else:
                replacement = f"\"{self._escape_lua_string(tr)}\""

            out = out[:a] + replacement + out[b:]

        return out

    # --------------------------------------------------
    # Extract
    # --------------------------------------------------
    def _extract_lang_strings_abs(
        self,
        *,
        full_text: str,
        block_inner: str,
        block_inner_start_abs: int,
        lang: str,
    ) -> list[dict]:
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

        return list(self._iter_lua_string_tokens(lang_inner, lang_inner_start_abs))

    def _iter_lua_string_tokens(self, s: str, base_abs: int) -> Iterator[dict]:
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
                        i += 1
                        end = i
                        inner = s[start + 1 : end - 1]
                        yield {
                            "kind": "quoted",
                            "span_abs": (base_abs + start, base_abs + end),
                            "text_for_editor": self._unescape_lua_string(inner),
                        }
                        break
                    i += 1
                else:
                    return
                continue

            # -----------------------
            # Long bracket: [=*[ ... ]=*]
            # -----------------------
            if ch == "[":
                lb = self._try_parse_long_bracket(s, i)
                if lb is not None:
                    start, end, raw_inner = lb

                    # FIX robusto: remove aspas externas de forma segura preservando newlines
                    style, editor_text = self._strip_outer_quotes_preserve(raw_inner)

                    if style == "wrapped":
                        yield {
                            "kind": "long",
                            "long_style": "wrapped",
                            "span_abs": (base_abs + start, base_abs + end),
                            "text_for_editor": self._unescape_lua_string(editor_text),
                        }
                    elif style == "leading_quote":
                        yield {
                            "kind": "long",
                            "long_style": "leading_quote",
                            "span_abs": (base_abs + start, base_abs + end),
                            "text_for_editor": self._unescape_lua_string(editor_text),
                        }
                    else:
                        yield {
                            "kind": "long",
                            "long_style": "plain",
                            "span_abs": (base_abs + start, base_abs + end),
                            "text_for_editor": raw_inner,
                        }

                    i = end
                    continue

            i += 1

    def _strip_outer_quotes_preserve(self, raw_inner: str) -> tuple[str, str]:
        """
        Detecta aspas externas dentro de raw_inner (conteúdo do long bracket),
        ignorando whitespace nas pontas, e remove:
        - se houver " ... " -> wrapped (remove ambos)
        - se houver só aspas iniciais -> leading_quote (remove só a inicial)
        - caso contrário -> plain
        Preserva quebras de linha e whitespace internos.
        """
        n = len(raw_inner)
        if n == 0:
            return "plain", ""

        # achar primeiro char não-whitespace
        l = 0
        while l < n and raw_inner[l].isspace():
            l += 1
        if l >= n or raw_inner[l] != '"':
            return "plain", ""

        # achar último char não-whitespace
        r = n - 1
        while r >= 0 and raw_inner[r].isspace():
            r -= 1
        if r <= l:
            # só uma aspa e/ou whitespace
            # remove a inicial
            return "leading_quote", raw_inner[:l] + raw_inner[l + 1 :]

        if raw_inner[r] == '"':
            # wrapped: remove inicial e final, preservando whitespace fora delas
            without_first = raw_inner[:l] + raw_inner[l + 1 :]
            # após remover a primeira, o índice r muda se r > l
            r2 = r - 1
            # remove a última " (agora em r2, ignorando whitespace não muda)
            return "wrapped", without_first[:r2] + without_first[r2 + 1 :]

        # leading_quote: remove só a inicial (a final não é externa)
        return "leading_quote", raw_inner[:l] + raw_inner[l + 1 :]

    def _try_parse_long_bracket(self, s: str, i: int) -> Optional[Tuple[int, int, str]]:
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

        open_len = 2 + eq
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
        depth = 0
        in_q = False
        esc = False

        i = open_pos
        n = len(text)

        while i < n:
            ch = text[i]

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

            if ch == "[":
                lb = self._try_parse_long_bracket(text, i)
                if lb is not None:
                    _, end, _ = lb
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
    # String helpers
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
        eq = 0
        while True:
            close = "]" + ("=" * eq) + "]"
            if close not in inner:
                open_ = "[" + ("=" * eq) + "["
                return open_, close
            eq += 1


plugin = ArtemisParser()
