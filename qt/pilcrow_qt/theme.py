"""Design tokens and the application stylesheet.

A port of web/src/styles/tokens.css: warm neutrals in the key of Claude's own apps, charcoal and
bone rather than blue-grey, with Claude's orange as the single accent. Orange is reserved for the
primary action, the send button, attention ("needs you") and focus; lilac marks reasoning and
nothing else.

Widgets never carry literal colours. They paint with ``tokens()``, or take their colours from a
palette: the application palette carries the theme (``palette()`` maps the tokens onto Qt's roles),
and ``tint()`` gives one widget other roles from the tokens (a fainter text, a chrome background),
re-applied whenever the theme changes. The application stylesheet (``stylesheet()``) is static: it
sets geometry and fonts and names colours only as ``palette(role)``, so a theme switch is a palette
change and a repaint, not a re-parse of every widget's style.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Callable, Dict, Optional, Tuple, Union

from PySide6.QtCore import QEasingCurve, QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QScrollBar,
    QTextEdit,
    QWidget,
)

# ---------------------------------------------------------------- type scale, radii, spacing

FS_MICRO = 11
FS_SMALL = 12
FS_UI = 13
FS_BODY = 14.5
FS_H = 15
FS_TITLE = 17
FS_DISPLAY = 22
MONO_SIZE = 12.5

R1, R2, R3, R4, R5 = 4, 6, 10, 16, 999
SP1, SP2, SP3, SP4, SP5, SP6 = 4, 8, 12, 16, 24, 32
HEADER_H = 48
COL = 700  # the conversation column: ~80 characters at 14.5px

T_FAST, T_MED, T_PANEL, T_SLOW, T_EXIT = 120, 180, 240, 300, 140


@dataclass(frozen=True)
class Tokens:
    """One theme's colours. Names follow tokens.css (``--text-2`` → ``text_2``)."""

    name: str
    dark: bool
    canvas: str
    chrome: str
    raised: str
    raised_2: str
    inset: str
    hair: str
    hair_2: str
    hair_3: str
    fill_1: str
    fill_2: str
    fill_3: str
    text: str
    text_2: str
    text_3: str
    text_4: str
    accent: str
    accent_2: str
    accent_ink: str
    accent_text: str
    accent_wash: str
    accent_line: str
    accent_ring: str
    ok: str
    ok_wash: str
    err: str
    err_wash: str
    info: str
    info_wash: str
    warn: str
    warn_wash: str
    plan: str
    plan_wash: str
    think: str
    think_wash: str
    diff_add: str
    diff_add_hl: str
    diff_del: str
    diff_del_hl: str
    scrim: str
    selection: str
    glass: str
    glass_2: str
    edge_hi: str
    cm_comment: str
    cm_keyword: str
    cm_string: str
    cm_number: str
    cm_function: str
    cm_type: str
    cm_property: str
    cm_tag: str
    cm_regex: str
    cm_punct: str
    ansi_black: str
    ansi_red: str
    ansi_green: str
    ansi_yellow: str
    ansi_blue: str
    ansi_magenta: str
    ansi_cyan: str
    ansi_white: str
    ansi_bright_black: str
    ansi_bright_red: str
    ansi_bright_green: str
    ansi_bright_yellow: str
    ansi_bright_blue: str
    ansi_bright_magenta: str
    ansi_bright_cyan: str
    ansi_bright_white: str

    def color(self, name: str) -> QColor:
        """A token as a QColor (``rgba()`` strings included)."""
        return qcolor(getattr(self, name))

    def ansi(self, var: str) -> Optional[str]:
        """``var(--ansi-bright-red)`` (as lib/ansi.py emits) → the colour string, else None."""
        if var.startswith("var(--ansi-") and var.endswith(")"):
            return getattr(self, "ansi_" + var[len("var(--ansi-") : -1].replace("-", "_"), None)
        return None


