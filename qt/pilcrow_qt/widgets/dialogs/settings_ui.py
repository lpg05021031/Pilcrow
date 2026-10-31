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
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
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
from ..ui.bits import Box, IconButton, Pill, hairline, label, paint_box
from ..ui.menu import Menu, Popover, host_for
from ..ui.signals import bind
from .parts import Backdrop, Text, sheet_bg, sheet_line, well_bg, well_line
from .parts import Switch as _Switch

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
    for name in ("fs", "mono"):
        if lb.property(name) is not None:
            out.setProperty(name, lb.property(name))
    out.setStyleSheet(lb.styleSheet())
    out.setFont(lb.font())
    tint = theme.tint_of(lb)
    if tint:
        theme.tint(out, **tint)
    out.setAlignment(lb.alignment())
    lb.deleteLater()
    return out


def line_edit(placeholder: str = "", mono: bool = False, name: str = "") -> QLineEdit:
    """``.input``: the global stylesheet draws it; ``mono`` is ``.input.mono``."""
    e = QLineEdit()
    e.setObjectName("input")
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(False)
    e.setFixedHeight(34)
    e.setAccessibleName(name or placeholder)
    styled(e, lambda t: input_css("#input", t, mono, "0 10px"))
    return e


def input_css(sel: str, t: theme.Tokens, mono: bool = False, padding: str = "0 10px") -> str:
    """``.input`` / ``.textarea``: a quiet fill with a hairline, the page colour and a stronger line on focus."""
    surface = sheet_bg(t).name()
    bg = theme.blend(t.fill_1, surface).name()
    font = (
        f'font-family: "{theme._MONO_FAMILY or "monospace"}"; font-size: 12.5px;'
        if mono
        else f"font-size: {theme.FS_UI}px;"
    )
    return (
        f"{sel} {{ background: {bg}; border: 1px solid {theme.blend(t.hair, bg).name()}; border-radius: {theme.R2}px; padding: {padding}; color: {t.text}; {font} selection-background-color: {theme.blend(t.selection, bg).name()}; }}"
        f" {sel}:hover {{ border-color: {theme.blend(t.hair_2, bg).name()}; }}"
        f" {sel}:focus {{ background: {t.canvas}; border-color: {theme.blend(t.hair_3, t.canvas).name()}; }}"
    )


def text_area(placeholder: str = "", rows: int = 2, mono: bool = False, name: str = "") -> QPlainTextEdit:
    """``.textarea``: a plain text box ``rows`` lines tall that does not grow with its content."""
    t = QPlainTextEdit()
    t.setObjectName("textarea")
    t.setPlaceholderText(placeholder)
    t.setTabChangesFocus(True)
    t.setFixedHeight(int(round(rows * (12.5 if mono else theme.FS_UI) * 1.55)) + 18)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    t.setAccessibleName(name or placeholder)
    styled(t, lambda tk: input_css("#textarea", tk, mono, "6px 8px"))
    return t


