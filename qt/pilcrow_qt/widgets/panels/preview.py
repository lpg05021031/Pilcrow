"""The Preview panel (a port of web/src/components/panels/PreviewPanel.tsx).

The web interface frames the dev server in an iframe. This client has no web engine, so the panel
keeps the address bar and the shortcuts to the localhost addresses Claude's commands printed, and
opens the page in the system browser; the body says so and shows whether something is listening at
the address.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ... import icons, theme
from ...store import store
from ..ui.bits import IconButton, Row, button, hairline, label
from ..ui.signals import bind

Json = Dict[str, Any]

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:/[^\s'\"<>)]*)?")
DEFAULT_URL = "http://localhost:3000"

_urls: Dict[str, str] = {}  # session id → the address last opened


def find_urls(session_id: str) -> List[str]:
    """Localhost addresses printed by the session's Bash and PowerShell steps, newest first (at most 6)."""
    m = store.models.get(session_id)
    if m is None:
        return []
    found: Dict[str, None] = {}
    for t in m.tools.values():
        if t.name not in ("Bash", "PowerShell"):
            continue
        s = t.structured if isinstance(t.structured, dict) else {}
        text = f"{s.get('stdout') or ''}\n{s.get('stderr') or ''}\n{t.result.text if t.result else ''}"
        for u in URL_RE.findall(text):
            found[re.sub(r"[.,;]+$", "", u.replace("0.0.0.0", "localhost"))] = None
    return list(found)[-6:][::-1]