DARK = Tokens(
    name="dark",
    dark=True,
    # Claude Code's own tokens (cds, neutral set): page #191919, sidebar #1f1f1f, popover #303030.
    canvas="#191919",
    chrome="#1f1f1f",
    raised="#262626",
    raised_2="#303030",
    inset="#141414",
    hair="rgba(255, 255, 255, 0.07)",
    hair_2="rgba(255, 255, 255, 0.12)",
    hair_3="rgba(255, 255, 255, 0.2)",
    fill_1="rgba(255, 255, 255, 0.075)",
    fill_2="rgba(255, 255, 255, 0.15)",
    fill_3="rgba(255, 255, 255, 0.2)",
    text="#ededed",
    text_2="#9c9c9c",
    text_3="#878787",
    text_4="#6b6b6b",
    accent="#d97757",
    accent_2="#e2896c",
    accent_ink="#ffffff",
    accent_text="#e8946f",
    accent_wash="rgba(217, 119, 87, 0.14)",
    accent_line="rgba(217, 119, 87, 0.48)",
    accent_ring="rgba(217, 119, 87, 0.55)",
    ok="#53c556",
    ok_wash="rgba(83, 197, 86, 0.12)",
    err="#f17778",
    err_wash="rgba(220, 60, 61, 0.12)",
    info="#6aabf1",
    info_wash="rgba(106, 171, 241, 0.12)",
    warn="#e0b341",
    warn_wash="rgba(224, 179, 65, 0.12)",
    plan="#3bbd8c",
    plan_wash="rgba(91, 191, 174, 0.12)",
    think="#a89af0",
    think_wash="rgba(185, 166, 230, 0.1)",
    diff_add="rgba(50, 215, 75, 0.12)",
    diff_add_hl="rgba(50, 215, 75, 0.3)",
    diff_del="rgba(255, 44, 86, 0.11)",
    diff_del_hl="rgba(255, 44, 86, 0.3)",
    scrim="rgba(12, 11, 9, 0.55)",
    selection="rgba(122, 167, 240, 0.3)",
    glass="rgba(40, 40, 43, 0.96)",
    glass_2="rgba(33, 33, 36, 0.98)",
    edge_hi="rgba(255, 255, 255, 0.07)",
    cm_comment="#767369",
    cm_keyword="#c7aaee",
    cm_string="#a8d19a",
    cm_number="#ecb37e",
    cm_function="#93bbf3",
    cm_type="#86d1c4",
    cm_property="#cfcbc0",
    cm_tag="#f09c96",
    cm_regex="#eeaecd",
    cm_punct="#93908a",
    ansi_black="#3d3b37",
    ansi_red="#e8695c",
    ansi_green="#6fbf8a",
    ansi_yellow="#e6c06a",
    ansi_blue="#7aa7f0",
    ansi_magenta="#c7aaee",
    ansi_cyan="#5bbfae",
    ansi_white="#d3d0c6",
    ansi_bright_black="#76736a",
    ansi_bright_red="#ff8c80",
    ansi_bright_green="#8fdba8",
    ansi_bright_yellow="#ffd98a",
    ansi_bright_blue="#a6c5ff",
    ansi_bright_magenta="#dfc6ff",
    ansi_bright_cyan="#97e3d6",
    ansi_bright_white="#f6f4ec",
)

