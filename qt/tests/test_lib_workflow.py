"""Port of test/unit/workflow.test.ts."""

from __future__ import annotations

from typing import Any, Dict

from pilcrow_qt.lib.workflow import (
    PhaseMeta,
    WorkflowCounts,
    WorkflowMeta,
    agent_state,
    parse_workflow_meta,
    workflow_columns,
    workflow_counts,
)

# --- parseWorkflowMeta


def test_reads_name_description_and_phases_from_the_literal():
    script = """export const meta = {
      name: 'review-changes',
      description: "Review changed files, verify each finding",
      phases: [{ title: 'Review' }, { title: "Verify", kind: 'check' }, 'Report'],
    }
    const x = 1;"""
    assert parse_workflow_meta(script) == WorkflowMeta(
        name="review-changes",
        description="Review changed files, verify each finding",
        phases=[PhaseMeta("Review", None), PhaseMeta("Verify", "check"), PhaseMeta("Report")],
    )


def test_is_empty_without_a_meta_block():
    assert parse_workflow_meta("const a = 1") == WorkflowMeta(phases=[])
    assert parse_workflow_meta(None) == WorkflowMeta(phases=[])


def agent(**p: Any) -> Dict[str, Any]:
    return {"type": "workflow_agent", "index": 0, "state": "start", **p}


# --- agentState / workflowCounts


def test_maps_sdk_states_to_card_states():
    assert agent_state(agent(state="done")) == "done"
    assert agent_state(agent(state="done", cached=True)) == "cached"
    assert agent_state(agent(state="error")) == "error"
    assert agent_state(agent(state="error", blocked=True)) == "blocked"
    assert agent_state(agent(state="start", queuedAt=1)) == "queued"
    assert agent_state(agent(state="start", queuedAt=1, startedAt=2)) == "running"
    assert agent_state(agent(state="progress")) == "running"


def test_counts():
    c = workflow_counts(
        [agent(state="done"), None, agent(state="start", queuedAt=1), agent(state="error"), agent(state="progress")]
    )
    assert c == WorkflowCounts(total=4, queued=1, running=1, done=1, error=1)


# --- workflowColumns


def test_uses_announced_phases_fills_in_declared_ones_and_invents_one_for_stray_agents():
    task = {
        "id": "t",
        "status": "running",
        "workflow": {
            "phases": [{"index": 0, "title": "Review (live)"}],
            "agents": [
                agent(index=1, phaseIndex=0),
                agent(index=0, phaseIndex=0),
                agent(index=2, phaseIndex=2, phaseTitle="Extra"),
            ],
            "logs": [],
        },
    }
    cols = workflow_columns(task, WorkflowMeta(phases=[PhaseMeta("Review"), PhaseMeta("Verify")]))
    assert [c.phase["title"] for c in cols] == ["Review (live)", "Verify", "Extra"]
    assert [a["index"] for a in cols[0].agents] == [0, 1]
    assert cols[1].agents == []
    assert [a["index"] for a in cols[2].agents] == [2]
