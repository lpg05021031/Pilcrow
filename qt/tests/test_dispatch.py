"""Dispatch against the mock server: the board, composing and starting a job, the canary that asks and
is answered once for every repository (from the control room and from the job view), the rows that
update in place, the review foot, the finish, the report in the inbox, and the sidebar's job group."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtCore import Qt

MOCK_PORT = 5431
HOME = "/home/tester"


def _dispatch(c):
    c.store.show_dispatch(None)
    c.wait_until(lambda: c.win.dispatch is not None and c.win.dispatch.isVisible(), 5, "dispatch page")
    c.wait_until(lambda: c.store.jobs_loaded, 5, "dispatch.list")
    c.sleep(80)
    return c.win.dispatch


def _job(c, jid):
    return c.store.jobs.get(jid) or {}


def test_board_shows_the_jobs_and_the_sidebar_entry(app_client):
    c = app_client
    page = _dispatch(c)
    board = page.board
    assert c.win.sidebar.page_dispatch._on, "the sidebar's Dispatch entry is lit"
    assert board.summary.text() == "1 job"
    assert board.sections["finished"].isVisible()
    assert not board.sections["needs"].isVisible() and not board.sections["running"].isVisible()
    lines = list(board.lines.values())
    assert [ln.name.text() for ln in lines] == ["Pin node 22 in CI · 3 repos"]
    assert lines[0].counts.text() == "2 done · 1 failed"
    assert not board.empty.isVisible()
    # j focuses it, Enter opens the job view
    board.handle_key(0, "j")
    assert board.focus_index == 0
    board.handle_key(Qt.Key.Key_Return, "")
    c.wait_until(lambda: page.job_view is not None and page.job_view.isVisible(), 5, "job view")
    jv = page.job_view
    c.wait_until(lambda: len(jv.list.runs) == 3, 5, "the job's runs")
    assert jv.title.text() == "Pin node 22 in CI"
    assert "finished" in jv.meta.text()
    assert jv.foot.isVisible(), "a finished job shows its review foot"
    c.wait_until(lambda: jv.review_text.text().startswith("Finished:"), 5, "review text")
    assert "2 of 3 done" in jv.review_text.text() and "2 committed" in jv.review_text.text()
    assert not jv.commit_btn.isVisible(), "nothing left to land"
    assert [r.get("name") for r in jv.list.runs] == ["cli", "docs", "infra"], "rows keep the plan's order"
    assert c.shot("dispatch-job-finished").exists()
    jv.back.click()
    c.wait_until(lambda: board.isVisible(), 5, "back to the board")


def test_a_job_from_the_composer_through_the_canary_to_the_report(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 0})
    page = _dispatch(c)
    page.board.new_btn.click()
    c.wait_until(lambda: page.composer is not None and page.composer.isVisible(), 5, "composer")
    comp = page.composer
    picker = comp.targets
    c.wait_until(lambda: picker.repos is not None, 10, "workspace.scan")
    assert [r["name"] for r in picker.repos][:3] == ["design-system", "web-ui", "docs"]
    assert "8 repositories" not in picker.count.text() and picker.count.text() == "0 of 8 selected"
    comp.name.setText("Bump eslint")
    comp.brief.setPlainText("Run a command needing permission in {{repo.name}} on {{repo.branch}}.")
    picker.set_selected([f"{HOME}/code/{n}" for n in ("design-system", "web-ui", "docs")])
    assert picker.count.text() == "3 of 8 selected"
    assert comp.branch.text() == "pilcrow/bump-eslint", "the branch follows the name"
    c.wait_until(lambda: comp.plan is not None and comp.start_btn.isEnabled(), 10, "the plan")
    plan = comp.plan
    assert plan["ok"] == 3 and plan["blocked"] == 0
    by = {t["name"]: t for t in plan["targets"]}
    assert by["web-ui"]["level"] == 1, "web-ui depends on design-system"
    assert by["web-ui"]["brief"] == "Run a command needing permission in web-ui on main."
    assert picker.chips_of(f"{HOME}/code/web-ui") == ["ok", "after design-system"], "the plan's verdict on the row"
    assert c.shot("dispatch-composer").exists()

    comp.start()
    c.wait_until(lambda: page.job_view is not None and page.job_view.isVisible(), 10, "the job view after Start")
    jv = page.job_view
    jid = jv.jid
    assert jid and _job(c, jid)["name"] == "Bump eslint"
    # the canary (design-system: level 0, smallest, first by name) asks; the rest wait
    c.wait_until(lambda: any(r.get("state") == "needs_action" for r in c.store.runs_of(jid)), 15, "the canary asks")
    runs = {r["name"]: r for r in c.store.runs_of(jid)}
    assert runs["design-system"]["wave"] == "canary" and runs["design-system"]["state"] == "needs_action"
    assert runs["web-ui"]["state"] == "queued" and runs["docs"]["state"] == "queued"
    assert c.store.dispatch_needs == 1
    assert c.win.sidebar.page_dispatch._count == 1
    c.sleep(80)
    from pilcrow_qt.widgets.dispatch.board import RequestRow

    reqs = c.find(RequestRow, jv)
    assert len(reqs) == 1 and reqs[0].tool.text() == "Bash"
    assert reqs[0].who.text() == "asked by design-system"
    row_before = jv.list.scene.block(f"run:{runs['design-system']['id']}")
    assert row_before is not None
    assert c.shot("dispatch-job-asking").exists()

    # the control room shows the same request with Allow for all; answering there answers the job
    c.store.show_page("overview")
    c.wait_until(lambda: c.win.overview is not None and c.win.overview.needs_cards, 5, "needs card")
    card = next(iter(c.win.overview.needs_cards.values()))
    c.wait_until(lambda: card.all_btn.isVisible(), 5, "Allow for all on the card")
    card.all_btn.click()
    c.wait_until(lambda: _job(c, jid).get("canaryWaiting"), 15, "the canary finished")
    job = _job(c, jid)
    assert [r["key"] for r in job["rules"]] == ["Bash:rm -rf build && npm run build"]
    assert job["learned"] and job["learned"][0]["name"] == "design-system"
    assert job["forecast"]["runs"] == 2

    # back in the job view: the canary card with the forecast; the row is the same block, updated
    c.store.open_job(jid)
    c.wait_until(
        lambda: jv.isVisible() and any(r["name"] == "design-system" and r["state"] == "done" for r in jv.list.runs),
        5,
        "job view again",
    )
    assert [r["name"] for r in jv.list.runs] == ["design-system", "docs", "web-ui"], "dependencies first, then by name"

    c.sleep(80)
    from pilcrow_qt.widgets.dispatch.board import CanaryRow

    canary = c.find(CanaryRow, jv)
    assert len(canary) == 1 and canary[0].text.text().startswith("Canary design-system finished")
    assert canary[0].forecast.text().startswith("forecast $")
    assert jv.list.scene.block(f"run:{runs['design-system']['id']}") is row_before, "the row was patched, not rebuilt"
    assert "canary done" in jv.meta.text()
    assert jv.rules.isVisible() and "rm -rf build" in jv.rules.text()
    # the brief can change before the rest run: the main wave fixes files instead of asking
    assert jv.edit_btn.isVisible()
    jv.edit_btn.click()
    assert jv.brief_edit.isVisible() and jv.brief_edit.toPlainText().startswith("Run a command")
    jv.brief_edit.setPlainText("Fix the eslint config in {{repo.name}}.")
    jv.edit_btn.click()
    c.wait_until(lambda: _job(c, jid)["brief"].startswith("Fix the eslint"), 5, "brief saved")
    assert jv.brief.text() == "Fix the eslint config in {{repo.name}}."
    canary[0].go_on()
    c.wait_until(lambda: _job(c, jid)["state"] == "reviewing" and _job(c, jid)["wave"] == "done", 20, "the main wave")
    runs = {r["name"]: r for r in c.store.runs_of(jid)}
    assert all(r["state"] == "done" for r in runs.values()), runs
    assert runs["web-ui"]["startedAt"] >= runs["design-system"]["endedAt"], "web-ui ran after its dependency"
    assert runs["web-ui"]["files"] and runs["docs"]["files"], "the main wave edited files"
    c.sleep(100)
    assert jv.foot.isVisible() and jv.commit_btn.isVisible() and jv.keep_btn.isVisible()
    c.wait_until(lambda: jv.review_text.text().startswith("Review:"), 5, "review loaded")
    assert "2 repositories changed" in jv.review_text.text()
    assert "the same change in 2 repos (web/src/lib/partialJson.ts)" in jv.review_text.text()
    assert c.shot("dispatch-job-review").exists()
    # the sidebar: the three runs sit under the job, collapsed
    c.wait_until(lambda: f"job:{jid}" in c.win.sidebar._heads, 5, "the job group in the sidebar")
    from pilcrow_qt.widgets.sidebar import SessionRow

    shown = [w.sid for w in c.find(SessionRow, c.win.sidebar) if w.isVisible()]
    assert not any(c.store.session(s) and (c.store.session(s) or {}).get("dispatch") for s in shown), "collapsed"
    # keep everything: the job is finished and the report is in the inbox
    jv.finish("keep")
    c.wait_until(lambda: _job(c, jid)["state"] == "finished", 10, "finished")
    c.wait_until(lambda: c.store.inbox_unread == 1, 5, "inbox unread")
    inbox = c.call("inbox.list")
    assert inbox["items"][0]["kind"] == "dispatch" and inbox["items"][0]["scheduleName"] == "Bump eslint"
    # the inbox shows the report as its own card
    c.store.show_page("inbox")
    c.wait_until(lambda: c.win.inbox is not None and c.win.inbox.run_cards, 5, "inbox cards")
    from pilcrow_qt.widgets.inbox import DispatchCard

    cards = [w for w in c.win.inbox.run_cards if isinstance(w, DispatchCard)]
    assert len(cards) == 1 and cards[0].name.text() == "Bump eslint"
    assert cards[0].sum.text().startswith("3 of 3 repositories done")
    assert cards[0].rows.count() == 3
    assert c.shot("dispatch-inbox").exists()
    cards[0].open_btn.click()
    c.wait_until(lambda: c.store.page == "dispatch" and c.store.dispatch_view == jid, 5, "Open job from the inbox")
    page.board.refresh()
    c.store.show_dispatch(None)
    c.sleep(80)
    from pilcrow_qt.widgets.dispatch.board import FinishedLine

    lay = page.board.finished_lay
    shown_lines = [
        lay.itemAt(i).widget() for i in range(lay.count()) if isinstance(lay.itemAt(i).widget(), FinishedLine)
    ]
    assert [ln.name.text() for ln in shown_lines] == ["Bump eslint · 3 repos", "Pin node 22 in CI · 3 repos"], (
        "newest first"
    )
    assert c.shot("dispatch-board-after").exists()


def test_playbooks_run_again_schedule_and_the_written_report(app_client):
    c = app_client
    c.call("mock.pace", {"ms": 0})
    page = _dispatch(c)
    c.wait_until(lambda: c.store.playbooks, 5, "playbooks")
    assert [p["name"] for p in c.store.playbooks] == ["Dependency bump", "License headers"]
    # the composer takes a playbook: its brief, settings and repositories
    c.store.show_dispatch("new")
    c.wait_until(lambda: page.composer is not None and page.composer.isVisible(), 5, "composer")
    comp = page.composer
    picker = comp.targets
    c.wait_until(lambda: picker.repos is not None, 10, "workspace.scan")
    assert len(picker.list.blocks) == 8, "one row per repository, on the canvas"
    picker.filter.setText("web")
    assert len(picker.list.blocks) == 1
    picker.filter.setText("")
    picker.toggle(f"{HOME}/code/cli")
    assert picker.count.text() == "1 of 8 selected" and picker.targets()[0]["name"] == "cli"
    i = comp.playbook.findData("pb-deps")
    assert i > 0
    comp.playbook.setCurrentIndex(i)
    assert comp.brief.toPlainText().startswith("Bump the dependencies in {{repo.name}}")
    assert comp.name.text() == "Dependency bump"
    assert comp.verify.text() == "npm test"
    assert picker.count.text() == "3 of 8 selected"
    assert [t["name"] for t in picker.targets()] == ["design-system", "docs", "web-ui"]
    comp.report.setCurrentIndex(comp.report.findData("agent"))
    assert comp.values()["settings"]["report"] == "agent" and comp.values()["playbookId"] == "pb-deps"
    c.wait_until(lambda: comp.plan is not None and comp.start_btn.isEnabled(), 10, "the plan")
    assert c.shot("dispatch-composer-playbook").exists()
    comp.start()
    c.wait_until(lambda: page.job_view is not None and page.job_view.isVisible(), 10, "job view")
    jv = page.job_view
    jid = jv.jid
    assert _job(c, jid)["playbookId"] == "pb-deps"
    c.wait_until(lambda: _job(c, jid).get("canaryWaiting"), 20, "canary done")
    c.call("dispatch.continue", {"id": jid})
    c.wait_until(lambda: _job(c, jid)["state"] == "reviewing" and _job(c, jid)["wave"] == "done", 20, "main wave")
    jv.finish("keep")
    c.wait_until(lambda: _job(c, jid)["state"] == "finished", 10, "finished")
    # the written report arrives after the table, and the view and the inbox card show it
    c.wait_until(lambda: (_job(c, jid).get("report") or {}).get("prose"), 10, "the prose")
    c.sleep(80)
    assert jv.prose_box.isVisible() and jv.prose.text().startswith("The brief was carried out the same way")
    md = c.call("dispatch.export", {"id": jid})
    assert "# Dependency bump" in md
    # run again: a new job from the same playbook
    jv.run_again()
    c.wait_until(lambda: c.store.dispatch_view not in (None, "new", jid), 10, "a new job from the playbook")
    jid2 = str(c.store.dispatch_view)
    c.wait_until(lambda: jid2 in c.store.jobs, 5, "its summary")
    assert _job(c, jid2)["playbookId"] == "pb-deps" and _job(c, jid2)["name"] == "Dependency bump"
    c.wait_until(lambda: next(p for p in c.store.playbooks if p["id"] == "pb-deps")["uses"] == 4, 5, "uses")
    c.call("dispatch.stop", {"id": jid2})
    # save as playbook: one more, named after the job
    c.store.open_job(jid)
    c.sleep(50)
    jv.save_playbook()
    c.wait_until(lambda: len(c.store.playbooks) == 3, 5, "a third playbook")
    # schedule this: the dialog opens for a playbook, saves a dispatch schedule, and Run now starts an unattended job
    from pilcrow_qt.widgets import dialogs

    jv.schedule_playbook()
    c.wait_until(lambda: dialogs.current("schedule") is not None, 5, "schedule dialog")
    dlg = dialogs.current("schedule")
    c.sleep(50)
    assert dlg.what_runs() == "dispatch" and dlg.playbook.currentData() == "pb-deps"
    assert not dlg.prompt_field.isVisible() and not dlg.cwd_field.isVisible() and dlg.dispatch_note.isVisible()
    assert dlg.name.text() == "Dependency bump", "the name follows the playbook"
    dlg.name.setText("Nightly bump")
    assert c.shot("schedule-dispatch").exists()
    dlg.save()
    c.wait_until(lambda: dialogs.current("schedule") is None, 5, "dialog closed")
    scheds = c.call("schedules.list")
    assert len(scheds) == 1 and scheds[0]["kind"] == "dispatch" and scheds[0]["playbookId"] == "pb-deps"
    r = c.call("schedules.runNow", {"id": scheds[0]["id"]})
    c.wait_until(lambda: r["jobId"] in c.store.jobs, 5, "the scheduled job")
    j3 = _job(c, r["jobId"])
    assert j3["wave"] == "main" and j3["scheduleId"] == scheds[0]["id"] and j3["settings"]["unattended"]
    c.wait_until(lambda: _job(c, r["jobId"])["state"] == "reviewing", 20, "the scheduled job ran through")
    # the inbox: the report card carries the prose
    c.store.show_page("inbox")
    c.wait_until(lambda: c.win.inbox is not None and c.win.inbox.run_cards, 5, "inbox cards")
    from pilcrow_qt.widgets.inbox import DispatchCard

    card = next(w for w in c.win.inbox.run_cards if isinstance(w, DispatchCard) and w.it.get("jobId") == jid)
    assert card.prose is not None and card.prose.text().startswith("The brief was carried out")
    assert c.win.inbox.sched_cards and c.win.inbox.sched_cards[0].s["kind"] == "dispatch"
