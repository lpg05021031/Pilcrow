"""The Preview panel (a port of web/src/components/panels/PreviewPanel.tsx).

The web interface frames the dev server in an iframe. This client has no web engine, so the panel
keeps the address bar and the shortcuts to the localhost addresses Claude's commands printed, and
opens the page in the system browser; the body says so and shows whether something is listening at
the address.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontMetricsF, QPainter, QPen
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ... import theme
from ...store import store
from ..ui.bits import Row
from ..ui.signals import bind
from .right_panel import Btn, EmptyState, Hairline, icon_btn, lbl

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
    """A detected address as a shortcut (``.chip.mono``): a 24px pill, 11.5px mono, lit when chosen."""

    def __init__(self, url: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(re.sub(r"^https?://", "", url), parent)
        self.url = url
        self.on = False
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(url)
        self.setToolTip(url)
        self.setFixedHeight(24)
        self.setFixedWidth(9 + int(QFontMetricsF(theme.font_mono(11.5)).horizontalAdvance(self.text())) + 9 + 1)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_on(self, on: bool) -> None:
        self.on = on
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        hover = self.underMouse()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.blend(t.hair_3 if self.on else t.hair, t.canvas), 1))
        p.setBrush(theme.blend(t.fill_2 if (self.on or hover) else t.fill_1, t.canvas))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 12, 12)
        p.setFont(theme.font_mono(11.5))
        p.setPen(QColor(t.text if (self.on or hover) else t.text_2))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


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
        self.go_btn = icon_btn("arrow-right", "Open", 14)
        self.go_btn.clicked.connect(lambda: self.go(self.field.text()))
        bar.add(self.go_btn)
        self.reload_btn = icon_btn("rotate-cw", "Reload", 13)
        self.reload_btn.clicked.connect(self.reload)
        bar.add(self.reload_btn)
        self.open_btn = icon_btn("external-link", "Open in your browser", 13)
        self.open_btn.clicked.connect(self.open_in_browser)
        bar.add(self.open_btn)
        root.addWidget(bar)
        root.addWidget(Hairline())

        self.chips = QWidget()
        self.chips_box = QHBoxLayout(self.chips)
        self.chips_box.setContentsMargins(12, 8, 12, 8)
        self.chips_box.setSpacing(6)
        self.chips_box.addStretch(1)
        root.addWidget(self.chips)
        self.chips_line = Hairline()
        root.addWidget(self.chips_line)

        # The body: the ``.empty`` block at the top — the address card when one is set, the invitation otherwise.
        self.body = EmptyState("app-window", "", "")
        bl = self.body.layout()
        self.icon = self.body.ico
        self.title = self.body.text
        self.hint = self.body.hint
        self.status = lbl("", theme.FS_SMALL, tone="faint")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        bl.addWidget(self.status)
        self.browser_btn = Btn(
            "Open in browser", icon="external-link", kind="primary", size="", on_click=self.open_in_browser
        )
        bl.addWidget(self.browser_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.body)
        root.addStretch(1)

        bind(store.rt_changed, self, self._on_rt)
        bind(theme.theme.changed, self, lambda _t: self._apply_theme())
        self._apply_theme()
        self.refresh()

    def _apply_theme(self) -> None:
        # .input.mono: a quiet fill with a hairline, 12.5px mono; focus lifts it onto the canvas.
        t = theme.tokens()
        f = theme.font_mono(12.5)
        self.field.setStyleSheet(
            f'QLineEdit {{ font-family: "{f.family()}"; font-size: 12px; background: {theme.blend(t.fill_1, t.canvas).name()}; '
            f"border: 1px solid {theme.blend(t.hair, t.canvas).name()}; border-radius: {theme.R2}px; padding: 0 10px; color: {t.text}; }}"
            f"QLineEdit:hover {{ border-color: {theme.blend(t.hair_2, t.canvas).name()}; }}"
            f"QLineEdit:focus {{ background: {t.canvas}; border-color: {theme.blend(t.hair_3, t.canvas).name()}; }}"
        )
        pal = self.field.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, theme.qcolor(t.text_3))
        self.field.setPalette(pal)
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
        _ = t
        if self.url:
            self.body.set_icon("globe")
            self.body.set_text(self.url, "The preview opens in your browser in this client.")
            self.browser_btn.show()
            self.status.show()
        else:
            self.body.set_icon("app-window")
            self.body.set_text(
                "Preview a local web app next to the conversation.",
                "Ask Claude to start your dev server — addresses it prints show up above as shortcuts.",
            )
            self.browser_btn.hide()
            self.status.hide()
            self.status.setText("")


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
