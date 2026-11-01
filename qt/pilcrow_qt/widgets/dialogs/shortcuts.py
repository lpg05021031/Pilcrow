"""The keyboard shortcuts dialog (ShortcutsDialog in web/src/components/Overlays.tsx)."""

from __future__ import annotations

from typing import List

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import theme
from ...lib.shortcuts import SHORTCUTS
from ..ui.bits import hairline
from .parts import Keys
from .sheet import Sheet

_WORDS = ("…", "·", "in", "an", "empty", "box")  # words between the keys, not keys


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
        for i, sc in enumerate(SHORTCUTS.values()):
            if i:
                self.body_lay.addWidget(hairline())
            row = ShortcutRow(sc.keys, sc.label)
            self.rows.append(row)
            self.body_lay.addWidget(row)
