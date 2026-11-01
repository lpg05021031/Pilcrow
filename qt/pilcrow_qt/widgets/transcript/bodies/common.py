"""Shared pieces of the widgets the conversation hosts (the permission card, the question and plan
forms): rows, surfaces, the `.badge` and caption rules of transcript.css as small widgets, plus the
error-text helpers of ToolCard.tsx the canvas bodies read.

Everything paints from the theme tokens: a label takes its colour from ``theme.tint``, a pill or a
ring is painted; no stylesheet carries a colour.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .... import markdown, theme
from ...ui import bits
from ...ui.signals import bind

Tokens = theme.Tokens
Json = Dict[str, Any]


# ---------------------------------------------------------------- styling helpers


def mono_family() -> str:
    return theme.font_mono().family()


def vbox(w: QWidget, margins: tuple = (0, 0, 0, 0), spacing: int = 8) -> QVBoxLayout:
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(w: QWidget, margins: tuple = (0, 0, 0, 0), spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


class Row(QWidget):
    """A horizontal line of parts (``.row``), vertically centred."""

    def __init__(self, spacing: int = 8, margins: tuple = (0, 0, 0, 0), parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = hbox(self, margins, spacing)

    def add(self, w: Optional[QWidget], stretch: int = 0) -> Optional[QWidget]:
        if w is not None:
            self.lay.addWidget(w, stretch, Qt.AlignmentFlag.AlignVCenter)
        return w

    def stretch(self, n: int = 1) -> None:
        self.lay.addStretch(n)


class Surface(QFrame):
    """A flat rounded panel painted from tokens; ``bg`` and ``border`` are token names ('inset', 'hair').
    A border of ``"light:hair"`` is drawn in the light theme only (the inset wells of transcript.css
    carry their hairline only on a white page)."""

    def __init__(
        self,
        bg: str = "inset",
        border: str = "hair",
        radius: int = theme.R3,
        margins: tuple = (12, 10, 12, 10),
        spacing: int = 6,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.bg, self.border, self.radius = bg, border, radius
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.lay = vbox(self, margins, spacing)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def add(self, w: Optional[QWidget], stretch: int = 0) -> Optional[QWidget]:
        if w is not None:
            self.lay.addWidget(w, stretch)
        return w

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = self.border
        if border.startswith("light:"):
            border = "" if t.dark else border[6:]
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5) if border else QRectF(self.rect())
        if border:
            p.setPen(QPen(t.color(border), 1))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        if self.bg:
            p.setBrush(t.color(self.bg))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, self.radius, self.radius)


class NeedsDot(QWidget):
    """`.wait-dot`: the accent dot a tool's status column shows while Claude waits for the person; the
    question and plan cards and a permission card of its own open with it."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color("accent_wash"))
        p.drawEllipse(QRectF(0.5, 0.5, 13, 13))
        p.setBrush(t.color("accent"))
        p.drawEllipse(QRectF(3.5, 3.5, 7, 7))


def caption(text: str, live: bool) -> QLabel:
    """The small semibold line that opens a card: accent while it waits on the person, text-3 after."""
    lb = QLabel(text)
    lb.setFont(theme.font_ui(theme.FS_SMALL, 600))
    return theme.tint(lb, fg="accent_text" if live else "text_3")


class Hairline(QFrame):
    """A one-pixel rule in the ``hair`` token (the ``box-shadow: inset 0 1px 0 var(--hair)`` of the web)."""

    def __init__(self, token: str = "hair", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._token = token
        self.setFixedHeight(1)
        self.setFrameShape(QFrame.Shape.NoFrame)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.tokens().color(self._token))


# ---------------------------------------------------------------- text


def label(text: str, tone: str = "", size: str = "", mono: bool = False, wrap: bool = False) -> QLabel:
    return bits.label(text, tone=tone, size=size, mono=mono, wrap=wrap)


def faint(text: str, size: str = "small", wrap: bool = False) -> QLabel:
    """`.faint`: text_3."""
    return bits.label(text, tone="faint", size=size, wrap=wrap)


