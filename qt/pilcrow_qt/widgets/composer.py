"""The composer (a port of web/src/components/Composer.tsx, with the model helpers of lib/models.ts).

The card at the foot of a session: an auto-sizing field, attachments, the @ file and / command
popups, the queue of messages held while Claude works, the permission-mode and model menus, the
context ring and the one send/stop button. Drafts and histories live here for as long as the app
runs, one per session, exactly like the module-level maps in the React version.

The pieces are reusable, so another page can build its own box the way ``.welcome-composer`` does:

    box = ComposerBox(pad_bottom=16)          # the ring, shadow, focus halo and lift (``.composer``)
    edit = ComposerEdit()                     # the textarea (``.composer-input``), owner optional
    box.body.addWidget(composer_field(edit, min_height=74))   # ``.composer-field`` paddings
    bar = ComposerBar()                       # ``.composer-bar``
    bar.lay.addWidget(ChipButton("Choose a folder")); bar.lay.addStretch(1); bar.lay.addWidget(SendButton())
    box.body.addWidget(bar)

``column_width(w)`` gives the conversation column for a pane ``w`` wide (the web's ``--col``);
the mode and model menus are ``ui.menu`` menus opened over the card.
"""

from __future__ import annotations

import atexit
import base64
import math
import os
import re
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import shiboken6
from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QIODevice,
    QMimeData,
    QMimeDatabase,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..canvas.clock import Tween
from ..lib.fmt import bytes_, tildify
from ..lib.fuzzy import fuzzy_filter
from ..lib.models import ALIASES, model_label, trim_claude
from ..store import store
from .queue_tray import QueueTray
from .suggest import SuggestPopup, _FloatingPanel, word_match
from .ui.bits import ContextRing, LayoutButton, context_label, draw_box_shadow, paint_kbd, reveal_on_hover, rounded_path
from .ui.button import Btn
from .ui.menu import MenuPopover, close_menus, show_menu
from .ui.send import SendButton
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

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
MAX_IMAGE = 8 * 1024 * 1024
MAX_TEXT = 400 * 1024

#: ``.composer-input``: 14.5px on a 1.55 line (22.475px), padding 14px 16px 6px, at least 50px tall in all.
LINE_H = 22.475
FIELD_PAD = (16, 14, 16, 6)
FIELD_MIN = 50

_drafts: Dict[str, str] = {}
_histories: Dict[str, List[str]] = {}
_file_index_cache: Dict[str, Tuple[float, List[str]]] = {}


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


def effort_name(level: str) -> str:
    return "Extra high" if level == "xhigh" else level[:1].upper() + level[1:]


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


# ---------------------------------------------------------------- the column


def column_width(pane_width: int) -> int:
    """The conversation column of a pane this wide: ``clamp(640px, min(61.8%, 100% - 48px), 1080px)``
    (app.css ``.main --col``), the golden section that Claude's own desktop app settles on. The
    composer resolves it against the ``.composer-wrap`` content box, 20px in from each side."""
    avail = max(0, pane_width - 40)
    return min(theme.column_width(avail), avail)


# ---------------------------------------------------------------- the box (.composer)


def _in_overlay(w: Optional[QWidget]) -> bool:
    """Inside a menu, a popover or the composer's suggestion list: in-window panels that work with the
    composer, so a press there keeps its focus (and its halo)."""
    from .ui.menu import Popover

    while w is not None:
        if isinstance(w, (Popover, _FloatingPanel)):
            return True
        w = w.parentWidget()
    return False


#: the boxes' watches on the application, taken off at interpreter exit (see tooltip.install)
_WATCHES: weakref.WeakSet[_PressElsewhere] = weakref.WeakSet()


def _drop_watches() -> None:
    app = QApplication.instance()
    for w in list(_WATCHES):
        if app is not None and shiboken6.isValid(w) and shiboken6.isValid(app):
            app.removeEventFilter(w)


atexit.register(_drop_watches)


class _PressElsewhere(QObject):
    """The web's blur: a press anywhere in the window outside the composer takes the focus off it, also
    where nothing takes the focus instead (the conversation, a label, the empty sidebar), so the focus
    halo goes away with the first click outside rather than only on the few controls that take focus.
    Installed on the application only while the box has the focus."""

    def __init__(self, box: ComposerBox) -> None:
        super().__init__(box)
        self.box = box
        _WATCHES.add(self)

    def eventFilter(self, obj, e) -> bool:
        if e.type() != QEvent.Type.MouseButtonPress or not isinstance(obj, QWidget):
            return False
        box = self.box
        if obj is box or box.isAncestorOf(obj) or obj.window() is not box.window() or _in_overlay(obj):
            return False
        # a press the box did not take travels on to its parents: judge by where the pointer is
        if box.rect().contains(box.mapFromGlobal(e.globalPosition().toPoint())):
            return False
        # after the press has been handled: if what was pressed took the focus, nothing is left to do
        QTimer.singleShot(0, box.blur_if_focused)
        return False


