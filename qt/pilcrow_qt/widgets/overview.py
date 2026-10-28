"""Placeholder until the control room (web/src/components/Overview.tsx) lands."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Overview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        t = QLabel("Control room")
        t.setProperty("size", "title")
        lay.addWidget(t)
        lay.addStretch(1)
