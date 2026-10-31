"""Tool cards and the permission prompt as the canvas draws them: items pushed through the transcript
model become blocks (no server), whose heads, bodies, decisions and keys are checked, and a
screenshot of every kind is painted through a real canvas view."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.canvas import nodes as nd  # noqa: E402
from pilcrow_qt.lib.transcript_model import ToolItem, TranscriptModel  # noqa: E402
from pilcrow_qt.store import store  # noqa: E402
from pilcrow_qt.widgets.transcript.ctx import ToolCtx  # noqa: E402

CWD = "/proj"
_seq = 0


def uid() -> str:
    global _seq
    _seq += 1
    return f"u-{_seq}"


def assistant(msg_id: str, content: List[Dict[str, Any]], parent: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid(),
        "message": {"id": msg_id, "role": "assistant", "content": content},
        "parent_tool_use_id": parent,
    }


def tool_use(tid: str, name: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(
    tid: str, content: Any, is_error: bool = False, structured: Any = None, parent: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid(),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}],
        },
        "parent_tool_use_id": parent,
        "tool_use_result": structured,
    }


def make(
    name: str, inp: Dict[str, Any], result: Any = None, is_error: bool = False, structured: Any = None, tid: str = ""
) -> ToolItem:
    """One tool call pushed through the model: the tool_use, then its result when given."""
    tid = tid or f"tool-{uid()}"
    m = TranscriptModel()
    m.push(assistant(f"m-{tid}", [tool_use(tid, name, inp)]))
    if result is not None:
        m.push(tool_result(tid, result, is_error, structured))
    return m.tools[tid]


class FakeCall:
    def then(self, fn):
        return self

    def catch(self, fn):
        return self


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


@pytest.fixture()
def respond(monkeypatch):
    calls: List[tuple] = []

    def fake(sid: str, request_id: str, decision: Dict[str, Any]) -> FakeCall:
        calls.append((sid, request_id, decision))
        return FakeCall()

    monkeypatch.setattr(store, "respond", fake)
    return calls


def ctx(pending: Optional[Dict[str, Any]] = None) -> ToolCtx:
    return ToolCtx(session_id="sess", cwd=CWD, pending=pending or {})


def measurer():
    from pilcrow_qt.canvas.view import measurer

    return measurer()


def block(item: ToolItem, c: Optional[ToolCtx] = None, perm: Optional[dict] = None):
    from pilcrow_qt.canvas.blocks.fold import Flat
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    return ToolBlock(item, c or ctx(), perm, lambda items, cc: Flat(items, cc, "sess", in_work=True))


def body(b) -> nd.Node:
    """The card's body, built and laid out at the column width."""
    b.body.measure(640, measurer())
    assert b.body.root is not None
    return b.body.root


def nodes(n: nd.Node) -> Iterator[nd.Node]:
    yield n
    for c in getattr(n, "children", ()):
        yield from nodes(c)
    child = getattr(n, "child", None)
    if child is not None:
        yield from nodes(child)


def texts(n: nd.Node) -> List[str]:
    """What the leaves of a node tree say (labels, badges, links, buttons, text)."""
    out: List[str] = []
    for x in nodes(n):
        if getattr(x, "children", None) is not None or getattr(x, "child", None) is not None:
            continue
        t = x.text()
        if t:
            out.append(t)
    return out


def html(n: nd.Node) -> str:
    return "\n".join(x._text for x in nodes(n) if isinstance(x, nd.Text))  # noqa: SLF001


def pump(ms: int = 30) -> None:
    import time

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()


# ---------------------------------------------------------------- heads and bodies


def test_read_card_head_and_lines(app):
    item = make(
        "Read",
        {"file_path": "/proj/src/a.ts"},
        "1→const a = 1;\n2→const b = 2;\n3→export { a, b };",
        structured={
            "type": "text",
            "file": {
                "filePath": "/proj/src/a.ts",
                "content": "const a = 1;\nconst b = 2;\nexport { a, b };",
                "numLines": 3,
                "startLine": 1,
                "totalLines": 3,
            },
        },
    )
    b = block(item)
    assert b.meta.verb == "Read"
    assert b.meta.target == "src/a.ts"
    assert b.meta.mono is True
    assert not b.open  # a finished read folds to one line
    root = body(b)
    assert "src/a.ts" in texts(root)
    h = html(root)
    assert "const" in h and ">3<" in h  # highlighted, numbered