LIGHT = Tokens(
    name="light",
    dark=False,
    canvas="#ffffff",
    chrome="#fafafa",
    raised="#ffffff",
    raised_2="#f2f2f2",
    inset="#f9f9f9",
    hair="rgba(0, 0, 0, 0.08)",
    hair_2="rgba(0, 0, 0, 0.12)",
    hair_3="rgba(0, 0, 0, 0.2)",
    fill_1="rgba(0, 0, 0, 0.05)",
    fill_2="rgba(0, 0, 0, 0.1)",
    fill_3="rgba(0, 0, 0, 0.145)",
    text="#1a1a1a",
    text_2="#666666",
    text_3="#878787",
    text_4="#a3a3a3",
    accent="#d97757",
    accent_2="#c6613f",
    accent_ink="#ffffff",
    accent_text="#ad4f2e",
    accent_wash="rgba(217, 119, 87, 0.12)",
    accent_line="rgba(217, 119, 87, 0.5)",
    accent_ring="rgba(217, 119, 87, 0.55)",
    ok="#006c10",
    ok_wash="rgba(0, 108, 16, 0.1)",
    err="#a42528",
    err_wash="rgba(220, 60, 61, 0.12)",
    info="#1757ac",
    info_wash="rgba(23, 87, 172, 0.09)",
    warn="#9a6b00",
    warn_wash="rgba(154, 107, 0, 0.1)",
    plan="#065f49",
    plan_wash="rgba(15, 122, 108, 0.09)",
    think="#5437b8",
    think_wash="rgba(123, 95, 196, 0.09)",
    diff_add="rgba(30, 158, 60, 0.1)",
    diff_add_hl="rgba(30, 158, 60, 0.26)",
    diff_del="rgba(205, 32, 84, 0.08)",
    diff_del_hl="rgba(205, 32, 84, 0.24)",
    scrim="rgba(40, 36, 28, 0.24)",
    selection="rgba(44, 98, 196, 0.16)",
    glass="rgba(255, 255, 255, 0.97)",
    glass_2="rgba(255, 255, 255, 0.98)",
    edge_hi="rgba(255, 255, 255, 0.9)",
    cm_comment="#8d8a80",
    cm_keyword="#7c3fbf",
    cm_string="#2f7a37",
    cm_number="#b2570f",
    cm_function="#1f5fc0",
    cm_type="#0d7a6e",
    cm_property="#3f3d36",
    cm_tag="#b52a33",
    cm_regex="#b2357a",
    cm_punct="#6e6b63",
    ansi_black="#1f1e1b",
    ansi_red="#c73d2f",
    ansi_green="#2f8a4f",
    ansi_yellow="#9a6b00",
    ansi_blue="#2c62c4",
    ansi_magenta="#8a3fc2",
    ansi_cyan="#0f7a6c",
    ansi_white="#5d5a53",
    ansi_bright_black="#8d8a80",
    ansi_bright_red="#e24b3f",
    ansi_bright_green="#2aa55e",
    ansi_bright_yellow="#b8740b",
    ansi_bright_blue="#3b7be0",
    ansi_bright_magenta="#a35bd8",
    ansi_bright_cyan="#13968a",
    ansi_bright_white="#1f1e1b",
)


def qcolor(s: str) -> QColor:
    """``#rrggbb`` or ``rgba(r, g, b, a)`` → QColor."""
    s = s.strip()
    if s.startswith("rgba(") or s.startswith("rgb("):
        parts = [p.strip() for p in s[s.index("(") + 1 : -1].split(",")]
        r, g, b = (int(float(x)) for x in parts[:3])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return QColor(r, g, b, int(round(a * 255)))
    return QColor(s)


def blend(fg: str, bg: str) -> QColor:
    """A translucent token composited over an opaque one: what the eye sees of a hairline on a surface."""
    return over(qcolor(fg), qcolor(bg))


def over(f: QColor, b: QColor) -> QColor:
    """``f`` (translucent) laid over ``b`` (opaque)."""
    a = f.alphaF()
    return QColor(
        int(round(f.red() * a + b.red() * (1 - a))),
        int(round(f.green() * a + b.green() * (1 - a))),
        int(round(f.blue() * a + b.blue() * (1 - a))),
    )


# ---------------------------------------------------------------- springs


def _spring(stiffness: float, damping: float, mass: float = 1.0) -> Callable[[float], float]:
    """A damped mass-spring sampled by progress: the curves in tokens.css, generated rather than tabulated."""
    w0 = math.sqrt(stiffness / mass)
    zeta = damping / (2 * math.sqrt(stiffness * mass))
    wd = w0 * math.sqrt(max(1e-9, 1 - zeta * zeta)) if zeta < 1 else 0.0

    def at(seconds: float) -> float:
        if zeta < 1:
            return 1 - math.exp(-zeta * w0 * seconds) * (
                math.cos(wd * seconds) + (zeta * w0 / wd) * math.sin(wd * seconds)
            )
        return 1 - (1 + w0 * seconds) * math.exp(-w0 * seconds)

    return at


