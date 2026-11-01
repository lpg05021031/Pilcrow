"""Port of test/unit/shortcuts.test.ts, and the one table driving the window's keys and the sheet."""

from __future__ import annotations

from harness import app_client, mock_server

from pilcrow_qt.lib.shortcuts import SHORTCUTS


def test_every_entry_has_keys_and_a_label():
    for s in SHORTCUTS.values():
        assert s.keys.strip() and s.label.strip(), s


def test_no_two_shortcuts_answer_to_the_same_key_sequence():
    seqs = [k for s in SHORTCUTS.values() for k in s.sequences if "+" in k or k == "Escape"]
    assert len(seqs) == len(set(seqs)), sorted(seqs)
    assert SHORTCUTS["tabs"].combos == tuple(f"Alt+{i}" for i in range(1, 8))


def test_the_window_binds_the_table_and_the_sheet_lists_it_in_order(app_client):
    from PySide6.QtGui import QKeySequence, QShortcut

    from pilcrow_qt.widgets import dialogs

    c = app_client
    bound = {s.key().toString() for s in c.win.findChildren(QShortcut)}
    for name in ("palette", "newSession", "tabs", "nextSession", "zoom", "stop", "quit", "dispatch"):
        for k in SHORTCUTS[name].sequences:
            assert QKeySequence(k).toString() in bound, (name, k)
    c.store.set_dialog("shortcuts", True)
    dlg = dialogs.current("shortcuts")
    assert [(r.keys, r.what) for r in dlg.rows] == [(s.keys, s.label) for s in SHORTCUTS.values()]
    dlg.close_btn.click()
