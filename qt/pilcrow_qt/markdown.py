"""Markdown → HTML for QTextDocument (a port of web/src/lib/markdown.ts and the ``.md`` rules of
transcript.css).

GitHub-flavoured Markdown through markdown-it-py; fenced code is highlighted with Pygments into
inline-styled spans using the theme's ``cm_*`` colours, since QTextDocument knows no stylesheets.
Every size here is a whole pixel computed from the CSS (14.5px prose, line-height 1.65, headings at
1.32/1.16/1.04em…), because QTextDocument rounds ``font-size`` to a pixel and ignores ``em``.
What QTextDocument cannot draw from HTML — the rounded pill behind inline code, the rule beside a
blockquote, the rounded well of a code block, the hairlines of a table — is marked here (a
transparent background on the code span, a 14px left margin on the quote, a collapsed 100% wide
table for the fence) and painted by the view (canvas/paint.py ``paint_decor``).

What QTextDocument cannot lay out from HTML is laid out here: a table's columns are sized the way a
browser's auto layout sizes them (from the widest word and the widest line of every cell, within the
column's width), a code line that does not fit wraps with its continuation indented past the line's
own indentation, list markers are quiet and change shape as lists nest, and a task list draws its
boxes in place of bullets.
Tables, lists, links, images and task lists come through; raw HTML is dropped (QTextDocument would
render only some of it, and the web version sanitises it too).
"""

from __future__ import annotations

import html
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtGui import QFontMetricsF

from . import theme

try:
    from markdown_it import MarkdownIt
    from markdown_it.token import Token as MdToken
except ImportError:  # pragma: no cover - the package is in requirements.txt
    MarkdownIt = None  # type: ignore[assignment]
    MdToken = None  # type: ignore[assignment]

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

# The padding of an inline code pill (``.md :not(pre) > code { padding: 0.14em 0.38em }``):
# QTextDocument cannot pad a span, so a narrow no-break space (U+202F, glue: no line break around it)
# on either side makes the room and the view paints the pill under it. In the mono face a space is a
# full cell, far wider than 0.38em, so the pad is set at the font size that makes it that wide
# (``pill_pad_px``). The pads are stripped again when the text is copied.
PILL_PAD = "\u202f"
PILL_PAD_EM = 0.38
# The left margin that marks a blockquote block for the view (``padding-left: 14px`` plus the 2px rule).
QUOTE_INDENT = 14
# A table cell's right padding (``.md td { padding: 7px 14px 7px 0 }``).
CELL_PAD = 14
# The image resources a task list's boxes are drawn from (the measurer registers them on the document).
CHECK_ON, CHECK_OFF = "pilcrow-check:on", "pilcrow-check:off"
CHECK_SIZE = 14
# A code line that wraps continues this many characters past the line's own indentation.
WRAP_INDENT = 2
# A tab in code is this many mono columns: the documents' tab stops (``Measurer._doc``) and the
# hanging indent of a tab-indented line (``code_lines_html``, ``grow.FenceGrower``) agree on it.
TAB_COLS = 4
# A table column squeezed for room keeps at least this many characters of prose a line.
MIN_CELL_CHARS = 6
HAN = re.compile("[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+")


# Inline code never wraps inside on the web (``white-space: nowrap``); QTextDocument has no span-level
# nowrap, so a word joiner (U+2060, a UAX #14 "no break" class) goes between the characters. Entities
# stay whole. The view strips the joiners again when the text is copied.
_UNIT_RE = re.compile(r"(&[#\w]+;|.)(?=.)", re.S)
NO_BREAK = "\u2060"


def _no_break(code_html: str) -> str:
    return _UNIT_RE.sub(lambda mt: mt.group(1) + NO_BREAK, code_html)


