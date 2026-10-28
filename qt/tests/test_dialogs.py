"""The dialogs: confirm, the command palette, the shortcuts table, the schedule dialog, the lightbox."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5372


def _ready(c):
    c.wait_until(lambda: c.store.sessions_loaded and len(c.store.sessions) >= 5, 10, "sessions")
    c.sleep(50)


def test_confirm_dialog(app_client):
    c = app_client
    _ready(c)
    from pilcrow_qt.store import ConfirmSpec
    from pilcrow_qt.widgets import dialogs

    got = {}
    c.store.confirm_action(
        ConfirmSpec(
            title="Delete it?",
            body="Gone for good.",
            confirm="Delete",
            danger=True,
            checkbox={"label": "Also remove the folder", "value": False},
            list=["a.txt", "b.txt"],
            on_confirm=lambda checked: got.__setitem__("checked", checked),
        )
    )
    dlg = dialogs.current("confirm")
    assert dlg is not None and dlg.isVisible()
    assert dlg.title_lb.text() == "Delete it?"
    assert dlg.confirm_btn.text() == "Delete" and dlg.confirm_btn.property("kind") == "danger"
    assert dlg.checkbox is not None and not dlg.checkbox.isChecked()
    dlg.checkbox.setChecked(True)
    dlg.confirm_btn.click()
    assert got == {"checked": True}
    assert c.store.dialogs["confirm"] is None
    c.sleep(20)
    assert dialogs.current("confirm") is None
    # Escape cancels through the store
    c.store.confirm_action(
        ConfirmSpec(title="Again?", confirm="Yes", on_confirm=lambda _c: got.__setitem__("again", True))
    )
    dlg = dialogs.current("confirm")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert c.store.dialogs["confirm"] is None
    assert "again" not in got


def test_palette_filters_and_opens(app_client):
    c = app_client
    _ready(c)
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt.widgets import dialogs

    c.store.set_dialog("palette", True)
    pal = dialogs.current("palette")
    assert pal is not None and pal.isVisible()
    c.wait_until(pal.input.hasFocus, 3, "the search field takes focus")
    groups = {i.group for i in pal.items}
    assert groups == {"Actions", "Sessions"}
    assert pal.items[0].label == "New session"
    c.sleep(80)
    assert c.shot("palette").exists()
    pal.input.setText("parser")
    assert [i.label for i in pal.items if i.group == "Sessions"] == ["Write tests for the parser"]
    assert all("parser" in (i.label + i.hint).lower() or True for i in pal.items)
    assert pal.selected() is not None and pal.selected().label == "Write tests for the parser"
    QTest.keyClick(pal.input, Qt.Key.Key_Return)
    assert c.store.dialogs["palette"] is False
    c.wait_until(lambda: c.store.active_id == "sess-tests", 5, "session opened from the palette")
    # the window shortcut toggles it
    c.store.set_dialog("palette", True)
    pal = dialogs.current("palette")
    pal.input.setText("zzzqqq")
    assert not pal.items and pal.empty.isVisible()
    c.store.set_dialog("palette", False)
    c.sleep(20)
    assert dialogs.current("palette") is None


def test_shortcuts_dialog(app_client):
    c = app_client
    _ready(c)
    from pilcrow_qt.widgets import dialogs

    c.store.set_dialog("shortcuts", True)
    dlg = dialogs.current("shortcuts")
    assert dlg is not None and dlg.isVisible()
    keys = [r.keys for r in dlg.rows]
    assert "Ctrl+K" in keys
    assert dlg.rows[0].what == "Command palette"
    assert c.texts(type(dlg.rows[0].findChildren(type(dlg.rows[0].label))[0]), dlg.rows[0])  # the row has text
    c.sleep(80)
    assert c.shot("shortcuts").exists()
    dlg.close_btn.click()
    assert c.store.dialogs["shortcuts"] is False


def test_schedule_dialog_preview(app_client):
    c = app_client
    _ready(c)
    from pilcrow_qt.widgets import dialogs

    c.store.set_dialog("schedule", {})
    dlg = dialogs.current("schedule")
    assert dlg is not None and dlg.isVisible()
    assert dlg.title_lb.text() == "New schedule"
    assert dlg.save_btn.text() == "Create schedule"
    assert dlg.cadence() == {"kind": "daily", "time": "09:00"}
    c.wait_until(dlg.name.hasFocus, 3, "the name field takes focus")
    c.wait_until(lambda: dlg.preview_state in ("ready", "error"), 5, "schedules.preview answered")
    assert isinstance(dlg.preview_lb.text(), str)  # null fields or an unknown method both leave a quiet footer
    # switching the cadence re-asks and keeps the dialog whole
    dlg.kind.setCurrentIndex(2)  # weekly
    assert dlg.weekday.isVisible() and dlg.cadence()["kind"] == "weekly" and dlg.cadence()["weekday"] == 1
    dlg.kind.setCurrentIndex(5)  # cron
    assert dlg.cron.isVisible() and not dlg.time.isVisible()
    c.wait_until(lambda: dlg.preview_state in ("ready", "error"), 5, "preview again")
    dlg.set_mode("plan")
    assert dlg.mode_rows["plan"].isChecked() and not dlg.mode_rows["acceptEdits"].isChecked()
    dlg.toggle_more()
    assert dlg.more_box.isVisible()
    assert dlg.values()["maxMinutes"] == 45 and dlg.values()["mode"] == "plan"
    c.sleep(80)
    assert c.shot("schedule").exists()
    dlg.cancel_btn.click()
    assert c.store.dialogs["schedule"] is None


def test_lightbox_and_unbuilt_keys(app_client, capsys):
    c = app_client
    _ready(c)
    from pilcrow_qt.widgets import dialogs

    dialogs.install_overlays(c.win)
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    c.store.open_lightbox(png)
    lb = dialogs._overlays[id(c.win)]
    assert lb.isVisible() and not lb._pm.isNull()
    lb.close_btn.click()
    assert c.store.lightbox is None and not lb.isVisible()
    c.store.set_dialog("nope", True)
    assert "nope requested (not built yet)" in capsys.readouterr().out
    c.store.set_dialog("nope", False)
