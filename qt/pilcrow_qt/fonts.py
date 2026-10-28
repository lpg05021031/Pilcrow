"""Register the bundled faces (Inter, JetBrains Mono) and pick the families the theme uses.

assets/fonts holds TTFs made by scripts/make-qt-fonts.py from the @fontsource-variable packages
the web interface ships. When the folder is empty (a checkout without the generated assets), the
client falls back to the same families if the system has them, else the platform's default faces.
Chinese glyphs come from the system's CJK face through Qt's own substitution.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

from . import theme

FONT_DIR = Path(__file__).parent / "assets" / "fonts"

UI_CANDIDATES = [
    "Inter Variable",
    "Inter",
    "Noto Sans CJK TC",
    "Noto Sans TC",
    "PingFang TC",
    "Microsoft JhengHei UI",
    "Segoe UI",
]
MONO_CANDIDATES = [
    "JetBrains Mono Variable",
    "JetBrains Mono",
    "Noto Sans Mono CJK TC",
    "DejaVu Sans Mono",
    "Cascadia Mono",
    "Consolas",
    "Menlo",
]


def load() -> tuple[str, str]:
    """Register bundled fonts and return the (ui, mono) families in force."""
    loaded: list[str] = []
    if FONT_DIR.exists():
        for f in sorted(FONT_DIR.glob("*.[to]tf")):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                loaded.extend(QFontDatabase.applicationFontFamilies(fid))
    have = set(QFontDatabase.families())
    ui = next((c for c in UI_CANDIDATES if c in have or c in loaded), "")
    mono = next((c for c in MONO_CANDIDATES if c in have or c in loaded), "")
    theme.set_families(ui, mono)
    return ui, mono