def pill_pad_px(m: Metrics) -> int:
    """The font size at which one pad character is as wide as the pill's side padding (0.38em of the
    code size), measured from the mono face's own advance for it."""
    adv = font_metrics(m.code, mono=True).horizontalAdvance(PILL_PAD)
    if adv <= 0:
        return max(1, m.code // 2)
    return max(1, px(PILL_PAD_EM * m.code * m.code / adv))


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
_scaled: dict = {}


def metrics(compact: bool) -> Metrics:
    """The prose or compact metrics at the theme's text scale."""
    k = theme.scale
    if k == 1.0:
        return COMPACT if compact else PROSE
    key = (compact, k)
    m = _scaled.get(key)
    if m is None:
        m = _scaled[key] = Metrics.of((theme.FS_UI if compact else theme.FS_BODY) * k, 1.65)
    return m


def metrics_for(size: float, line_height: float = 1.65) -> Metrics:
    """The metrics of a Markdown style at any size (a thought is 13.5px) at the theme's text scale."""
    key = (round(size, 2), line_height, theme.scale)
    m = _scaled.get(key)
    if m is None:
        m = _scaled[key] = Metrics.of(size * theme.scale, line_height)
    return m


_fms: Dict[Tuple, QFontMetricsF] = {}


def font_metrics(size: float, mono: bool = False, weight: int = 400) -> QFontMetricsF:
    """Metrics of the UI or mono face at a pixel size, kept per size (the faces never change)."""
    key = (round(size, 2), mono, weight, theme._UI_FAMILY, theme._MONO_FAMILY)
    fm = _fms.get(key)
    if fm is None:
        fm = _fms[key] = QFontMetricsF(theme.font_mono(size, weight) if mono else theme.font_ui(size, weight))
    return fm


def mono_size() -> float:
    """The size of code in prose at the theme's text scale."""
    return theme.MONO_SIZE * theme.scale


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


def _lexer(lang: str, code: str):
    if lex is None:
        return None
    name = LEXER_ALIAS.get(lang.lower(), lang.lower()) if lang else ""
    try:
        return get_lexer_by_name(name, stripnl=False) if name else None
    except ClassNotFound:
        pass
    if not name and len(code) < 20000:
        try:
            return guess_lexer(code, stripnl=False)
        except ClassNotFound:
            return None
    return None


def _styled(code: str, lang: str, t: theme.Tokens) -> List[Tuple[str, Optional[str]]]:
    """``code`` as (text, style) runs: the theme's colour for each Pygments token (None for plain),
    calls coloured as functions, and CJK text inside an italic style (comments) set upright on its
    own, since CJK faces have no italic and Qt would slant them (``font-synthesis-style: none``)."""
    lexer = _lexer(lang, code) if len(code) <= 200000 else None
    if lexer is None:
        return [(code, None)]
    tokens = list(lex(code, lexer))
    calls = _call_names(tokens)
    out: List[Tuple[str, Optional[str]]] = []
    for i, (ttype, value) in enumerate(tokens):
        if not value:
            continue
        st = f"color:{t.cm_function}" if i in calls else _style_for(ttype, t)
        if st and "italic" in st and HAN.search(value):
            up = st.replace(";font-style:italic", "")
            pos = 0
            for mt in HAN.finditer(value):
                if mt.start() > pos:
                    out.append((value[pos : mt.start()], st))
                out.append((mt.group(0), up))
                pos = mt.end()
            if pos < len(value):
                out.append((value[pos:], st))
        else:
            out.append((value, st))
    return out


HL_CACHE_CHARS = 4_000_000  # of source kept highlighted: a session of big fences cannot pin memory
HL_CACHE_ENTRIES = 512
_hl_cache: OrderedDict[Tuple[str, str, str], Tuple[str, ...]] = OrderedDict()
_hl_chars = 0


def highlight_lines(code: str, lang: str, theme_name: str) -> Tuple[str, ...]:
    """Each line of ``code`` as escaped HTML with the theme's syntax colours (``<span style="color:…">``
    runs); plain escaped lines when Pygments has no lexer. The results are kept, least recently used
    first out, under a budget of source characters as well as a count."""
    global _hl_chars
    key = (code, lang, theme_name)
    hit = _hl_cache.get(key)
    if hit is not None:
        _hl_cache.move_to_end(key)
        return hit
    out = tuple(_highlight_lines(code, lang, theme_name))
    if len(code) <= HL_CACHE_CHARS // 4:
        _hl_cache[key] = out
        _hl_chars += len(code)
        while _hl_cache and (_hl_chars > HL_CACHE_CHARS or len(_hl_cache) > HL_CACHE_ENTRIES):
            (old_code, _l, _t), _v = _hl_cache.popitem(last=False)
            _hl_chars -= len(old_code)
    return out


def highlight_cache_size() -> Tuple[int, int]:
    """(entries, source characters) the highlight cache holds."""
    return len(_hl_cache), _hl_chars


def _highlight_lines(code: str, lang: str, theme_name: str) -> List[str]:
    t = theme.DARK if theme_name == "dark" else theme.LIGHT
    lines = code.split("\n")
    out: List[str] = [""]
    for value, st in _styled(code, lang, t):
        for k, part in enumerate(value.split("\n")):
            if k:
                out.append("")
            if part:
                esc = html.escape(part)
                out[-1] += f'<span style="{st}">{esc}</span>' if st else esc
    out = out[: len(lines)]
    while len(out) < len(lines):
        out.append("")
    return out


def token_runs(line: str, lang: str, theme_name: str) -> List[Tuple[int, int, str]]:
    """One line of code as (start, end, style) runs for a cursor to colour; plain stretches are left out."""
    t = theme.DARK if theme_name == "dark" else theme.LIGHT
    out: List[Tuple[int, int, str]] = []
    pos = 0
    for value, st in _styled(line, lang, t):
        if st:
            out.append((pos, pos + len(value), st))
        pos += len(value)
    return out


def highlight_html(code: str, lang: str, theme_name: str) -> str:
    """Code as ``<span style="color:…">`` runs, escaped, lines joined by newlines."""
    return "\n".join(highlight_lines(code, lang, theme_name))


# ---------------------------------------------------------------- rendering


def render_html(
    src: str,
    streaming: bool = False,
    base_dir: Optional[str] = None,
    compact: bool = False,
    width: int = 0,
    m: Optional[Metrics] = None,
) -> str:
    """Markdown → HTML body for ``QTextDocument.setHtml``/``QLabel`` rich text. ``width`` is the room
    the document will be laid out in: tables size their columns to it (0: no table sizing)."""
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
    mm = m or metrics(compact)
    _shape_lists(tokens, t, mm)
    for tok in tokens:
        if tok.type == "fence" or tok.type == "code_block":
            lang = (tok.info or "").strip().split(" ")[0] if tok.type == "fence" else ""
            tok.attrSet("data-lang", lang)
    out = md.renderer.render(tokens, md.options, {})
    out = _post_process(out, t, base_dir, mm, width, streaming)
    return out


def render_inline(text: str, m: Metrics, colour: bool = True) -> str:
    """The inline Markdown of one list item or table cell as HTML for a cursor to insert: the same
    code pills and links the block render gives it, wrapped in the text colour when ``colour`` (the
    words of a list item, whose marker is in the quiet colour)."""
    t = theme.tokens()
    if MarkdownIt is None:
        return html.escape(text)
    body = parser().renderInline(text)
    body = _inline_code(body, t, m)
    body = re.sub(r"<img src=\"(?!https?:|data:|/|pilcrow-check:)([^\"]+)\"", lambda mt: _img(mt, None), body)
    return f'<span style="color:{t.text}">{body}</span>' if colour else body


def fence_sources(src: str) -> List[str]:
    """The code of every fence in ``src``, in order (what a well's Copy copies)."""
    if not src or MarkdownIt is None:
        return []
    try:
        tokens = parser().parse(src)
    except Exception:  # noqa: BLE001
        return []
    return [tok.content.rstrip("\n") for tok in tokens if tok.type in ("fence", "code_block")]


TASK_RE = re.compile(r"^\[([ xX])\]\s+")
LIST_STYLES = ("disc", "circle", "square")


def _shape_lists(tokens: list, t: theme.Tokens, m: Metrics) -> None:
    """Lists as the web shows them, decided on the token stream: markers in ``text_3`` with the words
    in ``text`` (``li::marker``), the marker shape changing as bullets nest, and a task list
    (``- [ ]``) drawing a box in the marker's place instead of a bullet (after the bullet when only
    some items are tasks)."""
    if MdToken is None:
        return
    n = len(tokens)
    depth = 0  # open bullet lists
    in_item = 0  # open list items
    task_lists: set = set()  # indexes of bullet_list_open tokens whose every item is a task
    # pass 1: which bullet lists are task lists
    for i, tok in enumerate(tokens):
        if tok.type != "bullet_list_open":
            continue
        items = 0
        tasks = 0
        j = i + 1
        while j < n and not (tokens[j].type == "bullet_list_close" and tokens[j].level == tok.level):
            tj = tokens[j]
            if tj.type == "list_item_open" and tj.level == tok.level + 1:
                items += 1
                if (
                    j + 2 < n
                    and tokens[j + 1].type == "paragraph_open"
                    and tokens[j + 2].type == "inline"
                    and TASK_RE.match(tokens[j + 2].content or "")
                ):
                    tasks += 1
            j += 1
        if items and items == tasks:
            task_lists.add(i)
    # pass 2: attributes and the colour spans
    stack: List[bool] = []  # per open list: is it a task list
    for i, tok in enumerate(tokens):
        ty = tok.type
        if ty == "bullet_list_open":
            depth += 1
            style = f"list-style-type:{LIST_STYLES[(depth - 1) % 3]}"
            if i in task_lists:
                style = "list-style-type:none"
            tok.attrSet("style", style)
            stack.append(i in task_lists)
        elif ty == "ordered_list_open":
            stack.append(False)
        elif ty in ("bullet_list_close", "ordered_list_close"):
            if ty == "bullet_list_close":
                depth -= 1
            if stack:
                stack.pop()
        elif ty == "list_item_open":
            in_item += 1
            style = f"color:{t.text_3}"
            if stack and stack[-1]:
                style += f";text-indent:-{m.list_indent}px"
            tok.attrSet("style", style)
        elif ty == "list_item_close":
            in_item -= 1
        elif ty == "inline" and in_item and tok.children is not None:
            kids = tok.children
            head = f'<span style="color:{t.text}">'
            task = None
            if stack and kids and kids[0].type == "text" and i >= 2:
                mt = TASK_RE.match(kids[0].content)
                if mt and tokens[i - 1].type == "paragraph_open" and tokens[i - 2].type == "list_item_open":
                    task = mt.group(1) != " "
                    kids[0].content = kids[0].content[mt.end() :]
            if task is not None:
                # the box: in the marker's place in a task list, after the bullet in a mixed one
                src = CHECK_ON if task else CHECK_OFF
                head = (
                    f'<img src="{src}" width="{m.list_indent}" height="{CHECK_SIZE}" style="vertical-align:middle">'
                    + head
                )
            kids.insert(0, MdToken("html_inline", "", 0, content=head))
            kids.append(MdToken("html_inline", "", 0, content="</span>"))


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


def _post_process(
    body: str, t: theme.Tokens, base_dir: Optional[str], m: Metrics, width: int = 0, streaming: bool = False
) -> str:
    mono = theme._MONO_FAMILY or "monospace"
    code_px = px(mono_size())
    code_line = px(mono_size() * 1.6)

    def fence(mt: re.Match) -> str:
        lm = _LANG_RE.search(mt.group(1) or "")
        lang = html.unescape(lm.group(1)) if lm else ""
        code = html.unescape(mt.group(2)).rstrip("\n")
        label = html.escape(lang_label(lang))
        # ``.md-code``: a collapsed 100%-wide table marks the well (the view paints its rounded
        # background and the Copy at its corner); the head row is 30px with the language in micro
        # text; the code sits in padding 0 14px 13px, one paragraph per line so that a line too
        # long for the well wraps past its own indentation rather than scrolling out of sight.
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;margin-top:{px(m.base * 0.6)}px;margin-bottom:{m.base}px">'
            f'<tr><td style="padding-left:14px;padding-right:10px;padding-top:8px;padding-bottom:8px">'
            f'<p style="margin:0;line-height:14px;font-size:{theme.FS_MICRO}px;color:{t.text_3}">{label}</p></td></tr>'
            f'<tr><td style="padding-left:14px;padding-right:14px;padding-top:0;padding-bottom:13px">'
            f"{code_lines_html(code, lang, t.name, mono, code_px, code_line)}</td></tr></table>"
        )

    body = _FENCE_RE.sub(fence, body)
    body = _inline_code(body, t, m)
    body = _HEAD_RE.sub(lambda mt: _heading(mt, t, m), body)
    body = _TABLE_RE.sub(lambda mt: _table(mt, t, m, width, chunk=streaming), body)
    body = body.replace("<hr>", '<hr width="100%">')
    body = re.sub(r"<img src=\"(?!https?:|data:|/|pilcrow-check:)([^\"]+)\"", lambda mt: _img(mt, base_dir), body)
    body = body.replace("<p>", f'<p style="line-height:{m.line}px">')
    # the newline markdown-it puts after a block would end its paragraph with a space in QTextDocument
    return _BLOCK_NL_RE.sub(r"\1", body).rstrip("\n")