class ComposerBox(QWidget):
    """The composer card (``.composer``): raised, a hairline ring, a soft shadow; on focus-within the
    ring brightens, an orange halo appears, the shadow deepens and the card rises a pixel, all on the
    soft spring. Children go in ``body``; the pads around it hold the halo and the shadow, which CSS
    draws outside the box. ``set_tone`` tints the ring for plan / bypass; ``set_dragging`` for drops."""

    PAD_X = 6
    PAD_TOP = 6
    geometry_changed = Signal()
    focus_changed = Signal(bool)  # focus-within, after the application's focusChanged

    def __init__(self, parent: Optional[QWidget] = None, pad_bottom: int = 34) -> None:
        super().__init__(parent)
        self.pad_bottom = pad_bottom
        self._focused = False
        self._lift = 0
        self._tone = ""
        self._dragging = False
        self.focus_target: Optional[Callable[[], None]] = None
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(self.PAD_X, self.PAD_TOP, self.PAD_X, pad_bottom)
        self.body.setSpacing(0)
        self.focus_t = Tween(self._on_focus_frame, ms=theme.SPRING_SOFT_T, curve=theme.spring_soft)
        self._press_watch = _PressElsewhere(self)
        app = QApplication.instance()
        if app is not None:
            bind(app.focusChanged, self, self._on_focus_changed)
            bind(app.aboutToQuit, self, lambda: app.removeEventFilter(self._press_watch))
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- state
    @property
    def focused(self) -> bool:
        return self._focused

    def card_rect(self) -> QRectF:
        """The box the CSS element occupies (its border box), inside the pads."""
        return QRectF(
            self.PAD_X,
            self.PAD_TOP - self._lift,
            self.width() - 2 * self.PAD_X,
            self.height() - self.PAD_TOP - self.pad_bottom,
        )

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self.update()

    def set_dragging(self, on: bool) -> None:
        if on != self._dragging:
            self._dragging = on
            self.update()

    def _on_focus_changed(self, _old: Optional[QWidget], new: Optional[QWidget]) -> None:
        focused = new is not None and (new is self or self.isAncestorOf(new))
        if focused == self._focused:
            return
        self._focused = focused
        app = QApplication.instance()
        if app is not None:
            # while focused, watch for a press elsewhere (see _PressElsewhere); idle otherwise
            if focused:
                app.installEventFilter(self._press_watch)
            else:
                app.removeEventFilter(self._press_watch)
        self.focus_t.go(1.0 if focused else 0.0)
        self.focus_changed.emit(focused)

    def blur_if_focused(self) -> None:
        """Let go of the keyboard focus if it is still inside the box (a press elsewhere did not take it)."""
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self or self.isAncestorOf(fw)):
            fw.clearFocus()

    def _on_focus_frame(self, t: float) -> None:
        lift = 1 if t > 0.5 and not self._dragging else 0
        if lift != self._lift:
            self._lift = lift
            self.body.setContentsMargins(self.PAD_X, self.PAD_TOP - lift, self.PAD_X, self.pad_bottom + lift)
        self.update()

    def mousePressEvent(self, e) -> None:
        if self.focus_target is not None and e.button() == Qt.MouseButton.LeftButton:
            self.focus_target()
        super().mousePressEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.geometry_changed.emit()

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self.geometry_changed.emit()

    # ---- paint
    def ring_colors(self, t: float) -> Tuple[QColor, QColor]:
        """(ring, halo) for focus progress ``t`` and the current tone."""
        tk = theme.tokens()
        tone = self._tone
        if tone == "plan":
            idle, focus = theme.mix(tk.plan, 0.5, tk.hair_2), theme.mix(tk.plan, 0.65, tk.hair_3)
            halo = theme.with_alpha(tk.plan, 0.09)
        elif tone == "err":
            idle, focus = theme.mix(tk.err, 0.45, tk.hair_2), theme.mix(tk.err, 0.6, tk.hair_3)
            halo = theme.with_alpha(tk.err, 0.08)
        else:
            idle, focus = theme.qcolor(tk.hair_2), theme.qcolor(tk.hair_3)
            halo = theme.with_alpha(tk.accent, 0.08 if not tk.dark else 0.07)
        halo.setAlphaF(halo.alphaF() * max(0.0, min(1.0, t)))
        return theme.lerp(idle, focus, t), halo

    def paintEvent(self, _e) -> None:
        tk = theme.tokens()
        t = self.focus_t.value
        r = self.card_rect()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._dragging:
            if tk.dark:
                draw_box_shadow(p, r, 6, 24, -14, 0.5, 1 - t)
                draw_box_shadow(p, r, 12, 36, -14, 0.6, t)
            else:
                draw_box_shadow(p, r, 8, 24, -16, 0.22, 1 - t, (17, 17, 26))
                draw_box_shadow(p, r, 14, 36, -16, 0.28, t, (17, 17, 26))
        # the rings, outside the border box: the halo first, the hairline on top of it
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._dragging:
            p.setPen(QPen(theme.qcolor(tk.accent_wash), 6))
            p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), theme.R4 + 3, theme.R4 + 3)
            p.setPen(QPen(theme.qcolor(tk.accent_line), 2))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), theme.R4 + 1, theme.R4 + 1)
        else:
            ring, halo = self.ring_colors(t)
            if halo.alpha() > 0:
                p.setPen(QPen(halo, 5))
                p.drawRoundedRect(r.adjusted(-2.5, -2.5, 2.5, 2.5), theme.R4 + 2.5, theme.R4 + 2.5)
            p.setPen(QPen(ring, 1))
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), theme.R4 + 0.5, theme.R4 + 0.5)
        # the card itself, lit along its top edge on dark
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tk.raised))
        p.drawRoundedRect(r, theme.R4, theme.R4)
        if tk.dark:
            p.setClipPath(rounded_path(r, theme.R4))
            p.fillRect(QRectF(r.x(), r.y(), r.width(), 1), theme.qcolor(tk.edge_hi))
            p.setClipping(False)


def composer_field(edit: QWidget, min_height: int = FIELD_MIN) -> QWidget:
    """``.composer-field``: the textarea's paddings (14px 16px 6px) and its minimum height."""
    host = QWidget()
    host.setObjectName("composer-field")
    lay = QVBoxLayout(host)
    # Qt puts a line's extra leading under the glyphs where CSS splits it above and below; one pixel
    # of the top padding moves the ink to where the browser puts it.
    lay.setContentsMargins(FIELD_PAD[0], FIELD_PAD[1] + 1, FIELD_PAD[2], FIELD_PAD[3] - 1)
    lay.setSpacing(0)
    lay.addWidget(edit)
    host.setMinimumHeight(min_height)
    return host


