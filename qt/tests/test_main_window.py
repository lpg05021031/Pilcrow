"""The shell: the sheets on the chrome ground, the seams that drag, the custom title bar."""

from __future__ import annotations

import os

from harness import Client, app_client, mock_server  # noqa: F401
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

MOCK_PORT = 5411


def _ready(c) -> None:
    """Sessions loaded and the boot screen gone (with motion on it fades for 320ms over everything)."""
    c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions to load")
    c.wait_until(lambda: not c.win.boot.isVisible(), 5, "the boot screen to leave")
    c.sleep(50)


def _rgb(img, x: int, y: int) -> tuple:
    c = img.pixelColor(x, y)
    return (c.red(), c.green(), c.blue())


def _tok():
    from pilcrow_qt import theme

    return theme.tokens()


def _blend(a: str, b: str) -> tuple:
    from pilcrow_qt import theme

    c = theme.blend(a, b)
    return (c.red(), c.green(), c.blue())


def _drag(c, resizer, dx: int, y: int = 300) -> None:
    """Drag a seam by ``dx`` window pixels the way a pointer would: the handle moves with the seam,
    so each move is aimed at a window position, not a point on the handle."""
    start = resizer.mapTo(c.win, QPoint(4, y))
    QTest.mousePress(resizer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, y))
    steps = max(1, abs(dx) // 10)
    for i in range(1, steps + 1):
        target = QPoint(start.x() + dx * i // steps, y)
        QTest.mouseMove(resizer, resizer.mapFrom(c.win, target))
        QApplication.processEvents()
    QTest.mouseRelease(
        resizer,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        resizer.mapFrom(c.win, QPoint(start.x() + dx, y)),
    )
    c.sleep(50)


def test_sheet_rests_on_the_ground(app_client):
    """.app { padding: 0 8px 8px 0 }; .main { margin-top: 8px; border-radius: 12px; border: 0.5px solid var(--hair) }"""
    c = app_client
    from pilcrow_qt.widgets.main_window import Card

    _ready(c)
    c.sleep(100)
    win, main, side = c.win, c.win.main, c.win.sidebar
    g = main.geometry()
    assert side.geometry().x() == 0 and side.geometry().y() == 0
    assert g.x() == side.width() + 1, "a 1px seam between the sidebar and the sheet"
    assert g.right() == win.width() - 8 - 1 and g.bottom() == win.height() - 8 - 1
    assert c.win.pages.geometry().y() >= Card.TOP, "the pages lay out under the 8px band"
    t = _tok()
    img = win.grab().toImage()
    chrome = _blend(t.chrome, t.chrome)
    border = _blend(t.hair, t.canvas)
    canvas = _blend(t.canvas, t.canvas)
    x0, y0 = g.x(), g.y() + Card.TOP
    assert _rgb(img, x0 + 100, y0 - 1) == chrome, "the band above the sheet is chrome"
    assert _rgb(img, x0 + 100, y0) == border, "the hairline along the top"
    assert _rgb(img, x0, y0 + 100) == border, "the hairline along the left"
    assert _rgb(img, x0 + 100, y0 + 100) == canvas
    assert _rgb(img, x0, y0) == chrome, "the corner is cut round"
    assert _rgb(img, g.right(), y0) == chrome
    assert _rgb(img, x0 - 1, y0 + 100) != canvas, "nothing of the sheet leaks across the seam"


def test_sidebar_hidden_keeps_the_margin(app_client):
    """.app.no-side > .main { margin-left: 8px }"""
    c = app_client
    _ready(c)
    c.store.set_ui(sidebar=False)
    c.sleep(50)
    assert not c.win.sidebar.isVisible() and not c.win.side_resizer.isVisible()
    assert c.win.main.geometry().x() == 8
    c.store.set_ui(sidebar=True)
    c.sleep(50)
    assert c.win.main.geometry().x() == c.win.sidebar.width() + 1
    assert c.win.side_resizer.isVisible()


def test_drag_the_sidebar_seam(app_client):
    """App.tsx resizeSidebar: between 200 and 440, kept in the ui settings."""
    c = app_client
    _ready(c)
    c.sleep(100)
    rz = c.win.side_resizer
    w0 = c.win.sidebar.width()
    assert rz.geometry().x() == w0 - 4 and rz.width() == 9, "the 9px handle straddles the seam column"
    _drag(c, rz, 40)
    assert c.win.sidebar.width() == w0 + 40
    assert c.store.ui.sidebar_w == w0 + 40
    assert c.win.main.geometry().x() == w0 + 41
    assert rz.geometry().x() == w0 + 40 - 4, "the handle followed the seam"
    _drag(c, rz, 400)
    assert c.win.sidebar.width() == 440 == c.store.ui.sidebar_w
    _drag(c, rz, -600)
    assert c.win.sidebar.width() == 200 == c.store.ui.sidebar_w
    c.store.set_ui(sidebar_w=268)
    c.sleep(50)
    assert c.win.sidebar.width() == 268


def test_drag_the_panel_seam(app_client):
    """App.tsx resizePanel: at least 340, at most 62% of the window."""
    c = app_client
    _ready(c)
    c.open("sess-login")
    c.store.set_ui(panel=True, panel_tab="tasks")
    c.sleep(300)
    pr = c.win.panel_resizer
    host = c.win.panel_host
    assert host.isVisible() and pr.isVisible()
    assert host.geometry().right() == c.win.width() - 8 - 1
    assert pr.geometry().x() == c.win.main.geometry().right() + 1 - 4
    w0 = host.width()
    _drag(c, pr, -50)
    assert host.width() == w0 + 50 == c.store.ui.panel_w
    _drag(c, pr, 2000)
    assert host.width() == 340 == c.store.ui.panel_w
    _drag(c, pr, -3000)
    assert host.width() == round(c.win.width() * 0.62) == c.store.ui.panel_w
    c.store.set_ui(panel=False, panel_w=560)
    c.sleep(50)
    assert not pr.isVisible()


def test_handle_shows_on_rest_and_turns_accent_while_dragging(app_client):
    c = app_client
    _ready(c)
    rz = c.win.side_resizer
    assert rz._shown == 0.0
    QTest.mouseMove(rz, QPoint(4, 300))
    c.sleep(450)  # 120ms rest, then the 180ms fade
    assert rz._shown == 1.0
    QTest.mousePress(rz, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 300))
    assert rz.dragging
    img = rz.grab().toImage()
    t = _tok()
    assert _rgb(img, 4, rz.height() // 2) == _blend(t.accent, t.accent)
    QTest.mouseRelease(rz, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 300))
    assert not rz.dragging


def test_page_titles_are_semibold(app_client):
    c = app_client
    _ready(c)
    c.open("sess-login")
    title = c.win.session_view.title
    assert title.font().weight() == 600 and title.font().pixelSize() == 14
    assert c.win.session_view.head.height() == 48
    assert "transparent" in c.win.session_view.head.styleSheet(), "the head paints nothing over the sheet"


def test_custom_title_bar_is_one_surface_with_the_sidebar(mock_server):
    os.environ["PILCROW_TITLEBAR"] = "custom"
    try:
        c = Client(mock_server)
    finally:
        os.environ.pop("PILCROW_TITLEBAR", None)
    try:
        _ready(c)
        c.sleep(300)
        tb = c.win.titlebar
        assert tb is not None and tb.height() == 38
        t = _tok()
        img = c.win.grab().toImage()
        chrome = _blend(t.chrome, t.chrome)
        assert _rgb(img, 400, 5) == chrome and _rgb(img, 400, 37) == chrome, "no line under the bar"
        assert _rgb(img, 400, 44) == chrome, "the ground continues under it"
        from pilcrow_qt.widgets.main_window import Card

        assert c.win.main.mapTo(c.win, QPoint(0, Card.TOP)).y() == 38 + 8, "the sheet starts 8px under the bar"
        from pilcrow_qt.widgets.ui.bits import Mark

        mark = [m for m in tb.findChildren(Mark)][0]
        assert mark.mapTo(c.win, QPoint(0, 0)).x() == 18, "the mark sits in the sidebar's brand column"
        assert tb.title.text().endswith("Pilcrow")
        assert c.shot("titlebar").exists()
    finally:
        c.close()
