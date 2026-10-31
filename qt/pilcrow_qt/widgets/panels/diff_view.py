"""The diff renderer shared by the Changes and Timeline tabs (a port of DiffView in web/src/components/Code.tsx).

A :class:`DiffView` lists files (``DiffFile`` dicts, see web/src/lib/types.ts); each file is a header
row with its status, name, +added −deleted counts and actions (``.cfile-head``), over a collapsible
:class:`DiffGrid`. The grid paints the hunks itself the way transcript.css lays out ``.diff-grid``:
number columns with the CSS paddings, an 18px sign column, code at 12px mono on 19.2px lines coloured
by Pygments (shiki in the web), word-level marks on changed pairs of lines (difflib stands in for the
``diff`` library), hunk bars, and the striped void cells of a side-by-side view. Review notes attach
to a line through the gutter; :meth:`DiffView.notes_message` gathers them into one message for Claude.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...lib.fmt import basename, dirname
from ..ui.bits import Clickable, Hairline, Pill
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .editor import lexer_for_path, token_color
from .right_panel import Btn, Card, elide, icon_btn, lbl, rtl_display

Json = Dict[str, Any]
Range = Tuple[int, int]

STATUS_LABEL = {"M": "Modified", "A": "Added", "D": "Deleted", "R": "Renamed"}
DIFF_FONT_PX = 12
LINE_H = DIFF_FONT_PX * 1.6  # .diff line-height
HUNK_H = 26  # .diff-hunk height; 4px margins above and below
SIGN_W = 18  # .ds column
GUTTER_GLYPH = 18  # .diff-add-comment


# ---------------------------------------------------------------- pure helpers

_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _word_diff(a: str, b: str) -> Tuple[List[Range], List[Range], int]:
    """Changed character ranges of ``a`` and ``b`` after a word-level diff (``diffWordsWithSpace``)."""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    oa = [0]
    for t in ta:
        oa.append(oa[-1] + len(t))
    ob = [0]
    for t in tb:
        ob.append(ob[-1] + len(t))
    removed: List[Range] = []
    added: List[Range] = []
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag in ("replace", "delete") and i2 > i1:
            removed.append((oa[i1], oa[i2]))
            changed += oa[i2] - oa[i1]
        if tag in ("replace", "insert") and j2 > j1:
            added.append((ob[j1], ob[j2]))
            changed += ob[j2] - ob[j1]
    return removed, added, changed


def word_ranges(hunks: List[Json]) -> Dict[int, List[Range]]:
    """Per line (keyed by ``id(line)``), the character ranges to mark. Pairs of removed/added lines are
    compared word by word; when nearly everything differs the marks are left out, as the web does."""
    out: Dict[int, List[Range]] = {}
    for h in hunks:
        ls = h.get("lines") or []
        i = 0
        while i < len(ls):
            if ls[i].get("t") != "-":
                i += 1
                continue
            dels: List[Json] = []
            while i < len(ls) and ls[i].get("t") == "-":
                dels.append(ls[i])
                i += 1
            adds: List[Json] = []
            while i < len(ls) and ls[i].get("t") == "+":
                adds.append(ls[i])
                i += 1
            for k in range(min(len(dels), len(adds))):
                a, b = str(dels[k].get("s") or ""), str(adds[k].get("s") or "")
                if len(a) > 2000 or len(b) > 2000:
                    continue
                rd, ra, changed = _word_diff(a, b)
                if changed < (len(a) + len(b)) * 0.7:
                    out[id(dels[k])] = rd
                    out[id(adds[k])] = ra
    return out


@dataclass
class Row:
    kind: str  # 'hunk' | 'line' | 'split'
    hunk: Optional[Json] = None
    hi: int = 0
    line: Optional[Json] = None
    left: Optional[Json] = None
    right: Optional[Json] = None


def build_rows(hunks: List[Json], mode: str) -> List[Row]:
    """Hunks → rows: a hunk header when there is more than one hunk (or the first starts late), then
    the lines, paired up removed/added in split mode."""
    out: List[Row] = []
    for hi, h in enumerate(hunks):
        if len(hunks) > 1 or int(h.get("oldStart") or 0) > 1 or h.get("header"):
            out.append(Row("hunk", hunk=h, hi=hi))
        ls = h.get("lines") or []
        if mode == "unified":
            out.extend(Row("line", line=ln) for ln in ls)
            continue
        i = 0
        while i < len(ls):
            if ls[i].get("t") == " ":
                out.append(Row("split", left=ls[i], right=ls[i]))
                i += 1
                continue
            dels: List[Json] = []
            while i < len(ls) and ls[i].get("t") == "-":
                dels.append(ls[i])
                i += 1
            adds: List[Json] = []
            while i < len(ls) and ls[i].get("t") == "+":
                adds.append(ls[i])
                i += 1
            for k in range(max(len(dels), len(adds))):
                out.append(
                    Row("split", left=dels[k] if k < len(dels) else None, right=adds[k] if k < len(adds) else None)
                )
    return out


def line_key(ln: Json) -> str:
    """The identity of a line for comments: ``old:<n>`` for removed lines, ``new:<n>`` otherwise."""
    return f"old:{ln.get('o')}" if ln.get("t") == "-" else f"new:{ln.get('n')}"


def line_no(ln: Json) -> int:
    return int((ln.get("o") if ln.get("t") == "-" else ln.get("n")) or 0)


def _slide_blocks(src: List[str]) -> List[str]:
    """Move pure insertions/deletions as far down as they can go, like git does (see Code.tsx)."""
    out = list(src)
    i = 0
    while i < len(out):
        t = out[i][:1]
        if t not in ("+", "-"):
            i += 1
            continue
        j = i
        while j < len(out) and out[j][:1] == t:
            j += 1
        other = "-" if t == "+" else "+"
        mixed = (i > 0 and out[i - 1][:1] == other) or (j < len(out) and out[j][:1] == other)
        if not mixed:
            while j < len(out) and out[j][:1] == " " and out[j][1:] == out[i][1:]:
                out[i] = " " + out[i][1:]
                out[j] = t + out[j][1:]
                i += 1
                j += 1
        i = j
    return out


def hunks_from_patch(patch: List[Json]) -> List[Json]:
    """Hunks from an Edit/Write tool's ``structuredPatch`` (lines prefixed with ' ', '+', '-')."""
    out: List[Json] = []
    for p in patch or []:
        o, n = int(p.get("oldStart") or 0), int(p.get("newStart") or 0)
        lines: List[Json] = []
        for raw in _slide_blocks([str(x) for x in (p.get("lines") or [])]):
            t, s = raw[:1], raw[1:]
            if t == "+":
                lines.append({"t": "+", "s": s, "n": n})
                n += 1
            elif t == "-":
                lines.append({"t": "-", "s": s, "o": o})
                o += 1
            elif t == "\\":
                continue
            else:
                lines.append({"t": " ", "s": s, "o": o, "n": n})
                o += 1
                n += 1
        out.append(
            {
                "oldStart": p.get("oldStart"),
                "oldLines": p.get("oldLines"),
                "newStart": p.get("newStart"),
                "newLines": p.get("newLines"),
                "lines": lines,
            }
        )
    return out