def test_read_strips_line_numbers_from_plain_results(app):
    from pilcrow_qt.canvas.blocks.bodies import strip_line_numbers

    assert strip_line_numbers("     5→def f():\n     6→    pass") == ("def f():\n    pass", 5)
    item = make("Read", {"file_path": "/proj/x.py"}, "     5→def f():\n     6→    pass")
    h = html(body(block(item)))
    assert ">5<" in h and ">6<" in h and "def" in h


def test_edit_diff_has_add_and_del_rows(app):
    from pilcrow_qt.canvas.blocks.bodies import edit_hunks
    from pilcrow_qt.canvas.blocks.tool import diff_stats

    old = "a\nb\nc\nd\ne\nf\ng\n"
    new = "a\nb\nc\nD\ne\nf\ng\nh\n"
    item = make(
        "Edit",
        {"file_path": "/proj/src/a.ts", "old_string": old, "new_string": new},
        "ok",
        structured={"filePath": "/proj/src/a.ts"},
    )
    b = block(item)
    assert diff_stats(item) == {"add": 2, "del": 1}  # the +2 −1 at the right of the head
    hunks, numbered = edit_hunks(item)
    kinds = [ln.t for ln in hunks[0].lines]
    assert kinds.count("+") == 2 and kinds.count("-") == 1 and not numbered
    t = theme.tokens()
    h = html(body(b))
    assert theme.blend(t.diff_add, t.inset).name() in h
    assert theme.blend(t.diff_del, t.inset).name() in h


def test_edit_structured_patch_is_numbered(app):
    from pilcrow_qt.canvas.blocks.bodies import edit_hunks

    patch = [{"oldStart": 10, "oldLines": 2, "newStart": 10, "newLines": 2, "lines": [" x", "-y", "+z"]}]
    item = make(
        "Edit",
        {"file_path": "/proj/f.py", "old_string": "y", "new_string": "z"},
        "ok",
        structured={"structuredPatch": patch},
    )
    hunks, numbered = edit_hunks(item)
    assert numbered is True
    assert [(ln.t, ln.o, ln.n) for ln in hunks[0].lines] == [(" ", 10, 10), ("-", 11, None), ("+", None, 11)]
    h = html(body(block(item)))
    assert ">10<" in h and ">11<" in h


def test_bash_card_renders_ansi_output(app):
    item = make(
        "Bash",
        {"command": "npm test", "description": "Run the tests"},
        "\x1b[32mPASS\x1b[0m 3 tests\n\x1b[1mdone\x1b[0m",
    )
    b = block(item)
    assert b.meta.verb == "" and b.meta.target == "Run the tests"
    h = html(body(b))
    assert theme.tokens().ansi_green in h and "PASS" in h and "font-weight:600" in h
    assert "npm test" in h


def test_bash_exit_code_and_tail(app):
    out = "\n".join(f"line {i}" for i in range(40))
    item = make("Bash", {"command": "ls"}, "Exit code 2\n" + out, is_error=True)
    b = block(item)
    assert b.open  # errors open by default
    root = body(b)
    t = texts(root)
    assert "exit 2" in t and "Show all 40 lines" in t
    h = html(root)
    assert "line 39" in h and "line 0\n" not in h
    # the whole output on request
    b.body.state["full"] = True
    b.body.rebuild()
    assert "line 0" in html(body(b))


def test_denied_card_status(app):
    item = make("Bash", {"command": "rm -rf /"}, "The user doesn't want to proceed with this tool use.", is_error=True)
    assert item.status == "denied"
    b = block(item)
    assert not b.open and b.meta.target == "rm -rf /" and b.text() == "Run rm -rf /"


def test_write_and_todo_bodies(app):
    from pilcrow_qt.canvas.blocks.tool import diff_stats

    w = make("Write", {"file_path": "/proj/new.md", "content": "# Hi\n\ntext"}, "ok", structured={"type": "create"})
    assert diff_stats(w) == {"add": 3, "del": 0}
    todos = [
        {"content": "Read", "status": "completed"},
        {"content": "Fix it", "activeForm": "Fixing it", "status": "in_progress"},
        {"content": "Test", "status": "pending"},
    ]
    t = block(make("TodoWrite", {"todos": todos}, "ok"))
    assert t.meta.target == "1/3 done · Fixing it"
    assert [x for x in texts(body(t)) if x in ("Read", "Fixing it", "Test")] == ["Read", "Fixing it", "Test"]


