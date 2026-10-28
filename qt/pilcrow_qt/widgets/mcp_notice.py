"""One line under the session header while an MCP server this session uses needs attention (a port of
web/src/components/McpNotice.tsx). The usual cause is an expired sign-in; the fix is one click here
rather than a trip to Settings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import icons, theme
from ..lib.fmt import err_text
from ..store import store
from .ui.bits import IconButton, button
from .ui.signals import bind

Json = Dict[str, Any]


class McpNotice(QWidget):
    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.dismissed: Set[str] = set()
        self.busy: Optional[str] = None
        self._key: List[tuple] = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 4, 20, 0)
        self.lay.setSpacing(4)
        self.hide()
        bind(store.rt_changed, self, lambda sid: sid == self.sid and self.refresh())
        bind(theme.theme.changed, self, lambda _t: self.refresh(True))
        self.refresh()

    def _needy(self) -> List[Json]:
        r = store.rt.get(self.sid)
        mcp = (r.get("mcp") if r else None) or []
        return [
            m
            for m in mcp
            if m.get("status") in ("needs-auth", "failed")
            and f"{m.get('name')}:{m.get('status')}" not in self.dismissed
        ]

    def refresh(self, force: bool = False) -> None:
        needy = self._needy()[:3]
        key = [(m.get("name"), m.get("status"), m.get("error"), self.busy) for m in needy]
        if key == self._key and not force:
            return
        self._key = key
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for m in needy:
            self.lay.addWidget(self._row(m))
        self.setVisible(bool(needy))

    def _row(self, m: Json) -> QWidget:
        t = theme.tokens()
        name = str(m.get("name") or "")
        auth = m.get("status") == "needs-auth"
        row = QWidget()
        row.setObjectName("mcp-notice")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        wash = theme.blend(t.warn_wash if auth else t.err_wash, t.canvas).name()
        row.setStyleSheet(f"#mcp-notice {{ background: {wash}; border-radius: {theme.R3}px; }}")
        row.setAccessibleName(f"{name} needs attention")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 6, 6, 6)
        lay.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(icons.pixmap("key-round" if auth else "circle-alert", t.warn if auth else t.err, 14))
        lay.addWidget(ico)
        if auth:
            text = f"<b>{name}</b> needs you to sign in again before Claude can use it."
        else:
            err = str(m.get("error") or "").split("\n")[0][:120]
            text = f"<b>{name}</b> is not connected" + (
                f"<span style='color:{t.text_3}'> · {err}</span>" if err else "."
            )
        lb = QLabel(text)
        lb.setTextFormat(Qt.TextFormat.RichText)
        lb.setStyleSheet(f"font-size: {theme.FS_SMALL}px; color: {t.text};")
        lay.addWidget(lb, 1)
        if auth:
            lay.addWidget(
                button(
                    "Sign in",
                    kind="primary",
                    size="sm",
                    on_click=lambda: store.set_dialog("mcpAuth", {"sessionId": self.sid, "name": name}),
                )
            )
        else:
            retry = button("Retry", size="sm", icon="refresh-cw", on_click=lambda: self._retry(name))
            retry.setEnabled(self.busy != name)
            lay.addWidget(retry)
        x = IconButton("x", "Dismiss", 13)
        x.clicked.connect(lambda: self._dismiss(f"{name}:{m.get('status')}"))
        lay.addWidget(x)
        return row

    def _dismiss(self, key: str) -> None:
        self.dismissed.add(key)
        self.refresh()

    def _retry(self, name: str) -> None:
        self.busy = name
        self.refresh()

        def done(_r: Any) -> None:
            store.toast(f"Reconnected {name}", "success")
            self._settle()

        def bad(e: Any) -> None:
            store.toast(err_text(e), "error")
            self._settle()

        store.call("session.mcp.reconnect", {"id": self.sid, "name": name}, quiet=True).then(done).catch(bad)

    def _settle(self) -> None:
        self.busy = None
        self.refresh()
