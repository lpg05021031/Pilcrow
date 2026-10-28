"""Placeholder until the inbox (web/src/components/Inbox.tsx) lands."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class InboxPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        t = QLabel("Inbox")
        t.setProperty("size", "title")
        lay.addWidget(t)
        lay.addStretch(1)
