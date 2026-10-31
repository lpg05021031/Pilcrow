"""Settings → Claude Code (the ``claude`` section of SettingsDialog.tsx plus the API setup card from
Welcome.tsx): which ``claude`` program runs, the defaults for new sessions, idle timeout, worktree
folder, and how Claude Code reaches the API."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ... import icons, theme
from ...lib.fmt import tildify
from ...store import store
from ..ui.bits import Box, IconButton, button, copy_text, label
from ..ui.signals import bind
from .settings_general import Rows, SetRow, config, save
from .settings_ui import (
    EFFORTS,
    ModelSelect,
    combo,
    combo_value,
    home,
    line_edit,
    modes_for,
    select_value,
    vbox,
)

Json = Dict[str, Any]
PROGRAMS = ("auto", "bundled", "system")
IDLE = [10, 30, 60, 240, 1440]
SNIPPET = """{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-gateway.example.com",
    "ANTHROPIC_AUTH_TOKEN": "your-api-key"
  }
}"""


def idle_label(m: int) -> str:
    return f"{m} minutes" if m < 60 else f"{m // 60} hour{'s' if m > 60 else ''}" if m < 1440 else "1 day"


def claude_hint(info: Optional[Json]) -> str:
    """Say which Claude Code runs in words first (SettingsDialog.tsx claudeHint)."""
    c = (info or {}).get("claude") or {}
    if not c.get("path"):
        return "Not found"
    v = f" {c['version']}" if c.get("version") else ""
    src = c.get("source")
    return (
        f"The copy installed with Pilcrow{v}"
        if src == "bundled"
        else f"The claude on your PATH{v}"
        if src == "system"
        else f"Custom program{v}"
    )


class ClaudePage(Rows):
    def __init__(self, sheet: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        c = config()
        info = store.info or {}
        claude = info.get("claude") or {}
        bundled = claude.get("bundled")
        system = claude.get("system")
        self.program = combo(
            [
                ("auto", "Automatic"),
                ("bundled", "Bundled with Pilcrow"),
                ("system", "Installed `claude`"),
                ("custom", "Custom path…"),
            ],
            str(c.get("claudeBinary")) if c.get("claudeBinary") in PROGRAMS else "custom",
            hints={
                "auto": "The bundled copy, or `claude` on PATH",
                "bundled": f"Claude Code {bundled['version']}" if bundled else "Not installed",
                "system": f"Claude Code {system['version']} · {tildify(system['path'], home())}"
                if system
                else "Not found on PATH",
            },
            name="Claude Code program",
            popup_width=300,
        )
        self.program.currentIndexChanged.connect(self._on_program)
        self.program_row = SetRow("Claude Code program", self.program, claude_hint(info), fill=True)
        self.program_row.title.setToolTip(tildify(str(claude.get("path") or ""), home()))
        self.add(self.program_row)

        self.custom = line_edit("/usr/local/bin/claude", mono=True, name="Custom path")
        self.custom.setText(str(c.get("claudeBinary") or "") if c.get("claudeBinary") not in PROGRAMS else "")
        self.custom.editingFinished.connect(lambda: save({"claudeBinary": self.custom.text().strip() or "auto"}))
        self.custom_row = SetRow("Custom path", self.custom, fill=True)
        self.custom_row.setVisible(c.get("claudeBinary") not in PROGRAMS)
        self.add(self.custom_row)

        modes = modes_for(info)
        self.mode = combo(
            [(str(m["id"]), str(m["label"])) for m in modes],
            str(c.get("defaultMode") or "default"),
            hints={str(m["id"]): str(m["hint"]) for m in modes},
            name="Default permission mode",
            icons_={str(m["id"]): str(m["icon"]) for m in modes},
            popup_width=300,
        )
        self.mode.currentIndexChanged.connect(lambda _i: save({"defaultMode": combo_value(self.mode)}))
        self.add(SetRow("Default permission mode", self.mode, fill=True))

        self.model = ModelSelect(str(c.get("defaultModel") or ""), name="Default model")
        self.model.changed.connect(lambda v, typing: not typing and save({"defaultModel": v}))
        self.model.committed.connect(
            lambda: (
                self.model.value.strip() != str(config().get("defaultModel") or "")
                and save({"defaultModel": self.model.value.strip()})
            )
        )
        self.add(SetRow("Default model", self.model, "For new sessions; the models your gateway offers", fill=True))

        self.effort = combo(EFFORTS, str(c.get("defaultEffort") or ""), name="Default effort")
        self.effort.currentIndexChanged.connect(lambda _i: save({"defaultEffort": combo_value(self.effort)}))
        self.add(SetRow("Default effort", self.effort, fill=True))

        self.idle = combo(
            [(str(m), idle_label(m)) for m in IDLE],
            str(c.get("idleTimeoutMin") or 30),
            name="Stop idle Claude Code processes after",
        )
        self.idle.currentIndexChanged.connect(lambda _i: save({"idleTimeoutMin": int(combo_value(self.idle) or 30)}))
        self.add(
            SetRow(
                "Stop idle Claude Code processes after",
                self.idle,
                "Sessions resume automatically on your next message",
                fill=True,
            )
        )

        self.worktree = line_edit("~/.local/share/pilcrow/worktrees", mono=True, name="Worktree folder")
        self.worktree.setText(str(c.get("worktreeRoot") or ""))
        self.worktree.editingFinished.connect(lambda: save({"worktreeRoot": self.worktree.text().strip()}))
        self.add(
            SetRow(
                "Worktree folder",
                self.worktree,
                "Where isolated worktrees are created. Empty uses ~/.local/share/pilcrow/worktrees",
                fill=True,
            )
        )

        bind(store.config_changed, self, self._sync)
        bind(store.info_changed, self, lambda _i: self._info())

    def _on_program(self, _i: int) -> None:
        v = combo_value(self.program)
        self.custom_row.setVisible(v == "custom")
        if v == "custom":
            self.custom.setFocus()
            return
        save({"claudeBinary": v})

    def _sync(self, c: Json) -> None:
        for w in (self.program, self.mode, self.effort, self.idle):
            w.blockSignals(True)
        select_value(self.program, str(c.get("claudeBinary")) if c.get("claudeBinary") in PROGRAMS else "custom")
        select_value(self.mode, str(c.get("defaultMode") or "default"))
        select_value(self.effort, str(c.get("defaultEffort") or ""))
        select_value(self.idle, str(c.get("idleTimeoutMin") or 30))
        for w in (self.program, self.mode, self.effort, self.idle):
            w.blockSignals(False)
        self.custom_row.setVisible(c.get("claudeBinary") not in PROGRAMS)
        if not self.worktree.hasFocus():
            self.worktree.setText(str(c.get("worktreeRoot") or ""))

    def _info(self) -> None:
        info = store.info or {}
        self.program_row.set_hint(claude_hint(info))
        self.program_row.title.setToolTip(tildify(str((info.get("claude") or {}).get("path") or ""), home()))


class SetupCard(Box):
    """``.setup-card`` from Welcome.tsx: what Claude Code needs to reach the API and where it goes, with
    the current endpoint and credential as app.info reports them, and a re-check."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            bg=lambda t: theme.blend(t.raised_2, t.raised) if t.dark else QColor(t.raised),
            line="accent_ring/raised",
            radius=14,
            line_w=1.5,
            parent=parent,
        )
        self.setObjectName("setup")
        lay = vbox(self, 8, (16, 14, 16, 12))
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.key_ico = QLabel()
        hl.addWidget(self.key_ico)
        self.head_lb = label("Connect Claude Code to your API")
        self.head_lb.setStyleSheet("font-size: 14px; font-weight: 600;")
        hl.addWidget(self.head_lb, 1)
        lay.addWidget(head)
        self.endpoint = label("", tone="muted", wrap=True)
        lay.addWidget(self.endpoint)
        self.credential = label("", tone="muted", wrap=True)
        lay.addWidget(self.credential)
        self.para = label(
            "Pilcrow runs the same claude program you use in the terminal, with the same settings. No endpoint or key was found yet. Add them to ~/.claude/settings.json:",
            tone="muted",
            wrap=True,
        )
        lay.addWidget(self.para)
        snip = Box(bg="inset", radius=theme.R3)
        snip.setObjectName("snippet")
        sl = QHBoxLayout(snip)
        sl.setContentsMargins(12, 10, 6, 10)
        sl.setSpacing(6)
        self.snippet = label(SNIPPET, tone="muted", mono=True)
        self.snippet.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.snippet.setStyleSheet("font-size: 12px;")
        sl.addWidget(self.snippet, 1)
        self.copy = IconButton("copy", "Copy", 13)
        self.copy.clicked.connect(lambda: (copy_text(SNIPPET), store.toast("Copied", "success")))
        sl.addWidget(self.copy, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(snip)
        acts = QWidget()
        al = QHBoxLayout(acts)
        al.setContentsMargins(0, 2, 0, 0)
        al.setSpacing(10)
        al.addWidget(label("ANTHROPIC_* environment variables work too.", tone="faint", size="small"), 1)
        self.check = button("Check again", size="sm", icon="refresh-cw", on_click=self._check)
        self.check.setAccessibleName("Re-check")
        al.addWidget(self.check)
        lay.addWidget(acts)
        bind(theme.theme.changed, self, lambda _t: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        t = theme.tokens()
        info = store.info or {}
        g = info.get("gateway") or {}
        connected = bool(g.get("hasToken") or g.get("loggedIn"))
        self.key_ico.setPixmap(icons.pixmap("key-round", t.accent_text, 15))
        self.head_lb.setText("Claude Code is connected to your API" if connected else "Connect Claude Code to your API")
        base = g.get("baseUrl")
        src = f" (from {g['source']} settings)" if g.get("source") else ""
        self.endpoint.setText(
            f"API endpoint: {base}{src}" if base else "API endpoint: Default Anthropic API (no ANTHROPIC_BASE_URL set)"
        )
        self.credential.setText(
            "API credential: Found in settings or environment"
            if g.get("hasToken")
            else "API credential: Signed in with `claude login`"
            if g.get("loggedIn")
            else "API credential: No token or API key found; Claude Code may ask you to log in"
        )
        if g.get("error"):
            self.credential.setText(f"Could not read Claude Code's settings: {g['error']}")
        self.para.setVisible(not connected)
        self.check.setText("Re-check" if connected else "Check again")

    def _check(self) -> None:
        self.check.setEnabled(False)

        def done(info: Json) -> None:
            self.check.setEnabled(True)
            g = (info or {}).get("gateway") or {}
            ok = bool(g.get("hasToken") or g.get("loggedIn"))
            store.toast("Claude Code is connected" if ok else "Still no API key found", "success" if ok else "warning")

        store.refresh_info().then(done).catch(lambda _e: self.check.setEnabled(True))


_ = QSizePolicy
