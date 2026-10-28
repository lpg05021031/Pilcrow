"""Shared pieces of the tool bodies: the `.tool-section`, `.tool-pre`, `.badge`, `.tool-error` rules of
transcript.css as small widgets, plus the error-text helpers of ToolCard.tsx.

Everything paints from the theme tokens. Widgets that need a colour of their own get a per-widget
stylesheet through :func:`restyle`, which re-applies it when the theme changes.
"""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .... import icons, theme
from ....lib.fmt import truncate
from ....lib.transcript_model import ToolItem
from ....store import store
from ...ui import bits
from ...ui.collapse import Collapse
from ...ui.signals import bind

Tokens = theme.Tokens
Json = Dict[str, Any]


# ---------------------------------------------------------------- styling helpers


def restyle(w: QWidget, css: Callable[[Tokens], str]) -> None:
    """Give a widget its own stylesheet built from the tokens, re-applied when the theme changes."""
    w.setStyleSheet(css(theme.tokens()))
    bind(theme.theme.changed, w, lambda t: w.setStyleSheet(css(t)))


def mono_family() -> str:
    return theme.font_mono().family()


def ui_family() -> str:
    return theme.font_ui().family()


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


class Section(QWidget):
    """`.tool-section`: a column of body parts with an 8px gap."""

    def __init__(self, spacing: int = 8, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = vbox(self, spacing=spacing)

    def add(self, w: Optional[QWidget], stretch: int = 0) -> Optional[QWidget]:
        if w is not None:
            self.lay.addWidget(w, stretch)
        return w


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

    def paintEvent(self, _e) -> None:  # noqa: N802
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


class Hairline(QFrame):
    """A one-pixel rule in the ``hair`` token (the ``box-shadow: inset 0 1px 0 var(--hair)`` of the web)."""

    def __init__(self, token: str = "hair", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._token = token
        self.setFixedHeight(1)
        self.setFrameShape(QFrame.Shape.NoFrame)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
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


def tool_label(text: str) -> QLabel:
    """`.tool-label`: a micro, semibold heading in text_3 ("Input", "Output", "Result")."""
    lb = bits.label(text, tone="faint", size="micro")
    restyle(lb, lambda t: f"QLabel {{ color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600; }}")
    return lb


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
    fam = mono_family() if mono else ui_family()
    px = size or (12 if mono else theme.FS_UI)

    def css(t: Tokens) -> str:
        c = getattr(t, color) if color else t.text
        return f"QLabel {{ font-family: '{fam}'; font-size: {px}px; color: {c}; }}"

    restyle(lb, css)
    lb.setText(body)
    return lb


def pre_html(text: str, limit: int = 60000, line_height: float = 1.55, size: int = 12) -> str:
    """Preformatted text on the web's line pitch (``.tool-pre``/``.bash-out``: 12px on 1.55 lines)."""
    lh = int(size * line_height + 0.5)
    return (
        f'<div style="white-space:pre-wrap;line-height:{lh}px">{html.escape(truncate(text, limit)) or "&nbsp;"}</div>'
    )


def pre(text: str, err: bool = False, limit: int = 60000) -> Surface:
    """`.tool-pre`: mono text on an inset panel; errors read in text_2 (the head already says it failed)."""
    s = Surface("inset", "light:hair", theme.R3, margins=(12, 10, 12, 10))
    s.add(rich(pre_html(text, limit), mono=True, size=12, color="text_2" if err else "text"))
    return s


def json_pretty(v: Any) -> str:
    try:
        return json.dumps(v, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


class ElideLabel(QWidget):
    """One line of text that shrinks with its row and elides on the right, painted in a token colour."""

    def __init__(
        self,
        text: str = "",
        color: str = "text",
        mono: bool = False,
        size: float = 0,
        strike: bool = False,
        weight: int = 400,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._text, self._color, self._mono, self._size, self._strike, self._weight = (
            text,
            color,
            mono,
            size,
            strike,
            weight,
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._apply_font()
        bind(theme.theme.changed, self, lambda _t: self.update())

    def _apply_font(self) -> None:
        if self._mono:
            f = theme.font_mono(self._size or 12.5)
        else:
            f = theme.font_ui(self._size or theme.FS_UI)
        f.setWeight(QFont.Weight(self._weight))
        f.setStrikeOut(self._strike)
        self._font = f
        self.setFixedHeight(QFontMetrics(f).height() + 4)
        self.updateGeometry()

    def set_text(
        self, text: str, color: Optional[str] = None, mono: Optional[bool] = None, strike: Optional[bool] = None
    ) -> None:
        self._text = text
        if color is not None:
            self._color = color
        if mono is not None:
            self._mono = mono
        if strike is not None:
            self._strike = strike
        self._apply_font()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self):  # noqa: N802
        fm = QFontMetrics(self._font)
        return QSize(fm.horizontalAdvance(self._text) + 2, fm.height() + 4)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(24, QFontMetrics(self._font).height() + 4)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(self._font)
        p.setPen(theme.tokens().color(self._color))
        fm = QFontMetrics(self._font)
        text = fm.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width() - 2)
        p.drawText(self.rect().adjusted(1, 0, -1, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class LinkLabel(QLabel):
    """`.link`: quiet text in text_2 that brightens on hover and acts on click."""

    clicked = Signal()

    def __init__(self, text: str, mono: bool = False, size: float = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        fam = mono_family() if mono else ui_family()
        px = size or (12 if mono else theme.FS_UI)
        restyle(
            self,
            lambda t: (
                f"QLabel {{ color: {t.text_2}; font-family: '{fam}'; font-size: {px}px; }} "
                f"QLabel:hover {{ color: {t.text}; }}"
            ),
        )

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class IconLabel(QWidget):
    """A tinted Lucide icon that repaints from the tokens when the theme changes."""

    def __init__(self, name: str, color: str = "text_2", size: int = 14, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name, self._color, self._size = name, color, size
        self.setFixedSize(size, size)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set(self, name: str, color: Optional[str] = None) -> None:
        self._name = name
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        pm = icons.pixmap(self._name, getattr(theme.tokens(), self._color), self._size)
        p.drawPixmap(0, 0, pm)


class Badge(QLabel):
    """`.badge`: a small pill; tones '', 'err', 'ok', 'info', 'accent', 'plan'."""

    def __init__(self, text: str, tone: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(19)

        def css(t: Tokens) -> str:
            fg, bg = {
                "err": (t.err, t.err_wash),
                "ok": (t.ok, t.ok_wash),
                "info": (t.info, t.info_wash),
                "accent": (t.accent_text, t.accent_wash),
                "plan": (t.plan, t.plan_wash),
            }.get(tone, (t.text_2, t.fill_2))
            return (
                f"QLabel {{ color: {fg}; background: {theme.blend(bg, t.canvas).name()}; border-radius: 9px; "
                f"padding: 0 7px; font-size: 11px; font-weight: 600; }}"
            )

        restyle(self, css)


def code_more(text: str, on_click: Callable[[], None]) -> QPushButton:
    """`.code-more`: the quiet "Show all N lines" control under a clipped block."""
    b = bits.button(text, kind="ghost", size="sm", on_click=on_click)
    b.setAccessibleName(text)
    restyle(
        b,
        lambda t: (
            f"QPushButton {{ color: {t.text_3}; padding: 3px 8px; font-size: {theme.FS_SMALL}px; }} "
            f"QPushButton:hover {{ color: {t.text}; background: {theme.blend(t.fill_2, t.canvas).name()}; }}"
        ),
    )
    return b


class Toggle(QWidget):
    """A chevron + label that opens a Collapse beneath it (`.agent-prompt-toggle`, `.wf-foot-btn`,
    `.tool-error-more`)."""

    def __init__(
        self, text: str, builder: Callable[[], QWidget], open_: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        lay = vbox(self, spacing=4)
        self.btn = QPushButton()
        self.btn.setFlat(True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setAccessibleName(text)
        self._text = text
        self._open = open_
        restyle(
            self.btn,
            lambda t: (
                f"QPushButton {{ border: none; background: transparent; color: {t.text_3}; "
                f"padding: 2px 6px 2px 2px; text-align: left; font-size: {theme.FS_SMALL}px; }} "
                f"QPushButton:hover {{ color: {t.text}; background: {theme.blend(t.fill_1, t.canvas).name()}; }}"
            ),
        )
        self.body = Collapse(builder, open_)
        self.btn.clicked.connect(self.toggle)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.body)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_text(self, text: str) -> None:
        self._text = text
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.btn.setText(self._text)
        self.btn.setIcon(icons.icon("chevron-down" if self._open else "chevron-right", t.text_3, 12, hover=t.text))
        self.btn.setIconSize(QSize(12, 12))

    def toggle(self) -> None:
        self._open = not self._open
        self.body.set_open(self._open)
        self._paint()


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


class ErrorBlock(QWidget):
    """`.tool-error`: the first line of a failure, an optional fix, and the details behind a toggle."""

    def __init__(
        self,
        text: str,
        tool_input: Optional[Json] = None,
        fix: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        lay = vbox(self, spacing=4)
        parts = split_error(text)
        title = bits.label(parts["title"], tone="muted", wrap=True)
        lay.addWidget(title)
        if fix is not None:
            lay.addWidget(fix)
        has_input = isinstance(tool_input, dict) and len(tool_input) > 0
        has_more = bool(parts["rest"]) or parts["title"].endswith("…") or has_input

        def detail() -> QWidget:
            d = Section()
            if parts["rest"] or parts["title"].endswith("…"):
                d.add(pre(parts["full"], err=True))
            if has_input:
                d.add(tool_label("Input"))
                d.add(pre(json_pretty(tool_input)))
            return d

        if has_more:
            self.more = Toggle("Details", detail)
            lay.addWidget(self.more)


class McpFix(QWidget):
    """For a failed MCP tool: the server's live status decides whether a sign-in or a reconnect fixes it."""

    PATTERN = (
        r"mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|"
        r"token (has )?expired|not connected|connection (closed|refused|reset)|ECONNREFUSED|"
        r"MCP server .* (failed|unavailable)"
    )
    AUTHY = (
        r"mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|"
        r"token (has )?expired"
    )
    DOWN = r"not connected|connection (closed|refused|reset)|ECONNREFUSED|MCP server .* (failed|unavailable)"

    @classmethod
    def build(cls, item: ToolItem, session_id: str) -> Optional[McpFix]:
        parts = item.name.split("__")
        server_key = parts[1] if len(parts) > 1 else ""
        rt = store.rt.get(session_id)
        live: List[Json] = list((rt.get("mcp") if rt else None) or [])
        text = item.result.text if item.result else ""
        match = next((m for m in live if m.get("name") == server_key), None) or next(
            (m for m in live if m.get("name") and m["name"] in text), None
        )
        name = (match or {}).get("name") or server_key
        status = (match or {}).get("status")
        authy = bool(re.search(cls.AUTHY, text, re.I)) or status == "needs-auth"
        down = bool(re.search(cls.DOWN, text, re.I)) or status == "failed"
        textual = bool(re.search(cls.PATTERN, text, re.I))
        # Only an error that talks about auth or connectivity gets a fix; a validation error is Claude's to sort out.
        if not name or not textual or (not authy and not down):
            return None
        if status == "connected" and not authy:
            return None
        return cls(name, authy, session_id)

    def __init__(self, name: str, authy: bool, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = hbox(self, spacing=8)
        self.name, self.session_id = name, session_id
        if authy:
            b = bits.button(f"Sign in to {name}", kind="primary", size="sm")
            b.setAccessibleName(b.text())
            b.clicked.connect(lambda: store.set_dialog("mcpAuth", {"sessionId": session_id, "name": name}))
        else:
            b = bits.button(f"Reconnect {name}", size="sm")
            b.setAccessibleName(b.text())
            b.clicked.connect(self._retry)
        self.btn = b
        row.addWidget(b)
        row.addWidget(faint("then ask Claude to try again"))
        row.addStretch(1)

    def _retry(self) -> None:
        self.btn.setEnabled(False)

        def ok(_r: Any) -> None:
            store.toast(f"Reconnected {self.name} — ask Claude to try again", "success")
            self.btn.setEnabled(True)

        store.call("session.mcp.reconnect", {"id": self.session_id, "name": self.name}).then(ok).catch(
            lambda _e: self.btn.setEnabled(True)
        )


def image_widgets(srcs: List[str], max_w: int = 520) -> List[QWidget]:
    """`.tool-img`: result images, scaled to the column; a click opens the lightbox."""
    out: List[QWidget] = []
    for src in srcs:
        pm = QPixmap()
        m = re.match(r"^data:[^;]+;base64,(.*)$", src, re.S)
        if m:
            try:
                pm.loadFromData(base64.b64decode(m.group(1)))
            except (ValueError, TypeError):
                pass
        if pm.isNull():
            continue
        lb = QLabel()
        lb.setPixmap(pm.scaledToWidth(min(max_w, pm.width()), Qt.TransformationMode.SmoothTransformation))
        lb.setAccessibleName("Tool output")
        lb.setToolTip("Open")
        bits.Clickable(lb).clicked.connect(lambda s=src: store.open_lightbox(s))
        out.append(lb)
    return out


def result_text(
    item: ToolItem, label_: bool = False, tool_input: Optional[Json] = None, session_id: str = ""
) -> Optional[QWidget]:
    """ResultText in ToolCard.tsx: the output, or the error read in two steps, plus any images."""
    r = item.result
    if r is None:
        if item.status == "running":
            return faint("Running…")
        return None
    sec = Section()
    if label_ and not r.is_error:
        sec.add(tool_label("Output"))
    if r.text:
        if r.is_error:
            plain = plain_error(r.text)
            if plain:
                s = Surface("fill_1", "", theme.R1, margins=(12, 8, 12, 8))
                lb = note(plain)
                lb.setToolTip(r.text)
                s.add(lb)
                sec.add(s)
            else:
                fix = McpFix.build(item, session_id) if session_id and item.name.startswith("mcp__") else None
                sec.add(ErrorBlock(r.text, tool_input, fix))
        else:
            sec.add(pre(r.text))
    for w in image_widgets(r.images):
        sec.add(w)
    return sec


def clear_layout(lay) -> None:
    """Remove and delete every widget in a layout (for re-rendering a host in place)."""
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