class ComposerBar(QWidget):
    """``.composer-bar``: the row of chips under the field. Add to ``lay``."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 4, 8, 7)  # the send button is a pixel taller for its shadow
        self.lay.setSpacing(2)


# ---------------------------------------------------------------- the field


class ComposerEdit(QTextEdit):
    """The text field (``.composer-input``). Keys go to the owner first when it has ``handle_key``; the
    IME guard keeps Enter from sending mid-composition. Works without an owner too. A QTextEdit kept to
    plain text, because only its document layout honours the block line height (22px lines)."""

    def __init__(self, owner: Optional[Any] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # the number keys that answer a waiting card look for this: an empty composer is not "typing"
        self.setProperty("composer", True)
        self._owner = owner
        self._composing = False
        self.setAcceptRichText(False)
        self.setAcceptDrops(True)
        self.setTabChangesFocus(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setAccessibleName("Message")
        self.setMinimumHeight(FIELD_MIN - FIELD_PAD[1] - FIELD_PAD[3])
        self.document().setDocumentMargin(0)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFont(theme.font_ui(theme.FS_BODY))
        self.setStyleSheet("ComposerEdit { background: transparent; border: none; padding: 0; }")
        theme.tint(self, placeholder="text_3", highlight="selection/raised")
        self.apply_line_height()
        # what a change brings in takes the line height at once; the rest of the draft is not read
        self.document().contentsChange.connect(self._on_contents_change)
        self._fixing = False

    def _on_contents_change(self, pos: int, _removed: int, added: int) -> None:
        if self._fixing or added <= 0:
            return
        doc = self.document()
        extra = self.line_extra()
        block = doc.findBlock(pos)
        end = doc.findBlock(pos + added)
        kind = QTextBlockFormat.LineHeightTypes.LineDistanceHeight.value
        need = False
        while block.isValid():
            f = block.blockFormat()
            if f.lineHeightType() != kind or abs(f.lineHeight() - extra) > 0.01:
                need = True
                break
            if block == end:
                break
            block = block.next()
        if not need:
            return
        fmt = QTextBlockFormat()
        fmt.setLineHeight(extra, kind)
        cur = QTextCursor(doc)
        cur.setPosition(pos)
        cur.setPosition(min(pos + added, doc.characterCount() - 1), QTextCursor.MoveMode.KeepAnchor)
        self._fixing = True
        try:
            cur.mergeBlockFormat(fmt)  # one edit over the whole range, not one per block
        finally:
            self._fixing = False

    def line_extra(self) -> float:
        """What a 22.475px line adds under the font's own ascent + descent (nothing for a taller fallback face)."""
        fm = QFontMetrics(self.font())
        return max(0.0, LINE_H - (fm.ascent() + fm.descent()))

    def apply_line_height(self) -> None:
        """Every block on a 22.475px line (``line-height: 1.55`` at 14.5px); new blocks inherit it. The
        extra goes under the glyphs (LineDistanceHeight), where Qt also puts the placeholder's."""
        doc = self.document()
        extra = self.line_extra()
        block = doc.firstBlock()
        need = False
        while block.isValid():
            f = block.blockFormat()
            if f.lineHeightType() != QTextBlockFormat.LineHeightTypes.LineDistanceHeight.value or (
                abs(f.lineHeight() - extra) > 0.01
            ):
                need = True
                break
            block = block.next()
        if not need:
            return
        fmt = QTextBlockFormat()
        fmt.setLineHeight(extra, QTextBlockFormat.LineHeightTypes.LineDistanceHeight.value)
        cur = QTextCursor(doc)
        cur.select(QTextCursor.SelectionType.Document)
        self.blockSignals(True)
        cur.mergeBlockFormat(fmt)
        self.blockSignals(False)

    @property
    def composing(self) -> bool:
        return self._composing

    def content_height(self) -> int:
        """What the text wants, in whole lines. The document is laid out at the viewport's width by
        the field itself; setting that width again here would lay the whole draft out twice a key."""
        doc = self.document()
        want = max(10, self.viewport().width())
        if abs(doc.textWidth() - want) > 0.5:
            doc.setTextWidth(want)
        line = max(LINE_H, QFontMetrics(self.font()).ascent() + QFontMetrics(self.font()).descent())
        lines = max(1, int(round(doc.documentLayout().documentSize().height() / line)))
        return int(math.ceil(lines * line))

    def inputMethodEvent(self, e) -> None:
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

    def keyPressEvent(self, e: QKeyEvent) -> None:
        handle = getattr(self._owner, "handle_key", None)
        if handle is not None and handle(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        self._call("detect_popup")

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self._call("refresh_foot")

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._call("refresh_foot")
        close = getattr(self._owner, "close_popup_if_unfocused", None)
        if close is not None:
            QTimer.singleShot(120, close)

    def _call(self, name: str, *args: Any) -> None:
        fn = getattr(self._owner, name, None)
        if fn is not None:
            fn(*args)

    # ---- paste and drop
    def canInsertFromMimeData(self, src: QMimeData) -> bool:
        return src.hasImage() or src.hasUrls() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src: QMimeData) -> None:
        if src.hasImage() and hasattr(self._owner, "add_image"):
            self._owner.add_image(QImage(src.imageData()), "pasted image")
            return
        if src.hasUrls() and hasattr(self._owner, "add_files"):
            paths = [u.toLocalFile() for u in src.urls() if u.isLocalFile()]
            if paths:
                self._owner.add_files(paths)
                return
        super().insertFromMimeData(src)
        self.apply_line_height()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            self._call("set_dragging", True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e) -> None:
        self._call("set_dragging", False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e) -> None:
        self._call("set_dragging", False)
        super().dropEvent(e)


