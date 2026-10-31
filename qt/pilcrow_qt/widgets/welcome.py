"""The welcome page (a port of web/src/components/Welcome.tsx): shown when no session is open.

The app's own icon sits in a soft orange glow over a greeting for the time of day; under it a
composer box starts a session in the chosen folder with the first message, recent folders are
chips, and two key hints close the page. Everything is laid out on the web's own measures: the
column is the golden section of the pane (``--col``), the box 640px wide, the icon 72px.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .. import icons, theme
from ..lib.fmt import basename, tildify
from ..store import store
from .dialogs.parts import Keys, LayoutButton, Text, page_column
from .ui.bits import Box, IconButton, Spinner, paint_box
from .ui.signals import bind

ICON = 72
BOX_W = 640


def greeting(name: Optional[str], hour: Optional[int] = None) -> str:
    """ "Working late, Test Person": the part of the day, then the person's name."""
    h = datetime.now().hour if hour is None else hour
    part = "Working late" if h < 5 else "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
    if not name:
        return part
    # An OS login like "tester" reads better as a name; leave real names ("Lin Wei-Ting") alone.
    import re

    shown = name[0].upper() + name[1:] if re.match(r"^[a-z][a-z0-9._-]*$", name) else name
    return f"{part}, {shown}"


def column(width: int) -> int:
    """``--col`` in the welcome: the pane less its 24px padding."""
    return page_column(width, 24)


class AppIcon(QWidget):
    """The app icon as a tile (``AppIcon`` in ui/bits.tsx): the mark people see in the taskbar."""

    def __init__(self, size: int = ICON, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        s = self._size / 256.0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # The icon's own colours (the SVG's): the dark chrome, Claude's orange, the light chrome.
        p.setBrush(QColor(theme.DARK.chrome))
        p.drawRoundedRect(QRectF(0, 0, self._size, self._size), 60 * s, 60 * s)
        p.setBrush(QColor(t.accent))
        p.drawEllipse(QPointF(105 * s, 98 * s), 43.5 * s, 43.5 * s)
        p.setBrush(QColor(theme.LIGHT.chrome))
        for x, y, w, h in ((105, 55, 69, 20.5), (123.5, 55, 20.5, 146), (153, 55, 20.5, 146)):
            p.drawRoundedRect(QRectF(x * s, y * s, w * s, h * s), 4.5 * s, 4.5 * s)


class FolderButton(LayoutButton):
    """``.cbtn``: the folder chooser in the composer bar: icon, the folder's name, a small chevron."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("cbtn")
        self.setAutoDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.ico = QWidget()
        self.ico.setFixedSize(14, 14)
        lay.addWidget(self.ico)
        self.label = Text("Choose a folder", theme.FS_SMALL, 500, "text_2", wrap=False)
        lay.addWidget(self.label)
        self.chev = QWidget()
        self.chev.setFixedSize(12, 12)
        lay.addSpacing(-2)
        lay.addWidget(self.chev)
        for w in (self.ico, self.label, self.chev):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_look(hover="fill_2/raised", radius=theme.R2)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_folder(self, name: str) -> None:
        self.label.setText(name or "Choose a folder")

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        t = theme.tokens()
        hover = self.underMouse()
        p = QPainter(self)
        p.drawPixmap(self.ico.x(), self.ico.y(), icons.pixmap("folder", t.text if hover else t.text_2, 14))
        p.setOpacity(0.55)
        p.drawPixmap(self.chev.x(), self.chev.y(), icons.pixmap("chevron-down", t.text if hover else t.text_2, 12))
        self.label.set_color("text" if hover else "text_2")

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)


class SendButton(QPushButton):
    """``.send``: a 32px circle; a quiet ring until there is text, then Claude's orange."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAutoDefault(False)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Start (Enter)")
        self.setAccessibleName("Start")
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")
        self._ready = False
        self._busy = False
        self.spinner = Spinner(14, self)
        self.spinner.move(9, 9)
        self.spinner.hide()
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, ready: bool, busy: bool) -> None:
        self._ready, self._busy = ready, busy
        self.spinner.setVisible(busy)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, 31, 31)
        if self._ready:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_2 if self.underMouse() and self.isEnabled() else t.accent))
            p.drawEllipse(QRectF(0, 0, 32, 32))
            ink = t.accent_ink
        else:
            p.setPen(theme.blend(t.hair_2, t.raised))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
            ink = t.text_3
        if not self._busy:
            p.drawPixmap(8, 8, icons.pixmap("arrow-up", ink, 16, stroke=2.25))

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)