def _curve(stiffness: float, damping: float, settle_ms: int) -> QEasingCurve:
    """The spring sampled into a piecewise cubic Bézier spline. Not a Custom-type curve with a Python
    callable: Qt keeps only a raw function pointer to that, and once the lambda is collected the next
    animation frame dereferences freed memory (the segfaults when a panel opened or a message went out)."""
    f = _spring(stiffness, damping)
    total = settle_ms / 1000.0
    n = 8  # Qt's BezierSpline keeps room for 10 segments and never grows it; more corrupts the heap
    h = 1.0 / n
    eps = 1e-4

    def slope(x: float) -> float:
        return (f((x + eps) * total) - f((x - eps) * total)) / (2 * eps)

    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    for i in range(n):
        x0, x1 = i * h, (i + 1) * h
        y0 = f(x0 * total) if i else 0.0
        y1 = f(x1 * total) if i < n - 1 else 1.0
        m0, m1 = slope(x0) if i else slope(eps), slope(x1)
        c.addCubicBezierSegment(_pt(x0 + h / 3, y0 + m0 * h / 3), _pt(x1 - h / 3, y1 - m1 * h / 3), _pt(x1, y1))
    return c


# soft: no overshoot, for layout (a panel sliding open). snappy: ~6% overshoot, for things that
# appear (menus, cards, the send button). bouncy: ~19% overshoot, for small toggles and check marks.
SPRING_SOFT_T, SPRING_SNAPPY_T, SPRING_BOUNCY_T = 560, 600, 760


def spring_soft() -> QEasingCurve:
    return _curve(170.0, 26.0, SPRING_SOFT_T)


def spring_snappy() -> QEasingCurve:
    return _curve(380.0, 30.0, SPRING_SNAPPY_T)


def spring_bouncy() -> QEasingCurve:
    return _curve(420.0, 20.0, SPRING_BOUNCY_T)


def ease() -> QEasingCurve:
    """cubic-bezier(0.22, 1, 0.36, 1): the default ease-out for hover, menus and panels."""
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(_pt(0.22, 1), _pt(0.36, 1), _pt(1, 1))
    return c


def ease_exit() -> QEasingCurve:
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(_pt(0.4, 0), _pt(1, 1), _pt(1, 1))
    return c