Tok = Tuple[int, int, object]  # start, end, Pygments token type (the colour is looked up at paint time)
_TOKEN_LOOK: Dict[Tuple[str, object], Tuple[Optional[QColor], bool]] = {}


def token_look(ttype: object, t: theme.Tokens) -> Tuple[Optional[QColor], bool]:
    """``token_color`` memoised per theme: tokens are lexed once, coloured whenever they are painted."""
    key = (t.name, ttype)
    look = _TOKEN_LOOK.get(key)
    if look is None:
        look = _TOKEN_LOOK[key] = token_color(ttype, t)
    return look


def line_tokens(lines: List[Json], path: str) -> Dict[int, List[Tok]]:
    """Syntax token runs per line (by ``id(line)``) from Pygments. Theme-free: a theme change only repaints."""
    lexer = lexer_for_path(path) if path else None
    out: Dict[int, List[Tok]] = {}
    if lexer is None:
        return out
    from pygments import lex

    for ln in lines:
        s = str(ln.get("s") or "")
        toks: List[Tok] = []
        col = 0
        for ttype, value in lex(s, lexer):
            value = value.rstrip("\n")
            if not value:
                continue
            toks.append((col, col + len(value), ttype))
            col += len(value)
        out[id(ln)] = toks
    return out


# ---------------------------------------------------------------- small painted bits


