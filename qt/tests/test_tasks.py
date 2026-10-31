"""The Tasks tab's rows against TasksPanel.tsx / WorkflowBody.tsx: the to-do list with its states,
the finished section, the running cards, and the workflow run with its phases and sub-agents."""

from __future__ import annotations

import time

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtGui import QFontInfo

MOCK_PORT = 5394
SID = "sess-login"

TODO_MSGS = [
    {
        "type": "assistant",
        "message": {
            "id": "msg_todo",
            "role": "assistant",
            "model": "claude-mock-1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_todo1",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [
                            {"content": "Read the auth module", "status": "completed", "activeForm": "Reading"},
                            {
                                "content": "Guard the redirect",
                                "status": "in_progress",
                                "activeForm": "Guarding the redirect",
                            },
                            {"content": "Run the tests", "status": "pending", "activeForm": "Running the tests"},
                        ]
                    },
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        "parent_tool_use_id": None,
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_todo1", "content": "ok", "is_error": False}],
        },
        "parent_tool_use_id": None,
    },
]


def _open_tasks(c):
    c.open(SID)
    c.store.set_ui(panel=True, panel_tab="tasks", panel_w=560)
    c.wait_until(lambda: c.win.panel is not None and c.win.panel.page("tasks") is not None, 5, "tasks tab")
    return c.win.panel.page("tasks")


def test_todo_rows_states_and_finished_section(app_client):
    c = app_client
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.right_panel import Section, Text
    from pilcrow_qt.widgets.panels.tasks import TaskRow, TodoBox

    now = time.time() * 1000
    c.open(SID)
    c.call("mock.messages", {"id": SID, "msgs": TODO_MSGS})
    c.store.rt[SID].st["tasks"] = [
        {
            "id": "t-done",
            "type": "local_agent",
            "status": "completed",
            "description": "Survey the tests",
            "endedAt": now - 20_000,
            "summary": "Found 12 suites",
        },
        {
            "id": "t-fail",
            "type": "local_bash",
            "status": "failed",
            "description": "npm run build",
            "endedAt": now - 120_000,
            "summary": "exit 1",
        },
    ]
    c.store.rt_changed.emit(SID)
    page = _open_tasks(c)
    c.wait_until(lambda: len(c.find(TodoBox, page)) == 3, 5, "todos")
    boxes = c.find(TodoBox, page)
    assert [b.status for b in boxes] == ["completed", "in_progress", "pending"]
    heads = [s.title.text() for s in c.find(Section, page)]
    assert heads == ["To-do list", "Running now", "Finished"]
    for s in c.find(Section, page):
        assert QFontInfo(s.title.font()).pixelSize() == 15 and QFontInfo(s.title.font()).weight() == 600
    faint = [w.text() for w in c.find(Text, page) if w.text() == "1 of 3 done"]
    assert faint
    todo_texts = [
        w for w in c.find(Text, page) if w.text() in ("Read the auth module", "Guarding the redirect", "Run the tests")
    ]
    assert [w.text() for w in todo_texts] == ["Read the auth module", "Guarding the redirect", "Run the tests"]
    assert QFontInfo(todo_texts[1].font()).weight() == 500
    assert todo_texts[1].palette().color(todo_texts[1].foregroundRole()).name() == theme.tokens().text
    assert todo_texts[2].palette().color(todo_texts[2].foregroundRole()).name() == theme.tokens().text_2, "muted"
    rows = c.find(TaskRow, page)
    assert [r.task["id"] for r in rows] == ["t-fail", "t-done"]  # newest first, quiet rows without a card
    assert all(r._done for r in rows)
    assert (
        rows[0].layout().contentsMargins().left() == 2 and rows[0].layout().contentsMargins().top() == 4
    )  # .task-row.done
    assert "failed · 2m ago · exit 1" == rows[0].sub.text()
    _ = theme
    c.store.rt[SID].st["tasks"] = []
    c.store.rt_changed.emit(SID)


def test_running_cards_and_workflow_phases(app_client):
    c = app_client
    from pilcrow_qt.widgets.panels.right_panel import Btn
    from pilcrow_qt.widgets.panels.tasks import AgentRow, PhaseRow, SubAgentRow, TaskRow

    now = time.time() * 1000
    wf = {
        "id": "task-wf",
        "type": "local_workflow",
        "status": "running",
        "workflowName": "Ship the fix",
        "startedAt": now - 90_000,
        "workflow": {
            "phases": [
                {"index": 0, "title": "Research"},
                {"index": 1, "title": "Implement", "kind": "parallel"},
                {"index": 2, "title": "Verify"},
            ],
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
                    "attempt": 2,
                },
                {
                    "type": "workflow_agent",
                    "index": 3,
                    "label": "reviewer",
                    "phaseIndex": 2,
                    "state": "start",
                    "queuedAt": now - 10_000,
                },
            ],
            "logs": [{"at": now - 80_000, "message": "started"}],
        },
    }
    tasks = [
        wf,
        {
            "id": "task-bash",
            "type": "local_bash",
            "status": "running",
            "description": "npm run dev",
            "startedAt": now - 5000,
            "progress": "listening on 3000",
        },
        {
            "id": "task-sub",
            "type": "local_agent",
            "status": "running",
            "description": "Check the fixtures",
            "ownedBySubagent": True,
            "startedAt": now - 45_000,
        },
    ]
    c.open(SID)
    c.store.rt[SID].st["tasks"] = tasks
    c.store.rt_changed.emit(SID)
    page = _open_tasks(c)
    c.wait_until(lambda: page.workflow_cards(), 5, "card")
    card = page.workflow_cards()[0]
    assert card.body.is_open  # a live run opens
    phases = c.find(PhaseRow, card)
    assert [p.state for p in phases] == ["done", "running", "pending"]
    assert [p.node.n for p in phases] == [1, 2, 3]
    agents = c.find(AgentRow, card)
    assert [a.label.text() for a in agents] == ["survey", "builder", "tester", "reviewer"]
    subs = c.find(SubAgentRow, card)
    assert len(subs) == 1 and subs[0]._elbow  # the sub-agent hangs off the running builder
    labels = [b.text() for b in c.find(Btn, card)]
    assert "Log · 1" in labels and "Stop" in labels
    running = [r for r in c.find(TaskRow, page) if not r._done]
    assert [r.task["id"] for r in running] == ["task-bash"]  # the sub-agent is drawn inside the workflow
    assert running[0].layout().contentsMargins().left() == 12  # .task-row card padding
    assert c.win.panel.tabs.buttons["tasks"]._count == 3
    c.store.rt[SID].st["tasks"] = []
    c.store.rt_changed.emit(SID)
    c.wait_until(lambda: not page.workflow_cards(), 3, "cleared")
