"""Settings → Diagnostics (``Diagnostics`` in SettingsDialog.tsx): the app.info checks as a list, a copy
button and a re-check."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import icons, theme
from ...lib.fmt import tildify
from ...store import store
from ..ui.bits import button, copy_text, hairline
from ..ui.signals import bind
from .parts import Text
from .settings_ui import clear_layout, hint, vbox

Json = Dict[str, Any]
WINDOW_LABEL = {
    "electron": "Desktop window",
    "chrome": "Chrome app window",
    "browser": "Browser tab",
    "none": "Server only",
    "qt": "Desktop window",
}


def checks_of(i: Json) -> List[Tuple[str, bool, str]]:
    c = i.get("claude") or {}
    g = i.get("gateway") or {}
    term = i.get("terminal") or {}
    h = str(i.get("home") or "")
    endpoint = "Default Anthropic API (no ANTHROPIC_BASE_URL set)"
    if g.get("baseUrl"):
        endpoint = str(g["baseUrl"]) + (f" (from {g['source']} settings)" if g.get("source") else "")
    return [
        (
            "Claude Code program",
            bool(c.get("path")),
            f"{c.get('version') or '?'} · {c.get('source')} · {tildify(str(c.get('path')), h)}"
            if c.get("path")
            else "Not found — install Claude Code or reinstall Pilcrow",
        ),
        ("API endpoint", bool(g.get("baseUrl")) or g.get("provider") != "anthropic", endpoint),
        (
            "API credential",
            bool(g.get("hasToken")),
            "Found in settings or environment"
            if g.get("hasToken")
            else "No token or API key found; Claude Code may ask you to log in",
        ),
        (
            "Terminal support",
            bool(term.get("pty")),
            f"python3 at {term['python']}"
            if term.get("python")
            else "util-linux script (no resizing)"
            if term.get("script")
            else "Install python3 for the terminal panel",
        ),
        (
            "git",
            bool(i.get("git")),
            "Available" if i.get("git") else "Not found — the Changes panel and worktrees need git",
        ),
        (
            "Running as root",
            not i.get("isRoot"),
            'Claude Code refuses "bypass permissions" mode as root' if i.get("isRoot") else "No",
        ),
    ]


def diagnostics_text(i: Json) -> str:
    lines = [f"Pilcrow {i.get('version')} · {i.get('shell')} shell · Node {i.get('node')} · {i.get('platform')}"]
    lines += [f"{'✓' if ok else '✗'} {k}: {v}" for k, ok, v in checks_of(i)]
    lines.append(f"Config: {i.get('configDir')}")
    return "\n".join(lines)


class DiagnosticsPage(QWidget):
    def __init__(self, sheet: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        self.info: Json = dict(store.info or {})
        self.lay = vbox(self, 0, (0, 0, 0, 0))
        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 2, (0, 0, 0, 14))
        self.lay.addWidget(self.list_w)
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.copy_btn = button("Copy diagnostics", size="sm", icon="copy", on_click=self._copy)
        self.copy_btn.setAccessibleName("Copy diagnostics")
        bl.addWidget(self.copy_btn)
        self.recheck = button("Re-check", kind="ghost", size="sm", icon="refresh-cw", on_click=self._recheck)
        self.recheck.setAccessibleName("Re-check")
        bl.addWidget(self.recheck)
        bl.addStretch(1)
        self.window_lb = hint("", wrap=False)
        bl.addWidget(self.window_lb)
        self.lay.addWidget(bar)
        self.logs = Text("", theme.FS_SMALL, 400, "text_3", 1.5)
        self.lay.addWidget(self.logs)
        bind(store.info_changed, self, lambda i: self.set_info(i))
        self.render()

    def set_info(self, i: Json) -> None:
        self.info = dict(i or {})
        self.render()

    def render(self) -> None:
        clear_layout(self.list_lay)
        i = self.info
        if not i:
            return
        t = theme.tokens()
        for n, (k, ok, v) in enumerate(checks_of(i)):
            r = QWidget()
            rl = QHBoxLayout(r)
            rl.setContentsMargins(0, 10, 0, 10)
            rl.setSpacing(11)
            if n:
                self.list_lay.addWidget(hairline())
            ico = QLabel()
            ico.setPixmap(icons.pixmap("circle-check" if ok else "circle-alert", t.ok if ok else t.warn, 15))
            ico.setFixedSize(15, 17)
            ico.setAlignment(Qt.AlignmentFlag.AlignTop)
            rl.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
            col = QWidget()
            cl = vbox(col, 2)
            cl.addWidget(Text(k, theme.FS_UI, 500))
            cl.addWidget(Text(v, theme.FS_SMALL, 400, "text_3", 1.5))
            rl.addWidget(col, 1)
            self.list_lay.addWidget(r)
        self.window_lb.setText(f"{WINDOW_LABEL.get(str(i.get('shell')), str(i.get('shell')))} · Node {i.get('node')}")
        self.logs.setText(
            f"Logs: ~/.local/state/pilcrow/pilcrow.log · Settings: {tildify(str(i.get('configDir') or ''), str(i.get('home') or ''))}"
        )

    def _copy(self) -> None:
        try:
            copy_text(diagnostics_text(self.info))
            store.toast("Diagnostics copied", "success")
        except Exception:  # noqa: BLE001 — no clipboard on this display
            store.toast("Could not copy to the clipboard", "error")

    def _recheck(self) -> None:
        store.call("app.info", {}).then(self.set_info)
