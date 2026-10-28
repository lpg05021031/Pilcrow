"""Code, diffs and terminal output as rich text (a port of web/src/components/Code.tsx).

`CodeView` draws numbered lines, `DiffView` the rows of unified hunks tinted with the diff tokens, and
`ansi_html` turns SGR-coloured output into spans in the theme's ANSI palette. Hunks come from a
tool's ``structuredPatch`` (:func:`hunks_from_patch`) or from an old/new pair (:func:`compute_hunks`,
difflib standing in for jsdiff's structuredPatch).
"""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtWidgets import QLabel, QWidget

from .... import markdown, theme
from ....lib.ansi import parse_ansi
from ....lib.fmt import lang_from_path
from ...ui.signals import bind
from .common import Surface, code_more, rich, vbox

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover
    lex = None  # type: ignore[assignment]

Range = Tuple[int, int]


# ---------------------------------------------------------------- hunks


@dataclass(eq=False)
class DiffLine:
    t: str  # '+', '-' or ' '
    s: str
    o: Optional[int] = None
    n: Optional[int] = None


@dataclass
class DiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: List[DiffLine] = field(default_factory=list)
    header: Optional[str] = None


def slide_blocks(src: Sequence[str]) -> List[str]:
    """
    Move pure insertions/deletions as far down as they can go, like git does. A line-diff often
    splits "}\\n\\nfunction b() {\\n}" as "+}" … " }"; sliding the block keeps the original "}" as context.
    """
    lines = list(src)
    i = 0
    while i < len(lines):
        t = lines[i][:1]
        if t not in ("+", "-"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j][:1] == t:
            j += 1
        other = "-" if t == "+" else "+"
        mixed = (i > 0 and lines[i - 1][:1] == other) or (j < len(lines) and lines[j][:1] == other)
        if not mixed:
            while j < len(lines) and lines[j][:1] == " " and lines[j][1:] == lines[i][1:]:
                lines[i] = " " + lines[i][1:]
                lines[j] = t + lines[j][1:]
                i += 1
                j += 1
        i = j
    return lines


def hunks_from_patch(patch: Sequence[dict]) -> List[DiffHunk]:
    """Build hunks from an Edit/Write tool's structuredPatch (lines prefixed with ' ', '+', '-')."""
    out: List[DiffHunk] = []
    for p in patch:
        if not isinstance(p, dict):
            continue
        o = int(p.get("oldStart") or 1)
        n = int(p.get("newStart") or 1)
        lines: List[DiffLine] = []
        for raw in slide_blocks([str(x) for x in (p.get("lines") or [])]):
            t, s = raw[:1], raw[1:]
            if t == "+":
                lines.append(DiffLine("+", s, n=n))
                n += 1
            elif t == "-":
                lines.append(DiffLine("-", s, o=o))
                o += 1
            elif t == "\\":
                continue
            else:
                lines.append(DiffLine(" ", s, o=o, n=n))
                o += 1
                n += 1
        out.append(
            DiffHunk(
                int(p.get("oldStart") or 1),
                int(p.get("oldLines") or 0),
                int(p.get("newStart") or 1),
                int(p.get("newLines") or 0),
                lines,
            )
        )
    return out


_hunk_cache: Dict[Tuple[str, str], List[DiffHunk]] = {}


