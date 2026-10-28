"""Markdown → HTML for QTextDocument (a port of web/src/lib/markdown.ts and the ``.md`` rules of
transcript.css).

GitHub-flavoured Markdown through markdown-it-py; fenced code is highlighted with Pygments into
inline-styled spans using the theme's ``cm_*`` colours, since QTextDocument knows no stylesheets.
Every size here is a whole pixel computed from the CSS (14.5px prose, line-height 1.65, headings at
1.32/1.16/1.04em…), because QTextDocument rounds ``font-size`` to a pixel and ignores ``em``.
What QTextDocument cannot draw from HTML — the rounded pill behind inline code, the rule beside a
blockquote, the rounded well of a code block, the hairlines of a table — is marked here (a
transparent background on the code span, a 14px left margin on the quote, a 100% wide table for the
fence) and painted by the view (widgets/transcript/items.py MarkdownView).
Tables, lists, links, images and task lists come through; raw HTML is dropped (QTextDocument would
render only some of it, and the web version sanitises it too).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
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

# The padding of an inline code pill (``.md :not(pre) > code { padding: 0.14em 0.38em }``): QTextDocument
# cannot pad a span, so two narrow no-break spaces (U+202F, glue: no line break around them) on either side make the room and the view paints the pill
# under them. They are stripped again when the text is copied.
PILL_PAD = "\u202f\u202f"
# The left margin that marks a blockquote block for the view (``padding-left: 14px`` plus the 2px rule).
QUOTE_INDENT = 14


# Inline code never wraps inside on the web (``white-space: nowrap``); QTextDocument has no span-level
# nowrap, so a word joiner (U+2060, a UAX #14 "no break" class) goes between the characters. Entities
# stay whole. The view strips the joiners again when the text is copied.
_UNIT_RE = re.compile(r"(&[#\w]+;|.)(?=.)", re.S)
NO_BREAK = "\u2060"


def _no_break(code_html: str) -> str:
    return _UNIT_RE.sub(lambda mt: mt.group(1) + NO_BREAK, code_html)


def lang_label(lang: str) -> str:
    return LANG_NAMES.get(lang.lower(), lang) if lang else "Text"


def px(v: float) -> int:
    """A CSS length rounded half up to the whole pixel QTextDocument draws with."""
    return int(v + 0.5)


@dataclass(frozen=True)
class Metrics:
    """The pixel sizes of one Markdown style: ``.md`` (14.5px, line-height 1.65) or ``.md-compact``/the
    quiet notes inside a fold (13px, line-height 1.6–1.65)."""

    size: float  # the CSS prose size (14.5px), which the em sizes below derive from
    base: int  # prose font size as drawn
    line: int  # prose line height
    para: int  # paragraph bottom margin (0.75em)
    code: int  # inline code font size (0.86em)
    list_indent: int  # text start of a list item (padding-left 1.45em + li padding 0.15em)
    li_gap: int  # li margin (0.22em)
    table: int  # table font size (0.93em)

    @staticmethod
    def of(size: float, line_height: float) -> Metrics:
        return Metrics(
            size=size,
            base=px(size),
            line=px(size * line_height),
            para=px(size * 0.75),
            code=px(size * 0.86),
            list_indent=px(size * 1.6),
            li_gap=px(size * 0.22),
            table=px(size * 0.93),
        )

    def heading(self, level: int) -> tuple:
        """(font px, line height px, margin top px, margin bottom px) for h1–h4 (h5/h6 read as h4)."""
        em = {1: 1.32, 2: 1.16, 3: 1.04}.get(level, 1.0)
        size = self.size * em
        return px(size), px(size * 1.3), px(size * 1.35), px(size * 0.45)


PROSE = Metrics.of(theme.FS_BODY, 1.65)
COMPACT = Metrics.of(theme.FS_UI, 1.65)


def metrics(compact: bool) -> Metrics:
    return COMPACT if compact else PROSE


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
    """The theme's syntax colours for a Pygments token type, after the web's Shiki theme (lib/highlight.ts)."""
    if ttype in Comment:
        return f"color:{t.cm_comment};font-style:italic"
    if ttype in Keyword.Constant:
        return f"color:{t.cm_number}"  # true, false, null: constant.language
    if ttype in Keyword.Type:
        return f"color:{t.cm_type}"
    if ttype in Keyword or ttype in Operator.Word:
        return f"color:{t.cm_keyword}"
    if ttype in String.Regex or ttype in String.Escape:
        return f"color:{t.cm_regex}"
    if ttype in String:
        return f"color:{t.cm_string}"
    if ttype in Number:
        return f"color:{t.cm_number}"
    if ttype in Name.Function or ttype in Name.Builtin or ttype in Name.Decorator:
        return f"color:{t.cm_function}"
    if ttype in Name.Class or ttype in Name.Exception or ttype in Name.Namespace:
        return f"color:{t.cm_type}"
    if ttype in Name.Tag:
        return f"color:{t.cm_tag}"
    if ttype in Name.Attribute or ttype in Name.Property or ttype in Name.Variable or ttype in Name.Constant:
        return f"color:{t.cm_property}"
    if ttype in Punctuation or ttype in Operator:
        return f"color:{t.cm_punct}"
    return None


def _call_names(tokens: list) -> set:
    """Indexes of plain names that are followed by ``(``: calls and declarations, which the web's
    grammar-based highlighter colours as functions (meta.function-call) and Pygments leaves plain."""
    out = set()
    for i, (ttype, value) in enumerate(tokens):
        if ttype not in Name or ttype in Name.Function or ttype in Name.Builtin or not value.strip():
            continue
        j = i + 1
        while j < len(tokens) and not tokens[j][1].strip():
            j += 1
        if j < len(tokens) and tokens[j][1].startswith("("):
            out.add(i)
    return out


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
    tokens = list(lex(code, lexer))
    calls = _call_names(tokens)
    out = []
    for i, (ttype, value) in enumerate(tokens):
        esc = html.escape(value)
        st = f"color:{t.cm_function}" if i in calls else _style_for(ttype, t)
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
    out = _post_process(out, t, base_dir, metrics(compact))
    return out


def stylesheet(t: theme.Tokens, m: Metrics) -> str:
    """The document stylesheet for one style: what the HTML's own ``style`` attributes do not say."""
    ui = theme._UI_FAMILY or "sans-serif"
    hair = theme.blend(t.hair, t.canvas).name()
    return (
        f"p {{ margin-top: 0; margin-bottom: {m.para}px; line-height: {m.line}px; }} "
        f"li {{ margin-top: {m.li_gap}px; margin-bottom: {m.li_gap}px; line-height: {m.line}px; }} "
        f"ul, ol {{ margin-top: 0; margin-bottom: {m.para}px; }} "
        f"blockquote {{ margin-left: {QUOTE_INDENT}px; margin-right: 0; margin-top: 0; margin-bottom: {m.para}px; "
        f"color: {t.text_2}; }} "
        f"a {{ color: {t.info}; text-decoration: none; }} "
        f"strong, b {{ font-weight: 620; }} "
        f"hr {{ margin-top: {px(m.base * 1.4) - 9}px; margin-bottom: {px(m.base * 1.4) - 9}px; color: {hair}; }} "
        f"th {{ font-weight: 600; color: {t.text_2}; }} "
        f"pre {{ font-family: '{theme._MONO_FAMILY or 'monospace'}'; margin: 0; }} "
        f"body {{ font-family: '{ui}'; }}"
    )


_FENCE_RE = re.compile(r"<pre><code([^>]*)>(.*?)</code></pre>", re.S)
_LANG_RE = re.compile(r'class="language-([^"]*)"')
_HEAD_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.S)
_TABLE_RE = re.compile(r"<table>(.*?)</table>", re.S)
_BLOCK_NL_RE = re.compile(r"(</(?:p|ul|ol|li|table|thead|tbody|tr|th|td|blockquote)>|<hr[^>]*>)\n")


