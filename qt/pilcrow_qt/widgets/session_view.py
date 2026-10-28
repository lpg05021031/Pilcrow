"""Placeholder until the real SessionView (web/src/components/SessionView.tsx) lands."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SessionView(QWidget):
    def __init__(self, session: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session.get("id")
        lay = QVBoxLayout(self)
        self.title = QLabel(str(session.get("title") or ""))
        self.title.setProperty("size", "title")
        lay.addWidget(self.title)
        lay.addStretch(1)

    def update_session(self, session: dict) -> None:
        self.title.setText(str(session.get("title") or ""))

    def open_find(self) -> None:
        pass
