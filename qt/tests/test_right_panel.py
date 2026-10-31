"""The side panel's shared pieces against panels.css / base.css: text at the right size and weight,
the button family, the segmented control, headings, empty states, and the tab strip's ink."""

from __future__ import annotations

from harness import app_client, mock_server  # noqa: F401
from PySide6.QtGui import QFontInfo, QFontMetrics

MOCK_PORT = 5393
SID = "sess-login"


def _font(w):
    w.ensurePolished()
    return QFontInfo(w.font())


def test_lbl_sets_size_weight_and_leading(app_client):
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.right_panel import lbl

    h = lbl("To-do list", theme.FS_H, 600)
    h.show()
    fi = _font(h)
    assert fi.pixelSize() == 15 and fi.weight() == 600 and fi.styleName() == "SemiBold"
    assert h.minimumHeight() == 22  # 15px × the body's 1.45 line height
    small = lbl("x", theme.FS_SMALL, tone="faint")
    small.show()
    assert _font(small).pixelSize() == 12
    assert small.palette().color(small.foregroundRole()).name() == theme.tokens().text_3, "tone faint = text-3"
    mono = lbl("sess", 11.5, mono=True)
    mono.show()
    assert _font(mono).family() == theme.font_mono().family() and _font(mono).pixelSize() == 12
    wrapped = lbl("a hint that wraps", theme.FS_SMALL, wrap=True, line_height=1.5)
    assert wrapped.text() == "a hint that wraps"  # plain words, even though it renders as rich text
    wrapped.set_html("<s>done</s>")
    assert wrapped.text() == "done"


def test_btn_metrics_follow_the_css(app_client):
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.right_panel import Btn

    sm = Btn("Review", icon="eye", kind="ghost", size="sm")
    fm = QFontMetrics(theme.font_ui(theme.FS_SMALL, 500))
    assert sm.sizeHint().height() == 26  # .btn.sm
    assert sm.sizeHint().width() == 10 + 13 + 6 + fm.horizontalAdvance("Review") + 10  # padding, icon, gap, text
    xs = Btn("Restore to here", icon="rotate-ccw", size="xs", icon_size=11)
    assert xs.sizeHint().height() == 24  # .btn.xs
    big = Btn("Open in browser", kind="primary", size="")
    assert big.sizeHint().height() == 30  # .btn
    link = Btn("Manage", kind="link")
    assert link.sizeHint().height() == 18 and link.text() == "Manage"
    foot = Btn("Log · 2", icon="chevron-right", kind="foot", icon_size=12)
    assert foot.sizeHint().height() == 22
    foot.set_icon_name("chevron-down")
    assert foot.icon_name == "chevron-down"
    danger = Btn("Stop", icon="circle-stop", kind="danger-text", size="sm")
    _bg, fg, _alpha, _line = danger._colors()
    assert fg.name() == theme.tokens().err
    filled = Btn("Delete", kind="danger")
    assert filled._colors()[0].name() == theme.tokens().err and filled.property("kind") == "danger"


def test_seg_picks_and_disables(app_client):
    from pilcrow_qt.widgets.panels.right_panel import Seg

    seg = Seg(
        [
            {"value": "unified", "icon": "rows-2", "title": "Unified"},
            {"value": "split", "icon": "columns-2", "title": "Side by side"},
        ]
    )
    assert seg.width() == 2 + 27 * 2 + 2 and seg.height() == 26  # .seg.sm: 2px padding, 22px buttons + 7px sides
    got = []
    seg.changed.connect(got.append)
    assert seg._index_at(2 + 27 + 5) == 1
    seg.set_disabled("split", True, "Side by side (widen the panel to use it)")
    assert "split" in seg.disabled and seg.options[1]["title"].startswith("Side by side (widen")
    seg.set_value("split")
    assert seg.value == "split"


def test_rtl_display_mirrors_the_web_dir(app_client):
    from pilcrow_qt.widgets.panels.right_panel import rtl_display

    assert rtl_display("./web/src/lib") == "web/src/lib"
    assert rtl_display("web/src/lib") == "web/src/lib"
    assert rtl_display("") == ""


def test_section_and_empty_state_typography(app_client):
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.right_panel import EmptyState, Section

    s = Section("Context window")
    s.show()
    assert _font(s.title).pixelSize() == 15 and _font(s.title).weight() == 600
    faint = s.head_faint("2 of 4 done")
    faint.show()
    assert _font(faint).pixelSize() == 12
    assert faint.palette().color(faint.foregroundRole()).name() == theme.tokens().text_3
    assert s.body.contentsMargins().top() == 10 and s.body.contentsMargins().bottom() == 18  # .ps padding and gap
    e = EmptyState("history", "Nothing on the timeline yet.", "Each step becomes a point here.")
    e.resize(400, 200)
    e.show()
    assert e.text.text() == "Nothing on the timeline yet." and e.hint.isVisible()
    assert e.layout().contentsMargins().left() == 28 and e.layout().contentsMargins().top() == 40  # .panel .empty
    assert e.text.width() > 300  # the sentence spans the block; it is centred by its alignment, not squeezed
    e.set_icon("")
    assert not e.ico.isVisible()
    _ = theme
    for w in (s, faint, e):  # top-level windows left open would sit over the next test's window
        w.close()
        w.deleteLater()


def test_panel_sits_on_the_canvas_and_the_ink_follows(app_client):
    c = app_client
    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.right_panel import TabStrip

    c.open(SID)
    c.store.set_ui(panel=True, panel_tab="tasks", panel_w=560)
    c.wait_until(lambda: c.win.panel is not None and c.win.panel.page("tasks") is not None, 5, "tasks tab")
    panel = c.win.panel
    # the panel paints its background (a stylesheet here would re-polish every tab on a theme switch)
    assert panel.styleSheet() == ""
    img = panel.grab().toImage()
    assert img.pixelColor(2, panel.height() - 3).name() == theme.tokens().canvas
    strip = c.find(TabStrip)[0]
    assert strip.height() == theme.HEADER_H and strip.close.width() == 24  # .icon-btn.sm
    c.wait_until(lambda: strip._ink is not None, 3, "ink")
    b = strip.buttons["tasks"]
    c.wait_until(lambda: strip._ink.x() == b.x(), 4, "the ink to settle")  # it springs there with motion on
    assert strip._ink.height() == 2 and strip._ink.x() == b.x() and strip._ink.width() == b.width()
    assert strip.narrow and b.shows_label and not strip.buttons["files"].shows_label
    assert strip.buttons["files"].width() == 34 and b.sizeHint().width() > 34
    c.store.set_ui(panel_tab="info")
    c.wait_until(lambda: strip.active == "info", 3, "info")
    c.wait_until(lambda: strip._ink.x() == strip.buttons["info"].x(), 4, "the ink to settle under Session")
