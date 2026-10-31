"""The theme on palettes: the application palette carries the tokens, ``tint`` gives a widget its own
roles and follows a switch, the native widgets whose stylesheet rules name palette roles are polished
again, and the stylesheet itself is set once and never re-parsed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a
    theme.theme.apply(True)


def _fg(w) -> str:
    return w.palette().color(w.foregroundRole()).name()


def test_palette_carries_the_tokens(app):
    t = theme.DARK
    p = theme.palette(t)
    r = QPalette.ColorRole
    assert p.color(r.Window).name() == t.canvas
    assert p.color(r.WindowText).name() == t.text and p.color(r.Text).name() == t.text
    assert p.color(r.Base).name() == t.inset and p.color(r.AlternateBase).name() == t.raised
    assert p.color(r.Light).name() == theme.blend(t.fill_1, t.canvas).name()
    assert p.color(r.Shadow).name() == theme.blend(t.hair, t.canvas).name()
    assert p.color(r.Link).name() == t.accent_text and p.color(r.LinkVisited).name() == t.accent
    assert p.color(QPalette.ColorGroup.Disabled, r.WindowText).name() == t.text_4


def test_resolve_reads_tokens_blends_and_literals(app):
    t = theme.tokens()
    assert theme.resolve("text_2", t).name() == t.text_2
    assert theme.resolve("fill_2/canvas", t).name() == theme.blend(t.fill_2, t.canvas).name()
    over = theme.blend(t.hair, theme.blend(t.fill_2, t.canvas).name()).name()
    assert theme.resolve("hair/fill_2/canvas", t).name() == over
    assert theme.resolve("#123456", t).name() == "#123456" and theme.resolve("transparent", t).alpha() == 0
    assert theme.resolve(lambda tk: theme.qcolor(tk.ok), t).name() == t.ok
    assert theme.resolve(None, t) is None


def test_tints_follow_the_switch_and_plain_widgets_take_the_app_palette(app):
    host = QWidget()
    lay = QVBoxLayout(host)
    plain = QLabel("plain")
    faint = QLabel("faint")
    theme.tint(faint, fg="text_3")
    boxed = QWidget()
    theme.tint(boxed, bg="chrome")
    for w in (plain, faint, boxed):
        lay.addWidget(w)
    host.show()
    QCoreApplication.processEvents()
    assert _fg(plain) == theme.DARK.text and _fg(faint) == theme.DARK.text_3
    assert boxed.palette().color(QPalette.ColorRole.Window).name() == theme.DARK.chrome
    assert theme.tint_of(faint) == {"fg": "text_3"} and theme.tint_of(plain) == {}
    theme.theme.apply(False)
    try:
        assert _fg(plain) == theme.LIGHT.text, "a child of a window follows the application palette"
        assert _fg(faint) == theme.LIGHT.text_3, "a tint is re-applied from the new tokens"
        assert boxed.palette().color(QPalette.ColorRole.Window).name() == theme.LIGHT.chrome
        theme.untint(faint)
        assert _fg(faint) == theme.LIGHT.text
    finally:
        theme.theme.apply(True)
    host.close()


def test_native_fields_are_repolished_so_their_rules_see_the_new_palette(app):
    host = QWidget()
    lay = QVBoxLayout(host)
    field = QLineEdit("x")
    lay.addWidget(field)
    host.resize(200, 60)
    host.show()
    QCoreApplication.processEvents()
    dark = host.grab().toImage().pixelColor(field.x() + field.width() // 2, field.y() + 2).name()
    assert dark == theme.DARK.inset, "the field's background is palette(base)"
    theme.theme.apply(False)
    try:
        QCoreApplication.processEvents()
        light = host.grab().toImage().pixelColor(field.x() + field.width() // 2, field.y() + 2).name()
        assert light == theme.LIGHT.inset, "after the switch the rule resolves against the new palette"
    finally:
        theme.theme.apply(True)
    host.close()


def test_the_stylesheet_is_static_and_set_once(app):
    before = app.styleSheet()
    assert "palette(" in before and theme.DARK.text not in before and theme.LIGHT.text not in before
    assert "QPushButton" not in before, "buttons are painted, not styled"
    theme.theme.apply(False)
    try:
        assert app.styleSheet() == before
    finally:
        theme.theme.apply(True)
    assert app.styleSheet() == before


def test_button_kinds_and_labels_paint_from_the_tokens(app):
    from pilcrow_qt.widgets.ui.bits import button, label

    b = button("Go", kind="primary")
    assert b.property("kind") == "primary" and b._colors()[0].name() == theme.tokens().accent
    ghost = button("Quiet", kind="ghost", size="sm")
    assert ghost.sizeHint().height() == 26 and ghost._colors()[0] is None
    assert button("Remove", kind="danger")._colors()[1].name() == "#ffffff"
    lb = label("hint", tone="faint")
    assert _fg(lb) == theme.tokens().text_3
