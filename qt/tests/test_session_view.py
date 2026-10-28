"""The session page against the mock server: header, composer keys, popups, queue, screenshot."""

from __future__ import annotations

import time

import pytest
from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5361


@pytest.fixture(autouse=True)
def _settled_socket():
    """After each test, let the client's socket finish closing before the next client connects."""
    yield
    from PySide6.QtCore import QCoreApplication

    from pilcrow_qt.rpc import rpc

    end = time.monotonic() + 3
    while rpc._ws is not None and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture(autouse=True, scope="module")
def _fresh_store_afterwards():
    """The store is one per process: leave it with no open session, as the next test module expects to find it."""
    yield
    from pilcrow_qt.store import store

    store.active_id = None
    store.rt.clear()
    store.models.clear()
    store.sessions = []
    store.sessions_loaded = False


def _open(c, sid: str = "sess-login"):
    from pilcrow_qt.widgets.session_view import SessionView

    c.open(sid)
    c.wait_until(
        lambda: isinstance(c.win.session_view, SessionView) and c.win.session_view.sid == sid, 10, "session view"
    )
    v = c.win.session_view
    c.wait_until(lambda: v.git is not None, 10, "git info")
    return v


def _idle(c, sid: str) -> None:
    c.wait_until(lambda: c.store.rt[sid].status == "idle", 20, "idle")


def test_header_shows_title_and_crumbs(app_client):
    c = app_client
    v = _open(c)
    assert v.title.text() == "Fix the login redirect bug"
    assert v.folder_crumb.isVisible() and v.folder_crumb.text() == "pilcrow"
    assert v.branch_crumb.isVisible() and v.branch_crumb.text() == "main"
    assert v.diffstat.isVisible() and v.diffstat.add.text() == "+14" and v.diffstat.dele.text() == "−3"
    assert not v.pill.isVisible()
    assert v.composer.mode_btn.text_lb.text() == "Ask before edits"
    assert v.composer.model_btn.text_lb.text() == "Mock 1"


def test_enter_sends_and_shift_enter_breaks_the_line(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    c.call("mock.pace", {"ms": 0})
    sid = v.sid
    edit = v.composer.edit
    edit.setFocus()
    QTest.keyClicks(edit, "first line")
    QTest.keyClick(edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(edit, "second")
    assert edit.toPlainText() == "first line\nsecond"
    assert v.composer.send_btn.state == "ready"
    before = len(c.store.get_model(sid).items)
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert edit.toPlainText() == ""
    c.wait_until(lambda: len(c.store.get_model(sid).items) > before, 10, "user message in the model")
    texts = [str(getattr(i, "text", "") or "") for i in c.store.get_model(sid).items]
    assert any("first line\nsecond" in t for t in texts), texts
    _idle(c, sid)


def test_busy_shows_stop_and_escape_interrupts(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    sid = v.sid
    c.call("mock.pace", {"ms": 150})
    c.store.send_message(sid, "tell me something slow")
    c.wait_until(lambda: c.store.rt[sid].busy, 10, "busy")
    c.wait_until(lambda: v.composer.send_btn.state == "stop", 5, "stop button")
    assert v.pill.isVisible() and v.pill.text() == "Working"
    assert v.composer.edit.placeholderText() == "Queue a message for after this step…"
    v.composer.edit.setFocus()
    QTest.keyClick(v.composer.edit, Qt.Key.Key_Escape)
    _idle(c, sid)
    assert v.composer.send_btn.state == "idle"
    c.call("mock.pace", {"ms": 0})


def test_slash_opens_the_command_popup(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    edit = v.composer.edit
    edit.setFocus()
    QTest.keyClicks(edit, "/")
    c.wait_until(lambda: v.composer.popup.isVisible(), 5, "command popup")
    labels = [s["label"] for s in v.composer.suggestions]
    assert "/compact" in labels, labels
    assert v.composer.popup.head.text() == "Commands"
    QTest.keyClicks(edit, "comp")
    assert v.composer.suggestions[0]["label"] == "/compact"
    QTest.keyClick(edit, Qt.Key.Key_Tab)
    assert edit.toPlainText() == "/compact "
    assert not v.composer.popup.isVisible()
    edit.clear()


def test_at_opens_the_file_popup(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    edit = v.composer.edit
    edit.setFocus()
    QTest.keyClicks(edit, "@")
    c.wait_until(lambda: v.composer.popup.isVisible() and v.composer.suggestions, 5, "file popup")
    labels = [s["label"] for s in v.composer.suggestions]
    assert "README.md" in labels and "server/main.js" in labels, labels
    QTest.keyClicks(edit, "readme")
    c.wait_until(lambda: v.composer.suggestions and v.composer.suggestions[0]["label"] == "README.md", 5, "fuzzy match")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert edit.toPlainText() == "@README.md "
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    edit.clear()


def test_shift_tab_cycles_the_permission_mode(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    sid = v.sid
    edit = v.composer.edit
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    c.wait_until(lambda: c.store.rt[sid].get("mode") == "acceptEdits", 5, "mode echoed")
    c.wait_until(lambda: v.composer.mode_btn.text_lb.text() == "Edit automatically", 5, "mode chip")
    QTest.keyClick(edit, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    c.wait_until(lambda: c.store.rt[sid].get("mode") == "plan", 5, "plan mode")
    c.store.set_mode(sid, "default")
    c.wait_until(lambda: c.store.rt[sid].get("mode") == "default", 5, "back to default")


def test_sending_while_busy_queues_the_message(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    sid = v.sid
    c.call("mock.pace", {"ms": 150})
    c.store.send_message(sid, "tell me something slow")
    c.wait_until(lambda: c.store.rt[sid].busy, 10, "busy")
    edit = v.composer.edit
    edit.setFocus()
    QTest.keyClicks(edit, "and then this")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    c.wait_until(lambda: len(c.store.rt[sid].held) == 1, 5, "held")
    c.wait_until(lambda: v.composer.queue.isVisible(), 5, "queue tray")
    assert v.composer.queue.head_text.text() == "Queued — Claude reads it after this step"
    from pilcrow_qt.widgets.composer import QueueItem

    items = c.find(QueueItem, v.composer.queue)
    assert len(items) == 1 and items[0].text_btn.text() == "and then this"
    assert edit.placeholderText() == "Queue another message…"
    c.store.interrupt(sid)
    _idle(c, sid)
    c.wait_until(lambda: c.store.rt[sid].status == "idle" and not c.store.rt[sid].busy, 20, "idle again")
    c.call("mock.pace", {"ms": 0})


def test_insert_from_elsewhere_and_drafts(app_client):
    c = app_client
    v = _open(c)
    sid = v.sid
    c.store.insert_into_composer(sid, "Give me a tour of this codebase", True)
    assert v.composer.edit.toPlainText() == "Give me a tour of this codebase"
    c.store.insert_into_composer(sid, "and more", False)
    assert v.composer.edit.toPlainText() == "Give me a tour of this codebase\n\nand more"
    v.composer.edit.clear()


def test_screenshot(app_client):
    c = app_client
    v = _open(c)
    v.composer.edit.setFocus()
    v.composer.insert("Explain the login redirect fix", True)
    c.sleep(100)
    assert v.composer.send_btn.state == "ready"
    assert c.shot("session-view").exists()
    v.composer.edit.clear()
