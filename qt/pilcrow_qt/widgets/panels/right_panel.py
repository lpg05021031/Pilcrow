"""The side panel (a port of web/src/components/RightPanel.tsx): a strip of tabs over the chosen tab's
content. Changes, Files, Terminal, Preview, Tasks, Timeline and Session, each built the first time it
is opened and kept across switches (the web unmounts most of them; here Changes and Timeline refresh
when shown again instead); every page is rebuilt when the session or its folder changes.

This module also holds the small pieces every tab shares, each a port of a rule in panels.css or
base.css: text at an exact size and weight (``lbl``), the ``.btn`` family (:class:`Btn`), the
segmented control (:class:`Seg`), the toolbar row, the section with a heading (``.ps``), the empty
state, the hairline-ringed card, and :class:`Refresher`, which re-renders a tab on the session's
runtime changes without doing so more than twice a second while Claude streams.

Fonts are set per widget with ``QWidget.setFont`` from ``theme.font_ui``/``font_mono``; colours are
tints (``theme.tint``), so a theme change costs a repaint and no stylesheet is re-parsed.
"""

from __future__ import annotations

import html
import re
import time
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...store import store
from ..ui.bits import Hairline, IconButton  # noqa: F401 - Hairline is re-exported to the panels
from ..ui.button import Btn  # noqa: F401 - re-exported to the panels
from ..ui.signals import bind

TABS = [
    {"id": "changes", "label": "Changes", "icon": "git-compare", "key": "1"},
    {"id": "files", "label": "Files", "icon": "folder-tree", "key": "2"},
    {"id": "terminal", "label": "Terminal", "icon": "square-terminal", "key": "3"},
    {"id": "preview", "label": "Preview", "icon": "app-window", "key": "4"},
    {"id": "tasks", "label": "Tasks", "icon": "list-checks", "key": "5"},
    {"id": "history", "label": "Timeline", "icon": "history", "key": "6"},
    {"id": "info", "label": "Session", "icon": "info", "key": "7"},
]
NARROW_W = 760  # below this only the chosen tab keeps its name (panels.css container query)


# ---------------------------------------------------------------- text