# ---------------------------------------------------------------- small buttons


class ChipButton(LayoutButton):
    """A quiet pill with an optional icon, label, sub label and chevron (``.cbtn``): 30px tall, 12px
    medium text, a fill on hover and while its menu is open (the chevron turns on a spring then).
    ``icon_only`` makes the 30×30 square (``.cbtn.icon``). Mode tones add a coloured wash."""

    def __init__(
        self,
        tooltip: str = "",
        chevron: bool = True,
        icon_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon = ""
        self._icon_size = 16 if icon_only else 14
        self._tone = ""
        self._chevron = chevron and not icon_only
        self._shown: tuple = ()
        self._icon_only = icon_only
        self._open = False
        self._pressed = False
        self.setFlat(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0 if icon_only else 9, 0, 0 if icon_only else 9, 0)
        lay.setSpacing(6)
        self.ico = QLabel()
        self.ico.hide()
        lay.addWidget(self.ico)
        self.text_lb = QLabel()
        self.text_lb.setFont(theme.font_ui(theme.FS_SMALL, 500))
        lay.addWidget(self.text_lb)
        if icon_only:
            self.text_lb.hide()  # hidden once parented: a parentless label shown is a window of its own
        self.sub_lb = QLabel()
        self.sub_lb.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.sub_lb, fg="text_3")
        self.sub_lb.hide()
        lay.addWidget(self.sub_lb)
        # the chevron is painted (it rotates); this keeps its place: 12px less the -2px margin
        self.chev = QLabel()
        self.chev.setFixedSize(10, 12)
        lay.addWidget(self.chev)
        if not self._chevron:
            self.chev.hide()
        if icon_only:
            self.setFixedWidth(30)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hover_t = Tween(lambda _v: self.update(), ms=theme.T_FAST, curve=theme.ease)
        self.chev_t = Tween(lambda _v: self.update(), ms=theme.SPRING_SNAPPY_T, curve=theme.spring_snappy)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set(self, text: str, icon: str = "", sub: str = "", tone: str = "") -> None:
        if (text, icon, sub, tone) == self._shown:
            return
        self._shown = (text, icon, sub, tone)
        self.text_lb.setText(text)
        compact = getattr(self, "_compact", False)
        self.text_lb.setVisible(bool(text) and not self._icon_only and not compact)
        self.sub_lb.setText(f"· {sub}" if sub else "")
        self.sub_lb.setVisible(bool(sub))
        self._icon, self._tone = icon, tone
        self.ico.setVisible(bool(icon))
        self._paint()

    def set_compact(self, on: bool) -> None:
        """Only the icon (the column is narrow): the label and the chevron come back with the room."""
        if on == getattr(self, "_compact", False):
            return
        self._compact = on
        self.text_lb.setVisible(not on and bool(self.text_lb.text()) and not self._icon_only)
        self.sub_lb.setVisible(not on and bool(self.sub_lb.text()))
        self.chev.setVisible(self._chevron and not on)
        self.layout().setContentsMargins(0 if on else 9, 0, 0 if on else 9, 0)
        if on:
            self.setFixedWidth(30)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
        self.updateGeometry()

    def set_open(self, on: bool) -> None:
        if on == self._open:
            return
        self._open = on
        self.chev_t.go(1.0 if on else 0.0)
        self.update()

    @property
    def is_open(self) -> bool:
        return self._open

    def color_name(self) -> str:
        """The token the text and icon take: the tone's, else text (lit) or text-2."""
        tone = {"accent": "accent_text", "plan": "plan", "info": "info", "err": "err", "set": "text"}.get(self._tone)
        if tone:
            return tone
        return "text" if (self.underMouse() or self._open) else "text_2"

    def color(self) -> str:
        return getattr(theme.tokens(), self.color_name())

    def _paint(self) -> None:
        name = self.color_name()
        if self._icon:
            self.ico.setPixmap(icons.pixmap(self._icon, getattr(theme.tokens(), name), self._icon_size))
        theme.tint(self.text_lb, fg=name)
        self.update()

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self.hover_t.go(1.0)
        self._paint()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self.hover_t.go(0.0)
        self._paint()

    def mousePressEvent(self, e) -> None:
        self._pressed = True
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._pressed = False
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        wash = {"accent": t.accent_wash, "plan": t.plan_wash, "info": t.info_wash, "err": t.err_wash}.get(self._tone)
        p.setPen(Qt.PenStyle.NoPen)
        if wash:
            p.setBrush(theme.qcolor(wash))
            p.drawRoundedRect(r, theme.R2, theme.R2)
        hover = max(self.hover_t.value, 1.0 if self._open else 0.0)
        if hover > 0 and not wash:
            c = theme.qcolor(t.fill_2)
            c.setAlphaF(c.alphaF() * hover)
            p.setBrush(c)
            p.drawRoundedRect(r, theme.R2, theme.R2)
        if self._chevron and self.chev.isVisible():
            pm = icons.pixmap("chevron-down", self.color(), 12)
            g = self.chev.geometry()
            cx, cy = g.x() + g.width() - 6, g.y() + 6
            p.save()
            p.setOpacity(0.55)
            p.translate(cx, cy)
            p.rotate(180.0 * self.chev_t.value)
            p.drawPixmap(QPointF(-6, -6), pm)
            p.restore()


