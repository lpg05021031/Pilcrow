"""Design tokens and the application stylesheet.

A port of web/src/styles/tokens.css: warm neutrals in the key of Claude's own apps, charcoal and
bone rather than blue-grey, with Claude's orange as the single accent. Orange is reserved for the
primary action, the send button, attention ("needs you") and focus; lilac marks reasoning and
nothing else.

Widgets never carry literal colours. They either paint with ``tokens()`` or are styled by the
global stylesheet through object names and dynamic properties (``setProperty("tone", "accent")``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Callable, Optional

from PySide6.QtCore import QEasingCurve, QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

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
    f, b = qcolor(fg), qcolor(bg)
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
            return 1 - math.exp(-zeta * w0 * seconds) * (math.cos(wd * seconds) + (zeta * w0 / wd) * math.sin(wd * seconds))
        return 1 - (1 + w0 * seconds) * math.exp(-w0 * seconds)

    return at


def _curve(stiffness: float, damping: float, settle_ms: int) -> QEasingCurve:
    f = _spring(stiffness, damping)
    total = settle_ms / 1000.0
    c = QEasingCurve(QEasingCurve.Type.Custom)
    c.setCustomType(lambda t: f(t * total))
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


def font_ui(size: float = FS_UI, weight: int = QFont.Weight.Normal, italic: bool = False) -> QFont:
    f = QFont(_UI_FAMILY or QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family())
    f.setPointSizeF(px_to_pt(size))
    f.setWeight(weight)
    f.setItalic(italic)
    return f


def font_mono(size: float = MONO_SIZE, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont(_MONO_FAMILY or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
    f.setPointSizeF(px_to_pt(size))
    f.setWeight(weight)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def px_to_pt(px: float) -> float:
    """The web sizes are CSS pixels (96 dpi); Qt fonts are points. Device scaling is Qt's job."""
    return px * 72.0 / 96.0


# ---------------------------------------------------------------- the stylesheet

def stylesheet(t: Tokens) -> str:
    """The global QSS, from the tokens. Object names and dynamic properties are the selectors."""
    ui = _UI_FAMILY or "sans-serif"
    mono = _MONO_FAMILY or "monospace"
    hair_on_chrome = blend(t.hair, t.chrome).name()
    hair_on_canvas = blend(t.hair, t.canvas).name()
    hair2_on_raised = blend(t.hair_2, t.raised).name()
    fill1_on_chrome = blend(t.fill_1, t.chrome).name()
    fill1_on_canvas = blend(t.fill_1, t.canvas).name()
    fill2_on_chrome = blend(t.fill_2, t.chrome).name()
    accent_wash_on_canvas = blend(t.accent_wash, t.canvas).name()
    return f"""
    * {{ font-family: "{ui}"; font-size: {FS_UI}px; color: {t.text}; outline: none; }}
    QWidget {{ background: transparent; }}
    QMainWindow, #canvas {{ background: {t.canvas}; }}
    #chrome, #sidebar, #panel {{ background: {t.chrome}; }}
    QToolTip {{ background: {t.raised_2}; color: {t.text}; border: 1px solid {hair2_on_raised}; padding: 4px 8px; border-radius: {R2}px; font-size: {FS_SMALL}px; }}

    /* text */
    QLabel {{ background: transparent; }}
    QLabel[tone="muted"] {{ color: {t.text_2}; }}
    QLabel[tone="faint"] {{ color: {t.text_3}; }}
    QLabel[tone="accent"] {{ color: {t.accent_text}; }}
    QLabel[tone="err"] {{ color: {t.err}; }}
    QLabel[tone="ok"] {{ color: {t.ok}; }}
    QLabel[mono="true"] {{ font-family: "{mono}"; font-size: {MONO_SIZE}px; }}
    QLabel[size="small"] {{ font-size: {FS_SMALL}px; }}
    QLabel[size="micro"] {{ font-size: {FS_MICRO}px; }}
    QLabel[size="title"] {{ font-size: {FS_TITLE}px; font-weight: 600; }}
    QLabel[size="h"] {{ font-size: {FS_H}px; font-weight: 600; }}

    /* buttons: quiet by default; the primary action is the only orange one */
    QPushButton {{ background: transparent; border: 1px solid {hair2_on_raised}; border-radius: {R2}px; padding: 5px 11px; color: {t.text}; }}
    QPushButton:hover {{ background: {fill1_on_canvas}; }}
    QPushButton:pressed {{ background: {fill2_on_chrome}; }}
    QPushButton:disabled {{ color: {t.text_4}; border-color: {hair_on_canvas}; }}
    QPushButton[kind="primary"] {{ background: {t.accent}; color: {t.accent_ink}; border: none; font-weight: 600; }}
    QPushButton[kind="primary"]:hover {{ background: {t.accent_2}; }}
    QPushButton[kind="primary"]:disabled {{ background: {fill2_on_chrome}; color: {t.text_4}; }}
    QPushButton[kind="ghost"] {{ border: none; color: {t.text_2}; }}
    QPushButton[kind="ghost"]:hover {{ background: {fill1_on_canvas}; color: {t.text}; }}
    QPushButton[kind="danger"] {{ background: {t.err}; color: white; border: none; }}
    QPushButton[kind="link"] {{ border: none; padding: 0; color: {t.text_2}; text-decoration: underline; }}
    QPushButton[kind="link"]:hover {{ color: {t.text}; background: transparent; }}
    QPushButton[size="sm"] {{ padding: 3px 9px; font-size: {FS_SMALL}px; }}
    QPushButton[icon-only="true"] {{ border: none; padding: 4px; border-radius: {R2}px; color: {t.text_2}; }}
    QPushButton[icon-only="true"]:hover {{ background: {fill1_on_chrome}; color: {t.text}; }}
    QPushButton[icon-only="true"][active="true"] {{ background: {fill2_on_chrome}; color: {t.text}; }}

    /* fields */
    QLineEdit, QPlainTextEdit, QTextEdit {{ background: {t.inset}; border: 1px solid {hair_on_canvas}; border-radius: {R2}px; padding: 5px 8px; selection-background-color: {blend(t.selection, t.inset).name()}; color: {t.text}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {blend(t.accent_line, t.inset).name()}; }}
    QLineEdit[quiet="true"] {{ background: transparent; border: none; padding: 2px 4px; }}
    QComboBox {{ background: {t.raised}; border: 1px solid {hair2_on_raised}; border-radius: {R2}px; padding: 4px 8px; }}
    QComboBox QAbstractItemView {{ background: {t.raised_2}; border: 1px solid {hair2_on_raised}; selection-background-color: {fill2_on_chrome}; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {t.text_4}; border-radius: {R1}px; background: {t.inset}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}

    /* scrollbars: thin, quiet, only the thumb */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
    QScrollBar::handle:vertical {{ background: {fill2_on_chrome}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {blend(t.fill_3, t.chrome).name()}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
    QScrollBar::handle:horizontal {{ background: {fill2_on_chrome}; border-radius: 4px; min-width: 28px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; width: 0; }}
    QScrollArea {{ border: none; background: transparent; }}
    QAbstractScrollArea {{ background: transparent; }}

    /* menus and dialogs: floating material */
    QMenu {{ background: {blend(t.glass, t.canvas).name()}; border: 1px solid {hair2_on_raised}; border-radius: {R3}px; padding: 6px; }}
    QMenu::item {{ padding: 6px 10px 6px 30px; border-radius: {R2}px; }}
    QMenu::item:selected {{ background: {fill1_on_canvas}; }}
    QMenu::separator {{ height: 1px; background: {hair_on_canvas}; margin: 6px 4px; }}
    QDialog {{ background: {t.raised}; }}
    #sheet {{ background: {blend(t.glass_2, t.canvas).name()}; border: 1px solid {hair2_on_raised}; border-radius: {R4}px; }}

    /* splitters and hairlines */
    QSplitter::handle {{ background: {hair_on_chrome}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QFrame[hairline="true"] {{ background: {hair_on_canvas}; max-height: 1px; min-height: 1px; border: none; }}
    QFrame[hairline-v="true"] {{ background: {hair_on_canvas}; max-width: 1px; min-width: 1px; border: none; }}

    QTabBar::tab {{ background: transparent; padding: 6px 10px; color: {t.text_2}; border: none; }}
    QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
    QProgressBar {{ background: {fill1_on_chrome}; border: none; border-radius: 3px; height: 6px; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
    """