def _pt(x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


# ---------------------------------------------------------------- fonts

_UI_FAMILY = ""
_MONO_FAMILY = ""


def set_families(ui: str, mono: str) -> None:
    """Called once by fonts.py after the bundled faces are registered (or system ones chosen)."""
    global _UI_FAMILY, _MONO_FAMILY
    _UI_FAMILY, _MONO_FAMILY = ui, mono


def _weight(w) -> QFont.Weight:
    """CSS-style numbers (400, 500, 600…) and QFont.Weight values both work."""
    if isinstance(w, QFont.Weight):
        return w
    n = int(w)
    return QFont.Weight(max(1, min(1000, n)))


def font_ui(size: float = FS_UI, weight: int = QFont.Weight.Normal, italic: bool = False) -> QFont:
    f = QFont(_UI_FAMILY or QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family())
    # Pixel sizes, like the CSS: a point size would follow the X server's idea of the DPI (Xft.dpi,
    # often wrong on Linux), and the rows are laid out in pixels. HiDPI scaling still applies.
    f.setPixelSize(max(1, int(size + 0.5)))  # half up, like the CSS engines round 14.5px
    f.setWeight(_weight(weight))
    f.setItalic(italic)
    return f


def font_mono(size: float = MONO_SIZE, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont(_MONO_FAMILY or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
    f.setPixelSize(max(1, int(size + 0.5)))
    f.setWeight(_weight(weight))
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def px_to_pt(px: float) -> float:
    """The web sizes are CSS pixels (96 dpi); Qt fonts are points. Device scaling is Qt's job."""
    return px * 72.0 / 96.0


# ---------------------------------------------------------------- the stylesheet


def stylesheet() -> str:
    """The global QSS. It is applied once: every colour in it is a palette role, so a theme change
    only swaps the palette (and re-polishes the few native widgets that keep these rules, see
    :func:`repolish`). Buttons, labels, cards and hairlines are painted or tinted and need no rule.

    Roles (see :func:`palette`): window = canvas, base = inset, alternate-base = raised, button =
    raised-2 (floating surfaces), light/midlight/mid = fill-1/2/3 on the canvas, shadow = the hair
    on the canvas, dark = hair-2 on raised, highlight = the selection, link = accent text,
    link-visited = the accent, bright-text = the accent's ink."""
    mono = _MONO_FAMILY or "monospace"
    dark_arrow, light_arrow = _arrow_files()
    return f"""
    * {{ outline: none; }}
    QToolTip {{ background: palette(button); color: palette(text); border: 1px solid palette(dark); padding: 4px 8px; border-radius: {R2}px; font-size: {FS_SMALL}px; }}

    /* text: sizes by property; colours come from the palette (tint()) */
    QLabel[mono="true"] {{ font-family: "{mono}"; font-size: {MONO_SIZE}px; }}
    QLabel[fs="small"] {{ font-size: {FS_SMALL}px; }}
    QLabel[fs="micro"] {{ font-size: {FS_MICRO}px; }}
    QLabel[fs="title"] {{ font-size: {FS_TITLE}px; font-weight: 600; }}
    QLabel[fs="h"] {{ font-size: {FS_H}px; font-weight: 600; }}

    /* fields */
    QLineEdit, QPlainTextEdit, QTextEdit {{ background: palette(base); border: 1px solid palette(shadow); border-radius: {R2}px; padding: 5px 8px; selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: palette(link-visited); }}
    QLineEdit[quiet="true"] {{ background: transparent; border: none; padding: 2px 4px; }}
    QComboBox {{ background: palette(light); border: 1px solid palette(shadow); border-radius: {R2}px; padding: 0 8px 0 10px; min-height: 32px; max-height: 32px; combobox-popup: 0; }}
    QComboBox:hover, QComboBox:on {{ background: palette(midlight); border-color: palette(dark); }}
    QComboBox:focus {{ border-color: palette(dark); }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; background: transparent; }}
    QComboBox::down-arrow {{ image: url("{dark_arrow}"); width: 13px; height: 13px; }}
    QComboBox[dark="false"]::down-arrow {{ image: url("{light_arrow}"); }}
    QComboBox QAbstractItemView {{ background: palette(button); border: 1px solid palette(dark); border-radius: {R3}px; padding: 5px; outline: none; selection-background-color: palette(midlight); selection-color: palette(text); }}
    QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 9px; border-radius: {R2}px; }}
    QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{ background: palette(midlight); }}
    QSpinBox, QDoubleSpinBox {{ background: palette(base); border: 1px solid palette(shadow); border-radius: {R2}px; padding: 4px 8px; min-height: 18px; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

    /* scrollbars: thin, quiet, only the thumb */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
    QScrollBar::handle:vertical {{ background: palette(midlight); border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: palette(mid); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
    QScrollBar::handle:horizontal {{ background: palette(midlight); border-radius: 4px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: palette(mid); }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; width: 0; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QAbstractScrollArea {{ background: transparent; }}
    """


def _arrow_files() -> Tuple[str, str]:
    """The chevron for QComboBox in each theme's faint colour, written once to the cache folder (QSS
    takes a file). The stylesheet picks one by the combo's ``dark`` property (set by :func:`repolish`)."""
    import os

    from PySide6.QtCore import QStandardPaths

    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or os.path.join(
        os.path.expanduser("~"), ".cache", "pilcrow"
    )
    os.makedirs(d, exist_ok=True)
    out = []
    for t in (DARK, LIGHT):
        path = os.path.join(d, f"chevrons-up-down-{t.name}.svg")
        if not os.path.exists(path):
            src = os.path.join(os.path.dirname(__file__), "assets", "icons", "chevrons-up-down.svg")
            try:
                with open(src, encoding="utf-8") as f:
                    svg = f.read().replace("currentColor", t.text_3)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(svg)
            except OSError:
                out.append("")
                continue
        out.append(path.replace("\\", "/"))
    return out[0], out[1]


# ---------------------------------------------------------------- the palette


def palette(t: Tokens) -> QPalette:
    """The tokens on Qt's colour roles: what every widget paints with unless it is tinted, and what
    the stylesheet's ``palette(role)`` references resolve to."""
    p = QPalette()
    g = QPalette.ColorGroup
    r = QPalette.ColorRole
    roles = {
        r.Window: qcolor(t.canvas),
        r.WindowText: qcolor(t.text),
        r.Base: qcolor(t.inset),
        r.AlternateBase: qcolor(t.raised),
        r.Text: qcolor(t.text),
        r.Button: qcolor(t.raised_2),
        r.ButtonText: qcolor(t.text),
        r.BrightText: qcolor(t.accent_ink),
        r.Light: blend(t.fill_1, t.canvas),
        r.Midlight: blend(t.fill_2, t.canvas),
        r.Mid: blend(t.fill_3, t.canvas),
        r.Dark: blend(t.hair_2, t.raised),
        r.Shadow: blend(t.hair, t.canvas),
        r.Highlight: blend(t.selection, t.inset),
        r.HighlightedText: qcolor(t.text),
        r.Link: qcolor(t.accent_text),
        r.LinkVisited: qcolor(t.accent),
        r.ToolTipBase: qcolor(t.raised_2),
        r.ToolTipText: qcolor(t.text),
        r.PlaceholderText: qcolor(t.text_4),
    }
    for group in (g.Active, g.Inactive, g.Disabled):
        for role, c in roles.items():
            p.setColor(group, role, c)
    for role in (r.Text, r.ButtonText, r.WindowText):
        p.setColor(g.Disabled, role, qcolor(t.text_4))
    return p


# ---------------------------------------------------------------- tints

# A colour spec: a token name ("text_2"), tokens laid over one another ("fill_2/canvas": fill-2 over
# the canvas), a literal ("#rrggbb", "rgba(…)", "transparent"), a QColor, or a function of the tokens.
Fill = Union[str, QColor, Callable[[Tokens], QColor], None]

# ``tone`` names used by the label factories, as the stylesheet once spelt them.
TONES = {"muted": "text_2", "faint": "text_3", "accent": "accent_text", "err": "err", "ok": "ok"}

_ROLE_SETS: Dict[str, Tuple[QPalette.ColorRole, ...]] = {
    "fg": (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText),
    "bg": (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button),
    "placeholder": (QPalette.ColorRole.PlaceholderText,),
    "highlight": (QPalette.ColorRole.Highlight,),
    "highlighted_text": (QPalette.ColorRole.HighlightedText,),
    "link": (QPalette.ColorRole.Link,),
}
_tints: Dict[int, Tuple[QWidget, Dict[str, Fill]]] = {}
_tint_cache: Dict[Tuple[Tuple[str, str], ...], QPalette] = {}


def resolve(spec: Fill, t: Optional[Tokens] = None) -> Optional[QColor]:
    """A :data:`Fill` as a QColor (None for None)."""
    if spec is None:
        return None
    t = t or theme.tokens
    if isinstance(spec, QColor):
        return QColor(spec)
    if callable(spec):
        return spec(t)
    parts = spec.split("/")
    c = qcolor(_lit(parts[-1], t))
    for p in reversed(parts[:-1]):
        c = over(qcolor(_lit(p, t)), c)
    return c


def _lit(name: str, t: Tokens) -> str:
    """A token's value by name, else the string itself (a literal colour)."""
    v = getattr(t, name, None)
    return v if isinstance(v, str) else name


def tint(w: QWidget, fg: Fill = None, bg: Fill = None, **roles: Fill) -> QWidget:
    """Give a widget its own colours from the tokens, for good: ``fg`` fills its text roles, ``bg``
    its background roles (paint it with ``setAutoFillBackground(True)``), and ``placeholder``,
    ``highlight``, ``highlighted_text`` and ``link`` the roles of those names. The tint is the whole
    of the widget's own palette (an earlier tint is replaced) and is applied again on every theme
    change, so a theme handler is not needed for it. Returns the widget."""
    spec: Dict[str, Fill] = {}
    if fg is not None:
        spec["fg"] = fg
    if bg is not None:
        spec["bg"] = bg
    for k, v in roles.items():
        if k not in _ROLE_SETS:
            raise KeyError(k)
        if v is not None:
            spec[k] = v
    key = id(w)
    if key not in _tints:
        w.destroyed.connect(lambda *_a, key=key: _tints.pop(key, None))
    _tints[key] = (w, spec)
    w.setPalette(_tint_palette(spec, theme.tokens))
    return w


def untint(w: QWidget) -> None:
    """Back to the application palette."""
    if _tints.pop(id(w), None) is not None:
        w.setPalette(QPalette())


def tint_of(w: QWidget) -> Dict[str, Fill]:
    """The tint on a widget (empty when it has none)."""
    found = _tints.get(id(w))
    return dict(found[1]) if found is not None else {}


def _tint_palette(spec: Dict[str, Fill], t: Tokens) -> QPalette:
    g = QPalette.ColorGroup
    cacheable = all(isinstance(v, str) for v in spec.values())
    key = ((t.name, ""), *sorted((k, v) for k, v in spec.items())) if cacheable else None  # type: ignore[misc]
    if key is not None:
        hit = _tint_cache.get(key)
        if hit is not None:
            return hit
    p = QPalette()
    for k, v in spec.items():
        c = resolve(v, t)
        if c is None:
            continue
        groups = (g.Active, g.Inactive) if k == "fg" else (g.Active, g.Inactive, g.Disabled)
        for group in groups:
            for role in _ROLE_SETS[k]:
                p.setColor(group, role, c)
    if key is not None:
        _tint_cache[key] = p
    return p


def _retint(t: Tokens) -> None:
    """Every tinted widget takes its colours from the new tokens."""
    _tint_cache.clear()
    dead = []
    for key, (w, spec) in list(_tints.items()):
        try:
            w.setPalette(_tint_palette(spec, t))
        except RuntimeError:  # the C++ widget is gone and destroyed() did not reach us
            dead.append(key)
    for key in dead:
        _tints.pop(key, None)


# The native widgets whose stylesheet rules name palette roles: Qt resolves those when it polishes
# the widget, so after a palette change they are polished again (a few microseconds each).
_NATIVE = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox, QScrollBar, QAbstractItemView)


def repolish(app: QApplication, t: Tokens) -> int:
    """Re-run the stylesheet on the native widgets whose rules reference the palette. Returns how many."""
    style = app.style()
    n = 0
    for w in app.allWidgets():
        if isinstance(w, _NATIVE):
            if isinstance(w, QComboBox):
                w.setProperty("dark", "true" if t.dark else "false")
            style.unpolish(w)
            style.polish(w)
            n += 1
    return n


# ---------------------------------------------------------------- the live theme


# The conversation's text scale (Ctrl+= / Ctrl+- / Ctrl+0): prose, code in prose, bubbles, thoughts.
# The window's chrome keeps its size; the canvas re-measures what shows under its anchor.
SCALE_STEPS = (0.85, 1.0, 1.15, 1.3, 1.5)
scale = 1.0


class Theme(QObject):
    """The theme in force. ``changed`` fires after the palette is swapped and the tints re-applied;
    ``scale_changed`` when the conversation's text scale moves."""

    changed = Signal(object)  # Tokens
    scale_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._tokens: Tokens = DARK
        self._styled = False

    @property
    def tokens(self) -> Tokens:
        return self._tokens

    def apply(self, dark: bool) -> None:
        t = DARK if dark else LIGHT
        self._tokens = t
        app = QApplication.instance()
        if isinstance(app, QApplication):
            pal = palette(t)
            app.setPalette(pal)
            # With a stylesheet in force Qt does not hand a palette change from a window down to its
            # children, so the change above reaches only the windows; a class palette for QWidget is
            # delivered to every widget (and is the same palette).
            app.setPalette(pal, "QWidget")
            if not self._styled:
                self._styled = True
                app.setFont(font_ui(FS_UI))
                app.setStyleSheet(stylesheet())
            else:
                repolish(app, t)
            _retint(t)
        self.changed.emit(t)

    def set_scale(self, value: float) -> None:
        global scale
        value = float(min(max(value, SCALE_STEPS[0]), SCALE_STEPS[-1]))
        if abs(value - scale) < 1e-6:
            return
        scale = value
        self.scale_changed.emit(value)


theme = Theme()


def tokens() -> Tokens:
    return theme.tokens


def token_names() -> list[str]:
    return [f.name for f in fields(Tokens) if f.name not in ("name", "dark")]
