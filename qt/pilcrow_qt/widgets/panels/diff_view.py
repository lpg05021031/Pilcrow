"""The diff renderer shared by the Changes and Timeline tabs (a port of DiffView in web/src/components/Code.tsx).

A :class:`DiffView` lists files (``DiffFile`` dicts, see web/src/lib/types.ts); each file is a header
row with its status, name, +added −deleted counts and actions, over a collapsible :class:`DiffGrid`.
The grid paints the hunks itself: unified (two number columns, as in a code review) or side by side,
with word-level highlights on changed pairs of lines (difflib stands in for the ``diff`` library).
Review notes attach to a line through the gutter; :meth:`DiffView.notes_message` gathers them into
one message for Claude.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, dirname
from ..ui.bits import Clickable, IconButton, button, label
from ..ui.collapse import Collapse
from ..ui.signals import bind

Json = Dict[str, Any]
Range = Tuple[int, int]

STATUS_LABEL = {"M": "Modified", "A": "Added", "D": "Deleted", "R": "Renamed"}
DIFF_FONT_PX = 12
HUNK_H = 30
GUTTER_GLYPH = 18


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


# ---------------------------------------------------------------- small painted bits


class StatusBadge(QLabel):
    """The 16px file-status square: M grey, A/U green, D red, R blue (``.fstat``)."""

    def __init__(self, status: str, untracked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__("U" if untracked else status, parent)
        self._status = "U" if untracked else status
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("Untracked" if untracked else STATUS_LABEL.get(status, status))
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        fg, bg = {
            "A": (t.ok, t.ok_wash),
            "U": (t.ok, t.ok_wash),
            "D": (t.err, t.err_wash),
            "R": (t.info, t.info_wash),
        }.get(self._status, (t.text_2, t.fill_2))
        mono = theme.font_mono(10).family()
        self.setStyleSheet(
            f'color: {fg}; background: {theme.blend(bg, t.chrome).name()}; border-radius: 4px; font-family: "{mono}"; font-size: 10px; font-weight: 650;'
        )


class Num(QLabel):
    """``+12`` in green or ``−3`` in red (``.num-add`` / ``.num-del``); grey when zero."""

    def __init__(self, kind: str, n: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kind, self._n = kind, n
        self.setProperty("mono", "true")
        self.set_value(n)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_value(self, n: int) -> None:
        self._n = n
        self.setText(f"{'+' if self._kind == 'add' else '−'}{n}")
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        color = t.text_4 if not self._n else (t.ok if self._kind == "add" else t.err)
        self.setStyleSheet(f'color: {color}; font-family: "{theme.font_mono().family()}"; font-size: 11.5px;')


# ---------------------------------------------------------------- the grid


@dataclass
class _Geom:
    y: int
    h: int
    segs_l: List[Range]
    segs_r: List[Range]


class CommentEditor(QWidget):
    """The note box under a line: a text area, Cancel and Save note (Ctrl+Enter saves, Escape cancels)."""

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
        self.edit.setFixedHeight(56)
        self.edit.setFont(theme.font_ui(12.5))
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        row.addWidget(button("Cancel", kind="ghost", size="sm", on_click=self.cancelled.emit))
        self.save = button(
            "Save note",
            kind="primary",
            size="sm",
            icon="check",
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


class CommentCard(QWidget):
    """A saved note under a line, with edit and delete."""

    edit_requested = Signal()
    delete_requested = Signal()

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("diffComment")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(6)
        self.text = label(text, size="small", wrap=True)
        lay.addWidget(self.text, 1)
        e = IconButton("message-square-plus", "Edit comment", 13)
        e.clicked.connect(self.edit_requested.emit)
        lay.addWidget(e, 0, Qt.AlignmentFlag.AlignTop)
        d = IconButton("trash", "Delete comment", 13)
        d.clicked.connect(self.delete_requested.emit)
        lay.addWidget(d, 0, Qt.AlignmentFlag.AlignTop)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#diffComment {{ background: {t.raised}; border-radius: {theme.R2}px; border: 1px solid {theme.blend(t.hair, t.raised).name()}; }}"
        )


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
        self._geom: List[_Geom] = []
        self._extra_rects: List[Tuple[QRect, str]] = []
        self._extras: Dict[str, QWidget] = {}
        self._key_lines: Dict[str, Json] = {}
        self._cols: Dict[str, int] = {}
        self._content_w = 0
        self._total_h = 0
        self._more: Optional[QPushButton] = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rebuild()
        bind(theme.theme.changed, self, lambda _t: self.update())

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

    def _rebuild(self) -> None:
        self._rows = build_rows(self._hunks, self._mode)
        self._words = word_ranges(self._hunks)
        self._key_lines = {}
        for r in self._rows:
            for ln in (r.line, r.left, r.right):
                if ln is not None and ln.get("t") in ("+", "-", " "):
                    self._key_lines[line_key(ln)] = ln
        self._relayout()

    def _fm(self) -> QFontMetricsF:
        return QFontMetricsF(theme.font_mono(DIFF_FONT_PX))

    def _segments(self, s: str, avail: float, fm: QFontMetricsF) -> List[Range]:
        if not self._wrap or avail <= 0 or not s:
            return [(0, len(s))]
        out: List[Range] = []
        start = 0
        acc = 0.0
        for i, ch in enumerate(s):
            w = fm.horizontalAdvance(ch)
            if acc + w > avail and i > start:
                out.append((start, i))
                start, acc = i, 0.0
            acc += w
        out.append((start, len(s)))
        return out

    def _relayout(self) -> None:
        fm = self._fm()
        lh = max(19, int(round(fm.height() + 4)))
        self._lh = lh
        char_w = fm.horizontalAdvance("0")
        max_no = 1
        longest = 0.0
        for r in self._rows:
            for ln in (r.line, r.left, r.right):
                if ln is None:
                    continue
                max_no = max(max_no, int(ln.get("o") or 0), int(ln.get("n") or 0))
                longest = max(longest, fm.horizontalAdvance(str(ln.get("s") or "")))
        digits = max(2, len(str(max_no)))
        num_w = int(char_w * digits + 22) if self._numbers else 0
        ds_w = 18
        pad = 8
        width = max(self.width(), 1)
        two = self._dual and self._numbers and self._mode == "unified"
        cols: Dict[str, int] = {"num_w": num_w, "ds_w": ds_w, "pad": pad}
        if self._mode == "split":
            half = max(40, (width - 2 * num_w) // 2)
            cols.update(dn_l=0, dc_l=num_w, dn_r=num_w + half, dc_r=2 * num_w + half, half=half)
            avail = half - pad - 12
            self._content_w = 0 if self._wrap else int(2 * num_w + 2 * (longest + pad + 18))
        else:
            x = 0
            cols["dn0"] = x
            x += num_w
            if two:
                cols["dn1"] = x
                x += num_w
            cols["ds"] = x
            x += ds_w
            cols["dc"] = x
            avail = width - x - pad - 12
            self._content_w = 0 if self._wrap else int(x + longest + pad + 18)
        self._cols = cols
        self._sync_extras()
        geom: List[_Geom] = []
        extra_rects: List[Tuple[QRect, str]] = []
        y = 6
        for r in self._rows[: self._limit()]:
            if r.kind == "hunk":
                geom.append(_Geom(y, HUNK_H, [], []))
                y += HUNK_H
                continue
            if r.kind == "line":
                segs = self._segments(str((r.line or {}).get("s") or ""), avail, fm)
                h = lh * len(segs)
                geom.append(_Geom(y, h, segs, []))
                y += h
                keys = [line_key(r.line)] if r.line is not None else []
            else:
                sl = self._segments(str((r.left or {}).get("s") or ""), avail, fm) if r.left else [(0, 0)]
                sr = self._segments(str((r.right or {}).get("s") or ""), avail, fm) if r.right else [(0, 0)]
                h = lh * max(len(sl), len(sr))
                geom.append(_Geom(y, h, sl, sr))
                y += h
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
                w.setGeometry(44, y + 8, inner_w, wh)
                extra_rects.append((QRect(0, y, width, wh + 18), key))
                y += wh + 18
        if len(self._rows) > self._limit():
            if self._more is None:
                self._more = button(f"Show all {len(self._rows)} lines", kind="link", size="sm", on_click=self._expand)
                self._more.setParent(self)
                self._more.show()
            self._more.setText(f"Show all {len(self._rows)} lines")
            self._more.setGeometry(14, y + 4, self._more.sizeHint().width(), 24)
            y += 32
        elif self._more is not None:
            self._more.deleteLater()
            self._more = None
        y += 8
        self._geom = geom
        self._extra_rects = extra_rects
        self._total_h = y
        self.setMinimumWidth(self._content_w)
        self.setFixedHeight(y)
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
        self._relayout()

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
        fm = QFontMetricsF(font)
        p.setFont(font)
        clip = e.rect()
        c = self._cols
        lh = self._lh
        width = self.width()
        add_bg, del_bg = theme.qcolor(t.diff_add), theme.qcolor(t.diff_del)
        add_hl, del_hl = theme.qcolor(t.diff_add_hl), theme.qcolor(t.diff_del_hl)
        void_bg = theme.qcolor(t.fill_1)
        void_bg.setAlphaF(void_bg.alphaF() * 0.6)
        baseline_off = (lh - fm.height()) / 2 + fm.ascent()

        def bg_of(ln: Optional[Json]) -> Optional[QColor]:
            if ln is None:
                return void_bg
            return add_bg if ln.get("t") == "+" else del_bg if ln.get("t") == "-" else None

        def draw_code(ln: Optional[Json], x: int, y: int, segs: List[Range]) -> None:
            if ln is None:
                return
            s = str(ln.get("s") or "")
            marks = self._words.get(id(ln)) or []
            hl = add_hl if ln.get("t") == "+" else del_hl
            for k, (a, b) in enumerate(segs):
                yy = y + k * lh
                for ma, mb in marks:
                    lo, hi = max(a, ma), min(b, mb)
                    if lo >= hi:
                        continue
                    x1 = x + c["pad"] + fm.horizontalAdvance(s[a:lo])
                    x2 = x1 + fm.horizontalAdvance(s[lo:hi])
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(hl)
                    p.drawRoundedRect(QRectF(x1, yy + 2, x2 - x1, lh - 4), 2, 2)
                p.setPen(QColor(t.text))
                p.drawText(QPointF(x + c["pad"], yy + baseline_off), s[a:b])

        def draw_num(x: int, y: int, n: Any, tone: str) -> None:
            if not self._numbers or n in (None, ""):
                return
            p.setPen(QColor(tone))
            p.drawText(
                QRectF(x, y, c["num_w"] - 8, lh), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(n)
            )

        def draw_glyph(x: int, y: int) -> None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.text))
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
                p.fillRect(QRect(0, g.y + 2, width, HUNK_H - 4), theme.qcolor(t.fill_1))
                p.setFont(theme.font_ui(theme.FS_MICRO))
                p.setPen(QColor(t.text_4))
                p.drawText(QRectF(14, g.y, 20, HUNK_H), Qt.AlignmentFlag.AlignVCenter, "⋯")
                p.setPen(QColor(t.text_3))
                txt = f"{gap} unchanged line{'s' if gap > 1 else ''}" if gap > 0 else ""
                p.drawText(QRectF(34, g.y, width - 48, HUNK_H), Qt.AlignmentFlag.AlignVCenter, txt)
                if h.get("header"):
                    p.setFont(theme.font_mono(11))
                    p.setPen(QColor(t.text_4))
                    tw = QFontMetricsF(theme.font_ui(theme.FS_MICRO)).horizontalAdvance(txt)
                    hx = 34 + tw + (10 if txt else 0)
                    p.drawText(
                        QRectF(hx, g.y, max(0, width - hx - 14), HUNK_H),
                        Qt.AlignmentFlag.AlignVCenter,
                        QFontMetricsF(theme.font_mono(11)).elidedText(
                            str(h["header"]), Qt.TextElideMode.ElideRight, int(max(0, width - hx - 14))
                        ),
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
                    draw_num(c["dn0"], g.y, "" if ln.get("t") == "+" else ln.get("o"), num_tone)
                    draw_num(c["dn1"], g.y, "" if ln.get("t") == "-" else ln.get("n"), num_tone)
                else:
                    draw_num(c["dn0"], g.y, ln.get("o") if ln.get("t") == "-" else ln.get("n"), num_tone)
                sign = " " if ln.get("t") == " " else "−" if ln.get("t") == "-" else "+"
                p.setPen(QColor(t.ok if sign == "+" else t.err if sign == "−" else t.text_4))
                p.drawText(QRectF(c["ds"], g.y, c["ds_w"], lh), Qt.AlignmentFlag.AlignCenter, sign)
                draw_code(ln, c["dc"], g.y, g.segs_l)
                if hovered:
                    draw_glyph(2, g.y)
                continue
            # split
            half = c["half"]
            lbg, rbg = bg_of(r.left), bg_of(r.right)
            if lbg is not None:
                p.fillRect(QRect(c["dn_l"], g.y, c["num_w"] + half, g.h), lbg)
            if rbg is not None:
                p.fillRect(QRect(c["dn_r"], g.y, width - c["dn_r"], g.h), rbg)
            p.setPen(QPen(theme.blend(t.hair, t.canvas), 1))
            p.drawLine(c["dn_r"], g.y, c["dn_r"], g.y + g.h)
            if r.left is not None:
                draw_num(c["dn_l"], g.y, r.left.get("o"), t.text_3 if r.left.get("t") == "-" else t.text_4)
                draw_code(r.left, c["dc_l"], g.y, g.segs_l)
            if r.right is not None:
                draw_num(c["dn_r"], g.y, r.right.get("n"), t.text_3 if r.right.get("t") == "+" else t.text_4)
                draw_code(r.right, c["dc_r"], g.y, g.segs_r)
            if hovered:
                target = r.left if (r.left is not None and r.left.get("t") == "-" and r.right is None) else r.right
                draw_glyph(c["dn_l"] + 2 if target is r.left and target is not r.right else c["dn_r"] + 2, g.y)
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


# ---------------------------------------------------------------- files

Action = Tuple[str, Callable[[Json], str], Callable[[Json], None]]  # icon, tooltip(file), run(file)


class DiffFileRow(QWidget):
    """A file in the list (``.cfile``): the header with status, name, counts and actions; the grid below."""

    toggled = Signal(str, bool)

    def __init__(self, f: Json, view: DiffView, open_: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.file = f
        self._view = view
        self.path = str(f.get("path") or "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        self.head.setMinimumHeight(40)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(6, 0, 8, 0)
        hl.setSpacing(6)
        self.toggle = QWidget()
        tl = QHBoxLayout(self.toggle)
        tl.setContentsMargins(4, 0, 4, 0)
        tl.setSpacing(7)
        self.chev = QLabel()
        self.chev.setFixedSize(13, 13)
        tl.addWidget(self.chev)
        tl.addWidget(StatusBadge(str(f.get("status") or "M"), bool(f.get("untracked"))))
        self.name = label(basename(self.path), mono=True)
        self.name.setStyleSheet(f'font-family: "{theme.font_mono().family()}"; font-size: 12.5px; font-weight: 500;')
        tl.addWidget(self.name)
        d = dirname(self.path)
        old = f.get("oldPath")
        dir_text = "" if d == "/" or "/" not in self.path.replace("\\", "/") else d
        if old and old != self.path:
            dir_text = f"{old} → {dir_text}" if dir_text else f"{old} →"
        self.dir = label(dir_text, tone="faint", mono=True)
        self.dir.setStyleSheet(f'font-family: "{theme.font_mono().family()}"; font-size: 11.5px;')
        self.dir.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tl.addWidget(self.dir, 1)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setAccessibleName(f"Toggle {basename(self.path)}")
        Clickable(self.toggle).clicked.connect(self._toggle)
        hl.addWidget(self.toggle, 1)
        self.note_badge = label("", tone="accent", size="micro")
        self.note_badge.hide()
        hl.addWidget(self.note_badge)
        self.add = Num("add", int(f.get("additions") or 0))
        self.dele = Num("del", int(f.get("deletions") or 0))
        hl.addWidget(self.add)
        hl.addWidget(self.dele)
        for icon_name, tip, run in view.actions:
            b = IconButton(icon_name, tip(f), 13)
            b.clicked.connect(lambda _c=False, fn=run, ff=f: fn(ff))
            hl.addWidget(b)
        lay.addWidget(self.head)
        self.grid: Optional[DiffGrid] = None
        self.body = Collapse(self._build_body, open_)
        lay.addWidget(self.body)
        self.line = QWidget()
        self.line.setFixedHeight(1)
        lay.addWidget(self.line)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.chev.setPixmap(icons.pixmap("chevron-down" if self.body.is_open else "chevron-right", t.text_3, 13))
        self.line.setStyleSheet(f"background: {theme.blend(t.hair, t.chrome).name()};")
        self.note_badge.setStyleSheet(
            f"color: {t.accent_text}; background: {theme.blend(t.accent_wash, t.chrome).name()}; border-radius: 8px; padding: 1px 6px; font-size: {theme.FS_MICRO}px; font-weight: 600;"
        )

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
            self.path,
            mode=v.mode,
            wrap=v.wrap,
            dual=True,
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
        self.note_badge.setVisible(n > 0)
        self.note_badge.setText(f"✎ {n}")
        self.note_badge.setToolTip("Review notes")


def _note(text: str) -> QLabel:
    lb = label(text, tone="faint", size="small")
    lb.setContentsMargins(14, 12, 14, 12)
    return lb


class DiffView(QWidget):
    """A list of changed files with their diffs. ``set_files`` replaces the list (keeping which files are
    folded); ``mode`` is 'unified' or 'split'; review notes live here per path and line key."""

    notes_changed = Signal()

    def __init__(
        self,
        mode: str = "unified",
        wrap: bool = False,
        can_comment: bool = False,
        max_rows: int = 600,
        open_threshold: int = 8,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.wrap = wrap
        self.can_comment = can_comment
        self.max_rows = max_rows
        self.open_threshold = open_threshold
        self.actions: List[Action] = []
        self.notes: Dict[str, Dict[str, Json]] = {}
        self._files: List[Json] = []
        self._open: Dict[str, bool] = {}
        self._rows: List[DiffFileRow] = []
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    # ---- public
    def files(self) -> List[Json]:
        return list(self._files)

    def rows(self) -> List[DiffFileRow]:
        return list(self._rows)

    def set_actions(self, actions: List[Action]) -> None:
        self.actions = list(actions)

    def set_files(self, files: List[Json]) -> None:
        if files == self._files and self._rows:
            return
        self._files = [dict(f) for f in files]
        for r in self._rows:
            self._lay.removeWidget(r)
            r.setParent(None)
            r.deleteLater()
        self._rows = []
        for f in self._files:
            path = str(f.get("path") or "")
            open_ = self._open.get(path, len(self._files) <= self.open_threshold)
            row = DiffFileRow(f, self, open_)
            row.toggled.connect(lambda p, o: self._open.__setitem__(p, o))
            row.set_note_count(len(self.notes.get(path) or {}))
            self._lay.addWidget(row)
            self._rows.append(row)

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