def palette(t: Tokens) -> QPalette:
    """The palette behind the stylesheet, for the few things QSS does not reach (selection, disabled text)."""
    p = QPalette()
    g = QPalette.ColorGroup
    r = QPalette.ColorRole
    for group in (g.Active, g.Inactive, g.Disabled):
        p.setColor(group, r.Window, qcolor(t.canvas))
        p.setColor(group, r.WindowText, qcolor(t.text))
        p.setColor(group, r.Base, qcolor(t.inset))
        p.setColor(group, r.AlternateBase, qcolor(t.chrome))
        p.setColor(group, r.Text, qcolor(t.text))
        p.setColor(group, r.Button, qcolor(t.raised))
        p.setColor(group, r.ButtonText, qcolor(t.text))
        p.setColor(group, r.Highlight, blend(t.selection, t.canvas))
        p.setColor(group, r.HighlightedText, qcolor(t.text))
        p.setColor(group, r.ToolTipBase, qcolor(t.raised_2))
        p.setColor(group, r.ToolTipText, qcolor(t.text))
        p.setColor(group, r.PlaceholderText, qcolor(t.text_4))
        p.setColor(group, r.Link, qcolor(t.accent_text))
    p.setColor(g.Disabled, r.Text, qcolor(t.text_4))
    p.setColor(g.Disabled, r.ButtonText, qcolor(t.text_4))
    p.setColor(g.Disabled, r.WindowText, qcolor(t.text_4))
    return p


# ---------------------------------------------------------------- the live theme

class Theme(QObject):
    """The theme in force. ``changed`` fires after the stylesheet and palette are re-applied."""

    changed = Signal(object)  # Tokens

    def __init__(self) -> None:
        super().__init__()
        self._tokens: Tokens = DARK

    @property
    def tokens(self) -> Tokens:
        return self._tokens

    def apply(self, dark: bool) -> None:
        t = DARK if dark else LIGHT
        self._tokens = t
        app = QApplication.instance()
        if app is not None:
            app.setPalette(palette(t))
            app.setStyleSheet(stylesheet(t))
        self.changed.emit(t)


theme = Theme()


def tokens() -> Tokens:
    return theme.tokens


def token_names() -> list[str]:
    return [f.name for f in fields(Tokens) if f.name not in ("name", "dark")]
