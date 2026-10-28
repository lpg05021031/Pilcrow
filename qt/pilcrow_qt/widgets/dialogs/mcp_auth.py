"""OAuth sign-in to an MCP server (web/src/components/McpAuthDialog.tsx). Claude Code runs the flow and
listens for the browser's redirect on localhost; this sheet opens the sign-in page, watches the server's
status and offers a paste box for the cases where the redirect cannot reach this machine.

``open_mcp_auth(window, {"sessionId", "name"})`` opens it; a falsy value closes it."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import icons, theme
from ...lib.fmt import err_text
from ...store import store
from ..ui.bits import Spinner, button, copy_text, label
from ..ui.signals import bind
from .settings_ui import Sheet, hint, line_edit, open_sheet, vbox

Json = Dict[str, Any]


def open_external(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class McpAuthSheet(Sheet):
    def __init__(self, window: QWidget, spec: Json) -> None:
        self.sid = str(spec.get("sessionId") or "")
        self.name = str(spec.get("name") or "")
        super().__init__(
            window, "mcpAuth", f"Sign in to {self.name}", 480, on_submit=self.submit_pasted, title_icon="key-round"
        )
        self.start: Optional[Json] = None
        self.error: Optional[str] = None
        self.done = False
        self.busy = False
        self.opened = False

        self.box = QWidget()
        self.lay = vbox(self.box, 12)
        self.add(self.box)
        self.line = QWidget()
        ll = QHBoxLayout(self.line)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(9)
        self.spinner = Spinner(14)
        ll.addWidget(self.spinner)
        self.line_ico = QLabel()
        self.line_ico.hide()
        ll.addWidget(self.line_ico)
        self.line_lb = label("", wrap=True)
        ll.addWidget(self.line_lb, 1)
        self.lay.addWidget(self.line)
        self.note = hint("")
        self.lay.addWidget(self.note)
        self.paste_row = QWidget()
        pl = QHBoxLayout(self.paste_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        self.paste = line_edit("http://localhost:…/callback?code=…", mono=True, name="Callback address")
        self.paste.textChanged.connect(
            lambda _t: self.use_btn.setEnabled(bool(self.paste.text().strip()) and not self.busy)
        )
        self.paste.returnPressed.connect(self.submit_pasted)
        pl.addWidget(self.paste, 1)
        self.use_btn = button("Use", size="sm", icon="clipboard-paste", on_click=self.submit_pasted)
        self.use_btn.setAccessibleName("Use")
        self.use_btn.setEnabled(False)
        pl.addWidget(self.use_btn)
        self.lay.addWidget(self.paste_row)

        self.copy_btn = button("Copy link", kind="ghost", size="sm", on_click=self._copy)
        self.add_foot(self.copy_btn)
        self.foot_stretch()
        self.cancel_btn = button("Cancel", on_click=self.dismiss)
        self.add_foot(self.cancel_btn)
        self.open_btn = button("Open sign-in page", kind="primary", icon="external-link", on_click=self._open)
        self.add_foot(self.open_btn)

        bind(store.rt_changed, self, self._on_rt)
        self.render()
        store.call("session.mcp.auth", {"id": self.sid, "name": self.name}, quiet=True).then(self._started).catch(
            self._failed
        )

    # ---- the flow
    def _started(self, r: Any) -> None:
        self.start = r if isinstance(r, dict) else {}
        url = self.start.get("authUrl")
        if url and not self.opened:
            self.opened = True
            open_external(str(url))
        if self.start.get("requiresUserAction") is False:
            self.done = True
        self.render()

    def _failed(self, e: Any) -> None:
        self.error = err_text(e) or "Could not start the sign-in"
        self.render()

    def _status(self) -> Optional[str]:
        r = store.rt_of(self.sid)
        if r is None:
            return None
        for m in r.get("mcp") or []:
            if m.get("name") == self.name:
                return m.get("status")
        return None

    def _on_rt(self, sid: str) -> None:
        if sid != self.sid or self.start is None or self.done:
            return
        if self._status() == "connected":
            self.done = True
            self.render()
            # The server flipped to connected; give the person a beat to read it.
            QTimer.singleShot(900, self._finish)

    def _finish(self) -> None:
        store.toast(f"Signed in to {self.name}", "success")
        self.dismiss()

    def submit_pasted(self) -> None:
        url = self.paste.text().strip()
        if not url or self.busy:
            return
        self.busy = True
        self.use_btn.setEnabled(False)

        def ok(_r: Any) -> None:
            self.busy = False
            self.paste.setText("")

        def bad(_e: Any) -> None:
            self.busy = False
            self.use_btn.setEnabled(bool(self.paste.text().strip()))

        store.call("session.mcp.authCallback", {"id": self.sid, "name": self.name, "url": url}).then(ok).catch(bad)

    def _copy(self) -> None:
        if self.start and self.start.get("authUrl"):
            copy_text(str(self.start["authUrl"]))
            store.toast("Link copied", "success")

    def _open(self) -> None:
        if self.start and self.start.get("authUrl"):
            open_external(str(self.start["authUrl"]))

    # ---- rendering
    def render(self) -> None:
        t = theme.tokens()
        has_url = bool(self.start and self.start.get("authUrl"))
        waiting = self.start is not None and not self.done and self.error is None
        self.spinner.setVisible(self.start is None and self.error is None or (waiting and has_url))
        self.line_ico.hide()
        self.line_lb.setStyleSheet("")
        if self.error is not None:
            self.line_lb.setText(f"Sign-in is not available right now: {self.error}")
            self.line_lb.setStyleSheet(f"color: {t.err};")
        elif self.start is None:
            self.line_lb.setText(f"Asking {self.name} where to sign in…")
        elif self.done:
            self.line_ico.setPixmap(icons.pixmap("circle-check", t.ok, 16))
            self.line_ico.show()
            self.line_lb.setText(f"Signed in. Claude can use {self.name} now.")
            self.line_lb.setStyleSheet(f"color: {t.ok};")
        elif not has_url:
            self.line_ico.setPixmap(icons.pixmap("circle-alert", t.warn, 16))
            self.line_ico.show()
            self.line_lb.setText(
                f"Sign-in is not available: {self.name} did not give a sign-in link. It may not need one, or Claude Code could not reach it."
            )
        else:
            self.line_lb.setText("Waiting for you to finish in the browser.")
        port = (self.start or {}).get("callbackPort")
        if waiting and (self.start or {}).get("redirectScheme") == "localhost" and port:
            self.note.setText(
                f"When the provider sends you back, it lands on localhost:{port}, where Claude Code is listening. If the browser can’t open that page, paste its address below."
            )
        else:
            self.note.setText(
                "When the provider sends you back, Claude Code finishes the sign-in. If the browser ends on a page it cannot open, paste that page’s address below."
            )
        self.note.setVisible(waiting)
        self.paste_row.setVisible(waiting)
        self.copy_btn.setVisible(has_url and not self.done)
        self.open_btn.setVisible(has_url and not self.done)
        self.cancel_btn.setText("Done" if self.done else "Cancel")
        self.schedule_relayout()


def open_mcp_auth(window: QWidget, opts: Any) -> Optional[McpAuthSheet]:
    """Open the sign-in sheet for ``{"sessionId", "name"}``; a falsy value closes it."""
    cur = open_sheet("mcpAuth")
    if not opts:
        if cur is not None:
            cur.dismiss(reset=False)
        return None
    spec = opts if isinstance(opts, dict) else {}
    if (
        isinstance(cur, McpAuthSheet)
        and cur.sid == str(spec.get("sessionId") or "")
        and cur.name == str(spec.get("name") or "")
    ):
        cur.raise_()
        return cur
    if cur is not None:
        cur.dismiss(reset=False)
    s = McpAuthSheet(window, spec)
    s.open()
    return s