class StatusBadge(Pill):
    """The 16px file-status square (``.fstat``): M grey, A/U green, D red, R blue."""

    def __init__(self, status: str, untracked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__("U" if untracked else status, radius=4, parent=parent)
        self._status = "U" if untracked else status
        self.setFont(theme.font_mono(10, 650))
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("Untracked" if untracked else STATUS_LABEL.get(status, status))
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        fg, bg = {
            "A": ("ok", "ok_wash"),
            "U": ("ok", "ok_wash"),
            "D": ("err", "err_wash"),
            "R": ("info", "info_wash"),
        }.get(self._status, ("text_2", "fill_2"))
        theme.tint(self, fg=fg)
        self.set_look(bg=f"{bg}/chrome")


class Num(QLabel):
    """``+12`` in green or ``−3`` in red (``.num-add`` / ``.num-del``); grey when zero."""

    def __init__(self, kind: str, n: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kind, self._n = kind, n
        self.setFont(theme.font_mono(11.5))
        self.setMinimumHeight(17)
        self.set_value(n)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_value(self, n: int) -> None:
        self._n = n
        self.setText(f"{'+' if self._kind == 'add' else '−'}{n}")
        self._paint()

    def _paint(self) -> None:
        theme.tint(self, fg="text_4" if not self._n else ("ok" if self._kind == "add" else "err"))


class NoteBadge(QLabel):
    """``.badge.accent``: the count of review notes on a file."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(19)
        self.setToolTip("Review notes")
        self._n = 0
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_count(self, n: int) -> None:
        self._n = n
        self.setFixedWidth(7 + 11 + 4 + QFontMetricsF(theme.font_ui(11, 550)).horizontalAdvance(str(n)) + 7 + 1)
        self.setVisible(n > 0)
        self.update()

    def _paint(self) -> None:
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.accent_wash, t.chrome))
        p.drawRoundedRect(QRectF(self.rect()), 9.5, 9.5)
        p.drawPixmap(7, 4, icons.pixmap("message-square-text", t.accent_text, 11))
        p.setFont(theme.font_ui(11, 550))
        p.setPen(QColor(t.accent_text))
        p.drawText(QRect(22, 0, self.width() - 22, self.height()), Qt.AlignmentFlag.AlignVCenter, str(self._n))


# ---------------------------------------------------------------- the grid


@dataclass
class _Geom:
    y: int
    h: int
    segs_l: List[Range]
    segs_r: List[Range]


class CommentEditor(QWidget):
    """The note box under a line (``.comment-editor``): a text area, Cancel and Save note
    (Ctrl+Enter saves, Escape cancels)."""

    saved = Signal(str)
    cancelled = Signal()

    def __init__(self, initial: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Leave a note for Claude about this line…")
        self.edit.setPlainText(initial)
        self.edit.setFixedHeight(57)
        self.edit.installEventFilter(self)
        style_textarea(self.edit, 12.5)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        row.addWidget(Btn("Cancel", kind="ghost", size="sm", on_click=self.cancelled.emit))
        self.save = Btn(
            "Save note",
            icon="check",
            kind="primary",
            size="sm",
            on_click=lambda: self.saved.emit(self.edit.toPlainText()),
        )
        row.addWidget(self.save)
        lay.addLayout(row)

    def eventFilter(self, obj, e: QEvent) -> bool:  # noqa: N802
        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            ke: QKeyEvent = e  # type: ignore[assignment]
            if (
                ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and ke.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self.saved.emit(self.edit.toPlainText())
                return True
            if ke.key() == Qt.Key.Key_Escape:
                self.cancelled.emit()
                return True
        return super().eventFilter(obj, e)


def style_textarea(edit: QPlainTextEdit, size: float = theme.FS_UI) -> None:
    """``.textarea``: a quiet fill with a hairline, 9px 11px padding, placeholder in the faint colour.
    The colours are palette roles, re-read when the theme repolishes the field."""
    edit.setFont(theme.font_ui(size))
    edit.setStyleSheet(
        f"QPlainTextEdit {{ background: palette(light); border: 1px solid palette(shadow); border-radius: {theme.R2}px; "
        "padding: 8px 10px; selection-background-color: palette(highlight); }"
        "QPlainTextEdit:focus { background: palette(window); border-color: palette(mid); }"
    )
    theme.tint(edit, placeholder="text_3")


class CommentCard(Card):
    """A saved note under a line (``.diff-comment``), with edit and delete."""

    edit_requested = Signal()
    delete_requested = Signal()

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__("raised", "hair", theme.R2, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(6)
        self.text = lbl(text, theme.FS_SMALL, wrap=True, line_height=1.5)
        lay.addWidget(self.text, 1)
        e = icon_btn("message-square-plus", "Edit comment", 13)
        e.clicked.connect(self.edit_requested.emit)
        lay.addWidget(e, 0, Qt.AlignmentFlag.AlignTop)
        d = icon_btn("trash", "Delete comment", 13)
        d.clicked.connect(self.delete_requested.emit)
        lay.addWidget(d, 0, Qt.AlignmentFlag.AlignTop)


class DiffGrid(QWidget):
    """One file's hunks, painted: line numbers, signs, code with word marks; comments hang under lines."""

    comment_changed = Signal(str, object, dict)  # key, text (None removes), line

    def __init__(
        self,
        hunks: List[Json],
        path: str = "",
        mode: str = "unified",
        wrap: bool = False,
        dual: bool = True,
        numbers: bool = True,
        comments: Optional[Dict[str, str]] = None,
        can_comment: bool = False,
        max_rows: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._hunks = hunks
        self._path = path
        self._mode = mode
        self._wrap = wrap
        self._dual = dual
        self._numbers = numbers
        self._can_comment = can_comment
        self._max_rows = max_rows
        self._expanded = False
        self._comments: Dict[str, str] = dict(comments or {})
        self._editing: Optional[str] = None
        self._hover = -1
        self._rows: List[Row] = []
        self._words: Dict[int, List[Range]] = {}
        self._toks: Dict[int, List[Tok]] = {}
        self._geom: List[_Geom] = []
        self._extra_rects: List[Tuple[QRect, str]] = []
        self._extras: Dict[str, QWidget] = {}
        self._key_lines: Dict[str, Json] = {}
        self._cols: Dict[str, int] = {}
        self._content_w = 0
        self._total_h = 0
        self._more: Optional[QPushButton] = None
        self._lh = LINE_H
        self._longest = 0.0  # the widest line's advance, measured once per rebuild
        self._digits = (1, 1)  # the number columns' widths, likewise
        self._fm_cache: Optional[QFontMetricsF] = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rebuild()
        bind(theme.theme.changed, self, lambda _t: self._on_theme())

    # ---- public
    @property
    def rows(self) -> List[Row]:
        return self._rows

    @property
    def shown_rows(self) -> List[Row]:
        return self._rows[: self._limit()]

    @property
    def columns(self) -> int:
        """Code columns: two side by side in split mode, one otherwise."""
        return 2 if self._mode == "split" else 1

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self._rebuild()

    def set_wrap(self, wrap: bool) -> None:
        if wrap != self._wrap:
            self._wrap = wrap
            self._relayout()

    def set_comments(self, comments: Dict[str, str]) -> None:
        self._comments = dict(comments)
        self._relayout()

    def comments(self) -> Dict[str, str]:
        return dict(self._comments)

    def open_editor(self, key: str) -> None:
        if key in self._key_lines:
            self._editing = key
            self._relayout()
            ed = self._extras.get(key)
            if isinstance(ed, CommentEditor):
                ed.edit.setFocus()

    def row_rect(self, i: int) -> QRect:
        g = self._geom[i]
        return QRect(0, g.y, self.width(), g.h)

    def row_tint(self, i: int) -> Optional[QColor]:
        """The background a row is painted with (None for plain context), for tests and tooling."""
        r = self._rows[i]
        t = theme.tokens()
        ln = r.line if r.kind == "line" else r.right or r.left
        if ln is None:
            return None
        if ln.get("t") == "+":
            return theme.qcolor(t.diff_add)
        if ln.get("t") == "-":
            return theme.qcolor(t.diff_del)
        return None

    # ---- layout
    def _limit(self) -> int:
        return len(self._rows) if (not self._max_rows or self._expanded) else min(self._max_rows, len(self._rows))

    def _on_theme(self) -> None:
        self._fm_cache = None
        self.update()

    def _rebuild(self) -> None:
        self._rows = build_rows(self._hunks, self._mode)
        self._words = word_ranges(self._hunks)
        self._toks = line_tokens([ln for h in self._hunks for ln in (h.get("lines") or [])], self._path)
        self._key_lines = {}
        for r in self._rows:
            for ln in (r.line, r.left, r.right):
                if ln is not None and ln.get("t") in ("+", "-", " "):
                    self._key_lines[line_key(ln)] = ln
        self._measure_lines()
        self._relayout()

    def _measure_lines(self) -> None:
        fm = self._fm()
        max_o = max_n = 1
        longest = 0.0
        for r in self._rows:
            for ln in (r.line, r.left, r.right):
                if ln is None:
                    continue
                max_o = max(max_o, int(ln.get("o") or 0))
                max_n = max(max_n, int(ln.get("n") or 0))
                longest = max(longest, fm.horizontalAdvance(str(ln.get("s") or "")))
        self._longest = longest
        self._digits = (len(str(max_o)), len(str(max_n)))

    def _fm(self) -> QFontMetricsF:
        if self._fm_cache is None:
            self._fm_cache = QFontMetricsF(theme.font_mono(DIFF_FONT_PX))
        return self._fm_cache

    def _segments(self, s: str, avail: float, fm: QFontMetricsF) -> List[Range]:
        """Where a line wraps: between tokens where it can, mid-token only when it must
        (``word-break: normal; overflow-wrap: anywhere``)."""
        if not self._wrap or avail <= 0 or not s:
            return [(0, len(s))]
        out: List[Range] = []
        start = 0
        acc = 0.0
        last_break = -1
        i = 0
        while i < len(s):
            w = fm.horizontalAdvance(s[i])
            if acc + w > avail and i > start:
                cut = last_break + 1 if last_break > start else i
                out.append((start, cut))
                start, i = cut, cut
                acc, last_break = 0.0, -1
                continue
            if s[i].isspace():
                last_break = i
            acc += w
            i += 1
        out.append((start, len(s)))
        return out

    def _relayout(self) -> None:
        fm = self._fm()
        lh = LINE_H
        self._lh = lh
        char_w = fm.horizontalAdvance("0")
        longest = self._longest
        d_o, d_n = self._digits
        width = max(self.width(), 1)
        two = self._dual and self._numbers and self._mode == "unified"
        cols: Dict[str, int] = {}
        if self._mode == "split":
            # .diff.split .dn: padding 0 8px 0 12px; no sign column; code right after the numbers
            nw_l = int(12 + char_w * d_o + 8)
            nw_r = int(12 + char_w * d_n + 8)
            half = max(40, (width - nw_l - nw_r) // 2)
            cols.update(dn_l=0, nw_l=nw_l, dc_l=nw_l, dn_r=nw_l + half, nw_r=nw_r, dc_r=nw_l + half + nw_r, half=half)
            avail = half - 18 - 4
            self._content_w = 0 if self._wrap else int(nw_l + nw_r + 2 * (longest + 18 + 4))
        else:
            x = 0
            if self._numbers:
                if two:
                    # .diff.dual .dn: 0 4px 0 12px; .dn-new: 0 10px 0 8px
                    cols["dn0"] = x
                    cols["nw0"] = int(12 + char_w * d_o + 4)
                    x += cols["nw0"]
                    cols["dn1"] = x
                    cols["nw1"] = int(8 + char_w * d_n + 10)
                    x += cols["nw1"]
                else:
                    cols["dn0"] = x
                    cols["nw0"] = int(14 + char_w * max(d_o, d_n) + 8)
                    x += cols["nw0"]
            cols["ds"] = x
            x += SIGN_W
            cols["dc"] = x
            avail = width - x - 18 - 4
            self._content_w = 0 if self._wrap else int(x + longest + 18 + 4)
        self._cols = cols
        self._sync_extras()
        geom: List[_Geom] = []
        extra_rects: List[Tuple[QRect, str]] = []
        y = 4.0  # .cfile-body .diff-grid padding-top
        for i, r in enumerate(self._rows[: self._limit()]):
            if r.kind == "hunk":
                y += -2 if i == 0 else 4  # the first hunk's margin-top is -2px
                geom.append(_Geom(int(round(y)), HUNK_H, [], []))
                y += HUNK_H + 4
                continue
            if r.kind == "line":
                segs = self._segments(str((r.line or {}).get("s") or ""), avail, fm)
                top = int(round(y))
                y += lh * len(segs)
                geom.append(_Geom(top, int(round(y)) - top, segs, []))
                keys = [line_key(r.line)] if r.line is not None else []
            else:
                sl = self._segments(str((r.left or {}).get("s") or ""), avail, fm) if r.left else [(0, 0)]
                sr = self._segments(str((r.right or {}).get("s") or ""), avail, fm) if r.right else [(0, 0)]
                top = int(round(y))
                y += lh * max(len(sl), len(sr))
                geom.append(_Geom(top, int(round(y)) - top, sl, sr))
                keys = []
                if r.right is not None:
                    keys.append(line_key(r.right))
                if r.left is not None and r.left is not r.right and r.left.get("t") == "-":
                    keys.append(line_key(r.left))
            for key in keys:
                w = self._extras.get(key)
                if w is None:
                    continue
                inner_w = max(80, width - 44 - 12)
                w.setFixedWidth(inner_w)
                wh = w.sizeHint().height()
                top = int(round(y))
                w.setGeometry(44, top + 8, inner_w, wh)
                extra_rects.append((QRect(0, top, width, wh + 18), key))
                y += wh + 18
        clipped = len(self._rows) > self._limit()
        if clipped:
            if self._more is None:
                self._more = Btn(f"Show all {len(self._rows)} lines", kind="more", on_click=self._expand)
                self._more.setParent(self)
                self._more.show()
            self._more.setText(f"Show all {len(self._rows)} lines")
            y += 28  # room under the last rows for the button that floats over the fade
        elif self._more is not None:
            self._more.deleteLater()
            self._more = None
        y += 8  # padding-bottom
        total = int(round(y))
        if self._more is not None:
            bw = self._more.sizeHint().width()
            self._more.setGeometry((width - bw) // 2, total - 9 - 24, bw, 24)
        self._geom = geom
        self._extra_rects = extra_rects
        self._total_h = total
        self.setMinimumWidth(self._content_w)
        self.setFixedHeight(self._total_h)
        self.updateGeometry()
        self.update()

    def _expand(self) -> None:
        self._expanded = True
        self._relayout()

    def _sync_extras(self) -> None:
        wanted = set(self._comments)
        if self._editing:
            wanted.add(self._editing)
        for key in list(self._extras):
            w = self._extras[key]
            editing = key == self._editing
            if (
                key not in wanted
                or (editing and not isinstance(w, CommentEditor))
                or (not editing and isinstance(w, CommentEditor))
            ):
                w.hide()
                w.deleteLater()
                del self._extras[key]
        for key in wanted:
            if key in self._extras:
                if not isinstance(self._extras[key], CommentEditor):
                    self._extras[key].text.setText(self._comments.get(key, ""))  # type: ignore[attr-defined]
                continue
            if key == self._editing:
                ed = CommentEditor(self._comments.get(key, ""), self)
                ed.saved.connect(lambda text, k=key: self._save(k, text))
                ed.cancelled.connect(self._cancel)
                ed.show()
                self._extras[key] = ed
            else:
                card = CommentCard(self._comments[key], self)
                card.edit_requested.connect(lambda k=key: self.open_editor(k))
                card.delete_requested.connect(lambda k=key: self._save(k, ""))
                card.show()
                self._extras[key] = card

    def _save(self, key: str, text: str) -> None:
        line = self._key_lines.get(key) or {}
        text = text.strip()
        if text:
            self._comments[key] = text
        else:
            self._comments.pop(key, None)
        self._editing = None
        self._relayout()
        self.comment_changed.emit(key, text or None, line)

    def _cancel(self) -> None:
        self._editing = None
        self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(max(self._content_w, 200), self._total_h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._content_w, self._total_h)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        # without wrapping the rows' heights do not depend on the width: only the columns move
        if self._wrap or self._mode == "split" or self._extras:
            self._relayout()
        else:
            self._place_columns()

    def _place_columns(self) -> None:
        """The width changed but no line wraps: recompute the columns and repaint, keep the rows."""
        fm = self._fm()
        char_w = fm.horizontalAdvance("0")
        d_o, d_n = self._digits
        width = max(self.width(), 1)
        two = self._dual and self._numbers and self._mode == "unified"
        x = 0
        cols: Dict[str, int] = {}
        if self._numbers:
            if two:
                cols["dn0"] = x
                cols["nw0"] = int(12 + char_w * d_o + 4)
                x += cols["nw0"]
                cols["dn1"] = x
                cols["nw1"] = int(8 + char_w * d_n + 10)
                x += cols["nw1"]
            else:
                cols["dn0"] = x
                cols["nw0"] = int(14 + char_w * max(d_o, d_n) + 8)
                x += cols["nw0"]
        cols["ds"] = x
        x += SIGN_W
        cols["dc"] = x
        self._cols = cols
        self._content_w = int(x + self._longest + 18 + 4)
        if self._more is not None:
            bw = self._more.sizeHint().width()
            self._more.setGeometry((width - bw) // 2, self._total_h - 9 - 24, bw, 24)
        self.update()

    # ---- interaction
    def _row_at(self, y: int) -> int:
        for i, g in enumerate(self._geom):
            if g.y <= y < g.y + g.h:
                return i
        return -1

    def _gutter_target(self, i: int, x: int) -> Optional[Json]:
        """Which line a click in the gutter of row ``i`` means (None when it is not a gutter)."""
        r = self._rows[i]
        c = self._cols
        if r.kind == "line":
            return r.line if x < c["dc"] else None
        if x < c["dc_l"]:
            if r.left is not None and r.left.get("t") == "-":
                return r.left
            return r.right
        if c["dn_r"] <= x < c["dc_r"]:
            return r.right
        return None

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        i = self._row_at(int(e.position().y()))
        if i != self._hover:
            self._hover = i
            self.update()
        if (
            self._can_comment
            and i >= 0
            and self._rows[i].kind != "hunk"
            and self._gutter_target(i, int(e.position().x())) is not None
        ):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._can_comment:
            i = self._row_at(int(e.position().y()))
            if i >= 0 and self._rows[i].kind != "hunk":
                ln = self._gutter_target(i, int(e.position().x()))
                if ln is not None:
                    self.open_editor(line_key(ln))
                    return
        super().mousePressEvent(e)

    # ---- painting
    def paintEvent(self, e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.qcolor(t.canvas))
        font = theme.font_mono(DIFF_FONT_PX)
        italic_font = theme.font_mono(DIFF_FONT_PX)
        italic_font.setItalic(True)
        fm = QFontMetricsF(font)
        p.setFont(font)
        clip = e.rect()
        c = self._cols
        lh = self._lh
        width = self.width()
        add_bg, del_bg = theme.qcolor(t.diff_add), theme.qcolor(t.diff_del)
        add_hl, del_hl = theme.qcolor(t.diff_add_hl), theme.qcolor(t.diff_del_hl)
        stripe = theme.qcolor(t.fill_1)
        text_c = QColor(t.text)
        baseline_off = (lh - fm.height()) / 2 + fm.ascent()

        def bg_of(ln: Optional[Json]) -> Optional[QColor]:
            if ln is None:
                return None
            return add_bg if ln.get("t") == "+" else del_bg if ln.get("t") == "-" else None

        def draw_void(rect: QRectF) -> None:
            """``repeating-linear-gradient(135deg, transparent 0 5px, var(--fill-1) 5px 6px)``."""
            p.save()
            p.setClipRect(rect)
            p.setPen(QPen(stripe, 1))
            x0 = int(rect.left() - rect.height())
            k = 0
            while x0 + k * 6 < rect.right() + rect.height():
                x = x0 + k * 6
                p.drawLine(QPointF(x, rect.bottom()), QPointF(x + rect.height(), rect.top()))
                k += 1
            p.restore()

        def draw_code(ln: Optional[Json], x: int, y: int, segs: List[Range]) -> None:
            if ln is None:
                return
            s = str(ln.get("s") or "")
            marks = self._words.get(id(ln)) or []
            toks = self._toks.get(id(ln)) or []
            hl = add_hl if ln.get("t") == "+" else del_hl
            for k, (a, b) in enumerate(segs):
                yy = y + k * lh
                for ma, mb in marks:
                    lo, hi = max(a, ma), min(b, mb)
                    if lo >= hi:
                        continue
                    x1 = x + fm.horizontalAdvance(s[a:lo])
                    x2 = x1 + fm.horizontalAdvance(s[lo:hi])
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(hl)
                    p.drawRoundedRect(QRectF(x1, yy + 2, x2 - x1, lh - 4), 2, 2)
                # the code, one coloured run at a time
                pos = a
                runs = [(max(a, ts), min(b, te), *token_look(tt, t)) for ts, te, tt in toks if te > a and ts < b] or [
                    (a, b, None, False)
                ]
                for ts, te, col, it in runs:
                    if ts > pos:
                        p.setFont(font)
                        p.setPen(text_c)
                        p.drawText(QPointF(x + fm.horizontalAdvance(s[a:pos]), yy + baseline_off), s[pos:ts])
                    p.setFont(italic_font if it else font)
                    p.setPen(col or text_c)
                    p.drawText(QPointF(x + fm.horizontalAdvance(s[a:ts]), yy + baseline_off), s[ts:te])
                    pos = te
                if pos < b:
                    p.setFont(font)
                    p.setPen(text_c)
                    p.drawText(QPointF(x + fm.horizontalAdvance(s[a:pos]), yy + baseline_off), s[pos:b])
            p.setFont(font)

        def draw_num(x: int, w: int, pad_r: int, y: int, n: Any, tone: str) -> None:
            if not self._numbers or n in (None, ""):
                return
            p.setPen(QColor(tone))
            p.drawText(QRectF(x, y, w - pad_r, lh), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(n))

        def draw_glyph(x: int, y: int) -> None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(text_c)
            p.drawRoundedRect(QRectF(x, y + (lh - GUTTER_GLYPH) / 2, GUTTER_GLYPH, GUTTER_GLYPH), theme.R1, theme.R1)
            pm = icons.pixmap("message-square-plus", t.canvas, 12)
            p.drawPixmap(QPointF(x + 3, y + (lh - 12) / 2), pm)

        for i, r in enumerate(self._rows[: self._limit()]):
            g = self._geom[i]
            if g.y + g.h < clip.top() or g.y > clip.bottom():
                continue
            if r.kind == "hunk":
                h = r.hunk or {}
                prev = self._hunks[r.hi - 1] if r.hi > 0 else None
                gap = int(h.get("oldStart") or 0) - (
                    (int(prev.get("oldStart") or 0) + int(prev.get("oldLines") or 0)) if prev else 1
                )
                p.fillRect(QRect(0, g.y, width, HUNK_H), theme.blend(t.fill_1, t.canvas))
                p.setFont(theme.font_ui(theme.FS_MICRO))
                p.setPen(QColor(t.text_4))
                x = 14
                dots_w = QFontMetricsF(theme.font_ui(theme.FS_MICRO)).horizontalAdvance("⋯") + 2
                p.drawText(QRectF(x, g.y, dots_w, HUNK_H), Qt.AlignmentFlag.AlignVCenter, "⋯")
                x += dots_w + 10
                txt = f"{gap} unchanged line{'s' if gap > 1 else ''}" if gap > 0 else ""
                if txt:
                    p.setPen(QColor(t.text_3))
                    tw = QFontMetricsF(theme.font_ui(theme.FS_MICRO)).horizontalAdvance(txt)
                    p.drawText(QRectF(x, g.y, tw + 2, HUNK_H), Qt.AlignmentFlag.AlignVCenter, txt)
                    x += tw + 10
                if h.get("header"):
                    mf = theme.font_mono(11)
                    p.setFont(mf)
                    p.setPen(QColor(t.text_4))
                    avail = max(0, width - x - 14)
                    p.drawText(
                        QRectF(x, g.y, avail, HUNK_H),
                        Qt.AlignmentFlag.AlignVCenter,
                        QFontMetricsF(mf).elidedText(str(h["header"]), Qt.TextElideMode.ElideRight, int(avail)),
                    )
                p.setFont(font)
                continue
            hovered = i == self._hover and self._can_comment
            if r.kind == "line":
                ln = r.line or {}
                bg = bg_of(ln)
                if bg is not None:
                    p.fillRect(QRect(0, g.y, width, g.h), bg)
                num_tone = t.text_3 if ln.get("t") in ("+", "-") else t.text_4
                if "dn1" in c:
                    draw_num(c["dn0"], c["nw0"], 4, g.y, "" if ln.get("t") == "+" else ln.get("o"), num_tone)
                    draw_num(c["dn1"], c["nw1"], 10, g.y, "" if ln.get("t") == "-" else ln.get("n"), num_tone)
                elif "dn0" in c:
                    draw_num(c["dn0"], c["nw0"], 8, g.y, ln.get("o") if ln.get("t") == "-" else ln.get("n"), num_tone)
                sign = " " if ln.get("t") == " " else "−" if ln.get("t") == "-" else "+"
                p.setPen(QColor(t.ok if sign == "+" else t.err if sign == "−" else t.text_4))
                p.drawText(QRectF(c["ds"], g.y, SIGN_W, lh), Qt.AlignmentFlag.AlignCenter, sign)
                draw_code(ln, c["dc"], g.y, g.segs_l)
                if hovered:
                    draw_glyph(c["ds"], g.y)
                continue
            # split
            lbg, rbg = bg_of(r.left), bg_of(r.right)
            if r.left is None:
                draw_void(QRectF(c["dn_l"], g.y, c["dn_r"] - c["dn_l"], g.h))
            elif lbg is not None:
                p.fillRect(QRect(c["dn_l"], g.y, c["dn_r"] - c["dn_l"], g.h), lbg)
            if r.right is None:
                draw_void(QRectF(c["dn_r"], g.y, width - c["dn_r"], g.h))
            elif rbg is not None:
                p.fillRect(QRect(c["dn_r"], g.y, width - c["dn_r"], g.h), rbg)
            if r.left is not None:
                draw_num(
                    c["dn_l"], c["nw_l"], 8, g.y, r.left.get("o"), t.text_3 if r.left.get("t") == "-" else t.text_4
                )
                draw_code(r.left, c["dc_l"], g.y, g.segs_l)
            if r.right is not None:
                draw_num(
                    c["dn_r"], c["nw_r"], 8, g.y, r.right.get("n"), t.text_3 if r.right.get("t") == "+" else t.text_4
                )
                draw_code(r.right, c["dc_r"], g.y, g.segs_r)
            if hovered:
                target = r.left if (r.left is not None and r.left.get("t") == "-" and r.right is None) else r.right
                draw_glyph(c["dn_l"] if target is r.left and target is not r.right else c["dn_r"], g.y)
        # comment rows: a quiet band of chrome with hairlines above and below
        chrome = theme.qcolor(t.chrome)
        hair = theme.blend(t.hair, t.chrome)
        for rect, _key in self._extra_rects:
            if rect.bottom() < clip.top() or rect.top() > clip.bottom():
                continue
            p.fillRect(rect, chrome)
            p.setPen(QPen(hair, 1))
            p.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
            p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if self._more is not None:
            # .code-more.fade: the last rows dissolve into the inset colour under the button
            grad = QLinearGradient(0, self._total_h - 52, 0, self._total_h)
            inset = theme.qcolor(t.inset)
            clear = QColor(inset)
            clear.setAlpha(0)
            grad.setColorAt(0, clear)
            grad.setColorAt(0.72, inset)
            grad.setColorAt(1, inset)
            p.fillRect(QRect(0, self._total_h - 52, width, 52), grad)


# ---------------------------------------------------------------- files

Action = Tuple[str, Callable[[Json], str], Callable[[Json], None]]  # icon, tooltip(file), run(file)


class _Head(QWidget):
    """``.cfile-head``: the chrome strip; its icon buttons appear on hover."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.hover_widgets: List[QWidget] = []
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def enterEvent(self, e) -> None:  # noqa: N802
        for w in self.hover_widgets:
            w.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        for w in self.hover_widgets:
            w.setVisible(False)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), theme.qcolor(theme.tokens().chrome))


class DiffFileRow(QWidget):
    """A file in the list (``.cfile``): the header with status, name, counts and actions; the grid below.
    In ``simple`` mode (edits made in this session) the header is just the name and folder, and opens
    the file."""

    toggled = Signal(str, bool)

    def __init__(self, f: Json, view: DiffView, open_: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.file = f
        self._view = view
        self.path = str(f.get("path") or "")
        simple = view.simple
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = _Head()
        self.head.setMinimumHeight(40)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(view.head_pad_left, 0, 8, 0)
        hl.setSpacing(6)
        self.toggle = QWidget()
        self.toggle.setFixedHeight(30)
        tl = QHBoxLayout(self.toggle)
        tl.setContentsMargins(4, 0, 4, 0)
        tl.setSpacing(7)
        self.chev = QLabel()
        self.chev.setFixedSize(13, 13)
        if not simple:
            tl.addWidget(self.chev)
            tl.addWidget(StatusBadge(str(f.get("status") or "M"), bool(f.get("untracked"))))
        self.name = lbl(basename(self.path), 12.5, 500, mono=True)
        tl.addWidget(self.name)
        d = dirname(self.path)
        shown = str(f.get("_dir") if f.get("_dir") is not None else d)
        dir_text = "" if (d == "/" or "/" not in self.path.replace("\\", "/")) and f.get("_dir") is None else shown
        self.dir = elide(lbl(rtl_display(dir_text), 11.5, tone="faint", mono=True))
        old = f.get("oldPath")
        if old and old != self.path:
            self.dir.setToolTip(f"Renamed from {old}")
        tl.addWidget(self.dir, 1)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setAccessibleName(f"Toggle {basename(self.path)}")
        Clickable(self.toggle).clicked.connect(self._toggle if not simple else lambda: view.on_open(f))
        hl.addWidget(self.toggle, 1)
        self.note_badge = NoteBadge()
        self.note_badge.hide()
        if not simple:
            hl.addWidget(self.note_badge)
            self.add = Num("add", int(f.get("additions") or 0))
            self.dele = Num("del", int(f.get("deletions") or 0))
            hl.addWidget(self.add)
            hl.addWidget(self.dele)
            for icon_name, tip, run in view.actions:
                b = icon_btn(icon_name, tip(f), 13)
                b.clicked.connect(lambda _c=False, fn=run, ff=f: fn(ff))
                b.hide()
                self.head.hover_widgets.append(b)
                hl.addWidget(b)
        lay.addWidget(self.head)
        self.head_line = Hairline(over="chrome")
        lay.addWidget(self.head_line)
        self.grid: Optional[DiffGrid] = None
        self.body = Collapse(self._build_body, open_ or simple)
        lay.addWidget(self.body)
        self.line = Hairline()
        lay.addWidget(self.line)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.chev.setPixmap(icons.pixmap("chevron-down" if self.body.is_open else "chevron-right", t.text_3, 13))
        self.head_line.setVisible(self.body.is_open)

    def _build_body(self) -> QWidget:
        f = self.file
        if f.get("binary"):
            return _note("Binary file")
        hunks = f.get("hunks") or []
        if f.get("truncated") and not hunks:
            return _note("This diff is too large to show here.")
        v = self._view
        notes = {k: n["text"] for k, n in (v.notes.get(self.path) or {}).items()}
        self.grid = DiffGrid(
            hunks,
            str(f.get("_abs") or self.path),
            mode=v.mode,
            wrap=v.wrap,
            dual=v.dual,
            comments=notes,
            can_comment=v.can_comment,
            max_rows=v.max_rows,
        )
        self.grid.comment_changed.connect(lambda key, text, line: v._on_comment(self.path, key, text, line))
        return self.grid

    def _toggle(self) -> None:
        self.set_open(not self.body.is_open)
        self.toggled.emit(self.path, self.body.is_open)

    def set_open(self, open_: bool) -> None:
        self.body.set_open(open_)
        self._paint()

    def set_note_count(self, n: int) -> None:
        self.note_badge.set_count(n)
        self.note_badge.setVisible(n > 0 and not self._view.simple)


def _note(text: str) -> QLabel:
    lb = lbl(text, theme.FS_SMALL, tone="faint")
    lb.setContentsMargins(14, 12, 14, 12)
    return lb


class DiffView(QWidget):
    """A list of changed files with their diffs (``.changes-files``). ``set_files`` replaces the list
    (keeping which files are folded); ``mode`` is 'unified' or 'split'; review notes live here per path
    and line key."""

    notes_changed = Signal()

    def __init__(
        self,
        mode: str = "unified",
        wrap: bool = False,
        can_comment: bool = False,
        max_rows: int = 600,
        open_threshold: int = 8,
        dual: bool = True,
        simple: bool = False,
        head_pad_left: int = 6,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.wrap = wrap
        self.can_comment = can_comment
        self.max_rows = max_rows
        self.open_threshold = open_threshold
        self.dual = dual
        self.simple = simple
        self.head_pad_left = head_pad_left
        self.on_open: Callable[[Json], None] = lambda _f: None
        self.actions: List[Action] = []
        self.notes: Dict[str, Dict[str, Json]] = {}
        self._files: List[Json] = []
        self._open: Dict[str, bool] = {}
        self._rows: List[DiffFileRow] = []
        self._pending_rows: List[Optional[DiffFileRow]] = []
        self._gen = 0
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        # The list is always exactly as tall as its cards. Without this a card whose grid has just
        # measured itself makes the list lay out once in its old height (every card squeezed) and
        # once more when the scroll host catches up: two passes of geometry over every card and
        # its parts for each card added, tens of thousands of events while a change of a hundred
        # files comes in.
        self._lay.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)

    # ---- public
    def files(self) -> List[Json]:
        return list(self._files)

    def rows(self) -> List[DiffFileRow]:
        return list(self._rows)

    def set_actions(self, actions: List[Action]) -> None:
        self.actions = list(actions)

    # Cards are built for this long per turn of the event loop (well under a frame, with the layout
    # and the paint that follow): the first ones paint before the rest exist, and no turn holds the window.
    BUDGET_MS = 4.0

    def set_files(self, files: List[Json]) -> None:
        if files == self._files and self._rows:
            return
        self._files = [dict(f) for f in files]
        self._gen = getattr(self, "_gen", 0) + 1
        # a file whose diff did not change keeps its card (and its grid, its scroll, its open state)
        had = {r.path: r for r in self._rows}
        rows: List[Optional[DiffFileRow]] = []
        todo: List[int] = []
        for i, f in enumerate(self._files):
            path = str(f.get("path") or "")
            old = had.pop(path, None)
            if old is not None and old.file == f:
                rows.append(old)
                continue
            if old is not None:
                self._lay.removeWidget(old)
                old.hide()
                old.setParent(None)
                old.deleteLater()
            rows.append(None)
            todo.append(i)
        for old in had.values():
            self._lay.removeWidget(old)
            old.hide()
            old.setParent(None)
            old.deleteLater()
        self._pending_rows = rows
        self._build_rows(todo, self._gen)

    def _make_row(self, i: int) -> DiffFileRow:
        f = self._files[i]
        path = str(f.get("path") or "")
        open_ = self._open.get(path, len(self._files) <= self.open_threshold)
        row = DiffFileRow(f, self, open_)
        row.toggled.connect(lambda p, o: self._open.__setitem__(p, o))
        row.set_note_count(len(self.notes.get(path) or {}))
        return row

    def _build_rows(self, todo: List[int], gen: int) -> None:
        """A batch of new cards, placed in order among the kept ones; the rest on the next turn of the
        event loop, so a change of a hundred files never holds the window."""
        if gen != getattr(self, "_gen", 0):
            return  # a newer list came
        rows = self._pending_rows
        t0 = time.perf_counter()
        k = 0
        while k < len(todo) and (k == 0 or (time.perf_counter() - t0) * 1000 < self.BUDGET_MS):
            rows[todo[k]] = self._make_row(todo[k])
            k += 1
        rest = todo[k:]
        self._place_rows([r for r in rows if r is not None])
        if rest:
            # a timer of the view's own: a view deleted with cards still to come takes it along
            QTimer.singleShot(0, self, lambda: self._build_rows(rest, gen))
        else:
            self._pending_rows = []

    def _place_rows(self, rows: List[DiffFileRow]) -> None:
        for i, row in enumerate(rows):
            item = self._lay.itemAt(i)
            if item is not None and item.widget() is row:
                continue
            self._lay.removeWidget(row)
            self._lay.insertWidget(i, row)
            row.show()
        self._rows = rows

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        for r in self._rows:
            if r.grid is not None:
                r.grid.set_mode(mode)

    def set_wrap(self, wrap: bool) -> None:
        if wrap == self.wrap:
            return
        self.wrap = wrap
        for r in self._rows:
            if r.grid is not None:
                r.grid.set_wrap(wrap)

    def set_notes(self, notes: Dict[str, Dict[str, Json]]) -> None:
        self.notes = {p: dict(m) for p, m in notes.items() if m}
        for r in self._rows:
            r.set_note_count(len(self.notes.get(r.path) or {}))
            if r.grid is not None:
                r.grid.set_comments({k: n["text"] for k, n in (self.notes.get(r.path) or {}).items()})
        self.notes_changed.emit()

    def clear_notes(self) -> None:
        self.set_notes({})

    def note_count(self) -> int:
        return sum(len(m) for m in self.notes.values())

    def notes_message(self) -> str:
        """The notes as one request to Claude, file by file, each with the line it was left on."""
        parts = ["Please address my review notes on the current changes:", ""]
        for path, m in self.notes.items():
            entries = sorted(m.items(), key=lambda kv: kv[1].get("lineNo", 0))
            if not entries:
                continue
            parts.append(path)
            for key, n in entries:
                removed = " (removed line)" if key.startswith("old") else ""
                parts.append(
                    f"- line {n.get('lineNo', 0)}{removed}: {n['text']}\n  > {str(n.get('line') or '').strip()[:160]}"
                )
            parts.append("")
        return "\n".join(parts).strip()

    # ---- internal
    def _on_comment(self, path: str, key: str, text: Optional[str], line: Json) -> None:
        cur = dict(self.notes.get(path) or {})
        if text:
            cur[key] = {"text": text, "line": str(line.get("s") or ""), "lineNo": line_no(line)}
        else:
            cur.pop(key, None)
        if cur:
            self.notes[path] = cur
        else:
            self.notes.pop(path, None)
        for r in self._rows:
            if r.path == path:
                r.set_note_count(len(cur))
        self.notes_changed.emit()
