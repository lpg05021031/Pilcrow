"""The conversation view against the mock server: rows and folds, a live turn, a long conversation
that opens at its end, following and the "Latest" pill, the remembered place, find."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5341
MOCK_LONG = 120

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _socket_settled():
    """The previous test's socket must have finished closing before the next client queues its
    ``mock.reset``: a late close would reject that call and the fixture with it."""
    from PySide6.QtWidgets import QApplication

    from pilcrow_qt.rpc import rpc

    app = QApplication.instance()
    start = time.monotonic()
    aborted = False
    while app is not None and rpc._ws is not None and time.monotonic() - start < 8:  # noqa: SLF001
        if not aborted and time.monotonic() - start > 1.5:
            rpc._ws.abort()  # the close handshake is taking its time: drop the socket outright  # noqa: SLF001
            aborted = True
        app.processEvents()
        time.sleep(0.01)
    yield


def _mount(c, sid: str):
    """Open a session and reach its Transcript: the one in the real session view when that hosts it,
    otherwise one built in a plain window (the session view may still be a placeholder)."""
    from pilcrow_qt.canvas.transcript import Transcript

    c.open(sid)
    sv = getattr(c.win, "session_view", None)
    tr = getattr(sv, "transcript", None)
    if isinstance(tr, Transcript) and tr.sid == sid:
        c.sleep(400)
        return c.win, tr
    return _plain(c, sid)


def _plain(c, sid: str):
    """A Transcript of its own in a plain window, as another place in the app would host it."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from pilcrow_qt.canvas.transcript import Transcript

    c.open(sid)
    cwd = str((c.store.session(sid) or {}).get("cwd") or "")
    win = QWidget()
    win.setObjectName("canvas")
    win.resize(1000, 900)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    tr = Transcript(sid, cwd)
    lay.addWidget(tr)
    win.show()
    c.sleep(400)
    return win, tr


def _shot(c, win, name: str) -> Path:
    if win is c.win:
        return c.shot(name)
    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"qt-{name}.png"
    win.grab().save(str(path))
    return path


def _blocks(tr, cls, shown: bool = False):
    """The blocks of a class in the scene, in reading order; ``shown`` keeps only those a person can
    see once they scroll there (not inside a closed fold)."""
    return [b for b in tr.scene.root.walk() if isinstance(b, cls) and (not shown or b.visible_in_tree())]


def _toggle(c, tr, group) -> None:
    from pilcrow_qt.canvas.scene import Target

    tr.view.activate(group, Target("head", group.KIND, (0, 0, 0, 0)))
    c.sleep(500)


