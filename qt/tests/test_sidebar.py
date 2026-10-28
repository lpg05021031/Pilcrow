"""The sidebar against the mock server: grouping, order, archive, search."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5331


def _rows(c):
    from pilcrow_qt.widgets.sidebar import SessionRow

    return [r for r in c.find(SessionRow) if r.isVisible()]


def test_lists_sessions_grouped_by_project(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    ids = [r.sid for r in _rows(c)]
    assert ids[0] == "sess-login", ids  # most recently touched first
    assert "sess-old" not in ids  # archived rows are hidden
    from pilcrow_qt.widgets.sidebar import GroupHead

    heads = [h.btn.text() for h in c.find(GroupHead) if h.isVisible()]
    assert heads == ["pilcrow", "website"], heads


def test_archive_toggle_and_search(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    c.store.set_ui(show_archived=True)
    c.wait_until(lambda: [r.sid for r in _rows(c)] == ["sess-old"], 5, "archived view")
    c.store.set_ui(show_archived=False)
    c.win.sidebar.search.setText("parser")
    c.wait_until(lambda: [r.sid for r in _rows(c)] == ["sess-tests"], 5, "search")
    c.win.sidebar.search.setText("")
    c.wait_until(lambda: len(_rows(c)) >= 5, 5, "rows back")


def test_sending_moves_session_up(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    c.call("mock.pace", {"ms": 0})
    c.store.send_message("sess-tests", "hello there")
    c.wait_until(lambda: [r.sid for r in _rows(c)][:1] == ["sess-tests"], 10, "sess-tests at the top")
    c.wait_until(lambda: c.store.rt["sess-tests"].status == "idle", 15, "turn to finish")


def test_screenshot(app_client):
    c = app_client
    c.wait_until(lambda: len(_rows(c)) >= 5, 10, "rows")
    assert c.shot("sidebar").exists()
