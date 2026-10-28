"""The sidebar against the mock server: grouping, order, archive, search, and the measures of app.css."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

MOCK_PORT = 5331


def _ready(c) -> None:
    """Sessions loaded and the boot screen gone (with motion on it fades for 320ms over everything)."""
    c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions to load")
    c.wait_until(lambda: not c.win.boot.isVisible(), 5, "the boot screen to leave")
    c.sleep(50)


def _rows(c):
    from pilcrow_qt.widgets.sidebar import SessionRow

    return [r for r in c.find(SessionRow) if r.isVisible()]


def _heads(c):
    from pilcrow_qt.widgets.sidebar import GroupHead

    return [h for h in c.find(GroupHead) if h.isVisible()]


def _at(w, c) -> QPoint:
    return w.mapTo(c.win, QPoint(0, 0))


def _rgb(img, x: int, y: int) -> tuple:
    p = img.pixelColor(x, y)
    return (p.red(), p.green(), p.blue())


def _fill(name: str) -> tuple:
    from pilcrow_qt import theme

    t = theme.tokens()
    col = theme.blend(getattr(t, name), t.chrome)
    return (col.red(), col.green(), col.blue())


def _hover(w) -> None:
    # Step off the widget first: when the last simulated position already equals its centre (a
    # previous test left it there), the move alone produces no enter event.
    QTest.mouseMove(w, QPoint(-30, -30))
    QApplication.processEvents()
    QTest.mouseMove(w, w.rect().center())
    QApplication.processEvents()


def test_lists_sessions_grouped_by_project(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    ids = [r.sid for r in _rows(c)]
    assert ids[0] == "sess-login", ids  # most recently touched first
    assert "sess-old" not in ids  # archived rows are hidden
    heads = [h.btn.text() for h in _heads(c)]
    assert heads == ["pilcrow", "website"], heads


def test_archive_toggle_and_search(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    c.store.set_ui(show_archived=True)
    c.wait_until(lambda: [r.sid for r in _rows(c)] == ["sess-old"], 5, "archived view")
    c.store.set_ui(show_archived=False)
    c.win.sidebar.search.setText("parser")
    c.wait_until(lambda: [r.sid for r in _rows(c)] == ["sess-tests"], 5, "search")
    assert "font-weight:600" in _rows(c)[0].title.text(), "the match is marked (.side-match)"
    c.win.sidebar.search.setText("")
    c.wait_until(lambda: len(_rows(c)) >= 5, 5, "rows back")


def test_sending_moves_session_up(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    c.call("mock.pace", {"ms": 0})
    c.store.send_message("sess-tests", "hello there")
    c.wait_until(lambda: [r.sid for r in _rows(c)][:1] == ["sess-tests"], 10, "sess-tests at the top")
    c.wait_until(lambda: c.store.rt["sess-tests"].status == "idle", 15, "turn to finish")


def test_measures_match_app_css(app_client):
    """.side-top: 12px 10px 6px, 30px rows 2px apart, the pages 4+6px under a hairline; .side-group
    14px above a 24px head; 30px rows; .side-foot 8px 10px 10px with 28px buttons."""
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    c.sleep(100)
    sb = c.win.sidebar
    assert sb.width() == 268
    assert _at(sb.brand_name, c) == QPoint(42, 12), "mark 15px at 18px, gap 9"
    assert _at(sb.collapse_btn, c) == QPoint(226, 12) and sb.collapse_btn.size().width() == 24
    assert _at(sb.new_btn, c) == QPoint(10, 44) and sb.new_btn.height() == 30
    assert _at(sb.new_opts, c) == QPoint(232, 46) and sb.new_opts.width() == 26
    assert _at(sb.search_row, c) == QPoint(10, 76) and sb.search_row.height() == 30
    assert _at(sb.page_overview, c) == QPoint(10, 118) and sb.page_overview.height() == 30
    assert _at(sb.page_inbox, c) == QPoint(10, 149)
    heads = _heads(c)
    assert _at(heads[0].btn, c) == QPoint(10, 201) and heads[0].btn.height() == 24
    rows = _rows(c)
    assert _at(rows[0], c) == QPoint(10, 225) and rows[0].height() == 30 and rows[0].width() == 248
    assert _at(rows[1], c) == QPoint(10, 255)
    assert _at(rows[0].title, c).x() == 18, ".side-row-main padding-left 8"
    assert _at(heads[1].btn, c).y() == _at(rows[3], c).y() + 30 + 14
    h = c.win.height()
    assert _at(sb.settings_btn, c) == QPoint(10, h - 8 - 10 - 28) and sb.settings_btn.height() == 28
    assert _at(sb.theme_btn, c).x() + 28 == 268 - 10
    assert _at(sb.archive_btn, c).x() + 28 + 2 == _at(sb.theme_btn, c).x()
    img = c.win.grab().toImage()
    assert _rgb(img, 100, 112) == _fill("hair"), "the hairline between the search and the pages"
    assert _rgb(img, 100, 111) == _fill("chrome") and _rgb(img, 100, 113) == _fill("chrome")


def test_search_is_a_plain_row(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    sb = c.win.sidebar
    assert not sb.search.hasFrame()
    assert sb.search.parentWidget() is sb.search_row
    assert not sb.search_row.clear_btn.isVisible()
    img = c.win.grab().toImage()
    assert _rgb(img, 60, 91) == _fill("chrome"), "no box around the field"
    sb.search.setText("deploy")
    c.sleep(50)
    assert sb.search_row.clear_btn.isVisible()
    QTest.keyClick(sb.search, Qt.Key.Key_Escape)
    c.sleep(50)
    assert sb.search.text() == "" and not sb.search_row.clear_btn.isVisible()


def test_hover_reveals_the_options_and_the_menu(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    sb = c.win.sidebar
    assert not sb.new_opts.shown
    _hover(sb.new_btn)
    assert sb.new_opts.shown
    head = _heads(c)[0]
    assert not head.add.shown
    _hover(head)
    assert head.add.shown and not sb.new_opts.shown
    row = _rows(c)[1]
    assert row.time.isVisible() and not row.menu_btn.isVisible()
    _hover(row)
    assert row.menu_btn.isVisible() and not row.time.isVisible()
    assert not head.add.shown
    img = c.win.grab().toImage()
    p = _at(row, c)
    assert _rgb(img, p.x() + 3, p.y() + 15) == _fill("fill_1"), ".side-row:hover"
    _hover(sb.settings_btn)
    assert row.time.isVisible() and not row.menu_btn.isVisible()


def test_active_row_is_a_pill(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    c.open("sess-login")
    rows = _rows(c)
    active = [r for r in rows if r.sid == "sess-login"][0]
    other = [r for r in rows if r.sid != "sess-login"][0]
    assert active._active and not other._active
    img = c.win.grab().toImage()
    p = _at(active, c)
    assert _rgb(img, p.x() + 3, p.y() + 15) == _fill("fill_2"), ".side-row.active"
    assert _rgb(img, p.x() - 1, p.y() + 15) == _fill("chrome"), "inset from the edge (10px gutter)"
    assert _rgb(img, p.x(), p.y()) == _fill("chrome"), "6px corners"
    q = _at(other, c)
    assert _rgb(img, q.x() + 3, q.y() + 15) == _fill("chrome")
    from pilcrow_qt import theme

    assert active.title.font().weight() == 400
    assert theme.tokens().text in active.title.styleSheet()
    assert theme.tokens().text_2 in other.title.styleSheet()


def test_needs_you_section_and_glyph(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    c.call("mock.pace", {"ms": 0})
    c.open("sess-login")
    c.store.send_message("sess-tests", "please ask permission before you run a command")
    c.wait_until(
        lambda: any(s.get("id") == "sess-tests" and s.get("status") == "requires_action" for s in c.store.sessions),
        15,
        "permission",
    )
    from pilcrow_qt.widgets.sidebar import SectionLabel

    c.wait_until(lambda: any(s.text() == "Needs you" for s in c.find(SectionLabel) if s.isVisible()), 5)
    row = [r for r in _rows(c) if r.sid == "sess-tests"][0]
    assert row.glyph.isVisible() and not row.time.isVisible()
    assert row.meta.isVisible() and row.height() == 47, "the project line under the title"
    assert row.meta.project.text() == "pilcrow"
    assert c.shot("sidebar-needs").exists()


def test_screenshot(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    _ready(c)
    assert c.shot("sidebar").exists()
