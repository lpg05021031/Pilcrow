"""The sheet every dialog is built on, and the form pieces the settings pages, the new-session dialog and
the MCP sign-in dialog share (ports of web/src/components/ui/Dialog.tsx and the ``Seg``/``Switch``/
``Select``/``ModelSelect`` controls, with the ``.sheet``, ``.dialog*``, ``.field``, ``.chip`` and
``.cust*`` rules of app.css).

A :class:`Sheet` is a frameless QDialog that lives *inside* the main window as a child widget: it
covers the window with the scrim and holds the ``#sheet`` frame near the top, the way the web
``.scrim`` + ``.dialog`` do. Being a child means ``window.grab()`` includes it and no second OS window
appears. Escape, the scrim and the × close it; closing resets the store key it was opened for.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...store import store
from ..ui.bits import IconButton, hairline, label
from ..ui.signals import bind

Json = Dict[str, Any]

# Composer.tsx MODES: the permission modes with their words and Lucide icons.
MODES: List[Json] = [
    {
        "id": "default",
        "label": "Ask before edits",
        "hint": "Asks before changing files or running commands",
        "icon": "shield-check",
        "tone": "",
    },
    {
        "id": "acceptEdits",
        "label": "Edit automatically",
        "hint": "File edits are applied without asking",
        "icon": "file-pen",
        "tone": "accent",
    },
    {
        "id": "plan",
        "label": "Plan mode",
        "hint": "Explores and proposes a plan; changes nothing",
        "icon": "map",
        "tone": "plan",
    },
    {
        "id": "auto",
        "label": "Auto",
        "hint": "Approves routine actions, asks about risky ones",
        "icon": "zap",
        "tone": "info",
    },
    {
        "id": "bypassPermissions",
        "label": "Bypass permissions",
        "hint": "Runs everything without asking — sandboxes only",
        "icon": "shield-off",
        "tone": "err",
    },
]
EFFORTS: List[Tuple[str, str]] = [
    ("", "Default"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra high"),
    ("max", "Max"),
]


def modes_for(info: Optional[Json]) -> List[Json]:
    """The modes offered: bypass is hidden when running as root (Claude Code refuses it there)."""
    root = bool((info or {}).get("isRoot"))
    return [m for m in MODES if not (m["id"] == "bypassPermissions" and root)]


def home() -> str:
    return str((store.info or {}).get("home") or "")


# ---------------------------------------------------------------- styling helpers


def styled(w: QWidget, css: Callable[[theme.Tokens], str]) -> None:
    """Give ``w`` a stylesheet computed from the tokens, and recompute it when the theme changes."""
    w.setStyleSheet(css(theme.tokens()))
    bind(theme.theme.changed, w, lambda t: w.setStyleSheet(css(t)))


def repolish(w: QWidget) -> None:
    """Re-run the stylesheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value: Any) -> None:
    w.setProperty(name, value)
    repolish(w)


def hint(text: str = "", wrap: bool = True) -> QLabel:
    """``.hint``: small, text-3, wrapping."""
    lb = label(text, tone="faint", size="small", wrap=wrap)
    return lb


def mono_label(text: str = "", tone: str = "faint") -> QLabel:
    lb = label(text, tone=tone, mono=True)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class ElidedLabel(QLabel):
    """A one-line label that gives up width rather than widening its row and ends in … when cut
    (the web ``.ellipsis``). The full text is its tooltip unless another is set."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setWordWrap(False)
        if text:
            self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        if not self.toolTip():
            self.setToolTip(text)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        r = self.contentsRect()
        p.drawText(
            r,
            int(self.alignment()) | Qt.TextFlag.TextSingleLine,
            self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, r.width()),
        )


def elide(lb: QLabel) -> QLabel:
    """``lb``'s text and look on an :class:`ElidedLabel`; the original is discarded."""
    out = ElidedLabel(lb.text())
    for name in ("tone", "size", "mono"):
        if lb.property(name) is not None:
            out.setProperty(name, lb.property(name))
    out.setStyleSheet(lb.styleSheet())
    out.setAlignment(lb.alignment())
    lb.deleteLater()
    return out


def line_edit(placeholder: str = "", mono: bool = False, name: str = "") -> QLineEdit:
    """``.input``: the global stylesheet draws it; ``mono`` is ``.input.mono``."""
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(False)
    if mono:
        e.setFont(theme.font_mono())
    e.setMinimumHeight(32)
    e.setAccessibleName(name or placeholder)
    return e


