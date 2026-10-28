"""The welcome page (a port of web/src/components/Welcome.tsx): shown when no session is open."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..lib.fmt import basename, tildify
from ..store import store
from .ui.bits import IconButton, Mark, button, label
from .ui.signals import bind


class Welcome(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 8, 10, 0)
        self.side_btn = IconButton("panel-left-open", "Show sidebar (Ctrl+B)", 16)
        self.side_btn.clicked.connect(lambda: store.set_ui(sidebar=True))
        head.addWidget(self.side_btn)
        head.addStretch(1)
        outer.addLayout(head)

        inner = QWidget()
        inner.setMaximumWidth(560)
        il = QVBoxLayout(inner)
        il.setSpacing(10)
        il.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(Mark(56), 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = label("Pilcrow", size="title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(self.title)
        self.sub = label("A desktop for Claude Code. Start a session in a folder and ask for something.", tone="muted", wrap=True)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(self.sub)
        self.new_btn = button("New session", kind="primary", icon="square-pen", on_click=lambda: store.set_dialog("newSession", {}))
        il.addWidget(self.new_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.folders = QVBoxLayout()
        self.folders.setSpacing(4)
        il.addLayout(self.folders)
        outer.addWidget(inner, 1, Qt.AlignmentFlag.AlignCenter)
        bind(store.config_changed, self, lambda _c: self._refresh())
        bind(store.ui_changed, self, lambda _u: self.side_btn.setVisible(not store.ui.sidebar))
        bind(store.info_changed, self, lambda _i: self._refresh())
        self._refresh()

    def _refresh(self) -> None:
        while self.folders.count():
            w = self.folders.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        recent = (store.config or {}).get("recentProjects") or []
        if recent:
            self.folders.addWidget(label("Recent folders", tone="faint", size="micro"))
        home = (store.info or {}).get("home")
        for p in recent[:6]:
            b = QPushButton(f"{basename(str(p))}   {tildify(str(p), home)}")
            b.setProperty("kind", "ghost")
            b.setStyleSheet("text-align: left;")
            b.clicked.connect(lambda _c=False, path=p: store.quick_new_session(str(path)))
            self.folders.addWidget(b)
        _ = theme
