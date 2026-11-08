"""The keyboard shortcuts in one table (a port of web/src/lib/shortcuts.ts): the window binds the
ones it runs (``MainWindow._install_shortcuts``), the command palette shows their keys, and the
Keyboard shortcuts sheet lists every entry in this order. What each one does lives with the window;
matching a key press is Qt's (``QShortcut``), so the web's ``matches`` has no counterpart here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Shortcut:
    #: as shown: modifiers first, then the key ("Ctrl+Shift+N"); Ctrl is ⌘ on a Mac
    keys: str
    label: str
    #: the key sequences that trigger it (Qt's spelling), when they differ from ``keys``
    combos: Tuple[str, ...] = ()

    @property
    def sequences(self) -> Tuple[str, ...]:
        return self.combos or (self.keys,)


PANEL_KEYS = tuple(f"Alt+{i}" for i in range(1, 8))  # Changes … Session, in PANEL_TABS order

SHORTCUTS: Dict[str, Shortcut] = {
    "palette": Shortcut("Ctrl+K", "Command palette"),
    "newSession": Shortcut("Ctrl+N", "New session"),
    "newSessionWith": Shortcut("Ctrl+Shift+N", "New session with options"),
    "find": Shortcut("Ctrl+F", "Find in the conversation"),
    "sidebar": Shortcut("Ctrl+B", "Toggle sidebar"),
    "panel": Shortcut("Ctrl+\\", "Toggle side panel"),
    "view": Shortcut("Ctrl+O", "Cycle view: Normal, Thinking, Verbose"),
    "tabs": Shortcut("Alt+1 … 7", "Changes, Files, Terminal, Preview, Tasks, Timeline, Session", PANEL_KEYS),
    "board": Shortcut("Ctrl+Shift+O", "Workboard"),
    "dispatch": Shortcut("Ctrl+Shift+D", "Dispatch"),
    "nextSession": Shortcut("Ctrl+Alt+↑ / ↓", "Previous / next session", ("Ctrl+Alt+Up", "Ctrl+Alt+Down")),
    # the window's own: a browser has these keys itself
    "zoom": Shortcut("Ctrl+= / − / 0", "Larger, smaller, actual text size", ("Ctrl+=", "Ctrl++", "Ctrl+-", "Ctrl+0")),
    "send": Shortcut("Enter", "Send (Shift+Enter for a new line)"),
    "mode": Shortcut("Shift+Tab", "Cycle permission mode"),
    "stop": Shortcut("Esc", "Stop Claude", ("Escape",)),
    "recall": Shortcut("↑ in an empty box", "Recall your previous message"),
    "mention": Shortcut("@", "Mention a file or folder"),
    "command": Shortcut("/", "Run a slash command"),
    "answer": Shortcut("1 · 2 · 3", "Answer a permission request"),
    "save": Shortcut("Ctrl+S", "Save the open file"),
    "settings": Shortcut("Ctrl+,", "Settings"),
    "shortcuts": Shortcut("Ctrl+/", "Keyboard shortcuts"),
    "quit": Shortcut("Ctrl+Q", "Quit Pilcrow"),
}
