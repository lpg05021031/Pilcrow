"""The conversation view against the mock server: rows and folds, a live turn, paging of a long
conversation, following and the "Latest" pill, find."""

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
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from pilcrow_qt.widgets.transcript.view import Transcript

    c.open(sid)
    sv = getattr(c.win, "session_view", None)
    tr = getattr(sv, "transcript", None)
    if isinstance(tr, Transcript) and tr.sid == sid:
        c.sleep(400)
        return c.win, tr
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


def _cards(c, root):
    from pilcrow_qt.widgets.transcript.folds import FallbackToolCard

    out = [w for w in c.find(FallbackToolCard, root)]
    try:
        from pilcrow_qt.widgets.transcript.tool_card import ToolCard

        out += [w for w in c.find(ToolCard, root)]
    except ImportError:
        pass
    return [w for w in out if w.isVisible()]


def test_login_shows_bubbles_text_and_closed_folds(app_client):
    c = app_client
    from pilcrow_qt.widgets.transcript.folds import WorkFold
    from pilcrow_qt.widgets.transcript.items import AssistantText, UserMessage

    win, tr = _mount(c, "sess-login")
    try:
        bubbles = c.find(UserMessage, tr)
        assert len(bubbles) == 3, len(bubbles)
        assert bubbles[0].item.text.startswith("The login page redirects")
        texts = [w.item.text for w in c.find(AssistantText, tr)]
        assert any("hidden field" in t for t in texts), texts
        folds = c.find(WorkFold, tr)
        assert len(folds) == 2, len(folds)  # the two turns with tools; the last turn only spoke
        assert all(not f.body.is_open for f in folds)
        assert folds[0].sum.text() == "Read 2 files, searched once", folds[0].sum.text()
        assert folds[1].sum.text() == "Edited a file, ran a command", folds[1].sum.text()
        assert folds[0].n.text() == "3 steps"
        assert not _cards(c, tr)  # closed folds draw no tool cards
        # the column is centred and is the golden section of the pane plus its 24px paddings (app.css --col)
        from pilcrow_qt.widgets.transcript.view import column_width

        assert tr.inner.width() == column_width(tr.width()) + 48 and tr.inner.x() > 0
        # thinking/verbose open every fold; normal closes them again (a remembered toggle would win)
        c.store.set_ui(view="verbose")
        c.sleep(300)
        folds = c.find(WorkFold, tr)
        assert len(folds) == 2 and all(f.body.is_open for f in folds)
        assert len(_cards(c, tr)) == 5, len(_cards(c, tr))  # verbose: every step is its own card
        c.store.set_ui(view="normal")
        c.sleep(300)
        folds = c.find(WorkFold, tr)
        assert len(folds) == 2 and all(not f.body.is_open for f in folds)
        # a fold opens on click and shows the steps as an "Explored" line (quiet reads fold) or cards
        folds[0].toggle()
        c.sleep(200)
        assert folds[0].body.is_open
        from pilcrow_qt.widgets.transcript.folds import Explore

        assert c.find(Explore, folds[0]), "quiet finished steps fold into one Explored line"
        folds[1].toggle()
        c.sleep(200)
        assert _cards(c, folds[1]), "an edit and a command stay as their own cards"
        _shot(c, win, "transcript-open-fold")
        folds[1].toggle()
        folds[0].toggle()
        c.sleep(100)
        assert not folds[0].body.is_open
        _shot(c, win, "transcript-login")
    finally:
        if win is not c.win:
            win.close()


def test_turn_shows_live_fold_then_done(app_client):
    c = app_client
    from pilcrow_qt.widgets.transcript.folds import WorkFold
    from pilcrow_qt.widgets.transcript.items import AssistantText, UserMessage

    win, tr = _mount(c, "sess-empty")
    try:
        assert not c.find(UserMessage, tr)
        c.call("mock.pace", {"ms": 0})
        c.store.send_message("sess-empty", "please look at the parser and fix the comment")
        c.wait_until(lambda: c.find(UserMessage, tr), 10, "the user message")
        assert c.find(UserMessage, tr)[0].item.text == "please look at the parser and fix the comment"
        c.wait_until(lambda: any(f.unit.live for f in c.find(WorkFold, tr)), 10, "a live fold")
        live = [f for f in c.find(WorkFold, tr) if f.unit.live][0]
        assert live.mark.isVisible(), "the live fold carries the breathing mark"
        assert not tr.working.isVisible(), "so the working line stays out of the way"
        _shot(c, win, "transcript-live")
        c.wait_until(lambda: c.store.rt["sess-empty"].status == "idle", 20, "the turn to finish")
        c.sleep(300)
        texts = [w for w in c.find(AssistantText, tr)]
        assert texts and "Done" in texts[-1].item.text, [t.item.text for t in texts]
        folds = c.find(WorkFold, tr)
        assert folds and all(not f.unit.live and not f.body.is_open for f in folds)
        assert all(not f.mark.isVisible() for f in folds)
        assert not tr.working.isVisible()
        vb = tr.verticalScrollBar()
        assert vb.value() == vb.maximum()
        _shot(c, win, "transcript-turn")
    finally:
        if win is not c.win:
            win.close()


