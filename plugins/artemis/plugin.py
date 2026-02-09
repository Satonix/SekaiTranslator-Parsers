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

        src_lang_raw = (project.get("source_language") or "ja").strip()
        tgt_lang_raw = (project.get("target_language") or "pt-BR").strip()

        src_lang = self._normalize_lang_key(src_lang_raw)
        tgt_lang = self._normalize_lang_key(tgt_lang_raw)

        entries: list[dict] = []

        for m in re.finditer(r"\ warning:?\btext\s*=\s*{", text):
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

            if not found:
                for fb in ("ja", "en", "cn"):
                    if fb == src_lang:
                        continue
                    found = self._extract_lang_strings_abs(
                        full_text=text,
                        block_inner=block_inner,
                        block_inner_start_abs=inner_start_abs,
                        lang=fb,
                    )
                    if found:
                        src_lang = fb
                        break

            if not found:
                continue

            for original_str, span_abs, style in found:
                entry_id = f"{inner_start_abs}:{span_abs[0]}:{span_abs[1]}"
                entries.append(
                    {
                        "entry_id": entry_id,
                        "original": original_str,
                        "translation": "",
                        "status": "untranslated",
                        "is_translatable": True,
                        "meta": {
                            "span_abs": span_abs,          # token inteiro
                            "string_style": style,         # "quoted" | "bracket" | "bracket_quoted"
                            "src_lang": src_lang,
                            "tgt_lang": tgt_lang,
                            "src_lang_raw": src_lang_raw,
                            "tgt_lang_raw": tgt_lang_raw,
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
            span = (e.get("meta") or {}).get("span_abs")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                return int(span[0])
            return -1

        for e in sorted(entries, key=_key, reverse=True):
            tr = e.get("translation")
            if not isinstance(tr, str) or not tr.strip():
                continue

            meta = e.get("meta") or {}
            span = meta.get("span_abs")
            style = (meta.get("string_style") or "quoted").strip()

            if not (isinstance(span, (list, tuple)) and len(span) == 2):
                continue

            a, b = int(span[0]), int(span[1])
            if not (0 <= a < b <= len(out)):
                continue

            replacement = self._format_replacement(tr.strip(), style)
            out = out[:a] + replacement + out[b:]

        return out

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------
    def _normalize_lang_key(self, lang: str) -> str:
        s = (lang or "").strip().lower()
        if not s:
            return "ja"

        if s in ("zh", "zh-cn", "zh-hans", "zh-sg", "zh-tw", "zh-hant", "zh-hk", "zh-mo"):
            return "cn"
        if s in ("jp", "ja-jp"):
            return "ja"
        if s.startswith("en-"):
            return "en"
        if s.startswith("pt-"):
            return "pt"
        if "-" in s:
            base = s.split("-", 1)[0].strip()
            if base:
                return base
        return s

    def _format_replacement(self, s: str, style: str) -> str:
        """
        Mantém o mesmo formato do token original:
        - quoted: "..."
        - bracket: [[...]]
        - bracket_quoted: [["..."]]
        """
        if style == "bracket":
            # se contiver ']]', não dá para usar [[...]] com segurança
            if "]]" in s:
                esc = self._escape_lua_string(s)
                return f"\"{esc}\""
            return f"[[{s}]]"

        if style == "bracket_quoted":
            esc = self._escape_lua_string(s)
            return f"[[\"{esc}\"]]"

        # default: quoted
        esc = self._escape_lua_string(s)
        return f"\"{esc}\""

    def _extract_lang_strings_abs(
        self,
        *,
        full_text: str,
        block_inner: str,
        block_inner_start_abs: int,
        lang: str,
    ) -> list[tuple[str, tuple[int, int], str]]:
        """
        Retorna lista de:
          (conteúdo, (span_abs_a, span_abs_b), style)

        style:
          - "quoted"          => "..."
          - "bracket"         => [[...]]
          - "bracket_quoted"  => [["..."]]
        """
        results: list[tuple[str, tuple[int, int], str]] = []

        lang = self._normalize_lang_key(lang)
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

        # 1) bracket_quoted: [["..."]]
        # 2) bracket: [[...]]
        # 3) quoted: "..."
        token_re = re.compile(
            r"""
            (\[\[\s*\"((?:\\.|[^\"\\])*)\"\s*\]\])        # 1: [["..."]]
            |
            (\[\[(?:(?!\]\]).)*\]\])                      # 3: [[...]]  (não-guloso sem atravessar ]]
            |
            (\"([^\"\\]*(?:\\.[^\"\\]*)*)\")              # 4: "..."
            """,
            re.VERBOSE | re.DOTALL,
        )

        for sm in token_re.finditer(lang_inner):
            tok0 = sm.group(0)
            token_rel_a, token_rel_b = sm.span(0)
            token_abs_a = lang_inner_start_abs + token_rel_a
            token_abs_b = lang_inner_start_abs + token_rel_b

            if sm.group(1) is not None:
                # [["..."]]
                raw_inside = sm.group(2) or ""
                original = self._unescape_lua_string(raw_inside)
                results.append((original, (token_abs_a, token_abs_b), "bracket_quoted"))
                continue

            if tok0.startswith("[[") and tok0.endswith("]]") and (sm.group(4) is None):
                # [[...]] (sem quotes)
                inner = tok0[2:-2]
                results.append((inner, (token_abs_a, token_abs_b), "bracket"))
                continue

            if sm.group(4) is not None:
                # "..."
                raw_inside = sm.group(5) or ""
                original = self._unescape_lua_string(raw_inside)
                results.append((original, (token_abs_a, token_abs_b), "quoted"))
                continue

        return results

    def _find_matching_brace(self, text: str, open_pos: int) -> int | None:
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