class Select(QPushButton):
    """``Select`` (web/src/components/ui/Select.tsx): a ``.select-btn`` that opens the app's own menu in a
    popover. The item API follows QComboBox (``addItem``, ``count``, ``itemText``, ``findData``,
    ``setCurrentIndex``, ``currentData``, ``currentIndexChanged``) so forms can treat it as one.
    ``popup_width`` is the web's ``width`` prop: the list's width when it differs from the button's."""

    currentIndexChanged = Signal(int)  # noqa: N815 — the QComboBox name, so forms treat both alike

    def __init__(
        self, name: str = "", popup_width: int = 0, ghost: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.items: List[Json] = []
        self._index = -1
        self._popup_width = popup_width
        self._ghost = ghost
        self._popover: Optional[Popover] = None
        self.setObjectName("select")
        self.setAutoDefault(False)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(26 if ghost else 34)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed if ghost else QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8 if ghost else 11, 0, 6 if ghost else 10, 0)
        lay.setSpacing(8)
        self.ico = QLabel()
        self.ico.setFixedSize(14, 14)
        self.ico.hide()
        lay.addWidget(self.ico)
        self.label_w = Text("", theme.FS_SMALL if ghost else theme.FS_UI, 600 if ghost else 400, wrap=False)
        self.label_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.label_w, 1)
        self.chev = QLabel()
        self.chev.setFixedSize(13, 13)
        lay.addWidget(self.chev)
        for w in (self.ico, self.label_w, self.chev):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.clicked.connect(self.toggle_popup)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    # ---- the QComboBox-shaped item API
    def addItem(  # noqa: N802
        self, text: str, data: Any = None, hint: str = "", icon: str = "", mono: bool = False
    ) -> None:
        self.items.append({"label": text, "value": data, "hint": hint, "icon": icon, "mono": mono, "disabled": False})
        if self._index < 0:
            self.setCurrentIndex(0)
        elif len(self.items) - 1 == self._index:
            self._show_current()

    def setItemData(self, i: int, value: Any, role: Any = None) -> None:  # noqa: N802
        if 0 <= i < len(self.items):
            self.items[i]["hint"] = str(value or "")
            if i == self._index:
                self._show_current()

    def set_item_enabled(self, i: int, on: bool) -> None:
        if 0 <= i < len(self.items):
            self.items[i]["disabled"] = not on

    def count(self) -> int:
        return len(self.items)

    def itemText(self, i: int) -> str:  # noqa: N802
        return str(self.items[i]["label"]) if 0 <= i < len(self.items) else ""

    def itemData(self, i: int) -> Any:  # noqa: N802
        return self.items[i]["value"] if 0 <= i < len(self.items) else None

    def findData(self, value: Any) -> int:  # noqa: N802
        return next((i for i, it in enumerate(self.items) if it["value"] == value), -1)

    def findText(self, text: str) -> int:  # noqa: N802
        return next((i for i, it in enumerate(self.items) if it["label"] == text), -1)

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        i = i if 0 <= i < len(self.items) else (0 if self.items else -1)
        changed = i != self._index
        self._index = i
        self._show_current()
        if changed:
            self.currentIndexChanged.emit(i)

    def currentData(self) -> Any:  # noqa: N802
        return self.items[self._index]["value"] if 0 <= self._index < len(self.items) else None

    def currentText(self) -> str:  # noqa: N802
        return self.itemText(self._index)

    def text(self) -> str:
        """What the button shows (QPushButton's ``text``): the chosen item's label."""
        return self.currentText()

    def clear(self) -> None:
        self.items = []
        self._index = -1
        self._show_current()

    # ---- looks
    def _show_current(self) -> None:
        it = self.items[self._index] if 0 <= self._index < len(self.items) else None
        self.label_w.setText(str(it["label"]) if it else "Choose…")
        self.label_w.set_color("text" if it else "text_3")
        t = theme.tokens()
        if it and it.get("icon"):
            self.ico.setPixmap(icons.pixmap(str(it["icon"]), t.text_3, 14))
            self.ico.show()
        else:
            self.ico.hide()

    def _paint(self) -> None:
        t = theme.tokens()
        h = 26 if self._ghost else 34  # the web's 32px box inside its 1px line
        css = f"#select {{ background: transparent; border: none; padding: 0; min-height: {h}px; max-height: {h}px; text-align: left; }}"
        if self.styleSheet() != css:
            self.setStyleSheet(css)
        self.chev.setPixmap(icons.pixmap("chevrons-up-down", t.text_3, 13))
        self._show_current()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        surface = sheet_bg(t)
        lit = self.underMouse() or self.property("open") == "true"
        p = QPainter(self)
        if self._ghost:
            bg = theme.over(theme.qcolor(t.fill_2), surface) if lit else None
            paint_box(p, QRectF(self.rect()), bg, None, theme.R2)
            return
        bg = theme.over(theme.qcolor(t.fill_2 if lit else t.fill_1), surface)
        line = theme.over(theme.qcolor(t.hair_2 if lit else t.hair), bg)
        paint_box(p, QRectF(self.rect()), bg, line, theme.R2)

    # ---- the popup
    def showPopup(self) -> None:  # noqa: N802
        if self._popover is not None and self._popover.is_open:
            return
        host = host_for(self)
        if host is None:
            return
        items: List[Json] = []
        for i, it in enumerate(self.items):
            items.append(
                {
                    "label": it["label"],
                    "hint": it.get("hint") or "",
                    "icon": it.get("icon") or "",
                    "mono": bool(it.get("mono")),
                    "checked": i == self._index,
                    "disabled": bool(it.get("disabled")),
                    "on_select": (lambda k=i: self.setCurrentIndex(k)),
                }
            )
        pop = Popover(host, self._popup_width or self.width())
        menu = Menu(items)
        menu.picked.connect(self._picked)
        pop.set_content(menu)
        pop.menu = menu  # type: ignore[attr-defined]
        pop.closed.connect(self._popup_closed)
        self._popover = pop
        pop.open_at(self, "bottom-start")
        set_prop(self, "open", "true")

    def _picked(self, it: Json) -> None:
        pop = self._popover
        fn = it.get("on_select")
        if pop is not None:
            pop.dismiss()
        if callable(fn):
            fn()

    def _popup_closed(self) -> None:
        self._popover = None
        set_prop(self, "open", "false")

    def hidePopup(self) -> None:  # noqa: N802
        if self._popover is not None:
            self._popover.dismiss()

    def toggle_popup(self) -> None:
        if self._popover is not None and self._popover.is_open:
            self.hidePopup()
        else:
            self.showPopup()

    @property
    def popup(self) -> Optional[Popover]:
        return self._popover if self._popover is not None and self._popover.is_open else None

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        pop = self.popup
        if pop is not None:
            m = getattr(pop, "menu", None)
            if e.key() == Qt.Key.Key_Down and m is not None:
                m.step(1)
                return
            if e.key() == Qt.Key.Key_Up and m is not None:
                m.step(-1)
                return
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and m is not None:
                if not m.choose_active():
                    pop.dismiss()
                return
        elif e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.showPopup()
            return
        super().keyPressEvent(e)


