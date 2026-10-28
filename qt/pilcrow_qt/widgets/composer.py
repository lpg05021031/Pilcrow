"""The composer (a port of web/src/components/Composer.tsx, with the model helpers of lib/models.ts).

The card at the foot of a session: an auto-sizing field, attachments, the @ file and / command
popups, the queue of messages held while Claude works, the permission-mode and model menus, the
context ring and the one send/stop button. Drafts and histories live here for as long as the app
runs, one per session, exactly like the module-level maps in the React version.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QMimeData, QPoint, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import bytes_, short_model
from ..lib.fuzzy import fuzzy_filter
from ..store import store
from .ui.bits import ContextRing, IconButton, context_label
from .ui.menu import build_menu
from .ui.signals import bind

Json = Dict[str, Any]

MODES: List[Json] = [
    {
        "id": "default",
        "label": "Ask before edits",
        "hint": "Asks before changing files or running commands",
        "icon": "shield-check",
        "tone": "",
    },
    {
        "id": "acceptEdits",
        "label": "Edit automatically",
        "hint": "File edits are applied without asking",
        "icon": "file-pen",
        "tone": "accent",
    },
    {
        "id": "plan",
        "label": "Plan mode",
        "hint": "Explores and proposes a plan; changes nothing",
        "icon": "map",
        "tone": "plan",
    },
    {
        "id": "auto",
        "label": "Auto",
        "hint": "Approves routine actions, asks about risky ones",
        "icon": "zap",
        "tone": "info",
    },
    {
        "id": "bypassPermissions",
        "label": "Bypass permissions",
        "hint": "Runs everything without asking — sandboxes only",
        "icon": "shield-off",
        "tone": "err",
    },
]

BUILTIN_COMMANDS: List[Json] = [
    {"name": "compact", "description": "Summarize the conversation to free up context"},
    {"name": "clear", "description": "Start the conversation over"},
    {"name": "context", "description": "Show what is using the context window"},
    {"name": "usage", "description": "Show cost and token usage"},
    {"name": "init", "description": "Write a CLAUDE.md for this project"},
    {"name": "code-review", "description": "Review the current changes"},
    {"name": "model", "description": "Change the model"},
]

#: Aliases Claude Code always understands; used until a session has reported its own list (lib/models.ts).
ALIASES: List[Json] = [
    {"value": "opus", "displayName": "Opus", "description": "Most capable"},
    {"value": "sonnet", "displayName": "Sonnet", "description": "Fast and capable"},
    {"value": "haiku", "displayName": "Haiku", "description": "Fastest"},
]

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
MAX_IMAGE = 8 * 1024 * 1024
MAX_TEXT = 400 * 1024

_drafts: Dict[str, str] = {}
_histories: Dict[str, List[str]] = {}
_file_index_cache: Dict[str, Tuple[float, List[str]]] = {}


# ---------------------------------------------------------------- lib/models.ts


def _trim_claude(s: str) -> str:
    """ "Claude Opus 5.5 (1M context)" → "Opus 5.5 (1M context)": every model here is Claude; the chip is small."""
    return re.sub(r"^Claude\s+", "", s, flags=re.I)


def gateway_state() -> Json:
    """The endpoint's model list as the UI should show it: ``{"models", "loading", "error", "base"}``."""
    g = store.gateway_models
    if g == "loading":
        return {"models": [], "loading": True, "error": None, "base": None}
    if not isinstance(g, dict):
        return {"models": [], "loading": False, "error": None, "base": None}
    ok = bool(g.get("ok"))
    error = None if ok or g.get("noCredential") else (g.get("error") or "Could not list the models")
    return {
        "models": (g.get("models") or []) if ok else [],
        "loading": False,
        "error": error,
        "base": g.get("base") or None,
    }


def model_label(value: str, gateway: List[Json], aliases: Optional[List[Json]] = None) -> str:
    """A short, human name for a model value: the endpoint's display name, Claude Code's alias name, or a tidied ID."""
    for m in gateway:
        if m.get("id") == value and m.get("displayName"):
            return _trim_claude(str(m["displayName"]))
    for a in aliases if aliases is not None else ALIASES:
        if a.get("value") == value and a.get("displayName"):
            return str(a["displayName"])
    return short_model(value) if re.search(r"claude-", value, re.I) else value


def model_options(gateway: List[Json]) -> List[Json]:
    """Options for model pickers: Default, then the gateway's own models (Claude Code's aliases are not offered)."""
    out: List[Json] = [{"value": "", "label": "Default", "hint": "Claude Code's configured model"}]
    for m in gateway:
        name = m.get("displayName")
        out.append(
            {
                "value": m["id"],
                "label": _trim_claude(str(name)) if name else m["id"],
                "hint": m["id"] if name else None,
                "mono": True,
            }
        )
    return out


def effort_name(level: str) -> str:
    return "Extra high" if level == "xhigh" else level[:1].upper() + level[1:]


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


# ---------------------------------------------------------------- the field