def test_search_body_lists_files(app):
    item = make(
        "Grep",
        {"pattern": "foo", "path": "/proj"},
        "x",
        structured={"filenames": ["/proj/a.ts", "/proj/src/b.ts"], "numFiles": 2},
    )
    t = texts(body(block(item)))
    assert "2 files" in t and "src/b.ts" in t and "a.ts" in t


def test_agent_card_with_nested_children(app):
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    m = TranscriptModel()
    m.push(
        assistant(
            "m1",
            [
                tool_use(
                    "agent_1",
                    "Agent",
                    {"description": "look around", "subagent_type": "Explore", "prompt": "Find the parser"},
                )
            ],
        )
    )
    m.push(assistant("m2", [tool_use("sub_1", "Grep", {"pattern": "parse"})], parent="agent_1"))
    m.push(tool_result("sub_1", "found", parent="agent_1"))
    m.push(
        {
            "type": "assistant",
            "uuid": uid(),
            "message": {
                "id": "m3",
                "role": "assistant",
                "content": [{"type": "text", "text": "It lives in lib/parser.ts"}],
            },
            "parent_tool_use_id": "agent_1",
        }
    )
    m.push(tool_result("agent_1", "It lives in lib/parser.ts"))
    agent = m.tools["agent_1"]
    b = block(agent)
    assert b.meta.verb == "Agent · Explore" and b.meta.target == "look around"
    assert b.children_group is not None and b.reports is not None
    nested = [x for x in b.walk() if isinstance(x, ToolBlock)]
    assert [x.item.name for x in nested] == ["Grep"]
    assert nested[0].ctx.depth == 1
    assert "Explore" in texts(body(b))
    # the agent's report is its last words already, so it is not shown twice
    b.reports.measure(640, measurer())
    assert not b.reports.root.children and b.reports.h == 0
    # a report that says more than the last words is shown
    m.push(tool_result("agent_1", "Found it: lib/parser.ts, with a test in lib/parser.test.ts"))
    agent = m.tools["agent_1"]
    b2 = block(agent)
    b2.reports.measure(640, measurer())
    assert "lib/parser.test.ts" in b2.reports.root.text()


def test_generic_and_mcp_body(app):
    item = make("mcp__github__list_issues", {"repo": "a/b", "state": "open"}, "#1 bug\n#2 feature")
    b = block(item)
    assert b.meta.verb == "Github · list issues"
    root = body(b)
    t = texts(root)
    assert "Input" in t
    h = html(root)
    assert "repo" in h and "a/b" in h and "#2 feature" in h


def test_sync_skips_same_rev_and_keeps_the_fold(app):
    item = make("Read", {"file_path": "/proj/a.ts"}, "x")
    c = ctx()
    b = block(item, c)
    body(b)
    before = b.body.root
    assert b.sync(item, c, None) == [] and b.body.root is before
    b.user_open = True
    b.open = True
    b.set_reveal(1.0)
    item.rev += 1
    changed = b.sync(item, c, None)
    assert b.body in changed and b.body.root is None  # built again when it is next measured
    assert b.open and b.reveal == 1.0


# ---------------------------------------------------------------- AskUserQuestion


ASK_INPUT = {
    "questions": [
        {
            "question": "Which framework?",
            "header": "Framework",
            "options": [{"label": "React", "description": "The usual"}, {"label": "Vue"}],
        },
        {"question": "Which tools?", "multiSelect": True, "options": [{"label": "ESLint"}, {"label": "Prettier"}]},
    ]
}


def special(item: ToolItem, perm: Optional[dict] = None):
    """The question / plan island and its widget, as the canvas builds them."""
    from pilcrow_qt.canvas.blocks.tool import SpecialIsland, is_special

    assert is_special(item, perm)
    isl = SpecialIsland(item, ctx({item.id: perm} if perm else {}), perm)
    w = isl.ensure_widget()
    w.show()
    pump()
    return isl, w


