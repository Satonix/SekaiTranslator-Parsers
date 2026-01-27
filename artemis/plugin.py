# parsers/plugins/artemis/plugin.py
from __future__ import annotations

import re
from parsers.base import ParseContext


# Detecta início de estruturas principais
RE_ASTVER = re.compile(r"^\s*astver\s*=")
RE_AST_ROOT = re.compile(r"^\s*ast\s*=\s*{")
RE_TEXT_BLOCK = re.compile(r"^\s*text\s*=\s*{")
RE_JA_BLOCK = re.compile(r"^\s*ja\s*=\s*{")

# Pega a primeira string "..." (típico: "texto",)
RE_FIRST_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')


def _is_likely_artemis_ast(text: str) -> bool:
    """
    Heurística:
    - procura astver= OU ast={ no começo do arquivo
    """
    head = "\n".join(text.splitlines()[:80])
    if RE_ASTVER.search(head):
        return True
    if RE_AST_ROOT.search(head):
        return True
    return False


class ArtemisAstPlugin:
    plugin_id = "artemis.ast"
    name = "Artemis AST (.ast/.txt)"
    extensions = {".ast", ".txt"}

    def detect(self, ctx: ParseContext, text: str) -> float:
        ext = ctx.path.suffix.lower()

        # .ast quase sempre é Artemis AST
        if ext == ".ast":
            return 0.95

        # .txt pode ser AST ou texto simples
        if ext == ".txt" and _is_likely_artemis_ast(text):
            return 0.85

        # Se não parecer AST, ainda pode servir como fallback leve pra .txt
        if ext == ".txt":
            return 0.25

        return 0.0

    # ----------------------------
    # PARSE
    # ----------------------------
    def parse(self, ctx: ParseContext, text: str) -> list[dict]:
        # Se não for AST, trata como texto simples (para .txt)
        if not _is_likely_artemis_ast(text) and ctx.path.suffix.lower() == ".txt":
            return self._parse_plain_text(text)

        return self._parse_ast_like(text)

    def _parse_plain_text(self, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines()

        for i, ln in enumerate(lines):
            entries.append({
                "entry_id": str(i),
                "original": ln,
                "translation": "",
                "status": "untranslated",
                "speaker": "",
                "is_translatable": True,
                "_artemis_kind": "plain",
                "_artemis_raw_line": ln,
            })
        return entries

    def _parse_ast_like(self, text: str) -> list[dict]:
        entries: list[dict] = []
        lines = text.splitlines()

        in_text_block = False
        text_brace_depth = 0

        in_ja_block = False
        ja_brace_depth = 0

        # helper: conta { e } ignorando strings (simples o suficiente pro caso)
        def brace_delta(line: str) -> int:
            # remove strings "..." pra não contar chaves dentro de texto
            s = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
            return s.count("{") - s.count("}")

        for i, ln in enumerate(lines):
            stripped = ln.strip()

            # Atualiza estados de entrada
            if not in_text_block and RE_TEXT_BLOCK.match(stripped):
                in_text_block = True
                text_brace_depth = 0  # vamos somar a partir desta linha

            if in_text_block:
                text_brace_depth += brace_delta(ln)
                # Se acabou o text = { ... }
                if text_brace_depth <= 0 and stripped.endswith("},"):
                    in_text_block = False
                    in_ja_block = False
                    ja_brace_depth = 0

            # Dentro do text block, detecta ja = {
            if in_text_block and not in_ja_block and RE_JA_BLOCK.match(stripped):
                in_ja_block = True
                ja_brace_depth = 0

            if in_ja_block:
                ja_brace_depth += brace_delta(ln)
                # Se terminou o ja = { ... }
                if ja_brace_depth <= 0 and stripped.endswith("},"):
                    in_ja_block = False

            # Regra Opção A: dentro do ja block, 1 entry por linha com "..."
            if in_text_block and in_ja_block:
                m = RE_FIRST_QUOTED.search(ln)
                if m:
                    original = m.group(1)
                    prefix = ln[:m.start(0)]
                    suffix = ln[m.end(0):]

                    entries.append({
                        "entry_id": str(i),
                        "original": original,
                        "translation": "",
                        "status": "untranslated",
                        "speaker": "",
                        "is_translatable": True,
                        "_artemis_kind": "ast",
                        "_artemis_line_index": i,
                        "_artemis_prefix": prefix,
                        "_artemis_suffix": suffix,
                    })
                else:
                    # linha dentro de ja sem texto (ou só chaves/vazias) = preserva
                    entries.append({
                        "entry_id": f"{i}:raw",
                        "original": ln,
                        "translation": "",
                        "status": "untranslated",
                        "speaker": "",
                        "is_translatable": False,
                        "_artemis_kind": "raw",
                        "_artemis_line_index": i,
                        "_artemis_raw_line": ln,
                    })
            else:
                # fora do ja: preserva como não traduzível
                entries.append({
                    "entry_id": f"{i}:raw",
                    "original": ln,
                    "translation": "",
                    "status": "untranslated",
                    "speaker": "",
                    "is_translatable": False,
                    "_artemis_kind": "raw",
                    "_artemis_line_index": i,
                    "_artemis_raw_line": ln,
                })

        return entries

    # ----------------------------
    # REBUILD
    # ----------------------------
    def rebuild(self, ctx: ParseContext, entries: list[dict]) -> str:
        # Decide modo pelo que veio no parse
        kind = ""
        for e in entries:
            kind = (e.get("_artemis_kind") or "").strip()
            if kind:
                break

        if kind == "plain":
            out: list[str] = []
            for e in entries:
                tr = (e.get("translation") or "").strip()
                orig = e.get("original") or ""
                out.append(tr if tr else orig)
            return "\n".join(out)

        # AST-like: reconstruir por linha, usando prefix/suffix ou raw_line
        # Vamos reconstruir as linhas em um dict (line_index -> line_text)
        lines_by_index: dict[int, str] = {}

        for e in entries:
            idx = e.get("_artemis_line_index")
            if not isinstance(idx, int):
                continue

            if not e.get("is_translatable", True):
                raw = e.get("_artemis_raw_line")
                if isinstance(raw, str):
                    lines_by_index[idx] = raw
                else:
                    lines_by_index[idx] = e.get("original") or ""
                continue

            prefix = e.get("_artemis_prefix") or ""
            suffix = e.get("_artemis_suffix") or ""

            tr = (e.get("translation") or "").strip()
            orig = e.get("original") or ""
            text = tr if tr else orig

            # re-escapa aspas se necessário (AST usa "")
            text = text.replace('"', r'\"')

            lines_by_index[idx] = f'{prefix}"{text}"{suffix}'

        # Agora monta em ordem do maior índice que apareceu
        if not lines_by_index:
            return ""

        max_idx = max(lines_by_index.keys())
        out_lines: list[str] = []
        for i in range(max_idx + 1):
            out_lines.append(lines_by_index.get(i, ""))

        return "\n".join(out_lines)


def get_plugin():
    return ArtemisAstPlugin()
