"""The welcome page against the mock: the greeting, the folder chips, and starting a session from the box."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

MOCK_PORT = 5421


def _welcome(c):
    c.wait_until(lambda: c.store.config is not None and c.store.info is not None, 10, "config and info")
    c.store.active_id = None
    c.store.show_page("session")
    c.sleep(200)
    w = c.win.welcome
    assert w.isVisible()
    return w


def test_greeting_sub_and_chips(app_client):
    from pilcrow_qt.widgets.welcome import greeting

    c = app_client
    w = _welcome(c)
    assert greeting("tester", 3) == "Working late, Tester"
    assert greeting("Lin Wei-Ting", 9) == "Good morning, Lin Wei-Ting"
    assert greeting("", 15) == "Good afternoon"
    assert greeting("x", 20) == "Good evening, X"
    assert w.title.text().endswith(", Test Person")
    assert w.cwd == "/home/tester/code/pilcrow"
    assert (w.sub_a.text(), w.sub_b.text(), w.sub_c.text()) == ("What should Claude work on in ", "pilcrow", "?")
    assert [ch.text() for ch in w.chips] == ["pilcrow", "website"]
    assert w.chips[0]._on and not w.chips[1]._on
    assert w.box.folder_btn.label.text() == "pilcrow"
    # the send button is quiet until there is text
    assert not w.box.send.isEnabled() and not w.box.send._ready
    w.chips[1].click()
    assert w.cwd == "/home/tester/code/website" and w.sub_b.text() == "website"
    assert w.box.folder_btn.label.text() == "website"
    assert w.box.width() == 640
    c.sleep(50)
    assert c.shot("welcome").exists()


def test_box_starts_a_session_in_the_chosen_folder(app_client):
    c = app_client
    w = _welcome(c)
    c.call("mock.pace", {"ms": 0})
    before = {x.get("id") for x in c.store.sessions}
    w.box.edit.setPlainText("fix the parser")
    assert w.box.send.isEnabled() and w.box.send._ready
    QTest.keyClick(w.box.edit, Qt.Key.Key_Return)
    c.wait_until(lambda: len(c.store.sessions) > len(before), 10, "session created")
    new_id = next(x.get("id") for x in c.store.sessions if x.get("id") not in before)
    new = c.store.session(new_id)
    assert new.get("cwd") == "/home/tester/code/pilcrow"
    c.wait_until(lambda: c.store.active_id == new_id, 10, "opened")
    c.wait_until(lambda: any(it.kind == "user" for it in c.store.get_model(new_id).items), 10, "message sent")
    # Shift+Enter is a new line, not a send
    c.store.active_id = None
    c.store.active_changed.emit(None)
    c.sleep(100)
    w.box.edit.setPlainText("one")
    QTest.keyClick(w.box.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in w.box.edit.toPlainText()
    w.box.edit.setPlainText("")
