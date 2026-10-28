#!/usr/bin/env python3
"""Make the TTF faces the Qt client bundles from the @fontsource-variable packages in node_modules.

    python scripts/make-qt-fonts.py

Writes qt/pilcrow_qt/assets/fonts/*.ttf: static instances of Inter at the weights the interface uses
(Regular 400, Medium 500, SemiBold 600, Bold 700, plus Italic) and of JetBrains Mono (Regular, Bold,
Italic), each one file with the latin + latin-ext woff2 subsets merged. Static instances rather than the variable font
because FreeType on Linux does not pick a weight out of a variable font the way DirectWrite does:
with the variable file, every weight drew as Regular there. Needs fonttools and brotli.
The fonts are OFL-licensed; the licence text goes alongside.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.merge import Merger
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qt" / "pilcrow_qt" / "assets" / "fonts"
SRC = ROOT / "node_modules" / "@fontsource-variable"

WEIGHTS = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}
# (package, file stems to merge, output family stem, weights, italic). The latin and latin-ext
# subsets are merged into one file per weight: as two files they both register as "Inter Regular"
# and fontconfig may pick the latin-ext one, whose missing ASCII glyphs then fall back to DejaVu.
FACES = [
    ("inter", ("inter-latin-wght-normal", "inter-latin-ext-wght-normal"), "Inter", (400, 500, 600, 700), False),
    ("inter", ("inter-latin-wght-italic", "inter-latin-ext-wght-italic"), "Inter", (400,), True),
    ("jetbrains-mono", ("jetbrains-mono-latin-wght-normal", "jetbrains-mono-latin-ext-wght-normal"), "JetBrainsMono", (400, 700), False),
    ("jetbrains-mono", ("jetbrains-mono-latin-wght-italic", "jetbrains-mono-latin-ext-wght-italic"), "JetBrainsMono", (400,), True),
]


def instance(src: Path, weight: int) -> TTFont:
    f = TTFont(str(src))
    f.flavor = None  # woff2 → plain sfnt
    return instancer.instantiateVariableFont(f, {"wght": weight}, updateFontNames=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.ttf"):
        old.unlink()
    n = 0
    for pkg, stems, out_stem, weights, italic in FACES:
        srcs = [SRC / pkg / "files" / f"{stem}.woff2" for stem in stems]
        srcs = [p for p in srcs if p.exists()]
        if not srcs:
            print(f"missing {stems}")
            continue
        for w in weights:
            parts = []
            for p in srcs:
                tmp = OUT / f".part-{p.stem}-{w}.ttf"
                instance(p, w).save(str(tmp))
                parts.append(tmp)
            style = WEIGHTS[w] + (" Italic" if italic else "")
            name = f"{out_stem}-{style.replace(' ', '')}.ttf"
            if len(parts) == 1:
                parts[0].rename(OUT / name)
            else:
                merged = Merger().merge([str(p) for p in parts])
                merged.save(str(OUT / name))
                for p in parts:
                    p.unlink()
            n += 1
            print(f"{name}: {(OUT / name).stat().st_size // 1024} KB")
    for pkg in ("inter", "jetbrains-mono"):
        lic = SRC / pkg / "LICENSE"
        if lic.exists():
            shutil.copy(lic, OUT / f"LICENSE-{pkg}.txt")
    print(f"{n} faces written to {OUT}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
