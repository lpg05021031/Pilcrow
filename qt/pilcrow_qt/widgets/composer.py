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
``KbdChip`` and ``paint_kbd`` draw ``<kbd>`` chips; ``MenuPopup`` is the web's ``Popover + Menu``.
"""

from __future__ import annotations

import base64
import math
import re
import time
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from PySide6.QtCore import (
    QBuffer,
    QEasingCurve,
    QEvent,
    QIODevice,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import bytes_, short_model
from ..lib.fuzzy import fuzzy_filter
from ..store import store
from .ui.bits import ContextRing, context_label
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

#: ``.composer-input``: 14.5px on a 1.55 line (22.475px), padding 14px 16px 6px, at least 50px tall in all.
LINE_H = 22.475
FIELD_PAD = (16, 14, 16, 6)
FIELD_MIN = 50

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


# ---------------------------------------------------------------- colours, shadows, motion


def column_width(pane_width: int) -> int:
    """The conversation column of a pane this wide: ``clamp(640px, min(61.8%, 100% - 48px), 1080px)``
    (app.css ``.main --col``), the golden section that Claude's own desktop app settles on. The
    composer resolves it against the ``.composer-wrap`` content box, 20px in from each side."""
    avail = max(0, pane_width - 40)
    col = max(640.0, min(0.618 * avail, avail - 48.0))
    return int(round(min(col, 1080.0, max(avail, 0))))


def _mix(a: str, b: str, pct: float) -> QColor:
    """CSS ``color-mix(in srgb, a pct, b)`` for two opaque tokens."""
    x, y = theme.qcolor(a), theme.qcolor(b)
    return QColor(
        int(round(x.red() * pct + y.red() * (1 - pct))),
        int(round(x.green() * pct + y.green() * (1 - pct))),
        int(round(x.blue() * pct + y.blue() * (1 - pct))),
    )


def _color_mix(a: str, pa: float, b: str) -> QColor:
    """CSS ``color-mix(in srgb, a pa%, b)`` with alpha: premultiplied interpolation, as the browser does it."""
    x, y = theme.qcolor(a), theme.qcolor(b)
    pb = 1 - pa
    alpha = pa * x.alphaF() + pb * y.alphaF()
    if alpha <= 0:
        return QColor(0, 0, 0, 0)

    def ch(u: int, v: int) -> int:
        return int(round((pa * x.alphaF() * u + pb * y.alphaF() * v) / alpha))

    c = QColor(ch(x.red(), y.red()), ch(x.green(), y.green()), ch(x.blue(), y.blue()))
    c.setAlphaF(alpha)
    return c


def _alpha(c: str, a: float) -> QColor:
    q = theme.qcolor(c)
    q.setAlphaF(a)
    return q


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
    )


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@lru_cache(maxsize=48)
def _shadow_pixmap(w: int, h: int, blur: int, alpha: float, rgb: Tuple[int, int, int] = (0, 0, 0)) -> QPixmap:
    """A CSS box shadow of a ``w``×``h`` box blurred by ``blur`` (σ = blur/2), as a pixmap with ``blur``
    pixels of margin on every side. The Gaussian of a box is separable, so it is one row profile and
    one column profile multiplied: a few hundred ``erf`` calls and one blit per row, not a blur pass.
    The corners come out square rather than rounded; under a 16px radius and an 18px sigma the
    difference is a level or two in the darkest corner."""
    ext = blur
    tw, th = w + 2 * ext, h + 2 * ext
    sigma = max(0.5, blur / 2.0)

    def profile(n: int, x0: float, x1: float) -> List[float]:
        return [_phi((i + 0.5 - x0) / sigma) - _phi((i + 0.5 - x1) / sigma) for i in range(n)]

    fx = profile(tw, ext, ext + w)
    fy = profile(th, ext, ext + h)
    r, g, b = rgb
    row = bytearray()
    for v in fx:
        a = max(0, min(255, int(round(255 * alpha * v))))
        # premultiplied BGRA, little-endian ARGB32
        row += bytes((b * a // 255, g * a // 255, r * a // 255, a))
    row_img = QImage(bytes(row), tw, 1, tw * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    img = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, v in enumerate(fy):
        if v < 0.002:
            continue
        p.setOpacity(v)
        p.drawImage(0, y, row_img)
    p.end()
    return QPixmap.fromImage(img)


def draw_box_shadow(
    p: QPainter,
    rect: QRectF,
    dy: float,
    blur: int,
    spread: float,
    alpha: float,
    opacity: float = 1.0,
    rgb: Tuple[int, int, int] = (0, 0, 0),
) -> None:
    """``box-shadow: 0 dy blur spread rgba(rgb, alpha)`` under ``rect`` (the element's border box)."""
    if opacity <= 0.003 or alpha <= 0:
        return
    sr = rect.adjusted(-spread, -spread, spread, spread).translated(0, dy)
    w, h = int(round(sr.width())), int(round(sr.height()))
    if w <= 0 or h <= 0:
        return
    pm = _shadow_pixmap(w, h, blur, alpha, rgb)
    p.setOpacity(opacity)
    p.drawPixmap(QPointF(sr.x() - blur, sr.y() - blur), pm)
    p.setOpacity(1.0)


def _rounded(r: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path


class Tween(QObject):
    """One animated number. ``go(target)`` eases from the current value on ``curve`` over ``duration``;
    with reduced motion it lands at once. ``on_frame`` gets every value, ``on_done`` the end."""

    def __init__(self, owner: QWidget, duration: int, curve: Callable[[], Any], on_frame: Callable[[float], None]):
        super().__init__(owner)
        self.value = 0.0
        self._duration = duration
        self._curve = curve
        self._on_frame = on_frame
        self._anim: Optional[QVariantAnimation] = None
        self._on_done: Optional[Callable[[], None]] = None

    def set(self, v: float) -> None:
        self.stop()
        self.value = v
        self._on_frame(v)

    def go(self, target: float, on_done: Optional[Callable[[], None]] = None, duration: Optional[int] = None) -> None:
        self.stop()
        self._on_done = on_done
        if store.reduced_motion or abs(target - self.value) < 1e-6:
            self.value = target
            self._on_frame(target)
            self._finish()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self.value))
        anim.setEndValue(float(target))
        anim.setDuration(duration if duration is not None else self._duration)
        anim.setEasingCurve(self._curve())
        anim.valueChanged.connect(self._frame)
        anim.finished.connect(self._finish)
        self._anim = anim
        anim.start()

    def stop(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None

    @property
    def running(self) -> bool:
        return self._anim is not None

    def _frame(self, v: Any) -> None:
        self.value = float(v)
        self._on_frame(self.value)

    def _finish(self) -> None:
        if self._anim is not None:
            self._anim.deleteLater()
            self._anim = None
        done, self._on_done = self._on_done, None
        if done is not None:
            done()


# ---------------------------------------------------------------- <kbd>


def paint_kbd(p: QPainter, x: float, y: float, text: str, small: bool = False) -> float:
    """Draw a ``<kbd>`` chip with its left edge at ``x`` and its top at ``y``; returns its width.
    ``small`` is the composer-foot flavour (16px, outlined); the base one is 18px on a fill."""
    t = theme.tokens()
    h = 16 if small else 18
    font = theme.font_ui(10 if small else 11, 500)
    fm = QFontMetrics(font)
    w = max(h, fm.horizontalAdvance(text) + 10)
    r = QRectF(x, y, w, h)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    if small:
        p.setPen(QPen(theme.qcolor(t.hair_2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), theme.R1, theme.R1)
    else:
        p.setBrush(theme.qcolor(t.fill_2))
        p.drawRoundedRect(r, theme.R1, theme.R1)
        p.setClipPath(_rounded(r, theme.R1))
        p.fillRect(QRectF(x, y + h - 1, w, 1), theme.qcolor(t.hair))
        p.setClipping(False)
    p.setFont(font)
    p.setPen(QColor(t.text_3))
    p.drawText(QRect(int(x), int(y), int(w), h), Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return w


class KbdChip(QWidget):
    """A ``<kbd>`` as a widget, for layouts."""

    def __init__(self, text: str, small: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text, self._small = text, small
        fm = QFontMetrics(theme.font_ui(10 if small else 11, 500))
        h = 16 if small else 18
        self.setFixedSize(max(h, fm.horizontalAdvance(text) + 10), h)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint_kbd(p, 0, 0, self._text, self._small)


# ---------------------------------------------------------------- the box (.composer)


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
        self.focus_t = Tween(self, theme.SPRING_SOFT_T, theme.spring_soft, self._on_focus_frame)
        app = QApplication.instance()
        if app is not None:
            bind(app.focusChanged, self, self._on_focus_changed)
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
        self.focus_t.go(1.0 if focused else 0.0)
        self.focus_changed.emit(focused)

    def _on_focus_frame(self, t: float) -> None:
        lift = 1 if t > 0.5 and not self._dragging else 0
        if lift != self._lift:
            self._lift = lift
            self.body.setContentsMargins(self.PAD_X, self.PAD_TOP - lift, self.PAD_X, self.pad_bottom + lift)
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.focus_target is not None and e.button() == Qt.MouseButton.LeftButton:
            self.focus_target()
        super().mousePressEvent(e)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.geometry_changed.emit()

    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        self.geometry_changed.emit()

    # ---- paint
    def ring_colors(self, t: float) -> Tuple[QColor, QColor]:
        """(ring, halo) for focus progress ``t`` and the current tone."""
        tk = theme.tokens()
        tone = self._tone
        if tone == "plan":
            idle, focus = _color_mix(tk.plan, 0.5, tk.hair_2), _color_mix(tk.plan, 0.65, tk.hair_3)
            halo = _alpha(tk.plan, 0.09)
        elif tone == "err":
            idle, focus = _color_mix(tk.err, 0.45, tk.hair_2), _color_mix(tk.err, 0.6, tk.hair_3)
            halo = _alpha(tk.err, 0.08)
        else:
            idle, focus = theme.qcolor(tk.hair_2), theme.qcolor(tk.hair_3)
            halo = _alpha(tk.accent, 0.08 if not tk.dark else 0.07)
        halo.setAlphaF(halo.alphaF() * max(0.0, min(1.0, t)))
        return _lerp(idle, focus, t), halo

    def paintEvent(self, _e) -> None:  # noqa: N802
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
            p.setClipPath(_rounded(r, theme.R4))
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
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self.apply_line_height()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setFont(theme.font_ui(theme.FS_BODY))
        # the size goes through QSS too: the app sheet's `* { font-size }` would override setFont
        self.setStyleSheet(
            f"ComposerEdit {{ background: transparent; border: none; padding: 0; color: {t.text};"
            f" font-size: {int(theme.FS_BODY + 0.5)}px;"
            f" selection-background-color: {theme.blend(t.selection, t.raised).name()}; }}"
        )
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, theme.qcolor(t.text_3))
        self.setPalette(pal)

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
        """What the text wants, in whole lines."""
        doc = self.document()
        doc.setTextWidth(max(10, self.viewport().width()))
        line = max(LINE_H, QFontMetrics(self.font()).ascent() + QFontMetrics(self.font()).descent())
        lines = max(1, int(round(doc.documentLayout().documentSize().height() / line)))
        return int(math.ceil(lines * line))

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
        handle = getattr(self._owner, "handle_key", None)
        if handle is not None and handle(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        super().mouseReleaseEvent(e)
        self._call("detect_popup")

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self._call("refresh_foot")

    def focusOutEvent(self, e) -> None:  # noqa: N802
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
    def canInsertFromMimeData(self, src: QMimeData) -> bool:  # noqa: N802
        return src.hasImage() or src.hasUrls() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src: QMimeData) -> None:  # noqa: N802
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

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            self._call("set_dragging", True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._call("set_dragging", False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._call("set_dragging", False)
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
        self.text_lb.setVisible(not icon_only)
        lay.addWidget(self.text_lb)
        self.sub_lb = QLabel()
        self.sub_lb.hide()
        lay.addWidget(self.sub_lb)
        # the chevron is painted (it rotates); this keeps its place: 12px less the -2px margin
        self.chev = QLabel()
        self.chev.setFixedSize(10, 12)
        self.chev.setVisible(self._chevron)
        lay.addWidget(self.chev)
        if icon_only:
            self.setFixedWidth(30)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hover_t = Tween(self, theme.T_FAST, theme.ease, lambda _v: self.update())
        self.chev_t = Tween(self, theme.SPRING_SNAPPY_T, theme.spring_snappy, lambda _v: self.update())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set(self, text: str, icon: str = "", sub: str = "", tone: str = "") -> None:
        self.text_lb.setText(text)
        self.text_lb.setVisible(bool(text) and not self._icon_only)
        self.sub_lb.setText(f"· {sub}" if sub else "")
        self.sub_lb.setVisible(bool(sub))
        self._icon, self._tone = icon, tone
        self.ico.setVisible(bool(icon))
        self._paint()

    def set_open(self, on: bool) -> None:
        if on == self._open:
            return
        self._open = on
        self.chev_t.go(1.0 if on else 0.0)
        self.update()

    @property
    def is_open(self) -> bool:
        return self._open

    def color(self) -> str:
        t = theme.tokens()
        tone = {"accent": t.accent_text, "plan": t.plan, "info": t.info, "err": t.err, "set": t.text}.get(self._tone)
        if tone:
            return tone
        return t.text if (self.underMouse() or self._open) else t.text_2

    def _paint(self) -> None:
        t = theme.tokens()
        color = self.color()
        if self._icon:
            self.ico.setPixmap(icons.pixmap(self._icon, color, self._icon_size))
        self.text_lb.setStyleSheet(f"color: {color}; font-size: {theme.FS_SMALL}px; font-weight: 500;")
        self.sub_lb.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px; font-weight: 400;")
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self.hover_t.go(1.0)
        self._paint()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self.hover_t.go(0.0)
        self._paint()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._pressed = True
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._pressed = False
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
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


class SendButton(QPushButton):
    """Send ↔ stop: one round button. Empty = quiet outline, typed = orange arrow (it arrives on the
    bouncy spring, and launches when a message goes), working = stop square (the glyphs morph on the
    snappy spring, the colours cross over in 180ms)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = "idle"
        self._hover = False
        self._pressed = False
        self.setFixedSize(32, 33)  # one pixel under the circle for its drop shadow
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")
        self._bg_from = QColor(0, 0, 0, 0)
        self._bg_to = QColor(0, 0, 0, 0)
        self._ink_from = QColor(0, 0, 0, 0)
        self._ink_to = QColor(0, 0, 0, 0)
        self.bg_t = Tween(self, theme.T_MED, theme.ease, lambda _v: self.update())
        self.stop_t = Tween(self, theme.SPRING_SNAPPY_T, theme.spring_snappy, lambda _v: self.update())
        self.ready_t = Tween(self, theme.SPRING_BOUNCY_T, theme.spring_bouncy, lambda _v: self.update())
        self.ready_t.set(1.0)
        self.launch_t = Tween(self, 460, theme.ease, lambda _v: self.update())
        bind(theme.theme.changed, self, lambda _t: self._retarget(jump=True))
        self.set_state(False, False, True)
        self._retarget(jump=True)

    def set_state(self, ready: bool, stop: bool, send_with_enter: bool) -> None:
        prev = self.state
        self.state = "stop" if stop else "ready" if ready else "idle"
        self.setEnabled(ready or stop)
        tip = "Stop (Esc)" if stop else "Send (Enter)" if send_with_enter else "Send (Ctrl+Enter)"
        self.setToolTip(tip)
        self.setAccessibleName("Stop" if stop else "Send")
        if self.state != prev:
            if self.state == "ready":
                self.ready_t.set(0.0)
                self.ready_t.go(1.0)
            self.stop_t.go(1.0 if self.state == "stop" else 0.0)
            self._retarget()
        self.update()

    def sizeHint(self):  # noqa: N802
        return QSize(32, 33)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(32, 33)

    def launch(self) -> None:
        """The arrow flies up and comes back from below: a message went out."""
        self.launch_t.set(0.0)
        self.launch_t.go(1.0)

    def _targets(self) -> Tuple[QColor, QColor]:
        t = theme.tokens()
        if self.state == "stop":
            return QColor(t.text), QColor(t.canvas)
        if self.state == "ready":
            return QColor(t.accent_2 if self._hover else t.accent), QColor(t.accent_ink)
        return QColor(0, 0, 0, 0), QColor(t.text_3)

    def _retarget(self, jump: bool = False) -> None:
        bg, ink = self._targets()
        cur_bg = _lerp(self._bg_from, self._bg_to, self.bg_t.value)
        cur_ink = _lerp(self._ink_from, self._ink_to, self.bg_t.value)
        self._bg_from, self._bg_to = cur_bg, bg
        self._ink_from, self._ink_to = cur_ink, ink
        self.bg_t.set(0.0)
        if jump:
            self.bg_t.set(1.0)
        else:
            self.bg_t.go(1.0)

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self._hover = True
        self._retarget()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._hover = False
        self._retarget()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._pressed = True
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = _lerp(self._bg_from, self._bg_to, self.bg_t.value)
        ink = _lerp(self._ink_from, self._ink_to, self.bg_t.value)
        scale = 1.0
        if self.state == "ready":
            scale = 0.82 + 0.18 * self.ready_t.value
        if self._pressed and self.isEnabled():
            scale *= 0.9
        p.translate(16, 16)
        p.scale(scale, scale)
        p.translate(-16, -16)
        circle = QRectF(0, 0, 32, 32)
        ready_a = bg.alphaF() if self.state == "ready" else 0.0
        if self.state == "ready" and ready_a > 0:
            # box-shadow: 0 1px 2px rgba(0,0,0,.25)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(64 * ready_a)))
            p.drawEllipse(circle.translated(0, 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(circle)
        if self.state == "ready" and ready_a > 0:
            # inset 0 1px 0 rgba(255,255,255,.2)
            p.setClipPath(_rounded(circle, 16))
            p.fillRect(QRectF(0, 0, 32, 1), QColor(255, 255, 255, int(51 * ready_a)))
            p.setClipping(False)
        if self.state == "idle" or (self.state == "ready" and self.bg_t.value < 1):
            # box-shadow: inset 0 0 0 1px var(--hair-2), fading as the fill arrives
            ring = theme.qcolor(t.hair_2)
            if self.state == "ready":
                ring.setAlphaF(ring.alphaF() * (1 - self.bg_t.value))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1))
            p.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
        # the glyphs: arrow ↔ stop square
        s = self.stop_t.value
        arrow_a = 1 - s
        if arrow_a > 0.01:
            pm = icons.pixmap("arrow-up", ink, 16, stroke=2.25)
            p.save()
            lt = self.launch_t.value
            dy, sc, op = 0.0, 1.0, 1.0
            if self.launch_t.running or 0 < lt < 1:
                if lt < 0.38:
                    k = lt / 0.38
                    dy, sc, op = -16 * k, 1 - 0.1 * k, 1 - k
                elif lt < 0.42:
                    dy, sc, op = 12.0, 0.9, 0.0
                else:
                    k = (lt - 0.42) / 0.58
                    dy, sc, op = 12 * (1 - k), 0.9 + 0.1 * k, k
            p.setOpacity(arrow_a * op)
            p.translate(16, 16 + dy + 4 * s)
            k2 = (0.5 + 0.5 * arrow_a) * sc
            p.scale(k2, k2)
            p.drawPixmap(QPointF(-8, -8), pm)
            p.restore()
        if s > 0.01:
            p.save()
            p.setOpacity(s)
            p.translate(16, 16)
            k3 = 0.5 + 0.5 * s
            p.scale(k3, k3)
            p.setPen(QPen(ink, 0.92))
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(-4.125, -4.125, 8.25, 8.25), 0.92, 0.92)
            p.restore()


class CtxButton(LayoutButton):
    """``.ctx-btn``: the context ring, with the percentage once the window is half full."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFlat(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Context window usage")
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(5, 0, 5, 0)
        self.lay.setSpacing(5)
        self.ring = ContextRing(16)
        self.lay.addWidget(self.ring)
        self.num = QLabel()
        self.num.hide()
        self.lay.addWidget(self.num)
        self._hot = False
        self.hover_t = Tween(self, theme.T_FAST, theme.ease, lambda _v: self.update())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_values(self, used: int, total: int) -> None:
        pct = used / total if total > 0 else 0.0
        self.ring.set_values(used, total)
        with_num = pct >= 0.5
        self.num.setVisible(with_num)
        self.num.setText(f"{round(pct * 100)}%")
        self.lay.setContentsMargins(8 if with_num else 5, 0, 8 if with_num else 5, 0)
        self._hot = pct > 0.85
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        color = t.err if self._hot else (t.text if self.underMouse() else t.text_3)
        self.num.setStyleSheet(f"color: {color}; font-size: {theme.FS_MICRO}px;")
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self.hover_t.go(1.0)
        self._paint()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self.hover_t.go(0.0)
        self._paint()

    def paintEvent(self, _e) -> None:  # noqa: N802
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
        self.opacity = Tween(self, theme.T_MED, theme.ease, lambda _v: self.update())
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

    def paintEvent(self, _e) -> None:  # noqa: N802
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


# ---------------------------------------------------------------- floating material


def draw_glass_panel(p: QPainter, panel: QRectF, radius: float, pop_t: float = 1.0) -> None:
    """A popover or suggest list (``.popover``, ``.suggest``): the glass fill and ``--shadow-glass``,
    a dark half-pixel edge, a light inner edge lit along the top, and two soft shadows beneath."""
    t = theme.tokens()
    draw_box_shadow(p, panel, 20, 50, -14, 0.8 * pop_t)
    draw_box_shadow(p, panel, 6, 16, -8, 0.5 * pop_t)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.blend(t.glass, t.canvas))
    p.drawRoundedRect(panel, radius, radius)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 77), 1))  # 0.5px at .6 → one pixel at .3
    p.drawRoundedRect(panel.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)
    p.setPen(QPen(QColor(255, 255, 255, 12), 1))
    p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    p.setClipPath(_rounded(panel, radius))
    p.fillRect(QRectF(panel.x(), panel.y(), panel.width(), 1), theme.qcolor(t.edge_hi))
    p.setClipping(False)