class CtxButton(LayoutButton):
    """``.ctx-btn``: the context ring, with the percentage once the window is half full."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFlat(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Context window usage")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(5, 0, 5, 0)
        self.lay.setSpacing(5)
        self.ring = ContextRing(16)
        self.lay.addWidget(self.ring)
        self.num = QLabel()
        self.num.setFont(theme.font_ui(theme.FS_MICRO))
        self.num.hide()
        self.lay.addWidget(self.num)
        self._hot = False
        self.hover_t = Tween(lambda _v: self.update(), ms=theme.T_FAST, curve=theme.ease)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_values(self, used: int, total: int) -> None:
        if (used, total) == getattr(self, "_values", None):
            return
        self._values = (used, total)
        pct = used / total if total > 0 else 0.0
        self.ring.set_values(used, total)
        with_num = pct >= 0.5
        self.num.setVisible(with_num)
        self.num.setText(f"{round(pct * 100)}%")
        self.lay.setContentsMargins(8 if with_num else 5, 0, 8 if with_num else 5, 0)
        self._hot = pct > 0.85
        self._paint()

    def _paint(self) -> None:
        theme.tint(self.num, fg="err" if self._hot else ("text" if self.underMouse() else "text_3"))
        self.update()

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self.hover_t.go(1.0)
        self._paint()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self.hover_t.go(0.0)
        self._paint()

    def paintEvent(self, _e) -> None:
        if self.hover_t.value <= 0:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.qcolor(t.fill_1)
        c.setAlphaF(c.alphaF() * self.hover_t.value)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


# ---------------------------------------------------------------- the foot (.composer-foot)


class FootHints(QWidget):
    """The key hints under the card: ``↵ to send · Shift + ↵ for a new line · @ files · / commands``
    when idle, the queue/interrupt/stop line while Claude works. 18px tall, centred, fading in and
    out in 180ms. Painted in one go: kbd chips, bold marks and the ↵ glyphs are all segments."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._busy = False
        self._shown = False
        self.opacity = Tween(lambda _v: self.update(), ms=theme.T_MED, curve=theme.ease)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def shown(self) -> bool:
        return self._shown

    def set_state(self, busy: bool, shown: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.update()
        if shown != self._shown:
            self._shown = shown
            self.opacity.go(1.0 if shown else 0.0)

    def segments(self) -> List[Tuple[str, str]]:
        if self._busy:
            return [
                ("icon", "corner-down-left"),
                ("text", "queues for after this step ·"),
                ("kbd", "Ctrl"),
                ("kbd", "Shift"),
                ("icon", "corner-down-left"),
                ("text", "interrupts and sends now ·"),
                ("kbd", "Esc"),
                ("text", "to stop"),
            ]
        return [
            ("icon", "corner-down-left"),
            ("text", "to send ·"),
            ("kbd", "Shift"),
            ("text", "+"),
            ("icon", "corner-down-left"),
            ("text", "for a new line ·"),
            ("b", "@"),
            ("text", "files ·"),
            ("b", "/"),
            ("text", "commands"),
        ]

    def text(self) -> str:
        """The hint as one line (what a reader would say), or "" while hidden."""
        if not self._shown:
            return ""
        return " ".join("↵" if k == "icon" else v for k, v in self.segments())

    def paintEvent(self, _e) -> None:
        op = self.opacity.value
        if op <= 0.003:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(op)
        font = theme.font_ui(theme.FS_MICRO)
        bold = theme.font_ui(theme.FS_MICRO, 600)
        fm, bm = QFontMetrics(font), QFontMetrics(bold)
        kbd_fm = QFontMetrics(theme.font_ui(10, 500))
        segs = self.segments()
        widths: List[float] = []
        for kind, v in segs:
            if kind == "icon":
                widths.append(11)
            elif kind == "kbd":
                widths.append(max(16, kbd_fm.horizontalAdvance(v) + 10))
            elif kind == "b":
                widths.append(bm.horizontalAdvance(v))
            else:
                widths.append(fm.horizontalAdvance(v))
        total = sum(widths) + 4 * (len(segs) - 1)
        x = (self.width() - total) / 2
        h = self.height()
        for (kind, v), w in zip(segs, widths):
            if kind == "icon":
                p.drawPixmap(QPointF(x, (h - 11) / 2 + 0.5), icons.pixmap("corner-down-left", t.text_4, 11))
            elif kind == "kbd":
                paint_kbd(p, x, (h - 16) / 2, v, small=True)
            elif kind == "b":
                p.setFont(bold)
                p.setPen(QColor(t.text_3))
                p.drawText(QRect(int(x), 0, int(math.ceil(w)) + 2, h), Qt.AlignmentFlag.AlignVCenter, v)
            else:
                p.setFont(font)
                p.setPen(QColor(t.text_4))
                p.drawText(QRect(int(x), 0, int(math.ceil(w)) + 2, h), Qt.AlignmentFlag.AlignVCenter, v)
            x += w + 4


# ---------------------------------------------------------------- attachments


def round_x(tooltip: str, disc: bool = False, parent: Optional[QWidget] = None) -> Btn:
    """An 18px round remove button: quiet (``.attach-file button``) or on a dark disc (``.attach-x``)."""
    b = Btn(icon="x", icon_size=11, tooltip=tooltip, parent=parent)
    b.override.update(h=18, px=3.5, r=9)
    if disc:
        b.set_look("accent_ink", bg=lambda _t: QColor(0, 0, 0, 158))
    else:
        b.set_look("text_3", hover_fg="text", hover_bg="fill_3")
    b.setFixedSize(18, 18)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class _Pops(QWidget):
    """Arrives on ``pop-spring-sm`` (scale .55 → 1 with the opacity, snappy spring)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.pop_t = Tween(lambda _v: self.update(), ms=theme.SPRING_SNAPPY_T, curve=theme.spring_snappy)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.pop_t.value == 0.0 and not self.pop_t.running:
            self.pop_t.go(1.0)

    def apply_pop(self, p: QPainter) -> None:
        k = self.pop_t.value
        p.setOpacity(max(0.0, min(1.0, k)))
        cx, cy = self.width() / 2, self.height() / 2
        p.translate(cx, cy)
        s = 0.55 + 0.45 * k
        p.scale(s, s)
        p.translate(-cx, -cy)


class ImageChip(_Pops):
    """A 60px thumbnail with a remove button in its corner (``.attach-img``), shown under the pointer."""

    def __init__(self, img: Json, on_remove: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pm: QPixmap = img["pixmap"]
        self.setFixedSize(60, 60)
        self.setToolTip(f"{img['name']} · {bytes_(img['size'])}")
        self.x = round_x("Remove image", disc=True, parent=self)
        self.x.move(38, 4)
        reveal_on_hover(self, [self.x])
        self.x.clicked.connect(on_remove)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.apply_pop(p)
        path = rounded_path(QRectF(0, 0, 60, 60), theme.R3)
        p.setClipPath(path)
        pm = self._pm.scaled(
            60, 60, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        p.drawPixmap((60 - pm.width()) // 2, (60 - pm.height()) // 2, pm)
        p.setClipping(False)
        p.setPen(QPen(theme.qcolor(t.hair_2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, 59, 59), theme.R3, theme.R3)


class FileChip(_Pops):
    """A text attachment: file icon, name, remove (``.attach-file``): 30px on a fill, up to 260 wide."""

    def __init__(self, f: Json, on_remove: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.name = str(f["name"])
        fm = QFontMetrics(theme.font_ui(theme.FS_SMALL))
        w = min(260, 10 + 13 + 6 + fm.horizontalAdvance(self.name) + 6 + 18 + 6)
        self.setFixedSize(w, 30)
        self.setToolTip(self.name)
        self.x = round_x("Remove file", parent=self)
        self.x.move(w - 6 - 18, 6)
        self.x.clicked.connect(on_remove)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_pop(p)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.fill_2))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)
        p.drawPixmap(QPointF(10, 8.5), icons.pixmap("file", t.text, 13))
        p.setFont(theme.font_ui(theme.FS_SMALL))
        p.setPen(QColor(t.text))
        avail = self.width() - 10 - 13 - 6 - 6 - 18 - 6
        fm = QFontMetrics(p.font())
        p.drawText(
            QRect(29, 0, avail + 2, 30),
            Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(self.name, Qt.TextElideMode.ElideRight, avail),
        )


# ---------------------------------------------------------------- overlays


class GhostHint(QWidget):
    """``.composer-ghost``: a suggested prompt in the empty field, with the Tab chip."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self.fade = Tween(lambda _v: self.update(), ms=theme.T_MED, curve=theme.ease)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_text(self, text: str) -> None:
        if text and not self._text:
            self.fade.jump(0.0)
            self.fade.go(1.0)
        self._text = text
        self.setVisible(bool(text))
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fade.value)
        font = theme.font_ui(theme.FS_BODY)
        fm = QFontMetrics(font)
        kbd_w = max(18, QFontMetrics(theme.font_ui(11, 500)).horizontalAdvance("Tab") + 10)
        avail = max(20, self.width() - 8 - kbd_w)
        shown = fm.elidedText(self._text, Qt.TextElideMode.ElideRight, avail)
        p.setFont(font)
        p.setPen(QColor(t.text_3))
        p.drawText(QRect(0, 0, avail + 2, LINE_H), Qt.AlignmentFlag.AlignVCenter, shown)
        paint_kbd(p, fm.horizontalAdvance(shown) + 8, (LINE_H - 18) / 2 - 1, "Tab")


