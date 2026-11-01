"""The context figure: what the ring and the Info panel say, including right after a compaction, when the
size is not known until the next reply (server/usage.js; the mock plays the same events)."""

from __future__ import annotations

from harness import app_client, mock_server

MOCK_PORT = 5435


def test_ring_follows_the_figure_and_says_so_after_a_compact(app_client):
    c = app_client
    c.wait_until(lambda: c.store.config is not None and c.store.sessions, 10, "sessions")
    c.call("mock.pace", {"ms": 0})
    sid = c.store.sessions[0]["id"]
    c.open(sid)
    v = c.win.session_view
    btn = v.composer.ctx_btn
    c.wait_until(lambda: btn.isVisible() and btn.ring.total == 200000, 5, "the ring")
    assert btn.ring.used == 21280
    assert btn.toolTip().startswith("Context 11% used · 21k of 200k")
    # a compaction: the figure is unknown until the next reply, and the ring says so instead of showing the old number
    c.call("mock.pace", {"ms": 120})  # the mock holds the compacted state for a moment before its reply
    c.call("session.send", {"id": sid, "text": "compact this conversation"})
    c.wait_until(lambda: (c.store.rt[sid].st.get("usage") or {}).get("afterCompact") is True, 10, "compacted")
    c.sleep(100)
    assert btn.ring.used == 0 and btn.ring.total == 200000
    assert btn.toolTip() == "Context compacted — its size is measured at the next reply"
    # the reply after the compaction carries the new size (the mock's reply adds its 900)
    c.wait_until(lambda: c.store.rt[sid].status == "idle" and btn.ring.used == 14900, 15, "the next reply")
    assert btn.toolTip().startswith("Context 7% used · 15k of 200k")
    # the Info panel reads the same figure
    c.store.set_ui(panel=True, panel_tab="info")
    c.wait_until(lambda: c.win.panel is not None and c.win.panel.page("info") is not None, 5, "info tab")
    page = c.win.panel.page("info")
    c.wait_until(lambda: "15k" in page.gauge_line.text(), 5, "the gauge line")
    assert "of 200k tokens" in page.gauge_line.text()
