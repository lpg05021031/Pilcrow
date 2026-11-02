"""The window (a port of web/src/components/App.tsx): the sidebar, the conversation sheet and the
side panel on the chrome-coloured ground, the drag handles between them, the keyboard shortcuts,
the boot screen, the offline bar, dialogs and toasts.

The shell is laid out as app.css draws it: ``.app`` is the chrome ground with an 8px padding on the
right and the bottom; the sidebar stands on it flush with the top; the conversation (``.main``) and
the side panel (``.panel-shell``) are rounded canvas sheets that start 8px below the top with a
hairline round them; a 9px drag handle straddles each seam and takes 1px of the flow."""

from __future__ import annotations

import os
import sys
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QPen, QRegion, QShortcut
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..canvas.clock import Tween
from ..lib.shortcuts import SHORTCUTS
from ..store import PANEL_TABS, store
from .dialogs.parts import ElideLabel
from .sidebar import Sidebar
from .ui import tooltip
from .ui.bits import label
from .ui.signals import bind

GROUND_PAD = 8  # .app { padding: 0 8px 8px 0 }
SESSION_VIEWS_KEPT = 8  # conversations kept built behind the one on screen
Shadow = Tuple[int, int, int, int, float]  # a line of a sheet's shadow: x, y, w, h, its share of the strength
SIDEBAR_MIN, SIDEBAR_MAX = 200, 440  # App.tsx resizeSidebar
PANEL_MIN, PANEL_SHARE = 340, 0.62  # App.tsx resizePanel