def text_area(placeholder: str = "", rows: int = 2, mono: bool = False, name: str = "") -> QPlainTextEdit:
    """``.textarea``: a plain text box ``rows`` lines tall that does not grow with its content."""
    t = QPlainTextEdit()
    t.setPlaceholderText(placeholder)
    t.setTabChangesFocus(True)
    if mono:
        t.setFont(theme.font_mono())
    fm = t.fontMetrics()
    t.setFixedHeight(int(fm.lineSpacing() * rows * 1.3) + 22)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    t.setAccessibleName(name or placeholder)
    return t


def combo(
    options: Sequence[Tuple[str, str]],
    value: str = "",
    hints: Optional[Dict[str, str]] = None,
    name: str = "",
    width: int = 0,
) -> QComboBox:
    """``Select``: value/label pairs; ``hints`` become the items' tooltips. ``name`` is the accessible name."""
    c = QComboBox()
    c.setAccessibleName(name)
    c.setMinimumHeight(32)
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    for v, text in options:
        c.addItem(text, v)
        if hints and hints.get(v):
            c.setItemData(c.count() - 1, hints[v], Qt.ItemDataRole.ToolTipRole)
    select_value(c, value)
    if width:
        c.setFixedWidth(width)
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def select_value(c: QComboBox, value: str) -> None:
    i = c.findData(value)
    c.setCurrentIndex(i if i >= 0 else 0)


def combo_value(c: QComboBox) -> str:
    return str(c.currentData() if c.currentData() is not None else "")


def field(label_text: str, w: QWidget, icon: str = "") -> QWidget:
    """``.field``: a small bold label over a control."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(7)
    head = QWidget()
    hl = QHBoxLayout(head)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(5)
    if icon:
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon, theme.tokens().text_2, 11))
        hl.addWidget(ic)
    lb = label(label_text, tone="muted", size="small")
    lb.setStyleSheet("font-weight: 600;")
    hl.addWidget(lb)
    hl.addStretch(1)
    lay.addWidget(head)
    lay.addWidget(w)
    box.field_label = lb  # type: ignore[attr-defined]
    return box


def two(a: QWidget, b: QWidget, spacing: int = 12) -> QWidget:
    """``.ns-two`` / ``.cust-two``: two equal columns."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    lay.addWidget(a, 1)
    lay.addWidget(b, 1)
    return box