def test_ask_body_answers_through_respond(app, respond):
    from pilcrow_qt.widgets.transcript.bodies.ask import AskBody

    item = make("AskUserQuestion", ASK_INPUT)
    perm = {
        "requestId": "req-ask",
        "toolName": "AskUserQuestion",
        "input": ASK_INPUT,
        "suggestions": [],
        "interactive": True,
        "at": 0,
    }
    _isl, body_w = special(item, perm)
    assert isinstance(body_w, AskBody) and body_w.live
    labels = [lb.text() for lb in body_w.findChildren(type(body_w.findChildren(QWidget)[0]))] if False else None
    assert "Claude has 2 questions" in [lb.text() for lb in _labels(body_w)]
    assert not body_w.send_btn.isEnabled()
    body_w.rows[0][0].toggled.emit()  # React
    body_w.rows[1][0].toggled.emit()  # ESLint
    body_w.rows[1][1].toggled.emit()  # Prettier
    body_w.other_fields[1].setText("Biome")
    body_w.note_fields[0].setText("keep it simple")
    assert body_w.send_btn.isEnabled()
    body_w.send_btn.click()
    assert respond == [
        (
            "sess",
            "req-ask",
            {
                "behavior": "allow",
                "updatedInput": {
                    **ASK_INPUT,
                    "answers": {"Which framework?": "React", "Which tools?": "ESLint, Prettier, Biome"},
                    "annotations": {"Which framework?": {"notes": "keep it simple"}},
                },
            },
        )
    ]
    _ = labels


def _labels(w: QWidget) -> List[QWidget]:
    from PySide6.QtWidgets import QLabel

    return [lb for lb in w.findChildren(QLabel) if lb.text()]


def test_ask_single_select_other_clears_choice(app, respond):
    item = make("AskUserQuestion", {"questions": ASK_INPUT["questions"][:1]})
    perm = {
        "requestId": "r2",
        "toolName": "AskUserQuestion",
        "input": {"questions": ASK_INPUT["questions"][:1]},
        "suggestions": [],
    }
    _isl, body_w = special(item, perm)
    body_w.rows[0][1].toggled.emit()
    body_w.other_fields[0].setText("Svelte")
    assert body_w.sel[0] == [] and not body_w.rows[0][1].on
    body_w.skip_btn.click()
    assert respond[-1][2]["behavior"] == "deny"


def test_ask_done_shows_answers(app):
    item = make("AskUserQuestion", ASK_INPUT, "ok", structured={"answers": {"Which framework?": "React"}})
    _isl, body_w = special(item)
    assert body_w.answers == ["React", "No answer"]
    assert "Claude asked a few questions" in [lb.text() for lb in _labels(body_w)]


# ---------------------------------------------------------------- ExitPlanMode


def test_plan_body_approves_and_keeps_planning(app, respond):
    plan = "# Plan\n\n1. Do the thing\n2. Test it"
    item = make("ExitPlanMode", {"plan": plan})
    perm = {"requestId": "req-plan", "toolName": "ExitPlanMode", "input": {"plan": plan}, "suggestions": []}
    _isl, body_w = special(item, perm)
    assert "Plan ready for review" in [lb.text() for lb in _labels(body_w)]
    body_w.ask_btn.click()
    assert respond[-1] == (
        "sess",
        "req-plan",
        {
            "behavior": "allow",
            "updatedInput": {"plan": plan},
            "updatedPermissions": [{"type": "setMode", "mode": "default", "destination": "session"}],
        },
    )
    body_w._set_busy(False)
    body_w.keep_btn.click()
    assert body_w.feedback_box.isVisible()
    body_w.feedback.setPlainText("Add tests first")
    body_w.send_btn.click()
    assert respond[-1][2] == {"behavior": "deny", "message": "The user wants changes to the plan: Add tests first"}


# ---------------------------------------------------------------- PermissionCard


def perm_bash(request_id: str = "req-1", **extra: Any) -> Dict[str, Any]:
    return {
        "requestId": request_id,
        "toolName": "Bash",
        "input": {"command": "npm test"},
        "title": "Allow Claude to run this command?",
        "suggestions": [
            {
                "type": "addRules",
                "behavior": "allow",
                "destination": "localSettings",
                "rules": [{"toolName": "Bash", "ruleContent": "npm:*"}],
            }
        ],
        "interactive": False,
        "at": 0,
        **extra,
    }


def test_permission_card_key_1_allows(app, respond):
    from pilcrow_qt.widgets.transcript.permission import PermissionCard

    perm = perm_bash()
    c = ctx({"tool-x": perm})
    win = QWidget()
    lay = QVBoxLayout(win)
    pc = PermissionCard(perm, c, item=None)
    lay.addWidget(pc)
    win.show()
    win.activateWindow()
    pump()
    assert pc.first
    assert pc.title_text == "Allow Claude to run this command?"  # an orphan gets the default words
    assert pc.rule == "Bash(npm:*)"
    QTest.keyClick(win, Qt.Key_1)
    pump()
    assert respond == [("sess", "req-1", {"behavior": "allow", "decisionClassification": "user_temporary"})]
    win.close()


