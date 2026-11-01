"""The cards that wait on the person — a permission, a question, a plan — against the mock server: one
grammar (the accent dot in the caption, the ring, the key boxes), and the number keys answer them."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5434


def _send(c, sid: str, text: str) -> None:
    c.call("session.send", {"id": sid, "text": text})
    c.wait_until(lambda: c.store.rt[sid].status == "requires_action", 15, f"waiting after {text!r}")
    c.sleep(300)


def test_question_and_plan_cards_share_the_grammar_and_the_keys(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt.widgets.transcript.bodies.ask import AskBody
    from pilcrow_qt.widgets.transcript.bodies.common import NeedsDot
    from pilcrow_qt.widgets.transcript.bodies.plan import PlanBody

    c = app_client
    c.wait_until(lambda: c.store.config is not None and c.store.sessions, 10, "sessions")
    c.call("mock.pace", {"ms": 0})
    sid = c.store.sessions[0]["id"]
    c.open(sid)
    v = c.win.session_view
    # ---- a question: the caption opens with the accent dot, the options carry key boxes, 1 picks the first
    _send(c, sid, "ask me a question")
    ask = next(w for w in c.find(AskBody) if w.live and w.isVisible())
    assert any(isinstance(w, NeedsDot) for w in c.find(NeedsDot, ask)), "the caption's dot"
    assert ask.keyed and [r.label for r in ask.rows[0]] == ["Compact", "Verbose"]
    assert not ask.send_btn.isEnabled()
    # the composer holds focus by default, as in the real window; an empty composer is not "typing"
    v.composer.edit.setFocus()
    c.win.activateWindow()
    QTest.keyClick(c.win, Qt.Key_1)
    from PySide6.QtWidgets import QApplication

    c.wait_until(lambda: ask.sel.get(0) == ["Compact"], 3, f"1 picked Compact (focus {QApplication.focusWidget()!r})")
    assert ask.send_btn.isEnabled()
    assert v.pill.text() == "Needs you"
    ask.submit()
    c.wait_until(lambda: c.store.rt[sid].status == "idle", 15, "answered")
    # the record (inside the turn's closed fold): the answer travelled with the tool's result
    c.sleep(200)
    asked = next(it for it in c.store.models[sid].items if it.kind == "tool" and it.name == "AskUserQuestion")
    assert (asked.structured or {}).get("answers") == {"Which style?": "Compact"}
    # ---- a plan: the same caption and dot, the same key boxes, 1 approves
    _send(c, sid, "propose a plan")
    plan = next(w for w in c.find(PlanBody) if w.live and w.isVisible())
    assert any(isinstance(w, NeedsDot) for w in c.find(NeedsDot, plan)), "the caption's dot"
    assert plan.approve_btn.override["h"] == 28 == ask.send_btn.override["h"], "one button metric for every card"
    assert c.shot("islands-plan").exists()
    QTest.keyClick(c.win, Qt.Key_1)
    c.wait_until(lambda: c.store.rt[sid].status == "idle", 15, "approved")
    c.sleep(200)
    planned = next(it for it in c.store.models[sid].items if it.kind == "tool" and it.name == "ExitPlanMode")
    assert planned.status == "done" and planned.result is not None and not planned.result.is_error
    # the fold line names what happened in the person's words
    labels = [it.text for it in c.store.models[sid].items if it.kind == "text"]
    assert "Starting on the lexer." in labels