class Text(QLabel):
    """A label whose ``text()`` is always the plain words it was given, even when it renders as rich
    text to space its lines (``line_height``); ``set_html`` takes marked-up content (``<s>``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._plain = ""
        self._rich = False
        self._lh = 1.45

    def set_leading(self, line_height: float) -> None:
        self._rich = True
        self._lh = line_height
        self.setTextFormat(Qt.TextFormat.RichText)

    def setText(self, text: str) -> None:  # noqa: N802
        self._plain = text
        if self._rich:
            super().setText(f'<p style="line-height:{int(self._lh * 100)}%; margin:0">{html.escape(text)}</p>')
        else:
            super().setText(text)

    def set_html(self, inner: str, plain: str = "") -> None:
        self._plain = plain or re.sub(r"<[^>]+>", "", inner)
        super().setText(f'<p style="line-height:{int(self._lh * 100)}%; margin:0">{inner}</p>')

    def text(self) -> str:
        return self._plain


def lbl(
    text: str = "",
    size: float = theme.FS_UI,
    weight: int = 400,
    tone: str = "",
    mono: bool = False,
    wrap: bool = False,
    color: str = "",
    italic: bool = False,
    line_height: float = 0,
    selectable: bool = False,
) -> Text:
    """A label at an exact size and weight. ``tone`` is the app stylesheet's colour name (muted,
    faint, accent, err, ok); ``color`` a token name when the tone list has no such colour. Lines sit
    ``line_height`` × the size apart (the body's 1.45 unless a rule says otherwise): a single line
    gets that height as its minimum, a wrapped one renders as rich text with the same leading."""
    lb = Text()
    f = theme.font_mono(size, weight) if mono else theme.font_ui(size, weight)
    f.setItalic(italic)
    lb.setFont(f)
    if color or tone:
        theme.tint(lb, fg=color or theme.TONES.get(tone, tone))
    lb.setWordWrap(wrap)
    lh = line_height or 1.45
    if wrap:
        lb.set_leading(lh)
    else:
        lb.setTextFormat(Qt.TextFormat.PlainText)
        lb.setMinimumHeight(int(round(size * lh)))
    lb.setText(text)
    lb.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        if (wrap or selectable)
        else Qt.TextInteractionFlag.NoTextInteraction
    )
    return lb


def set_color(lb: QLabel, color: str, base: str = "") -> None:
    """Recolour a ``lbl`` with a token name (``base`` is unused; kept for callers)."""
    theme.tint(lb, fg=color)


def set_rich(lb: QLabel, text: str, line_height: float = 1.45) -> None:
    """Set a wrapped label's words (kept for callers; ``Text.setText`` does the same)."""
    if isinstance(lb, Text):
        lb.setText(text)
    else:
        lb.setText(f'<p style="line-height:{int(line_height * 100)}%; margin:0">{html.escape(text)}</p>')


def elide(lb: QLabel) -> QLabel:
    """Let a label give way to its column instead of widening it (``.ellipsis``)."""
    lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lb.setMinimumWidth(0)
    return lb


# ---------------------------------------------------------------- buttons


def icon_btn(name: str, tooltip: str = "", size: int = 13) -> IconButton:
    """``.icon-btn.sm``: a 24px square with a 13px (or 14px) icon."""
    b = IconButton(name, tooltip, size)
    b.setFixedSize(24, 24)
    return b


class Seg(QWidget):
    """The small segmented control (``.seg.sm`` in ui/bits.tsx): a thumb behind the chosen icon."""

    changed = Signal(str)

    def __init__(self, options: List[Dict[str, str]], value: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.options = options
        self.value = value or (options[0]["value"] if options else "")
        self.disabled: set = set()
        self._hover = -1
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Segmented control")
        self.setFixedSize(2 + 27 * len(options) + 2, 26)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_value(self, v: str) -> None:
        if v != self.value:
            self.value = v
            self.update()

    def set_disabled(self, value: str, disabled: bool, title: str = "") -> None:
        if disabled:
            self.disabled.add(value)
        else:
            self.disabled.discard(value)
        if title:
            for o in self.options:
                if o["value"] == value:
                    o["title"] = title
        self.update()

    def _index_at(self, x: float) -> int:
        i = int((x - 2) // 27)
        return i if 0 <= i < len(self.options) else -1

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = self._index_at(e.position().x())
        if i != self._hover:
            self._hover = i
            self.setToolTip(self.options[i].get("title", "") if i >= 0 else "")
            self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        i = self._index_at(e.position().x())
        if e.button() == Qt.MouseButton.LeftButton and i >= 0 and self.options[i]["value"] not in self.disabled:
            self.changed.emit(self.options[i]["value"])

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.blend(t.hair, t.canvas), 1))
        p.setBrush(theme.blend(t.fill_1, t.canvas))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)
        for i, o in enumerate(self.options):
            r = QRectF(2 + i * 27, 2, 27, 22)
            on = o["value"] == self.value
            off = o["value"] in self.disabled
            p.setOpacity(0.35 if off else 1.0)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.blend(t.fill_3, t.canvas))
                p.drawRoundedRect(r, 6, 6)
            color = t.text if (on or (i == self._hover and not off)) else t.text_3
            pm = icons.pixmap(o["icon"], color, 13)
            p.drawPixmap(int(r.x() + 7), int(r.y() + (22 - 13) / 2), pm)
        p.setOpacity(1.0)


# ---------------------------------------------------------------- shared bits


class Refresher(QObject):
    """Runs ``fn`` when the session's runtime changes (at most once per ``interval`` ms) and at once
    when the file system changes (``Store.bump_fs``). A hidden owner (a tab behind another, a closed
    panel) does no work: it is marked stale and refreshed once when it is shown again."""

    def __init__(
        self,
        owner: QWidget,
        sid: str,
        fn: Callable[[], None],
        interval: int = 500,
        fs: bool = True,
        visible_only: bool = True,
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._sid = sid
        self._fn = fn
        self._interval = interval
        self._visible_only = visible_only
        self._last = 0.0
        self.stale = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.now)
        bind(store.rt_changed, owner, self._on_rt)
        if fs:
            bind(store.fs_changed, owner, lambda _r: self.now())
        if visible_only:
            owner.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self._owner and e.type() == QEvent.Type.Show and self.stale:
            self.now()
        return False

    def _on_rt(self, sid: str) -> None:
        if sid == self._sid:
            self.request()

    def request(self) -> None:
        """Run soon: right away if the last run was long enough ago, else once the interval is up."""
        wait = self._interval / 1000 - (time.monotonic() - self._last)
        if wait <= 0:
            self.now()
        elif not self._timer.isActive():
            self._timer.start(int(wait * 1000) + 1)

    def now(self) -> None:
        self._timer.stop()
        if self._visible_only and not self._owner.isVisible():
            self.stale = True  # caught up when shown
            return
        self.stale = False
        self._last = time.monotonic()
        self._fn()


