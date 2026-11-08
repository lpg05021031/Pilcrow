"""Rules on the device pixel grid (canvas/pixels.py): at a fractional device pixel ratio a hairline
is one device row at full colour (two at 2×), wherever the painter's offset puts it; a snapped
rectangle has its edges on device pixels; the decorations of a Markdown document (table rules, the
quote rule, a well's border) and the Hairline widget come out the same way."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QPoint, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.canvas import pixels  # noqa: E402

GREY = QColor("#808080")
WHITE = 0xFFFFFF


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a


def image(dpr: float, w: int = 100, h: int = 300) -> QImage:
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor("white"))
    return img


def rows(img: QImage, x: int) -> List[Tuple[int, int]]:
    """(device row, rgb) of every row at device column x that is not white."""
    return [(y, img.pixel(x, y) & WHITE) for y in range(img.height()) if (img.pixel(x, y) & WHITE) != WHITE]


def cols(img: QImage, y: int) -> List[Tuple[int, int]]:
    return [(x, img.pixel(x, y) & WHITE) for x in range(img.width()) if (img.pixel(x, y) & WHITE) != WHITE]


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 1.75, 2.0])
def test_a_hairline_is_whole_device_rows_at_full_colour(app, dpr):
    thick = max(1, int(dpr))
    for y in (240, 241, 242, 243):
        for offset in (0, 7):  # the painter's own translation (a view's scroll, a block's origin)
            img = image(dpr)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.translate(0, offset)
            pixels.hline(p, 10, y - offset, 60, GREY)
            p.end()
            got = rows(img, 30)
            assert len(got) == thick, (dpr, y, offset, got)
            assert all(c == 0x808080 for _r, c in got), (dpr, y, offset, got)
            # the row is the one the logical rule covers (its top, on the grid)
            assert abs(got[0][0] - y * dpr) <= 0.5 + 1e-6, (dpr, y, got)
            # nothing beside it: the ends are on the grid too
            ends = cols(img, got[0][0])
            assert ends[0][0] == math.floor(10 * dpr + 0.5) and ends[-1][0] == math.floor(70 * dpr + 0.5) - 1


@pytest.mark.parametrize("dpr", [1.25, 1.5, 2.0])
def test_a_vertical_rule_is_whole_device_columns(app, dpr):
    thick = max(1, int(dpr))
    for x in (40, 41, 42):
        img = image(dpr)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pixels.vline(p, x, 20, 100, GREY)
        p.end()
        got = cols(img, int(60 * dpr))
        assert len(got) == thick and all(c == 0x808080 for _x, c in got), (dpr, x, got)


def test_snap_puts_every_edge_on_the_device_grid(app):
    img = image(1.5)
    p = QPainter(img)
    r = pixels.snap(p, QRectF(10.3, 20.7, 50.2, 30.1))
    for v in (r.left(), r.top(), r.right(), r.bottom()):
        assert abs(v * 1.5 - round(v * 1.5)) < 1e-6, r
    assert r.width() >= 1 / 1.5 and r.height() >= 1 / 1.5
    assert pixels.pen_width(p) == pytest.approx(1 / 1.5)
    assert pixels.thickness(p) == 1
    p.end()
    img2 = image(2.0)
    p2 = QPainter(img2)
    # at 2× a hairline is two device pixels: one logical pixel, as at 1×
    assert pixels.pen_width(p2) == pytest.approx(1.0) and pixels.thickness(p2) == 2
    p2.end()


def test_a_zero_length_rule_draws_nothing(app):
    img = image(1.5)
    p = QPainter(img)
    pixels.hline(p, 10, 10, 0, GREY)
    pixels.vline(p, 10, 10, -3, GREY)
    p.end()
    assert not any(rows(img, x) for x in range(img.width()))


def test_the_rules_of_a_markdown_document_are_single_rows_at_one_and_a_half(app):
    from pilcrow_qt.canvas.paint import paint_decor
    from pilcrow_qt.canvas.view import measurer

    m = measurer()
    doc = m.markdown_doc("> quoted words\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n```py\nx = 1\n```", 400)
    t = theme.tokens()
    img = QImage(int(400 * 1.5), int(doc.size().height() * 1.5) + 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(1.5)
    canvas = QColor(t.canvas)
    img.fill(canvas)
    p = QPainter(img)
    paint_decor(p, doc, 0, 0, None, t)
    p.end()
    # a column through the table's text: the rules under its rows, nothing but single rows of rule colour
    hair = theme.blend(t.hair, t.canvas).rgb() & WHITE
    hair2 = theme.blend(t.hair_2, t.canvas).rgb() & WHITE
    x = int(60 * 1.5)
    hit = [(y, img.pixel(x, y) & WHITE) for y in range(img.height()) if img.pixel(x, y) != canvas.rgb()]
    rules = [(y, c) for y, c in hit if c in (hair, hair2)]
    assert len(rules) >= 3, hit
    assert all(b - a > 1 for (a, _c1), (b, _c2) in zip(rules, rules[1:])), rules  # no two adjacent rows
    others = [(y, c) for y, c in hit if c not in (hair, hair2)]
    # the well's fill is a flat area, not a smear: whatever is not a rule is the inset colour
    inset = QColor(t.inset).rgb() & WHITE
    assert all(c == inset for _y, c in others), others[:5]


def test_the_hairline_widget_paints_one_device_row(app):
    from pilcrow_qt.widgets.ui.bits import Hairline

    w = Hairline()
    w.resize(80, 1)
    for dpr in (1.25, 1.5):
        img = image(dpr, 80, 1)
        p = QPainter(img)
        w.render(p, QPoint(0, 0))
        p.end()
        got = rows(img, int(40 * dpr))
        assert len(got) == 1, (dpr, got)