def test_permission_card_always_allow_and_deny_with_note(app, respond):
    from pilcrow_qt.widgets.transcript.permission import PermissionCard

    perm = perm_bash("req-2")
    item = make("Bash", {"command": "npm test"})
    pc = PermissionCard(perm, ctx({item.id: perm}), item=item)
    pc.show()
    pump()
    assert (
        pc.always_btn is not None and "Always allow Bash(npm:*) in this project (just you)" in pc.always_btn.toolTip()
    )
    pc.always_btn.click()
    assert respond[-1][2] == {
        "behavior": "allow",
        "updatedPermissions": perm["suggestions"],
        "decisionClassification": "user_permanent",
    }
    pc._set_busy(False)
    pc.note_link.click()
    assert pc.feedback_row.isVisible()
    pc.feedback.setText("use pnpm instead")
    assert pc.deny_btn.text() == "Deny with note"
    QTest.keyClick(pc.feedback, Qt.Key_Return)
    assert respond[-1][2] == {
        "behavior": "deny",
        "message": "The user declined and said: use pnpm instead",
        "decisionClassification": "user_reject",
    }


def test_permission_card_under_the_tool_row_and_keys_only_for_first(app, respond):
    from pilcrow_qt.widgets.transcript.permission import PermissionCard

    perm = perm_bash("req-3", defaultToNo=True)
    item = make("Bash", {"command": "npm test"})
    other = perm_bash("req-0")
    c = ctx({"someone-else": other, item.id: perm})
    b = block(item, c, perm)
    assert b.awaiting and b.open and b.head_h == 32
    assert b.perm_island is not None
    pc = b.perm_island.ensure_widget()
    assert isinstance(pc, PermissionCard) and not pc.first and not pc._shortcuts
    assert pc.deny_btn.property("kind") == "primary" and not pc.allow_btn.property("kind")
    pc.allow_btn.click()
    assert respond[-1][1] == "req-3"
    # The answer arrives: the request leaves the context, the prompt goes and the card folds again.
    b.sync(item, ctx({}), None)
    assert not b.awaiting and b.perm_island is None and not b.open and b.head_h == 28


def test_typing_elsewhere_and_suggestion_helpers(app):
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

    from pilcrow_qt.widgets.transcript.permission import (
        describe_suggestion,
        suggestion_label,
        suggestion_rules,
        typing_elsewhere,
    )

    assert typing_elsewhere(None) is False
    f = QLineEdit()
    f.setText("x")
    assert typing_elsewhere(f) is True
    composer = QPlainTextEdit()
    composer.setObjectName("composer")
    assert typing_elsewhere(composer) is False
    composer.setPlainText("1")
    assert typing_elsewhere(composer) is True
    s = {
        "type": "addRules",
        "behavior": "allow",
        "destination": "userSettings",
        "rules": [{"toolName": "Bash", "ruleContent": "git:*"}, {"toolName": "Bash", "ruleContent": "npm:*"}],
    }
    assert suggestion_label(s) == "Always allow these 2 commands everywhere"
    assert suggestion_rules(s) == "Bash(git:*)  ·  Bash(npm:*)"
    assert (
        describe_suggestion({"type": "setMode", "mode": "acceptEdits", "destination": "session"})
        == "Allow all edits for this session"
    )
    assert (
        describe_suggestion({"type": "addDirectories", "directories": ["/proj/sub"], "destination": "session"}, CWD)
        == "Allow access to sub for this session"
    )


# ---------------------------------------------------------------- the picture