class DropHint(QWidget):
    """``.drop-hint``: a translucent veil over the composer while files are dragged across it."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.fade = Tween(lambda _v: self.update(), ms=theme.T_FAST, curve=theme.ease)
        self.hide()

    def appear(self) -> None:
        self.fade.jump(0.0)
        self.show()
        self.raise_()
        self.fade.go(1.0)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fade.value)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(t.raised, 0.88))
        p.drawRoundedRect(QRectF(self.rect()), theme.R4, theme.R4)
        p.setFont(theme.font_ui(theme.FS_UI, 500))
        p.setPen(QColor(t.text))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop images or text files to attach")


class FadeOverlay(QWidget):
    """``.composer-wrap::before``: the conversation dissolves into the canvas just above the composer."""

    HEIGHT = 44

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(self.HEIGHT)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        base = theme.qcolor(t.canvas)
        for i in range(0, 11):
            k = i / 10
            c = QColor(base)
            # the gradient to 88% canvas, masked to full by three quarters of the way down
            c.setAlphaF(0.88 * k * min(1.0, k / 0.75))
            g.setColorAt(k, c)
        p.fillRect(self.rect(), g)


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
        # after a send the field settles back to one line on the soft spring
        self.settle_t = Tween(lambda v: self.edit.setFixedHeight(int(v)))
        self.col = theme.COL
        self.setObjectName("composer-wrap")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 0, 14, 0)  # 20px in the CSS, less the box's 6px pads
        outer.setSpacing(0)

        self.queue = QueueTray(session_id)
        self.queue.hide()
        self.queue_host = _centered(self.queue)
        self.queue_host.layout().setContentsMargins(0, 0, 0, 2)  # 8px to the card with the box's pad
        outer.addWidget(self.queue_host)

        # ---- the card
        self.box = ComposerBox(pad_bottom=34)  # the foot's 6 + 18 and the wrap's 10 below the card
        self.box.focus_target = self.focus_input
        self.box.geometry_changed.connect(self._place_overlays)
        self.box.focus_changed.connect(lambda _on: self.refresh_foot())
        self.card = self.box  # the old name
        cl = self.box.body

        self.attach_row = QWidget()
        self.attach_lay = QHBoxLayout(self.attach_row)
        self.attach_lay.setContentsMargins(14, 12, 14, 0)
        self.attach_lay.setSpacing(8)
        self.attach_lay.addStretch(1)
        self.attach_row.hide()
        cl.addWidget(self.attach_row)

        self.edit = ComposerEdit(self)
        self.edit.textChanged.connect(self._on_text_changed)
        self.field = composer_field(self.edit, FIELD_MIN)
        self.ghost = GhostHint(self.field)
        self.ghost.move(FIELD_PAD[0], FIELD_PAD[1] + 2)
        self.ghost.hide()
        cl.addWidget(self.field)

        bar = ComposerBar()
        bl = bar.lay
        self.attach_btn = ChipButton("Attach images or files", icon_only=True)
        self.attach_btn.set("", "plus")
        self.attach_btn.clicked.connect(self._pick_files)
        bl.addWidget(self.attach_btn)
        self.mode_btn = ChipButton("Permission mode (Shift+Tab to switch)")
        self.mode_btn.clicked.connect(self._open_mode_menu)
        bl.addWidget(self.mode_btn)
        bl.addStretch(1)
        self.model_btn = ChipButton("Model and effort")
        self.model_btn.clicked.connect(self._open_model_menu)
        bl.addWidget(self.model_btn)
        self.ctx_btn = CtxButton()
        self.ctx_btn.clicked.connect(lambda: store.set_ui(panel=True, panel_tab="info"))
        self.ring = self.ctx_btn.ring
        self.ctx_num = self.ctx_btn.num
        self.ctx_btn.hide()
        bl.addWidget(self.ctx_btn)
        bl.addSpacing(2)  # margin-left: 4px with the bar's 2px gap
        self.send_btn = SendButton()
        self.send_btn.clicked.connect(self._send_or_stop)
        bl.addWidget(self.send_btn)
        cl.addWidget(bar)
        self.bar = bar
        self.box_host = _centered(self.box)
        outer.addWidget(self.box_host)

        self.foot = FootHints(self)  # placed by hand over the box's bottom pad

        self.drop_hint = DropHint(self)

        host = parent if parent is not None else self
        # The popup paints over the conversation above the card, so it lives on the session page, not in here.
        self.popup = SuggestPopup(host)
        self.popup.on_pick = self.apply_suggestion
        self.popup.on_hover = self._hover_suggestion
        self.fade = FadeOverlay(host) if parent is not None else None
        self.menu: Optional[MenuPopover] = None  # the mode or model menu, while it is up

        self.edit.setPlainText(_drafts.get(session_id, ""))
        self.edit.apply_line_height()
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
        # read many times a keystroke (the draft, the popup, the controls): once per change
        cached = getattr(self, "_text_cache", None)
        if cached is None:
            cached = self._text_cache = self.edit.toPlainText()
        return cached

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip() or self.images or self.files)

    def set_cwd(self, cwd: str) -> None:
        self.cwd = cwd

    def focus_input(self) -> None:
        self.edit.setFocus()

    def _focus_if_free(self) -> None:
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
        self.edit.apply_line_height()
        c = self.edit.textCursor()
        c.setPosition(len(text) if caret is None else max(0, min(len(text), caret)))
        self.edit.setTextCursor(c)
        self._on_text_changed(detect=False)

    def _on_rt(self, sid: str) -> None:
        if sid == self.sid:
            self.refresh()

    def _on_text_changed(self, detect: bool = True) -> None:
        self._text_cache = None
        _drafts[self.sid] = self.text
        if detect:
            self.hist_pos = -1
            self.detect_popup()
        self._autosize()
        self.refresh()

    # ---------------------------------------------------------------- rendering
    def _paint(self) -> None:
        self.refresh()
        self.update()

    def refresh(self) -> None:
        """Re-read the runtime and config and bring every control up to date."""
        r = self.rt
        busy = self.busy
        held = self.held
        mode = self.mode
        info = next((m for m in MODES if m["id"] == mode), MODES[0])
        self.mode_btn.set(info["label"], info["icon"], "", (info["tone"] or "set") if mode != "default" else "")
        self.box.set_tone("plan" if mode == "plan" else "err" if mode == "bypassPermissions" else "")
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
            self.ctx_btn.set_values(used, total)
            if usage.get("afterCompact"):
                # the context was compacted; what it holds now is measured at the next reply
                self.ctx_btn.setToolTip("Context compacted — its size is measured at the next reply")
            else:
                tip = f"Context {round(pct * 100)}% used · {context_label(used, total)}"
                self.ctx_btn.setToolTip(tip + (" — /compact frees space" if pct >= 0.5 else ""))
        has = self.has_content
        self.send_btn.set_state(has, busy and not has, self.send_with_enter)
        suggestion = str(r.get("suggestion") or "") if r and not self.text else ""
        if suggestion:
            self.ghost.resize(max(40, self.edit.width()), LINE_H)
            self.ghost.raise_()
        self.ghost.set_text(suggestion)
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

    def refresh_foot(self) -> None:
        """Key hints appear only on a focused, empty composer (and while Claude works); typing clears them."""
        busy = self.busy
        self.foot.set_state(busy, busy or (self.box.focused and not self.text))

    def _autosize(self) -> None:
        """The field grows with its content up to 42% of the window and settles back after a send."""
        want = self.edit.content_height()
        win = self.window()
        cap = max(LINE_H, round((win.height() if win is not None else 800) * 0.42) - FIELD_PAD[1] - FIELD_PAD[3])
        prev = self._last_h
        nxt = min(max(FIELD_MIN - FIELD_PAD[1] - FIELD_PAD[3], want), cap)
        self._last_h = nxt
        self.settle_t.stop()
        if not self.text and prev > nxt + 2 and time.time() - self._sent_at < 0.6 and not store.reduced_motion:
            self.settle_t.start(prev, nxt, theme.SPRING_SOFT_T, theme.spring_soft())
        else:
            self.edit.setFixedHeight(nxt)
        self._place_popup()

    def _apply_column(self) -> None:
        """The card, the queue and the foot take the conversation column of this pane."""
        col = column_width(self.width())
        if col != self.col or self.box.maximumWidth() != col + 2 * ComposerBox.PAD_X:
            self.col = col
            self.box.setFixedWidth(col + 2 * ComposerBox.PAD_X)
            self.queue.setFixedWidth(col)

    def _place_overlays(self) -> None:
        """The foot sits in the box's bottom pad; the drop veil and the fade follow the card."""
        g = self.box.geometry()
        host = self.box_host.geometry()
        card_bottom = host.y() + g.y() + g.height() - self.box.pad_bottom
        self.foot.setGeometry(host.x() + g.x() + ComposerBox.PAD_X, card_bottom + 6, self.col, 18)
        self.foot.raise_()
        top = self.queue_host.geometry().y() if self.queue.isVisible() else host.y() + g.y() + ComposerBox.PAD_TOP
        self.drop_hint.setGeometry(20, top, max(0, self.width() - 40), max(0, self.height() - 6 - top))
        if self.fade is not None:
            self.fade.setGeometry(self.x(), self.y() - FadeOverlay.HEIGHT, self.width(), FadeOverlay.HEIGHT)
            self.fade.raise_()
        self._place_popup()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_column()
        self.mode_btn.set_compact(self.width() < 420)
        self._autosize()
        self._place_overlays()

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self._place_overlays()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.fade is not None:
            self.fade.show()
        self._place_overlays()

    def hideEvent(self, e) -> None:
        self.popup.hide()
        if self.menu is not None:
            self.menu.dismiss()
        if self.fade is not None:
            self.fade.hide()
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
        self.send_btn.launch()
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
            self.popup.pop_out()
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
            files = [{"key": f, "label": f, "hint": "", "kind": "dir" if f.endswith("/") else "file"} for f in lst]
            # registered repositories (REPOSITORIES.md) answer to their names and the person's aliases
            repos = [
                {
                    "key": str(e.get("name") or ""),
                    "label": str(e.get("name") or ""),
                    "hint": str(e.get("about") or tildify(str(e.get("path") or ""), (store.info or {}).get("home"))),
                    "kind": "repo",
                }
                for e in store.repos
                if e.get("name") and (not q or word_match(q, [e.get("name")] + list(e.get("aliases") or [])))
            ]
            self.suggestions = repos[:8] + files
            head = ("Repositories and files" if repos else "Files") if q else "Files — keep typing to narrow down"
        if not self.suggestions:
            self.popup.pop_out()
            return
        p["index"] = min(p["index"], len(self.suggestions) - 1)
        self.popup.set_items(head, self.suggestions, p["index"])
        self._place_popup()
        if not self.popup.isVisible():
            self.popup.pop_in()
        else:
            self.popup.raise_()

    def _place_popup(self) -> None:
        if not self.popup.isVisible() and not self.popup_state:
            return
        host = self.popup.parentWidget()
        if host is None:
            return
        # 2px above the wrap's top (the queue when there is one, else the card), the pane's column wide
        w = column_width(self.width() + 40)  # resolved against the wrap's padding box
        w = min(w, self.width() - 40)
        top_left = self.mapTo(host, QPoint(0, 0))
        top = self.queue_host.geometry().y() if self.queue.isVisible() else self.box_host.geometry().y() + 6
        h = self.popup.total_height()
        x = top_left.x() + (self.width() - w) // 2
        self.popup.place(QRect(x, max(0, top_left.y() + top - 2 - h), w, h))

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
        self.popup.pop_out()

    def close_popup_if_unfocused(self) -> None:
        if not self.edit.hasFocus():
            self.close_popup()

    # ---------------------------------------------------------------- menus
    def _toggle_menu(self, anchor: ChipButton, items: Callable[[], List[Json]], width: int, align_right: bool = False):
        """A chip's menu, over the card; the chip closes it again and turns its chevron while it is up."""
        if anchor.is_open:
            close_menus()
            return
        self.menu = show_menu(items(), anchor, width, align_right=align_right, above=True)
        if self.menu is not None:
            anchor.set_open(True)
            self.menu.closed.connect(lambda: anchor.set_open(False))

    def _open_mode_menu(self) -> None:
        self._toggle_menu(self.mode_btn, self.mode_menu_items, 372)

    def mode_menu_items(self) -> List[Json]:
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
        return items

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
                        "label": trim_claude(str(name)) if name else m["id"],
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
        if not self.model_btn.is_open:
            store.load_gateway_models()
        self._toggle_menu(self.model_btn, self.model_menu_items, 320, align_right=True)

    # ---------------------------------------------------------------- attachments
    def set_dragging(self, on: bool) -> None:
        if on == self._dragging:
            return
        self._dragging = on
        self.box.set_dragging(on)
        if on:
            self._place_overlays()
            self.drop_hint.appear()
        else:
            self.drop_hint.hide()

    def _pick_files(self) -> None:
        paths, _f = QFileDialog.getOpenFileNames(self, "Attach images or files")
        if paths:
            self.add_files(paths)

    def add_files(self, paths: List[str]) -> None:
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
                self.attach_lay.count() - 1,
                ImageChip(img, lambda _checked=False, i=img: self._remove(i, self.images)),
            )
        for f in self.files:
            self.attach_lay.insertWidget(
                self.attach_lay.count() - 1,
                FileChip(f, lambda _checked=False, x=f: self._remove(x, self.files)),
            )
        self.attach_row.setVisible(bool(self.images or self.files))
        self.refresh()

    def _remove(self, item: Json, lst: List[Json]) -> None:
        if item in lst:
            lst.remove(item)
        self._render_attachments()


def _centered(w: QWidget) -> QWidget:
    """A row that keeps ``w`` centred, like ``max-width + margin: auto``."""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    lay.addWidget(w, 0)
    lay.addStretch(1)
    return host