class WelcomeChip(QPushButton):
    """``.folder-chip``: a recent folder as a 26px pill; ``on`` marks the chosen one."""

    def __init__(self, text: str, tooltip: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("wchip")
        self.setAutoDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(text)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(24)
        self._on = False
        self._font = theme.font_ui(theme.FS_SMALL)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_on(self, on: bool) -> None:
        self._on = on
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(QFontMetrics(self._font).horizontalAdvance(self.text()) + 20, 24)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        lit = self._on or self.underMouse()
        bg = theme.blend(t.fill_2 if lit else t.fill_1, t.canvas)
        line = theme.blend(t.hair_3 if self._on else t.hair, bg.name())
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, line, 13)
        p.setFont(self._font)
        p.setPen(QColor(t.text if lit else t.text_2))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class WelcomeBox(QFrame):
    """``.composer.welcome-composer``: the box that starts a session. ``submitted`` carries the text."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("wbox")
        self._focus = False
        self.setFixedWidth(BOX_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("winput")
        self.edit.setPlaceholderText("Describe a task, ask about the code, or paste an error…")
        self.edit.setAccessibleName("Describe a task")
        self.edit.setFrameShape(QFrame.Shape.NoFrame)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.setFixedHeight(74)
        self.edit.setTabChangesFocus(False)
        self.edit.setStyleSheet(
            f"#winput {{ background: transparent; border: none; padding: 10px 12px 0 12px; font-size: {theme.FS_BODY}px; }}"
        )
        theme.tint(self.edit, fg="text", placeholder="text_3", highlight="selection/raised")
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 8)
        bl.setSpacing(2)
        self.folder_btn = FolderButton()
        bl.addWidget(self.folder_btn)
        bl.addStretch(1)
        bl.addSpacing(2)
        self.send = SendButton()
        bl.addWidget(self.send)
        lay.addWidget(bar)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        line = theme.blend(t.hair_3 if self._focus else t.hair_2, t.raised)
        paint_box(p, QRectF(self.rect()), QColor(t.raised), line, theme.R4)

    def text(self) -> str:
        return self.edit.toPlainText()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.edit:
            if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self.send.click()
                    return True
            elif e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self._focus = e.type() == QEvent.Type.FocusIn
                self.update()
        return super().eventFilter(obj, e)


class Welcome(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.picked = ""
        self.busy = False
        self.recents: List[str] = []
        self.chips: List[WelcomeChip] = []

        self.side_btn = IconButton("panel-left-open", "Show sidebar (Ctrl+B)", 16, parent=self)
        self.side_btn.setFixedSize(28, 28)
        self.side_btn.move(12, 10)
        self.side_btn.clicked.connect(lambda: store.set_ui(sidebar=True))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 40, 24, 94)
        outer.setSpacing(0)
        outer.addStretch(1)
        self.inner = QWidget()
        self.inner.setFixedWidth(BOX_W)
        il = QVBoxLayout(self.inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        il.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.icon = AppIcon()
        il.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        il.addSpacing(22)
        self.title = Text("", 26, 600, "text", wrap=False, align=Qt.AlignmentFlag.AlignHCenter, letter_spacing=-0.024)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        il.addWidget(self.title)
        il.addSpacing(8)
        sub = QWidget()
        sl = QHBoxLayout(sub)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addStretch(1)
        self.sub_a = Text("", theme.FS_BODY, 400, "text_3", wrap=False)
        self.sub_b = Text("", theme.FS_BODY, 600, "text_2", wrap=False)
        self.sub_c = Text("", theme.FS_BODY, 400, "text_3", wrap=False)
        for w in (self.sub_a, self.sub_b, self.sub_c):
            sl.addWidget(w)
        sl.addStretch(1)
        il.addWidget(sub)
        il.addSpacing(26)
        self.notice_host = QWidget()
        self.notice_lay = QVBoxLayout(self.notice_host)
        self.notice_lay.setContentsMargins(0, 0, 0, 0)
        self.notice_lay.setSpacing(14)
        self.notice_host.hide()
        il.addWidget(self.notice_host)
        self.box = WelcomeBox()
        self.box.send.clicked.connect(self.go)
        self.box.edit.textChanged.connect(self._sync_send)
        self.box.folder_btn.clicked.connect(
            lambda: store.set_dialog("newSession", {"cwd": self.cwd, "prompt": self.box.text()})
        )
        il.addWidget(self.box, 0, Qt.AlignmentFlag.AlignHCenter)
        self.chips_w = QWidget()
        self.chips_lay = QHBoxLayout(self.chips_w)
        self.chips_lay.setContentsMargins(0, 14, 0, 0)
        self.chips_lay.setSpacing(6)
        self.chips_lay.addStretch(1)
        self.chips_lay.addStretch(1)
        il.addWidget(self.chips_w)
        keys = QWidget()
        kl = QHBoxLayout(keys)
        kl.setContentsMargins(0, 30, 0, 0)
        kl.setSpacing(22)
        kl.addStretch(1)
        kl.addWidget(self._key_hint("Ctrl", "N", "new session with options"))
        kl.addWidget(self._key_hint("Ctrl", "K", "commands and search"))
        kl.addStretch(1)
        il.addWidget(keys)
        outer.addWidget(self.inner, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        bind(store.config_changed, self, lambda _c: self._refresh())
        bind(store.sessions_changed, self, lambda _s: self._refresh())
        bind(store.info_changed, self, lambda _i: self._refresh())
        bind(store.ui_changed, self, lambda _u: self.side_btn.setVisible(not store.ui.sidebar))
        bind(store.connected_changed, self, lambda _on: self._sync_send())
        bind(theme.theme.changed, self, lambda _t: self.update())
        self.side_btn.setVisible(not store.ui.sidebar)
        self._refresh()

    # ---- pieces
    @staticmethod
    def _key_hint(a: str, b: str, what: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(Keys(a))
        lay.addWidget(Keys(b))
        lay.addWidget(Text(what, theme.FS_SMALL, 400, "text_3", wrap=False))
        return w

    @property
    def cwd(self) -> str:
        return self.picked or (self.recents[0] if self.recents else "")

    def _recents(self) -> List[str]:
        out: List[str] = []
        for p in (store.config or {}).get("recentProjects") or []:
            if p and str(p) not in out:
                out.append(str(p))
        for s in store.sessions:
            if len(out) >= 8:
                break
            p = str(s.get("project") or s.get("cwd") or "")
            if p and not s.get("archived") and p not in out:
                out.append(p)
        return out[:6]

    # ---- state
    def _refresh(self) -> None:
        info = store.info or {}
        self.recents = self._recents()
        self.title.setText(greeting(str(info.get("displayName") or info.get("user") or "")))
        cwd = self.cwd
        if cwd:
            self.sub_a.setText("What should Claude work on in ")
            self.sub_b.setText(basename(cwd))
            self.sub_c.setText("?")
        else:
            self.sub_a.setText("Pick a folder and tell Claude what to work on.")
            self.sub_b.setText("")
            self.sub_c.setText("")
        home = info.get("home")
        self.box.folder_btn.set_folder(basename(cwd) if cwd else "")
        self.box.folder_btn.setToolTip(tildify(cwd, home) if cwd else "Choose a folder")
        # recent folders as chips, when there is a choice to make
        for ch in self.chips:
            ch.deleteLater()
        self.chips = []
        if len(self.recents) > 1:
            for r in self.recents:
                ch = WelcomeChip(basename(r), tildify(r, home))
                ch.set_on(r == cwd)
                ch.clicked.connect(lambda _c=False, path=r: self.pick(path))
                self.chips_lay.insertWidget(self.chips_lay.count() - 1, ch)
                self.chips.append(ch)
        self.chips_w.setVisible(len(self.recents) > 1)
        self._notices(info)
        self._sync_send()

    def _notices(self, info: dict) -> None:
        while self.notice_lay.count():
            w = self.notice_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        claude = info.get("claude") or {}
        gateway = info.get("gateway") or {}
        shown = False
        if info and not claude.get("path"):
            self.notice_lay.addWidget(
                _notice(
                    "Claude Code was not found. Install it so `claude` is on your PATH, or reinstall Pilcrow with its bundled copy. Settings → Diagnostics has details."
                )
            )
            shown = True
        if (
            claude.get("path")
            and gateway
            and not gateway.get("error")
            and not gateway.get("hasToken")
            and not gateway.get("loggedIn")
        ):
            from .dialogs.settings_claude import SetupCard

            self.notice_lay.addWidget(SetupCard())
            shown = True
        self.notice_host.setVisible(shown)

    def pick(self, path: str) -> None:
        self.picked = path
        self._refresh()

    def _sync_send(self) -> None:
        ready = bool(self.box.text().strip())
        self.box.send.set_state(ready, self.busy)
        self.box.send.setEnabled(ready and not self.busy and store.connected)

    def go(self) -> None:
        prompt = self.box.text()
        if not prompt.strip() or self.busy:
            return
        cwd = self.cwd
        if not cwd:
            store.set_dialog("newSession", {"prompt": prompt})
            return
        self.busy = True
        self._sync_send()
        c = store.config or {}

        def done(_x=None) -> None:
            self.busy = False
            self._sync_send()

        store.create_session(
            cwd, mode=c.get("defaultMode"), model=c.get("defaultModel"), effort=c.get("defaultEffort"), prompt=prompt
        ).then(lambda _r: (self.box.edit.setPlainText(""), done())).catch(done)

    # ---- geometry and the glow
    def resizeEvent(self, e) -> None:  # noqa: N802
        col = column(self.width())
        self.inner.setFixedWidth(col)
        self.box.setFixedWidth(max(640, min(int(round(col * 0.618)), col - 48, 1080)) if col > 1035 else BOX_W)
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(24, 40, 24, int(round(self.height() * 0.10)))
        super().resizeEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        # [data-theme='dark'] .welcome-icon { filter: drop-shadow(0 18px 44px rgba(217,119,87,.22)) }
        t = theme.tokens()
        if not t.dark or not self.icon.isVisible():
            return
        c = self.icon.mapTo(self, self.icon.rect().center())
        cx, cy = c.x() + 0.5, c.y() + 18.5
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(cx, cy), 120)
        glow = QColor(t.accent)
        for stop, a in ((0.0, 0.22), (0.3, 0.14), (0.6, 0.05), (1.0, 0.0)):
            glow.setAlphaF(a)
            g.setColorAt(stop, QColor(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), 120, 120)
        p.drawPath(path)


def _notice(text: str) -> QFrame:
    """``.notice.error``: a line with a warning icon on a red wash."""
    t = theme.tokens()
    f = Box(bg="err_wash/canvas", radius=theme.R3)
    f.setObjectName("notice")
    lay = QHBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(10)
    ico = QWidget()
    ico.setFixedSize(14, 14)
    lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
    ico.paintEvent = lambda _e, w=ico: QPainter(w).drawPixmap(0, 0, icons.pixmap("circle-alert", t.err, 14))  # type: ignore[method-assign]
    lay.addWidget(Text(text, theme.FS_UI, 400, "text", 1.45))
    return f
