"""lib/board against the shared cases (test/unit/board-cases.json) and the rest of test/unit/board.test.ts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pilcrow_qt.lib.board import (
    actions_for,
    awaits_review,
    card_line,
    card_state,
    drop_action,
    lane_of,
    ledger_of,
    schedule_card,
    step,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 8, 10, 30).timestamp() * 1000
BASE = {
    "id": "s",
    "title": "Fix the login redirect",
    "cwd": "/p",
    "project": "/p",
    "archived": False,
    "pinned": False,
    "worktree": None,
    "status": "idle",
    "live": False,
    "createdAt": NOW - 3600e3,
}


def card(**patch):
    return {**BASE, "intent": "Carry ?next through the login form.", **patch}


def _rel(v):
    return NOW + v * 1000 if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def test_shared_cases():
    data = json.loads((ROOT / "test" / "unit" / "board-cases.json").read_text(encoding="utf-8"))
    for c in data["cases"]:
        patch = {}
        for k, v in c["session"].items():
            if k in ("turnEnd", "reviewedTurn", "createdAt"):
                patch[k] = _rel(v)
            elif k == "decisions" and isinstance(v, list):
                patch[k] = [{**d, "at": _rel(d["at"])} for d in v]
            else:
                patch[k] = v
        s = {**data["base"], "createdAt": NOW - 3600e3, **patch}
        assert lane_of(s, c["rt"], NOW) == c["lane"], c["name"]
        assert card_state(s, c["rt"], NOW) == c["state"], c["name"]


def test_review_and_decisions():
    s = card(turnEnd=NOW - 60e3)
    assert awaits_review(s)
    assert lane_of(s, None, NOW) == "me" and card_state(s, None, NOW) == "review"
    t = NOW - 60e3
    assert (
        lane_of(card(turnEnd=t, reviewedTurn=t, decisions=[{"at": NOW - 30e3, "kind": "approve"}]), None, NOW) == "done"
    )
    assert (
        lane_of(card(turnEnd=t, reviewedTurn=t, decisions=[{"at": NOW - 30e3, "kind": "park"}]), None, NOW) == "backlog"
    )
    assert (
        lane_of(
            card(turnEnd=NOW - 10e3, reviewedTurn=NOW - 60e3, decisions=[{"at": NOW - 50e3, "kind": "approve"}]),
            None,
            NOW,
        )
        == "me"
    )


def test_actions():
    assert actions_for("review")[0] == {"id": "approve", "label": "Approve and commit", "key": "e", "primary": True}
    assert actions_for("backlog")[0]["id"] == "start"
    assert [a["id"] for a in actions_for("running")] == ["open", "stop"]
    assert [a["id"] for a in actions_for("queued")] == ["open", "stop"]
    assert actions_for("wait")[0] == {"id": "wake", "label": "Wake now", "key": "↵", "primary": True}
    assert any(a["id"] == "wait" and a["key"] == "w" for a in actions_for("backlog"))
    q = {"pending": [{"requestId": "r", "toolName": "AskUserQuestion", "input": {}, "suggestions": []}]}
    assert actions_for("prompt", q)[0]["label"] == "Answer the question"
    p = {"pending": [{"requestId": "r", "toolName": "ExitPlanMode", "input": {}, "suggestions": []}]}
    assert actions_for("prompt", p)[0]["label"] == "Review the plan"


def test_drop_action():
    assert drop_action("backlog", "agent") == "start"
    assert drop_action("wait", "agent") == "wake"
    assert drop_action("review", "done") == "approve"
    assert drop_action("running", "backlog") == "stop"
    assert drop_action("review", "me") is None
    assert drop_action("done", "agent") is None


def test_card_line():
    r = {"asked": "", "did": "", "files": [], "unverified": "", "ask": "Merge without Wayland?", "turn": 1}
    assert card_line(card(report=r), "review")[0] == "Merge without Wayland?"
    assert (
        card_line(card(), "review", {"lastReply": {"text": "All 148 tests pass.", "at": 1}})[0] == "All 148 tests pass."
    )
    rt = {"pending": [{"requestId": "r", "toolName": "Bash", "input": {"command": "npm test"}, "suggestions": []}]}
    assert card_line(card(), "prompt", rt) == ("npm test", "ask")
    assert card_line(card(intent="Do the thing. Then the other."), "backlog")[0] == "Do the thing."
    assert card_line(card(queued={"text": "go", "at": NOW}), "queued")[0].startswith("Queued")


def test_ledger():
    s = card(
        turnEnd=NOW - 60e3,
        costUsd=0.4,
        decisions=[
            {"at": NOW - 50e3, "kind": "reject", "note": "No QWebEngine."},
            {"at": NOW - 10e3, "kind": "approve", "commit": "abc1234def"},
        ],
    )
    led = ledger_of(s, {"activeModel": "claude-opus-5-5"})
    assert [e["kind"] for e in led] == ["intent", "turn", "decision", "decision", "commit"]
    assert led[1]["note"] == "opus-5-5 · $0.40"
    assert led[2]["note"] == "No QWebEngine."
    assert led[4]["label"] == "Committed abc1234"


def test_schedule_card():
    s = schedule_card(
        {
            "id": "sch1",
            "name": "Nightly tests",
            "cwd": "/p",
            "prompt": "Run the suite.",
            "at": NOW + 3600e3,
            "cadence": "daily at 02:00",
            "createdAt": NOW - 86400e3,
        }
    )
    assert s["id"] == "sched:sch1"
    assert lane_of(s, None, NOW) == "wait"
    assert card_line(s, "wait")[0].startswith("Until")


def test_step():
    lanes = {
        "backlog": [card(id="b1"), card(id="b2")],
        "agent": [card(id="a1", status="running")],
        "wait": [],
        "me": [card(id="m1", turnEnd=1), card(id="m2", turnEnd=1), card(id="m3", turnEnd=1)],
        "done": [],
    }
    assert step(lanes, "m1", "down") == "m2"
    assert step(lanes, "m3", "down") == "m3"
    assert step(lanes, "m1", "up") == "m1"
    assert step(lanes, "b2", "right") == "a1"
    assert step(lanes, "a1", "right") == "m1"
    assert step(lanes, "m3", "left") == "a1"
    assert step(lanes, "m3", "right") == "m3"
    assert step(lanes, None, "down") == "b1"