def test_screenshot_of_every_card_kind(app):
    from pilcrow_qt.canvas.blocks.tool import SpecialIsland
    from pilcrow_qt.canvas.scene import Group, Scene
    from pilcrow_qt.canvas.transcript import OrphanIsland
    from pilcrow_qt.canvas.view import CanvasView

    items: List[Any] = []
    items.append(
        make(
            "Read",
            {"file_path": "/proj/src/parser.ts"},
            "1→export function parse(src: string) {\n2→  return tokens(src);\n3→}",
        )
    )
    old = "function parse(src) {\n  return tokens(src);\n}\n"
    new = "function parse(src: string) {\n  const t = tokens(src);\n  return t;\n}\n"
    items.append(make("Edit", {"file_path": "/proj/src/parser.ts", "old_string": old, "new_string": new}, "ok"))
    bash = make(
        "Bash",
        {"command": "npm test -- parser"},
        "\x1b[32m✓\x1b[0m parser handles comments\n\x1b[32m✓\x1b[0m parser handles strings\n\x1b[1m2 passed\x1b[0m",
    )
    bash.start_ts, bash.end_ts = 0, 2300
    items.append(bash)
    items.append(
        make("Bash", {"command": "rm -rf build"}, "The user doesn't want to proceed with this tool use.", is_error=True)
    )
    items.append(
        make(
            "Grep",
            {"pattern": "TODO", "path": "/proj"},
            "x",
            structured={"filenames": ["/proj/src/a.ts", "/proj/src/b.ts"], "numFiles": 2},
        )
    )
    items.append(
        make(
            "TodoWrite",
            {
                "todos": [
                    {"content": "Read the parser", "status": "completed"},
                    {"content": "Fix the comment bug", "activeForm": "Fixing the comment bug", "status": "in_progress"},
                    {"content": "Run the tests", "status": "pending"},
                ]
            },
            "ok",
        )
    )
    m = TranscriptModel()
    m.push(
        assistant(
            "m1",
            [
                tool_use(
                    "agent_1",
                    "Agent",
                    {
                        "description": "Explore the parser",
                        "subagent_type": "Explore",
                        "prompt": "Find where comments are parsed",
                    },
                )
            ],
        )
    )
    m.push(assistant("m2", [tool_use("sub_1", "Grep", {"pattern": "comment", "path": "/proj/src"})], parent="agent_1"))
    m.push(tool_result("sub_1", "x", structured={"filenames": ["/proj/src/lexer.ts"]}, parent="agent_1"))
    m.push(tool_result("agent_1", "Comments are handled in src/lexer.ts"))
    items.append(m.tools["agent_1"])
    items.append(make("WebFetch", {"url": "https://example.com", "prompt": "What is this?"}, "A **placeholder** site."))
    items.append(make("mcp__github__list_issues", {"repo": "a/b"}, "#1 bug"))
    items.append(
        make(
            "Write",
            {"file_path": "/proj/notes.md", "content": "# Notes\n\n- one\n- two"},
            "ok",
            structured={"type": "create"},
        )
    )
    running = make("Bash", {"command": "sleep 10"})
    items.append(running)
    ask_item = make("AskUserQuestion", ASK_INPUT)
    ask_perm = {"requestId": "req-ask", "toolName": "AskUserQuestion", "input": ASK_INPUT, "suggestions": []}
    plan_item = make("ExitPlanMode", {"plan": "# Plan\n\n1. Fix the lexer\n2. Add a test for `//` comments"})
    plan_perm = {"requestId": "req-plan", "toolName": "ExitPlanMode", "input": plan_item.input, "suggestions": []}
    bash_wait = make("Bash", {"command": "npm run build"})
    bash_perm = perm_bash("req-build")
    pending = {bash_wait.id: bash_perm, ask_item.id: ask_perm, plan_item.id: plan_perm}
    c = ctx(pending)
    scene = Scene()
    view = CanvasView(scene)
    view.setObjectName("canvas")
    view.resize(760, 2600)
    blocks: List[Any] = [block(it, c) for it in items]
    blocks.append(block(bash_wait, c, bash_perm))
    blocks.append(SpecialIsland(ask_item, c, ask_perm))
    blocks.append(SpecialIsland(plan_item, c, plan_perm))
    blocks.append(OrphanIsland(perm_bash("req-orphan"), c))
    view.begin()
    try:
        for b in blocks:
            scene.root.append(b)
            if hasattr(b, "ensure_widget"):
                view.register_island(b)
    finally:
        view.end()
    for b in blocks[:8]:
        if isinstance(b, Group):
            view.toggle_group(b, True, animate=False)
    view.show()
    pump(200)
    assert all(b.warm for b in view.visible_blocks() if not isinstance(b, Group)), "every card on screen is measured"
    assert view.cold_paints == 0, "nothing was ever painted as a placeholder"
    assert scene.total > 1500
    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "qt-tool-cards.png"
    view.viewport().grab().save(str(path))
    assert path.exists()
    view.close()