def _inline_code(body: str, t: theme.Tokens, m: Metrics) -> str:
    """Inline code: mono at 0.86em on a pill the view paints; the transparent background is its marker."""
    mono = theme._MONO_FAMILY or "monospace"
    pad = f'<span style="font-size:{pill_pad_px(m)}px">{PILL_PAD}</span>'
    return re.sub(
        r"<code>(.*?)</code>",
        lambda mt: (
            f"<code style=\"font-family:'{mono}';font-size:{m.code}px;background-color:rgba(0,0,0,0);white-space:pre\">"
            f"{pad}{_no_break(mt.group(1))}{pad}</code>"  # the pads stay with the code (glue)
        ),
        body,
        flags=re.S,
    )


def code_lines_html(code: str, lang: str, theme_name: str, mono: str, size_px: int, line_px: int) -> str:
    """A code block as one ``<p>`` per line: ``white-space: pre-wrap`` keeps the spacing, and a hanging
    indent (``margin-left`` with a negative ``text-indent``) puts a wrapped continuation two characters
    past the line's own indentation, so a line that does not fit still reads as one line."""
    ch = font_metrics(size_px, mono=True).horizontalAdvance("0")
    out: List[str] = []
    for raw, hl in zip(code.split("\n"), highlight_lines(code, lang, theme_name)):
        lead = len(raw) - len(raw.lstrip(" \t"))
        cols = lead + (TAB_COLS - 1) * raw[:lead].count("\t") + WRAP_INDENT if len(raw) > lead else 0
        ind = int(cols * ch + 0.5)
        out.append(
            f"<p style=\"font-family:'{mono}';font-size:{size_px}px;margin:0;line-height:{line_px}px;"
            f'white-space:pre-wrap;margin-left:{ind}px;text-indent:-{ind}px">{hl or "&nbsp;"}</p>'
        )
    return "".join(out)


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