class Toolbar(QWidget):
    """The one row of borderless controls at the top of a tab (``.panel-toolbar``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QWidget()
        row.setMinimumHeight(42)
        self.box = QHBoxLayout(row)
        self.box.setContentsMargins(12, 6, 10, 6)
        self.box.setSpacing(4)
        outer.addWidget(row)
        outer.addWidget(Hairline())

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.box.addWidget(w, stretch)
        return w

    def stretch(self) -> None:
        self.box.addStretch(1)


class Section(QWidget):
    """A titled block of a scrolling tab (``.ps``): the heading row, then its content, a hairline under."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(0, 0, 0, 0)
        self.head.setSpacing(8)
        self.title = lbl(title, theme.FS_H, 600)
        self.head.addWidget(self.title)
        self.head.addStretch(1)
        lay.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 10, 0, 18)
        self.body.setSpacing(10)
        lay.addLayout(self.body)
        self.line = Hairline()
        lay.addWidget(self.line)

    def add(self, w: QWidget) -> QWidget:
        self.body.addWidget(w)
        return w

    def head_add(self, w: QWidget) -> QWidget:
        self.head.addWidget(w)
        return w

    def head_faint(self, text: str) -> QLabel:
        """The quiet figure at the right of a heading (``.ps-head .faint``)."""
        return self.head_add(lbl(text, theme.FS_SMALL, 400, tone="faint"))

    def set_last(self, last: bool) -> None:
        self.line.setVisible(not last)


def empty_text(text: str) -> QLabel:
    """A section's quiet explanation when there is nothing to show (``.ps-empty``)."""
    return lbl(text, theme.FS_SMALL, 400, tone="faint", wrap=True, line_height=1.5)


class EmptyState(QWidget):
    """An icon over a sentence and a hint, centred, at the top of a tab (``.panel .empty``)."""

    def __init__(self, icon: str, text: str, hint: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = icon
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 40, 28, 40)
        lay.setSpacing(8)
        self.ico = QLabel()
        self.ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ico.setFixedHeight(22)
        self.ico.setVisible(bool(icon))
        lay.addWidget(self.ico)
        self.text = lbl(text, theme.FS_UI, 400, tone="faint", wrap=True, line_height=1.55)
        self.text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.text)
        self.hint = lbl(hint, theme.FS_SMALL, 400, tone="faint", wrap=True, line_height=1.5)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_text(self, text: str, hint: str = "") -> None:
        set_rich(self.text, text, 1.55)
        set_rich(self.hint, hint, 1.5)
        self.hint.setVisible(bool(hint))

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self.ico.setVisible(bool(icon))
        self._paint()

    def _paint(self) -> None:
        if self._icon:
            self.ico.setPixmap(icons.pixmap(self._icon, theme.tokens().text_3, 22))


class Card(QWidget):
    """A surface with a hairline ring (``.task-row``, ``.wfd``, ``.diff-comment``): ``bg`` and ``ring``
    are token names; the ring is drawn inside the edge like the web's inset box-shadow."""

    def __init__(
        self, bg: str = "canvas", ring: str = "hair", radius: int = theme.R3, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.bg, self.ring, self.radius = bg, ring, radius
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.blend(getattr(t, self.ring), getattr(t, self.bg)), 1))
        p.setBrush(theme.qcolor(getattr(t, self.bg)))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), self.radius, self.radius)


