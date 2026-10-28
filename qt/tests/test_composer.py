"""The composer against the mock server: the card's ring and focus state, the hint row, geometry, the
popups and menus, attachments, drafts and history, the send key setting, and a screenshot."""

from __future__ import annotations

import time

import pytest
from harness import app_client, mock_server  # noqa: F401

MOCK_PORT = 5371


@pytest.fixture(autouse=True)
def _settled_socket():
    yield
    from PySide6.QtCore import QCoreApplication

    from pilcrow_qt.rpc import rpc

    end = time.monotonic() + 3
    while rpc._ws is not None and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture(autouse=True, scope="module")
def _fresh_store_afterwards():
    yield
    from pilcrow_qt.store import store

    store.active_id = None
    store.rt.clear()
    store.models.clear()
    store.sessions = []
    store.sessions_loaded = False


def _open(c, sid: str = "sess-login"):
    from pilcrow_qt.widgets.session_view import SessionView

    c.open(sid)
    c.wait_until(
        lambda: isinstance(c.win.session_view, SessionView) and c.win.session_view.sid == sid, 10, "session view"
    )
    v = c.win.session_view
    c.wait_until(lambda: v.git is not None, 10, "git info")
    c.win.activateWindow()
    return v


def _idle(c, sid: str) -> None:
    c.wait_until(lambda: c.store.rt[sid].status == "idle" and not c.store.rt[sid].busy, 20, "idle")


# ---------------------------------------------------------------- pure helpers


def test_column_width_is_the_golden_section_of_the_pane():
    from pilcrow_qt.widgets.composer import column_width

    # 1201px pane (the web at 1480 wide): 61.8% of the wrap's 1161px content box
    assert column_width(1201) == 717
    assert column_width(1212) == 724
    assert column_width(900) == 640  # never under 640 while the pane allows it
    assert column_width(600) == 560  # ...but never wider than the wrap's content box
    assert column_width(2400) == 1080  # and it stops growing at 1080


def test_shadow_pixmap_has_a_gaussian_edge_and_is_cached():
    from PySide6.QtWidgets import QApplication

    from pilcrow_qt.widgets.composer import _shadow_pixmap

    QApplication.instance() or QApplication([])
    pm = _shadow_pixmap(200, 40, 24, 0.5)
    assert pm.width() == 200 + 48 and pm.height() == 40 + 48
    img = pm.toImage()
    mid = img.height() // 2
    # darkest in the middle of the box, fading to nothing past the blur
    alphas = [img.pixelColor(x, mid).alpha() for x in (0, 6, 12, 24, 36, 124)]
    assert alphas[0] < 4 and alphas[-1] >= 110  # 0.5 × the 40px box's own vertical profile (0.9)
    assert all(a <= b for a, b in zip(alphas, alphas[1:]))
    assert _shadow_pixmap(200, 40, 24, 0.5) is pm


def test_color_mix_interpolates_premultiplied():
    from pilcrow_qt.widgets.composer import _color_mix

    # color-mix(in srgb, plan 50%, hair-2): the browser reports color(srgb .306 .766 .593 / .72)... for the
    # 65% focus ring; the 50% mix carries half the plan's alpha plus half the hairline's
    c = _color_mix("#3bbd8c", 0.65, "rgba(255, 255, 255, 0.2)")
    assert abs(c.alphaF() - 0.72) < 0.01
    assert abs(c.redF() - 0.306) < 0.02 and abs(c.greenF() - 0.766) < 0.02


# ---------------------------------------------------------------- the card


def test_geometry_matches_the_web(app_client):
    from pilcrow_qt.widgets.composer import FIELD_MIN, column_width

    c = app_client
    v = _open(c)
    comp = v.composer
    c.sleep(100)
    col = column_width(comp.width())
    card = comp.box.card_rect()
    assert int(card.width()) == col
    assert comp.queue.width() == col
    assert int(card.height()) == 94  # 50px field + 44px bar
    assert comp.field.height() == FIELD_MIN
    assert comp.bar.height() == 44
    assert comp.mode_btn.height() == 30 and comp.attach_btn.width() == 30
    assert comp.send_btn.width() == 32
    assert comp.foot.height() == 18
    # the foot sits 6px under the card, inside the box's bottom pad
    card_bottom = comp.box_host.y() + comp.box.y() + card.bottom()
    assert abs(comp.foot.y() - (card_bottom + 6)) <= 1
    assert comp.edit.font().pixelSize() == 15


