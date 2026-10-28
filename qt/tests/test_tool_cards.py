"""Tool cards and the permission prompt, built from raw messages through the transcript model and
drawn offscreen (no server): head text, bodies, decisions, keys, and a screenshot of every kind."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
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


def card(item: ToolItem, c: Optional[ToolCtx] = None, perm: Optional[dict] = None):
    from pilcrow_qt.widgets.transcript.tool_card import ToolCard

    w = ToolCard(item, c or ctx(), perm)
    w.show()
    QCoreApplication.processEvents()
    return w


def pump(ms: int = 30) -> None:
    import time

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()


def labels(w: QWidget) -> List[str]:
    return [lb.text() for lb in w.findChildren(QLabel) if lb.text()]


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
    w = card(item)
    assert w.head.verb.text() == "Read"
    assert w.head.target.text() == "src/a.ts"
    assert w.head.target._mono is True
    assert not w.is_open  # a finished read folds to one line
    w.toggle()
    pump()
    from pilcrow_qt.widgets.transcript.bodies.files import ReadBody

    body = w.findChild(ReadBody)
    assert body is not None and body.link.text() == "src/a.ts"
    assert body.code.lines == ["const a = 1;", "const b = 2;", "export { a, b };"]
    html = body.code.html()
    assert "const" in html and ">3<" in html


def test_read_strips_line_numbers_from_plain_results(app):
    item = make("Read", {"file_path": "/proj/x.py"}, "     5→def f():\n     6→    pass")
    w = card(item)
    w.toggle()
    from pilcrow_qt.widgets.transcript.bodies.files import ReadBody

    body = w.findChild(ReadBody)
    assert body.code.start_line == 5
    assert body.code.lines == ["def f():", "    pass"]


def test_edit_diff_has_add_and_del_rows(app):
    old = "a\nb\nc\nd\ne\nf\ng\n"
    new = "a\nb\nc\nD\ne\nf\ng\nh\n"
    item = make(
        "Edit",
        {"file_path": "/proj/src/a.ts", "old_string": old, "new_string": new},
        "ok",
        structured={"filePath": "/proj/src/a.ts"},
    )
    w = card(item)
    assert "+2" in labels(w.head) and "−1" in labels(w.head)
    w.toggle()
    pump()
    assert w.diff_body().diff is not None
    kinds = [ln.t for ln in w.diff_body().diff.hunks[0].lines]
    assert kinds.count("+") == 2 and kinds.count("-") == 1
    t = theme.tokens()
    html = w.diff_body().diff.html()
    assert theme.blend(t.diff_add, t.inset).name() in html
    assert theme.blend(t.diff_del, t.inset).name() in html


def test_edit_structured_patch_is_numbered(app):
    patch = [{"oldStart": 10, "oldLines": 2, "newStart": 10, "newLines": 2, "lines": [" x", "-y", "+z"]}]
    item = make(
        "Edit",
        {"file_path": "/proj/f.py", "old_string": "y", "new_string": "z"},
        "ok",
        structured={"structuredPatch": patch},
    )
    w = card(item)
    w.toggle()
    d = w.diff_body().diff
    assert d.numbers is True
    assert [(ln.t, ln.o, ln.n) for ln in d.hunks[0].lines] == [(" ", 10, 10), ("-", 11, None), ("+", None, 11)]
    assert "unchanged line" in d.html()


def test_bash_card_renders_ansi_output(app):
    item = make(
        "Bash",
        {"command": "npm test", "description": "Run the tests"},
        "\x1b[32mPASS\x1b[0m 3 tests\n\x1b[1mdone\x1b[0m",
    )
    w = card(item)
    assert w.head.verb.text() == "" and w.head.target.text() == "Run the tests"
    w.toggle()
    pump()
    from pilcrow_qt.widgets.transcript.bodies.bash import BashBody

    body = w.findChild(BashBody)
    html = body.out_label.text()
    assert theme.tokens().ansi_green in html and "PASS" in html and "font-weight:600" in html
    assert "npm test" in body.cmd_label.text()


def test_bash_exit_code_and_tail(app):
    out = "\n".join(f"line {i}" for i in range(40))
    item = make("Bash", {"command": "ls"}, "Exit code 2\n" + out, is_error=True)
    w = card(item)
    assert w.is_open  # errors open by default
    from pilcrow_qt.widgets.transcript.bodies.bash import BashBody

    body = w.findChild(BashBody)
    assert body.exit_code == "2"
    assert "line 39" in body.out_label.text() and "line 0" not in body.out_label.text()
    assert "Failed" in labels(w.head)


def test_denied_card_status(app):
    item = make("Bash", {"command": "rm -rf /"}, "The user doesn't want to proceed with this tool use.", is_error=True)
    assert item.status == "denied"
    w = card(item)
    assert "declined" in labels(w.head)
    assert w.head.target._strike is True and w.head.target._color == "text_3"
    from pilcrow_qt.widgets.transcript.tool_card import IconLabel

    ico = w.head.ico_host.findChild(IconLabel)
    assert ico is not None and ico._name == "ban" and ico._color == "err"


def test_write_and_todo_bodies(app):
    w = card(
        make("Write", {"file_path": "/proj/new.md", "content": "# Hi\n\ntext"}, "ok", structured={"type": "create"})
    )
    assert "+3" in labels(w.head)
    todos = [
        {"content": "Read", "status": "completed"},
        {"content": "Fix it", "activeForm": "Fixing it", "status": "in_progress"},
        {"content": "Test", "status": "pending"},
    ]
    t = card(make("TodoWrite", {"todos": todos}, "ok"))
    assert t.head.target.text() == "1/3 done · Fixing it"
    t.toggle()
    from pilcrow_qt.widgets.transcript.bodies.todo import TodoBody

    body = t.findChild(TodoBody)
    assert [x for x in labels(body)] == ["Read", "Fixing it", "Test"]


def test_search_body_lists_files(app):
    item = make(
        "Grep",
        {"pattern": "foo", "path": "/proj"},
        "x",
        structured={"filenames": ["/proj/a.ts", "/proj/src/b.ts"], "numFiles": 2},
    )
    w = card(item)
    w.toggle()
    from pilcrow_qt.widgets.transcript.bodies.search import SearchBody

    body = w.findChild(SearchBody)
    assert body.files == ["/proj/a.ts", "/proj/src/b.ts"]
    assert "2 files" in labels(body) and "src/b.ts" in labels(body)


def test_agent_card_with_nested_children(app):
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
    w = card(agent)
    assert w.head.verb.text() == "Agent · Explore" and w.head.target.text() == "look around"
    w.toggle()
    pump()
    from pilcrow_qt.widgets.transcript.bodies.agent import AgentBody
    from pilcrow_qt.widgets.transcript.tool_card import ToolCard

    body = w.findChild(AgentBody)
    assert body is not None and len(body.children) == 2
    nested = body.children_host.findChildren(ToolCard)
    assert [c.item.name for c in nested] == ["Grep"]
    assert nested[0].ctx.depth == 1
    assert "Explore" in labels(body)


def test_generic_and_mcp_body(app):
    item = make("mcp__github__list_issues", {"repo": "a/b", "state": "open"}, "#1 bug\n#2 feature")
    w = card(item)
    assert w.head.verb.text() == "Github · list issues"
    w.toggle()
    from pilcrow_qt.widgets.transcript.bodies.generic import GenericBody

    body = w.findChild(GenericBody)
    texts = labels(body)
    assert "Input" in texts and "Output" in texts
    assert any("repo" in t and "a/b" in t for t in texts) and any("#2 feature" in t for t in texts)


def test_refresh_keeps_fold_and_skips_same_rev(app):
    item = make("Read", {"file_path": "/proj/a.ts"}, "x")
    w = card(item)
    w.toggle()
    assert w.is_open
    before = w.body.body()
    w.refresh(item.rev)
    assert w.body.body() is before
    item.rev += 1
    w.refresh(item.rev)
    assert w.is_open and w.body.is_open


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


def test_ask_body_answers_through_respond(app, respond):
    item = make("AskUserQuestion", ASK_INPUT)
    perm = {
        "requestId": "req-ask",
        "toolName": "AskUserQuestion",
        "input": ASK_INPUT,
        "suggestions": [],
        "interactive": True,
        "at": 0,
    }
    w = card(item, ctx({item.id: perm}))
    from pilcrow_qt.widgets.transcript.bodies.ask import AskBody

    body = w.findChild(AskBody)
    assert body is not None and body.live
    assert "Claude has 2 questions" in labels(body)
    assert not body.send_btn.isEnabled()
    body.rows[0][0].toggled.emit()  # React
    body.rows[1][0].toggled.emit()  # ESLint
    body.rows[1][1].toggled.emit()  # Prettier
    body.other_fields[1].setText("Biome")
    body.note_fields[0].setText("keep it simple")
    assert body.send_btn.isEnabled()
    body.send_btn.click()
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


def test_ask_single_select_other_clears_choice(app, respond):
    item = make("AskUserQuestion", {"questions": ASK_INPUT["questions"][:1]})
    perm = {
        "requestId": "r2",
        "toolName": "AskUserQuestion",
        "input": {"questions": ASK_INPUT["questions"][:1]},
        "suggestions": [],
    }
    w = card(item, ctx({item.id: perm}))
    from pilcrow_qt.widgets.transcript.bodies.ask import AskBody

    body = w.findChild(AskBody)
    body.rows[0][1].toggled.emit()
    body.other_fields[0].setText("Svelte")
    assert body.sel[0] == [] and not body.rows[0][1].on
    body.skip_btn.click()
    assert respond[-1][2]["behavior"] == "deny"


def test_ask_done_shows_answers(app):
    item = make("AskUserQuestion", ASK_INPUT, "ok", structured={"answers": {"Which framework?": "React"}})
    w = card(item)
    from pilcrow_qt.widgets.transcript.bodies.ask import AskBody

    body = w.findChild(AskBody)
    assert body.answers == ["React", "No answer"]
    assert "Claude asked a few questions" in labels(body)


# ---------------------------------------------------------------- ExitPlanMode


def test_plan_body_approves_and_keeps_planning(app, respond):
    plan = "# Plan\n\n1. Do the thing\n2. Test it"
    item = make("ExitPlanMode", {"plan": plan})
    perm = {"requestId": "req-plan", "toolName": "ExitPlanMode", "input": {"plan": plan}, "suggestions": []}
    w = card(item, ctx({item.id: perm}))
    from pilcrow_qt.widgets.transcript.bodies.plan import PlanBody

    body = w.findChild(PlanBody)
    assert "Plan ready for review" in labels(body)
    body.ask_btn.click()
    assert respond[-1] == (
        "sess",
        "req-plan",
        {
            "behavior": "allow",
            "updatedInput": {"plan": plan},
            "updatedPermissions": [{"type": "setMode", "mode": "default", "destination": "session"}],
        },
    )
    body._set_busy(False)
    body.keep_btn.click()
    assert body.feedback_box.isVisible()
    body.feedback.setPlainText("Add tests first")
    body.send_btn.click()
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


def test_permission_card_in_tool_card_and_keys_only_for_first(app, respond):
    from pilcrow_qt.widgets.transcript.permission import PermissionCard

    perm = perm_bash("req-3", defaultToNo=True)
    item = make("Bash", {"command": "npm test"})
    other = perm_bash("req-0")
    c = ctx({"someone-else": other, item.id: perm})
    w = card(item, c)
    assert w.awaiting and w.is_open
    pc = w.findChild(PermissionCard)
    assert pc is not None and not pc.first and not pc._shortcuts
    assert pc.deny_btn.property("kind") == "primary" and not pc.allow_btn.property("kind")
    pc.allow_btn.click()
    assert respond[-1][1] == "req-3"
    # The answer arrives: the request leaves the context and the card folds, keeping the stale prompt.
    w.refresh(item.rev, ctx({}))
    assert not w.awaiting and not w.perm_collapse.is_open


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
    from pilcrow_qt.widgets.transcript.permission import PermissionCard

    column = QWidget()
    column.setObjectName("canvas")
    lay = QVBoxLayout(column)
    lay.setContentsMargins(24, 16, 24, 16)
    lay.setSpacing(2)
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
    cards = []
    for it in items:
        cards.append(card(it, c))
    for it in (bash_wait, ask_item, plan_item):
        cards.append(card(it, c))
    for w in cards:
        lay.addWidget(w)
    for w in cards[:6]:
        w.set_open(True)
    cards[6].set_open(True)
    cards[7].set_open(True)
    orphan = PermissionCard(perm_bash("req-orphan"), c, item=None)
    lay.addWidget(orphan)
    lay.addStretch(1)
    scroll = QScrollArea()
    scroll.setObjectName("canvas")
    scroll.setWidgetResizable(True)
    scroll.setWidget(column)
    scroll.resize(760, 2400)
    scroll.show()
    pump(200)
    column.resize(740, max(2400, column.sizeHint().height()))
    pump(200)
    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "qt-tool-cards.png"
    column.grab().save(str(path))
    assert path.exists()
    scroll.close()