class _Ghost(QWidget):
    """A snapshot of a popup that plays its exit (``pop-out-down``) after the real widget has gone."""

    def __init__(self, parent: QWidget, pm: QPixmap, geometry: QRect, origin: QPointF) -> None:
        super().__init__(parent)
        self._pm = pm
        self._origin = origin
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(geometry)
        self.show()
        self.raise_()
        self.t = Tween(self, theme.T_EXIT, theme.ease_exit, lambda _v: self.update())
        self.t.go(1.0, on_done=self.deleteLater)

    def paintEvent(self, _e) -> None:  # noqa: N802
        k = self.t.value
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(1 - k)
        p.translate(self._origin.x(), self._origin.y() + 2 * k)
        p.scale(1 - 0.03 * k, 1 - 0.03 * k)
        p.translate(-self._origin.x(), -self._origin.y())
        p.drawPixmap(0, 0, self._pm)


class _TextRef:
    """A text holder with a QLabel's ``text()``/``setText()``, for painted widgets."""

    def __init__(self) -> None:
        self._t = ""

    def text(self) -> str:
        return self._t

    def setText(self, t: str) -> None:  # noqa: N802
        self._t = t


class _FloatingPanel(QWidget):
    """A painted floating panel with shadow margins around it. Presses in the margins go to whatever
    is beneath (the margins are transparent, like a CSS shadow)."""

    M_X = 28
    M_TOP = 20
    M_BOTTOM = 36
    RADIUS = theme.R3

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pop_t = Tween(self, theme.SPRING_SNAPPY_T, theme.spring_snappy, lambda _v: self.update())
        self.hide()

    def panel_rect(self) -> QRectF:
        return QRectF(self.M_X, self.M_TOP, self.width() - 2 * self.M_X, self.height() - self.M_TOP - self.M_BOTTOM)

    def place(self, panel: QRect) -> None:
        self.setGeometry(panel.adjusted(-self.M_X, -self.M_TOP, self.M_X, self.M_BOTTOM))

    def pop_in(self) -> None:
        self.pop_t.set(0.0)
        self.show()
        self.raise_()
        self.pop_t.go(1.0)

    def pop_out(self) -> None:
        """Hide now; a ghost plays the exit."""
        if not self.isVisible():
            return
        parent = self.parentWidget()
        if not store.reduced_motion and parent is not None and self.pop_t.value > 0.5:
            pr = self.panel_rect()
            _Ghost(parent, self.grab(), self.geometry(), QPointF(pr.center().x(), pr.bottom()))
        self.hide()

    def forward_press(self, e: QMouseEvent) -> None:
        """A press in the shadow margin: give it to the widget underneath."""
        parent = self.parentWidget()
        if parent is None:
            return
        gp = e.globalPosition().toPoint()
        # widgetAt looks through mouse-transparent widgets: step aside for the lookup
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        target = QApplication.widgetAt(gp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        if target is None or target is self or self.isAncestorOf(target):
            return
        local = target.mapFromGlobal(gp)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            ev = QMouseEvent(kind, QPointF(local), QPointF(gp), e.button(), e.buttons(), e.modifiers())
            QApplication.sendEvent(target, ev)

    def apply_pop(self, p: QPainter) -> None:
        """``pop-spring-up``: scale .94 → 1 and 3px up, from the bottom centre, with the opacity."""
        k = self.pop_t.value
        pr = self.panel_rect()
        p.setOpacity(max(0.0, min(1.0, k)))
        ox, oy = pr.center().x(), pr.bottom()
        p.translate(ox, oy + 3 * (1 - k))
        s = 0.94 + 0.06 * k
        p.scale(s, s)
        p.translate(-ox, -oy)


# ---------------------------------------------------------------- the @ and / popup


class SuggestPopup(_FloatingPanel):
    """The list of files or commands above the field (``.suggest``). Keyboard-driven by the Composer;
    rows light under the pointer, a click picks. Painted whole, so it arrives on the snappy spring."""

    ROW = 32
    HEAD = 27  # padding 4px 8px 6px around an 11px line
    PAD = 6

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("suggest")
        self.items: List[Json] = []
        self.index = 0
        self.scroll = 0
        self.head = _TextRef()
        self.name_w = 0
        self.on_pick: Optional[Callable[[Json], None]] = None
        self.on_hover: Optional[Callable[[int], None]] = None
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- data
    def set_items(self, head: str, items: List[Json], index: int) -> None:
        self.head.setText(head)
        self.items = items[:40]
        name_ch = min(30, max([8] + [len(x["label"]) for x in self.items if x["kind"] == "cmd"]))
        fm = QFontMetrics(theme.font_mono(12.5))
        self.name_w = int((name_ch + 1) * fm.horizontalAdvance("0"))
        self.scroll = 0
        self.set_index(index)
        self.update()

    def list_height(self) -> int:
        return min(320, self.ROW * len(self.items))

    def total_height(self) -> int:
        """The panel's height (the widget is taller by its shadow margins)."""
        return self.PAD * 2 + self.HEAD + self.list_height()

    def set_index(self, index: int) -> None:
        self.index = index
        if 0 <= index < len(self.items):
            top, bottom = index * self.ROW, (index + 1) * self.ROW
            if top < self.scroll:
                self.scroll = top
            elif bottom > self.scroll + self.list_height():
                self.scroll = bottom - self.list_height()
        self.update()

    # ---- geometry
    def list_rect(self) -> QRectF:
        pr = self.panel_rect()
        return QRectF(pr.x() + self.PAD, pr.y() + self.PAD + self.HEAD, pr.width() - 2 * self.PAD, self.list_height())

    def row_at(self, pos: QPointF) -> int:
        lr = self.list_rect()
        if not lr.contains(pos):
            return -1
        i = int((pos.y() - lr.y() + self.scroll) // self.ROW)
        return i if 0 <= i < len(self.items) else -1

    # ---- events
    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = self.row_at(e.position())
        if i >= 0 and i != self.index and self.on_hover:
            self.on_hover(i)
        self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.panel_rect().contains(e.position()):
            self.forward_press(e)
            return
        i = self.row_at(e.position())
        if i >= 0 and self.on_pick:
            self.on_pick(self.items[i])

    def wheelEvent(self, e) -> None:  # noqa: N802
        over = self.ROW * len(self.items) - self.list_height()
        if over <= 0:
            return
        self.scroll = max(0, min(over, self.scroll - int(e.angleDelta().y() / 120 * 48)))
        self.update()
        e.accept()

    # ---- paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_pop(p)
        pr = self.panel_rect()
        draw_glass_panel(p, pr, self.RADIUS, self.pop_t.value)
        # head
        p.setFont(theme.font_ui(theme.FS_MICRO, 600))
        p.setPen(QColor(t.text_3))
        p.drawText(
            QRect(int(pr.x() + self.PAD + 8), int(pr.y() + self.PAD + 4), int(pr.width()) - 28, self.HEAD - 10),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.head.text(),
        )
        lr = self.list_rect()
        p.save()
        p.setClipRect(lr)
        mono = theme.font_mono(12.5)
        small = theme.font_ui(theme.FS_SMALL)
        mfm = QFontMetrics(mono)
        sfm = QFontMetrics(small)
        y0 = lr.y() - self.scroll
        first = max(0, int(self.scroll // self.ROW))
        last = min(len(self.items), int((self.scroll + lr.height()) // self.ROW) + 1)
        for i in range(first, last):
            it = self.items[i]
            ry = y0 + i * self.ROW
            row = QRectF(lr.x(), ry, lr.width(), self.ROW)
            if i == self.index:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.fill_2))
                p.drawRoundedRect(row, theme.R2, theme.R2)
            x = row.x() + 10
            kind = it["kind"]
            if kind in ("dir", "file"):
                p.drawPixmap(QPointF(x, ry + 9), icons.pixmap("folder" if kind == "dir" else "file", t.text_3, 14))
                x += 14 + 10
            p.setFont(mono)
            label = str(it["label"])
            if kind == "cmd":
                p.setPen(QColor(t.text_3))
                p.drawText(QRect(int(x), int(ry), 20, self.ROW), Qt.AlignmentFlag.AlignVCenter, "/")
                slash_w = mfm.horizontalAdvance("/")
                p.setPen(QColor(t.text))
                p.drawText(
                    QRect(int(x + slash_w), int(ry), int(lr.width()), self.ROW),
                    Qt.AlignmentFlag.AlignVCenter,
                    label[1:],
                )
                x += max(self.name_w, mfm.horizontalAdvance(label)) + 10
            else:
                max_w = int(row.width() * 0.6)
                shown = mfm.elidedText(label, Qt.TextElideMode.ElideRight, max_w)
                p.setPen(QColor(t.text))
                p.drawText(QRect(int(x), int(ry), max_w + 4, self.ROW), Qt.AlignmentFlag.AlignVCenter, shown)
                x += mfm.horizontalAdvance(shown) + 10
            hint = str(it.get("hint") or "")
            if hint:
                p.setFont(small)
                p.setPen(QColor(t.text_3))
                avail = int(row.right() - 10 - x)
                p.drawText(
                    QRect(int(x), int(ry), max(0, avail) + 2, self.ROW),
                    Qt.AlignmentFlag.AlignVCenter,
                    sfm.elidedText(hint, Qt.TextElideMode.ElideRight, max(0, avail)),
                )
        p.restore()
        over = self.ROW * len(self.items) - self.list_height()
        if over > 0:
            # the slim overlay thumb of the web's scrollbar
            frac = lr.height() / (self.ROW * len(self.items))
            th = max(28, lr.height() * frac)
            ty = lr.y() + (lr.height() - th) * (self.scroll / over)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.fill_3))
            p.drawRoundedRect(QRectF(lr.right() - 7, ty, 4, th), 2, 2)


# ---------------------------------------------------------------- menus (Popover + Menu)


class MenuPopup(_FloatingPanel):
    """A menu above or below an anchor (web/src/components/ui/Popover.tsx + Menu). Items are dicts:
    ``{"label", "icon"?, "hint"?, "mono"?, "checked"?, "danger"?, "disabled"?, "kbd"?, "on_select"}``,
    ``{"separator": True}`` or ``{"heading": str}``. Lives on the top-level window; closes on an
    outside press, Escape, or a pick; arrows move, Enter picks. ``open_menu(items, anchor, width)``."""

    PAD = 5
    ITEM_MIN = 30
    GAP = 1

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.items: List[Json] = []
        self.active = -1
        self.anchor: Optional[QWidget] = None
        self.on_close: Optional[Callable[[], None]] = None
        self._width = 240
        self._rows: List[Tuple[int, int, int]] = []  # (item index, y, height) within the panel
        self._filtering = False
        # a linear clock for the stagger; each row eases on its own slice of it
        self.rows_t = Tween(self, 240, lambda: QEasingCurve(QEasingCurve.Type.Linear), lambda _v: self.update())
        self.hover_t = Tween(self, theme.T_FAST, theme.ease, lambda _v: self.update())
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- layout
    @staticmethod
    def item_height(it: Json) -> int:
        if it.get("separator"):
            return 9
        if it.get("heading"):
            return 29
        if it.get("hint"):
            return 48 if it.get("mono") else 49
        return 30

    def _layout(self) -> int:
        self._rows = []
        y = self.PAD
        for i, it in enumerate(self.items):
            h = self.item_height(it)
            self._rows.append((i, y, h))
            y += h + self.GAP
        return y - self.GAP + self.PAD

    def open_menu(
        self,
        items: List[Json],
        anchor: QWidget,
        width: int,
        align_right: bool = False,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        self.items = items
        self.anchor = anchor
        self.on_close = on_close
        self._width = width
        self.active = -1
        h = self._layout()
        host = anchor.window()
        if host is None:
            return
        if self.parentWidget() is not host:
            self.setParent(host)
        a = QRect(anchor.mapTo(host, QPoint(0, 0)), anchor.size())
        left = a.right() + 1 - width if align_right else a.x()
        top = a.y() - h - 6
        if top < 8:
            top = min(host.height() - h - 8, a.bottom() + 1 + 6)
        left = max(8, min(left, host.width() - width - 8))
        self.place(QRect(left, max(0, top), width, h))
        self.rows_t.set(0.0)
        n = sum(1 for it in items if not it.get("separator") and not it.get("heading"))
        self.rows_t.go(1.0, duration=240 + 11 * min(n, 10))
        self.pop_in()
        app = QApplication.instance()
        if app is not None and not self._filtering:
            app.installEventFilter(self)
            self._filtering = True
        set_open = getattr(anchor, "set_open", None)
        if set_open is not None:
            set_open(True)

    def close_menu(self) -> None:
        if not self.isVisible() and not self._filtering:
            return
        app = QApplication.instance()
        if app is not None and self._filtering:
            app.removeEventFilter(self)
            self._filtering = False
        self.pop_out()
        set_open = getattr(self.anchor, "set_open", None)
        if set_open is not None:
            set_open(False)
        done, self.on_close = self.on_close, None
        if done is not None:
            done()

    @property
    def is_open(self) -> bool:
        return self.isVisible()

    def selectable(self) -> List[int]:
        return [
            i
            for i, it in enumerate(self.items)
            if not it.get("separator") and not it.get("heading") and not it.get("disabled")
        ]

    def pick(self, i: int) -> None:
        it = self.items[i]
        fn = it.get("on_select")
        self.close_menu()
        if fn is not None:
            fn()

    # ---- events
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        et = e.type()
        if et == QEvent.Type.MouseButtonPress and isinstance(e, QMouseEvent):
            gp = e.globalPosition().toPoint()
            inside = self.rect().contains(self.mapFromGlobal(gp))
            on_anchor = self.anchor is not None and self.anchor.rect().contains(self.anchor.mapFromGlobal(gp))
            if not inside and not on_anchor:
                self.close_menu()
            return False
        if et == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent):
            # the window's own shortcuts (Escape among them) wait while a menu is up
            if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                e.accept()
                return True
            return False
        if et == QEvent.Type.KeyPress and isinstance(e, QKeyEvent):
            key = e.key()
            sel = self.selectable()
            if key == Qt.Key.Key_Escape:
                self.close_menu()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and sel:
                idx = sel.index(self.active) if self.active in sel else -1
                step = 1 if key == Qt.Key.Key_Down else -1
                self.active = sel[(idx + step) % len(sel)] if idx >= 0 or step > 0 else sel[-1]
                self.update()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.active >= 0:
                self.pick(self.active)
                return True
        if et in (QEvent.Type.Resize, QEvent.Type.Move) and obj is self.parentWidget():
            self.close_menu()
        if et == QEvent.Type.WindowDeactivate and obj is self.window():
            self.close_menu()
        return False

    def row_at(self, pos: QPointF) -> int:
        pr = self.panel_rect()
        if not pr.contains(pos):
            return -1
        y = pos.y() - pr.y()
        for i, ry, h in self._rows:
            if ry <= y < ry + h:
                it = self.items[i]
                return -1 if it.get("separator") or it.get("heading") or it.get("disabled") else i
        return -1

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = self.row_at(e.position())
        if i != self.active:
            self.active = i
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.panel_rect().contains(e.position()):
            self.close_menu()
            self.forward_press(e)
            return

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        i = self.row_at(e.position())
        if i >= 0:
            self.pick(i)

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        if self.active >= 0:
            self.active = -1
            self.update()

    # ---- paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_pop(p)
        pr = self.panel_rect()
        draw_glass_panel(p, pr, self.RADIUS, self.pop_t.value)
        ui = theme.font_ui(theme.FS_UI)
        small = theme.font_ui(theme.FS_SMALL)
        mono = theme.font_mono(11.5)
        micro = theme.font_ui(theme.FS_MICRO, 600)
        ufm, sfm, mfm = QFontMetrics(ui), QFontMetrics(small), QFontMetrics(mono)
        total_t = self.rows_t.value
        n_anim = sum(1 for it in self.items if not it.get("separator") and not it.get("heading"))
        span = 240 + 11 * min(n_anim, 10)
        order = 0
        pop = self.pop_t.value
        for i, ry, h in self._rows:
            it = self.items[i]
            x0, y = pr.x() + self.PAD, pr.y() + ry
            w = pr.width() - 2 * self.PAD
            if it.get("separator"):
                p.fillRect(QRectF(x0 + 6, y + 4, w - 12, 1), theme.qcolor(t.hair))
                continue
            if it.get("heading"):
                p.setFont(micro)
                p.setPen(QColor(t.text_3))
                p.drawText(
                    QRect(int(x0 + 9), int(y + 8), int(w - 18), 17), Qt.AlignmentFlag.AlignVCenter, it["heading"]
                )
                continue
            # rows unfold a few milliseconds apart (menu-in: opacity 0, translateY(-3px))
            delay = 11 * min(order, 10)
            order += 1
            k = max(0.0, min(1.0, (total_t * span - delay) / 240.0)) if not store.reduced_motion else 1.0
            k = theme.ease().valueForProgress(k)
            p.save()
            p.setOpacity(pop * k)
            p.translate(0, -3 * (1 - k))
            row = QRectF(x0, y, w, h)
            danger, disabled = bool(it.get("danger")), bool(it.get("disabled"))
            if i == self.active and not disabled:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.fill_2))
                p.drawRoundedRect(row, theme.R2, theme.R2)
            ink = t.err if danger else t.text_3 if disabled else t.text
            icon_color = t.err if danger else (t.text_2 if i == self.active else t.text_3)
            if it.get("checked"):
                p.drawPixmap(QPointF(x0 + 9 + 1, y + 6 + 2), icons.pixmap("check", icon_color, 14))
            elif it.get("icon"):
                p.drawPixmap(QPointF(x0 + 9 + 1, y + 6 + 2), icons.pixmap(str(it["icon"]), icon_color, 14))
            tx = x0 + 9 + 16 + 9
            tw = w - (tx - x0) - 9
            kbd = str(it.get("kbd") or "")
            if kbd:
                p.setFont(theme.font_ui(theme.FS_MICRO, 500))
                kw = QFontMetrics(p.font()).horizontalAdvance(kbd)
                p.setPen(QColor(t.text_4))
                p.drawText(QRect(int(row.right() - 9 - kw), int(y), kw + 2, h), Qt.AlignmentFlag.AlignVCenter, kbd)
                tw -= kw + 9
            p.setFont(ui)
            p.setPen(QColor(ink))
            label = ufm.elidedText(str(it.get("label", "")), Qt.TextElideMode.ElideRight, int(tw))
            hint = str(it.get("hint") or "")
            if hint:
                p.drawText(QRect(int(tx), int(y + 6), int(tw), 18), Qt.AlignmentFlag.AlignVCenter, label)
                is_mono = bool(it.get("mono"))
                p.setFont(mono if is_mono else small)
                p.setPen(QColor(t.text_3))
                fmh = mfm if is_mono else sfm
                p.drawText(
                    QRect(int(tx), int(y + 6 + 18 + 2), int(tw), 16 if is_mono else 17),
                    Qt.AlignmentFlag.AlignVCenter,
                    fmh.elidedText(hint, Qt.TextElideMode.ElideRight, int(tw)),
                )
            else:
                p.drawText(QRect(int(tx), int(y), int(tw), h), Qt.AlignmentFlag.AlignVCenter, label)
            p.restore()