def combo(
    options: Sequence[Tuple[str, str]],
    value: str = "",
    hints: Optional[Dict[str, str]] = None,
    name: str = "",
    width: int = 0,
    icons_: Optional[Dict[str, str]] = None,
    popup_width: int = 0,
) -> Select:
    """``Select``: value/label pairs; ``hints`` go under the labels in the list, ``icons_`` before them.
    ``name`` is the accessible name; ``width`` fixes the button, ``popup_width`` the list."""
    c = Select(name, popup_width)
    for v, text in options:
        c.addItem(text, v, hint=(hints or {}).get(v, ""), icon=(icons_ or {}).get(v, ""))
    select_value(c, value)
    if width:
        c.setFixedWidth(width)
        c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return c


def select_value(c: Select, value: str) -> None:
    i = c.findData(value)
    c.setCurrentIndex(i if i >= 0 else 0)


def combo_value(c: Select) -> str:
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
    h, pad, fs, r = (16, 5, 10, 8) if tiny else (24, 9, 11.5, 12)
    if tone == "warn":
        lb = Pill(text, bg="err_wash/raised", radius=r)
        theme.tint(lb, fg="err")
    else:
        lb = Pill(text, bg="fill_1/raised", line="hair/raised", radius=r)
        theme.tint(lb, fg="text_2")
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setContentsMargins(pad, 0, pad, 0)
    lb.setFixedHeight(h)
    lb.setFont(theme.font_ui(fs))
    return lb


