"""Port of test/unit/transcript.test.ts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pilcrow_qt.lib.transcript_model import TranscriptModel

_seq = 0


def uid() -> str:
    global _seq
    _seq += 1
    return f"uuid-{_seq}"


def user_msg(text: str, **extra: Any) -> Dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid(),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "parent_tool_use_id": None,
        **extra,
    }


def stream(event: Dict[str, Any], parent: Optional[str] = None) -> Dict[str, Any]:
    return {"type": "stream_event", "uuid": uid(), "event": event, "parent_tool_use_id": parent}


def assistant(msg_id: str, content: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid(),
        "message": {"id": msg_id, "role": "assistant", "content": content},
        "parent_tool_use_id": None,
        **extra,
    }


def tool_result(tool_use_id: str, content: str, is_error: bool = False, structured: Any = None) -> Dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid(),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}],
        },
        "parent_tool_use_id": None,
        "tool_use_result": structured,
    }


def result(**extra: Any) -> Dict[str, Any]:
    return {
        "type": "result",
        "uuid": uid(),
        "subtype": "success",
        "duration_ms": 1200,
        "total_cost_usd": 0.01,
        "num_turns": 1,
        **extra,
    }


def stream_text(m: TranscriptModel, msg_id: str, index: int, words: List[str]) -> None:
    """A whole streamed text block: start, deltas, stop."""
    m.push(stream({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
    for w in words:
        m.push(stream({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": w}}))
    m.push(stream({"type": "content_block_stop", "index": index}))


# --- TranscriptModel: a plain turn


def test_user_text_streamed_reply_final_assistant_message_and_result():
    m = TranscriptModel()
    m.push(user_msg("hello"))
    assert len(m.items) == 1
    assert m.items[0].text == "hello"

    m.push(stream({"type": "message_start", "message": {"id": "msg_1"}}))
    stream_text(m, "msg_1", 0, ["Hel", "lo ", "there"])
    t = m.items[1]
    assert t.kind == "text"
    assert t.text == "Hello there"
    assert t.streaming is False

    # The final message replaces the draft rather than adding a twin.
    m.push(assistant("msg_1", [{"type": "text", "text": "Hello there!"}]))
    assert len(m.items) == 2
    assert m.items[1].text == "Hello there!"

    m.push(result())
    r = m.items[2]
    assert (r.kind, r.ok, r.duration_ms, r.cost_usd) == ("result", True, 1200, 0.01)
    assert r.chain_uuid == f"uuid-{_seq - 1}"


def test_the_same_uuid_is_never_applied_twice_history_replayed_over_live():
    m = TranscriptModel()
    u = user_msg("once")
    m.push(u)
    m.push(u)
    assert len(m.items) == 1


def test_an_empty_text_block_from_the_api_is_not_shown():
    m = TranscriptModel()
    m.push(assistant("m", [{"type": "text", "text": "   "}]))
    assert len(m.items) == 0


# --- TranscriptModel: tools


def test_tool_input_streams_as_partial_json_then_the_result_settles_the_status():
    m = TranscriptModel()
    m.push(user_msg("read it"))
    m.push(stream({"type": "message_start", "message": {"id": "msg_2"}}))
    m.push(
        stream(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
            }
        )
    )
    m.push(
        stream(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"file_pa'},
            }
        )
    )
    m.push(
        stream(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": 'th": "/a/b.ts"'},
            }
        )
    )
    t = m.tools["toolu_1"]
    assert t.status == "streaming"
    assert t.input == {"file_path": "/a/b.ts"}
    m.push(
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "}"}})
    )
    m.push(stream({"type": "content_block_stop", "index": 0}))
    assert t.status == "running"
    assert t.input_done is True

    m.push(
        assistant("msg_2", [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/a/b.ts"}}])
    )
    assert len([i for i in m.items if i.kind == "tool"]) == 1
    assert m.last_turn_tools == ["toolu_1"]

    m.push(
        tool_result(
            "toolu_1", "line 1\nline 2", False, {"type": "text", "file": {"filePath": "/a/b.ts", "numLines": 2}}
        )
    )
    assert t.status == "done"
    assert t.result is not None and t.result.text == "line 1\nline 2"
    assert t.structured is not None and t.structured["file"]["numLines"] == 2


def test_error_results_are_classified_denied_interrupted_error():
    m = TranscriptModel()
    for tool_id, text in [
        ("a", "The user doesn't want to proceed with this tool use."),
        ("b", "Request interrupted by user for tool use"),
        ("c", "ENOENT: no such file"),
    ]:
        m.push(
            assistant("m-" + tool_id, [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": "x"}}])
        )
        m.push(tool_result(tool_id, text, True))
    assert m.tools["a"].status == "denied"
    assert m.tools["b"].status == "interrupted"
    assert m.tools["c"].status == "error"


def test_a_subagents_steps_nest_under_the_agent_card():
    m = TranscriptModel()
    m.push(
        assistant(
            "m1", [{"type": "tool_use", "id": "agent_1", "name": "Agent", "input": {"description": "look around"}}]
        )
    )
    m.push(
        {
            **assistant("m2", [{"type": "tool_use", "id": "sub_1", "name": "Grep", "input": {"pattern": "x"}}]),
            "parent_tool_use_id": "agent_1",
        }
    )
    m.push({**tool_result("sub_1", "found"), "parent_tool_use_id": "agent_1"})
    agent = m.tools["agent_1"]
    assert len([i for i in m.items if i.kind == "tool"]) == 1
    assert len(agent.children) == 1
    assert agent.children[0].status == "done"
    assert agent.rev > 0  # the parent re-renders when a child changes


def test_the_turn_ending_settles_whatever_was_still_running():
    m = TranscriptModel()
    m.push(assistant("m1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]))
    m.push(stream({"type": "message_start", "message": {"id": "msg_x"}}))
    m.push(stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
    m.push(stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "part"}}))
    m.push(result(subtype="error_during_execution", is_error=True, result="stopped"))
    assert m.tools["t1"].status == "interrupted"
    text = next(i for i in m.items if i.kind == "text")
    assert text.streaming is False
    last = m.items[-1]
    assert (last.kind, last.ok, last.text) == ("result", False, "stopped")


def test_background_tasks_a_task_notification_becomes_a_status_line_and_finishes_the_tool():
    m = TranscriptModel()
    m.push(
        assistant(
            "m1",
            [
                {
                    "type": "tool_use",
                    "id": "bg_1",
                    "name": "Bash",
                    "input": {"command": "npm test", "description": "Run the tests", "run_in_background": True},
                }
            ],
        )
    )
    m.push(tool_result("bg_1", "started", False, {"isAsync": True, "backgroundTaskId": "task_9"}))
    t = m.tools["bg_1"]
    assert t.status == "background"
    m.push({"type": "system", "subtype": "task_started", "uuid": uid(), "task_id": "task_9", "tool_use_id": "bg_1"})
    assert t.task_id == "task_9"
    m.push(
        {
            "type": "system",
            "subtype": "task_notification",
            "uuid": uid(),
            "task_id": "task_9",
            "tool_use_id": "bg_1",
            "status": "completed",
            "summary": "All green",
        }
    )
    assert t.status == "done"
    last = m.items[-1]
    assert (last.kind, last.tag, last.text) == ("notice", "task", "Background command “Run the tests” finished")


# --- TranscriptModel: system messages


def test_interrupts_compaction_retries_and_permission_denials():
    m = TranscriptModel()
    m.push(user_msg("[Request interrupted by user]"))
    assert (m.items[-1].kind, m.items[-1].tag) == ("notice", "interrupt")

    m.push(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "uuid": uid(),
            "compact_metadata": {"trigger": "auto", "pre_tokens": 150000, "post_tokens": 20000},
        }
    )
    c = m.items[-1]
    assert (c.kind, c.trigger, c.pre_tokens) == ("compact", "auto", 150000)

    m.push(
        {"type": "system", "subtype": "api_retry", "uuid": uid(), "attempt": 1, "max_retries": 10, "error_status": 529}
    )
    m.push(
        {"type": "system", "subtype": "api_retry", "uuid": uid(), "attempt": 2, "max_retries": 10, "error_status": 529}
    )
    retries = [i for i in m.items if i.kind == "notice" and i.tag == "retry"]
    assert len(retries) == 1
    assert (retries[0].count, retries[0].text) == (2, "Retrying request (attempt 2 of 10) after HTTP 529")
    # The reply arriving settles the warning into a quiet note.
    m.push(stream({"type": "message_start", "message": {"id": "ok"}}))
    assert (retries[0].tag, retries[0].level, retries[0].text) == (
        "retry-ok",
        "info",
        "Connected after 2 retries (HTTP 529)",
    )

    m.push(assistant("m9", [{"type": "tool_use", "id": "den", "name": "Write", "input": {}}]))
    m.push(
        {
            "type": "system",
            "subtype": "permission_denied",
            "uuid": uid(),
            "tool_use_id": "den",
            "tool_name": "Write",
            "decision_reason": "policy",
        }
    )
    assert m.tools["den"].status == "denied"
    n = m.items[-1]
    assert (n.kind, n.level, n.text, n.detail) == ("notice", "warning", "Write was not allowed", "policy")


def test_slash_commands_and_their_output():
    m = TranscriptModel()
    m.push(user_msg("<command-name>/compact</command-name><command-args>focus on tests</command-args>"))
    u = m.items[-1]
    assert u.kind == "user" and u.command is not None
    assert (u.command.name, u.command.args) == ("compact", "focus on tests")
    m.push(user_msg("<local-command-stdout>Compacted.</local-command-stdout>"))
    assert (m.items[-1].kind, m.items[-1].text) == ("command-output", "Compacted.")
    m.push(user_msg("<local-command-caveat>ignored</local-command-caveat>"))
    assert len(m.items) == 2


def test_an_api_error_shows_once_not_again_as_the_result():
    m = TranscriptModel()
    m.push(assistant("bad", [{"type": "text", "text": "Invalid API key"}], error="authentication_failed"))
    assert len(m.items) == 1
    n = m.items[0]
    assert (n.kind, n.level) == ("notice", "error")
    assert "Authentication failed" in n.text
    m.push(result(subtype="error_during_execution", is_error=True, result="Invalid API key"))
    assert len(m.items) == 1


def test_latest_tool_finds_the_newest_by_name():
    m = TranscriptModel()
    m.push(assistant("a", [{"type": "tool_use", "id": "p1", "name": "ExitPlanMode", "input": {}}]))
    m.push(assistant("b", [{"type": "tool_use", "id": "p2", "name": "ExitPlanMode", "input": {}}]))
    latest = m.latest_tool("ExitPlanMode")
    assert latest is not None and latest.id == "p2"
    assert m.latest_tool("TodoWrite") is None