class MainWindow(QMainWindow):
    def __init__(self, title: str = "Pilcrow") -> None:
        super().__init__()
        self._title = title
        self.setWindowTitle(title)
        tooltip.install()  # painted tips with rounded corners, not the platform's square window
        # 1480×940 where it fits; smaller on a small screen (a 1366×768 laptop, a 125 % 1080p display), so the
        # panel and the composer are on screen from the first frame rather than after a resize
        self.resize(*fit_size())
        self.setMinimumSize(900, 560)

        rounded = custom_titlebar()
        central = Ground(self, rounded)  # the ground: sidebar and shell are chrome, the sheets canvas
        central.setObjectName("shell")
        if rounded:
            # our own frame has rounded top corners: the window is see-through outside the ground
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
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
        restore_geometry(self)

        # pages: welcome / session / overview / inbox (built lazily where heavy)
        from .welcome import Welcome

        self.welcome = Welcome()
        self.pages.addWidget(self.welcome)
        self.session_view: Optional[QWidget] = None
        self._session_views: OrderedDict[str, QWidget] = OrderedDict()
        self.overview: Optional[QWidget] = None
        self.inbox: Optional[QWidget] = None
        self.dispatch: Optional[QWidget] = None
        self.panel: Optional[QWidget] = None
        # a closed panel is built, or brought to the session on screen, once the window is idle
        self._idle_panel = QTimer(self)
        self._idle_panel.setSingleShot(True)
        self._idle_panel.setInterval(700)
        self._idle_panel.timeout.connect(self._prebuild_panel)

        self.boot = Boot(self)
        self.boot.raise_()
        self.toasts = None
        self._install_shortcuts()
        from .dialogs import install_overlays

        install_overlays(self)

        bind(store.connected_changed, self, self._on_connected)
        bind(store.config_changed, self, lambda _c: self._after_boot())
        bind(store.active_changed, self, lambda _a: self._route())
        bind(store.page_changed, self, lambda _p: self._route())
        bind(store.sessions_changed, self, lambda _s: self._route())
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(store.info_changed, self, lambda _i: self.update_notice.refresh())
        bind(store.dialog_changed, self, self._on_dialog)
        bind(store.toasts_changed, self, lambda _t: self._ensure_toasts())
        bind(store.notify, self, self._notify)
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

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.Type.WindowStateChange:
            self.centralWidget().update()  # the ground's corners square off when maximised
        super().changeEvent(e)

    def resizeEvent(self, e) -> None:
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
        elif page == "dispatch":
            if self.dispatch is None:
                from .dispatch.page import DispatchPage

                self.dispatch = DispatchPage()
                self.pages.addWidget(self.dispatch)
            self.pages.setCurrentWidget(self.dispatch)
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
            else "Dispatch — "
            if store.page == "dispatch"
            else f"{session.get('title')} — "
            if session
            else ""
        )
        self.setWindowTitle(f"{f'({waiting}) ' if waiting else ''}{head}{self._title}")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_framed", False):
            self._framed = True
            apply_windows_frame(self)
            bind(theme.theme.changed, self, lambda _t: apply_windows_frame(self))

    def closeEvent(self, e) -> None:
        save_geometry(self)
        super().closeEvent(e)

    def _on_ui(self) -> None:
        ui = store.ui
        self.sidebar.setVisible(ui.sidebar)
        session = store.active_session()
        panel_open = bool(session and store.page == "session" and ui.panel)
        if panel_open and self.panel is None:
            from .panels.right_panel import RightPanel

            self.panel = RightPanel()
            self.panel_layout.addWidget(self.panel)
        if panel_open:
            self._idle_panel.stop()
            self.panel.set_session(session)
        elif session is not None and store.page == "session":
            # closed: its tab follows the session when the window is idle, not on the switch itself,
            # so the first open is a flip, and a run of switches builds nothing
            self._idle_panel.start()
        self.panel_host.setFixedWidth(ui.panel_w)
        self.panel_host.setVisible(panel_open)
        self.body.place()

    def _prebuild_panel(self) -> None:
        session = store.active_session()
        if session is None or store.page != "session" or self.panel_host.isVisible():
            return
        if self.panel is not None:
            self.panel.set_session(session)
            return
        from .panels.right_panel import RightPanel

        self.panel = RightPanel()
        self.panel_layout.addWidget(self.panel)
        self.panel.set_session(session)
        QTimer.singleShot(0, self, self._warm_panel)

    def _warm_panel(self) -> None:
        """The prebuilt panel laid out and painted once off screen at the size it will open at, so
        the first open finds its layouts activated, its widgets polished and the font caches warm:
        a paint of what is there rather than the construction of it. Then, one per turn of the event
        loop, the syntax lexers the session's edits will need, so the Changes tab's first card does
        not compile one."""
        panel = self.panel
        if panel is None or not shiboken6.isValid(panel):
            return
        # A grab lays a hidden widget out as if it were shown, and the window's row would make room
        # for the closed panel's sheet: the conversation narrowed and widened again (two reflows,
        # two whole repaints) on every warm-up. The row is held still while the panel is grabbed.
        row = self.body.row
        row.setEnabled(False)
        try:
            panel.resize(max(PANEL_MIN, self.panel_host.width()), max(1, self.height()))
            panel.grab()
        finally:
            row.setEnabled(True)
        from .panels.changes import lexer_warmer

        step = lexer_warmer(store.active_id or "")

        def warm() -> None:
            if step():
                QTimer.singleShot(0, self, warm)

        QTimer.singleShot(0, self, warm)

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
        """The window's keys, from the one table (``lib/shortcuts``): each entry this window runs gets a
        QShortcut per key sequence, told which of them fired (Alt+1 … 7, the two arrows)."""
        run: Dict[str, Callable[[int], None]] = {
            "palette": lambda _i: store.set_dialog("palette", not store.dialogs.get("palette")),
            "newSession": lambda _i: store.quick_new_session(),
            "newSessionWith": lambda _i: store.set_dialog("newSession", {}),
            "find": lambda _i: self._find(),
            "sidebar": lambda _i: store.set_ui(sidebar=not store.ui.sidebar),
            "panel": lambda _i: store.set_ui(panel=not store.ui.panel),
            "view": lambda _i: store.cycle_view(),
            "tabs": lambda i: self._toggle_tab(PANEL_TABS[i]),
            "overview": lambda _i: store.show_page("session" if store.page == "overview" else "overview"),
            "dispatch": lambda _i: (
                store.show_page("session") if store.page == "dispatch" else store.show_dispatch(None)
            ),
            "nextSession": lambda i: self._step_session(1 if i else -1),
            "zoom": lambda i: store.zoom((1, 1, -1, 0)[i]),
            "stop": lambda _i: self._escape(),
            "settings": lambda _i: store.set_dialog("settings", True),
            "shortcuts": lambda _i: store.set_dialog("shortcuts", True),
            "quit": lambda _i: self._quit(),
        }
        for name, fn in run.items():
            for i, keys in enumerate(SHORTCUTS[name].sequences):
                s = QShortcut(QKeySequence(keys), self)
                # Esc stops Claude only in this window (a dialog's own Esc closes the dialog)
                s.setContext(
                    Qt.ShortcutContext.WindowShortcut if name == "stop" else Qt.ShortcutContext.ApplicationShortcut
                )
                s.activated.connect(lambda fn=fn, i=i: fn(i))

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


def fit_size(want_w: int = 1480, want_h: int = 940) -> Tuple[int, int]:
    """The default window size, clamped to the primary screen's available area with a margin."""

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return want_w, want_h
    a = screen.availableGeometry()
    return min(want_w, max(900, a.width() - 48)), min(want_h, max(560, a.height() - 48))


def _settings():
    """QSettings when the application has a name (the real app); tests run without persistence."""

    if not QCoreApplication.organizationName():
        return None
    return QSettings()


