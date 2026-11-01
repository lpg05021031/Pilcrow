"""The settings sheet, the new-session sheet and the MCP sign-in sheet against the mock server."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QPushButton

MOCK_PORT = 5381


def _labels(c, parent=None):
    """Every visible line of text: QLabels and the browser-like Text widgets the dialogs lay type on."""
    from pilcrow_qt.widgets.dialogs.parts import Text

    out = [w.text() for w in c.find(QLabel, parent) if w.isVisible() and w.text()]
    out += [w.text() for w in c.find(Text, parent) if w.isVisible() and w.text()]
    return out


def _button(c, text, parent=None):
    for b in c.find(QPushButton, parent):
        if b.isVisible() and b.text() == text:
            return b
    raise AssertionError(f"no visible button {text!r}")


def _close_all(c):
    from pilcrow_qt.widgets.dialogs.settings_ui import close_sheet

    for key in ("settings", "newSession", "mcpAuth"):
        close_sheet(key)
        c.store.dialogs[key] = False if key == "settings" else None
    c.sleep(50)


def test_general_page_and_theme(app_client):
    c = app_client
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.dialogs.settings import open_settings

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, "general")
    c.sleep(100)
    assert s.isVisible() and not s.isWindow()
    nav = [b.text() for b in c.find(QPushButton, s.nav)]
    assert nav == ["General", "Repositories", "Claude Code", "Skills", "MCP servers", "Memory", "Diagnostics"], nav
    assert s.nav_buttons["general"].isChecked()
    assert "Theme" in _labels(c, s.frame)
    seg = s.page.theme
    assert list(seg.buttons) == ["system", "dark", "light"]
    QTest.mouseClick(seg.buttons["light"], Qt.MouseButton.LeftButton)
    c.wait_until(lambda: theme.tokens().name == "light", 5, "light theme")
    c.wait_until(lambda: (c.store.config or {}).get("theme") == "light", 5, "config saved")
    assert c.shot("settings-light").exists()
    c.store.set_theme("dark")
    c.wait_until(lambda: theme.tokens().name == "dark", 5, "dark theme")
    # Escape closes the sheet and leaves the store key cleared.
    from pilcrow_qt.widgets.dialogs.settings_ui import open_sheet

    c.store.dialogs["settings"] = "general"
    QTest.keyClick(s, Qt.Key.Key_Escape)
    c.sleep(50)
    assert open_sheet("settings") is None
    assert c.store.dialogs["settings"] is False
    _close_all(c)


def test_skills_page_lists_roots(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.settings import open_settings

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, "skills")
    c.wait_until(lambda: s.page.data is not None, 10, "skills.list")
    c.sleep(100)
    texts = _labels(c, s.frame)
    assert "Teach Claude how you work" in texts
    for group in ("Personal", "This project", "From plugins"):
        assert group in texts, texts
    assert "None yet." in texts
    assert "Open a session to see project skills" in texts
    assert "No plugin skills installed." in texts
    assert "~/.claude/skills" in texts
    # Switching pages re-selects the nav and rebuilds the page.
    s.show_section("diagnostics")
    c.sleep(100)
    assert "Claude Code program" in _labels(c, s.frame)
    assert _button(c, "Copy diagnostics", s.frame)
    _close_all(c)


def test_mcp_page_add_form(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.settings import open_settings
    from pilcrow_qt.widgets.dialogs.settings_mcp import AddMcpSheet

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, "mcp")
    c.wait_until(lambda: s.page.data is not None, 10, "mcp.list")
    c.sleep(100)
    texts = _labels(c, s.frame)
    assert "Give Claude more tools" in texts
    assert any(t.startswith("Statuses appear once a session has Claude Code running.") for t in texts), texts
    QTest.mouseClick(_button(c, "Add server", s.frame), Qt.MouseButton.LeftButton)
    c.sleep(100)
    form = c.find(AddMcpSheet)[0]
    assert form.isVisible()
    assert [e.accessibleName() for e in c.find(QLineEdit, form.frame) if e.isVisible()][:2] == ["Name", "URL"]
    submit = _button(c, "Add server", form.frame)
    assert not submit.isEnabled()
    form.name.setText("linear")
    form.url.setText("https://mcp.linear.app/mcp")
    assert submit.isEnabled()
    n_toasts = len(c.store.toasts)
    QTest.mouseClick(submit, Qt.MouseButton.LeftButton)
    # The mock has no mcp.add: the call fails and is toasted, the form stays usable; a real server
    # answers with the list and the form closes. Either way nothing crashes.
    c.wait_until(lambda: len(c.store.toasts) > n_toasts or not form.isVisible(), 10, "mcp.add answered")
    c.sleep(50)
    assert form.isVisible() and submit.isEnabled() or not form.isVisible()
    assert c.shot("settings-mcp-add").exists()
    _close_all(c)


def test_memory_page(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.settings import open_settings

    c.wait_until(lambda: c.store.sessions_loaded, 10, "sessions")
    s = open_settings(c.win, "memory")
    c.wait_until(lambda: s.page.notes is not None, 10, "memory.list")
    c.sleep(100)
    texts = _labels(c, s.frame)
    assert "Project memory" in texts
    assert "No notes for pilcrow yet." in texts, texts
    assert s.page.proj.count() >= 2
    _close_all(c)


def test_new_session_dialog(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.new_session import NewSessionSheet, open_new_session
    from pilcrow_qt.widgets.dialogs.settings_ui import FolderChip

    c.wait_until(lambda: c.store.sessions_loaded, 10, "sessions")
    s = open_new_session(c.win, {})
    assert isinstance(s, NewSessionSheet)
    c.sleep(300)
    assert s.path.text() == "/home/tester/code/pilcrow"
    chips = [ch.text() for ch in c.find(FolderChip, s.frame) if ch.isVisible()]
    assert chips == ["web", "api", "legacy-api", "website"], chips  # registered repositories first, by name
    assert "Options" in _labels(c, s.frame)
    assert _button(c, "Create session", s.frame)
    # The options fold open with the permission modes, model and effort.
    QTest.mouseClick(s.disclose, Qt.MouseButton.LeftButton)
    c.sleep(150)
    assert s.radio.value == "default"
    assert "Isolated worktree" in _labels(c, s.frame)
    QTest.mouseClick(s.disclose, Qt.MouseButton.LeftButton)
    c.sleep(150)
    s.prompt.setPlainText("hello from the new session dialog")
    assert _button(c, "Start", s.frame)
    assert c.shot("new-session").exists()
    before = {x.get("id") for x in c.store.sessions}
    c.call("mock.pace", {"ms": 0})
    QTest.mouseClick(_button(c, "Start", s.frame), Qt.MouseButton.LeftButton)
    c.wait_until(lambda: len(c.store.sessions) > len(before), 10, "session created")
    new_id = next(x.get("id") for x in c.store.sessions if x.get("id") not in before)
    c.wait_until(lambda: c.store.active_id == new_id, 10, "opened")
    c.wait_until(lambda: any(it.kind == "user" for it in c.store.get_model(new_id).items), 10, "user item")
    from pilcrow_qt.widgets.dialogs.settings_ui import open_sheet

    c.wait_until(lambda: open_sheet("newSession") is None, 5, "sheet closed")
    assert c.store.dialogs["newSession"] is None
    # The mock forgets this session on the next reset; do not leave it as the one to re-open.
    c.store.active_id = None
    c.store.active_changed.emit(None)
    _close_all(c)


def test_mcp_auth_dialog(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.mcp_auth import open_mcp_auth

    c.wait_until(lambda: c.store.sessions_loaded, 10, "sessions")
    s = open_mcp_auth(c.win, {"sessionId": "sess-login", "name": "linear"})
    assert s.title_lb.text() == "Sign in to linear"
    c.wait_until(lambda: s.start is not None or s.error is not None, 10, "session.mcp.auth")
    c.sleep(50)
    assert any("not available" in t for t in _labels(c, s.frame)), _labels(c, s.frame)
    assert not s.open_btn.isVisible()
    assert s.cancel_btn.text() == "Cancel"
    _close_all(c)


def test_settings_screenshot(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.settings import open_settings

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, "general")
    c.sleep(150)
    assert c.shot("settings").exists()
    s.show_section("claude")
    c.sleep(150)
    assert c.shot("settings-claude").exists()
    s.show_section("skills")
    c.wait_until(lambda: s.page.data is not None, 10, "skills.list")
    c.sleep(150)
    assert c.shot("settings-skills").exists()
    s.show_section("mcp")
    c.wait_until(lambda: s.page.data is not None, 10, "mcp.list")
    c.sleep(150)
    assert c.shot("settings-mcp").exists()
    s.show_section("memory")
    c.wait_until(lambda: s.page.notes is not None, 10, "memory.list")
    c.sleep(150)
    assert c.shot("settings-memory").exists()
    s.show_section("diagnostics")
    c.sleep(150)
    assert c.shot("settings-diagnostics").exists()
    _close_all(c)


def test_new_session_options_screenshot(app_client):
    c = app_client
    from pilcrow_qt.widgets.dialogs.new_session import open_new_session

    c.wait_until(lambda: c.store.sessions_loaded, 10, "sessions")
    s = open_new_session(c.win, {"cwd": "/home/tester/code/website"})
    c.sleep(400)
    QTest.mouseClick(s.disclose, Qt.MouseButton.LeftButton)
    c.sleep(200)
    assert "Isolated worktree" in _labels(c, s.frame)
    QTest.mouseClick(s.wt_switch, Qt.MouseButton.LeftButton)
    c.sleep(200)
    assert s.use_worktree and s.wt_fields.isVisible()
    assert [s.base.itemText(i) for i in range(s.base.count())] == ["main"]
    assert c.shot("new-session-options").exists()
    QTest.mouseClick(s.disclose, Qt.MouseButton.LeftButton)
    c.sleep(150)
    if s.picker is not None:  # the in-app folder browser, when widgets/folder_picker.py is present
        QTest.mouseClick(s.browse_btn, Qt.MouseButton.LeftButton)
        c.sleep(300)
        assert s.picker.isVisible() and not s.chips_w.isVisible()
        assert c.shot("new-session-browse").exists()
    _close_all(c)


_ = QPlainTextEdit


def test_claude_tab_selects_open_the_menu(app_client):
    """The Claude Code tab's dropdowns are the web's Select: a popover with icon, label and hint rows, the
    chosen one checked, and the screenshot is taken with the list open."""
    c = app_client
    from pilcrow_qt.widgets.dialogs.settings import open_settings
    from pilcrow_qt.widgets.dialogs.settings_ui import Select

    c.wait_until(lambda: c.store.config is not None, 10, "config")
    s = open_settings(c.win, "claude")
    c.sleep(150)
    page = s.page
    selects = [w for w in c.find(Select, s.frame) if w.isVisible()]
    assert [w.accessibleName() for w in selects][:4] == [
        "Claude Code program",
        "Default permission mode",
        "Default model",
        "Default effort",
    ]
    assert all(w.height() == 34 for w in selects)
    mode = page.mode
    assert mode.currentText() == "Ask before edits" and mode.items[0]["icon"] == "shield-check"
    mode.showPopup()
    c.sleep(100)
    pop = mode.popup
    assert pop is not None and pop.isVisible() and pop.width() == 300
    rows = [r for r in pop.menu.rows if r is not None]
    assert [r.label for r in rows] == [
        "Ask before edits",
        "Edit automatically",
        "Plan mode",
        "Auto",
        "Bypass permissions",
    ]
    assert rows[0].it["checked"] and rows[0].hint.startswith("Asks before")
    # the popover opens 6px under the button, flush with its left edge
    assert pop.x() == mode.mapTo(pop.parentWidget(), mode.rect().topLeft()).x()
    assert pop.y() == mode.mapTo(pop.parentWidget(), mode.rect().bottomLeft()).y() + 1 + 6
    pop.menu.set_active(2)
    c.sleep(60)
    assert c.shot("settings-claude-open").exists()
    # choosing a row closes the list and saves the mode
    pop.menu.choose(2)
    c.wait_until(lambda: mode.popup is None, 3, "list closed")
    c.wait_until(lambda: (c.store.config or {}).get("defaultMode") == "plan", 5, "mode saved")
    assert mode.currentText() == "Plan mode"
    mode.showPopup()
    c.sleep(60)
    assert [r.it["checked"] for r in [r for r in mode.popup.menu.rows if r is not None]] == [
        False,
        False,
        True,
        False,
        False,
    ]
    mode.hidePopup()
    # the model select lists the gateway's models with their ids under the names
    page.model.box.showPopup()
    c.sleep(80)
    labels = [(r.label, r.hint) for r in page.model.box.popup.menu.rows if r is not None]
    assert labels[0] == ("Default", "Claude Code's configured model")
    assert ("Mock 1", "claude-mock-1") in labels
    assert c.shot("settings-claude-model-open").exists()
    page.model.box.hidePopup()
    c.store.set_config({"defaultMode": "default"})
    c.wait_until(lambda: (c.store.config or {}).get("defaultMode") == "default", 5, "mode restored")
    _close_all(c)
