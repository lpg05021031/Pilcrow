"""Helpers (HELPERS.md) against the mock server: a lead starts two helpers from one prompt; the bar and
the start cards follow; the sidebar nests the helpers under the lead; the control room says who asked
and answers for every helper; the handoffs come back as notes; the Changes panel shows each worktree and
finishes them; the inbox gets the record; deleting the lead can take its helpers along."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5433
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
    assert web_chip.text().startswith("web · needs you")
    api_chip = next(ch for ch in v.helpers_bar.chips if ch.h.get("name") == "api")
    assert api_chip.h.get("state") == "done" and api_chip.text().startswith("api · done")
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
    assert hv.lead_crumb.text() == "helper of Invoice download"
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