def restore_geometry(win: QMainWindow) -> None:
    """Where the window was last time, when that still lands on a screen (Qt checks); else the fit."""
    st = _settings()
    if st is None:
        return
    g = st.value("window/geometry")
    if g is not None:
        try:
            win.restoreGeometry(g)
        except Exception:
            pass


def save_geometry(win: QMainWindow) -> None:
    st = _settings()
    if st is not None:
        st.setValue("window/geometry", win.saveGeometry())


def apply_windows_frame(win: QMainWindow) -> None:
    """Windows: the native title bar in the theme's colours (dark mode, caption, border, text) and
    rounded corners on Windows 11 — through DWM, like the WebView2 shell does. A no-op elsewhere."""

    if sys.platform != "win32":
        return
    try:
        import ctypes  # Windows only

        hwnd = int(win.winId())
        dwm = ctypes.windll.dwmapi
        t = theme.tokens()

        def put(attr: int, value: int) -> int:
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v), 4)

        def colorref(hex_color: str) -> int:
            c = QColor(hex_color)
            return c.red() | (c.green() << 8) | (c.blue() << 16)

        if put(20, 1 if t.dark else 0) != 0:  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on older Windows 10)
            put(19, 1 if t.dark else 0)
        put(35, colorref(t.chrome))  # DWMWA_CAPTION_COLOR (Windows 11)
        put(34, colorref(t.chrome))  # DWMWA_BORDER_COLOR
        put(36, colorref(t.text))  # DWMWA_TEXT_COLOR
        put(33, 2)  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND (Windows 11; ignored before)
    except Exception:
        pass


def custom_titlebar() -> bool:
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


