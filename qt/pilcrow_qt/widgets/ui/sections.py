"""Section furniture shared by the pages that list work: a section head with a count, a quiet empty
note, and the small button with a key cap. They came out of the control room (``.ov-h``, ``.ov-empty``,
``.btn.sm`` + ``.key`` in overview.css) and the dispatch board keeps using them after the control room
gave way to the workboard."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import icons, theme
from ..dialogs.parts import Badge, Kbd, Text
from .bits import Box, LayoutButton, Mark


class SectionHead(QWidget):
    """``.ov-h``: an optional glyph, the title, a count, and a quiet line at the right end."""

    def __init__(self, title: str, glyph: bool = False, end_text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.glyph = Box(bg="accent", line="accent_wash/canvas", radius=6, line_w=2)
        self.glyph.setFixedSize(12, 12)
        lay.addWidget(self.glyph)
        self.glyph.setVisible(glyph)
        self.title = Text(title, theme.FS_H, 600, "text", wrap=False, letter_spacing=-0.01)
        lay.addWidget(self.title)
        self.count = Badge(0)
        lay.addWidget(self.count)
        lay.addStretch(1)
        self.end = Text(end_text, theme.FS_SMALL, 400, "text_3", wrap=False)
        lay.addWidget(self.end)
        self._glyph_on = glyph


class EmptyNote(Box):
    """``.ov-empty``: a quiet filled box with an icon and a sentence."""

    def __init__(self, text: str, icon: str = "", mark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="fill_1/canvas", radius=12, parent=parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        if mark:
            lay.addWidget(Mark(18), 0, Qt.AlignmentFlag.AlignVCenter)
        elif icon:
            ico = QLabel()
            ico.setPixmap(icons.pixmap(icon, theme.tokens().text_4, 16))
            lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = Text(text, theme.FS_SMALL, 400, "text_3", 1.5)
        lay.addWidget(self.text, 1)


class KeyButton(LayoutButton):
    """A small button with a key cap at its end (``.btn.sm`` + ``.key``)."""

    def __init__(self, text: str, kind: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.setAutoDefault(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.label = QLabel(text)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.label)
        self.kbd = Kbd("")
        self.kbd.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.kbd.hide()
        lay.addWidget(self.kbd)
        self.setFixedHeight(26)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.setProperty("kind", kind)
        self.label.setFont(theme.font_ui(theme.FS_SMALL, 600 if kind == "primary" else 400))
        theme.tint(self.label, fg="accent_ink" if kind == "primary" else "text_2" if kind == "ghost" else "text")
        if kind == "primary":
            self.set_look(bg="accent", hover="accent_2", radius=theme.R2)
        elif kind == "ghost":
            self.set_look(hover="fill_2/canvas", radius=theme.R2)
        else:
            self.set_look(bg="fill_2/canvas", hover="fill_3/canvas", radius=theme.R2)

    def set_key(self, n: Any) -> None:
        """The cap: a number or a letter; None hides it."""
        self.kbd.setText(str(n) if n else "")
        self.kbd.setVisible(bool(n))