def _post_process(body: str, t: theme.Tokens, base_dir: Optional[str], m: Metrics) -> str:
    mono = theme._MONO_FAMILY or "monospace"
    code_px = px(theme.MONO_SIZE)
    code_line = px(theme.MONO_SIZE * 1.6)

    def fence(mt: re.Match) -> str:
        lm = _LANG_RE.search(mt.group(1) or "")
        lang = html.unescape(lm.group(1)) if lm else ""
        code = html.unescape(mt.group(2)).rstrip("\n")
        body_html = highlight_html(code, lang, t.name)
        label = html.escape(lang_label(lang))
        # ``.md-code``: a collapsed 100%-wide table marks the well (the view paints its rounded
        # background); the head row is 30px with the language in micro text; the code sits in
        # padding 0 14px 13px.
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;margin-top:{px(m.base * 0.6)}px;margin-bottom:{m.base}px">'
            f'<tr><td style="padding-left:14px;padding-right:10px;padding-top:8px;padding-bottom:8px">'
            f'<p style="margin:0;line-height:14px;font-size:{theme.FS_MICRO}px;color:{t.text_3}">{label}</p></td></tr>'
            f'<tr><td style="padding-left:14px;padding-right:14px;padding-top:0;padding-bottom:13px">'
            f"<pre style=\"font-family:'{mono}';font-size:{code_px}px;line-height:{code_line}px;margin:0;"
            f'white-space:pre-wrap">{body_html}</pre></td></tr></table>'
        )

    body = _FENCE_RE.sub(fence, body)
    # Inline code: mono at 0.86em on a pill the view paints; the transparent background is its marker.
    body = re.sub(
        r"<code>(.*?)</code>",
        lambda mt: (
            f"<code style=\"font-family:'{mono}';font-size:{m.code}px;background-color:rgba(0,0,0,0);white-space:pre\">"
            f"{_no_break(PILL_PAD + mt.group(1) + PILL_PAD)}</code>"  # the pads stay with the code
        ),
        body,
        flags=re.S,
    )
    body = _HEAD_RE.sub(lambda mt: _heading(mt, t, m), body)
    body = _TABLE_RE.sub(lambda mt: _table(mt, t, m), body)
    body = body.replace("<hr>", '<hr width="100%">')
    body = re.sub(r"<img src=\"(?!https?:|data:|/)([^\"]+)\"", lambda mt: _img(mt, base_dir), body)
    body = body.replace("<p>", f'<p style="line-height:{m.line}px">')
    # the newline markdown-it puts after a block would end its paragraph with a space in QTextDocument
    return _BLOCK_NL_RE.sub(r"\1", body).rstrip("\n")


