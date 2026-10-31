"""The repositories registry (REPOSITORIES.md) against the mock server: Settings → Repositories lists,
edits in place and scans; the composer's @ answers to the person's names; the Dispatch picker puts
registered repositories first with their notes; the session menu registers; the sidebar uses the name."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5432
HOME = "/home/tester"
PROJ = f"{HOME}/code/pilcrow"


def _settings(c, page: str = "repos"):
    from pilcrow_qt.widgets.dialogs.settings import open_settings

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, page)
    c.sleep(120)
    return s


def _close(c) -> None:
    from pilcrow_qt.widgets.dialogs.settings_ui import close_sheet

    close_sheet("settings")
    c.store.dialogs["settings"] = False
    c.sleep(50)


def _entry(c, name: str) -> dict:
    return next(e for e in c.store.repos if e.get("name") == name)


def test_settings_lists_edits_and_scans(app_client):
    c = app_client
    s = _settings(c)
    page = s.page
    c.wait_until(lambda: len(page.rows) == 3, 5, "three registered repositories")
    assert [r.name_btn.text() for r in page.rows.values()] == ["web", "api", "legacy-api"]
    assert page.count.text() == "3 registered"
    assert not page.filter.isVisible(), "the filter waits for a long list"
    web = page.rows[_entry(c, "web")["id"]]
    assert [b.text() for b in web.aliases._items] == ["order FE", "storefront"]
    assert web.policy.value == "allowed"
    assert not web.detail.isVisible()
    # the policy is one click, saved at once
    api = page.rows[_entry(c, "api")["id"]]
    assert api.policy.value == "ask"
    api.policy._pick("allowed")
    c.wait_until(lambda: _entry(c, "api")["policy"] == "allowed", 5, "policy saved")
    # an alias typed and entered becomes a chip; a clicked chip goes
    web.alias_edit.setText("shop")
    web.alias_edit.returnPressed.emit()
    c.wait_until(lambda: "shop" in _entry(c, "web")["aliases"], 5, "alias saved")
    c.wait_until(lambda: [b.text() for b in web.aliases._items] == ["order FE", "storefront", "shop"], 5, "chips")
    web.aliases._items[2].click()
    c.wait_until(lambda: _entry(c, "web")["aliases"] == ["order FE", "storefront"], 5, "alias removed")
    # the rest opens in place
    web.set_open(True)
    assert web.detail.isVisible()
    assert web.notes.toPlainText().startswith("pnpm, not npm.")
    assert web.verify.text() == "pnpm test"
    assert "node · @acme/web-ui · main" in web.facts.text()
    web.about.setText("Next.js storefront for orders")
    web.about.editingFinished.emit()
    c.wait_until(lambda: _entry(c, "web")["about"] == "Next.js storefront for orders", 5, "about saved")
    # a dependency is picked by name and shows as a chip; the chip removes it
    assert [b.text() for b in web.deps._items] == []
    web.add_dep(_entry(c, "api")["id"])
    c.wait_until(lambda: [b.text() for b in web.deps._items] == ["api"], 5, "dependency chip")
    web.deps._items[0].click()
    c.wait_until(lambda: _entry(c, "web")["deps"] == [], 5, "dependency removed")
    assert c.shot("settings-repos").exists()
    # a scan lists what is under a folder; the registered ones are marked and cannot be checked again
    page.scan_root(f"{HOME}/code")
    c.wait_until(lambda: page.scan_found, 5, "repos.scan")
    assert len(page.scan_found) == 8
    assert page.register_btn.text() == "Register 5"
    assert "5 new repositories, 3 already registered" in page.scan_note.text()
    for p, ck in page.scan_checks.items():
        if ck.isEnabled() and not p.endswith("/cli"):
            ck.setChecked(False)
    assert page.register_btn.text() == "Register 1"
    page.register_checked()
    c.wait_until(lambda: len(c.store.repos) == 4, 5, "registered from the scan")
    assert _entry(c, "cli")["path"] == f"{HOME}/code/cli"
    c.wait_until(lambda: len(page.rows) == 4 and not page.scan_box.isVisible(), 5, "the list follows")
    # remove asks, then the row goes
    page.rows[_entry(c, "cli")["id"]].remove()
    spec = c.store.dialogs.get("confirm")
    assert spec is not None and spec.title == "Remove cli from the registry?"
    spec.on_confirm(False)
    c.wait_until(lambda: len(c.store.repos) == 3 and len(page.rows) == 3, 5, "removed")
    _close(c)


def test_mentions_picker_menu_and_sidebar(app_client):
    c = app_client
    c.wait_until(lambda: c.store.config is not None and c.store.sessions, 10, "config and sessions")
    sid = c.store.sessions[0]["id"]
    c.open(sid)
    v = c.win.session_view
    comp = v.composer
    # the composer's @: the person's alias finds the repository, inserted as @name
    comp._set_text("@ord", 4)
    comp.detect_popup()
    c.wait_until(lambda: comp.suggestions, 5, "suggestions")
    assert comp.suggestions[0]["kind"] == "repo" and comp.suggestions[0]["key"] == "web"
    assert comp.popup.head.text() == "Repositories and files"
    comp.apply_suggestion(comp.suggestions[0])
    assert comp.text == "@web "
    comp._set_text("", 0)
    comp.close_popup()
    # the session menu registers this session's folder, then opens its entry
    labels = [i.get("label") for i in v.menu_items()]
    assert "Register this repository…" in labels
    next(i for i in v.menu_items() if i.get("label") == "Register this repository…")["on_select"]()
    c.wait_until(lambda: any(e["path"] == PROJ for e in c.store.repos), 5, "registered")
    c.wait_until(lambda: c.store.dialogs.get("settings") == "repos", 5, "settings opened on Repositories")
    assert "Repository settings…" in [i.get("label") for i in v.menu_items()]
    _close(c)
    # the sidebar calls a registered folder by the person's name
    key = PROJ
    c.wait_until(lambda: key in c.win.sidebar._heads, 5, "project group")
    assert c.win.sidebar._heads[key].btn.text() == "pilcrow"
    c.call("repos.save", {"id": next(e for e in c.store.repos if e["path"] == PROJ)["id"], "name": "Pilcrow app"})
    c.wait_until(lambda: c.win.sidebar._heads[key].btn.text() == "Pilcrow app", 5, "renamed in the sidebar")
    # the Dispatch picker: registered repositories first, the registry's name on the row, its notes prefilled
    c.store.show_dispatch("new")
    c.wait_until(lambda: c.win.dispatch is not None and c.win.dispatch.composer is not None, 5, "composer")
    picker = c.win.dispatch.composer.targets
    c.wait_until(lambda: picker.repos is not None, 10, "workspace.scan")
    shown = [r["name"] for r in picker.shown()]
    assert shown[:4] == ["api-gateway", "legacy-api", "web-ui", "cli"], shown  # registered first, then the rest
    c.sleep(50)
    assert picker.chips_of(f"{HOME}/code/web-ui")[:2] == ["web", "depends on design-system"]
    assert picker.chips_of(f"{HOME}/code/legacy-api")[0] == "legacy-api"
    assert picker.notes_of(f"{HOME}/code/web-ui") == "pnpm, not npm. The ESLint config lives in config/eslint.cjs."
    assert c.shot("dispatch-picker-registered").exists()
