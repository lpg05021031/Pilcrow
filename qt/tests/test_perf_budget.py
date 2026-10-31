"""The performance budgets of qt/PERF.md §5, asserted with headroom on a short run of the bench
(120 heavy turns, 200 mixed). The run takes a few minutes and needs the mock, so it is skipped
unless ``PILCROW_PERF=1`` (``npm run release`` sets it).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))

pytestmark = pytest.mark.skipif(os.environ.get("PILCROW_PERF") != "1", reason="set PILCROW_PERF=1 to run the budgets")

MOCK_PORT = 5392


@pytest.fixture(scope="module")
def results():
    from bench.scenarios import SCENARIOS, Ctx
    from harness import Client, start_mock

    os.environ.setdefault("PILCROW_MOTION", "1")
    mock = start_mock(MOCK_PORT, 0)
    out = {}
    try:
        c = Client(MOCK_PORT)
        c.wait_until(lambda: c.store.sessions_loaded, 20, "sessions")
        c.call("mock.generate", {"id": "sess-heavy", "turns": 120, "shape": "heavy"}, timeout=120)
        c.call("mock.generate", {"id": "sess-mixed", "turns": 200, "shape": "mixed"}, timeout=120)
        c.wait_until(lambda: any(s.get("id") == "sess-heavy" for s in c.store.sessions), 20, "list")
        ctx = Ctx(c, "sess-heavy", "sess-mixed")
        for name in (
            "fold_toggle",
            "tool_toggle",
            "hover_sweep",
            "streaming_scrolled_up",
            "scroll_wheel",
            "switch_session",
            "find",
            "view_mode_cycle",
        ):
            out[name] = SCENARIOS[name](ctx)
            c.wait_until(lambda: not (c.store.rt.get("sess-heavy") and c.store.rt["sess-heavy"].busy), 90, "idle")
        c.close()
    finally:
        mock.kill()
    return out


def test_toggles_make_no_layouts_and_do_not_flash(results):
    for name, key in (("fold_toggle", "summary"), ("tool_toggle", "summary")):
        s = results[name][key]
        assert s["layout_per_toggle"] <= 2, (name, s)
        assert s["flashes"] == 0, (name, s)
        assert s["stall_max_ms"] <= 60, (name, s)
        assert s["fps_min_animated"] >= 40, (name, s)


def test_hover_and_scrolled_up_streaming_move_nothing(results):
    assert results["hover_sweep"]["layout"] == 0
    up = results["streaming_scrolled_up"]
    assert up["shift"]["unexpected_px"] == 0, up["shift"]
    assert up["stall"]["p95"] <= 60, up["stall"]


def test_fling_paints_what_it_shows(results):
    w = results["scroll_wheel"]["up_filmed"]
    assert w["flashes"] == 0, w
    assert w["stall"]["p95"] <= 40, w["stall"]
    assert w["cold_paints"] <= w["stall"]["n"] // 10, w  # placeholders in at most a tenth of the frames


def test_switch_find_and_view_mode_are_quick(results):
    sw = results["switch_session"]
    assert sw["widgets_made_per_switch"] <= 60, sw
    assert sw["switch"]["p50"] <= 600, sw
    assert results["find"]["search_ms"] <= 1500, results["find"]
    assert results["find"]["widgets"] == 0
    for mode, r in results["view_mode_cycle"].items():
        if isinstance(r, dict):
            assert r["to_paint_ms"] <= 1200, (mode, r)
            assert r["widgets_made"] == 0, (mode, r)
