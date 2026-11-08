"""The workboard against the mock server (the Qt side of the workboard tests in test/e2e/run.mjs)."""

from __future__ import annotations

from harness import app_client, mock_server
from PySide6.QtCore import Qt

MOCK_PORT = 5431


def _board(c):
    c.store.show_page("board")
    c.wait_until(lambda: c.win.board is not None and c.win.board.isVisible(), 5, "board page")
    b = c.win.board
    c.wait_until(lambda: b.state is not None, 10, "board.state")
    c.sleep(150)
    return b


def _ids(b, lane: str):
    return [w.sid for w in b.lanes[lane].card_widgets]


def _in_lane(b, lane: str, sid: str) -> bool:
    return sid in _ids(b, lane)


def test_five_lanes_and_seeded_cards(app_client):
    c = app_client
    b = _board(c)
    assert list(b.lanes) == ["backlog", "agent", "wait", "me", "done"]
    assert [lane.title.text() for lane in b.lanes.values()] == [
        "Backlog",
        "With Claude",
        "Waiting",
        "Needs you",
        "Done",
    ]
    assert _in_lane(b, "me", "sess-login"), "the finished, unreviewed session should need you"
    assert _in_lane(b, "done", "sess-tests"), "the approved session should be done"
    assert _in_lane(b, "backlog", "sess-empty"), "the session with an intent and no turns should be in the backlog"
    assert all(not _in_lane(b, lane, "sess-site") for lane in b.lanes), "a plain conversation at rest is not a card"
    card = b.cards["sess-login"]
    assert card.chip.text() == "To review" and card.chip.isVisible()
    assert b.lanes["agent"].count.text() == "0/3"
    assert "need" in b.facts[0].text() and "you" in b.facts[0].text()
    assert b.limit.isVisible()
    c.sleep(80)
    assert c.shot("board").exists()


def test_approve_moves_a_review_card_to_done(app_client):
    c = app_client
    b = _board(c)
    b.select("sess-login")
    c.wait_until(lambda: b.panel.isVisible() and b.panel.sid == "sess-login", 5, "panel")
    assert b.panel.title.text() == "Fix the login redirect bug"
    assert b.panel.where.text() == "With you · to review"
    assert b.panel.report is not None, "the seeded card carries a handoff report"
    assert len(b.panel._when_labels) >= 2, "the ledger should list the intent and the turn"
    labels = [x.label.text() for x in b.panel.action_buttons]
    assert labels[0] == "Approve and commit" and b.panel.action_buttons[0].kbd.text() == "e"
    c.sleep(80)
    assert c.shot("board-panel").exists()
    b.panel.action_buttons[0].click()
    c.wait_until(lambda: _in_lane(b, "done", "sess-login"), 10, "card moves to Done")
    assert not _in_lane(b, "me", "sess-login")
    # the sidebar's Needs you follows the same rule
    from pilcrow_qt.widgets.sidebar import SectionLabel

    assert not any(lb.text() == "Needs you" for lb in c.find(SectionLabel, c.win.sidebar) if lb.isVisible())


def test_permission_answered_from_the_panel(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 40})
    c.call("session.send", {"id": "sess-empty", "text": "please ask permission before running a command"})
    b = _board(c)
    c.wait_until(lambda: _in_lane(b, "me", "sess-empty") and b.cards["sess-empty"].state == "prompt", 15, "prompt card")
    b.select("sess-empty")
    c.wait_until(lambda: b.panel.perm is not None and b.panel.perm.perm is not None, 5, "permission in the panel")
    assert b.panel.perm.tool.text() == "Bash"
    assert "npm run build" in b.panel.perm.what.text()
    assert b.panel.perm.allow_btn.isVisible() and b.panel.perm.allow_btn.kbd.text() == "1"
    assert b.handle_key(0, "1"), "1 allows"
    c.wait_until(
        lambda: not _in_lane(b, "me", "sess-empty") or b.cards["sess-empty"].state != "prompt", 15, "prompt answered"
    )
    c.wait_until(lambda: c.store.rt["sess-empty"].status == "idle", 20, "turn finishes")