# ---------------------------------------------------------------- tables

_TAG_RE = re.compile(r"<[^>]+>")
_CODE_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.S)
_CELL_RE = re.compile(r"<(t[hd])(?: style=\"([^\"]*)\")?>(.*?)</t[hd]>", re.S)
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_UNIT_RE_TEXT = re.compile(
    "[\\u2e80-\\u9fff\\uac00-\\ud7af\\uf900-\\ufaff\\uff00-\\uffef]|[^\\s\\u2e80-\\u9fff\\uac00-\\ud7af\\uf900-\\ufaff\\uff00-\\uffef]+"
)


def _text_widths(fragment: str, fm: QFontMetricsF) -> Tuple[float, float, float]:
    """(width on one line, widest unbreakable unit, widest character) of an HTML fragment of prose:
    words, or single CJK characters, are the units a line may break between; a character is where
    it breaks when it must."""
    text = html.unescape(_TAG_RE.sub("", fragment))
    if not text.strip():
        return 0.0, 0.0, 0.0
    widest = max((fm.horizontalAdvance(u) for u in _UNIT_RE_TEXT.findall(text)), default=0.0)
    hard = max((fm.horizontalAdvance(ch) for ch in set(text) if not ch.isspace()), default=0.0)
    return fm.horizontalAdvance(text), widest, hard


