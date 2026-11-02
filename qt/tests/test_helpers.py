"""Helpers (HELPERS.md) against the mock server: a lead starts two helpers from one prompt; the bar and
the start cards follow; the sidebar nests the helpers under the lead; the control room says who asked
and answers for every helper; the handoffs come back as notes; the Changes panel shows each worktree and
finishes them; the inbox gets the record; deleting the lead can take its helpers along."""

from __future__ import annotations

import time

from harness import app_client, mock_server
from PySide6.QtCore import QPoint

HOME = "/home/tester"


def _items(c, sid):
    mdl = c.store.models.get(sid)
    return list(mdl.items) if mdl is not None else []


def _helpers(c, sid):
    rt = c.store.rt.get(sid)
    return list((rt.st.get("helpers") if rt else None) or [])


def test_a_lead_and_its_helpers(app_client):
    from pilcrow_qt.widgets.overview import NeedsCard
    from pilcrow_qt.widgets.panels.helpers_changes import HelpersChanges

    c = app_client
    c.wait_until(lambda: c.store.config is not None and c.store.sessions, 10, "sessions")
    c.call("mock.pace", {"ms": 0})
    sid = c.call("session.create", {"cwd": f"{HOME}/code/pilcrow", "title": "Invoice download"})["id"]
    c.open(sid)
    v = c.win.session_view
    assert not v.helpers_bar.isVisible()
    c.call("session.send", {"id": sid, "text": "Start helpers for the api and the web."})
    # the bar: a chip per helper, in the order they were started
    c.wait_until(lambda: v.helpers_bar.isVisible() and len(v.helpers_bar.chips) == 2, 10, "the helpers bar")
    assert [ch.h.get("name") for ch in v.helpers_bar.chips] == ["api", "web"]
    # two start cards in the lead's conversation, named by what happened
    c.wait_until(
        lambda: sum(1 for it in _items(c, sid) if it.kind == "tool" and it.name == "mcp__helpers__start") == 2,
        10,
        "start cards",
    )
    # the helpers are sessions of their own, nested under the lead in the sidebar
    hids = {h["name"]: h["id"] for h in _helpers(c, sid)}
    c.wait_until(lambda: all(h in c.win.sidebar._rows for h in hids.values()), 10, "helper rows")
    rows = c.win.sidebar._rows
    lead_left = rows[sid].layout().contentsMargins().left()
    assert all(rows[h].layout().contentsMargins().left() > lead_left for h in hids.values()), (
        "helpers sit under the lead"
    )
    # web waits for api, starts with api's handoff, then asks a permission: the lead is quiet, the pill says Leading
    web = hids["web"]
    c.wait_until(
        lambda: (c.store.rt.get(web) or None) is not None and c.store.rt[web].status == "requires_action",
        15,
        "web asks",
    )
    c.wait_until(lambda: v.pill.text() == "Leading", 5, "the lead's pill")
    web_chip = next(ch for ch in v.helpers_bar.chips if ch.h.get("name") == "web")
    api_chip = next(ch for ch in v.helpers_bar.chips if ch.h.get("name") == "api")
    # the chips follow the lead's state, which reaches the bar on the next frame
    # name · the repository it works in · state (the repository is said when it is not the name)
    c.wait_until(lambda: web_chip.text().startswith("web · web-ui · needs you"), 5, "web's chip")
    c.wait_until(
        lambda: api_chip.h.get("state") == "done" and api_chip.text().startswith("api · api-gateway · done"), 5, "api"
    )
    assert c.shot("helpers-lead-bar").exists()
    # the control room: the card says whose helper asked, and Allow for all helpers answers it
    c.store.show_page("overview")
    c.wait_until(lambda: any(card.sid == web for card in c.find(NeedsCard)), 10, "the needs card")
    card = next(card for card in c.find(NeedsCard) if card.sid == web)
    c.wait_until(lambda: card.all_btn.isVisible(), 5, "Allow for all helpers")
    assert card.all_btn.text() == "Allow for all helpers"
    assert "helper of Invoice download" in card.head.proj.text()
    card.allow_all()
    c.wait_until(lambda: c.store.rt[web].status != "requires_action", 10, "allowed")
    c.store.open_session(sid)
    # both finish; their handoffs come back to the lead as notes, and the lead answers them
    c.wait_until(lambda: all(h.get("state") == "done" for h in _helpers(c, sid)), 15, "both helpers done")
    c.wait_until(lambda: any(it.kind == "user" and it.note == "helper" for it in _items(c, sid)), 15, "a note")
    notes = [it for it in _items(c, sid) if it.kind == "user" and it.note == "helper"]
    assert all("<helper-note name=" in it.text for it in notes)
    c.wait_until(
        lambda: any(it.kind == "text" and it.text.startswith("Noted:") for it in _items(c, sid)),
        15,
        "the lead read the note",
    )
    c.wait_until(lambda: v.helpers_bar.review.isVisible(), 5, "Review on the bar")
    c.wait_until(lambda: c.store.rt[sid].status == "idle", 15, "the lead is quiet again")
    c.sleep(50)
    assert not v.pill.isVisible(), "no helper runs, the lead is idle: no pill"
    assert c.shot("helpers-lead-done").exists()
    # the Changes panel: one section per helper, the finish row; Keep leaves the worktrees
    c.store.set_ui(panel=True, panel_tab="changes")
    c.wait_until(lambda: any(w.isVisible() for w in c.find(HelpersChanges)), 10, "helpers in the Changes panel")
    hc = next(w for w in c.find(HelpersChanges) if w.isVisible())
    c.wait_until(lambda: len(hc.sections) == 2, 5, "two sections")
    assert [s.name.text() for s in hc.sections.values()] == ["api", "web"]
    assert hc.keep_btn.isVisible() and hc.commit_btn.isVisible()
    hc.finish("keep")
    c.wait_until(lambda: all(h.get("finished") == "keep" for h in _helpers(c, sid)), 10, "kept")
    assert c.shot("helpers-changes").exists()
    # the inbox has one record for the lead
    items = c.call("inbox.list")["items"]
    rec = [i for i in items if i.get("kind") == "helpers"]
    assert len(rec) == 1 and rec[0]["leadId"] == sid
    assert [h["name"] for h in rec[0]["helpers"]] == ["api", "web"]
    # a helper's own view says whose it is
    c.open(web)
    hv = c.win.session_view
    c.wait_until(lambda: hv.lead_crumb is not None and hv.lead_crumb.isVisible(), 5, "the helper's crumb")
    assert hv.lead_crumb.text() == "Lead · Invoice download"
    # the lead's brief is a card of its own: the assignment, where the helper works, on the plan's wash
    from pilcrow_qt.canvas.blocks.note import FromLeadBlock

    briefs = [b for b in hv.transcript.view.scene.root.walk() if isinstance(b, FromLeadBlock)]
    assert briefs, "the brief is drawn as the lead's card"
    hd = briefs[0].head()
    assert hd["verb"] == "Brief from the lead" and hd["target"] == "Invoice download"
    assert [m[1] for m in hd["meta"]] == ["web-ui", "pilcrow/web"], hd["meta"]
    # open, the helper stays under its lead in the sidebar (it used to leave for its folder's project)
    c.wait_until(lambda: web in c.win.sidebar._rows, 5, "the helper's row")
    srows = c.win.sidebar._rows
    assert srows[web].layout().contentsMargins().left() > srows[sid].layout().contentsMargins().left()
    order = [w for w in c.win.sidebar.findChildren(type(srows[sid])) if w.isVisible()]
    order.sort(key=lambda w: w.mapTo(c.win.sidebar, QPoint(0, 0)).y())
    i = order.index(srows[sid])
    assert srows[web] in order[i + 1 : i + 3], "right under the lead"
    assert any(it.kind == "user" and it.from_ == "lead" for it in _items(c, web)), "the brief came from the lead"
    assert "Detach from lead" in [i.get("label") for i in hv.menu_items()]
    # deleting the lead offers to take its helpers along
    c.open(sid)
    v = c.win.session_view
    delete = next(i for i in v.menu_items() if i.get("label") == "Delete…")
    delete["on_select"]()
    spec = c.store.dialogs.get("confirm")
    assert (
        spec is not None and spec.checkbox and spec.checkbox["label"] == "Also delete its 2 helpers and their worktrees"
    )
    spec.on_confirm(True)
    c.store.set_dialog("confirm", None)
    c.wait_until(
        lambda: not any(s["id"] in {sid, *hids.values()} for s in c.store.sessions), 10, "lead and helpers gone"
    )