def row(*widgets: QWidget, spacing: int = 8, stretch_after: int = -1) -> QWidget:
    """``.row``: widgets side by side; ``stretch_after`` is the index after which a stretch goes."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for i, w in enumerate(widgets):
        lay.addWidget(w)
        if i == stretch_after:
            lay.addStretch(1)
    if stretch_after >= len(widgets):
        lay.addStretch(1)
    return box


def vbox(
    parent: Optional[QWidget] = None, spacing: int = 0, margins: Tuple[int, int, int, int] = (0, 0, 0, 0)
) -> QVBoxLayout:
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def clear_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def chip(text: str, tone: str = "", tiny: bool = False) -> QLabel:
    """``.chip``: a small rounded tag. ``tone="warn"`` is the red one (rejected)."""
    lb = QLabel(text)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    h, pad, fs, r = (16, 5, 10, 8) if tiny else (24, 9, 11.5, 12)

    def css(t: theme.Tokens) -> str:
        if tone == "warn":
            color, bg, line = t.err, theme.blend(t.err_wash, t.raised).name(), "transparent"
        else:
            color, bg, line = t.text_2, theme.blend(t.fill_1, t.raised).name(), theme.blend(t.hair, t.raised).name()
        return f"QLabel {{ color: {color}; background: {bg}; border: 1px solid {line}; border-radius: {r}px; padding: 0 {pad}px; min-height: {h - 2}px; max-height: {h - 2}px; font-size: {fs}px; }}"

    styled(lb, css)
    return lb


def count_chip(n: int) -> QLabel:
    """``.cust-count``: the number beside a group title."""
    lb = QLabel(str(n))
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    styled(
        lb,
        lambda t: (
            f"QLabel {{ color: {t.text_3}; background: {theme.blend(t.fill_2, t.raised).name()}; border-radius: 9px; min-width: 12px; padding: 0 5px; min-height: 18px; max-height: 18px; font-size: 11px; }}"
        ),
    )
    return lb


def icon_box(name: str, size: int = 36) -> QLabel:
    """``.cust-ico``: an icon on a small inset tile."""
    lb = QLabel()
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paint(t: theme.Tokens) -> None:
        lb.setPixmap(icons.pixmap(name, t.text_2, 16))
        lb.setStyleSheet(
            f"QLabel {{ background: {t.inset}; border: 1px solid {theme.blend(t.hair, t.inset).name()}; border-radius: 9px; }}"
        )

    paint(theme.tokens())
    bind(theme.theme.changed, lb, paint)
    return lb


def icon_label(name: str, color_of: Callable[[theme.Tokens], str], size: int = 14) -> QLabel:
    lb = QLabel()
    lb.setFixedSize(size, size)

    def paint(t: theme.Tokens) -> None:
        lb.setPixmap(icons.pixmap(name, color_of(t), size))

    paint(theme.tokens())
    bind(theme.theme.changed, lb, paint)
    return lb


class Intro(QFrame):
    """``.cust-intro``: the inset card that explains a feature before anything exists."""

    def __init__(self, title: str, body: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("intro")
        styled(self, lambda t: f"#intro {{ background: {t.inset}; border-radius: 12px; }}")
        lay = vbox(self, 8, (20, 18, 20, 18))
        self.title = label(title)
        self.title.setStyleSheet(f"font-size: {theme.FS_BODY}px; font-weight: 600;")
        lay.addWidget(self.title)
        self.body = label(body, tone="muted", wrap=True)
        lay.addWidget(self.body)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.setContentsMargins(0, 4, 0, 0)
        lay.addLayout(self.actions)
        self.actions.addStretch(1)

    def add_action(self, b: QPushButton) -> QPushButton:
        self.actions.insertWidget(self.actions.count() - 1, b)
        return b


class Panel(QFrame):
    """``.mem-settings`` / ``.radio-list`` / ``.mem-add``: a rounded fill-1 box with a layout inside."""

    def __init__(
        self,
        radius: int = theme.R3,
        margins: Tuple[int, int, int, int] = (14, 4, 14, 4),
        border: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("panelbox")
        styled(
            self,
            lambda t: (
                f"#panelbox {{ background: {theme.blend(t.fill_1, t.raised).name()}; border-radius: {radius}px; border: 1px solid {theme.blend(t.hair, t.raised).name() if border else 'transparent'}; }}"
            ),
        )
        self.lay = vbox(self, 0, margins)


# ---------------------------------------------------------------- Seg, Switch, RadioRow


class Seg(QWidget):
    """Segmented control (``Seg`` in bits.tsx): one of a few short choices."""

    changed = Signal(str)

    def __init__(
        self,
        options: Sequence[Tuple[str, str]],
        value: str = "",
        small: bool = False,
        name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("seg")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName(name)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: Dict[str, QPushButton] = {}
        for v, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setAccessibleName(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.clicked.connect(lambda _c=False, val=v: self._pick(val))
            lay.addWidget(b)
            self.group.addButton(b)
            self.buttons[v] = b
        h = 22 if small else 24
        pad = 7 if small else 10
        styled(
            self,
            lambda t: (
                f"""
            #seg {{ background: {theme.blend(t.fill_1, t.raised).name()}; border: 1px solid {theme.blend(t.hair, t.raised).name()}; border-radius: 8px; }}
            QPushButton {{ border: none; background: transparent; color: {t.text_3}; padding: 0 {pad}px; min-height: {h}px; max-height: {h}px; border-radius: 6px; font-size: {theme.FS_SMALL}px; font-weight: 500; }}
            QPushButton:hover {{ color: {t.text}; }}
            QPushButton:checked {{ background: {theme.blend(t.fill_3, t.raised).name() if t.dark else t.raised}; color: {t.text}; border: 1px solid {theme.blend(t.hair, t.raised).name() if not t.dark else "transparent"}; }}
            QPushButton:disabled {{ color: {t.text_4}; }}
        """
            ),
        )
        self.set_value(value)

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, v: str) -> None:
        self._value = v
        b = self.buttons.get(v)
        if b is not None:
            b.setChecked(True)

    def _pick(self, v: str) -> None:
        if v == self._value:
            self.buttons[v].setChecked(True)
            return
        self._value = v
        self.changed.emit(v)


class Switch(QAbstractButton):
    """The iOS-style switch (``Switch`` in bits.tsx): 34×20, accent when on."""

    def __init__(self, on: bool = False, name: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(t.accent) if on else theme.blend(t.fill_2, t.raised)
        if not self.isEnabled():
            track.setAlphaF(0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 34, 20), 10, 10)
        if not on:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(theme.blend(t.hair_2, t.raised))
            p.drawRoundedRect(QRectF(0.5, 0.5, 33, 19), 9.5, 9.5)
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent_ink if on else t.text_2))
        x = 16 if on else 2
        p.drawEllipse(QRectF(x, 2, 16, 16))


class RadioRow(QPushButton):
    """One row of a ``.radio-list``: icon, label, hint and a check mark when chosen."""

    def __init__(self, mode: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setAccessibleName(str(mode["label"]))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("radio")
        self.setMinimumHeight(35)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self.ico = QLabel()
        self.ico.setFixedSize(18, 18)
        lay.addWidget(self.ico)
        self.lb = QLabel(str(mode["label"]))
        self.lb.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.lb)
        self.hint = elide(label(str(mode["hint"]), tone="faint", size="small"))
        lay.addWidget(self.hint, 1)
        self.check = QLabel()
        self.check.setFixedSize(16, 16)
        lay.addWidget(self.check)
        danger = mode["id"] == "bypassPermissions"
        styled(
            self,
            lambda t: (
                f"""
            #radio {{ background: transparent; border: none; border-radius: 0; padding: 0; text-align: left; }}
            #radio:hover {{ background: {theme.blend(t.fill_1, t.raised).name()}; }}
            #radio:checked {{ background: {theme.blend(t.fill_2, t.raised).name()}; }}
            #radio QLabel {{ background: transparent; }}
        """
            ),
        )
        self.toggled.connect(lambda _on: self._paint())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._danger = danger
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        on = self.isChecked()
        color = t.err if self._danger else (t.text if on else t.text_3)
        self.ico.setPixmap(icons.pixmap(str(self.mode["icon"]), color, 15))
        self.lb.setStyleSheet(f"font-weight: 600; color: {t.err if self._danger else (t.text if on else t.text_2)};")
        self.check.setPixmap(icons.pixmap("check", t.text, 14) if on else icons.pixmap("check", "transparent", 14))


class RadioList(QFrame):
    """``.radio-list``: the stacked rows that pick one permission mode."""

    changed = Signal(str)

    def __init__(self, modes: Sequence[Json], value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("radiolist")
        styled(
            self,
            lambda t: (
                f"#radiolist {{ background: {theme.blend(t.fill_1, t.raised).name()}; border: 1px solid {theme.blend(t.hair, t.raised).name()}; border-radius: {theme.R3}px; }}"
            ),
        )
        lay = vbox(self, 0, (0, 0, 0, 0))
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.rows: Dict[str, RadioRow] = {}
        self._value = value
        for i, m in enumerate(modes):
            if i:
                lay.addWidget(hairline())
            r = RadioRow(m)
            r.clicked.connect(lambda _c=False, mid=str(m["id"]): self._pick(mid))
            self.group.addButton(r)
            lay.addWidget(r)
            self.rows[str(m["id"])] = r
        if value in self.rows:
            self.rows[value].setChecked(True)
        elif self.rows:
            first = next(iter(self.rows))
            self.rows[first].setChecked(True)
            self._value = first

    @property
    def value(self) -> str:
        return self._value

    def _pick(self, mid: str) -> None:
        self._value = mid
        self.changed.emit(mid)


class FolderChip(QPushButton):
    """``.folder-chip``: a recent folder as a pill; ``on`` marks the chosen one."""

    def __init__(self, text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAutoDefault(False)
        self.setObjectName("fchip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(text)
        styled(
            self,
            lambda t: (
                f"""
            #fchip {{ min-height: 24px; max-height: 24px; padding: 0 11px; border-radius: 13px; font-size: {theme.FS_SMALL}px; color: {t.text_2}; background: {theme.blend(t.fill_1, t.raised).name()}; border: 1px solid {theme.blend(t.hair, t.raised).name()}; }}
            #fchip:hover {{ background: {theme.blend(t.fill_2, t.raised).name()}; color: {t.text}; }}
            #fchip[on="true"] {{ color: {t.text}; background: {theme.blend(t.fill_2, t.raised).name()}; border-color: {theme.blend(t.hair_3, t.raised).name()}; }}
        """
            ),
        )


class Flow(QWidget):
    """``.chips``: widgets laid out left to right, wrapping onto new lines when the row is full."""

    def __init__(self, spacing: int = 6, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[QWidget] = []
        self._spacing = spacing
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add(self, w: QWidget) -> QWidget:
        w.setParent(self)
        w.show()
        self._items.append(w)
        self._relayout()
        return w

    def clear(self) -> None:
        for w in self._items:
            w.deleteLater()
        self._items = []
        self._relayout()

    def resizeEvent(self, e) -> None:  # noqa: N802
        self._relayout()
        super().resizeEvent(e)

    def _relayout(self) -> None:
        width = max(1, self.width())
        x = y = 0
        line_h = 0
        for w in self._items:
            s = w.sizeHint()
            if x and x + s.width() > width:
                x = 0
                y += line_h + self._spacing
                line_h = 0
            w.setGeometry(x, y, s.width(), s.height())
            x += s.width() + self._spacing
            line_h = max(line_h, s.height())
        h = y + line_h if self._items else 0
        if h != self.height():
            self.setFixedHeight(h)
            self.updateGeometry()


def folder_picker(value: str, on_pick: Callable[[str], None]) -> Optional[QWidget]:
    """The in-app folder chooser (widgets/folder_picker.py, written separately) when it exists and takes
    ``(value, on_pick)``; None when it is not there yet, so callers fall back to a path field + QFileDialog."""
    try:
        from ..folder_picker import FolderPicker  # noqa: WPS433 — optional, lands with another module
    except Exception:  # noqa: BLE001
        return None
    for make in (lambda: FolderPicker(value, on_pick), lambda: FolderPicker(value=value, on_pick=on_pick)):
        try:
            w = make()
        except TypeError:
            continue
        except Exception as e:  # noqa: BLE001 — a broken picker must not take the dialog down
            print(f"[dialogs] FolderPicker failed: {e!r}")
            return None
        return w if isinstance(w, QWidget) else None
    return None


# ---------------------------------------------------------------- the model picker

CUSTOM = "__custom"


def gateway_state() -> Tuple[List[Json], bool, Optional[str]]:
    """(models, loading, error) from Store.gateway_models (lib/models.ts useGatewayModels)."""
    g = store.gateway_models
    if g == "loading":
        return [], True, None
    if not isinstance(g, dict):
        return [], False, None
    if g.get("ok"):
        return list(g.get("models") or []), False, None
    return [], False, None if g.get("noCredential") else str(g.get("error") or "Could not list the models")


def model_options(models: List[Json]) -> List[Tuple[str, str, str]]:
    """(value, label, hint) for the pickers: Default, then every model the gateway lists."""
    out = [("", "Default", "Claude Code's configured model")]
    for m in models:
        mid = str(m.get("id") or "")
        name = str(m.get("displayName") or "")
        name = name[7:] if name.lower().startswith("claude ") else name
        out.append((mid, name or mid, mid if name else ""))
    return out


class ModelSelect(QWidget):
    """``ModelSelect``: Default, the gateway's models, or a typed model ID. ``changed(value, typing)``
    fires on every change; ``committed`` when a typed ID is left."""

    changed = Signal(str, bool)
    committed = Signal()

    def __init__(self, value: str = "", name: str = "Model", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = value
        lay = vbox(self, 6)
        self.box = QComboBox()
        self.box.setAccessibleName(name)
        self.box.setMinimumHeight(32)
        self.box.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.box)
        self.edit = line_edit("claude-opus-5-5", mono=True, name=f"{name} ID")
        self.edit.hide()
        lay.addWidget(self.edit)
        self.box.currentIndexChanged.connect(self._on_box)
        self.edit.textEdited.connect(self._on_typed)
        self.edit.editingFinished.connect(self.committed.emit)
        self._fill()
        bind(store.gateway_models_changed, self, lambda _g: self._fill())
        store.load_gateway_models()

    @property
    def value(self) -> str:
        return self._value

    def _fill(self) -> None:
        models, loading, error = gateway_state()
        note = (
            "Asking the gateway for its models…"
            if loading
            else f"Couldn't list the gateway's models: {error}"
            if error
            else "Any model ID the endpoint accepts"
        )
        opts = model_options(models)
        known = any(v == self._value for v, _l, _h in opts)
        self.box.blockSignals(True)
        self.box.clear()
        for v, text, h in opts:
            self.box.addItem(text, v)
            if h:
                self.box.setItemData(self.box.count() - 1, h, Qt.ItemDataRole.ToolTipRole)
        self.box.addItem("Custom model ID…", CUSTOM)
        self.box.setItemData(self.box.count() - 1, note, Qt.ItemDataRole.ToolTipRole)
        if known:
            select_value(self.box, self._value)
            self.edit.hide()
        else:
            select_value(self.box, CUSTOM)
            self.edit.setText(self._value)
            self.edit.show()
        self.box.blockSignals(False)

    def _on_box(self, _i: int) -> None:
        v = combo_value(self.box)
        if v == CUSTOM:
            self.edit.show()
            self.edit.setFocus()
            return
        self.edit.hide()
        self._value = v
        self.changed.emit(v, False)

    def _on_typed(self, text: str) -> None:
        self._value = text
        self.changed.emit(text, True)


# ---------------------------------------------------------------- the sheet


class _ContentScroll(QScrollArea):
    """A scroll area that asks for its content's full height; the sheet caps it."""

    def __init__(self, content: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(content)
        content.installEventFilter(self)

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.widget()
        if w is None:
            return super().sizeHint()
        s = w.sizeHint()
        return QSize(s.width(), s.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        w = self.widget()
        return QSize(w.minimumSizeHint().width() if w is not None else 0, 0)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.widget() and e.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            self.updateGeometry()
        return super().eventFilter(obj, e)


_open: Dict[str, Sheet] = {}


class Sheet(QDialog):
    """A dialog drawn inside the window: scrim + the ``#sheet`` frame (``Dialog`` in ui/Dialog.tsx).

    ``key`` is the Store.dialogs entry it belongs to (``"settings"``, ``"newSession"``, ``"mcpAuth"``)
    or ``""`` for a nested sheet (the add-server form) that has no store key. ``on_submit`` runs on
    Ctrl+Enter. ``body`` is the vertical layout content goes into; ``foot`` the footer row (shown
    once something is added to it).
    """

    closed = Signal()

    def __init__(
        self,
        window: QWidget,
        key: str,
        title: str = "",
        width: int = 560,
        on_submit: Optional[Callable[[], None]] = None,
        body_margins: Tuple[int, int, int, int] = (22, 10, 22, 20),
        title_icon: str = "",
    ) -> None:
        host = (
            window.centralWidget()
            if hasattr(window, "centralWidget") and window.centralWidget() is not None
            else window
        )
        super().__init__(host)
        self.setWindowFlags(Qt.WindowType.Widget)
        # The global ``QDialog { background }`` rule would fill the overlay; the scrim is painted by hand.
        self.setObjectName("overlay")
        self.setStyleSheet("#overlay { background: transparent; }")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title or "Dialog")
        self.key = key
        self.on_submit = on_submit
        self._width = width
        self._closing = False
        self._prev_focus: Optional[QWidget] = None
        self.setGeometry(host.rect())
        host.installEventFilter(self)

        self.frame = QFrame(self)
        self.frame.setObjectName("sheet")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = vbox(self.frame, 0)
        self.head = QWidget()
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(22, 18, 16, 6)
        hl.setSpacing(10)
        if title_icon:
            hl.addWidget(icon_label(title_icon, lambda t: t.text_2, 15))
        self.title_lb = QLabel(title)
        self.title_lb.setStyleSheet(f"font-size: {theme.FS_H}px; font-weight: 600;")
        hl.addWidget(self.title_lb, 1)
        self.close_btn = IconButton("x", "Close (Esc)", 15)
        self.close_btn.clicked.connect(self.dismiss)
        hl.addWidget(self.close_btn)
        fl.addWidget(self.head)
        self.head.setVisible(bool(title))

        self.content = QWidget()
        self.body = vbox(self.content, 18, body_margins)
        self.scroll = _ContentScroll(self.content)
        fl.addWidget(self.scroll, 1)

        self.foot_w = QWidget()
        self.foot = QHBoxLayout(self.foot_w)
        self.foot.setContentsMargins(22, 14, 18, 16)
        self.foot.setSpacing(8)
        self.foot_line = hairline()
        fl.addWidget(self.foot_line)
        fl.addWidget(self.foot_w)
        self.foot_w.hide()
        self.foot_line.hide()

        bind(theme.theme.changed, self, lambda _t: self.update())
        if key:
            bind(store.dialog_changed, self, self._on_dialog_changed)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(0)
        self._relayout_timer.timeout.connect(self.relayout)
        self.frame.installEventFilter(self)

    # ---- building
    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(w, stretch)
        return w

    def add_foot(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.foot.addWidget(w, stretch)
        self.foot_w.show()
        self.foot_line.show()
        return w

    def foot_stretch(self) -> None:
        self.foot.addStretch(1)
        self.foot_w.show()
        self.foot_line.show()

    # ---- showing and closing
    def open(self) -> None:  # noqa: A003
        if self.key:
            old = _open.get(self.key)
            if old is not None and old is not self:
                old.dismiss(reset=False)
            _open[self.key] = self
        app_focus = self.parentWidget().window().focusWidget() if self.parentWidget() is not None else None
        self._prev_focus = app_focus
        self.show()
        self.raise_()
        self.relayout()
        QTimer.singleShot(0, self._focus_first)

    def _focus_first(self) -> None:
        if self._closing:
            return
        for cls in (QLineEdit, QPlainTextEdit, QComboBox):
            for w in self.frame.findChildren(cls):
                if w.isVisible() and w.isEnabled() and w.focusPolicy() != Qt.FocusPolicy.NoFocus:
                    w.setFocus(Qt.FocusReason.TabFocusReason)
                    return
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self, reset: bool = True) -> None:
        """Close the sheet. ``reset`` also clears the store key (the web's ``onClose``)."""
        if self._closing:
            return
        self._closing = True
        if self.key and _open.get(self.key) is self:
            del _open[self.key]
        self.hide()
        self.closed.emit()
        prev = self._prev_focus
        try:
            if prev is not None and prev.isVisible():
                prev.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            pass
        self.deleteLater()
        if reset and self.key and store.dialogs.get(self.key):
            store.set_dialog(self.key, False if self.key == "settings" else None)

    def reject(self) -> None:  # noqa: D102 — QDialog's Escape path
        self.dismiss()

    def accept(self) -> None:  # noqa: D102
        if self.on_submit:
            self.on_submit()

    def _on_dialog_changed(self, key: str, value: Any) -> None:
        if key == self.key and not value:
            self.dismiss(reset=False)

    # ---- geometry
    def relayout(self) -> None:
        host = self.parentWidget()
        if host is None or self._closing:
            return
        self.setGeometry(host.rect())
        w, h = self.width(), self.height()
        fw = min(self._width, max(320, w - 40))
        top = int(h * 0.09)
        max_h = min(int(h * 0.88), 860, h - top - 20)
        self.frame.setFixedWidth(fw)
        self.frame.setMaximumHeight(max(120, max_h))
        self.frame.adjustSize()
        fh = min(self.frame.sizeHint().height(), max(120, max_h))
        self.frame.setGeometry((w - fw) // 2, top, fw, fh)

    def schedule_relayout(self) -> None:
        self._relayout_timer.start()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.parentWidget() and e.type() == QEvent.Type.Resize:
            self.relayout()
        elif obj is self.frame and e.type() == QEvent.Type.LayoutRequest:
            self.schedule_relayout()
        return super().eventFilter(obj, e)

    # ---- painting and input
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), theme.qcolor(theme.tokens().scrim))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.frame.geometry().contains(e.position().toPoint()):
            self.dismiss()
        e.accept()

    def event(self, e: QEvent) -> bool:  # noqa: D102
        if e.type() == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent):
            if e.key() == Qt.Key.Key_Escape or (
                e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.dismiss()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.on_submit:
                self.on_submit()
            e.accept()
            return
        e.accept()


def open_sheet(key: str) -> Optional[Sheet]:
    """The sheet open for a store key, if any."""
    s = _open.get(key)
    return s if s is not None and not s._closing else None


def close_sheet(key: str) -> None:
    s = open_sheet(key)
    if s is not None:
        s.dismiss(reset=False)