def _heading(mt: re.Match, t: theme.Tokens, m: Metrics) -> str:
    # Headings are paragraphs with explicit sizes: QTextDocument's <h1>–<h6> scale the font again on top
    # of a font-size, and the web's sizes are em multiples of the prose.
    level = int(mt.group(1))
    size, line, top, bottom = m.heading(level)
    color = f";color:{t.text_2}" if level >= 4 else ""
    return (
        f'<p style="font-size:{size}px;font-weight:600;line-height:{line}px;margin-top:{top}px;'
        f'margin-bottom:{bottom}px;letter-spacing:-{size * 0.012:.2f}px{color}">{mt.group(2)}</p>'
    )


def _table(mt: re.Match, t: theme.Tokens, m: Metrics) -> str:
    # ``.md table``: 0.93em, cells padded 7px 14px 7px 0; the hairline under each row is painted by the view.
    inner = mt.group(1)
    inner = re.sub(
        r"<(t[hd])( style=\"[^\"]*\")?>",
        lambda c: (
            f'<{c.group(1)} style="padding-left:0;padding-right:14px;padding-top:7px;padding-bottom:7px;'
            f'font-size:{m.table}px;text-align:left;{(c.group(2) or "")[8:-1]}">'
        ),
        inner,
    )
    return (
        f'<table width="60%" cellpadding="0" cellspacing="0" '
        f'style="margin-top:{px(m.base * 0.4)}px;margin-bottom:{m.base}px">{inner}</table>'
    )


def _img(mt: re.Match, base_dir: Optional[str]) -> str:
    if not base_dir:
        return mt.group(0)
    return f'<img src="{html.escape(_join(base_dir, html.unescape(mt.group(1))))}"'


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