def test_long_opens_at_bottom_with_placeholder_pages(app_client):
    c = app_client
    from pilcrow_qt.widgets.transcript.items import UserMessage

    win, tr = _mount(c, "sess-long")
    try:
        c.sleep(500)
        pages = tr._pages
        assert len(pages) >= 10, len(pages)
        far = [p for p in pages if p.far]
        drawn = [p for p in pages if not p.far]
        assert far and drawn, (len(far), len(drawn))
        assert len(far) > len(pages) // 2, "most of the history is a placeholder"
        assert all(p.height() == p.est for p in far)
        assert not pages[-1].far
        vb = tr.verticalScrollBar()
        assert vb.value() == vb.maximum() and vb.maximum() > 2000
        assert not tr.jump.isVisible()
        bubbles = [b for b in c.find(UserMessage, tr) if b.isVisible()]
        assert bubbles and bubbles[-1].item.text.startswith("Turn 120")
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
        c.sleep(300)
        assert not tr._at_bottom
        assert abs(vb.value() - target) < 400, (vb.value(), target)  # pages near it drew; the view stayed put
        # pages around the new spot were drawn as the reader came near
        assert any(not p.far for p in tr._pages[: len(tr._pages) // 2])
        # new output while up shows "Latest"
        c.store.get_model("sess-long").rev += 1
        c.store.rt["sess-long"].rev += 1
        c.store.rt_changed.emit("sess-long")
        c.sleep(50)
        assert tr.jump.isVisible()
        _shot(c, win, "transcript-latest")
        c.store.sent.emit("sess-long")
        c.wait_until(lambda: vb.value() == vb.maximum(), 5, "back at the bottom")
        assert tr._at_bottom and not tr.jump.isVisible()
    finally:
        if win is not c.win:
            win.close()


def test_scroll_position_is_remembered_per_session(app_client):
    c = app_client
    win, tr = _mount(c, "sess-long")
    try:
        vb = tr.verticalScrollBar()
        vb.setValue(vb.maximum() // 3)
        c.sleep(400)
        index = tr.first_visible_unit()
        assert index > 0
        win2, tr2 = _mount(c, "sess-login")
        assert tr2 is not tr and tr2.verticalScrollBar().value() == tr2.verticalScrollBar().maximum()
        win3, tr3 = _mount(c, "sess-long")
        c.sleep(400)
        assert tr3 is not tr
        assert not tr3._at_bottom and tr3.jump.isVisible()
        assert abs(tr3.first_visible_unit() - index) <= 1, (tr3.first_visible_unit(), index)
    finally:
        for w in {win, win2, win3} if "win3" in locals() else {win}:
            if w is not c.win:
                w.close()


def test_find_counts_and_reveals(app_client):
    c = app_client
    from pilcrow_qt.widgets.transcript.find_bar import FindBar

    win, tr = _mount(c, "sess-long")
    try:
        bar = FindBar(tr)
        bar.field.setText("module 7")
        c.sleep(100)
        n = len(bar.matches)
        assert n >= 2, n
        assert bar.count.text().endswith(f"of {n}"), bar.count.text()
        assert all(not p.far for p in tr._pages), "find draws every page"
        bar.step(1)
        c.sleep(200)
        # the words themselves are lit in prose rows (::highlight(find)); other rows get the wash
        lit = [v for v in bar._lit if v.extraSelections()]
        assert lit or bar.highlight.isVisible()
        if lit:
            assert any(s.format.background().color().name() == "#d97757" for v in lit for s in v.extraSelections()), (
                "the current match is lit in full"
            )
        row = bar._row
        assert row is not None and row.isVisible()
        vb = tr.verticalScrollBar()
        y = row.mapTo(tr.widget(), row.rect().topLeft()).y()
        assert vb.value() - 10 <= y <= vb.value() + tr.viewport().height(), (y, vb.value())
        bar.field.setText("zzz-nothing-here")
        c.sleep(50)
        assert bar.count.text() == "No matches"
        bar.close_bar()
        _shot(c, win, "transcript")
    finally:
        if win is not c.win:
            win.close()
