"""The window (a port of web/src/components/App.tsx): the sidebar, the conversation sheet and the
side panel on the chrome-coloured ground, the drag handles between them, the keyboard shortcuts,
the boot screen, the offline bar, dialogs and toasts.

The shell is laid out as app.css draws it: ``.app`` is the chrome ground with an 8px padding on the
right and the bottom; the sidebar stands on it flush with the top; the conversation (``.main``) and
the side panel (``.panel-shell``) are rounded canvas sheets that start 8px below the top with a
hairline round them; a 9px drag handle straddles each seam and takes 1px of the flow."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from .. import icons, theme
from ..store import store
from .sidebar import Sidebar
from .ui.bits import Mark, Row, label
from .ui.signals import bind

TAB_KEYS = {"1": "changes", "2": "files", "3": "terminal", "4": "preview", "5": "tasks", "6": "history", "7": "info"}

GROUND_PAD = 8  # .app { padding: 0 8px 8px 0 }
SESSION_VIEWS_KEPT = 8  # conversations kept built behind the one on screen
SIDEBAR_MIN, SIDEBAR_MAX = 200, 440  # App.tsx resizeSidebar
PANEL_MIN, PANEL_SHARE = 340, 0.62  # App.tsx resizePanel


class MainWindow(QMainWindow):
    def __init__(self, title: str = "Pilcrow") -> None:
        super().__init__()
        self._title = title
        self.setWindowTitle(title)
        self.resize(1480, 940)
        self.setMinimumSize(900, 560)

        central = QWidget()
        central.setObjectName("shell")
        central.setAutoFillBackground(True)
        theme.tint(central, bg="chrome")  # the ground: sidebar and shell are chrome, the sheets canvas
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(central)
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        # Our own title bar where the window manager's would not match the theme (Linux by default;
        # PILCROW_TITLEBAR=custom|native overrides, as does --native-titlebar).
        self.titlebar = None
        if custom_titlebar():
            from .ui.titlebar import EdgeResizer, TitleBar

            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
            self.titlebar = TitleBar(self)
            col.addWidget(self.titlebar)
            EdgeResizer(self)
        self.body = Shell()
        col.addWidget(self.body, 1)
        row = self.body.row

        self.sidebar = Sidebar()
        row.addWidget(self.sidebar)

        self.main = Card("main")
        if self.titlebar is not None:
            self.main.installEventFilter(self)  # the title follows the sheet's left edge
        ml = self.main.body_layout
        self.offline = OfflineBar()
        self.offline.hide()
        ml.addWidget(self.offline)
        self.update_notice = UpdateNotice()
        self.update_notice.hide()
        ml.addWidget(self.update_notice)
        self.pages = QStackedWidget()
        ml.addWidget(self.pages, 1)
        row.addWidget(self.main, 1)

        self.panel_host = Card("panel")
        self.panel_layout = self.panel_host.body_layout
        row.addWidget(self.panel_host)
        self.panel_host.hide()
        # The seams carry no standing line on the web ("the two surfaces meet by tone alone"); these
        # stay for code that still toggles them, and show nothing.
        self.side_line = _no_line(self.body)
        self.panel_line = _no_line(self.body)

        self.side_resizer = Resizer(self.body)
        self.side_resizer.dragged.connect(self._drag_sidebar)
        self.side_resizer.released.connect(lambda: store.set_ui(sidebar_w=self.sidebar.width()))
        self.panel_resizer = Resizer(self.body)
        self.panel_resizer.dragged.connect(self._drag_panel)
        self.panel_resizer.released.connect(lambda: store.set_ui(panel_w=self.panel_host.width()))
        self.body.set_parts(self.sidebar, self.main, self.panel_host, self.side_resizer, self.panel_resizer)

        # pages: welcome / session / overview / inbox (built lazily where heavy)
        from .welcome import Welcome

        self.welcome = Welcome()
        self.pages.addWidget(self.welcome)
        self.session_view: Optional[QWidget] = None
        self._session_views: OrderedDict[str, QWidget] = OrderedDict()
        self.overview: Optional[QWidget] = None
        self.inbox: Optional[QWidget] = None
        self.panel: Optional[QWidget] = None

        self.boot = Boot(self)
        self.boot.raise_()
        self.toasts = None
        self._install_shortcuts()
        from .dialogs import install_overlays

        install_overlays(self)

        store.connected_changed.connect(self._on_connected)
        bind(store.config_changed, self, lambda _c: self._after_boot())
        bind(store.active_changed, self, lambda _a: self._route())
        bind(store.page_changed, self, lambda _p: self._route())
        bind(store.sessions_changed, self, lambda _s: self._route())
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(store.info_changed, self, lambda _i: self.update_notice.refresh())
        store.dialog_changed.connect(self._on_dialog)
        bind(store.toasts_changed, self, lambda _t: self._ensure_toasts())
        store.notify.connect(self._notify)
        bind(
            store.attention,
            self,
            lambda: QTimer.singleShot(0, self.activateWindow) if not self.isActiveWindow() else None,
        )
        self._on_ui()
        self._route()

    # ---- boot and connection
    def _on_connected(self, connected: bool) -> None:
        self.offline.setVisible(not connected and store.ever_connected)
        self.boot.set_connected(connected)

    def _after_boot(self) -> None:
        if store.config is not None:
            self.boot.leave()
        self.update_notice.refresh()

    def show_fatal(self, text: str) -> None:
        self.boot.set_fatal(text)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.main and e.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
            self._place_title()
        return super().eventFilter(obj, e)

    def _place_title(self) -> None:
        # The session header's text starts 21px into the sheet (its 1px border + 20px padding).
        if self.titlebar is not None:
            self.titlebar.set_text_x(self.main.mapTo(self, QPoint(0, 0)).x() + 21)

    def resizeEvent(self, e) -> None:  # noqa: N802
        self.boot.setGeometry(self.centralWidget().rect())
        if self.toasts is not None:
            self.toasts.reposition()
        super().resizeEvent(e)

    # ---- routing
    def _route(self) -> None:
        page = store.page
        session = store.active_session()
        if page == "overview":
            if self.overview is None:
                from .overview import Overview

                self.overview = Overview()
                self.pages.addWidget(self.overview)
            self.pages.setCurrentWidget(self.overview)
        elif page == "inbox":
            if self.inbox is None:
                from .inbox import InboxPage

                self.inbox = InboxPage()
                self.pages.addWidget(self.inbox)
            self.pages.setCurrentWidget(self.inbox)
        elif session is not None:
            from .session_view import SessionView

            sid = str(session.get("id"))
            view = self._session_views.get(sid)
            if view is None:
                view = SessionView(session)
                self.pages.addWidget(view)
                self._session_views[sid] = view
                # the last few conversations stay built, so coming back to one is a page flip
                while len(self._session_views) > SESSION_VIEWS_KEPT:
                    old_sid, old = next(iter(self._session_views.items()))
                    if old is view:
                        break
                    del self._session_views[old_sid]
                    self.pages.removeWidget(old)
                    old.deleteLater()
            else:
                self._session_views.move_to_end(sid)
                view.update_session(session)
            self.session_view = view
            self.pages.setCurrentWidget(view)
        else:
            self.pages.setCurrentWidget(self.welcome)
        self._on_ui()
        self._set_title()

    def _set_title(self) -> None:
        waiting = sum(1 for s in store.sessions if s.get("status") == "requires_action")
        session = store.active_session()
        head = (
            "Control room — "
            if store.page == "overview"
            else "Inbox — "
            if store.page == "inbox"
            else f"{session.get('title')} — "
            if session
            else ""
        )
        self.setWindowTitle(f"{f'({waiting}) ' if waiting else ''}{head}{self._title}")

    def _on_ui(self) -> None:
        ui = store.ui
        self.sidebar.setVisible(ui.sidebar)
        session = store.active_session()
        panel_open = bool(session and store.page == "session" and ui.panel)
        if panel_open and self.panel is None:
            from .panels.right_panel import RightPanel

            self.panel = RightPanel()
            self.panel_layout.addWidget(self.panel)
        if self.panel is not None and session is not None:
            self.panel.set_session(session)
        self.panel_host.setFixedWidth(ui.panel_w)
        self.panel_host.setVisible(panel_open)
        self.body.place()

    # ---- the drag handles (App.tsx resizeSidebar / resizePanel)
    def _drag_sidebar(self, dx: int) -> None:
        w = max(SIDEBAR_MIN, min(SIDEBAR_MAX, self.sidebar.width() + dx))
        self.sidebar.setFixedWidth(w)
        self.body.place()

    def _drag_panel(self, dx: int) -> None:
        w = max(PANEL_MIN, min(int(round(self.width() * PANEL_SHARE)), self.panel_host.width() - dx))
        self.panel_host.setFixedWidth(w)
        self.body.place()

    # ---- dialogs, toasts, notifications
    def _on_dialog(self, key: str, value) -> None:
        from .dialogs import open_dialog

        open_dialog(self, key, value)

    def _ensure_toasts(self) -> None:
        if self.toasts is None:
            from .ui.toasts import Toasts

            self.toasts = Toasts(self.centralWidget())
        self.toasts.refresh()

    def _notify(self, sid: str, title: str, body: str) -> None:
        if (store.config or {}).get("notifications") is False:
            return
        if self.isActiveWindow() and store.active_id == sid:
            return
        from .ui.toasts import system_notify

        system_notify(title, body, lambda: (self.activateWindow(), store.open_session(sid)))

    # ---- shortcuts (App.tsx)
    def _install_shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            s = QShortcut(QKeySequence(keys), self)
            s.setContext(Qt.ShortcutContext.ApplicationShortcut)
            s.activated.connect(fn)

        sc("Ctrl+K", lambda: store.set_dialog("palette", not store.dialogs.get("palette")))
        sc("Ctrl+N", store.quick_new_session)
        sc("Ctrl+Shift+N", lambda: store.set_dialog("newSession", {}))
        sc("Ctrl+B", lambda: store.set_ui(sidebar=not store.ui.sidebar))
        sc("Ctrl+O", store.cycle_view)
        sc("Ctrl+=", lambda: store.zoom(1))
        sc("Ctrl++", lambda: store.zoom(1))
        sc("Ctrl+-", lambda: store.zoom(-1))
        sc("Ctrl+0", lambda: store.zoom(0))
        sc("Ctrl+Shift+O", lambda: store.show_page("session" if store.page == "overview" else "overview"))
        sc("Ctrl+\\", lambda: store.set_ui(panel=not store.ui.panel))
        sc("Ctrl+,", lambda: store.set_dialog("settings", True))
        sc("Ctrl+Q", self._quit)
        sc("Ctrl+/", lambda: store.set_dialog("shortcuts", True))
        sc("Ctrl+F", self._find)
        for k, tab in TAB_KEYS.items():
            sc(f"Alt+{k}", lambda t=tab: self._toggle_tab(t))
        sc("Ctrl+Alt+Up", lambda: self._step_session(-1))
        sc("Ctrl+Alt+Down", lambda: self._step_session(1))
        esc = QShortcut(QKeySequence("Escape"), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self._escape)

    def _toggle_tab(self, tab: str) -> None:
        if not store.active_id:
            return
        ui = store.ui
        if ui.panel and ui.panel_tab == tab:
            store.set_ui(panel=False)
        else:
            store.set_ui(panel=True, panel_tab=tab)

    def _step_session(self, d: int) -> None:
        lst = [s for s in store.sessions if not s.get("archived")]
        ids = [s.get("id") for s in lst]
        try:
            i = ids.index(store.active_id)
        except ValueError:
            i = -1 if d > 0 else 0
        j = max(0, min(len(ids) - 1, i + d))
        if ids and ids[j]:
            store.open_session(ids[j])

    def _find(self) -> None:
        if self.session_view is not None and store.page == "session":
            self.session_view.open_find()

    def _escape(self) -> None:
        if any(store.dialogs.values()):
            return
        sid = store.active_id
        r = store.rt_of(sid)
        fw = self.focusWidget()
        typing = fw is not None and fw.metaObject().className() in (
            "QLineEdit",
            "QTextEdit",
            "QPlainTextEdit",
            "ComposerEdit",
        )
        if sid and r is not None and not typing and r.status in ("running", "starting"):
            store.interrupt(sid)

    def _quit(self) -> None:
        store.call("app.quit").then(lambda _r: self.close()).catch(lambda _e: self.close())


def custom_titlebar() -> bool:
    import os
    import sys

    pref = os.environ.get("PILCROW_TITLEBAR", "").lower()
    if pref in ("custom", "native"):
        return pref == "custom"
    return sys.platform.startswith("linux")


def _no_line(parent: QWidget) -> QWidget:
    w = QWidget(parent)
    w.setFixedSize(0, 0)
    w.hide()
    return w


# ---------------------------------------------------------------- the ground, the sheets, the seams


class Shell(QWidget):
    """``.app``: the chrome ground the sidebar and the sheets stand on. Lays the three columns out
    with a 1px seam between them (the flow width of a ``.resizer``), keeps the drag handles astride
    the seams, and paints the sheets' soft shadow on the ground around them."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, GROUND_PAD, GROUND_PAD)
        self.row.setSpacing(1)
        self.sidebar: Optional[QWidget] = None
        self.main: Optional[QWidget] = None
        self.panel: Optional[QWidget] = None
        self.side_resizer: Optional[Resizer] = None
        self.panel_resizer: Optional[Resizer] = None
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_parts(self, sidebar: QWidget, main: QWidget, panel: QWidget, side: Resizer, right: Resizer) -> None:
        self.sidebar, self.main, self.panel, self.side_resizer, self.panel_resizer = sidebar, main, panel, side, right

    def place(self) -> None:
        """Put the handles over the seams (after any change of what is shown or how wide it is)."""
        if self.main is None:
            return
        side_open = self.sidebar is not None and not self.sidebar.isHidden()
        # .app.no-side > .main { margin-left: 8px }: the sheet keeps its margin when the sidebar is hidden
        self.row.setContentsMargins(0 if side_open else GROUND_PAD, 0, GROUND_PAD, GROUND_PAD)
        self.row.activate()
        h = self.height() - GROUND_PAD
        if self.side_resizer is not None:
            self.side_resizer.setVisible(side_open)
            if side_open:
                seam = self.sidebar.geometry().right() + 1
                self.side_resizer.setGeometry(seam - Resizer.WIDTH // 2, 0, Resizer.WIDTH, h)
                self.side_resizer.raise_()
        if self.panel_resizer is not None:
            panel_open = self.panel is not None and not self.panel.isHidden()
            self.panel_resizer.setVisible(panel_open)
            if panel_open:
                seam = self.main.geometry().right() + 1
                self.panel_resizer.setGeometry(seam - Resizer.WIDTH // 2, 0, Resizer.WIDTH, h)
                self.panel_resizer.raise_()
        self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.place()

    def paintEvent(self, _e) -> None:  # noqa: N802
        # box-shadow 0 1px 2px: black at 18% on dark, 4% on light, blurred over the ground
        t = theme.tokens()
        strength = 0.18 if t.dark else 0.04
        p = QPainter(self)
        for card in (self.main, self.panel):
            if card is None or card.isHidden():
                continue
            r = card.geometry().adjusted(0, Card.TOP, 0, 0)
            for d, k in ((1, 0.75), (2, 0.36), (3, 0.17)):
                c = QColor(0, 0, 0, int(255 * strength * k))
                p.fillRect(QRect(r.left() + 1, r.bottom() + d, r.width() - 2, 1), c)
            for d, k in ((1, 0.36), (2, 0.17)):
                c = QColor(0, 0, 0, int(255 * strength * k))
                p.fillRect(QRect(r.left() - d, r.top() + 1, 1, r.height()), c)
                p.fillRect(QRect(r.right() + d, r.top() + 1, 1, r.height()), c)
            p.fillRect(QRect(r.left() + 1, r.top() - 1, r.width() - 2, 1), QColor(0, 0, 0, int(255 * strength * 0.17)))


class Card(QWidget):
    """``.main`` / ``.panel-shell``: a canvas sheet with 12px corners and a hairline, resting 8px
    below the top of the ground. The children lay out under the 8px band; a frame on top of them
    paints the ground back into the corners (``overflow: hidden``) and draws the hairline."""

    TOP = 8  # margin-top: 8px
    RADIUS = 12

    def __init__(self, name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(0, self.TOP, 0, 0)
        self.body_layout.setSpacing(0)
        self._frame = _CardFrame(self)
        bind(theme.theme.changed, self, lambda _t: (self.update(), self._frame.update()))

    def sheet_rect(self) -> QRect:
        return self.rect().adjusted(0, self.TOP, 0, 0)

    def childEvent(self, e) -> None:  # noqa: N802
        super().childEvent(e)
        frame = getattr(self, "_frame", None)
        if frame is not None and e.type() == QEvent.Type.ChildAdded and e.child() is not frame:
            frame.raise_()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._frame.setGeometry(self.rect())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.tokens().canvas))
        p.drawRoundedRect(QRectF(self.sheet_rect()), self.RADIUS, self.RADIUS)


class _CardFrame(QWidget):
    """The corners, the hairline and the dark theme's one-pixel light along the top edge, painted
    over whatever the children drew."""

    def __init__(self, card: Card) -> None:
        super().__init__(card)
        self._card = card
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        r = self._card.sheet_rect()
        radius = Card.RADIUS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        sheet = QPainterPath()
        sheet.addRoundedRect(QRectF(r), radius, radius)
        p.fillPath(outside.subtracted(sheet), theme.qcolor(t.chrome))
        # border: 0.5px solid var(--hair) renders as one pixel of the hairline over the canvas
        p.setPen(QPen(theme.blend(t.hair, t.canvas), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        if t.dark:
            # box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035)
            p.setPen(QPen(QColor(255, 255, 255, 9), 1))
            y = r.top() + 1.5
            p.drawLine(QPointF(r.left() + radius * 0.6, y), QPointF(r.right() + 1 - radius * 0.6, y))


class Resizer(QWidget):
    """``.resizer``: a 9px column astride a seam. Nothing shows until the pointer has rested on it
    for a moment; then a short handle, which turns to the accent while it is being dragged."""

    WIDTH = 9
    dragged = Signal(int)  # pointer x delta since the last move
    released = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setMouseTracking(True)
        self._shown = 0.0
        self._last_x = 0.0
        self.dragging = False
        self._anim: Optional[QVariantAnimation] = None
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(120)  # transition-delay: 120ms
        self._delay.timeout.connect(lambda: self._fade(1.0))
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- showing the handle
    def _fade(self, to: float) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if store.reduced_motion or to == self._shown:
            self._set_shown(to)
            return
        a = QVariantAnimation(self)
        a.setDuration(theme.T_MED)
        a.setStartValue(self._shown)
        a.setEndValue(to)
        a.setEasingCurve(theme.ease())
        a.valueChanged.connect(self._set_shown)
        a.start()
        self._anim = a

    def _set_shown(self, v: float) -> None:
        self._shown = float(v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._delay.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._delay.stop()
        if not self.dragging:
            self._fade(0.0)
        super().leaveEvent(e)

    # ---- dragging
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._last_x = e.globalPosition().x()
            self._delay.stop()
            self._set_shown(1.0)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.dragging:
            x = e.globalPosition().x()
            dx = int(round(x - self._last_x))
            if dx:
                self._last_x = x
                self.dragged.emit(dx)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self.dragging and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.released.emit()
            if not self.underMouse():
                self._fade(0.0)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._shown <= 0:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.qcolor(t.accent) if self.dragging else theme.qcolor(t.hair_3)
        c.setAlphaF(c.alphaF() * self._shown)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        # 3 x 40px, 2px corners, at left: 3px; scaleY from 0.6 as it appears
        h = 40 * (0.6 + 0.4 * self._shown)
        p.drawRoundedRect(QRectF(3, self.height() / 2 - h / 2, 3, h), 1.5, 1.5)


# ---------------------------------------------------------------- bars and the boot screen


class _ElideLabel(QLabel):
    """A label a layout may squeeze: its text is elided to the room it gets (OfflineBar._fit), so its
    full text must not become the sheet's minimum width."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())


class OfflineBar(QWidget):
    """``.offline``: the line across the top of the sheet while the background process is away."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("offline")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(8)
        lay.addStretch(1)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.text = _ElideLabel()
        self.text.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.text, fg="text_2")
        lay.addWidget(self.text)
        lay.addStretch(1)  # centred, like the web's justify-content: center
        self._full = ""
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self.set_text("Lost the connection to Pilcrow's background process. Reconnecting…")

    def set_text(self, text: str) -> None:
        self._full = text
        self._fit()

    def _fit(self) -> None:
        # One centred line; a notice wider than the bar is elided rather than wrapped or cut.
        room = self.width() - 14 * 2 - 14 - 8
        fm = QFontMetrics(self.text.font())
        self.text.setText(
            fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(40, room)) if room > 0 else self._full
        )

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap("wifi-off", t.text_2, 14))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), theme.blend(t.fill_1, t.canvas))
        p.fillRect(0, self.height() - 1, self.width(), 1, theme.blend(t.hair, t.canvas))