def compute_hunks(old: str, new: str, context: int = 3) -> List[DiffHunk]:
    """A unified diff of two strings with three lines of context (what jsdiff's structuredPatch gives the web)."""
    key = (old, new)
    hit = _hunk_cache.get(key)
    if hit is not None:
        return hit
    a, b = old.split("\n"), new.split("\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    patch: List[dict] = []
    for group in sm.get_grouped_opcodes(context):
        lines: List[str] = []
        i1, j1 = group[0][1], group[0][3]
        i2, j2 = group[-1][2], group[-1][4]
        for tag, ai, aj, bi, bj in group:
            if tag == "equal":
                lines.extend(" " + x for x in a[ai:aj])
                continue
            if tag in ("replace", "delete"):
                lines.extend("-" + x for x in a[ai:aj])
            if tag in ("replace", "insert"):
                lines.extend("+" + x for x in b[bi:bj])
        patch.append({"oldStart": i1 + 1, "oldLines": i2 - i1, "newStart": j1 + 1, "newLines": j2 - j1, "lines": lines})
    hunks = hunks_from_patch(patch)
    if len(_hunk_cache) > 300:
        _hunk_cache.pop(next(iter(_hunk_cache)))
    _hunk_cache[key] = hunks
    return hunks


_WORD_RE = re.compile(r"\s+|\w+|[^\w\s]")


def word_ranges(hunks: Sequence[DiffHunk]) -> Dict[DiffLine, List[Range]]:
    """Changed words inside paired -/+ lines, unless almost the whole line changed."""
    out: Dict[DiffLine, List[Range]] = {}
    for h in hunks:
        ls = h.lines
        i = 0
        while i < len(ls):
            if ls[i].t != "-":
                i += 1
                continue
            dels: List[DiffLine] = []
            while i < len(ls) and ls[i].t == "-":
                dels.append(ls[i])
                i += 1
            adds: List[DiffLine] = []
            while i < len(ls) and ls[i].t == "+":
                adds.append(ls[i])
                i += 1
            for k in range(min(len(dels), len(adds))):
                a, b = dels[k].s, adds[k].s
                if len(a) > 2000 or len(b) > 2000:
                    continue
                ta, tb = _WORD_RE.findall(a), _WORD_RE.findall(b)
                sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
                rd: List[Range] = []
                ra: List[Range] = []
                changed = 0
                pa = pb = 0
                for tag, ai, aj, bi, bj in sm.get_opcodes():
                    la = sum(len(x) for x in ta[ai:aj])
                    lb = sum(len(x) for x in tb[bi:bj])
                    if tag == "equal":
                        pa += la
                        pb += lb
                        continue
                    if la:
                        rd.append((pa, pa + la))
                        pa += la
                        changed += la
                    if lb:
                        ra.append((pb, pb + lb))
                        pb += lb
                        changed += lb
                if changed < (len(a) + len(b)) * 0.7:
                    out[dels[k]] = rd
                    out[adds[k]] = ra
    return out


# ---------------------------------------------------------------- highlighting


@lru_cache(maxsize=256)
def _highlight_lines(code: str, lang: str, theme_name: str) -> Tuple[str, ...]:
    """Each line of ``code`` as escaped HTML with the theme's syntax colours; plain when there is no lexer."""
    lines = code.split("\n")
    plain = tuple(html.escape(x) for x in lines)
    if lex is None or not lang or lang == "text" or len(code) > 200000:
        return plain
    name = markdown.LEXER_ALIAS.get(lang.lower(), lang.lower())
    try:
        lexer = get_lexer_by_name(name, stripnl=False)
    except ClassNotFound:
        return plain
    t = theme.DARK if theme_name == "dark" else theme.LIGHT
    out: List[str] = [""]
    for ttype, value in lex(code, lexer):
        st = markdown._style_for(ttype, t)
        for k, part in enumerate(value.split("\n")):
            if k:
                out.append("")
            if part:
                esc = html.escape(part)
                out[-1] += f'<span style="{st}">{esc}</span>' if st else esc
    out = out[: len(lines)]
    while len(out) < len(lines):
        out.append("")
    return tuple(out)


def highlight_lines(code: str, lang: str) -> List[str]:
    return list(_highlight_lines(code, lang, theme.tokens().name))


def mark_html(text: str, ranges: Sequence[Range], color: str) -> str:
    """Plain text with the changed ranges wrapped in a tinted mark."""
    out: List[str] = []
    pos = 0
    for a, b in ranges:
        if a > pos:
            out.append(html.escape(text[pos:a]))
        out.append(f'<span style="background-color:{color}">{html.escape(text[a:b])}</span>')
        pos = b
    out.append(html.escape(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------- CodeView


class CodeView(QWidget):
    """`.code-view`: numbered lines on an inset panel, clipped at ``max_lines`` with a "Show all" control."""

    def __init__(
        self,
        text: str,
        path: str = "",
        lang: str = "",
        start_line: int = 1,
        max_lines: int = 400,
        highlight_line: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.lines = text.split("\n")
        if len(self.lines) > 1 and self.lines[-1] == "":
            self.lines.pop()
        self.language = lang or (lang_from_path(path) if path else "text")
        self.start_line = start_line
        self.max_lines = max_lines
        self.highlight_line = highlight_line
        self._all = False
        lay = vbox(self, spacing=4)
        self.surface = Surface("inset", "hair", theme.R3, margins=(0, 8, 0, 8))
        self.label: QLabel = rich("", mono=True, size=12)
        self.surface.add(self.label)
        lay.addWidget(self.surface)
        self.more = code_more(f"Show all {len(self.lines)} lines", self._show_all)
        lay.addWidget(self.more)
        self._render()

    @property
    def shown(self) -> List[str]:
        return self.lines if self._all else self.lines[: self.max_lines]

    def _show_all(self) -> None:
        self._all = True
        self._render()

    def _render(self) -> None:
        t = theme.tokens()
        shown = self.shown
        hl = (
            highlight_lines("\n".join(shown), self.language)
            if len(self.lines) < 20000
            else [html.escape(x) for x in shown]
        )
        hit_bg = theme.blend(t.accent_wash, t.inset).name()
        rows: List[str] = []
        for i, body in enumerate(hl):
            n = self.start_line + i
            bg = f' bgcolor="{hit_bg}"' if self.highlight_line == n else ""
            rows.append(
                f'<tr><td width="1" align="right" style="color:{t.text_4};padding-left:14px;padding-right:12px"{bg}>'
                f'{n}</td><td style="white-space:pre-wrap;padding-right:16px"{bg}>{body or "&nbsp;"}</td></tr>'
            )
        self.label.setText(f'<table cellspacing="0" cellpadding="0" width="100%">{"".join(rows)}</table>')
        self.more.setVisible(len(shown) < len(self.lines))

    def html(self) -> str:
        return self.label.text()


# ---------------------------------------------------------------- DiffView


@dataclass
class DiffRow:
    kind: str  # 'hunk' | 'line'
    line: Optional[DiffLine] = None
    hunk: Optional[DiffHunk] = None
    hi: int = 0


class DiffView(QWidget):
    """`.diff`: unified hunks as numbered rows; adds and deletes tinted with the diff tokens."""

    def __init__(
        self,
        hunks: Sequence[DiffHunk],
        path: str = "",
        numbers: bool = True,
        max_rows: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.hunks = list(hunks)
        self.path = path
        self.numbers = numbers
        self.max_rows = max_rows
        self._expanded = False
        self.lang = lang_from_path(path) if path else "text"
        self.rows: List[DiffRow] = []
        for hi, h in enumerate(self.hunks):
            if len(self.hunks) > 1 or h.old_start > 1 or h.header:
                self.rows.append(DiffRow("hunk", hunk=h, hi=hi))
            for ln in h.lines:
                self.rows.append(DiffRow("line", line=ln))
        lay = vbox(self, spacing=4)
        self.surface = Surface("inset", "hair", theme.R3, margins=(0, 6, 0, 6))
        self.label: QLabel = rich("", mono=True, size=12)
        self.surface.add(self.label)
        lay.addWidget(self.surface)
        self.more = code_more(f"Show all {len(self.rows)} lines", self._expand)
        lay.addWidget(self.more)
        self._render()

    @property
    def shown(self) -> List[DiffRow]:
        limit = self.max_rows if self.max_rows and not self._expanded else len(self.rows)
        return self.rows[:limit]

    def _expand(self) -> None:
        self._expanded = True
        self._render()

    def _render(self) -> None:
        t = theme.tokens()
        words = word_ranges(self.hunks)
        toks: Dict[DiffLine, str] = {}
        for h in self.hunks:
            old_l = [ln for ln in h.lines if ln.t != "+"]
            new_l = [ln for ln in h.lines if ln.t != "-"]
            for group in (old_l, new_l):
                for ln, hl in zip(group, highlight_lines("\n".join(x.s for x in group), self.lang)):
                    toks[ln] = hl
        add_bg = theme.blend(t.diff_add, t.inset).name()
        del_bg = theme.blend(t.diff_del, t.inset).name()
        add_hl = theme.blend(t.diff_add_hl, add_bg).name()
        del_hl = theme.blend(t.diff_del_hl, del_bg).name()
        cols = 3 if self.numbers else 2
        rows: List[str] = []
        for r in self.shown:
            if r.kind == "hunk":
                h = r.hunk
                assert h is not None
                prev = self.hunks[r.hi - 1] if r.hi > 0 else None
                gap = h.old_start - (prev.old_start + prev.old_lines) if prev else h.old_start - 1
                text = f"{gap} unchanged line{'s' if gap > 1 else ''}" if gap > 0 else ""
                ctx = (
                    f' <span style="color:{t.text_4};font-size:11px">{html.escape(h.header)}</span>' if h.header else ""
                )
                rows.append(
                    f'<tr><td colspan="{cols}" style="padding:4px 14px;color:{t.text_3}">'
                    f'<span style="color:{t.text_4};letter-spacing:1px">⋯</span>&nbsp;&nbsp;'
                    f"{html.escape(text)}{ctx}</td></tr>"
                )
                continue
            ln = r.line
            assert ln is not None
            if ln.t == "+":
                bg, sign, sign_c, num_c, hl_c = add_bg, "+", t.ok, t.text_3, add_hl
            elif ln.t == "-":
                bg, sign, sign_c, num_c, hl_c = del_bg, "−", t.err, t.text_3, del_hl
            else:
                bg, sign, sign_c, num_c, hl_c = "", "&nbsp;", t.text_4, t.text_4, ""
            bga = f' bgcolor="{bg}"' if bg else ""
            ranges = words.get(ln)
            body = mark_html(ln.s, ranges, hl_c) if ranges else toks.get(ln, html.escape(ln.s))
            cells = ""
            if self.numbers:
                num = ln.o if ln.t == "-" else ln.n
                cells += (
                    f'<td width="1" align="right" style="color:{num_c};padding-left:14px;padding-right:8px"{bga}>'
                    f"{num if num is not None else ''}</td>"
                )
            pad = 4 if self.numbers else 12
            cells += (
                f'<td width="1" align="center" style="color:{sign_c};padding-left:{pad}px;padding-right:4px"{bga}>'
                f"{sign}</td>"
            )
            cells += f'<td style="white-space:pre-wrap;padding-right:18px"{bga}>{body or "&nbsp;"}</td>'
            rows.append(f"<tr>{cells}</tr>")
        self.label.setText(f'<table cellspacing="0" cellpadding="0" width="100%">{"".join(rows)}</table>')
        self.more.setVisible(len(self.shown) < len(self.rows))

    def html(self) -> str:
        return self.label.text()


# ---------------------------------------------------------------- ANSI


def ansi_html(text: str) -> str:
    """SGR-coloured output as spans in the theme's ANSI palette (AnsiText in Code.tsx)."""
    t = theme.tokens()
    out: List[str] = []
    for seg in parse_ansi(text):
        esc = html.escape(seg.text)
        styles: List[str] = []
        if seg.style:
            fg = seg.style.get("color")
            bg = seg.style.get("background")
            if fg:
                styles.append(f"color:{t.ansi(fg) or fg}")
            if bg:
                styles.append(f"background-color:{t.ansi(bg) or bg}")
        cls = (seg.cls or "").split()
        if "a-b" in cls:
            styles.append("font-weight:600")
        if "a-i" in cls:
            styles.append("font-style:italic")
        if "a-u" in cls:
            styles.append("text-decoration:underline")
        if "a-d" in cls and not any(s.startswith("color:") for s in styles):
            styles.append(f"color:{t.text_3}")
        out.append(f'<span style="{";".join(styles)}">{esc}</span>' if styles else esc)
    return f'<div style="white-space:pre-wrap">{"".join(out) or "&nbsp;"}</div>'


def ansi_label(text: str, color: str = "text_2") -> QLabel:
    """A mono label holding :func:`ansi_html` of a text; re-rendered when the theme changes."""
    lb = rich(ansi_html(text), mono=True, size=12, color=color)
    bind(theme.theme.changed, lb, lambda _t: lb.setText(ansi_html(text)))
    return lb
