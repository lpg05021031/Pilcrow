"""The Dispatch page: the board, a job's view or the composer, by ``store.dispatch_view``."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from ...store import store
from ..ui.signals import bind
from .board import Board
from .composer import JobComposer
from .job_view import JobView


class DispatchPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.board = Board()
        self.stack.addWidget(self.board)
        self.job_view: Optional[JobView] = None
        self.composer: Optional[JobComposer] = None
        bind(store.dispatch_view_changed, self, self.route)
        self.route(store.dispatch_view)

    def route(self, view: Any) -> None:
        if view == "new":
            if self.composer is None:
                self.composer = JobComposer()
                self.stack.addWidget(self.composer)
            self.composer.reset()
            self.stack.setCurrentWidget(self.composer)
        elif view:
            if self.job_view is None:
                self.job_view = JobView()
                self.stack.addWidget(self.job_view)
            self.job_view.show_job(str(view))
            self.stack.setCurrentWidget(self.job_view)
        else:
            self.stack.setCurrentWidget(self.board)
            self.board.refresh()
            self.board.setFocus()
