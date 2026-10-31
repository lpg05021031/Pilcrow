"""The side panel against the mock server: tabs, Changes (diff, notes), Tasks, Session info, Timeline."""

from __future__ import annotations

import time
from typing import Any, Dict

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtWidgets import QLabel

MOCK_PORT = 5391
SID = "sess-login"

DIFF_FILES = [
    {
        "path": "web/src/lib/auth.ts",
        "oldPath": None,
        "status": "M",
        "binary": False,
        "additions": 2,
        "deletions": 1,
        "truncated": False,
        "hunks": [
            {
                "oldStart": 10,
                "oldLines": 4,
                "newStart": 10,
                "newLines": 5,
                "header": "export function redirectTarget(params) {",
                "lines": [
                    {"t": " ", "s": "export function redirectTarget(params) {", "o": 10, "n": 10},
                    {"t": "-", "s": "  const next = params.get('next')", "o": 11},
                    {"t": "+", "s": "  const next = params.get('next') || '/'", "n": 11},
                    {"t": "+", "s": "  if (!next.startsWith('/')) return '/'", "n": 12},
                    {"t": " ", "s": "  return next", "o": 12, "n": 13},
                    {"t": " ", "s": "}", "o": 13, "n": 14},
                ],
            }
        ],
    },
    {
        "path": "server/routes/signin.js",
        "oldPath": "server/routes/login.js",
        "status": "R",
        "binary": False,
        "additions": 1,
        "deletions": 1,
        "truncated": False,
        "hunks": [
            {
                "oldStart": 1,
                "oldLines": 2,
                "newStart": 1,
                "newLines": 2,
                "lines": [
                    {"t": "-", "s": "const name = 'login'", "o": 1},
                    {"t": "+", "s": "const name = 'signin'", "n": 1},
                    {"t": " ", "s": "module.exports = { name }", "o": 2, "n": 2},
                ],
            }
        ],
    },
]


def _fake_calls(c, monkeypatch, table: Dict[str, Any]) -> None:
    """Answer some RPC methods locally (a value or a function of the params); the rest go to the mock."""
    from PySide6.QtCore import QTimer

    from pilcrow_qt.rpc import Call

    orig = c.store.call

    def fake(method: str, params=None, quiet: bool = False):
        if method not in table:
            return orig(method, params, quiet)
        v = table[method]
        result = v(params or {}) if callable(v) else v
        call = Call(method)
        QTimer.singleShot(0, lambda: call._resolve(result))
        return call

    monkeypatch.setattr(c.store, "call", fake)


def _open_tab(c, tab: str, width: int = 800):
    c.open(SID)
    c.store.set_ui(panel=True, panel_tab=tab, panel_w=width)
    if c.win.panel is not None:
        c.win.panel.reset()  # the panel may have come up at boot (the mock remembers the last tab) before any patching
    c.wait_until(lambda: c.win.panel is not None and c.win.panel.page(tab) is not None, 5, f"{tab} tab")
    c.sleep(50)
    return c.win.panel.page(tab)


def test_tab_strip_and_narrow_panel(app_client):
    c = app_client
    from pilcrow_qt.widgets.panels.right_panel import TabStrip

    _open_tab(c, "changes", 800)
    strip = c.find(TabStrip)[0]
    labels = [b.spec["label"] for b in strip.buttons.values()]
    assert labels == ["Changes", "Files", "Terminal", "Preview", "Tasks", "Timeline", "Session"], labels
    assert strip.active == "changes"
    assert all(b.shows_label for b in strip.buttons.values())
    c.store.set_ui(panel_w=420)
    c.wait_until(lambda: strip.narrow, 3, "narrow strip")
    assert strip.buttons["changes"].shows_label
    assert not strip.buttons["files"].shows_label
    c.store.set_ui(panel_tab="info")
    c.wait_until(lambda: strip.active == "info", 3, "info tab")
    assert strip.buttons["info"].shows_label and not strip.buttons["changes"].shows_label


