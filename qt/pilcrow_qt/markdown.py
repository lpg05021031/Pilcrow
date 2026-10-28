"""Markdown → HTML for QTextDocument (a port of web/src/lib/markdown.ts).

GitHub-flavoured Markdown through markdown-it-py; fenced code is highlighted with Pygments into
inline-styled spans using the theme's ``cm_*`` colours, since QTextDocument knows no stylesheets.
Tables, lists, links, images and task lists come through; raw HTML is dropped (QTextDocument would
render only some of it, and the web version sanitises it too).
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from typing import Optional

from . import theme

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - the package is in requirements.txt
    MarkdownIt = None  # type: ignore[assignment]

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String, Token
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover
    lex = None  # type: ignore[assignment]

LANG_NAMES = {
    "ts": "TypeScript",
    "tsx": "TSX",
    "typescript": "TypeScript",
    "js": "JavaScript",
    "jsx": "JSX",
    "javascript": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "py": "Python",
    "python": "Python",
    "sh": "Shell",
    "bash": "Shell",
    "zsh": "Shell",
    "shell": "Shell",
    "console": "Terminal",
    "json": "JSON",
    "jsonc": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "toml": "TOML",
    "md": "Markdown",
    "markdown": "Markdown",
    "html": "HTML",
    "css": "CSS",
    "scss": "SCSS",
    "sql": "SQL",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "c++": "C++",
    "cc": "C++",
    "hpp": "C++",
    "cs": "C#",
    "java": "Java",
    "kt": "Kotlin",
    "go": "Go",
    "rs": "Rust",
    "rust": "Rust",
    "rb": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "diff": "Diff",
    "patch": "Diff",
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "make": "Makefile",
    "verilog": "Verilog",
    "v": "Verilog",
    "sv": "SystemVerilog",
    "systemverilog": "SystemVerilog",
    "vhdl": "VHDL",
    "tcl": "Tcl",
    "perl": "Perl",
    "pl": "Perl",
    "r": "R",
    "matlab": "MATLAB",
    "lua": "Lua",
    "xml": "XML",
    "ini": "INI",
    "text": "Text",
    "txt": "Text",
    "plaintext": "Text",
    "csv": "CSV",
    "graphql": "GraphQL",
    "proto": "Protobuf",
}
# Pygments lexer aliases for the names Claude writes in fences.
LEXER_ALIAS = {
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
    "console": "console",
    "jsonc": "json",
    "tsx": "tsx",
    "jsx": "jsx",
    "vue": "html",
    "svelte": "html",
    "sv": "systemverilog",
    "v": "verilog",
    "make": "makefile",
    "c++": "cpp",
    "txt": "text",
    "plaintext": "text",
}


def lang_label(lang: str) -> str:
    return LANG_NAMES.get(lang.lower(), lang) if lang else "Text"


def _md() -> MarkdownIt:
    md = MarkdownIt("gfm-like" if MarkdownIt else "commonmark", {"html": False, "linkify": True, "typographer": False})
    try:
        md.enable("table")
        md.enable("strikethrough")
    except Exception:  # noqa: BLE001
        pass
    return md


_parser: Optional[MarkdownIt] = None


def parser() -> MarkdownIt:
    global _parser
    if _parser is None:
        _parser = _md()
    return _parser


# ---------------------------------------------------------------- code highlighting


def _style_for(ttype, t: theme.Tokens) -> Optional[str]:
    if ttype in Comment:
        return f"color:{t.cm_comment};font-style:italic"
    if ttype in Keyword or ttype in Operator.Word:
        return f"color:{t.cm_keyword}"
    if ttype in String:
        return f"color:{t.cm_string}"
    if ttype in Number:
        return f"color:{t.cm_number}"
    if ttype in Name.Function or ttype in Name.Function.Magic:
        return f"color:{t.cm_function}"
    if ttype in Name.Class or ttype in Name.Builtin or ttype in Keyword.Type or ttype in Name.Exception:
        return f"color:{t.cm_type}"
    if ttype in Name.Tag:
        return f"color:{t.cm_tag}"
    if ttype in Name.Attribute or ttype in Name.Property or ttype in Name.Variable:
        return f"color:{t.cm_property}"
    if ttype in String.Regex:
        return f"color:{t.cm_regex}"
    if ttype in Punctuation or ttype in Operator:
        return f"color:{t.cm_punct}"
    return None


@lru_cache(maxsize=512)
def highlight_html(code: str, lang: str, theme_name: str) -> str:
    """Code as ``<span style="color:…">`` runs, escaped; plain escaped text when Pygments has no lexer."""
    t = theme.DARK if theme_name == "dark" else theme.LIGHT
    if lex is None:
        return html.escape(code)
    name = LEXER_ALIAS.get(lang.lower(), lang.lower()) if lang else ""
    try:
        lexer = get_lexer_by_name(name, stripnl=False) if name else None
    except ClassNotFound:
        lexer = None
    if lexer is None:
        if not name and len(code) < 20000:
            try:
                lexer = guess_lexer(code, stripnl=False)
            except ClassNotFound:
                lexer = None
        if lexer is None:
            return html.escape(code)
    out = []
    for ttype, value in lex(code, lexer):
        esc = html.escape(value)
        st = _style_for(ttype, t)
        out.append(f'<span style="{st}">{esc}</span>' if st else esc)
    return "".join(out)


# ---------------------------------------------------------------- rendering


def render_html(src: str, streaming: bool = False, base_dir: Optional[str] = None, compact: bool = False) -> str:
    """Markdown → HTML body for ``QTextDocument.setHtml``/``QLabel`` rich text."""
    if not src:
        return ""
    text = src
    # While streaming, close an unterminated code fence so the block renders as code.
    if streaming and len(re.findall(r"^```", text, flags=re.M)) % 2 == 1:
        text += "\n```"
    t = theme.tokens()
    if MarkdownIt is None:
        return f"<p>{html.escape(text)}</p>"
    md = parser()
    tokens = md.parse(text)
    for tok in tokens:
        if tok.type == "fence" or tok.type == "code_block":
            lang = (tok.info or "").strip().split(" ")[0] if tok.type == "fence" else ""
            tok.attrSet("data-lang", lang)
    out = md.renderer.render(tokens, md.options, {})
    out = _post_process(out, t, base_dir, compact)
    return out


_FENCE_RE = re.compile(r'<pre><code(?: class="language-([^"]*)")?>(.*?)</code></pre>', re.S)


def _post_process(body: str, t: theme.Tokens, base_dir: Optional[str], compact: bool) -> str:
    mono = theme._MONO_FAMILY or "monospace"
    inset = t.inset
    hair = theme.blend(t.hair, t.canvas).name()

    def fence(m: re.Match) -> str:
        lang = html.unescape(m.group(1) or "")
        code = html.unescape(m.group(2)).rstrip("\n")
        body_html = highlight_html(code, lang, t.name)
        label = html.escape(lang_label(lang))
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0"><tr><td style="background:{inset};border:1px solid {hair};border-radius:{theme.R2}px;padding:0">'
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:4px 10px;color:{t.text_3};font-size:{theme.FS_MICRO}px;border-bottom:1px solid {hair}">{label}</td></tr>'
            f'<tr><td style="padding:8px 10px"><pre style="font-family:\'{mono}\';font-size:{theme.MONO_SIZE}px;margin:0;white-space:pre-wrap">{body_html}</pre></td></tr></table></td></tr></table>'
        )

    body = _FENCE_RE.sub(fence, body)
    body = body.replace(
        "<code>",
        f"<code style=\"font-family:'{mono}';font-size:{theme.MONO_SIZE}px;background:{theme.blend(t.fill_1, t.canvas).name()};border-radius:3px\">",
    )
    body = re.sub(r"<a href=", f'<a style="color:{t.accent_text};text-decoration:none" href=', body)
    body = body.replace(
        "<blockquote>",
        f'<blockquote style="border-left:2px solid {theme.blend(t.hair_3, t.canvas).name()};margin:6px 0;padding-left:10px;color:{t.text_2}">',
    )
    body = body.replace(
        "<table>", f'<table cellpadding="5" cellspacing="0" style="border:1px solid {hair};margin:6px 0">'
    )
    body = body.replace(
        "<th>",
        f'<th style="border-bottom:1px solid {hair};background:{theme.blend(t.fill_1, t.canvas).name()};text-align:left">',
    )
    body = body.replace("<hr>", f'<hr style="border:none;border-top:1px solid {hair}">')
    body = re.sub(
        r"<h([1-6])>",
        lambda m: (
            f'<h{m.group(1)} style="font-size:{[theme.FS_DISPLAY, theme.FS_TITLE, theme.FS_H, theme.FS_H, theme.FS_UI, theme.FS_UI][int(m.group(1)) - 1]}px;font-weight:600;margin:10px 0 4px">'
        ),
        body,
    )
    if base_dir:
        body = re.sub(
            r'<img src="(?!https?:|data:|/)([^"]+)"',
            lambda m: f'<img src="{html.escape(_join(base_dir, html.unescape(m.group(1))))}"',
            body,
        )
    if compact:
        body = body.replace("<p>", '<p style="margin:0">')
    return body


def _join(base: str, rel: str) -> str:
    base = base.rstrip("/")
    rel = rel[2:] if rel.startswith("./") else rel
    return f"{base}/{rel}"


def split_blocks(src: str) -> list[str]:
    """Top-level Markdown blocks, so a streaming reply re-renders only the block being written."""
    if not src:
        return []
    if MarkdownIt is None:
        return [src]
    try:
        tokens = parser().parse(src)
    except Exception:  # noqa: BLE001
        return [src]
    lines = src.split("\n")
    out: list[str] = []
    depth = 0
    for tok in tokens:
        if tok.nesting == 1:
            if depth == 0 and tok.map:
                out.append("\n".join(lines[tok.map[0] : tok.map[1]]))
            depth += 1
        elif tok.nesting == -1:
            depth -= 1
        elif depth == 0 and tok.map:
            out.append("\n".join(lines[tok.map[0] : tok.map[1]]))
    return out or [src]


PATH_RE = re.compile(r"^(?:\.{0,2}/)?(?:[\w@.+-]+/)*[\w@.+-]+\.[A-Za-z0-9]{1,10}(?::(\d+)(?:-\d+)?)?$")
KNOWN_EXT = set(
    "ts tsx js jsx mjs cjs mts cts json jsonc md mdx css scss sass less html htm vue svelte astro py pyi rb go rs java kt kts swift m mm c h cc cpp hpp cs fs php pl sh bash zsh fish ps1 psm1 bat cmd yml yaml toml ini cfg conf env txt log sql graphql gql proto xml svg png jpg jpeg gif webp ico pdf csv tsv lock map wasm dockerfile makefile gradle properties plist tf hcl ex exs erl hs ml elm clj cljs scala dart lua r jl nim zig v sol ipynb".split()
)


def as_path_ref(text: str) -> Optional[dict]:
    """Does an inline code span look like a file path (optionally with :line)?"""
    t = text.strip()
    if len(t) > 240 or re.search(r"\s", t) or re.match(r"^https?:", t, re.I):
        return None
    m = PATH_RE.match(t)
    if not m:
        return None
    if "/" not in t and re.sub(r":(\d+)(-\d+)?$", "", t).split(".")[-1].lower() not in KNOWN_EXT:
        return None
    return {"path": re.sub(r":(\d+)(-\d+)?$", "", t), "line": int(m.group(1)) if m.group(1) else None}


_ = Token