def note(text: str, wrap: bool = True) -> QLabel:
    """`.tool-note`: small text in text_2."""
    return bits.label(text, tone="muted", size="small", wrap=wrap)


def rich(body: str, mono: bool = False, size: float = 0, color: str = "", wrap: bool = True) -> QLabel:
    """A rich-text label for HTML built from Markdown or code; selectable, wrapping, themed."""
    lb = QLabel()
    lb.setTextFormat(Qt.TextFormat.RichText)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    lb.setOpenExternalLinks(False)
    lb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    lb.setFont(theme.font_mono(size or 12) if mono else theme.font_ui(size or theme.FS_UI))
    theme.tint(lb, fg=color or "text")
    lb.setText(body)
    return lb


class LinkLabel(QLabel):
    """`.link`: quiet text in text_2 that brightens on hover and acts on click."""

    clicked = Signal()

    def __init__(self, text: str, mono: bool = False, size: float = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self.setFont(theme.font_mono(size or 12) if mono else theme.font_ui(size or theme.FS_UI))
        theme.tint(self, fg="text_2")

    def enterEvent(self, e) -> None:
        theme.tint(self, fg="text")
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        theme.tint(self, fg="text_2")
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


#: a badge's tone: its ink and its wash
BADGE_TONES = {
    "err": ("err", "err_wash"),
    "ok": ("ok", "ok_wash"),
    "info": ("info", "info_wash"),
    "accent": ("accent_text", "accent_wash"),
    "plan": ("plan", "plan_wash"),
}


class Badge(QLabel):
    """`.badge`: a small pill; tones '', 'err', 'ok', 'info', 'accent', 'plan'. The pill is painted,
    the words are the label's in the tone's ink."""

    def __init__(self, text: str, tone: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(19)
        self.setContentsMargins(7, 0, 7, 0)
        self.setFont(theme.font_ui(11, 600))
        fg, self._wash = BADGE_TONES.get(tone, ("text_2", "fill_2"))
        theme.tint(self, fg=fg)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(getattr(t, self._wash), t.canvas))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        p.end()
        super().paintEvent(e)


def code_more(text: str, on_click: Callable[[], None]) -> QPushButton:
    """`.code-more`: the quiet "Show all N lines" control under a clipped block."""
    b = bits.button(text, kind="more", size="sm", on_click=on_click)
    b.setAccessibleName(text)
    return b


# ---------------------------------------------------------------- errors (ToolCard.tsx)


def plain_error(text: str) -> Optional[str]:
    """Internal failures that have a plain explanation; the raw text stays in the tooltip."""
    if re.search(r"Tool permission (request failed|stream closed)", text, re.I):
        return (
            "The approval request was cut off when Claude Code restarted, so this step did not run. "
            "Ask Claude to try again."
        )
    return None


def unwrap_error(text: str) -> str:
    """Claude Code wraps tool failures in <tool_use_error> tags meant for the model; people only need the message."""
    return re.sub(r"^\s*<tool_use_error>([\s\S]*?)</tool_use_error>\s*$", r"\1", text).strip()


def split_error(text: str) -> Dict[str, str]:
    """What went wrong (one calm line), then the raw text only if wanted; tool runners' prefixes say nothing new."""
    clean = re.sub(r"^Error executing tool [\w-]+:\s*", "", unwrap_error(text), flags=re.I)
    clean = re.sub(r"^Error:\s*", "", clean, flags=re.I)
    lines = clean.split("\n")
    title = (lines[0] or "Failed").strip()
    rest = "\n".join(lines[1:]).strip()
    return {"title": title[:177] + "…" if len(title) > 180 else title, "rest": rest, "full": clean}


def markdown_label(text: str, cwd: str = "", compact: bool = False) -> QLabel:
    """A Markdown block as rich text (the <Markdown> component), for results and reports."""
    return rich(markdown.render_html(text, base_dir=cwd or None, compact=compact))