def test_changes_diff_rows_and_notes(app_client, monkeypatch):
    c = app_client
    _fake_calls(
        c,
        monkeypatch,
        {"git.diff": {"isRepo": True, "root": "/home/tester/code/pilcrow", "branch": "fix/login", "files": DIFF_FILES}},
    )
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.diff_view import CommentEditor

    page = _open_tab(c, "changes", 800)
    c.wait_until(lambda: len(page.view.rows()) == 2, 5, "two file rows")
    rows = page.view.rows()
    assert rows[0].name.text() == "auth.ts" and rows[0].dir.text() == "web/src/lib"
    assert rows[1].dir.text() == "server/routes" and "login.js" in rows[1].dir.toolTip()  # the rename's origin
    assert page.stat_add.text() == "+3" and page.stat_del.text() == "−2" and page.stat_files.text() == "· 2 files"
    assert page.pr_btn.isVisible() and page.commit_btn.isVisible()
    c.wait_until(lambda: rows[0].grid is not None, 3, "grid built")
    grid = rows[0].grid
    assert grid.columns == 1
    kinds = [(r.kind, (r.line or {}).get("t")) for r in grid.rows]
    assert kinds[0] == ("hunk", None) and ("line", "+") in kinds and ("line", "-") in kinds
    add_row = next(i for i, r in enumerate(grid.rows) if r.kind == "line" and r.line["t"] == "+")
    assert grid.row_tint(add_row) == theme.qcolor(theme.tokens().diff_add)
    ctx_row = next(i for i, r in enumerate(grid.rows) if r.kind == "line" and r.line["t"] == " ")
    assert grid.row_tint(ctx_row) is None

    # a note on the second added line, then into the composer
    grid.open_editor("new:12")
    c.wait_until(lambda: c.find(CommentEditor, grid), 3, "comment editor")
    ed = c.find(CommentEditor, grid)[0]
    ed.edit.setPlainText("Use a proper path check here")
    ed.save.click()
    c.wait_until(lambda: page.view.note_count() == 1, 3, "note saved")
    assert page.notes_bar.isVisible() and page.notes_text.text() == "1 note on this diff"
    msg = page.notes_message()
    assert msg.startswith("Please address my review notes on the current changes:")
    assert "web/src/lib/auth.ts" in msg and "- line 12: Use a proper path check here" in msg
    assert "> if (!next.startsWith('/')) return '/'" in msg
    c.sleep(100)
    assert c.shot("panel-changes").exists()

    got: Dict[str, Any] = {}
    c.store.composer_insert.connect(lambda d: got.update(d))
    page.add_notes_btn.click()
    c.wait_until(lambda: got, 3, "composer insert")
    assert got["id"] == SID and got["text"] == msg
    assert page.view.note_count() == 0

    # side by side: two code columns, removed and added lines paired
    c.store.set_ui(diff_mode="split")
    c.wait_until(lambda: grid.columns == 2, 3, "split mode")
    pairs = [
        r
        for r in grid.rows
        if r.kind == "split" and r.left is not None and r.right is not None and r.left is not r.right
    ]
    assert pairs and pairs[0].left["t"] == "-" and pairs[0].right["t"] == "+"
    c.store.set_ui(diff_mode="unified")


def test_changes_send_notes_and_review(app_client, monkeypatch):
    c = app_client
    _fake_calls(c, monkeypatch, {"git.diff": {"isRepo": True, "root": "/r", "branch": "main", "files": DIFF_FILES}})
    page = _open_tab(c, "changes", 800)
    c.wait_until(lambda: len(page.view.rows()) == 2 and page.view.rows()[1].grid is not None, 5, "rows")
    grid = page.view.rows()[1].grid
    grid._save("old:1", "Keep the old name")
    c.wait_until(lambda: page.view.note_count() == 1, 3, "note")
    assert "- line 1 (removed line): Keep the old name" in page.notes_message()
    sent = []
    c.store.sent.connect(sent.append)
    c.call("mock.pace", {"ms": 0})
    page.send_notes_btn.click()
    c.wait_until(lambda: sent == [SID], 3, "sent")
    c.wait_until(lambda: page.view.note_count() == 0, 5, "notes cleared after sending")
    c.wait_until(lambda: c.store.rt[SID].status == "idle", 15, "turn to finish")


def test_tasks_panel_shows_workflow_counts(app_client):
    c = app_client
    now = time.time() * 1000
    workflow = {
        "id": "task-wf",
        "type": "local_workflow",
        "status": "running",
        "workflowName": "Ship the fix",
        "startedAt": now - 90_000,
        "workflow": {
            "phases": [{"index": 0, "title": "Research"}, {"index": 1, "title": "Implement"}],
            "agents": [
                {
                    "type": "workflow_agent",
                    "index": 0,
                    "label": "survey",
                    "phaseIndex": 0,
                    "state": "done",
                    "startedAt": now - 80_000,
                    "lastProgressAt": now - 60_000,
                },
                {
                    "type": "workflow_agent",
                    "index": 1,
                    "label": "builder",
                    "phaseIndex": 1,
                    "state": "progress",
                    "startedAt": now - 50_000,
                    "lastToolName": "Edit",
                },
                {
                    "type": "workflow_agent",
                    "index": 2,
                    "label": "tester",
                    "phaseIndex": 1,
                    "state": "error",
                    "error": "tests failed",
                    "startedAt": now - 40_000,
                },
            ],
            "logs": [{"at": now - 80_000, "message": "started"}],
        },
    }
    bash = {
        "id": "task-bash",
        "type": "local_bash",
        "status": "running",
        "description": "npm run dev",
        "startedAt": now - 5000,
        "progress": "listening on 3000",
    }
    done = {
        "id": "task-done",
        "type": "local_agent",
        "status": "completed",
        "description": "Explore the parser",
        "endedAt": now - 20_000,
        "summary": "Found it",
    }
    c.open(SID)
    c.store.rt[SID].st["tasks"] = [workflow, bash, done]
    c.store.rt_changed.emit(SID)
    page = _open_tab(c, "tasks", 800)
    from pilcrow_qt.widgets.panels.right_panel import Btn, TabStrip
    from pilcrow_qt.widgets.panels.tasks import TaskRow

    c.wait_until(lambda: page.workflow_cards(), 5, "workflow card")
    card = page.workflow_cards()[0]
    assert card.name.text() == "Ship the fix"
    assert card.sub.text().startswith("1 of 3 agents done · 1 running · 1 failed · 1m"), card.sub.text()
    assert card.counts.total == 3 and card.counts.done == 1 and card.counts.running == 1 and card.counts.error == 1
    assert [col.phase["title"] for col in card.cols] == ["Research", "Implement"]
    rows = c.find(TaskRow, page)
    assert [r.task["id"] for r in rows] == ["task-bash", "task-done"]
    assert "listening on 3000" in rows[0].sub.text()
    assert "Stop" in c.texts(Btn, rows[0])
    strip = c.find(TabStrip)[0]
    assert strip.buttons["tasks"]._count == 2  # the workflow and the command are running
    c.sleep(100)
    assert c.shot("panel-tasks").exists()
    c.store.rt[SID].st["tasks"] = []
    c.store.rt_changed.emit(SID)
    c.wait_until(lambda: not page.workflow_cards(), 3, "cards gone")


