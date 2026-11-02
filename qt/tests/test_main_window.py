"""The shell: the sheets on the chrome ground, the seams that drag, the custom title bar."""

from __future__ import annotations

import os

from harness import Client, app_client, mock_server
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


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


def test_dragging_the_panel_seam_repaints_the_panes_that_moved_not_the_sidebar(app_client):
    """The ground repaints the rims where the sheets' shadows fall, not the whole window; the sheets
    paint their own pixels, so a repaint inside one does not paint the ground under it first."""
    from PySide6.QtCore import QEvent, QObject

    c = app_client
    _ready(c)
    c.open("sess-login")
    c.store.set_ui(panel=True, panel_tab="tasks")
    c.sleep(400)

    class Paints(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.n: dict = {}

        def eventFilter(self, o, e) -> bool:
            if e.type() == QEvent.Type.Paint:
                self.n[o] = self.n.get(o, 0) + 1
            return False

    paints = Paints()
    watched = [c.win.sidebar, c.win.body, c.win.main, c.win.panel_host]
    for w in watched:
        w.installEventFilter(paints)
    try:
        for dx in (-12, -12, 12, 12):
            c.win._drag_panel(dx)
            QApplication.processEvents()
    finally:
        for w in watched:
            w.removeEventFilter(paints)
    assert paints.n.get(c.win.sidebar, 0) == 0, paints.n
    assert paints.n.get(c.win.main, 0) >= 4 and paints.n.get(c.win.panel_host, 0) >= 4, paints.n
    assert c.win.main.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    c.store.set_ui(panel=False, panel_w=560)


def test_warming_the_closed_panel_leaves_the_conversation_alone(app_client):
    """The closed panel is laid out and painted off screen once the window is idle; a grab lays a
    hidden widget out as if shown, and the window's row used to make room for it: the conversation
    narrowed and widened again, two reflows and two whole repaints after each session opened."""
    from PySide6.QtCore import QEvent, QObject

    c = app_client
    _ready(c)
    c.open("sess-login")
    c.wait_until(lambda: c.win.panel is not None, 5, "the panel prebuilt")
    c.sleep(200)
    view = c.win.session_view.transcript.view
    sizes: list = []

    class Resizes(QObject):
        def eventFilter(self, obj, e) -> bool:
            if e.type() == QEvent.Type.Resize:
                sizes.append(e.size().width())
            return False

    f = Resizes()
    view.installEventFilter(f)
    try:
        c.win._warm_panel()
        c.sleep(100)
    finally:
        view.removeEventFilter(f)
    assert sizes == [] and not c.win.panel_host.isVisible(), sizes


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

        assert not tb.findChildren(Mark), "no mark in the bar: the sidebar's brand row has it"
        from PySide6.QtWidgets import QLabel

        assert not [lb for lb in tb.findChildren(QLabel) if lb.text()], (
            "no title: the session's name is in the sheet's header"
        )
        # the top corners are rounded (outside them the window is see-through), the bottom ones are not
        w, h = img.width(), img.height()
        assert img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(w - 1, 0).alpha() == 0, "rounded top corners"
        assert img.pixelColor(0, h - 1).alpha() == 255 and img.pixelColor(w - 1, h - 1).alpha() == 255, "square bottom"
        assert _rgb(img, 12, 12) == chrome, "the chrome fills the corner inside the curve"
        assert c.shot("titlebar").exists()
    finally:
        c.close()


def test_edge_resize_cursor_shows_over_any_child(mock_server):
    """The resize cursor follows the pointer along every edge, also where a child takes the moves (the
    sidebar's rows, the conversation), and leaves as soon as the pointer is off the edge."""
    os.environ["PILCROW_TITLEBAR"] = "custom"
    try:
        c = Client(mock_server)
    finally:
        os.environ.pop("PILCROW_TITLEBAR", None)
    try:
        _ready(c)
        c.sleep(200)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest

        win = c.win
        w, h = win.width(), win.height()

        def shape_at(x: int, y: int):
            QTest.mouseMove(win.windowHandle(), QPoint(x, y))
            c.sleep(20)
            cur = QGuiApplication.overrideCursor()
            return cur.shape() if cur is not None else None

        assert win.childAt(3, 400) is not None, "the sidebar is under the left edge"
        assert shape_at(3, 400) == Qt.CursorShape.SizeHorCursor
        assert shape_at(w - 3, h // 2) == Qt.CursorShape.SizeHorCursor
        assert shape_at(w // 2, h - 2) == Qt.CursorShape.SizeVerCursor
        assert shape_at(w // 2, 2) == Qt.CursorShape.SizeVerCursor
        assert shape_at(4, 4) == Qt.CursorShape.SizeFDiagCursor
        assert shape_at(w - 4, h - 4) == Qt.CursorShape.SizeFDiagCursor
        assert shape_at(w - 4, 4) == Qt.CursorShape.SizeBDiagCursor
        assert shape_at(w // 2, h // 2) is None, "off the edge: no override, the child's own cursor"
        assert shape_at(3, 400) == Qt.CursorShape.SizeHorCursor
        win.showMaximized()
        c.sleep(80)
        assert QGuiApplication.overrideCursor() is None, "maximised: no edges"
    finally:
        while QGuiApplication.overrideCursor() is not None:
            QGuiApplication.restoreOverrideCursor()
        c.close()


def test_tooltips_are_painted_pills_not_the_platforms_square_window(mock_server):
    """A control's tooltip is our own window: translucent, so its corners are see-through around the
    rounded pill (the platform's tooltip window is square whatever the stylesheet rounds), with the
    shortcut as keycaps; it goes away when the pointer leaves the control."""
    c = Client(mock_server)
    try:
        _ready(c)
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication

        from pilcrow_qt.widgets.ui import tooltip
        from pilcrow_qt.widgets.ui.bits import IconButton

        btn = next(b for b in c.win.sidebar.findChildren(IconButton) if b.isVisible() and "(Ctrl+B)" in b.toolTip())
        QApplication.sendEvent(btn, QHelpEvent(QEvent.Type.ToolTip, QPoint(4, 4), btn.mapToGlobal(QPoint(4, 4))))
        c.sleep(30)
        tip = tooltip._live_tip()
        assert tip is not None and tip.isVisible()
        assert tooltip.visible_text() == "Hide sidebar (Ctrl+B)" and tip.keys == ["Ctrl", "B"]
        assert tip.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # the window's child, so Qt makes it a transient of the window: a parentless tooltip is a toplevel
        # of its own on Wayland (a window popping up, in the window switcher)
        assert tip.parentWidget() is c.win and tip.isWindow()
        assert tip.windowHandle() is not None and tip.windowHandle().transientParent() is c.win.windowHandle()
        img = tip.grab().toImage()
        body_x, body_y = tooltip.SHADOW, tooltip.SHADOW
        assert img.pixelColor(body_x, body_y).alpha() < 255, "the pill's corner is rounded off"
        pad = img.pixelColor(body_x + 3, img.height() // 2)  # the pill's padding, left of the words
        assert pad.alpha() == 255 and pad.lightness() < 80, "a dark pill"
        # under the control, not over it
        assert tip.y() + tooltip.SHADOW >= btn.mapToGlobal(QPoint(0, btn.height())).y()
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        assert not tip.isVisible()
    finally:
        c.close()


def test_folder_picker_rows_keep_name_and_path_together(mock_server):
    """A recent folder's row reads icon, name, path from the left: the icon keeps its own width and the
    name follows it, instead of the spare width spreading them across the row."""
    c = Client(mock_server)
    try:
        _ready(c)
        from pilcrow_qt.widgets.folder_picker import FolderPicker, _Row

        fp = FolderPicker("/home/tester/code/pilcrow", lambda _p: None)
        fp.resize(380, 300)
        fp.show()
        c.sleep(80)
        rows = [r for r in fp.findChildren(_Row) if r.isVisible()]
        assert rows
        row = rows[0]
        items = [row.lay.itemAt(i).widget() for i in range(row.lay.count()) if row.lay.itemAt(i).widget()]
        icon, name, path = items[0], items[1], items[2]
        assert icon.width() == 14
        assert name.x() == icon.x() + icon.width() + row.lay.spacing(), "the name right after the icon"
        assert path.x() == name.x() + name.width() + row.lay.spacing(), "the path right after the name"
        fp.close()
    finally:
        c.close()
