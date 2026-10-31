"""The control room against the mock server: tiles, the finished list, and answering a permission in place."""

from __future__ import annotations

import time
from datetime import datetime

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5371


def _overview(c):
    c.store.show_page("overview")
    c.wait_until(lambda: c.win.overview is not None and c.win.overview.isVisible(), 5, "overview page")
    ov = c.win.overview
    c.wait_until(lambda: ov.fleet is not None, 10, "fleet.state")
    c.sleep(100)
    return ov


def _day_start_ms() -> float:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000


def test_tiles_and_finished_today(app_client):
    c = app_client
    ov = _overview(c)
    labels = [t.key.text() for t in ov.tiles]
    assert labels[:3] == ["Working", "Needs you", "Finished today"]
    assert labels[3].startswith("Spent today")
    assert ov.tile_working.value.text() == "0"
    assert ov.tile_needs.value.text() == "0"
    assert ov.summary.text() == "Nothing running"
    assert not ov.working_sec.isVisible(), "nothing works: the tile says so, the section stays out"
    assert not ov.needs_sec.isVisible()
    today = [s for s in c.store.sessions if not s.get("archived") and (s.get("lastModified") or 0) >= _day_start_ms()]
    assert len(today) >= 1
    rows = ov.finished_rows
    assert {r.sid for r in rows} == {s["id"] for s in today}
    assert ov.tile_finished.value.text() == str(len(today))
    titles = [r.title.text() for r in rows]
    assert "New session" in titles
    assert ov.spend.isVisible()
    assert ov.spend.hour_v.text().startswith("$")
    assert len(ov.spend.bars.buckets) == 12
    assert ov.spend.rows, "the top sessions of the day"
    c.sleep(80)
    assert c.shot("overview").exists()
    # keyboard: j focuses the first card, Enter opens it
    ov.handle_key(0, "j")
    assert ov.focus_index == 0
    assert rows[0]._focused
    from PySide6.QtCore import Qt

    ov.handle_key(Qt.Key.Key_Return, "")
    c.wait_until(lambda: c.store.page == "session" and c.store.active_id == rows[0].sid, 5, "Enter opens the session")


def test_needs_card_answers_in_place(app_client):
    c = app_client
    ov = _overview(c)
    c.call("mock.pace", {"ms": 40})
    c.call("session.send", {"id": "sess-empty", "text": "please ask permission before running a command"})
    c.wait_until(
        lambda: "sess-empty" in ov.needs_cards and ov.needs_cards["sess-empty"].perm is not None, 15, "needs card"
    )
    card = ov.needs_cards["sess-empty"]
    assert ov.needs_sec.isVisible()
    assert card.tool.text() == "Bash"
    assert "npm run build" in card.what.text()
    assert card.note.text() == "Rebuild from scratch"
    assert card.allow_btn.isVisible() and card.allow_btn.property("kind") == "primary"
    assert card.allow_btn.kbd.text() == "1"
    assert card.always_btn.isVisible() and card.always_btn.kbd.text() == "2"
    assert card.deny_btn.kbd.text() == "3"
    assert ov.tile_needs.value.text() == "1"
    assert "1 waiting for you" in ov.summary.text()
    c.sleep(80)
    assert c.shot("overview-needs").exists()
    card.allow_btn.click()
    c.wait_until(lambda: "sess-empty" not in ov.needs_cards, 10, "card leaves")
    c.wait_until(lambda: c.store.rt["sess-empty"].status == "idle", 20, "turn finishes")
    c.wait_until(lambda: ov.tile_needs.value.text() == "0", 5, "needs tile back to 0")


def test_number_keys_answer_the_first_card(app_client):
    c = app_client
    ov = _overview(c)
    c.call("mock.pace", {"ms": 0})
    c.call("session.send", {"id": "sess-tests", "text": "run the command with permission"})
    c.wait_until(
        lambda: "sess-tests" in ov.needs_cards and ov.needs_cards["sess-tests"].perm is not None, 15, "needs card"
    )
    assert ov.handle_key(0, "3")  # deny
    c.wait_until(lambda: "sess-tests" not in ov.needs_cards, 10, "card leaves")
    c.wait_until(lambda: c.store.rt["sess-tests"].status == "idle", 20, "turn finishes")
    _ = time