class Ground(QWidget):
    """The window's ground, in chrome. With our own frame (``rounded``) the window is translucent and
    the ground is painted in its shape: the top corners rounded unless the window is maximised (the
    title bar paints the same shape over its strip); otherwise it is a plain fill."""

    def __init__(self, win: QWidget, rounded: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._win = win
        self._rounded = rounded
        if not rounded:
            self.setAutoFillBackground(True)
            theme.tint(self, bg="chrome")
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        if not self._rounded:
            return
        from .ui.titlebar import corner_radius, rounded_top

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(rounded_top(self.width(), self.height(), corner_radius(self._win)), QColor(theme.tokens().chrome))


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
        self._shadows_at: Dict[str, List[Shadow]] = {}  # each sheet's shadow as last placed
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
            side_x = self.sidebar.geometry().right() + 1 if side_open else None
            self._put_handle(self.side_resizer, side_x, h)
        if self.panel_resizer is not None:
            panel_open = self.panel is not None and not self.panel.isHidden()
            self._put_handle(self.panel_resizer, self.main.geometry().right() + 1 if panel_open else None, h)
        # the sheets repaint themselves when they move or resize; on the ground only the lines of
        # shadow that moved are repainted (a full update repainted every widget in the window)
        for card in (self.main, self.panel):
            if card is None:
                continue
            now = self._shadows(card)
            for x, y, w, h, _a in set(now).symmetric_difference(self._shadows_at.get(card.objectName(), [])):
                self.update(x, y, w, h)
            self._shadows_at[card.objectName()] = now

    @staticmethod
    def _put_handle(handle: Resizer, seam: Optional[int], h: int) -> None:
        """A handle astride the seam at ``seam`` (hidden when None), above the sheets. Raised only
        when it is shown: raising repaints what lies under it."""
        if seam is None:
            handle.hide()
            return
        handle.setGeometry(seam - Resizer.WIDTH // 2, 0, Resizer.WIDTH, h)
        if handle.isHidden():
            handle.show()
            handle.raise_()

    @staticmethod
    def _shadows(card: QWidget) -> List[Shadow]:
        """A sheet's shadow on the ground (box-shadow 0 1px 2px): three lines under it, two either
        side, each (x, y, w, h, strength)."""
        if card.isHidden():
            return []
        r = card.geometry().adjusted(0, Card.TOP, 0, 0)
        out = [(r.left() + 1, r.bottom() + d, r.width() - 2, 1, k) for d, k in ((1, 0.75), (2, 0.36), (3, 0.17))]
        for d, k in ((1, 0.36), (2, 0.17)):
            out += [(r.left() - d, r.top() + 1, 1, r.height(), k), (r.right() + d, r.top() + 1, 1, r.height(), k)]
        return out

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.place()

    def paintEvent(self, e) -> None:
        # black at 18% on dark, 4% on light, blurred over the ground
        strength = 0.18 if theme.tokens().dark else 0.04
        p = QPainter(self)
        region = e.region()
        for card in (self.main, self.panel):
            for x, y, w, h, k in self._shadows(card) if card is not None else []:
                if region.intersects(QRect(x, y, w, h)):
                    p.fillRect(QRect(x, y, w, h), QColor(0, 0, 0, int(255 * strength * k)))


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
        # it paints every pixel of itself (the ground in the band above the sheet, then the sheet), so
        # a repaint inside it does not repaint the ground under it first
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        bind(theme.theme.changed, self, lambda _t: (self.update(), self._frame.update()))

    def sheet_rect(self) -> QRect:
        return self.rect().adjusted(0, self.TOP, 0, 0)

    def childEvent(self, e) -> None:
        super().childEvent(e)
        frame = getattr(self, "_frame", None)
        if frame is not None and e.type() == QEvent.Type.ChildAdded and e.child() is not frame:
            frame.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._frame.setGeometry(self.rect())
        self._frame.setMask(self._frame_region())

    def _frame_region(self) -> QRegion:
        """What the frame draws on: the band above the sheet with its top line and the light under
        it, the corners, the side and bottom lines. Nothing else: Qt repaints the whole of a scrolled
        rect that a sibling overlaps, and the conversation blits its scroll (CanvasView.scrollContentsBy)
        only because the frame lies over nothing of it but the margins it leaves out."""
        r, rad, w = self.sheet_rect(), self.RADIUS, self.width()
        rg = QRegion(0, 0, w, r.top() + 3) + QRegion(0, r.bottom(), w, 1)
        rg += QRegion(r.left(), r.top(), 1, r.height()) + QRegion(r.right(), r.top(), 1, r.height())
        for x in (r.left(), r.right() + 1 - rad):
            for y in (r.top(), r.bottom() + 1 - rad):
                rg += QRegion(x, y, rad, rad)
        return rg

    def paintEvent(self, e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(e.rect(), theme.qcolor(t.canvas))
        # the band above the sheet and its corners, antialiased; the rest is the plain canvas
        sheet = self.sheet_rect()
        edges = QRegion(self.rect()) - QRegion(sheet.adjusted(0, self.RADIUS, 0, -self.RADIUS))
        edges = (edges - QRegion(sheet.adjusted(self.RADIUS, 0, -self.RADIUS, 0))) & e.region()
        if edges.isEmpty():
            return
        p.setClipRegion(edges)
        p.fillRect(edges.boundingRect(), theme.qcolor(t.chrome))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.canvas))
        p.drawRoundedRect(QRectF(sheet), self.RADIUS, self.RADIUS)


class _CardFrame(QWidget):
    """The corners, the hairline and the dark theme's one-pixel light along the top edge, painted
    over whatever the children drew."""

    def __init__(self, card: Card) -> None:
        super().__init__(card)
        self._card = card
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, e) -> None:
        t = theme.tokens()
        r = self._card.sheet_rect()
        radius = Card.RADIUS
        # what it draws lies along the edges: a repaint inside them (a breathing mark, a streamed
        # line) has nothing here to draw over
        if e.region().subtracted(QRegion(r.adjusted(radius, radius, -radius, -radius))).isEmpty():
            return
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
        self.fade_t = Tween(self._set_shown, ms=theme.T_MED, curve=theme.ease)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(120)  # transition-delay: 120ms
        self._delay.timeout.connect(lambda: self._fade(1.0))
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- showing the handle
    def _fade(self, to: float) -> None:
        self.fade_t.go(to)

    def _set_shown(self, v: float) -> None:
        self._shown = float(v)
        self.update()

    def enterEvent(self, e) -> None:
        self._delay.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._delay.stop()
        if not self.dragging:
            self._fade(0.0)
        super().leaveEvent(e)

    # ---- dragging
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._last_x = e.globalPosition().x()
            self._delay.stop()
            self.fade_t.jump(1.0)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            x = e.globalPosition().x()
            dx = int(round(x - self._last_x))
            if dx:
                self._last_x = x
                self.dragged.emit(dx)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.released.emit()
            if not self.underMouse():
                self._fade(0.0)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:
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
        # one centred line: a notice wider than the bar ends in … rather than wrapping or widening it
        self.text = ElideLabel()
        self.text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.text.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.text, fg="text_2")
        lay.addWidget(self.text)
        lay.addStretch(1)  # centred, like the web's justify-content: center
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self.set_text("Lost the connection to Pilcrow's background process. Reconnecting…")

    def set_text(self, text: str) -> None:
        self.text.setText(text)

    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap("wifi-off", t.text_2, 14))
        self.update()

    def paintEvent(self, _e) -> None:
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

        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._fade_t = Tween(eff.setOpacity, self.hide, ms=320, curve=theme.ease_exit)
        self._fade_t.jump(1.0)
        self._fade_t.go(0.0)