def test_new_card_keeps_the_board(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 0})
    b = _board(c)
    assert b.handle_key(0, "n")
    c.wait_until(lambda: b.new_card is not None and b.new_card.isVisible(), 3, "new card form")
    b.new_card.title.setText("Rename the config file")
    b.new_card.submit(False)
    c.wait_until(lambda: b.new_card is None, 10, "form closes")
    assert any(b.cards[s].title.text() == "Rename the config file" for s in _ids(b, "backlog")), (
        "the card is not in the backlog"
    )
    assert c.store.page == "board", "writing a card must not leave the board"
    assert b.handle_key(0, "n")
    c.wait_until(lambda: b.new_card is not None, 3, "new card form again")
    b.new_card.title.setText("Look at the parser")
    b.new_card.intent.setPlainText("Read the parser and fix the unrecoverable branch.")
    c.sleep(80)
    assert c.shot("board-new").exists()
    b.new_card.submit(True)
    c.wait_until(
        lambda: any(b.cards[s].title.text() == "Look at the parser" for s in _ids(b, "agent") + _ids(b, "me")),
        15,
        "handed to Claude",
    )


def test_handoff_shows_on_the_card_and_in_the_panel(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 0})
    c.call("session.send", {"id": "sess-empty", "text": "look at the parser and fix it"})
    b = _board(c)
    c.wait_until(lambda: _in_lane(b, "me", "sess-empty") and b.cards["sess-empty"].state == "review", 15, "review card")
    c.sleep(200)
    assert "Is a comment enough" in b.cards["sess-empty"].line.text()
    b.select("sess-empty")
    c.wait_until(lambda: b.panel.report is not None, 5, "handoff in the panel")
    from PySide6.QtWidgets import QLabel

    from pilcrow_qt.widgets.dialogs.parts import Text

    texts = [w.text() for w in c.find(Text, b.panel.report)] + [w.text() for w in c.find(QLabel, b.panel.report)]
    assert any("Added one comment" in t for t in texts), "the panel should show what was done"
    assert any("partialJson.ts" in t for t in texts), "the panel should list the changed file"


def test_wait_then_wake(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 0})
    b = _board(c)
    b.select("sess-empty")
    c.wait_until(lambda: b.panel.isVisible(), 5, "panel")
    assert b.handle_key(0, "w")
    c.wait_until(lambda: b.panel.wait_picker is not None, 3, "wait picker")
    assert not b.panel.wait_picker.options[0].isEnabled(), "CI should be offered but off when gh is missing"
    c.sleep(80)
    assert c.shot("board-wait").exists()
    assert b.handle_key(0, "2")  # tomorrow 09:00
    c.wait_until(lambda: _in_lane(b, "wait", "sess-empty"), 5, "card waits")
    assert b.cards["sess-empty"].line.text().startswith("Until")
    b.select("sess-empty")
    c.wait_until(
        lambda: b.panel.action_buttons and b.panel.action_buttons[0].label.text() == "Wake now", 5, "wake action"
    )
    b.handle_key(Qt.Key.Key_Return, "")
    c.wait_until(lambda: _in_lane(b, "agent", "sess-empty") or _in_lane(b, "me", "sess-empty"), 15, "woken")
    assert not _in_lane(b, "wait", "sess-empty")


def test_schedule_menu_opens_the_dialog(app_client):
    c = app_client
    b = _board(c)
    from pilcrow_qt.widgets import dialogs
    from pilcrow_qt.widgets.ui.menu import open_menu

    b.sched_btn.click()
    menu = c.wait_until(open_menu, 3, "schedule menu")
    items = [it for it in menu.menu.items if it.get("label")]
    assert items and items[-1]["label"] == "New schedule…"
    menu.menu.picked.emit(items[-1])
    c.wait_until(lambda: dialogs.current("schedule") is not None, 5, "schedule dialog")
    assert dialogs.current("schedule").title_lb.text() == "New schedule"
    c.store.set_dialog("schedule", None)
    c.wait_until(lambda: dialogs.current("schedule") is None, 3, "dialog closes")
    # The Inbox stays beside the board (dispatch reports land there); the Control room row is what went.
    assert not hasattr(c.win.sidebar, "page_overview"), "the Control room row should be gone from the sidebar"