def count_chip(n: int) -> QLabel:
    """``.cust-count``: the number beside a group title."""
    lb = Pill(str(n), bg="fill_2/raised", radius=9)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setContentsMargins(5, 0, 5, 0)
    lb.setMinimumWidth(22)
    lb.setFixedHeight(18)
    lb.setFont(theme.font_ui(11))
    theme.tint(lb, fg="text_3")
    return lb


def icon_box(name: str, size: int = 36) -> QLabel:
    """``.cust-ico``: an icon on a small inset tile."""
    lb = Pill(bg="inset", line="hair/inset", radius=9)
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paint(t: theme.Tokens) -> None:
        lb.setPixmap(icons.pixmap(name, t.text_2, 16))

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


class Intro(Box):
    """``.cust-intro``: the inset card that explains a feature before anything exists."""

    def __init__(self, title: str, body: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="inset", radius=12, parent=parent)
        self.setObjectName("intro")
        lay = vbox(self, 8, (20, 18, 20, 18))
        self.title = Text(title, theme.FS_BODY, 600)
        lay.addWidget(self.title)
        self.body = Text(body, theme.FS_UI, 400, "text_2", 1.55)
        self.body.set_max_width(516)  # max-width: 62ch
        lay.addWidget(self.body)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.setContentsMargins(0, 4, 0, 0)
        lay.addLayout(self.actions)
        self.actions.addStretch(1)

    def add_action(self, b: QPushButton) -> QPushButton:
        self.actions.insertWidget(self.actions.count() - 1, b)
        return b


class Panel(Box):
    """``.mem-settings`` / ``.radio-list`` / ``.mem-add``: a rounded fill-1 box with a layout inside."""

    def __init__(
        self,
        radius: int = theme.R3,
        margins: Tuple[int, int, int, int] = (14, 4, 14, 4),
        border: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(bg="fill_1/raised", line="hair/raised" if border else None, radius=radius, parent=parent)
        self.setObjectName("panelbox")
        self.lay = vbox(self, 0, margins)


# ---------------------------------------------------------------- Seg, Switch, RadioRow


class Seg(QWidget):
    """Segmented control (``Seg`` in bits.tsx / ``.seg``): one of a few short choices. The chosen
    segment carries the thumb (fill-3 on dark, raised with a shadow on light)."""

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

        def css(t: theme.Tokens) -> str:
            surface = sheet_bg(t).name()
            bg = theme.blend(t.fill_1, surface)
            on_bg = theme.blend(t.fill_3, surface).name() if t.dark else t.raised
            on_border = "transparent" if t.dark else theme.blend(t.hair, t.raised).name()
            return f"""
            #seg {{ background: {bg.name()}; border: 1px solid {theme.blend(t.hair, bg.name()).name()}; border-radius: 8px; }}
            QPushButton {{ border: 1px solid transparent; background: transparent; color: {t.text_3}; padding: 0 {pad - 1}px; min-height: {h - 2}px; max-height: {h - 2}px; border-radius: 6px; font-size: {theme.FS_SMALL}px; font-weight: 500; }}
            QPushButton:hover {{ color: {t.text}; }}
            QPushButton:checked {{ background: {on_bg}; color: {t.text}; border-color: {on_border}; }}
            QPushButton:disabled {{ color: {t.text_4}; }}
            """

        styled(self, css)
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


class Switch(_Switch):
    """The iOS-style switch (``Switch`` in bits.tsx); ``name`` is the accessible name."""

    def __init__(self, on: bool = False, name: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(on, name, parent)


class RadioRow(QPushButton):
    """One row of a ``.radio-list``: icon, label, hint and a check mark when chosen."""

    def __init__(self, mode: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setAccessibleName(str(mode["label"]))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setObjectName("radio")
        self.setFixedHeight(35)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self.ico = QLabel()
        self.ico.setFixedSize(18, 18)
        self.ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.ico)
        self.lb = QLabel(str(mode["label"]))
        self.lb.setFont(theme.font_ui(theme.FS_UI, 600))
        lay.addWidget(self.lb)
        self.hint = elide(label(str(mode["hint"]), tone="faint", size="small"))
        lay.addWidget(self.hint, 1)
        self.check = QLabel()
        self.check.setFixedSize(16, 16)
        lay.addWidget(self.check)
        danger = mode["id"] == "bypassPermissions"
        self.toggled.connect(lambda _on: self._paint())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._danger = danger
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        on = self.isChecked()
        color = t.err if self._danger else (t.text if on else t.text_3)
        self.ico.setPixmap(icons.pixmap(str(self.mode["icon"]), color, 15))
        theme.tint(self.lb, fg="err" if self._danger else ("text" if on else "text_2"))
        self.check.setPixmap(icons.pixmap("check", t.text, 14) if on else icons.pixmap("check", "transparent", 14))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        if self.isChecked():
            bg = theme.over(theme.qcolor(t.fill_2), well_bg(t))
        elif self.underMouse():
            bg = theme.over(theme.qcolor(t.fill_1), well_bg(t))
        else:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), bg)