def test_inbox_page(app_client):
    """The inbox: the empty state from the mock, then a filled state from records shaped like the server's."""
    c = app_client
    c.store.show_page("inbox")
    c.wait_until(lambda: c.win.inbox is not None and c.win.inbox.isVisible(), 5, "inbox page")
    ib = c.win.inbox
    c.wait_until(lambda: ib.items is not None and ib.schedules is not None, 5, "inbox.list and schedules.list")
    assert not ib.run_cards and not ib.sched_cards and not ib.rail.isVisible(), "the hero alone says it"
    assert ib.hero_title.text() == "Nothing runs on its own yet."
    now = time.time() * 1000
    sched = {
        "cwd": "/home/tester/code/pilcrow",
        "prompt": "Audit deps",
        "model": "",
        "effort": "",
        "worktree": False,
        "maxMinutes": 45,
        "createdAt": now,
        "runs": 0,
        "error": None,
        "running": None,
    }
    ib.schedules = [
        {
            **sched,
            "id": "s1",
            "name": "Nightly dependency audit",
            "cadence": {"kind": "daily", "time": "02:00"},
            "mode": "acceptEdits",
            "enabled": True,
            "lastRunAt": now - 5 * 3600e3,
            "lastStatus": "completed",
            "nextRunAt": now + 7 * 3600e3,
            "cadenceLabel": "Every day at 02:00",
        },
        {
            **sched,
            "id": "s2",
            "name": "Monday test sweep",
            "cadence": {"kind": "weekly", "weekday": 1, "time": "09:00"},
            "mode": "plan",
            "enabled": False,
            "lastRunAt": None,
            "lastStatus": None,
            "nextRunAt": None,
            "cadenceLabel": "Mondays at 09:00",
        },
    ]
    run = {
        "scheduleId": "s1",
        "scheduleName": "Nightly dependency audit",
        "cwd": "/home/tester/code/pilcrow",
        "reason": None,
        "manual": False,
    }
    ib.items = [
        {
            **run,
            "id": "i1",
            "sessionId": "sess-login",
            "startedAt": now - 5 * 3600e3 - 120e3,
            "endedAt": now - 5 * 3600e3,
            "durationMs": 120e3,
            "status": "completed",
            "summary": "Updated three packages; tests pass. " * 12,
            "cost": 0.42,
            "files": [{"path": "package.json", "add": 3, "del": 3}],
            "read": False,
        },
        {
            **run,
            "id": "i2",
            "sessionId": None,
            "startedAt": now - 86400e3 - 120e3,
            "endedAt": now - 86400e3,
            "durationMs": 2700e3,
            "status": "failed",
            "reason": "Stopped after 45 minutes",
            "summary": "",
            "cost": 1.2,
            "files": [],
            "read": True,
            "manual": True,
        },
    ]
    ib.render()
    c.sleep(80)
    assert len(ib.run_cards) == 2 and len(ib.sched_cards) == 2
    assert ib.summary.text() == "1 unread" and ib.mark_all.isVisible()
    assert ib.run_cards[0].more.isVisible() and ib.run_cards[0].more.text() == "Read more"
    assert ib.run_cards[1].sum.text() == "Stopped after 45 minutes"
    assert ib.sched_cards[0].next.text().startswith("Next in 7 h")
    assert ib.sched_cards[1].next.text() == "Paused"
    assert c.shot("inbox").exists()
    ib.sched_cards[0].main.click()
    assert ib.filter == "s1" and len(ib.run_cards) == 2
    ib.sched_cards[1].main.click()
    assert ib.filter == "s2" and len(ib.run_cards) == 0
    ib.set_filter(None)
    ib.run_cards[0].x_btn.click()  # inbox.remove is quiet on the mock, which does not know it
    assert [r.it["id"] for r in ib.run_cards] == ["i2"]