def test_login_shows_bubbles_text_and_closed_folds(app_client):
    c = app_client
    from pilcrow_qt.canvas.blocks.fold import Explore, FoldGroup
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.blocks.tool import ToolBlock
    from pilcrow_qt.canvas.blocks.user import UserBlock
    from pilcrow_qt.canvas.view import column_width

    win, tr = _mount(c, "sess-login")
    try:
        bubbles = _blocks(tr, UserBlock)
        assert len(bubbles) == 3, len(bubbles)
        assert bubbles[0].item.text.startswith("The login page redirects")
        texts = [g.item.text for g in _blocks(tr, TextGroup)]
        assert any("hidden field" in t for t in texts), texts
        folds = _blocks(tr, FoldGroup)
        assert len(folds) == 2, len(folds)  # the two turns with tools; the last turn only spoke
        assert all(not f.open for f in folds)
        assert folds[0].summary.text == "Read 2 files, searched once", folds[0].summary.text
        assert folds[1].summary.text == "Edited a file, ran a command", folds[1].summary.text
        assert folds[0].summary.steps == 3
        assert not _blocks(tr, ToolBlock, shown=True)  # closed folds show no tool cards
        # the column is centred and is the golden section of the pane (app.css --col)
        assert tr.view.col_w == column_width(tr.view.viewport().width()) and tr.view.col_x > 0
        # thinking/verbose open every fold; normal closes them again (a remembered toggle would win)
        c.store.set_ui(view="verbose")
        c.sleep(300)
        folds = _blocks(tr, FoldGroup)
        assert len(folds) == 2 and all(f.open for f in folds)
        cards = _blocks(tr, ToolBlock, shown=True)
        assert len(cards) == 5, len(cards)  # verbose: every step is its own card
        c.store.set_ui(view="normal")
        c.sleep(300)
        folds = _blocks(tr, FoldGroup)
        assert len(folds) == 2 and all(not f.open for f in folds)
        # a fold opens on click and shows the steps as an "Explored" line (quiet reads fold) or cards
        _toggle(c, tr, folds[0])
        assert folds[0].open and folds[0].reveal == 1.0
        assert [b for b in folds[0].walk() if isinstance(b, Explore)], (
            "quiet finished steps fold into one Explored line"
        )
        _toggle(c, tr, folds[1])
        assert [b for b in folds[1].walk() if isinstance(b, ToolBlock)], "an edit and a command stay as their own cards"
        assert all(b.warm for b in tr.view.visible_blocks() if not hasattr(b, "children")), "what shows is measured"
        _shot(c, win, "transcript-open-fold")
        _toggle(c, tr, folds[1])
        _toggle(c, tr, folds[0])
        assert not folds[0].open and folds[0].h == folds[0].HEAD
        _shot(c, win, "transcript-login")
    finally:
        if win is not c.win:
            win.close()


def test_turn_shows_live_fold_then_done(app_client):
    c = app_client
    from pilcrow_qt.canvas.blocks.fold import FoldGroup
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.blocks.user import UserBlock

    win, tr = _mount(c, "sess-empty")
    try:
        assert not _blocks(tr, UserBlock)
        c.call("mock.pace", {"ms": 0})
        c.store.send_message("sess-empty", "please look at the parser and fix the comment")
        c.wait_until(lambda: _blocks(tr, UserBlock), 10, "the user message")
        assert _blocks(tr, UserBlock)[0].item.text == "please look at the parser and fix the comment"
        c.wait_until(lambda: any(f.unit.live for f in _blocks(tr, FoldGroup)), 10, "a live fold")
        live = [f for f in _blocks(tr, FoldGroup) if f.unit.live][0]
        assert live.live(), "the live fold carries the breathing mark"
        assert not tr.scene.working.shown, "so the working line stays out of the way"
        _shot(c, win, "transcript-live")
        c.wait_until(lambda: c.store.rt["sess-empty"].status == "idle", 20, "the turn to finish")
        c.sleep(600)
        texts = _blocks(tr, TextGroup)
        assert texts and "Done" in texts[-1].item.text, [t.item.text for t in texts]
        folds = _blocks(tr, FoldGroup)
        assert folds and all(not f.unit.live and not f.open for f in folds)
        assert not tr.scene.working.shown and tr.scene.working.h == 0
        vb = tr.verticalScrollBar()
        c.wait_until(lambda: vb.value() == vb.maximum(), 5, "the view at the end")
        assert tr.view.follow.following
        _shot(c, win, "transcript-turn")
    finally:
        if win is not c.win:
            win.close()


def test_long_opens_at_bottom_with_most_of_the_history_cold(app_client):
    c = app_client
    from pilcrow_qt.canvas.blocks.user import UserBlock
    from pilcrow_qt.canvas.scene import GRADE_EXACT

    win, tr = _mount(c, "sess-long")
    try:
        c.sleep(500)
        vb = tr.verticalScrollBar()
        assert vb.value() == vb.maximum() and vb.maximum() > 2000
        assert not tr.jump.isVisible()
        bubbles = [b for b in tr.view.visible_blocks() if isinstance(b, UserBlock)]
        assert bubbles and bubbles[-1].item.text.startswith("Turn 120")
        leaves = [b for b in tr.scene.root.walk() if not hasattr(b, "children")]
        exact = [b for b in leaves if b.grade >= GRADE_EXACT]
        assert len(leaves) > 200 and len(exact) < len(leaves) // 2, (len(leaves), len(exact))
        assert all(b.warm for b in tr.view.visible_blocks() if not hasattr(b, "children")), "what shows is exact"
        _shot(c, win, "transcript-long")
    finally:
        if win is not c.win:
            win.close()


