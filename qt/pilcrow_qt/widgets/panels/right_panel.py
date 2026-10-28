"""Placeholder until the side panel (web/src/components/RightPanel.tsx) lands."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class RightPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        self.lb = QLabel("Panel")
        lay.addWidget(self.lb)
        lay.addStretch(1)

    def set_session(self, session: dict) -> None:
        self.lb.setText(f"Panel — {session.get('title')}")