class ComposerEdit(QPlainTextEdit):
    """The text field. Keys go to the owning Composer first; the IME guard keeps Enter from sending mid-composition."""

    def __init__(self, owner: Composer) -> None:
        super().__init__()
        self._owner = owner
        self._composing = False
        self.setAcceptDrops(True)
        self.setTabChangesFocus(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setAccessibleName("Message")
        self.document().setDocumentMargin(0)

    @property
    def composing(self) -> bool:
        return self._composing

    def inputMethodEvent(self, e) -> None:  # noqa: N802
        self._composing = bool(e.preeditString())
        super().inputMethodEvent(e)

    def event(self, e: QEvent) -> bool:
        # The window owns an Escape shortcut; claim the key here so a busy session can be stopped from the field.
        if (
            e.type() == QEvent.Type.ShortcutOverride
            and isinstance(e, QKeyEvent)
            and e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        ):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if self._owner.handle_key(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        super().mouseReleaseEvent(e)
        self._owner.detect_popup()

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self._owner.refresh_foot()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._owner.refresh_foot()
        QTimer.singleShot(120, self._owner.close_popup_if_unfocused)

    # ---- paste and drop
    def canInsertFromMimeData(self, src: QMimeData) -> bool:  # noqa: N802
        return src.hasImage() or src.hasUrls() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src: QMimeData) -> None:  # noqa: N802
        if src.hasImage():
            self._owner.add_image(QImage(src.imageData()), "pasted image")
            return
        if src.hasUrls():
            paths = [u.toLocalFile() for u in src.urls() if u.isLocalFile()]
            if paths:
                self._owner.add_files(paths)
                return
        super().insertFromMimeData(src)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            self._owner.set_dragging(True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._owner.set_dragging(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._owner.set_dragging(False)
        super().dropEvent(e)


# ---------------------------------------------------------------- small buttons


class LayoutButton(QPushButton):
    """A QPushButton built from child widgets in a layout: its size comes from the layout, not from its own text."""

    def sizeHint(self):  # noqa: N802
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()


class ChipButton(LayoutButton):
    """A quiet pill with an optional icon, label, sub label and chevron (``.cbtn``)."""

    def __init__(self, tooltip: str = "", chevron: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = ""
        self._tone = ""
        self._chevron = chevron
        self.setFlat(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.ico = QLabel()
        self.ico.hide()
        lay.addWidget(self.ico)
        self.text_lb = QLabel()
        lay.addWidget(self.text_lb)
        self.sub_lb = QLabel()
        self.sub_lb.hide()
        lay.addWidget(self.sub_lb)
        self.chev = QLabel()
        self.chev.setVisible(chevron)
        lay.addWidget(self.chev)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set(self, text: str, icon: str = "", sub: str = "", tone: str = "") -> None:
        self.text_lb.setText(text)
        self.text_lb.setVisible(bool(text))
        self.sub_lb.setText(f"· {sub}" if sub else "")
        self.sub_lb.setVisible(bool(sub))
        self._icon, self._tone = icon, tone
        self.ico.setVisible(bool(icon))
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        color = {"accent": t.accent_text, "plan": t.plan, "info": t.info, "err": t.err, "set": t.text}.get(
            self._tone, t.text_2
        )
        if self._icon:
            self.ico.setPixmap(icons.pixmap(self._icon, color, 14))
        self.chev.setPixmap(icons.pixmap("chevron-down", color, 12))
        self.text_lb.setStyleSheet(f"color: {color}; font-size: {theme.FS_SMALL}px; font-weight: 500;")
        self.sub_lb.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
        fill = theme.blend(t.fill_2, t.raised).name()
        self.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; background: transparent; }}"
            f" QPushButton:hover {{ background: {fill}; }}"
        )


class SendButton(QPushButton):
    """Send ↔ stop: one round button. Empty = quiet outline, typed = orange arrow, working = stop square."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = "idle"
        self.setFixedSize(32, 32)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self.set_state(False, False, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, ready: bool, stop: bool, send_with_enter: bool) -> None:
        self.state = "stop" if stop else "ready" if ready else "idle"
        self.setEnabled(ready or stop)
        tip = "Stop (Esc)" if stop else "Send (Enter)" if send_with_enter else "Send (Ctrl+Enter)"
        self.setToolTip(tip)
        self.setAccessibleName("Stop" if stop else "Send")
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, 31, 31)
        if self.state == "stop":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.text))
            p.drawEllipse(r)
            p.setBrush(QColor(t.canvas))
            p.drawRoundedRect(QRectF(10.5, 10.5, 11, 11), 2, 2)
            return
        if self.state == "ready":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_2 if self.underMouse() else t.accent))
            p.drawEllipse(r)
            ink = t.accent_ink
        else:
            p.setPen(QPen(theme.blend(t.hair_2, t.raised), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
            ink = t.text_3
        pm = icons.pixmap("arrow-up", ink, 16, stroke=2.25)
        p.drawPixmap(8, 8, 16, 16, pm)


# ---------------------------------------------------------------- the @ and / popup


class SuggestPopup(QFrame):
    """The list of files or commands above the field (``.suggest``). Keyboard-driven by the Composer."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("suggest")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.items: List[Json] = []
        self.index = 0
        self.on_pick: Optional[Callable[[Json], None]] = None
        self.on_hover: Optional[Callable[[int], None]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
        self.head = QLabel()
        self.head.setContentsMargins(8, 4, 8, 6)
        lay.addWidget(self.head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.list = QVBoxLayout(self.host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(0)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll)
        self.rows: List[SuggestRow] = []
        self.hide()
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        line = theme.blend(t.hair_2, t.raised).name()
        self.setStyleSheet(
            f"#suggest {{ background: {t.raised}; border: 1px solid {line}; border-radius: {theme.R3}px; }}"
        )
        self.head.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600;")

    def set_items(self, head: str, items: List[Json], index: int) -> None:
        self.head.setText(head)
        self.items = items
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.rows = []
        name_ch = max([8] + [len(x["label"]) for x in items if x["kind"] == "cmd"])
        name_ch = min(30, name_ch)
        for i, it in enumerate(items[:40]):
            row = SuggestRow(it, name_ch)
            row.clicked.connect(lambda _c=False, x=it: self.on_pick and self.on_pick(x))
            row.hovered.connect(lambda k=i: self.on_hover and self.on_hover(k))
            self.list.addWidget(row)
            self.rows.append(row)
        self.list.addStretch(1)
        self.set_index(index)
        rows_h = 32 * min(len(items), 40)
        self.scroll.setFixedHeight(min(320, rows_h))

    def set_index(self, index: int) -> None:
        self.index = index
        for i, row in enumerate(self.rows):
            row.set_active(i == index)
        if 0 <= index < len(self.rows):
            self.scroll.ensureWidgetVisible(self.rows[index], 0, 0)

    def total_height(self) -> int:
        return self.head.sizeHint().height() + self.scroll.height() + 12


class SuggestRow(QPushButton):
    """One suggestion: an icon for files and folders, the name in mono, the hint in a second column."""

    hovered = Signal()

    def __init__(self, item: Json, name_ch: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self._active = False
        self.setFlat(True)
        self.setFixedHeight(32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(item["label"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(10)
        kind = item["kind"]
        if kind in ("dir", "file"):
            ico = QLabel()
            ico.setPixmap(icons.pixmap("folder" if kind == "dir" else "file", theme.tokens().text_3, 14))
            lay.addWidget(ico)
        self.name = QLabel()
        self.name.setFont(theme.font_mono(12.5))
        self.name.setTextFormat(Qt.TextFormat.RichText)
        if kind == "cmd":
            self.name.setMinimumWidth(int((name_ch + 1) * self.name.fontMetrics().horizontalAdvance("m")))
        lay.addWidget(self.name)
        self.hint = QLabel(str(item.get("hint") or ""))
        self.hint.setVisible(bool(item.get("hint")))
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.hint, 1)
        self._paint()

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self._paint()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.hovered.emit()
        super().enterEvent(e)

    def _paint(self) -> None:
        from html import escape

        t = theme.tokens()
        label = self.item["label"]
        if self.item["kind"] == "cmd":
            self.name.setText(f"<span style='color:{t.text_3}'>/</span>{escape(label[1:])}")
        else:
            self.name.setText(escape(label))
        self.name.setStyleSheet(f"color: {t.text};")
        self.hint.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
        bg = theme.blend(t.fill_2, t.raised).name() if self._active else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; background: {bg}; text-align: left; }}"
        )


# ---------------------------------------------------------------- attachments


class ImageChip(QWidget):
    """A 60px thumbnail with a remove button in its corner (``.attach-img``)."""

    def __init__(self, img: Json, on_remove: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pm: QPixmap = img["pixmap"]
        self.setFixedSize(60, 60)
        self.setToolTip(f"{img['name']} · {bytes_(img['size'])}")
        x = QPushButton(self)
        x.setProperty("icon-only", "true")
        x.setIcon(icons.icon("x", theme.tokens().accent_ink, 11))
        x.setFixedSize(18, 18)
        x.move(38, 4)
        x.setAccessibleName("Remove image")
        x.setToolTip("Remove image")
        x.setStyleSheet(f"QPushButton {{ background: {theme.tokens().scrim}; border-radius: 9px; border: none; }}")
        x.clicked.connect(on_remove)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 60, 60), theme.R3, theme.R3)
        p.setClipPath(path)
        pm = self._pm.scaled(
            60, 60, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        p.drawPixmap((60 - pm.width()) // 2, (60 - pm.height()) // 2, pm)
        p.setClipping(False)
        p.setPen(QPen(theme.blend(theme.tokens().hair_2, theme.tokens().raised), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, 59, 59), theme.R3, theme.R3)


class FileChip(QWidget):
    """A text attachment: file icon, name, remove (``.attach-file``)."""

    def __init__(self, f: Json, on_remove: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        t = theme.tokens()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.setMaximumWidth(260)
        self.setStyleSheet(
            f"FileChip {{ background: {theme.blend(t.fill_2, t.raised).name()}; border-radius: {theme.R2}px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(6)
        ico = QLabel()
        ico.setPixmap(icons.pixmap("file", t.text_2, 13))
        lay.addWidget(ico)
        name = QLabel(str(f["name"]))
        name.setStyleSheet(f"font-size: {theme.FS_SMALL}px;")
        lay.addWidget(name, 1)
        x = IconButton("x", "Remove file", 11)
        x.setFixedSize(18, 18)
        x.clicked.connect(on_remove)
        lay.addWidget(x)


# ---------------------------------------------------------------- the queue tray


class QueueTray(QFrame):
    """Messages waiting for the current step to finish. Each can be edited, sent right away, or dropped."""

    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.held: List[Json] = []
        self._key: List[tuple] = []
        self.editing: Optional[str] = None
        self.setObjectName("queue")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("Queued messages")
        self.setMaximumWidth(theme.COL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 6, 6, 4)
        self.lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 2, 8, 6)
        hl.setSpacing(6)
        self.head_ico = QLabel()
        hl.addWidget(self.head_ico)
        self.head_text = QLabel()
        hl.addWidget(self.head_text, 1)
        self.lay.addWidget(head)
        self.rows = QVBoxLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.lay.addLayout(self.rows)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        bg = _mix(t.raised, t.canvas, 0.7)  # color-mix(in srgb, var(--raised) 70%, var(--canvas))
        line = theme.blend(t.hair, t.canvas).name()
        self.setStyleSheet(
            f"#queue {{ background: {bg.name()}; border: 1px solid {line}; border-radius: {theme.R3}px; }}"
        )
        self.head_ico.setPixmap(icons.pixmap("clock-3", t.accent_text, 12))
        self.head_text.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600;")

    def set_held(self, held: List[Json], force: bool = False) -> None:
        """Rebuild the rows when the queue changed (a rebuild while a row is being edited would lose the typing)."""
        key = [(h.get("uuid"), h.get("text"), h.get("images"), tuple(h.get("files") or [])) for h in held]
        if not force and key == self._key:
            return
        self._key = key
        self.held = held
        n = len(held)
        self.head_text.setText(
            "Queued — Claude reads it after this step"
            if n == 1
            else f"{n} queued — Claude reads them in order after this step"
        )
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, h in enumerate(held):
            self.rows.addWidget(QueueItem(self, h, i + 1, editing=self.editing == h.get("uuid")))
        self.setVisible(n > 0)

    def start_edit(self, uuid: str) -> None:
        self.editing = uuid
        self.set_held(self.held, force=True)

    def save(self, h: Json, text: str) -> None:
        self.editing = None
        t = text.strip()
        if not t and not h.get("images") and not (h.get("files") or []):
            store.remove_held(self.sid, h["uuid"]).catch(lambda _e: None)
        elif t != h.get("text"):
            store.update_held(self.sid, h["uuid"], t).catch(lambda _e: None)
        else:
            self.set_held(self.held, force=True)

    def cancel_edit(self) -> None:
        self.editing = None
        self.set_held(self.held, force=True)


class QueueItem(QWidget):
    def __init__(self, tray: QueueTray, h: Json, n: int, editing: bool) -> None:
        super().__init__(tray)
        self.tray, self.h = tray, h
        t = theme.tokens()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fill = theme.blend(t.fill_1, t.canvas).name()
        self.setStyleSheet(
            f"QueueItem {{ border-radius: {theme.R2}px; background: {fill if editing else 'transparent'}; }}"
            f" QueueItem:hover {{ background: {fill}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 6, 5)
        lay.setSpacing(8)
        num = QLabel(str(n))
        num.setFixedWidth(18)
        num.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        num.setStyleSheet(f"color: {t.text_4}; font-size: 11px; font-weight: 600; margin-top: 3px;")
        lay.addWidget(num)
        if editing:
            self.edit = _QueueEdit(str(h.get("text") or ""), lambda text: tray.save(h, text), tray.cancel_edit)
            lay.addWidget(self.edit, 1)
            QTimer.singleShot(0, self.edit.setFocus)
        else:
            text = str(h.get("text") or "")
            lines = text.split("\n")
            shown = "\n".join(lines[:3]) + ("…" if len(lines) > 3 else "")
            self.text_btn = QPushButton(shown or "(no text)")
            self.text_btn.setFlat(True)
            self.text_btn.setToolTip("Click to edit")
            self.text_btn.setAccessibleName("Edit queued message")
            self.text_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.text_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            color = t.text_4 if not text else t.text_2
            self.text_btn.setStyleSheet(
                f"QPushButton {{ border: none; text-align: left; padding: 2px 0; color: {color};"
                f" background: transparent; }} QPushButton:hover {{ color: {t.text}; }}"
            )
            self.text_btn.clicked.connect(lambda: tray.start_edit(h["uuid"]))
            lay.addWidget(self.text_btn, 1)
            attach: List[str] = []
            if (h.get("images") or 0) > 0:
                attach.append(f"🖼 {h['images']}")
            attach.extend(f"📄 {f}" for f in (h.get("files") or []))
            if attach:
                lb = QLabel("  ".join(attach))
                lb.setStyleSheet(f"color: {t.text_3}; font-size: 11px;")
                lay.addWidget(lb)
        now = QPushButton("Now")
        now.setFlat(True)
        now.setToolTip("Send now — interrupts the current step")
        now.setAccessibleName("Send now")
        now.setIcon(icons.icon("zap", t.text_3, 12, hover=t.accent_text))
        now.setFixedHeight(24)
        now.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; padding: 0 8px; color: {t.text_3};"
            f" font-size: 11px; font-weight: 600; }}"
            f" QPushButton:hover {{ background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.accent_text}; }}"
        )
        now.clicked.connect(lambda: store.send_held_now(tray.sid, h["uuid"]))
        lay.addWidget(now, 0, Qt.AlignmentFlag.AlignTop)
        x = IconButton("x", "Remove queued message", 13)
        x.setFixedSize(24, 24)
        x.clicked.connect(lambda: store.remove_held(tray.sid, h["uuid"]))
        lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)


class _QueueEdit(QPlainTextEdit):
    def __init__(self, text: str, on_save: Callable[[str], None], on_cancel: Callable[[], None]) -> None:
        super().__init__()
        self._save, self._cancel = on_save, on_cancel
        self._done = False
        self.setPlainText(text)
        lines = min(6, max(1, text.count("\n") + 1))
        self.setFixedHeight(lines * self.fontMetrics().lineSpacing() + 10)
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.setAccessibleName("Queued message")

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._finish(self.toPlainText())
            return
        if e.key() == Qt.Key.Key_Escape:
            if not self._done:
                self._done = True
                self._cancel()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._finish(self.toPlainText())

    def _finish(self, text: str) -> None:
        if self._done:
            return
        self._done = True
        self._save(text)


# ---------------------------------------------------------------- the composer


class Composer(QWidget):
    """The composer for one session. ``focus_input()`` and ``insert(text, replace)`` are its public ways in."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.images: List[Json] = []
        self.files: List[Json] = []
        self.popup_state: Optional[Json] = None  # {"kind": "mention"|"slash", "query", "start", "index"}
        self.suggestions: List[Json] = []
        self.index: List[str] = []
        self.hist_pos = -1
        self._sent_at = 0.0
        self._last_h = 0
        self._dragging = False
        self._anim: Optional[QVariantAnimation] = None
        self.setObjectName("composer-wrap")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 0, 20, 10)
        outer.setSpacing(0)

        self.queue = QueueTray(session_id)
        self.queue.hide()
        outer.addWidget(_centered(self.queue))
        outer.addSpacing(0)

        # ---- the card
        self.card = QFrame()
        self.card.setObjectName("composer")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setMaximumWidth(theme.COL)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.card.setCursor(Qt.CursorShape.IBeamCursor)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self.attach_row = QWidget()
        self.attach_lay = QHBoxLayout(self.attach_row)
        self.attach_lay.setContentsMargins(14, 12, 14, 0)
        self.attach_lay.setSpacing(8)
        self.attach_lay.addStretch(1)
        self.attach_row.hide()
        cl.addWidget(self.attach_row)

        field = QWidget()
        field.setObjectName("composer-field")
        fl = QVBoxLayout(field)
        fl.setContentsMargins(16, 14, 16, 6)
        fl.setSpacing(0)
        self.edit = ComposerEdit(self)
        self.edit.setFont(theme.font_ui(theme.FS_BODY))
        self.edit.textChanged.connect(self._on_text_changed)
        fl.addWidget(self.edit)
        self.ghost = QLabel(field)
        self.ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.ghost.setFont(theme.font_ui(theme.FS_BODY))
        self.ghost.setTextFormat(Qt.TextFormat.RichText)
        self.ghost.move(16, 14)
        self.ghost.hide()
        cl.addWidget(field)
        self.field = field

        bar = QWidget()
        bar.setCursor(Qt.CursorShape.ArrowCursor)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 8)
        bl.setSpacing(2)
        self.attach_btn = IconButton("plus", "Attach images or files", 16)
        self.attach_btn.setFixedSize(30, 30)
        self.attach_btn.clicked.connect(self._pick_files)
        bl.addWidget(self.attach_btn)
        self.mode_btn = ChipButton("Permission mode (Shift+Tab to switch)")
        self.mode_btn.clicked.connect(self._open_mode_menu)
        bl.addWidget(self.mode_btn)
        bl.addStretch(1)
        self.model_btn = ChipButton("Model and effort")
        self.model_btn.clicked.connect(self._open_model_menu)
        bl.addWidget(self.model_btn)
        self.ctx_btn = LayoutButton()
        self.ctx_btn.setFlat(True)
        self.ctx_btn.setFixedHeight(30)
        self.ctx_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ctx_btn.setAccessibleName("Context window usage")
        self.ctx_btn.clicked.connect(lambda: store.set_ui(panel=True, panel_tab="info"))
        xl = QHBoxLayout(self.ctx_btn)
        xl.setContentsMargins(5, 0, 5, 0)
        xl.setSpacing(5)
        self.ring = ContextRing(16)
        xl.addWidget(self.ring)
        self.ctx_num = QLabel()
        self.ctx_num.hide()
        xl.addWidget(self.ctx_num)
        self.ctx_btn.hide()
        bl.addWidget(self.ctx_btn)
        bl.addSpacing(4)
        self.send_btn = SendButton()
        self.send_btn.clicked.connect(self._send_or_stop)
        bl.addWidget(self.send_btn)
        cl.addWidget(bar)
        outer.addWidget(_centered(self.card))

        self.foot = QLabel()
        self.foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.foot.setFixedHeight(24)
        self.foot.setContentsMargins(0, 6, 0, 0)
        self.foot.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self.foot)

        self.drop_hint = QLabel("Drop images or text files to attach", self)
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drop_hint.hide()

        # The popup paints over the conversation above the card, so it lives on the session page, not in here.
        self.popup = SuggestPopup(parent if parent is not None else self)
        self.popup.on_pick = self.apply_suggestion
        self.popup.on_hover = self._hover_suggestion

        self.edit.setPlainText(_drafts.get(session_id, ""))
        self.edit.moveCursor(QTextCursor.MoveOperation.End)

        bind(store.rt_changed, self, self._on_rt)
        bind(store.config_changed, self, lambda _c: self.refresh())
        bind(store.info_changed, self, lambda _i: self.refresh())
        bind(store.gateway_models_changed, self, lambda _g: self.refresh())
        bind(store.composer_insert, self, self._on_insert)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self.refresh()
        QTimer.singleShot(0, self._autosize)
        QTimer.singleShot(0, self._focus_if_free)

    # ---------------------------------------------------------------- state
    @property
    def rt(self):
        return store.rt.get(self.sid)

    @property
    def busy(self) -> bool:
        r = self.rt
        return bool(r and r.busy)

    @property
    def held(self) -> List[Json]:
        r = self.rt
        return r.held if r else []

    @property
    def mode(self) -> str:
        r = self.rt
        return str((r.get("mode") if r else None) or (store.config or {}).get("defaultMode") or "default")

    @property
    def send_with_enter(self) -> bool:
        return ((store.config or {}).get("sendKey") or "enter") == "enter"

    @property
    def text(self) -> str:
        return self.edit.toPlainText()

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip() or self.images or self.files)

    def set_cwd(self, cwd: str) -> None:
        self.cwd = cwd

    def focus_input(self) -> None:
        self.edit.setFocus()

    def _focus_if_free(self) -> None:
        from PySide6.QtWidgets import QApplication

        fw = QApplication.focusWidget()
        if fw is None or fw.metaObject().className() not in ("QLineEdit", "QTextEdit", "QPlainTextEdit"):
            self.edit.setFocus()

    def insert(self, text: str, replace: bool = False) -> None:
        """Text from elsewhere (diff notes, edit & resend, example prompts)."""
        cur = self.text
        self._set_text(text if replace or not cur.strip() else f"{cur.rstrip()}\n\n{text}")
        self.edit.setFocus()
        self.edit.moveCursor(QTextCursor.MoveOperation.End)

    def _on_insert(self, d: Json) -> None:
        if d.get("id") == self.sid:
            self.insert(str(d.get("text") or ""), bool(d.get("replace")))

    def _set_text(self, text: str, caret: Optional[int] = None) -> None:
        self.edit.blockSignals(True)
        self.edit.setPlainText(text)
        self.edit.blockSignals(False)
        c = self.edit.textCursor()
        c.setPosition(len(text) if caret is None else max(0, min(len(text), caret)))
        self.edit.setTextCursor(c)
        self._on_text_changed(detect=False)

    def _on_rt(self, sid: str) -> None:
        if sid == self.sid:
            self.refresh()

    def _on_text_changed(self, detect: bool = True) -> None:
        _drafts[self.sid] = self.text
        if detect:
            self.hist_pos = -1
            self.detect_popup()
        self._autosize()
        self.refresh()

    # ---------------------------------------------------------------- rendering
    def _paint(self) -> None:
        t = theme.tokens()
        self._paint_card(dragging=False)
        self.edit.setStyleSheet(
            f"ComposerEdit {{ background: transparent; border: none; padding: 0; color: {t.text}; }}"
        )
        self.edit.setFont(theme.font_ui(theme.FS_BODY))
        self.ghost.setStyleSheet(f"color: {t.text_3};")
        self.foot.setStyleSheet(f"color: {t.text_4}; font-size: {theme.FS_MICRO}px;")
        self.ctx_num.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_MICRO}px;")
        self.drop_hint.setStyleSheet(
            f"background: {t.raised}; color: {t.text}; border: 2px solid {theme.blend(t.accent_line, t.raised).name()};"
            f" border-radius: {theme.R4}px; font-weight: 500;"
        )
        self._paint_ctx()
        self.refresh()

    def _paint_card(self, dragging: bool) -> None:
        """The card: raised, a hairline ring, 16px corners; the ring turns orange while files are dragged over it."""
        t = theme.tokens()
        line = theme.blend(t.accent_line if dragging else t.hair_2, t.canvas).name()
        width = 2 if dragging else 1
        self.card.setStyleSheet(
            f"#composer {{ background: {t.raised}; border: {width}px solid {line}; border-radius: {theme.R4}px; }}"
        )

    def refresh(self) -> None:
        """Re-read the runtime and config and bring every control up to date."""
        r = self.rt
        busy = self.busy
        held = self.held
        mode = self.mode
        info = next((m for m in MODES if m["id"] == mode), MODES[0])
        self.mode_btn.set(info["label"], info["icon"], "", (info["tone"] or "set") if mode != "default" else "")
        # Model: before this session's first reply Claude Code has not listed its models yet; borrow the list
        # (and the default's name) from another session so the menu is never a dead end and the label never jumps.
        models = (r.get("models") if r else None) or []
        if not models:
            for other in store.rt.values():
                if other is not r and other.get("models"):
                    models = other.get("models")
                    break
        default_seen = next(
            (o.get("activeModel") for o in store.rt.values() if o.get("activeModel") and not o.get("model")), None
        )
        models = models or ALIASES
        gw = gateway_state()
        current = str((r.get("model") if r else "") or "")
        shown = current or (r.get("activeModel") if r else "") or default_seen or ""
        label = model_label(str(shown), gw["models"], models) if shown else "Default model"
        effort = str((r.get("effort") if r else "") or "")
        self.model_btn.set(label, "", effort_name(effort) if effort else "")
        usage = (r.get("usage") if r else None) or {}
        used, total = int(usage.get("contextTokens") or 0), int(usage.get("contextWindow") or 0)
        self.ctx_btn.setVisible(total > 0)
        if total > 0:
            pct = used / total
            self.ring.set_values(used, total)
            self.ctx_num.setVisible(pct >= 0.5)
            self.ctx_num.setText(f"{round(pct * 100)}%")
            tip = f"Context {round(pct * 100)}% used · {context_label(used, total)}"
            self.ctx_btn.setToolTip(tip + (" — /compact frees space" if pct >= 0.5 else ""))
            self._paint_ctx(hot=pct > 0.85)
        has = self.has_content
        self.send_btn.set_state(has, busy and not has, self.send_with_enter)
        suggestion = str(r.get("suggestion") or "") if r and not self.text else ""
        self.ghost.setVisible(bool(suggestion))
        if suggestion:
            fm = self.ghost.fontMetrics()
            avail = max(40, self.edit.width() - 60)
            shown_text = fm.elidedText(suggestion, Qt.TextElideMode.ElideRight, avail)
            self.ghost.setText(f"{shown_text}  <span style='font-size:10px'>Tab</span>")
            self.ghost.adjustSize()
            self.ghost.raise_()
        placeholder = (
            ""
            if suggestion
            else ("Queue another message…" if held else "Queue a message for after this step…")
            if busy
            else "Ask Claude to build, fix or explain…"
        )
        if self.edit.placeholderText() != placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.queue.set_held(held)
        self.refresh_foot()

    def _paint_ctx(self, hot: bool = False) -> None:
        t = theme.tokens()
        self.ctx_num.setStyleSheet(f"color: {t.err if hot else t.text_3}; font-size: {theme.FS_MICRO}px;")
        self.ctx_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; background: transparent; }}"
            f" QPushButton:hover {{ background: {theme.blend(t.fill_1, t.raised).name()}; }}"
        )

    def refresh_foot(self) -> None:
        """Key hints appear only on a focused, empty composer (and while Claude works); typing clears them."""
        t = theme.tokens()
        b = f"<b style='color:{t.text_3}'>"
        if not (self.busy or (self.edit.hasFocus() and not self.text)):
            self.foot.setText("")  # the line keeps its height, like opacity: 0
        elif self.busy:
            self.foot.setText(
                f"↵ queues for after this step · {b}Ctrl</b> {b}Shift</b> ↵ interrupts and sends now"
                f" · {b}Esc</b> to stop"
            )
        else:
            self.foot.setText(f"↵ to send · {b}Shift</b> + ↵ for a new line · {b}@</b> files · {b}/</b> commands")

    def _autosize(self) -> None:
        """The field grows with its content up to 42% of the window and settles back after a send."""
        doc = self.edit.document()
        lines = max(1, int(doc.documentLayout().documentSize().height()))
        line_h = self.edit.fontMetrics().lineSpacing()
        want = lines * line_h + 2 * int(doc.documentMargin()) + 4
        win = self.window()
        cap = max(60, round((win.height() if win is not None else 800) * 0.42) - 20)
        prev = self._last_h
        nxt = min(max(line_h + 8, want), cap)
        self._last_h = nxt
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not self.text and prev > nxt + 2 and time.time() - self._sent_at < 0.6 and not store.reduced_motion:
            anim = QVariantAnimation(self)
            anim.setStartValue(prev)
            anim.setEndValue(nxt)
            anim.setDuration(theme.SPRING_SOFT_T // 2)
            anim.setEasingCurve(theme.spring_soft())
            anim.valueChanged.connect(lambda v: self.edit.setFixedHeight(int(v)))
            anim.start()
            self._anim = anim
        else:
            self.edit.setFixedHeight(nxt)
        self._place_popup()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._autosize()
        self.drop_hint.setGeometry(20, 0, max(0, self.width() - 40), max(0, self.height() - 6))

    def hideEvent(self, e) -> None:  # noqa: N802
        self.popup.hide()
        super().hideEvent(e)

    # ---------------------------------------------------------------- keys
    def handle_key(self, e: QKeyEvent) -> bool:
        """The keyboard rules of Composer.tsx. Returns True when the key was taken."""
        key = e.key()
        mods = e.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        is_enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self.edit.composing
        tab = key == Qt.Key.Key_Tab or key == Qt.Key.Key_Backtab
        if self.popup_state and self.suggestions:
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                d = 1 if key == Qt.Key.Key_Down else -1
                n = len(self.suggestions)
                self.popup_state["index"] = (self.popup_state["index"] + d + n) % n
                self.popup.set_index(self.popup_state["index"])
                return True
            if (is_enter or (tab and not shift)) and not ctrl:
                self.apply_suggestion(self.suggestions[self.popup_state["index"]])
                return True
            if key == Qt.Key.Key_Escape:
                self.close_popup()
                return True
        if tab and shift:
            self.cycle_mode()
            return True
        r = self.rt
        if tab and not self.text and r and r.get("suggestion"):
            self._set_text(str(r.get("suggestion")))
            return True
        busy = self.busy
        if is_enter and busy and ctrl and shift:
            # Steer: interrupt the current step and make this the next thing Claude reads.
            self.submit(True)
            return True
        if is_enter and (
            (self.send_with_enter and not shift and not ctrl and not alt)
            or (not self.send_with_enter and ctrl and not shift)
        ):
            self.submit()
            return True
        if key == Qt.Key.Key_Escape and busy:
            store.interrupt(self.sid)
            return True
        hist = _histories.get(self.sid, [])
        at_start = self.edit.textCursor().position() == 0
        at_end = self.edit.textCursor().position() == len(self.text)
        held = self.held
        if key == Qt.Key.Key_Up and self.text == "" and held and at_start:
            # Like the terminal: ↑ takes the last queued message back so it can be changed.
            last = held[-1]
            store.remove_held(self.sid, last["uuid"]).catch(lambda _e: None)
            self._set_text(str(last.get("text") or ""))
            return True
        if key == Qt.Key.Key_Up and hist and (self.text == "" or self.hist_pos >= 0) and at_start:
            p = min(len(hist) - 1, self.hist_pos + 1)
            self._set_text(hist[p])
            self.hist_pos = p
            return True
        if key == Qt.Key.Key_Down and self.hist_pos >= 0 and at_end:
            p = self.hist_pos - 1
            self._set_text(hist[p] if p >= 0 else "")
            self.hist_pos = p
            return True
        return False

    def cycle_mode(self) -> None:
        is_root = bool((store.info or {}).get("isRoot"))
        order = [m["id"] for m in MODES if not (m["id"] == "bypassPermissions" and is_root) and m["id"] != "auto"]
        cur = self.mode
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        store.set_mode(self.sid, nxt).catch(lambda _e: None)

    # ---------------------------------------------------------------- sending
    def submit(self, now: bool = False) -> None:
        body = self.text.strip()
        if not body and not self.images and not self.files:
            return
        payload_images = [{"mediaType": i["mediaType"], "data": i["data"]} for i in self.images]
        payload_files = [{"name": f["name"], "text": f["text"]} for f in self.files]
        self._sent_at = time.time()
        self._set_text("")
        self.images, self.files = [], []
        self._render_attachments()
        self.close_popup()
        self.hist_pos = -1
        _drafts[self.sid] = ""
        if body:
            _histories[self.sid] = ([body] + [h for h in _histories.get(self.sid, []) if h != body])[:50]
        store.send_message(self.sid, body, images=payload_images, files=payload_files, now=now and self.busy).catch(
            lambda _e: self._set_text(body)
        )

    def _send_or_stop(self) -> None:
        if self.send_btn.state == "stop":
            store.interrupt(self.sid)
        else:
            self.submit()

    # ---------------------------------------------------------------- the @ and / popup
    def _commands(self) -> List[Json]:
        r = self.rt
        cmds = r.get("commands") if r else None
        if cmds:
            return list(cmds)
        init = (r.get("init") if r else None) or {}
        names = init.get("slashCommands") or []
        if names:
            return [
                next((b for b in BUILTIN_COMMANDS if b["name"] == n), {"name": n, "description": ""}) for n in names
            ]
        return BUILTIN_COMMANDS

    def _load_index(self) -> None:
        hit = _file_index_cache.get(self.cwd)
        if hit and time.time() - hit[0] < 20:
            self.index = hit[1]
            self._refresh_suggestions()
            return

        def ok(files: Any) -> None:
            files = list(files or [])
            _file_index_cache[self.cwd] = (time.time(), files)
            self.index = files
            self._refresh_suggestions()

        store.call("fs.index", {"cwd": self.cwd}, quiet=True).then(ok).catch(lambda _e: None)

    def detect_popup(self) -> None:
        value = self.text
        caret = self.edit.textCursor().position()
        before = value[:caret]
        slash = re.match(r"^/([\w:-]*)$", before)
        if slash:
            self.popup_state = {"kind": "slash", "query": slash.group(1), "start": 0, "index": 0}
            self._refresh_suggestions()
            return
        at = re.search(r"(?:^|\s)@([^\s@]*)$", before)
        if at:
            if not self.index:
                self._load_index()
            self.popup_state = {
                "kind": "mention",
                "query": at.group(1),
                "start": caret - len(at.group(1)) - 1,
                "index": 0,
            }
            self._refresh_suggestions()
            return
        self.close_popup()

    def _refresh_suggestions(self) -> None:
        p = self.popup_state
        if not p:
            self.suggestions = []
            self.popup.hide()
            return
        if p["kind"] == "slash":
            matches = fuzzy_filter(p["query"], self._commands(), lambda c: str(c["name"]), 40)
            self.suggestions = [
                {
                    "key": m.item["name"],
                    "label": "/" + m.item["name"],
                    "hint": f"{m.item['argumentHint']} — {m.item.get('description', '')}"
                    if m.item.get("argumentHint")
                    else m.item.get("description", ""),
                    "kind": "cmd",
                }
                for m in matches
            ]
            head = "Commands"
        else:
            q = p["query"]
            lst = (
                [m.item for m in fuzzy_filter(q, self.index, lambda f: f, 40)]
                if q
                else [f for f in self.index if "/" not in f or len(f.split("/")) <= 2][:40]
            )
            self.suggestions = [
                {"key": f, "label": f, "hint": "", "kind": "dir" if f.endswith("/") else "file"} for f in lst
            ]
            head = "Files" if q else "Files — keep typing to narrow down"
        if not self.suggestions:
            self.popup.hide()
            return
        p["index"] = min(p["index"], len(self.suggestions) - 1)
        self.popup.set_items(head, self.suggestions, p["index"])
        self.popup.show()
        self.popup.raise_()
        self._place_popup()

    def _place_popup(self) -> None:
        if not self.popup.isVisible():
            return
        host = self.popup.parentWidget()
        if host is None:
            return
        top_left = self.card.mapTo(host, QPoint(0, 0))
        w = self.card.width()
        h = self.popup.total_height()
        self.popup.setGeometry(top_left.x(), max(0, top_left.y() - 2 - h), w, h)

    def _hover_suggestion(self, i: int) -> None:
        if self.popup_state:
            self.popup_state["index"] = i
            self.popup.set_index(i)

    def apply_suggestion(self, s: Json) -> None:
        p = self.popup_state
        if not p:
            return
        caret = self.edit.textCursor().position()
        text = self.text
        if p["kind"] == "slash":
            nxt = f"/{s['key']} " + text[caret:]
            pos = len(s["key"]) + 2
        else:
            ins = f"@{s['key']}{'' if s['kind'] == 'dir' else ' '}"
            nxt = text[: p["start"]] + ins + text[caret:]
            pos = p["start"] + len(ins)
        self.close_popup()
        self._set_text(nxt, pos)
        self.edit.setFocus()
        if s["kind"] == "dir":
            self.detect_popup()

    def close_popup(self) -> None:
        self.popup_state = None
        self.suggestions = []
        self.popup.hide()

    def close_popup_if_unfocused(self) -> None:
        if not self.edit.hasFocus():
            self.close_popup()

    # ---------------------------------------------------------------- menus
    def _menu_above(self, items: List[Json], anchor: QWidget, width: int, align_right: bool = False) -> None:
        m = build_menu(items, anchor, width)
        hint = m.sizeHint()
        x = anchor.width() - hint.width() if align_right else 0
        m.exec(anchor.mapToGlobal(QPoint(x, -hint.height() - 4)))

    def _open_mode_menu(self) -> None:
        is_root = bool((store.info or {}).get("isRoot"))
        mode = self.mode
        items = [
            {
                "label": m["label"],
                "hint": m["hint"],
                "icon": m["icon"],
                "checked": m["id"] == mode,
                "danger": m["id"] == "bypassPermissions",
                "on_select": lambda mid=m["id"]: store.set_mode(self.sid, mid).catch(lambda _e: None),
            }
            for m in MODES
            if not (m["id"] == "bypassPermissions" and is_root)
        ]
        self._menu_above(items, self.mode_btn, 372)

    def model_menu_items(self) -> List[Json]:
        r = self.rt
        gw = gateway_state()
        current = str((r.get("model") if r else "") or "")
        models = (r.get("models") if r else None) or ALIASES
        model_info = next((m for m in models if m.get("value") == current), None) or next(
            (m for m in models if m.get("value") == "default"), None
        )
        levels = (model_info or {}).get("supportedEffortLevels") or (
            EFFORT_LEVELS if (model_info or {}).get("supportsEffort") else []
        )
        effort = str((r.get("effort") if r else "") or "")
        items: List[Json] = [
            {"heading": "Model"},
            {
                "label": "Default",
                "hint": "Whatever Claude Code is configured to use",
                "checked": not current,
                "on_select": lambda: store.set_model(self.sid, ""),
            },
        ]
        # The gateway's own models. Claude Code's built-in aliases (Opus/Sonnet/Haiku) are not offered:
        # everything goes through the gateway, which may not know them.
        if gw["models"] or gw["loading"] or gw["error"]:
            items.append({"heading": f"On {_host_of(gw['base'])}" if gw["base"] else "On your gateway"})
            if gw["loading"]:
                items.append({"label": "Asking the gateway…", "disabled": True})
            for m in gw["models"]:
                name = m.get("displayName")
                items.append(
                    {
                        "label": _trim_claude(str(name)) if name else m["id"],
                        "hint": m["id"] if name else None,
                        "mono": True,
                        "checked": current == m["id"],
                        "on_select": lambda mid=m["id"]: store.set_model(self.sid, mid),
                    }
                )
            if gw["error"]:
                items.append({"label": "Couldn't list the models", "hint": gw["error"], "disabled": True})
            if not gw["loading"]:
                items.append(
                    {
                        "label": "Try again" if gw["error"] else "Refresh the list",
                        "icon": "refresh-cw",
                        "on_select": lambda: store.load_gateway_models(True),
                    }
                )
        if levels:
            items.append({"separator": True})
            items.append({"heading": "Effort"})
            items.append(
                {"label": "Default", "checked": not effort, "on_select": lambda: store.set_effort(self.sid, "")}
            )
            for lv in levels:
                items.append(
                    {
                        "label": effort_name(lv),
                        "checked": effort == lv,
                        "on_select": lambda level=lv: store.set_effort(self.sid, level),
                    }
                )
        return items

    def _open_model_menu(self) -> None:
        store.load_gateway_models()
        self._menu_above(self.model_menu_items(), self.model_btn, 320, align_right=True)

    # ---------------------------------------------------------------- attachments
    def set_dragging(self, on: bool) -> None:
        if on == self._dragging:
            return
        self._dragging = on
        self._paint_card(dragging=on)
        self.drop_hint.setVisible(on)
        if on:
            self.drop_hint.raise_()

    def _pick_files(self) -> None:
        paths, _f = QFileDialog.getOpenFileNames(self, "Attach images or files")
        if paths:
            self.add_files(paths)

    def add_files(self, paths: List[str]) -> None:
        import os

        from PySide6.QtCore import QMimeDatabase

        db = QMimeDatabase()
        for path in paths:
            name = os.path.basename(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            mt = db.mimeTypeForFile(path).name()
            if mt.startswith("image/"):
                if size > MAX_IMAGE:
                    store.toast(f"{name} is larger than 8 MB", "warning")
                    continue
                with open(path, "rb") as fh:
                    raw = fh.read()
                pm = QPixmap()
                pm.loadFromData(raw)
                self.images.append(
                    {
                        "name": name,
                        "mediaType": mt,
                        "data": base64.b64encode(raw).decode("ascii"),
                        "size": size,
                        "pixmap": pm,
                    }
                )
            elif size < MAX_TEXT:
                with open(path, "rb") as fh:
                    raw = fh.read()
                if b"\x00" in raw[:4000]:
                    store.toast(f"{name} looks like a binary file", "warning")
                    continue
                self.files.append({"name": name, "text": raw.decode("utf-8", "replace")})
            else:
                store.toast(f"{name} is too large to attach (400 KB max)", "warning")
        self._render_attachments()

    def add_image(self, img: QImage, name: str) -> None:
        if img.isNull():
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        raw = bytes(buf.data())
        if len(raw) > MAX_IMAGE:
            store.toast(f"{name} is larger than 8 MB", "warning")
            return
        self.images.append(
            {
                "name": name,
                "mediaType": "image/png",
                "data": base64.b64encode(raw).decode("ascii"),
                "size": len(raw),
                "pixmap": QPixmap.fromImage(img),
            }
        )
        self._render_attachments()

    def _render_attachments(self) -> None:
        while self.attach_lay.count() > 1:
            w = self.attach_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for img in self.images:
            self.attach_lay.insertWidget(
                self.attach_lay.count() - 1, ImageChip(img, lambda i=img: self._remove(i, self.images))
            )
        for f in self.files:
            self.attach_lay.insertWidget(
                self.attach_lay.count() - 1, FileChip(f, lambda x=f: self._remove(x, self.files))
            )
        self.attach_row.setVisible(bool(self.images or self.files))
        self.refresh()

    def _remove(self, item: Json, lst: List[Json]) -> None:
        if item in lst:
            lst.remove(item)
        self._render_attachments()


def _centered(w: QWidget) -> QWidget:
    """A row that keeps ``w`` at most ``theme.COL`` wide and centred, like ``max-width + margin: auto``."""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    lay.addWidget(w, 100)
    lay.addStretch(1)
    return host


def _mix(a: str, b: str, pct: float) -> QColor:
    """CSS ``color-mix(in srgb, a pct, b)`` for two opaque tokens."""
    x, y = theme.qcolor(a), theme.qcolor(b)
    return QColor(
        int(round(x.red() * pct + y.red() * (1 - pct))),
        int(round(x.green() * pct + y.green() * (1 - pct))),
        int(round(x.blue() * pct + y.blue() * (1 - pct))),
    )