def test_scrolling_up_stops_following_and_sent_jumps_back(app_client):
    c = app_client
    win, tr = _mount(c, "sess-long")
    try:
        c.sleep(300)
        vb = tr.verticalScrollBar()
        assert vb.value() == vb.maximum()
        target = vb.maximum() // 2
        vb.setValue(target)
        c.sleep(400)
        assert not tr.view.follow.following
        assert abs(vb.value() - target) < 400, (vb.value(), target)  # rows near it were measured; the view stayed put
        assert all(b.warm for b in tr.view.visible_blocks() if not hasattr(b, "children"))
        # new output while up shows "Latest"
        model = c.store.get_model("sess-long")
        model.push(
            {
                "type": "assistant",
                "uuid": "uu-late",
                "message": {
                    "id": "m-late",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "One more thing."}],
                },
            }
        )
        c.store.rt["sess-long"].rev += 1
        c.store.rt_changed.emit("sess-long")
        c.sleep(100)
        assert tr.jump.isVisible()
        assert abs(vb.value() - target) < 400, "the new row below did not move what the reader sees"
        _shot(c, win, "transcript-latest")
        c.store.sent.emit("sess-long")
        c.wait_until(lambda: vb.value() == vb.maximum(), 5, "back at the bottom")
        assert tr.view.follow.following and not tr.jump.isVisible()
    finally:
        if win is not c.win:
            win.close()


def test_scroll_position_is_remembered_per_session(app_client):
    c = app_client
    win, tr = _mount(c, "sess-long")
    wins = [win]
    try:
        vb = tr.verticalScrollBar()
        vb.setValue(vb.maximum() // 3)
        c.sleep(400)
        index = tr.first_visible_unit()
        assert index > 0
        # another conversation takes the window; the first one's place is kept with its scene
        win2, tr2 = _mount(c, "sess-login")
        wins.append(win2)
        assert tr2 is not tr and tr2.verticalScrollBar().value() == tr2.verticalScrollBar().maximum()
        assert tr.scene.saved is not None and not tr.scene.saved_following
        # a new view of the first conversation opens where the reader left it
        win3, tr3 = _plain(c, "sess-long")
        wins.append(win3)
        c.sleep(400)
        assert tr3 is not tr and tr3.scene is tr.scene, "the scene of a session is kept; the widget is new"
        assert not tr3.view.follow.following and tr3.jump.isVisible()
        assert abs(tr3.first_visible_unit() - index) <= 1, (tr3.first_visible_unit(), index)
    finally:
        for w in wins:
            if w is not c.win:
                w.close()


def test_find_counts_and_reveals(app_client):
    c = app_client
    from pilcrow_qt.canvas.find_bar import FindBar

    win, tr = _mount(c, "sess-long")
    try:
        bar = FindBar(tr)
        bar.field.setText("module 7")
        c.sleep(100)
        n = len(bar.matches)
        assert n >= 2, n
        assert bar.count.text().endswith(f"of {n}"), bar.count.text()
        bar.step(1)
        c.sleep(300)
        # the words are lit in the row that holds the current match, and that row is on screen
        assert tr.view.needle == "module 7" and tr.view.needle_key
        blk = tr.scene.block(tr.view.needle_key)
        assert blk is not None and blk.visible_in_tree() and blk.warm
        r = tr.block_rect(blk)
        assert -10 <= r.top() <= tr.viewport().height(), (r.top(), tr.viewport().height())
        bar.field.setText("zzz-nothing-here")
        c.sleep(50)
        assert bar.count.text() == "No matches"
        bar.close_bar()
        assert tr.view.needle == ""
        _shot(c, win, "transcript")
    finally:
        if win is not c.win:
            win.close()
