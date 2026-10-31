"""At a fractional device pixel ratio (1.5, a common laptop setting): is a cached block painted the
same as a direct one (or resampled, so that hovering it makes the text shimmer), and are hairlines
one device pixel or a smeared two?"""

from __future__ import annotations

import os
import subprocess
import sys

CODE = r"""
import os
import sys
sys.path.insert(0, os.environ["PILCROW_STRESS"])
from core import app, Session, assistant, user, pump
app()
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter, QColor, QGuiApplication
from pilcrow_qt.canvas.paint import PaintState
from pilcrow_qt import theme
s = Session("hidpi", [user("hi"), assistant([{"type": "text", "text": "The quick brown fox jumps over the lazy dog. `code` and **bold** text, 中文字也在這裡。\n\n| a | b |\n|---|---|\n| 1 | 2 |"}])], width=901, height=600)
v = s.view
dpr = v.viewport().devicePixelRatioF()
b = [x for x in v.scene.root.walk() if x.KIND == "prose"][0]
r = v.block_rect(b)
def shot(cached: bool):
    img = QImage(int(r.width() * dpr), int(r.height() * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor(theme.tokens().canvas))
    p = QPainter(img)
    st = PaintState(v.m); st.now = 0.0
    rect = QRect(0, 0, r.width(), r.height())
    if cached:
        v._paint_cached(p, b, rect, st)
    else:
        v._paint_block(p, b, rect, st.for_block(None, None, None, None))
    p.end()
    return img
a, c = shot(False), shot(True)
diff = sum(1 for y in range(a.height()) for x in range(a.width()) if a.pixel(x, y) != c.pixel(x, y))
key = (b.key, b.rev, b.sig, r.width(), v.m.theme_name, dpr)
pm = v.m.pixmaps.get(key)
print(f"dpr={dpr} block rect w={r.width()} h={r.height()} col_x={v.col_x} | cached pixmap {pm.width()}x{pm.height()} dpr={pm.devicePixelRatio()} logical={pm.width()/pm.devicePixelRatio():.2f}x{pm.height()/pm.devicePixelRatio():.2f} | direct vs cached differing device pixels: {diff} of {a.width()*a.height()}")
# a hairline (the table rule): how many device rows does it touch?
vp = v.viewport().grab().toImage()
print("viewport image", vp.width(), "x", vp.height(), "dpr", vp.devicePixelRatio())
rule = theme.blend(theme.tokens().hair_2, theme.tokens().canvas).rgb()
canvas = QColor(theme.tokens().canvas).rgb()
# scan a column through the table for rows that are neither canvas nor the exact rule colour (a smeared line)
x = int((v.col_x + 40) * vp.devicePixelRatio())
rows = [(y, hex(vp.pixel(x, y) & 0xFFFFFF)) for y in range(vp.height()) if vp.pixel(x, y) != canvas]
print("non-canvas rows in a column through the table (first 30):", rows[:30])
s.close()
"""

if __name__ == "__main__":
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        QT_SCALE_FACTOR=sys.argv[1] if len(sys.argv) > 1 else "1.5",
        PILCROW_STRESS=os.path.dirname(os.path.abspath(__file__)),
    )
    out = subprocess.run([sys.executable, "-c", CODE], env=env, capture_output=True, text=True, timeout=120)
    print("\n".join(line for line in (out.stdout + out.stderr).splitlines() if "propagate" not in line))