def test_info_panel_model_and_ring(app_client):
    c = app_client
    from pilcrow_qt.widgets.ui.bits import ContextRing

    page = _open_tab(c, "info", 800)
    c.wait_until(lambda: c.store.rt[SID].st.get("usage"), 5, "usage")
    c.wait_until(
        lambda: page.model_label.text() == "Mock 1", 5, "model label"
    )  # gateway display name, "Claude " trimmed
    rings = c.find(ContextRing, page)
    assert rings and rings[0].used == 21280 and rings[0].total == 200000 and rings[0].text == "11%"
    assert ">21k</b>" in page.gauge_line.text() and "200k tokens" in page.gauge_line.text()
    texts = c.texts(QLabel, page)
    assert "Context window" in texts and "Claude Code" in texts and "MCP servers" in texts and "Project memory" in texts
    assert "Starts with your next message" in texts and "Shown after Claude Code starts." in texts
    assert "~/code/pilcrow" in texts
    c.sleep(100)
    assert c.shot("panel-info").exists()


def test_history_panel_timeline_off(app_client):
    c = app_client
    from pilcrow_qt.widgets.panels.right_panel import EmptyState

    page = _open_tab(c, "history", 800)
    c.wait_until(lambda: page.meta is not None, 5, "timeline meta")
    off = [e for e in c.find(EmptyState, page) if e.isVisible()]
    assert off and off[0].text.text() == "The timeline is off for this folder."
    assert off[0].hint.text() == "Timeline is off in the mock"


def test_history_panel_with_steps(app_client, monkeypatch):
    c = app_client
    now = time.time() * 1000
    steps = [
        {
            "n": 1,
            "at": now - 300_000,
            "kind": "start",
            "tool": None,
            "toolUseId": None,
            "uuid": None,
            "label": "",
            "commit": None,
            "changed": False,
            "files": [],
            "add": 0,
            "del": 0,
        },
        {
            "n": 2,
            "at": now - 200_000,
            "kind": "message",
            "tool": None,
            "toolUseId": None,
            "uuid": "u1",
            "label": "Please fix it",
            "commit": None,
            "changed": False,
            "files": [],
            "add": 0,
            "del": 0,
        },
        {
            "n": 3,
            "at": now - 100_000,
            "kind": "tool",
            "tool": "Edit",
            "toolUseId": "t1",
            "uuid": None,
            "label": "auth.ts",
            "commit": "abc",
            "changed": True,
            "files": [{"path": "web/src/lib/auth.ts", "status": "M", "add": 2, "del": 1}],
            "add": 2,
            "del": 1,
        },
    ]
    _fake_calls(
        c,
        monkeypatch,
        {
            "timeline.steps": {"enabled": True, "reason": None, "steps": steps},
            "timeline.stepDiff": {"files": DIFF_FILES[:1]},
        },
    )
    page = _open_tab(c, "history", 800)
    from pilcrow_qt.widgets.panels.history import StepDetail

    c.wait_until(lambda: len(page._rows) == 3, 5, "step rows")
    assert [r.title for r in page._rows] == ["Session started", "Please fix it", "auth.ts"]
    assert page.list.isVisible() and all(r.warm for r in page._rows), "the rows are blocks, measured as they show"
    assert page.summary.text().startswith("1 change · ")
    assert page.scrubber.isVisible() and page.scrubber.slider.maximum() == 3 and page.scrubber.slider.value() == 3
    page.pick(3)
    c.wait_until(lambda: page.scrubber.slider.value() == 2 and page.now_btn.isVisible(), 3, "picked")
    c.wait_until(
        lambda: (
            c.find(StepDetail, page)
            and c.find(StepDetail, page)[0].view is not None
            and c.find(StepDetail, page)[0].view.rows()
        ),
        5,
        "step diff",
    )
    assert page._since is not None and "Nothing has changed since this point." in c.texts(QLabel, page._since)
    c.sleep(100)
    assert c.shot("panel-history").exists()
    page.scrubber.slider.setValue(3)
    c.wait_until(lambda: page.sel is None, 3, "back to now")