def cell_widths(inner: str, m: Metrics, head: bool = False) -> Tuple[float, float, float]:
    """(the width a cell's content wants on one line, the least it can be given, the least Qt will
    lay it out in) from the prose and the code spans in it. A code span never breaks, so the least a
    cell can be given is the span's width; when the table does not fit even so, the spans are let
    break (``squeezed``) and the layout can go down to the widest character, but never under it.
    A heading cell is measured in its semibold."""
    fm_t = font_metrics(m.table, weight=600 if head else 400)
    fm_c = font_metrics(m.code, mono=True)
    max_w = 0.0
    min_w = 0.0
    hard_w = 0.0
    pos = 0
    for mt in _CODE_RE.finditer(inner):
        w, u, h = _text_widths(inner[pos : mt.start()], fm_t)
        max_w += w
        min_w = max(min_w, u)
        hard_w = max(hard_w, h)
        code = html.unescape(_TAG_RE.sub("", mt.group(1))).replace(NO_BREAK, "")
        cw = fm_c.horizontalAdvance(code)
        max_w += cw
        min_w = max(min_w, cw)
        hard_w = max(hard_w, max((fm_c.horizontalAdvance(ch) for ch in set(code) if not ch.isspace()), default=0.0))
        pos = mt.end()
    w, u, h = _text_widths(inner[pos:], fm_t)
    max_w += w
    min_w = max(min_w, u)
    hard_w = max(hard_w, h)
    # prose never squeezes below a few characters a line (CJK would otherwise go one character a line)
    min_w = max(min_w, min(max_w, fm_t.horizontalAdvance("中") * MIN_CELL_CHARS))
    return max_w, min_w, hard_w


