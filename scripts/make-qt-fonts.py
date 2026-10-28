#!/usr/bin/env python3
"""Make the TTF faces the Qt client bundles from the @fontsource-variable packages in node_modules.

    python scripts/make-qt-fonts.py

Writes qt/pilcrow_qt/assets/fonts/{Inter,JetBrainsMono}-Variable[-Italic].ttf from the latin and
latin-ext woff2 subsets (Qt's FreeType cannot load woff2 directly). Needs fonttools and brotli.
The fonts are OFL-licensed; the licence text goes alongside.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qt" / "pilcrow_qt" / "assets" / "fonts"
SRC = ROOT / "node_modules" / "@fontsource-variable"

# (package, woff2 file stem, output name). The variable "wght" axis file covers all weights.
FACES = [
    ("inter", "inter-latin-wght-normal", "Inter-Variable.ttf"),
    ("inter", "inter-latin-ext-wght-normal", "Inter-Variable-LatinExt.ttf"),
    ("inter", "inter-latin-wght-italic", "Inter-Variable-Italic.ttf"),
    ("jetbrains-mono", "jetbrains-mono-latin-wght-normal", "JetBrainsMono-Variable.ttf"),
    ("jetbrains-mono", "jetbrains-mono-latin-ext-wght-normal", "JetBrainsMono-Variable-LatinExt.ttf"),
    ("jetbrains-mono", "jetbrains-mono-latin-wght-italic", "JetBrainsMono-Variable-Italic.ttf"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for pkg, stem, out_name in FACES:
        src = SRC / pkg / "files" / f"{stem}.woff2"
        if not src.exists():
            print(f"missing {src}")
            continue
        f = TTFont(str(src))
        f.flavor = None  # woff2 → plain sfnt
        f.save(str(OUT / out_name))
        n += 1
        print(f"{out_name}: {(OUT / out_name).stat().st_size // 1024} KB")
    for pkg in ("inter", "jetbrains-mono"):
        lic = SRC / pkg / "LICENSE"
        if lic.exists():
            shutil.copy(lic, OUT / f"LICENSE-{pkg}.txt")
    print(f"{n} faces written to {OUT}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