class UpdateNotice(OfflineBar):
    """After an update the window and the background process can briefly be different versions."""

    def refresh(self) -> None:
        server = (store.info or {}).get("version")
        if not store.connected or not server:
            self.hide()
            return
        from .. import __version__

        if server == __version__:
            self.hide()
            return
        newer = _compare_versions(str(server), __version__) > 0
        self.set_text(
            f"Pilcrow {server} is installed. Quit Pilcrow and open it again to use it."
            if newer
            else f"Pilcrow {__version__} is installed, but its background process is still {server}. Quit Pilcrow and open it again to finish updating."
        )
        self.ico.setPixmap(icons.pixmap("refresh-cw", theme.tokens().text_2, 14))
        self.show()


def _compare_versions(a: str, b: str) -> int:
    pa = [int(x) if x.isdigit() else 0 for x in a.split(".")]
    pb = [int(x) if x.isdigit() else 0 for x in b.split(".")]
    for i in range(max(len(pa), len(pb))):
        x, y = (pa[i] if i < len(pa) else 0), (pb[i] if i < len(pb) else 0)
        if x != y:
            return x - y
    return 0


class Boot(QWidget):
    """The opening: the mark arrives, the name settles in under it; while the process is still starting
    the bowl breathes; once connected and configured, the whole thing lifts away."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._connected = False
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(14)
        stage = QWidget()
        sl = QVBoxLayout(stage)
        sl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.setSpacing(10)
        from .ui.bits import WorkingMark

        self.mark = WorkingMark(44)
        sl.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)
        self.name = label("Pilcrow")
        self.name.setFont(theme.font_ui(theme.FS_TITLE, 600))
        sl.addWidget(self.name, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(stage)
        self.line = label("Connecting")
        self.line.setFont(theme.font_ui(theme.FS_SMALL))
        self.setAutoFillBackground(True)
        self.line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.line.setWordWrap(True)
        self.line.setMaximumWidth(460)
        lay.addWidget(self.line, 0, Qt.AlignmentFlag.AlignHCenter)
        self._slow = QTimer(self)
        self._slow.setSingleShot(True)
        self._slow.setInterval(5000)
        self._slow.timeout.connect(
            lambda: (
                not self._connected
                and self.line.setText("Starting the background process — the first launch takes a moment")
            )
        )
        self._slow.start()
        self._slower = QTimer(self)
        self._slower.setSingleShot(True)
        self._slower.setInterval(16000)
        self._slower.timeout.connect(
            lambda: (
                not self._connected
                and self.line.setText(
                    "Still trying to reach the background process. If this keeps up, quit Pilcrow and open it again."
                )
            )
        )
        self._slower.start()
        self._fatal = False
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        theme.tint(self.line, fg="err" if self._fatal else "text_2")

    def set_connected(self, on: bool) -> None:
        self._connected = on
        if on:
            self.line.setText("Loading your sessions")

    def set_fatal(self, text: str) -> None:
        self.line.setText(text)
        self._fatal = True
        self._paint()

    def leave(self) -> None:
        if not self.isVisible():
            return
        if store.reduced_motion:
            self.hide()
            return
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(320)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(theme.ease_exit())
        anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim


_ = (QEvent, Mark, Row)