def column_widths(mins: Sequence[float], maxs: Sequence[float], avail: float, hards: Sequence[float] = ()) -> List[int]:
    """Browser-style auto table layout: every column at the width its widest line wants when that
    fits (and at least 60% of the room shared out between them, ``min-width: 60%``); otherwise each
    at least its widest word, the rest of the room shared in proportion to what they still want.
    When not even that fits, each column keeps the width Qt will not go under (``hards``: its widest
    character) and the rest is squeezed in proportion, so the table ends where it was planned to."""
    n = len(mins)
    if not n:
        return []
    want = [int(math.ceil(w)) for w in maxs]
    need = [int(math.ceil(w)) for w in mins]
    room = int(avail)
    if sum(want) <= room:
        floor = int(avail * 0.6)
        return _summing_to(want, floor) if 0 < sum(want) < floor else want
    if sum(need) <= room:
        spare = [w - n for w, n in zip(want, need)]
        shares = _summing_to(spare if sum(spare) > 0 else [1] * n, room - sum(need))
        return [mn + sh for mn, sh in zip(need, shares)]
    floors = [min(nd, int(math.ceil(h))) for nd, h in zip(need, hards)] if len(hards) == n else [0] * n
    if sum(floors) >= room:
        return floors  # the columns cannot be narrower: the table is as wide as it must be
    spare = [nd - fl for nd, fl in zip(need, floors)]
    shares = _summing_to(spare if sum(spare) > 0 else [1] * n, room - sum(floors))
    return [fl + sh for fl, sh in zip(floors, shares)]