# ---------------------------------------------------------------- attachments


class RoundX(QPushButton):
    """An 18px round remove button: quiet (``.attach-file button``) or on a dark disc (``.attach-x``)."""

    def __init__(self, tooltip: str, disc: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._disc = disc
        self.setFixedSize(18, 18)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self._disc:
            p.setBrush(QColor(0, 0, 0, 158))
            p.drawEllipse(QRectF(0, 0, 18, 18))
            ink = "#ffffff"
        else:
            if self.underMouse():
                p.setBrush(theme.qcolor(t.fill_3))
                p.drawEllipse(QRectF(0, 0, 18, 18))
            ink = t.text if self.underMouse() else t.text_3
        p.drawPixmap(QPointF(3.5, 3.5), icons.pixmap("x", ink, 11))

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self.update()


class _Pops(QWidget):
    """Arrives on ``pop-spring-sm`` (scale .55 → 1 with the opacity, snappy spring)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.pop_t = Tween(self, theme.SPRING_SNAPPY_T, theme.spring_snappy, lambda _v: self.update())

    def showEvent(self, e) -> None:  # noqa: N802
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
        self.x = RoundX("Remove image", disc=True, parent=self)
        self.x.move(38, 4)
        self.x.hide()
        self.x.clicked.connect(on_remove)

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self.x.show()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self.x.hide()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.apply_pop(p)
        path = _rounded(QRectF(0, 0, 60, 60), theme.R3)
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
        self.x = RoundX("Remove file", parent=self)
        self.x.move(w - 6 - 18, 6)
        self.x.clicked.connect(on_remove)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
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


# ---------------------------------------------------------------- the queue tray


class QueueTray(QFrame):
    """Messages waiting for the current step to finish (``.queue``). Each can be edited, sent right away, or dropped."""

    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.held: List[Json] = []
        self._key: List[tuple] = []
        self.editing: Optional[str] = None
        self.setObjectName("queue")
        self.setAccessibleName("Queued messages")
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
        self.head_ico.setPixmap(icons.pixmap("clock-3", t.accent_text, 12))
        self.head_text.setStyleSheet(
            f"color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600; letter-spacing: 0.1px;"
        )
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(QPen(theme.qcolor(t.hair), 1))
        p.setBrush(_mix(t.raised, t.canvas, 0.7))  # color-mix(in srgb, var(--raised) 70%, var(--canvas))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), theme.R3, theme.R3)

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


class QueueAttach(QWidget):
    """``.queue-attach``: a small pill with an icon and a count or file name."""

    def __init__(self, icon: str, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon, self._text = icon, text
        fm = QFontMetrics(theme.font_ui(11))
        self.setFixedSize(6 + 11 + 3 + fm.horizontalAdvance(text) + 6, 18)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.fill_2))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        p.drawPixmap(QPointF(6, 3.5), icons.pixmap(self._icon, t.text_3, 11))
        p.setFont(theme.font_ui(11))
        p.setPen(QColor(t.text_3))
        p.drawText(QRect(20, 0, self.width() - 20, 18), Qt.AlignmentFlag.AlignVCenter, self._text)


class QueueItem(QWidget):
    """``.queue-item``: number, text (click to edit), attachment pills, and the actions under the pointer."""

    def __init__(self, tray: QueueTray, h: Json, n: int, editing: bool) -> None:
        super().__init__(tray)
        self.tray, self.h = tray, h
        self._editing = editing
        t = theme.tokens()
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
            self.text_btn.setCursor(Qt.CursorShape.IBeamCursor)
            self.text_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.text_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            color = t.text_4 if not text else t.text_2
            self.text_btn.setStyleSheet(
                f"QPushButton {{ border: none; text-align: left; padding: 2px 0; color: {color};"
                f" font-size: {theme.FS_UI}px; background: transparent; }} QPushButton:hover {{ color: {t.text}; }}"
            )
            self.text_btn.clicked.connect(lambda: tray.start_edit(h["uuid"]))
            lay.addWidget(self.text_btn, 1)
            if (h.get("images") or 0) > 0:
                lay.addWidget(QueueAttach("image", str(h["images"])), 0, Qt.AlignmentFlag.AlignTop)
            for f in h.get("files") or []:
                lay.addWidget(QueueAttach("file", str(f)), 0, Qt.AlignmentFlag.AlignTop)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        self.now = QPushButton("Now")
        self.now.setFlat(True)
        self.now.setToolTip("Send now — interrupts the current step")
        self.now.setAccessibleName("Send now")
        self.now.setIcon(icons.icon("zap", t.text_3, 12, hover=t.accent_text))
        self.now.setIconSize(QSize(12, 12))
        self.now.setFixedHeight(24)
        self.now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.now.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; padding: 0 8px; color: {t.text_3};"
            f" font-size: 11px; font-weight: 600; background: transparent; }}"
            f" QPushButton:hover {{ background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.accent_text}; }}"
        )
        self.now.clicked.connect(lambda: store.send_held_now(tray.sid, h["uuid"]))
        al.addWidget(self.now)
        self.x = QPushButton()
        self.x.setFlat(True)
        self.x.setFixedSize(24, 24)
        self.x.setToolTip("Remove")
        self.x.setAccessibleName("Remove queued message")
        self.x.setIcon(icons.icon("x", t.text_3, 13, hover=t.text))
        self.x.setIconSize(QSize(13, 13))
        self.x.setCursor(Qt.CursorShape.PointingHandCursor)
        self.x.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; background: transparent; }}"
            f" QPushButton:hover {{ background: {theme.blend(t.fill_2, t.canvas).name()}; }}"
        )
        self.x.clicked.connect(lambda: store.remove_held(tray.sid, h["uuid"]))
        al.addWidget(self.x)
        # the actions keep their room and show under the pointer (opacity 0 → 1 in CSS)
        for b in (self.now, self.x):
            sp = b.sizePolicy()
            sp.setRetainSizeWhenHidden(True)
            b.setSizePolicy(sp)
            b.setVisible(editing)
        lay.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignTop)
        self.setMouseTracking(True)

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self.now.show()
        self.x.show()
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        if not self._editing:
            self.now.hide()
            self.x.hide()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not (self.underMouse() or self._editing):
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.fill_1))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


class _QueueEdit(QPlainTextEdit):
    """``.queue-edit``: an inset field; Enter saves, Escape cancels, so does leaving it."""

    def __init__(self, text: str, on_save: Callable[[str], None], on_cancel: Callable[[], None]) -> None:
        super().__init__()
        t = theme.tokens()
        self._save, self._cancel = on_save, on_cancel
        self._done = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {t.inset}; border: 1px solid {theme.blend(t.hair_2, t.canvas).name()};"
            f" border-radius: {theme.R1}px; padding: 2px 6px; color: {t.text}; font-size: {theme.FS_UI}px; }}"
            f" QPlainTextEdit:focus {{ border-color: {theme.blend(t.accent_line, t.canvas).name()}; }}"
        )
        self.setPlainText(text)
        lines = min(6, max(1, text.count("\n") + 1))
        self.setFixedHeight(max(24, lines * self.fontMetrics().lineSpacing() + 8))
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


# ---------------------------------------------------------------- overlays


class GhostHint(QWidget):
    """``.composer-ghost``: a suggested prompt in the empty field, with the Tab chip."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self.fade = Tween(self, theme.T_MED, theme.ease, lambda _v: self.update())
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_text(self, text: str) -> None:
        if text and not self._text:
            self.fade.set(0.0)
            self.fade.go(1.0)
        self._text = text
        self.setVisible(bool(text))
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, _e) -> None:  # noqa: N802
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
        self.fade = Tween(self, theme.T_FAST, theme.ease, lambda _v: self.update())
        self.hide()

    def appear(self) -> None:
        self.fade.set(0.0)
        self.show()
        self.raise_()
        self.fade.go(1.0)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.fade.value)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(t.raised, 0.88))
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

    def paintEvent(self, _e) -> None:  # noqa: N802
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
        self._anim: Optional[QVariantAnimation] = None
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
        self.menu = MenuPopup(self.window() if self.window() is not None else host)

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
        return self.edit.toPlainText()

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
        self.edit.apply_line_height()
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
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not self.text and prev > nxt + 2 and time.time() - self._sent_at < 0.6 and not store.reduced_motion:
            anim = QVariantAnimation(self)
            anim.setStartValue(prev)
            anim.setEndValue(nxt)
            anim.setDuration(theme.SPRING_SOFT_T)
            anim.setEasingCurve(theme.spring_soft())
            anim.valueChanged.connect(lambda v: self.edit.setFixedHeight(int(v)))
            anim.start()
            self._anim = anim
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

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._apply_column()
        self._autosize()
        self._place_overlays()

    def moveEvent(self, e) -> None:  # noqa: N802
        super().moveEvent(e)
        self._place_overlays()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self.fade is not None:
            self.fade.show()
        self._place_overlays()

    def hideEvent(self, e) -> None:  # noqa: N802
        self.popup.hide()
        self.menu.close_menu()
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
            self.suggestions = [
                {"key": f, "label": f, "hint": "", "kind": "dir" if f.endswith("/") else "file"} for f in lst
            ]
            head = "Files" if q else "Files — keep typing to narrow down"
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
    def _open_mode_menu(self) -> None:
        if self.menu.is_open and self.menu.anchor is self.mode_btn:
            self.menu.close_menu()
            return
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
        self.menu.open_menu(items, self.mode_btn, 372)

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
        if self.menu.is_open and self.menu.anchor is self.model_btn:
            self.menu.close_menu()
            return
        store.load_gateway_models()
        self.menu.open_menu(self.model_menu_items(), self.model_btn, 320, align_right=True)

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
