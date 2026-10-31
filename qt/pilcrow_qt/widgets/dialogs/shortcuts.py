"""The keyboard shortcuts dialog (ShortcutsDialog in web/src/components/Overlays.tsx)."""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import theme
from ..ui.bits import hairline
from .parts import Keys
from .sheet import Sheet

SHORTCUTS: List[Tuple[str, str]] = [
    ("Ctrl+K", "Command palette"),
    ("Ctrl+N", "New session"),
    ("Ctrl+Shift+N", "New session with options"),
    ("Ctrl+F", "Find in the conversation"),
    ("Ctrl+B", "Toggle sidebar"),
    ("Ctrl+\\", "Toggle side panel"),
    ("Ctrl+O", "Cycle view: Normal, Thinking, Verbose"),
    ("Alt+1 … 7", "Changes, Files, Terminal, Preview, Tasks, Timeline, Session"),
    ("Ctrl+Shift+O", "Control room"),
    ("Ctrl+Alt+↑ / ↓", "Previous / next session"),
    ("Enter", "Send (Shift+Enter for a new line)"),
    ("Shift+Tab", "Cycle permission mode"),
    ("Esc", "Stop Claude"),
    ("↑ in an empty box", "Recall your previous message"),
    ("@", "Mention a file or folder"),
    ("/", "Run a slash command"),
    ("1 · 2 · 3", "Answer a permission request"),
    ("Ctrl+S", "Save the open file"),
    ("Ctrl+,", "Settings"),
]
_WORDS = ("…", "·", "in", "an", "empty", "box")


class ShortcutRow(QWidget):
    """One line: what it does on the left, the keys on the right (``.shortcut``)."""

    def __init__(self, keys: str, what: str, parent=None) -> None:
        super().__init__(parent)
        self.keys = keys
        self.what = what
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 9, 0, 9)
        lay.setSpacing(16)
        self.label = QLabel(what)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        caps = QWidget()
        cl = QHBoxLayout(caps)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(3)
        for p in keys.split(" "):
            if p in _WORDS or (p == "/" and " / " in keys):
                w = QLabel(p)
                theme.tint(w, fg="text_3")
                w.setProperty("fs", "small")
                cl.addWidget(w)
            else:
                cl.addWidget(Keys(p))
        lay.addWidget(caps)


class ShortcutsDialog(Sheet):
    def __init__(self, window: QWidget) -> None:
        super().__init__(window, "shortcuts", False, width=480, title="Keyboard shortcuts")
        self.body_lay.setSpacing(0)
        self.rows: List[ShortcutRow] = []
        for i, (k, v) in enumerate(SHORTCUTS):
            if i:
                self.body_lay.addWidget(hairline())
            row = ShortcutRow(k, v)
            self.rows.append(row)
            self.body_lay.addWidget(row)
