"""Port of test/unit/tools.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.tools_meta import expand_by_default, file_path_of, is_file_tool, tool_meta

CWD = "/home/me/proj"


# --- toolMeta


def test_bash_the_description_when_given_else_the_first_line_of_the_command_in_mono():
    m = tool_meta("Bash", {"command": "ls -la\nwc -l"}, CWD)
    assert (m.verb, m.target, m.mono) == ("Run", "ls -la", True)
    m = tool_meta("Bash", {"command": "ls", "description": "List files"}, CWD)
    assert (m.verb, m.target, m.mono) == ("", "List files", False)
    assert tool_meta("Bash", {"command": "sleep 9", "run_in_background": True}, CWD).verb == "Run in background"


def test_read_shows_the_path_relative_to_the_folder_with_the_line_range():
    assert (
        tool_meta("Read", {"file_path": "/home/me/proj/src/a.ts", "offset": 10, "limit": 5}, CWD).target
        == "src/a.ts:10–15"
    )
    assert tool_meta("Read", {"file_path": "/home/me/proj/src/a.ts"}, CWD).target == "src/a.ts"


def test_edit_write_grep_glob():
    m = tool_meta("Edit", {"file_path": "/home/me/proj/x.py"}, CWD)
    assert (m.verb, m.target) == ("Edit", "x.py")
    m = tool_meta("Write", {"file_path": "/elsewhere/x.py"}, CWD)
    assert (m.verb, m.target) == ("Write", "/elsewhere/x.py")
    assert (
        tool_meta("Grep", {"pattern": "TODO", "path": "/home/me/proj/src", "glob": "*.ts"}, CWD).target
        == "TODO in src (*.ts)"
    )
    assert tool_meta("Glob", {"pattern": "**/*.md"}, CWD).target == "**/*.md"


def test_wrong_input_types_never_throw():
    assert tool_meta("Read", {"file_path": 42}, CWD).target == ""
    assert tool_meta("TodoWrite", {"todos": "nope"}, CWD).target == ""
    assert tool_meta("Bash", None, CWD).target == ""


def test_todo_write_summarises_progress():
    todos = [{"status": "completed"}, {"status": "in_progress", "activeForm": "Writing tests"}, {"status": "pending"}]
    assert tool_meta("TodoWrite", {"todos": todos}, CWD).target == "1/3 done · Writing tests"


def test_agent_verb_carries_the_subagent_type():
    m = tool_meta("Agent", {"subagent_type": "Explore", "description": "find callers"}, CWD)
    assert (m.verb, m.target) == ("Agent · Explore", "find callers")
    m = tool_meta("Agent", {"subagent_type": "general-purpose", "prompt": "Look\nmore"}, CWD)
    assert (m.verb, m.target) == ("Agent", "Look")


def test_workflow_name_comes_from_the_script_when_not_given():
    assert tool_meta("Workflow", {"script": "export const meta = { name: 'review-all' }"}, CWD).target == "review-all"


def test_mcp_tools_server_and_tool_names_opaque_ids_dropped():
    assert tool_meta("mcp__github__list_issues", {"query": "open", "repo": "a/b"}, CWD).verb == "Github · list issues"
    assert (
        tool_meta("mcp__github__list_issues", {"query": "open", "repo": "a/b"}, CWD).target == "query open · repo a/b"
    )
    assert tool_meta("mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__read", {}, CWD).verb == "Read"


def test_unknown_tools_fall_back_to_a_string_field():
    m = tool_meta("Mystery", {"url": "http://x", "other": 1}, CWD)
    assert (m.verb, m.target) == ("Mystery", "http://x")
    assert tool_meta("Mystery", {"foo": "bar"}, CWD).target == "bar"


# --- helpers


def test_expand_by_default_is_file_tool_file_path_of():
    assert expand_by_default("Edit") is True
    assert expand_by_default("Read") is False
    assert is_file_tool("NotebookEdit") is True
    assert is_file_tool("Bash") is False
    assert file_path_of("NotebookEdit", {"notebook_path": "/n.ipynb"}) == "/n.ipynb"
    assert file_path_of("Read", None) is None