class Chip(QPushButton):
    """A detected address as a shortcut (``.chip.mono``)."""

    def __init__(self, url: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(re.sub(r"^https?://", "", url), parent)
        self.url = url
        self.setProperty("size", "sm")
        mono = theme._MONO_FAMILY or "monospace"
        self.setStyleSheet(f'QPushButton {{ font-family: "{mono}"; font-size: {theme.FS_SMALL}px; }}')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(url)
        self.setToolTip(url)

    def set_on(self, on: bool) -> None:
        self.setProperty("kind", "" if on else "ghost")
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class PreviewPanel(QWidget):
    """The Preview tab of the side panel for one session."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session_id = session_id
        self.cwd = cwd
        self.url = _urls.get(session_id, "")
        self.detected: List[str] = []
        self._chips: List[Chip] = []
        self._net = QNetworkAccessManager(self)
        self._reply: Optional[QNetworkReply] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = Row(4, (12, 6, 10, 6))
        bar.setMinimumHeight(42)
        self.field = QLineEdit(self.url or DEFAULT_URL)
        self.field.setPlaceholderText(DEFAULT_URL)
        self.field.setFixedHeight(28)
        self.field.setAccessibleName("Address")
        self.field.returnPressed.connect(lambda: self.go(self.field.text()))
        bar.add(self.field, 1)
        self.go_btn = IconButton("arrow-right", "Open", 14)
        self.go_btn.clicked.connect(lambda: self.go(self.field.text()))
        bar.add(self.go_btn)
        self.reload_btn = IconButton("rotate-cw", "Reload", 13)
        self.reload_btn.clicked.connect(self.reload)
        bar.add(self.reload_btn)
        self.open_btn = IconButton("external-link", "Open in your browser", 13)
        self.open_btn.clicked.connect(self.open_in_browser)
        bar.add(self.open_btn)
        root.addWidget(bar)
        root.addWidget(hairline())

        self.chips = QWidget()
        self.chips_box = QHBoxLayout(self.chips)
        self.chips_box.setContentsMargins(12, 8, 12, 8)
        self.chips_box.setSpacing(6)
        self.chips_box.addStretch(1)
        root.addWidget(self.chips)
        self.chips_line = hairline()
        root.addWidget(self.chips_line)

        # The body: the address card when one is set, the empty state otherwise.
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(28, 40, 28, 40)
        bl.setSpacing(8)
        bl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.icon)
        self.title = label("", tone="muted", wrap=True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.title)
        self.hint = label("", tone="faint", size="small", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.hint)
        self.status = label("", tone="faint", size="small")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.status)
        self.browser_btn = button(
            "Open in browser",
            kind="primary",
            icon="external-link",
            on_click=self.open_in_browser,
            tooltip="Open in your browser",
        )
        self.browser_btn.setText("Open in browser")
        bl.addWidget(self.browser_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.body, 1)

        bind(store.rt_changed, self, self._on_rt)
        bind(theme.theme.changed, self, lambda _t: self._apply_theme())
        self._apply_theme()
        self.refresh()

    def _apply_theme(self) -> None:
        # The address is in the mono face; it goes in a stylesheet because the app's ``*`` rule beats setFont.
        mono = theme._MONO_FAMILY or "monospace"
        self.field.setStyleSheet(f'QLineEdit {{ font-family: "{mono}"; font-size: {theme.FS_SMALL}px; }}')
        self.render()

    # ---------------------------------------------------------------- state
    def set_session(self, session: Json) -> None:
        sid = str(session.get("id") or self.session_id)
        if sid != self.session_id:
            self.session_id = sid
            self.url = _urls.get(sid, "")
            self.field.setText(self.url or DEFAULT_URL)
        self.cwd = str(session.get("cwd") or session.get("project") or self.cwd)
        self.refresh()

    def _on_rt(self, sid: str) -> None:
        if sid == self.session_id:
            self.refresh()

    def refresh(self) -> None:
        """Look for addresses again and redraw."""
        self.detected = find_urls(self.session_id)
        self.render()

    def go(self, u: str) -> None:
        v = u.strip()
        if not v:
            return
        if not re.match(r"^https?://", v):
            v = f"http://{v}"
        _urls[self.session_id] = v
        self.url = v
        self.field.setText(v)
        self.render()
        self._check()

    def reload(self) -> None:
        self.refresh()
        self._check()

    def open_in_browser(self) -> None:
        if self.url:
            QDesktopServices.openUrl(QUrl(self.url))

    # ---------------------------------------------------------------- reachability
    def _check(self) -> None:
        """Ask the address whether anything answers, so the card can say so."""
        if not self.url:
            return
        if self._reply is not None:
            self._reply.abort()
            self._reply = None
        self.status.setText("Checking…")
        req = QNetworkRequest(QUrl(self.url))
        req.setTransferTimeout(2500)
        reply = self._net.get(req)
        self._reply = reply

        def done() -> None:
            if self._reply is not reply:
                return
            self._reply = None
            if (
                reply.error() == QNetworkReply.NetworkError.NoError
                or reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) is not None
            ):
                self.status.setText("Something is listening there.")
                self.status.setProperty("tone", "ok")
            else:
                self.status.setText("Nothing answered at this address yet.")
                self.status.setProperty("tone", "faint")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            reply.deleteLater()

        reply.finished.connect(done)

    # ---------------------------------------------------------------- render
    def render(self) -> None:
        t = theme.tokens()
        for c in self._chips:
            self.chips_box.removeWidget(c)
            c.deleteLater()
        self._chips = []
        for i, u in enumerate(self.detected):
            c = Chip(u)
            c.set_on(u == self.url)
            c.clicked.connect(lambda _c=False, u=u: self.go(u))
            self.chips_box.insertWidget(i, c)
            self._chips.append(c)
        self.chips.setVisible(bool(self.detected))
        self.chips_line.setVisible(bool(self.detected))
        self.reload_btn.setEnabled(bool(self.url))
        self.open_btn.setEnabled(bool(self.url))
        if self.url:
            self.icon.setPixmap(icons.pixmap("globe", t.text_3, 22))
            self.title.setText(self.url)
            self.title.setProperty("mono", "true")
            self.hint.setText("The preview opens in your browser in this client.")
            self.browser_btn.show()
            self.status.show()
        else:
            self.icon.setPixmap(icons.pixmap("app-window", t.text_3, 22))
            self.title.setText("Preview a local web app next to the conversation.")
            self.title.setProperty("mono", "false")
            self.hint.setText("Ask Claude to start your dev server — addresses it prints show up above as shortcuts.")
            self.browser_btn.hide()
            self.status.hide()
            self.status.setText("")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)


if __name__ == "__main__":  # pragma: no cover - development window
    import sys

    from PySide6.QtWidgets import QApplication

    from ... import fonts
    from ...rpc import rpc

    app = QApplication(sys.argv)
    fonts.load()
    theme.theme.apply(True)
    store.init()
    rpc.connect_to(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5300/")
    panel = PreviewPanel("dev", ".")
    panel.setObjectName("panel")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    panel.resize(560, 480)
    panel.show()
    sys.exit(app.exec())