def placeholder(text: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.addWidget(lbl(text, theme.FS_UI, tone="faint", wrap=True))
    lay.addStretch(1)
    return w


def rtl_display(s: str) -> str:
    """How ``.cfile-dir`` shows a path: it is laid out right-to-left so the tail stays visible, which
    moves a leading run of punctuation (``./``) to the end — ``./web/src/lib`` reads ``web/src/lib/.``."""
    i = 0
    while i < len(s) and not s[i].isalnum():
        i += 1
    return s[i:] + s[:i][::-1] if 0 < i < len(s) else s


# ---------------------------------------------------------------- tabs


class TabButton(QAbstractButton):
    """One tab: an icon, its name (hidden in a narrow panel unless chosen) and, for Tasks, a count."""

    def __init__(self, spec: Dict[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.tab_id = spec["id"]
        self._on = False
        self._narrow = False
        self._count = 0
        self._hover = False
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{spec['label']} (Alt+{spec['key']})")
        self.setAccessibleName(spec["label"])
        self.setFixedHeight(30)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def shows_label(self) -> bool:
        return self._on or not self._narrow

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self.updateGeometry()
            self.update()

    def set_narrow(self, narrow: bool) -> None:
        if narrow != self._narrow:
            self._narrow = narrow
            self.updateGeometry()
            self.update()

    def set_count(self, n: int) -> None:
        if n != self._count:
            self._count = n
            self.updateGeometry()
            self.update()

    def _font(self) -> QFont:
        return theme.font_ui(theme.FS_UI, 550 if self._on else 400)

    def _pads(self) -> tuple:
        """(left, right) padding: 0 8px, or 0 10px 0 8px for the chosen tab of a narrow panel."""
        return (8, 10) if (self._on and self._narrow) else (8, 8)

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self.shows_label:
            return QSize(34, 30)
        fm = QFontMetrics(self._font())
        pl, pr = self._pads()
        w = pl + 15 + 6 + fm.horizontalAdvance(self.spec["label"]) + pr
        if self._count:
            w += 6 + max(16, QFontMetrics(theme.font_ui(10.5, 600)).horizontalAdvance(str(self._count)) + 8)
        return QSize(w, 30)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover and not self._on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_1, t.canvas))
            p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)
        color = QColor(t.text if (self._on or self._hover) else t.text_3)
        pm = icons.pixmap(self.spec["icon"], color, 15)
        if not self._on:
            p.setOpacity(0.85)  # .panel-tab-ico opacity
        if not self.shows_label:
            p.drawPixmap((self.width() - 15) // 2, (self.height() - 15) // 2, pm)
            return
        x = self._pads()[0]
        p.drawPixmap(x, (self.height() - 15) // 2, pm)
        p.setOpacity(1.0)
        x += 15 + 6
        font = self._font()
        p.setFont(font)
        p.setPen(color)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(self.spec["label"])
        p.drawText(
            QRect(x, 0, tw + 2, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.spec["label"],
        )
        x += tw + 6
        if self._count:
            cf = theme.font_ui(10.5, 600)
            cw = max(16, QFontMetrics(cf).horizontalAdvance(str(self._count)) + 8)
            r = QRect(x, (self.height() - 16) // 2, cw, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_3, t.canvas))
            p.drawRoundedRect(r, 8, 8)
            p.setFont(cf)
            p.setPen(QColor(t.text))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self._count))


class TabStrip(QWidget):
    """The row of tabs with the ink that slides under the chosen one, and the close button."""

    def __init__(self, on_pick: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(theme.HEADER_H)
        self._active = ""
        self._ink: Optional[QRect] = None
        self._anim: Optional[QVariantAnimation] = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(2)
        self.buttons: Dict[str, TabButton] = {}
        for spec in TABS:
            b = TabButton(spec)
            b.clicked.connect(lambda _c=False, tid=spec["id"]: on_pick(tid))
            lay.addWidget(b)
            self.buttons[spec["id"]] = b
        lay.addStretch(1)
        self.close = icon_btn("x", "Close panel", 14)
        self.close.clicked.connect(lambda: store.set_ui(panel=False))
        lay.addWidget(self.close)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def active(self) -> str:
        return self._active

    @property
    def narrow(self) -> bool:
        return self.width() < NARROW_W

    def set_active(self, tab: str) -> None:
        self._active = tab
        for tid, b in self.buttons.items():
            b.set_on(tid == tab)
        QTimer.singleShot(0, self._follow)

    def set_count(self, tab: str, n: int) -> None:
        b = self.buttons.get(tab)
        if b is not None:
            b.set_count(n)

    def resizeEvent(self, e) -> None:  # noqa: N802
        narrow = self.narrow
        for b in self.buttons.values():
            b.set_narrow(narrow)
        super().resizeEvent(e)
        QTimer.singleShot(0, self._follow)

    def _follow(self) -> None:
        b = self.buttons.get(self._active)
        if b is None:
            return
        try:
            target = QRect(b.x(), self.height() - 2, b.width(), 2)
        except RuntimeError:  # the strip was torn down before this deferred call ran
            return
        if self._ink is None or store.reduced_motion or not self.isVisible():
            self._ink = target
            self.update()
            return
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(self._ink)
        anim.setEndValue(target)
        anim.setDuration(theme.T_PANEL)
        anim.setEasingCurve(theme.spring_snappy())
        anim.valueChanged.connect(self._set_ink)
        anim.start()
        self._anim = anim

    def _set_ink(self, r: QRect) -> None:
        self._ink = r
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(QRect(0, self.height() - 1, self.width(), 1), theme.blend(t.hair, t.canvas))
        if self._ink is not None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.text))
            r = self._ink
            # radius 2px 2px 0 0: the rounded rect runs past the bottom edge, where it is clipped square
            p.drawRoundedRect(QRectF(r.x(), r.y(), r.width(), r.height() + 2), 2, 2)


class RightPanel(QWidget):
    """The side panel: tabs, and one page per tab under them. Lives on the canvas colour."""

    def __init__(self) -> None:
        super().__init__()
        self._session: Dict = {}
        self._sid = ""
        self._cwd = ""
        self._pages: Dict[str, QWidget] = {}
        self.setObjectName("rightPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = TabStrip(lambda tid: store.set_ui(panel_tab=tid))
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.stack, 1)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())
        bind(store.ui_changed, self, lambda _u: self._show(store.ui.panel_tab))
        bind(store.rt_changed, self, self._on_rt)

    def _paint(self) -> None:
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        # painted, not styled: a stylesheet on the panel would re-polish every widget under it on each
        # theme switch
        QPainter(self).fillRect(self.rect(), theme.qcolor(theme.tokens().canvas))

    # ---- public
    def set_session(self, session: Dict) -> None:
        sid = str(session.get("id") or "")
        rt = store.rt_of(sid)
        cwd = (rt.cwd if rt else "") or str(session.get("cwd") or "")
        self._session = session
        if sid != self._sid or cwd != self._cwd:
            self._sid, self._cwd = sid, cwd
            for tid, page in list(self._pages.items()):
                self.stack.removeWidget(page)
                page.deleteLater()
                del self._pages[tid]
        else:
            for page in self._pages.values():
                set_session = getattr(page, "set_session", None)
                if set_session:
                    set_session(session)
        self._show(store.ui.panel_tab)
        self._count_running()

    def page(self, tab: str) -> Optional[QWidget]:
        return self._pages.get(tab)

    def reset(self) -> None:
        """Drop every tab's page; the chosen one is rebuilt at once (after a reconnect, or in tests)."""
        for tid, page in list(self._pages.items()):
            self.stack.removeWidget(page)
            page.setParent(None)
            page.deleteLater()
            del self._pages[tid]
        self._show(store.ui.panel_tab)

    # ---- internal
    def _show(self, tab: str) -> None:
        if not self._sid:
            return
        page = self._pages.get(tab)
        if page is None:
            page = self._make(tab)
            self._pages[tab] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.tabs.set_active(tab)

    def _make(self, tab: str) -> QWidget:
        sid, cwd, session = self._sid, self._cwd, self._session
        try:
            if tab == "changes":
                from .changes import ChangesPanel

                return ChangesPanel(sid, cwd)
            if tab == "tasks":
                from .tasks import TasksPanel

                return TasksPanel(sid, cwd)
            if tab == "history":
                from .history import HistoryPanel

                return HistoryPanel(sid, cwd)
            if tab == "info":
                from .info import InfoPanel

                return InfoPanel(sid, cwd)
            if tab == "files":
                from .files import FilesPanel

                w = FilesPanel(sid, cwd)
            elif tab == "terminal":
                from .terminal import TerminalPanel

                w = TerminalPanel(sid, cwd)
            elif tab == "preview":
                from .preview import PreviewPanel

                w = PreviewPanel(sid, cwd)
            else:
                return placeholder(f"No such tab: {tab}")
        except Exception as e:  # noqa: BLE001 — a tab that cannot load must not take the panel down
            print(f"[panel] {tab} is not available: {e!r}")
            return placeholder(f"The {tab} tab is not available in this build.")
        set_session = getattr(w, "set_session", None)
        if set_session:
            set_session(session)
        return w

    def _on_rt(self, sid: str) -> None:
        if sid == self._sid:
            self._count_running()

    def _count_running(self) -> None:
        rt = store.rt_of(self._sid)
        tasks: List[Dict] = list(rt.get("tasks") or []) if rt else []
        self.tabs.set_count("tasks", sum(1 for t in tasks if t.get("status") == "running"))