def test_focus_brightens_the_ring_and_shows_the_hints(app_client):
    c = app_client
    v = _open(c)
    comp = v.composer
    comp.edit.setFocus()
    c.sleep(50)
    assert comp.box.focused
    c.wait_until(lambda: comp.foot.shown and comp.foot.opacity.value > 0.99, 3, "foot in")
    assert comp.foot.text() == "↵ to send · Shift + ↵ for a new line · @ files · / commands"
    c.wait_until(lambda: comp.box.focus_t.value > 0.99, 3, "focus ring")
    ring_focus, halo_focus = comp.box.ring_colors(1.0)
    ring_idle, halo_idle = comp.box.ring_colors(0.0)
    assert ring_focus.alpha() > ring_idle.alpha()  # hair-3 over hair-2
    assert halo_focus.alpha() > 0 and halo_idle.alpha() == 0  # the orange 7% halo
    # typing hides the hints; clearing them brings them back
    comp.insert("hello", True)
    c.wait_until(lambda: not comp.foot.shown, 3, "foot out")
    assert comp.foot.text() == ""
    comp.insert("", True)
    c.wait_until(lambda: comp.foot.shown, 3, "foot back")
    # focus elsewhere: the ring settles back
    c.win.setFocus()
    comp.edit.clearFocus()
    c.sleep(50)
    assert not comp.box.focused
    c.wait_until(lambda: comp.box.focus_t.value < 0.01 and not comp.foot.shown, 3, "idle again")


def test_mode_tints_the_ring(app_client):
    c = app_client
    v = _open(c)
    comp = v.composer
    sid = v.sid
    c.store.set_mode(sid, "plan")
    c.wait_until(lambda: comp.box._tone == "plan", 5, "plan tone")
    ring, halo = comp.box.ring_colors(1.0)
    assert ring.green() > ring.red() and halo.green() > halo.red()
    assert comp.mode_btn.text_lb.text() == "Plan mode"
    c.store.set_mode(sid, "bypassPermissions")
    c.wait_until(lambda: comp.box._tone == "err", 5, "err tone")
    ring, _h = comp.box.ring_colors(0.0)
    assert ring.red() > ring.green()
    c.store.set_mode(sid, "default")
    c.wait_until(lambda: comp.box._tone == "", 5, "default tone")


def test_field_grows_one_line_at_a_time_and_settles_after_a_send(app_client):
    c = app_client
    v = _open(c)
    comp = v.composer
    c.call("mock.pace", {"ms": 0})
    one = comp.edit.height()
    assert one == 30
    comp.insert("one\ntwo\nthree\nfour", True)
    c.sleep(50)
    assert comp.edit.height() == 90  # 4 × 22.475, like the browser's 110px textarea less its padding
    assert int(comp.box.card_rect().height()) == 154
    comp.edit.setFocus()
    comp.submit()
    c.wait_until(lambda: comp.edit.toPlainText() == "", 5, "sent")
    c.wait_until(lambda: comp.edit.height() == 30, 3, "settled back")
    _idle(c, v.sid)


def test_send_button_states(app_client):
    c = app_client
    v = _open(c)
    comp = v.composer
    sid = v.sid
    assert comp.send_btn.state == "idle" and not comp.send_btn.isEnabled()
    comp.insert("x", True)
    assert comp.send_btn.state == "ready" and comp.send_btn.isEnabled()
    c.wait_until(lambda: comp.send_btn.ready_t.value > 0.99, 3, "entrance spring")
    comp.insert("", True)
    c.call("mock.pace", {"ms": 150})
    c.store.send_message(sid, "tell me something slow")
    c.wait_until(lambda: c.store.rt[sid].busy, 10, "busy")
    c.wait_until(lambda: comp.send_btn.state == "stop", 5, "stop")
    c.wait_until(lambda: comp.send_btn.stop_t.value > 0.99, 3, "stop morph")
    assert comp.foot.busy and comp.foot.shown
    assert comp.foot.text() == "↵ queues for after this step · Ctrl Shift ↵ interrupts and sends now · Esc to stop"
    assert comp.edit.placeholderText() == "Queue a message for after this step…"
    comp.send_btn.click()  # stop
    _idle(c, sid)
    assert comp.send_btn.state == "idle"
    c.call("mock.pace", {"ms": 0})


# ---------------------------------------------------------------- popups and menus


