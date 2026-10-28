"""The window (a port of web/src/components/App.tsx): sidebar | main column | side panel, the
keyboard shortcuts, the boot screen, the offline bar, dialogs and toasts."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from .. import icons, theme
from ..store import store
from .sidebar import Sidebar
from .ui.bits import Mark, Row, label
from .ui.signals import bind

TAB_KEYS = {"1": "changes", "2": "files", "3": "terminal", "4": "preview", "5": "tasks", "6": "history", "7": "info"}


class MainWindow(QMainWindow):
    def __init__(self, title: str = "Pilcrow") -> None:
        super().__init__()
        self._title = title
        self.setWindowTitle(title)
        self.resize(1480, 940)
        self.setMinimumSize(900, 560)

        central = QWidget()
        central.setObjectName("canvas")
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = Sidebar()
        row.addWidget(self.sidebar)
        self.side_line = _vline()
        row.addWidget(self.side_line)

        main = QWidget()
        main.setObjectName("main")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.offline = OfflineBar()
        self.offline.hide()
        ml.addWidget(self.offline)
        self.update_notice = UpdateNotice()
        self.update_notice.hide()
        ml.addWidget(self.update_notice)
        self.pages = QStackedWidget()
        ml.addWidget(self.pages, 1)
        row.addWidget(main, 1)

        self.panel_line = _vline()
        row.addWidget(self.panel_line)
        self.panel_host = QWidget()
        self.panel_host.setObjectName("panel")
        self.panel_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.panel_layout = QVBoxLayout(self.panel_host)
        self.panel_layout.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.panel_host)
        self.panel_host.hide()
        self.panel_line.hide()

        # pages: welcome / session / overview / inbox (built lazily where heavy)
        from .welcome import Welcome

        self.welcome = Welcome()
        self.pages.addWidget(self.welcome)
        self.session_view: Optional[QWidget] = None
        self.overview: Optional[QWidget] = None
        self.inbox: Optional[QWidget] = None
        self.panel: Optional[QWidget] = None

        self.boot = Boot(self)
        self.boot.raise_()
        self.toasts = None
        self._install_shortcuts()

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

            if self.session_view is None or getattr(self.session_view, "sid", None) != session.get("id"):
                old = self.session_view
                self.session_view = SessionView(session)
                self.pages.addWidget(self.session_view)
                if old is not None:
                    self.pages.removeWidget(old)
                    old.deleteLater()
            else:
                self.session_view.update_session(session)
            self.pages.setCurrentWidget(self.session_view)
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
        self.side_line.setVisible(ui.sidebar)
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
        self.panel_line.setVisible(panel_open)

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


def _vline() -> QWidget:
    from .ui.bits import hairline

    return hairline(vertical=True)


class OfflineBar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("offline")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 16, 6)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.text = QLabel("Lost the connection to Pilcrow's background process. Reconnecting…")
        lay.addWidget(self.text, 1)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap("wifi-off", t.warn, 14))
        self.setStyleSheet(
            f"#offline {{ background: {theme.blend(t.warn_wash, t.canvas).name()}; border-bottom: 1px solid {theme.blend(t.hair, t.canvas).name()}; }} QLabel {{ color: {t.text}; font-size: {theme.FS_SMALL}px; }}"
        )


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
        self.text.setText(
            f"Pilcrow {server} is installed. Quit Pilcrow and open it again to use it."
            if newer
            else f"Pilcrow {__version__} is installed, but its background process is still {server}. Quit Pilcrow and open it again to finish updating."
        )
        self.ico.setPixmap(icons.pixmap("refresh-cw", theme.tokens().info, 14))
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
        self.name = label("Pilcrow", size="title")
        sl.addWidget(self.name, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(stage)
        self.line = label("Connecting", tone="muted", size="small")
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
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(f"Boot {{ background: {t.canvas}; }}")

    def set_connected(self, on: bool) -> None:
        self._connected = on
        if on:
            self.line.setText("Loading your sessions")

    def set_fatal(self, text: str) -> None:
        self.line.setText(text)
        self.line.setProperty("tone", "err")
        self.line.style().unpolish(self.line)
        self.line.style().polish(self.line)

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