def _helper(k: int, tick: int) -> dict:
    state = "running" if k < 6 else ("needs_action" if k == 6 else "done")
    return {
        "id": f"h{k}",
        "name": f"helper-{k}",
        "cwd": f"{HOME}/code/repo{k}",
        "branch": f"pilcrow/lead/helper-{k}",
        "worktree": f"{HOME}/code/repo{k}/.worktrees/lead",
        "state": state,
        "since": 1,
        "elapsedMs": 1000 * (tick + 3 * k),
        "cost": round(0.013 * k + 0.0001 * tick, 4),
        "files": 3 if state == "done" else 0,
        "add": 40 if state == "done" else 0,
        "del": 12 if state == "done" else 0,
        "pending": 1 if state == "needs_action" else 0,
        "lastLine": f"Read src/mod{(tick + k) % 23}.ts",
        "after": [],
    }


def test_helper_updates_touch_only_the_bar(app_client):
    """Eight helpers ticking (elapsed time, a new last line, the cost) repaint their chips and nothing else:
    the conversation and the sidebar do not paint, no widget outside the bar receives a layout pass, and
    the sessions list is not re-emitted (HELPERS.md §11; qt/PERF.md §4.12). The bar arriving is the one
    layout of the view, and it happens before the count starts."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    c = app_client
    c.wait_until(lambda: c.store.config is not None and c.store.sessions, 10, "sessions")
    sid = c.store.sessions[0]["id"]
    c.open(sid)
    v = c.win.session_view
    cur = c.store.rt[sid].st

    def push(tick: int) -> None:
        c.store._on_state(
            {
                "id": sid,
                "status": cur.get("status"),
                "live": cur.get("live"),
                "pending": cur.get("pending") or [],
                "helpers": [_helper(k, tick) for k in range(8)],
                "lead": {"since": 1, "cost": 0.5 + tick * 0.001, "caps": {}, "rules": []},
            }
        )

    push(0)
    c.wait_until(lambda: v.helpers_bar.isVisible() and len(v.helpers_bar.chips) == 8, 5, "the bar")
    assert v.pill.text() == "Leading"
    # the window's own idle work first: the side panel is built and painted once off screen after a session
    # opens (MainWindow._warm_panel), which lays the view out — not a cost of the helpers
    c.wait_until(lambda: c.win.panel is not None, 5, "the panel prebuilt")
    c.sleep(600)
    viewport = v.transcript.view.viewport()
    bar = v.helpers_bar
    sidebar = c.win.sidebar

    class Count(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.paints = {"viewport": 0, "bar": 0, "sidebar": 0, "composer": 0}
            self.who: dict = {}
            self.layouts: list = []

        def eventFilter(self, obj, e) -> bool:
            t = e.type()
            if t == QEvent.Type.Paint:
                for key, w in (("viewport", viewport), ("bar", bar), ("sidebar", sidebar), ("composer", v.composer)):
                    if obj is w or w.isAncestorOf(obj):
                        self.paints[key] += 1
                        if key != "bar":
                            name = f"{key}/{type(obj).__name__}:{obj.objectName()}"
                            self.who[name] = self.who.get(name, 0) + 1
                        break
            elif t == QEvent.Type.LayoutRequest and obj is not bar:
                self.layouts.append(f"{type(obj).__name__}:{obj.objectName()}")
            return False

    lists = []
    c.store.sessions_changed.connect(lambda s: lists.append(1))
    cnt = Count()
    QApplication.instance().installEventFilter(cnt)
    costs = []
    try:
        for tick in range(1, 21):
            t0 = time.perf_counter()
            push(tick)
            costs.append((time.perf_counter() - t0) * 1000)
            c.sleep(20)
        c.sleep(150)
    finally:
        QApplication.instance().removeEventFilter(cnt)
    report = {
        "paints": cnt.paints,
        "who": cnt.who,
        "layouts": cnt.layouts,
        "lists": len(lists),
        "ms": [round(x, 2) for x in costs],
    }
    assert v.helpers_bar.chips[0].text().startswith("helper-0 · repo0 · running · "), "the chips followed"
    assert cnt.paints["bar"] > 0, "the chips repainted"
    assert cnt.paints["viewport"] == 0, report
    assert cnt.paints["sidebar"] == 0, report
    assert cnt.paints["composer"] == 0, report
    assert cnt.layouts == [], report
    assert not lists, ("the sessions list was re-emitted for a helper tick", report)
    assert max(costs) < 40, report  # one update on the main thread, offscreen, with headroom