class RadioList(Box):
    """``.radio-list``: the stacked rows that pick one permission mode."""

    changed = Signal(str)

    def __init__(self, modes: Sequence[Json], value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg=well_bg, line=well_line, radius=theme.R3, parent=parent)
        self.setObjectName("radiolist")
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
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(24)
        self._font = theme.font_ui(theme.FS_SMALL)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(QFontMetrics(self._font).horizontalAdvance(self.text()) + 20, 24)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        surface = sheet_bg(t)
        on = self.property("on") == "true"
        lit = on or self.underMouse()
        bg = theme.over(theme.qcolor(t.fill_2 if lit else t.fill_1), surface)
        line = theme.over(theme.qcolor(t.hair_3), surface) if on else theme.over(theme.qcolor(t.hair), bg)
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, line, 13)
        p.setFont(self._font)
        p.setPen(QColor(t.text if lit else t.text_2))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


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
        self.box = Select(name, popup_width=320)
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
            self.box.addItem(text, v, hint=h, mono=bool(v))
        self.box.addItem("Custom model ID…", CUSTOM, hint=note)
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

        self.frame = Box(bg=sheet_bg, line=sheet_line, radius=theme.R4, parent=self)
        self.frame.setObjectName("sheet")
        self.frame.setContentsMargins(1, 1, 1, 1)  # inside the painted line, as inside the web's border
        fl = vbox(self.frame, 0)
        self.head = QWidget()
        self.head.setFixedHeight(48)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(22, 18, 16, 6)
        hl.setSpacing(10)
        if title_icon:
            hl.addWidget(icon_label(title_icon, lambda t: t.text_2, 15))
        self.title_lb = Text(title, theme.FS_H, 600, wrap=False)
        hl.addWidget(self.title_lb, 1)
        self.close_btn = IconButton("x", "Close (Esc)", 15)
        self.close_btn.setFixedSize(24, 24)
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
        self.backdrop = Backdrop(self.parentWidget())
        self.show()
        self.raise_()
        self.relayout()
        QTimer.singleShot(0, self._focus_first)

    def _focus_first(self) -> None:
        if self._closing:
            return
        for cls in (QLineEdit, QPlainTextEdit, Select):
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
        bd = getattr(self, "backdrop", None)
        if bd is not None:
            bd.paint(p, self)
        else:
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
    """The sheet open for a store key, if any (one whose window went away without closing it is forgotten)."""
    s = _open.get(key)
    if s is None:
        return None
    try:
        if s._closing or s.isHidden() and not s.isVisibleTo(s.parentWidget()):
            return None
    except RuntimeError:  # the C++ side is gone with its window
        _open.pop(key, None)
        return None
    return s


def close_sheet(key: str) -> None:
    s = open_sheet(key)
    if s is not None:
        s.dismiss(reset=False)