def test_suggest_popup_spans_the_column_above_the_card(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt.widgets.composer import column_width

    c = app_client
    v = _open(c)
    comp = v.composer
    comp.edit.setFocus()
    QTest.keyClicks(comp.edit, "/")
    c.wait_until(lambda: comp.popup.isVisible() and comp.suggestions, 5, "popup")
    c.wait_until(lambda: comp.popup.pop_t.value > 0.99, 3, "popped")
    panel = comp.popup.panel_rect()
    assert int(panel.width()) == min(column_width(comp.width() + 40), comp.width() - 40)
    assert comp.popup.head.text() == "Commands"
    assert comp.popup.total_height() == 6 + 27 + 32 * len(comp.suggestions) + 6
    # the panel's bottom is 2px above the card
    host = comp.popup.parentWidget()
    card_top = comp.mapTo(host, comp.box_host.pos()).y() + comp.box.y() + 6
    panel_bottom = comp.popup.y() + panel.bottom()
    assert abs(card_top - 2 - panel_bottom) <= 1
    QTest.keyClick(comp.edit, Qt.Key.Key_Down)
    assert comp.popup.index == 1
    QTest.keyClick(comp.edit, Qt.Key.Key_Escape)
    assert not comp.popup.isVisible()
    comp.edit.clear()


def test_mode_menu_opens_above_the_chip_and_picks(app_client):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    c = app_client
    v = _open(c)
    comp = v.composer
    sid = v.sid
    comp.mode_btn.click()
    c.wait_until(lambda: comp.menu.is_open, 3, "menu")
    assert comp.mode_btn.is_open
    labels = [it.get("label") for it in comp.menu.items]
    assert labels == ["Ask before edits", "Edit automatically", "Plan mode", "Auto", "Bypass permissions"]
    assert comp.menu.items[0]["checked"] and comp.menu.items[-1]["danger"]
    panel = comp.menu.panel_rect()
    assert int(panel.width()) == 372
    assert int(panel.height()) == 5 + 5 * 49 + 4 + 5
    host = comp.menu.parentWidget()
    anchor = comp.mode_btn.mapTo(host, QPoint(0, 0))
    assert comp.menu.x() + int(panel.x()) == anchor.x()
    assert comp.menu.y() + int(panel.bottom()) == anchor.y() - 6
    # keyboard: down twice, Enter picks Plan mode
    QTest.keyClick(c.win, Qt.Key.Key_Down)
    QTest.keyClick(c.win, Qt.Key.Key_Down)
    QTest.keyClick(c.win, Qt.Key.Key_Down)
    assert comp.menu.active == 2
    QTest.keyClick(c.win, Qt.Key.Key_Return)
    assert not comp.menu.is_open and not comp.mode_btn.is_open
    c.wait_until(lambda: c.store.rt[sid].get("mode") == "plan", 5, "plan picked")
    # the chip toggles it closed again; Escape closes too
    comp.mode_btn.click()
    c.wait_until(lambda: comp.menu.is_open, 3, "menu again")
    comp.mode_btn.click()
    assert not comp.menu.is_open
    comp.mode_btn.click()
    c.wait_until(lambda: comp.menu.is_open, 3, "menu once more")
    QTest.keyClick(c.win, Qt.Key.Key_Escape)
    assert not comp.menu.is_open
    c.store.set_mode(sid, "default")
    c.wait_until(lambda: c.store.rt[sid].get("mode") == "default", 5, "default")


def test_model_menu_aligns_right_and_lists_the_gateway(app_client):
    from PySide6.QtCore import QPoint

    c = app_client
    v = _open(c)
    comp = v.composer
    comp.model_btn.click()
    c.wait_until(lambda: comp.menu.is_open and len(comp.menu.items) >= 4, 5, "model menu")
    c.wait_until(lambda: any(it.get("mono") for it in comp.menu.items), 5, "gateway models")
    headings = [it["heading"] for it in comp.menu.items if it.get("heading")]
    assert headings[0] == "Model" and headings[1].startswith("On ")
    panel = comp.menu.panel_rect()
    assert int(panel.width()) == 320
    host = comp.menu.parentWidget()
    anchor = comp.model_btn.mapTo(host, QPoint(0, 0))
    assert comp.menu.x() + int(panel.right()) == anchor.x() + comp.model_btn.width()  # QRectF.right() is x + w
    comp.menu.close_menu()
    assert not comp.menu.is_open


# ---------------------------------------------------------------- attachments, drag, queue


def test_attachments_show_as_chips(app_client):
    from PySide6.QtGui import QImage

    from pilcrow_qt.widgets.composer import FileChip, ImageChip

    c = app_client
    v = _open(c)
    comp = v.composer
    img = QImage(64, 64, QImage.Format.Format_ARGB32)
    img.fill(0xFF3366FF)
    comp.add_image(img, "pasted image")
    comp.files.append({"name": "notes.txt", "text": "hello"})
    comp._render_attachments()
    c.sleep(50)
    assert comp.attach_row.isVisible()
    chips_i, chips_f = c.find(ImageChip, comp.attach_row), c.find(FileChip, comp.attach_row)
    assert len(chips_i) == 1 and chips_i[0].size().width() == 60
    assert len(chips_f) == 1 and chips_f[0].height() == 30 and chips_f[0].name == "notes.txt"
    assert comp.send_btn.state == "ready"
    chips_f[0].x.click()
    chips_i[0].x.click()
    c.sleep(50)
    assert not comp.images and not comp.files and not comp.attach_row.isVisible()
    assert comp.send_btn.state == "idle"


def test_dragging_rings_the_card_and_shows_the_veil(app_client):
    c = app_client
    v = _open(c)
    comp = v.composer
    comp.set_dragging(True)
    c.sleep(50)
    assert comp.drop_hint.isVisible() and comp.box._dragging
    assert comp.drop_hint.x() == 20 and comp.drop_hint.width() == comp.width() - 40
    comp.set_dragging(False)
    assert not comp.drop_hint.isVisible() and not comp.box._dragging


def test_queue_tray_above_the_card(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt.widgets.composer import QueueItem

    c = app_client
    v = _open(c)
    comp = v.composer
    sid = v.sid
    c.call("mock.pace", {"ms": 150})
    c.store.send_message(sid, "tell me something slow")
    c.wait_until(lambda: c.store.rt[sid].busy, 10, "busy")
    comp.edit.setFocus()
    QTest.keyClicks(comp.edit, "and then this")
    QTest.keyClick(comp.edit, Qt.Key.Key_Return)
    c.wait_until(lambda: comp.queue.isVisible(), 5, "queue tray")
    assert comp.queue.width() == comp.col
    items = c.find(QueueItem, comp.queue)
    assert len(items) == 1 and items[0].text_btn.text() == "and then this"
    assert not items[0].now.isVisible()  # the actions wait for the pointer
    assert comp.queue_host.y() + comp.queue.y() + comp.queue.height() + 8 == comp.box_host.y() + comp.box.y() + 6
    c.store.interrupt(sid)
    _idle(c, sid)
    c.call("mock.pace", {"ms": 0})


# ---------------------------------------------------------------- drafts, history, send key


def test_drafts_history_and_the_send_key_setting(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt.widgets import composer as mod

    c = app_client
    v = _open(c)
    comp = v.composer
    sid = v.sid
    c.call("mock.pace", {"ms": 0})
    comp.edit.setFocus()
    QTest.keyClicks(comp.edit, "first thing")
    QTest.keyClick(comp.edit, Qt.Key.Key_Return)
    c.wait_until(lambda: comp.edit.toPlainText() == "", 5, "sent")
    _idle(c, sid)
    assert mod._histories[sid][0] == "first thing"
    QTest.keyClick(comp.edit, Qt.Key.Key_Up)
    assert comp.edit.toPlainText() == "first thing"
    QTest.keyClick(comp.edit, Qt.Key.Key_Down)
    assert comp.edit.toPlainText() == ""
    # a draft survives leaving the session
    QTest.keyClicks(comp.edit, "half written")
    assert mod._drafts[sid] == "half written"
    _open(c, "sess-empty")
    v = _open(c, sid)
    comp = v.composer
    assert comp.edit.toPlainText() == "half written"
    comp.edit.clear()
    # Ctrl+Enter sends when the setting says so; Enter then breaks the line
    cfg = dict(c.store.config or {})
    c.store.config = {**cfg, "sendKey": "mod-enter"}
    comp.refresh()
    assert not comp.send_with_enter and comp.send_btn.toolTip() == "Send (Ctrl+Enter)"
    comp.edit.setFocus()
    QTest.keyClicks(comp.edit, "a")
    QTest.keyClick(comp.edit, Qt.Key.Key_Return)
    assert comp.edit.toPlainText() == "a\n"
    QTest.keyClick(comp.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    c.wait_until(lambda: comp.edit.toPlainText() == "", 5, "sent with ctrl")
    _idle(c, sid)
    c.store.config = cfg
    comp.refresh()


def test_screenshot(app_client):
    c = app_client
    v = _open(c)
    v.composer.edit.setFocus()
    c.sleep(300)
    assert c.shot("composer").exists()
