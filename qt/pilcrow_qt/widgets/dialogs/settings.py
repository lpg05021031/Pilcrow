"""The settings sheet (web/src/components/SettingsDialog.tsx): a left nav of six pages and the page
beside it, with the version line underneath.

``open_settings(window, page)`` opens it on ``page`` (``True`` means General), raises the one already
open, or closes it when ``page`` is falsy.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ... import theme
from ...store import store
from ..ui.bits import Mark, hairline
from ..ui.signals import bind
from .parts import Text, sheet_bg
from .settings_ui import Sheet, open_sheet, styled, vbox

SECTIONS: List[Tuple[str, str]] = [
    ("general", "General"),
    ("claude", "Claude Code"),
    ("skills", "Skills"),
    ("mcp", "MCP servers"),
    ("memory", "Memory"),
    ("diagnostics", "Diagnostics"),
]
WIDE = ("skills", "mcp", "memory")


class SettingsSheet(Sheet):
    def __init__(self, window: QWidget, page: str = "general") -> None:
        super().__init__(window, "settings", "Settings", 720, body_margins=(0, 0, 0, 0))
        self.body.setSpacing(0)
        self.section = ""
        self.page: Optional[QWidget] = None

        layout = QWidget()
        row = QHBoxLayout(layout)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.nav = QWidget()
        self.nav.setObjectName("settings-nav")
        self.nav.setFixedWidth(172)
        nl = vbox(self.nav, 1, (14, 6, 10, 14))
        nl.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.group = QButtonGroup(self.nav)
        self.group.setExclusive(True)
        self.nav_buttons: Dict[str, QPushButton] = {}
        for sid, text in SECTIONS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setAccessibleName(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
            b.setFont(theme.font_ui(theme.FS_UI))
            b.clicked.connect(lambda _c=False, s=sid: self.show_section(s))
            nl.addWidget(b)
            self.group.addButton(b)
            self.nav_buttons[sid] = b
        nl.addStretch(1)
        styled(
            self.nav,
            lambda t: (
                f"""
            #settings-nav QPushButton {{ text-align: left; border: none; background: transparent; padding: 0 10px; min-height: 30px; max-height: 30px; border-radius: {theme.R2}px; color: {t.text_2}; font-weight: 400; }}
            #settings-nav QPushButton:hover {{ background: {theme.blend(t.fill_1, sheet_bg(t).name()).name()}; color: {t.text}; }}
            #settings-nav QPushButton:checked {{ background: {theme.blend(t.fill_2, sheet_bg(t).name()).name()}; color: {t.text}; font-weight: 600; }}
        """
            ),
        )
        row.addWidget(self.nav)

        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_host = QWidget()
        self.page_lay = vbox(self.page_host, 0, (10, 0, 22, 20))
        self.page_lay.addStretch(1)
        self.page_scroll.setWidget(self.page_host)
        row.addWidget(self.page_scroll, 1)
        self.layout_w = layout
        self.add(layout)

        self.add_foot_about()
        self.show_section(page if page in dict(SECTIONS) else "general")

    def add_foot_about(self) -> None:
        about = QWidget()
        al = QHBoxLayout(about)
        al.setContentsMargins(22, 12, 22, 14)
        al.setSpacing(8)
        al.addWidget(Mark(16))
        self.about_lb = Text("", theme.FS_MICRO, 400, "text_3", wrap=False)
        al.addWidget(self.about_lb, 1)
        self.foot.setContentsMargins(0, 0, 0, 0)
        self.add_foot(about, 1)
        bind(store.info_changed, self, lambda _i: self._about())
        self._about()

    def _about(self) -> None:
        info = store.info or {}
        claude = (info.get("claude") or {}).get("version") or "—"
        self.about_lb.setText(f"Pilcrow {info.get('version') or ''} · Claude Code {claude}")

    def show_section(self, sid: str) -> None:
        if sid == self.section and self.page is not None:
            return
        self.section = sid
        self.nav_buttons[sid].setChecked(True)
        old = self.page
        if old is not None:
            # Deferred deletion keeps the old page painted until the loop runs; unparent it at once.
            self.page_lay.removeWidget(old)
            old.hide()
            old.setParent(None)
            old.deleteLater()
        self.page = self._build(sid)
        self.page_lay.insertWidget(0, self.page)
        self._width = 860 if sid in WIDE else 720
        self.page_scroll.verticalScrollBar().setValue(0)
        self.schedule_relayout()

    def relayout(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        h = host.height()
        wide = self.section in WIDE
        self.layout_w.setFixedHeight(min(620, int(h * 0.78)) if wide else min(480, int(h * 0.64)))
        super().relayout()

    def _build(self, sid: str) -> QWidget:
        if sid == "general":
            from .settings_general import GeneralPage

            return GeneralPage(self)
        if sid == "claude":
            from .settings_claude import ClaudePage

            return ClaudePage(self)
        if sid == "skills":
            from .settings_skills import SkillsPage

            return SkillsPage(self)
        if sid == "mcp":
            from .settings_mcp import McpPage

            return McpPage(self)
        if sid == "memory":
            from .settings_memory import MemoryPage

            return MemoryPage(self)
        from .settings_diagnostics import DiagnosticsPage

        return DiagnosticsPage(self)


def open_settings(window: QWidget, page: Any) -> Optional[SettingsSheet]:
    """Open (or raise) the settings sheet on ``page``; a falsy ``page`` closes it."""
    cur = open_sheet("settings")
    if not page:
        if cur is not None:
            cur.dismiss(reset=False)
        return None
    section = page if isinstance(page, str) and page in dict(SECTIONS) else "general"
    if isinstance(cur, SettingsSheet):
        cur.show_section(section)
        cur.raise_()
        return cur
    s = SettingsSheet(window, section)
    s.open()
    return s


_ = (hairline, QVBoxLayout)
