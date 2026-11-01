"""The Tasks tab's rows against TasksPanel.tsx / WorkflowBody.tsx: the to-do list with its states,
the finished section, the running cards, and the workflow run with its phases and sub-agents."""

from __future__ import annotations

import time

from harness import app_client, mock_server
from PySide6.QtGui import QFontInfo

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
    assert heads == ["To-do list", "Finished"], "a section appears when it has something to show"
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


def test_progress_updates_the_workflow_card_in_place(app_client):
    """A run's progress (ten times a second from the server, coalesced) updates the card's figures and rows
    in place: the card and its phase rows stay, and only an agent whose state changed gets a new row."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    from pilcrow_qt.widgets.panels.tasks import AgentRow, PhaseRow

    c = app_client
    now = time.time() * 1000

    def agents(tick: int):
        out = []
        for i in range(20):
            a = {
                "type": "workflow_agent",
                "index": i,
                "label": f"a{i}",
                "phaseIndex": i // 10,
                "state": "start",
                "queuedAt": now,
            }
            if tick >= i // 2:
                a.update(
                    state="progress",
                    startedAt=now,
                    lastProgressAt=now + tick * 100,
                    lastToolName="Read",
                    lastToolSummary=f"Read mod{tick}.ts",
                    totalTokens=tick * 500,
                )
            if tick >= i // 2 + 8:
                a.update(state="done", resultPreview=f"a{i} done")
            out.append(a)
        return out

    def wf(tick: int):
        return {
            "id": "task-wf2",
            "type": "local_workflow",
            "status": "running",
            "workflowName": "Twenty",
            "startedAt": now,
            "usage": {"total_tokens": tick * 1000, "tool_uses": tick, "duration_ms": tick * 100},
            "workflow": {
                "phases": [{"index": 0, "title": "A"}, {"index": 1, "title": "B"}],
                "agents": agents(tick),
                "logs": [{"at": now, "message": "started"}],
                "updatedAt": now + tick,
            },
        }

    c.open(SID)
    c.store.rt[SID].st["tasks"] = [wf(0)]
    c.store.rt_changed.emit(SID)
    page = _open_tasks(c)
    c.wait_until(lambda: page.workflow_cards(), 5, "card")
    card = page.workflow_cards()[0]
    phases = c.find(PhaseRow, card)
    assert len(phases) == 2 and len(c.find(AgentRow, card)) == 20

    class Made(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.n = 0

        def eventFilter(self, obj, e) -> bool:
            if e.type() == QEvent.Type.ChildAdded and hasattr(obj, "isAncestorOf"):
                try:
                    if obj is page or page.isAncestorOf(obj):
                        self.n += 1
                except RuntimeError:
                    pass
            return False

    made = Made()
    QApplication.instance().installEventFilter(made)
    try:
        for tick in range(1, 4):  # progress only: no agent changes state (a0, a1 start at tick 0; a2, a3 at tick 1…)
            c.store.rt[SID].st["tasks"] = [wf(tick)]
            c.store.rt_changed.emit(SID)
            c.sleep(600)  # the panel refreshes at most twice a second
    finally:
        QApplication.instance().removeEventFilter(made)
    assert page.workflow_cards()[0] is card, "the card stayed"
    assert c.find(PhaseRow, card) == phases, "the phase rows stayed"
    assert card.sub.text().startswith("0 of 20 agents done · 8 running"), card.sub.text()
    rows = c.find(AgentRow, card)
    assert rows[0].what_lbl.text() == "Read mod3.ts", "the row's text followed the tick"
    # ticks 1..3 start agents a2..a7 (queued → running): six rows rebuilt, nothing else
    assert made.n <= 6 * 12, made.n
    # a tick that finishes agents replaces their rows, and the figures follow
    c.store.rt[SID].st["tasks"] = [wf(9)]
    c.store.rt_changed.emit(SID)
    c.wait_until(lambda: card.sub.text().startswith("4 of 20 agents done"), 3, card.sub.text)
    assert page.workflow_cards()[0] is card
    c.store.rt[SID].st["tasks"] = []
    c.store.rt_changed.emit(SID)
    c.wait_until(lambda: not page.workflow_cards(), 3, "cleared")


def test_the_mock_workflow_runs_through_the_panel_and_the_card(app_client):
    """The mock's run of eight agents: the Tasks panel shows the card live and settled; the conversation's
    Workflow card follows the run while it goes (its body reads the task, not only the tool result)."""
    from pilcrow_qt.widgets.panels.tasks import AgentRow

    c = app_client
    c.call("mock.pace", {"ms": 0})
    c.open(SID)
    c.store.set_ui(panel=True, panel_tab="tasks", panel_w=560)
    c.wait_until(lambda: c.win.panel is not None and c.win.panel.page("tasks") is not None, 5, "tasks tab")
    page = c.win.panel.page("tasks")
    c.call("session.send", {"id": SID, "text": "run the review workflow"})
    c.wait_until(
        lambda: page.workflow_cards() and page.workflow_cards()[0].task.get("workflowName") == "review-changes",
        15,
        "the card",
    )
    c.wait_until(lambda: c.store.rt[SID].status == "idle", 20, "the run")
    c.sleep(700)
    card = page.workflow_cards()[0]
    assert card.task.get("status") == "completed"
    assert card.counts.done == 7 and card.counts.error == 1
    assert card.sub.text().startswith("7 of 8 agents done · 1 failed")
    card._toggle()  # a settled run is folded; open it to the rows
    c.sleep(300)
    labels = [a.label.text() for a in c.find(AgentRow, card)]
    assert labels[:3] == ["review:bugs", "review:perf", "review:tests"]
    # the conversation's card settled with the run's figures
    items = list(c.store.models[SID].items)
    wf_item = next(it for it in items if it.kind == "tool" and it.name == "Workflow")
    assert wf_item.status == "done"
    assert c.shot("tasks-workflow-mock").exists()
