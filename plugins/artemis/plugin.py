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
        project = ctx.project or {}
        src_lang = self._normalize_lang_key((project.get("source_language") or "ja").strip())
        tgt_lang = (project.get("target_language") or "pt-BR").strip()

        entries: list[dict] = []

        # Procura por blocos "text = { ... }"
        for m in re.finditer(r"\btext\s*=\s*{", text):
            text_open_abs = m.end() - 1  # posição do "{"
            text_close_abs = self._find_matching_brace(text, text_open_abs)
            if text_close_abs is None:
                continue

            inner_start_abs = text_open_abs + 1
            inner_end_abs = text_close_abs
            block_inner = text[inner_start_abs:inner_end_abs]

            found = self._extract_lang_strings_abs(
                full_text=text,
                block_inner=block_inner,
                block_inner_start_abs=inner_start_abs,
                lang=src_lang,
            )

            # fallback simples
            if not found and src_lang != "ja":
                found = self._extract_lang_strings_abs(
                    full_text=text,
                    block_inner=block_inner,
                    block_inner_start_abs=inner_start_abs,
                    lang="ja",
                )

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
                            "span_abs": span_abs,          # token inteiro (inclui delimitadores)
                            "string_style": style,         # quoted | bracket | bracket_quoted
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
        if s in ("zh", "zh-cn", "zh-hans", "zh-sg", "zh-tw", "zh-hant", "zh-hk"):
            return "cn"
        if s in ("jp", "ja-jp"):
            return "ja"
        if "-" in s:
            s = s.split("-", 1)[0].strip()
        return s or "ja"

    def _format_replacement(self, s: str, style: str) -> str:
        if style == "bracket":
            # [[...]] não suporta ']]' dentro
            if "]]" in s:
                esc = self._escape_lua_string(s)
                return f"\"{esc}\""
            return f"[[{s}]]"

        if style == "bracket_quoted":
            esc = self._escape_lua_string(s)
            return f"[[\"{esc}\"]]"

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
        Retorna (texto, (a,b), style)
        style:
          - quoted: "..."
          - bracket: [[...]]
          - bracket_quoted: [["..."]]
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

        i = 0
        n = len(lang_inner)

        while i < n:
            ch = lang_inner[i]

            # [["..."]]
            if i + 3 < n and lang_inner[i:i+2] == "[[" and lang_inner[i+2] == "\"":
                start = i
                # procura o final "\"]]"
                j = i + 3
                esc = False
                while j < n:
                    c = lang_inner[j]
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == "\"":
                        # espera ]] depois
                        if j + 3 < n and lang_inner[j:j+4] == "\"]]":
                            end = j + 4
                            raw_inside = lang_inner[i+3:j]
                            original = self._unescape_lua_string(raw_inside)
                            a = lang_inner_start_abs + start
                            b = lang_inner_start_abs + end
                            results.append((original, (a, b), "bracket_quoted"))
                            i = end
                            break
                    j += 1
                else:
                    i += 1
                continue

            # [[...]]
            if i + 1 < n and lang_inner[i:i+2] == "[[":
                start = i
                end = lang_inner.find("]]", i + 2)
                if end != -1:
                    end2 = end + 2
                    inner = lang_inner[i+2:end]
                    a = lang_inner_start_abs + start
                    b = lang_inner_start_abs + end2
                    results.append((inner, (a, b), "bracket"))
                    i = end2
                    continue

            # "..."
            if ch == "\"":
                start = i
                j = i + 1
                esc = False
                while j < n:
                    c = lang_inner[j]
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == "\"":
                        end = j + 1
                        raw_inside = lang_inner[i+1:j]
                        original = self._unescape_lua_string(raw_inside)
                        a = lang_inner_start_abs + start
                        b = lang_inner_start_abs + end
                        results.append((original, (a, b), "quoted"))
                        i = end
                        break
                    j += 1
                else:
                    i += 1
                continue

            i += 1

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