def _summing_to(ws: Sequence[float], total: int) -> List[int]:
    """The widths scaled to add up to ``total`` exactly, in whole pixels (largest remainders first)."""
    s = sum(ws)
    if s <= 0:
        return [total // len(ws)] * len(ws)
    scaled = [w * total / s for w in ws]
    out = [int(w) for w in scaled]
    short = total - sum(out)
    for i in sorted(range(len(ws)), key=lambda i: scaled[i] - out[i], reverse=True)[: max(0, short)]:
        out[i] += 1
    return out


TABLE_CHUNK = 40  # body rows per table while a table is being written (a row appends in O(chunk))
LIST_CHUNK = 40  # items per list while a flat list is being written (a block joins a list in O(list))
Cell = Tuple[str, str, str]  # tag (th/td), align, inner HTML


Plan = Tuple[List[int], List[float], List[float], List[float]]  # widths, and each column's least, most, floor


def table_plan(rows: Sequence[Sequence[Cell]], m: Metrics, width: int) -> Plan:
    """Column widths for the rows (see ``column_widths``), with the least and the most each column
    wants and the floor it cannot go under, so a row added later can tell whether the plan still holds."""
    ncol = max((len(r) for r in rows), default=0)
    mins = [0.0] * ncol
    maxs = [0.0] * ncol
    hards = [0.0] * ncol
    for row in rows:
        for c, (tag, _align, inner) in enumerate(row):
            mx, mn, hd = cell_widths(inner, m, head=tag == "th")
            maxs[c] = max(maxs[c], mx + CELL_PAD)
            # a heading stays on one line: a column is never narrower than its name
            mins[c] = max(mins[c], (mx if tag == "th" else mn) + CELL_PAD)
            hards[c] = max(hards[c], hd + CELL_PAD)
    avail = float(width) if width > 0 else sum(maxs)
    return column_widths(mins, maxs, avail, hards), mins, maxs, hards


def squeezed(widths: Sequence[int], mins: Sequence[float]) -> Tuple[bool, ...]:
    """Which columns got less than the least they asked for (the table did not fit the room even so):
    a code span in such a column may break, since it cannot overhang the cell instead."""
    return tuple(w < int(math.ceil(mn)) for w, mn in zip(widths, mins))


def table_html(
    rows: Sequence[Sequence[Cell]],
    widths: Sequence[int],
    m: Metrics,
    top: int,
    bottom: int,
    squeezed: Sequence[bool] = (),
) -> str:
    """One ``<table>`` of the rows: cells padded 7px 14px 7px 0 at 0.93em, the first row carrying the
    column widths; ``top``/``bottom`` are its margins (a continuation chunk has none above). The
    cells of a ``squeezed`` column lose the joiners that keep a code span on one line."""
    out = [
        f'<table width="{sum(widths)}" cellpadding="0" cellspacing="0" style="margin-top:{top}px;margin-bottom:{bottom}px">'
    ]
    for r, row in enumerate(rows):
        out.append("<tr>")
        for c, (tag, align, inner) in enumerate(row):
            width_attr = f' width="{widths[c]}"' if r == 0 and c < len(widths) else ""
            if c < len(squeezed) and squeezed[c]:
                inner = inner.replace(NO_BREAK, "")
            out.append(
                f'<{tag}{width_attr} style="padding-left:0;padding-right:{CELL_PAD}px;padding-top:7px;padding-bottom:7px;'
                f'font-size:{m.table}px;text-align:{align or "left"}">{inner}</{tag}>'
            )
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _table(mt: re.Match, t: theme.Tokens, m: Metrics, width: int, chunk: bool = False) -> str:
    # ``.md table``; the hairline under each row is painted by the view. While the table is being
    # written (``chunk``) its body rows come in tables of TABLE_CHUNK with no space between, so the
    # row that arrives next is appended to a small table rather than laid out with all of them.
    rows: List[List[Cell]] = []
    for row in _ROW_RE.findall(mt.group(1)):
        cells = _CELL_RE.findall(row)
        if cells:
            rows.append([(tag, (style or "").replace("text-align:", ""), inner) for tag, style, inner in cells])
    if not rows:
        return ""
    widths, mins, _maxs, _hards = table_plan(rows, m, width)
    sq = squeezed(widths, mins)
    top, bottom = px(m.base * 0.4), m.base
    if not chunk or len(rows) <= 1 + TABLE_CHUNK:
        return table_html(rows, widths, m, top, bottom, sq)
    groups = [rows[: 1 + TABLE_CHUNK]]
    for i in range(1 + TABLE_CHUNK, len(rows), TABLE_CHUNK):
        groups.append(rows[i : i + TABLE_CHUNK])
    last = len(groups) - 1
    return "".join(
        table_html(g, widths, m, top if k == 0 else 0, bottom if k == last else 0, sq) for k, g in enumerate(groups)
    )


def table_cells(line: str) -> List[str]:
    """The cells of one table row, split the way markdown-it splits it: on pipes outside code spans,
    an escaped pipe kept for the inline parser, the outer pipes dropped."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: List[str] = []
    cur: List[str] = []
    i = 0
    n = len(s)
    ticks = 0  # the length of the backtick run that opened the code span we are in (0: none)
    while i < n:
        ch = s[i]
        if ch == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            run = j - i
            if ticks == 0:
                ticks = run
            elif run == ticks:
                ticks = 0
            cur.append(s[i:j])
            i = j
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(s[i : i + 2])
            i += 2
            continue
        if ch == "|" and ticks == 0:
            cells.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _img(mt: re.Match, base_dir: Optional[str]) -> str:
    if not base_dir:
        return mt.group(0)
    return f'<img src="{html.escape(_join(base_dir, html.unescape(mt.group(1))))}"'


def _join(base: str, rel: str) -> str:
    base = base.rstrip("/")
    rel = rel[2:] if rel.startswith("./") else rel
    return f"{base}/{rel}"


PARA_LIMIT = 4000  # a paragraph longer than this is kept in pieces, so a token costs one piece's layout
PARA_SLACK = 1500  # how far past the limit a piece may run to find a place to break
_BALANCE = ("`", "*", "_", "~")
_PAIRS = (("[", "]"), ("(", ")"), ("<", ">"))


def _breakable(prefix: str) -> bool:
    """Can a paragraph be cut after ``prefix`` without cutting an inline construct in two?"""
    for ch in _BALANCE:
        if prefix.count(ch) % 2:
            return False
    return all(prefix.count(a) == prefix.count(b) for a, b in _PAIRS)


def para_pieces(text: str) -> List[str]:
    """A paragraph in pieces of about PARA_LIMIT characters, cut at whitespace where the inline
    markup is balanced. The cuts depend only on the text before them, so a paragraph being written
    is cut where its finished self will be: nothing moves when it is done."""
    pieces: List[str] = []
    start = 0
    while len(text) - start > PARA_LIMIT:
        cut = -1
        i = start + PARA_LIMIT
        stop = min(len(text), start + PARA_LIMIT + PARA_SLACK)
        while i < stop:
            if text[i].isspace() and _breakable(text[start:i]):
                cut = i
                break
            i += 1
        if cut < 0:
            if stop < len(text):
                # nowhere to cut in this window: the piece runs on to the next window
                start_next = start
                text_len = len(text)
                while cut < 0 and i < text_len:
                    if text[i].isspace() and _breakable(text[start_next:i]):
                        cut = i
                    i += 1
            if cut < 0:
                break
        pieces.append(text[start:cut])
        start = cut + 1
    pieces.append(text[start:])
    return pieces


Piece = Tuple[str, bool, int]  # source, continuation of the piece before, offset of its block in the text


def split_pieces(src: str, base: int = 0) -> List[Piece]:
    """Top-level Markdown blocks as (source, continuation, offset): a streaming reply re-renders only
    the block being written, and a paragraph past PARA_LIMIT comes as pieces, the later ones marked
    as continuations (no space above them). ``offset`` is where the piece's block starts in the text
    (``base`` added), the same for every piece of one paragraph."""
    if not src:
        return []
    if MarkdownIt is None:
        return [(src, False, base)]
    try:
        tokens = parser().parse(src)
    except Exception:  # noqa: BLE001
        return [(src, False, base)]
    lines = src.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    out: List[Piece] = []
    depth = 0
    for tok in tokens:
        if tok.nesting == 1:
            if depth == 0 and tok.map:
                block = "\n".join(lines[tok.map[0] : tok.map[1]])
                at = base + starts[tok.map[0]]
                if tok.type == "paragraph_open" and len(block) > PARA_LIMIT:
                    for k, piece in enumerate(para_pieces(block)):
                        out.append((piece, k > 0, at))
                else:
                    out.append((block, False, at))
            depth += 1
        elif tok.nesting == -1:
            depth -= 1
        elif depth == 0 and tok.map:
            out.append(("\n".join(lines[tok.map[0] : tok.map[1]]), False, base + starts[tok.map[0]]))
    return out or [(src, False, base)]


def grow_pieces(old: List[Piece], old_text: str, text: str) -> List[Piece]:
    """The pieces of ``text``, which extends ``old_text``, from the pieces of ``old_text``: only the
    last block is parsed again (a block the parser closed cannot take a later block back: what could
    join it, a rule under a paragraph or a lazy line under an item, was one block with it from the
    start), the rest stand. The parse costs the tail, not the whole of a long reply."""
    if not old or not text.startswith(old_text) or len(old) < 2:
        return split_pieces(text)
    tail_from = old[-1][2]  # the last block's start (all pieces of a paragraph share it)
    head = [p for p in old if p[2] < tail_from]
    return head + split_pieces(text[tail_from:], tail_from)


def split_blocks_ex(src: str) -> List[Tuple[str, bool]]:
    """Top-level Markdown blocks as (source, continuation)."""
    return [(s, c) for s, c, _at in split_pieces(src)]


def split_blocks(src: str) -> list[str]:
    """Top-level Markdown blocks (a long paragraph in its pieces)."""
    return [s for s, _cont, _at in split_pieces(src)]


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
