"""The performance budgets of qt/PERF.md §5, asserted on a run of the bench (120 heavy turns, 200 mixed).

Each budget is the §5 target with headroom for a busy machine: costs at 1.5× the target, a frame-rate
floor of 45 fps for the 55 fps target, counts that must be zero at zero.

The normal ``pytest`` pass runs the quick set (about a minute offscreen): opening, a fold, a streamed
turn, a window resize, switching, the theme, find and a pointer sweep. ``PILCROW_PERF=1`` adds the rest
(``npm run release`` sets it). ``python qt/tests/bench/run.py`` prints every number.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FULL = os.environ.get("PILCROW_PERF") == "1"
QUICK = (
    "open_cold",
    "fold_toggle",
    "hover_sweep",
    "streaming_follow",
    "resize_window",
    "switch_session",
    "find",
    "theme_toggle",
)
MORE = (
    "tool_toggle",
    "typing",
    "streaming_scrolled_up",
    "scroll_wheel",
    "scroll_drag",
    "select_drag",
    "view_mode_cycle",
    "panel_tabs",
    "tasks_fold_toggle",
    "idle",
)

# the budgets, against the targets of PERF.md §5
STALL_P95_MS = 12  # target 8
STALL_MAX_MS = 24  # target 16 while streaming
TOGGLE_STALL_MAX_MS = 15  # target 10 for a fold or a card
FPS_MIN = 45  # target 55
TO_PAINT_MS = 150  # target 100: view mode cycle, theme switch
SWITCH_P50_MS = 45  # target 30
FIRST_PAINT_MS = 150  # target 150 after the snapshot is modelled (measured ≈ 4)
MODELLING_STALL_MS = 75  # target 50: no long task while a snapshot is modelled
RESIZE_STEP_P95_MS = 50  # target 33, two frames a step; none over 50
FIND_MS = 150  # target 100 over the whole conversation
DRAG_STEP_P95_MS = 50  # scrollbar drag, a screen a step, target 33 (measured 34)
DRAG_STEP_MAX_MS = 80  # target 50 (measured 51)
DRAG_COLD_PAINTS = 30  # placeholders over the 81 steps (measured 16-19)
SEAM_STEP_P95_MS = 60  # the panel seam dragged 12px a step, target 40 (measured 39)
TAB_FIRST_TURN_MS = 100  # the slowest tab's first show, target 66 (the terminal's, measured 14-66)
SELECT_PX_PER_MOVE = 60_000  # a selection drag repaints the rows it changed (measured 24k; the viewport is 460k)
FOLD_STALL_MAX_MS = 25  # a Collapse in the Tasks tab, target 16 (measured 8)
IDLE_BUSY_CPU_PCT = 4.5  # target 3: a busy-looking conversation, the clock at 20 Hz (measured 2.6-3.2)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """The bench, in a process of its own: what earlier tests left alive (widgets, a panel's tab, the
    store's sessions) would otherwise be timed along with the client."""
    out = tmp_path_factory.mktemp("bench") / "bench.json"
    names = QUICK + (MORE if FULL else ())
    cmd = [sys.executable, str(ROOT / "qt" / "tests" / "bench" / "run.py"), "--heavy", "120", "--mixed", "200"]
    cmd += ["--only", ",".join(names), "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-4000:]
    scenarios = json.loads(out.read_text(encoding="utf-8"))["scenarios"]
    failed = {k: v["error"] for k, v in scenarios.items() if "error" in v}
    assert not failed, (failed, proc.stderr[-4000:])
    return scenarios


full_only = pytest.mark.skipif(not FULL, reason="set PILCROW_PERF=1 for the full budgets")


def test_opening_paints_at_once_and_never_blocks(results):
    for name in ("heavy", "mixed"):
        r = results["open_cold"][name]
        assert r["first_paint_ms"] <= FIRST_PAINT_MS, (name, r)
        assert r["modelling_stall_max_ms"] <= MODELLING_STALL_MS, (name, r)
        assert r["widgets"] == 0, (name, r)


def test_toggles_make_no_layouts_and_do_not_flash(results):
    for name in ("fold_toggle", "tool_toggle"):
        if name not in results:
            continue
        s = results[name]["summary"]
        assert s["layout_per_toggle"] <= 1, (name, s)
        assert s["flashes"] == 0, (name, s)
        assert s["stall_max_ms"] <= TOGGLE_STALL_MAX_MS, (name, s)
        assert s["fps_min_animated"] >= FPS_MIN, (name, s)


def test_streaming_while_following_costs_the_paragraph(results):
    r = results["streaming_follow"]
    assert r["stall"]["p95"] <= STALL_P95_MS, r["stall"]
    assert r["stall"]["max"] <= STALL_MAX_MS, r["stall"]
    assert r["layout_per_s"] == 0, r
    assert r["transcript"]["widgets_made"] == 0, r["transcript"]
    assert r["filmed"]["flashes"] == 0, r["filmed"]


def test_hover_moves_nothing(results):
    r = results["hover_sweep"]
    assert r["layout"] == 0 and r["widgets_made"] == 0, r
    assert r["stall"]["p95"] <= STALL_P95_MS, r["stall"]


def test_resizing_the_window_reflows_without_long_tasks(results):
    r = results["resize_window"]
    assert r["over_50ms"] == 0, r
    assert r["step"]["p95"] <= RESIZE_STEP_P95_MS, r["step"]
    assert r["flashes"] == 0 and r["layout"] == 0 and r["widgets_made"] == 0, r


def test_switching_makes_nothing_and_is_quick(results):
    r = results["switch_session"]
    assert r["widgets_made_per_switch"] == 0, r
    assert r["switch"]["p50"] <= SWITCH_P50_MS, r
    assert r["rss_growth_mb"] <= 2, r


def test_find_and_the_theme_are_quick(results):
    f = results["find"]
    assert f["search_ms"] <= FIND_MS and f["widgets"] == 0, f
    for name, r in results["theme_toggle"].items():
        if isinstance(r, dict):
            assert r["to_paint_ms"] <= TO_PAINT_MS, (name, r)
            assert r["window"]["widgets_made"] == 0, (name, r)


@full_only
def test_scrolled_up_streaming_and_typing_move_nothing(results):
    up = results["streaming_scrolled_up"]
    assert up["shift"]["unexpected_px"] == 0 and up["scroll_sets"] == 0, up
    assert up["stall"]["p95"] <= STALL_P95_MS, up["stall"]
    t = results["typing"]
    assert t["layout"] == 0 and t["shift"]["unexpected_px"] == 0, t
    assert t["stall"]["p95"] <= STALL_P95_MS, t["stall"]


@full_only
def test_fling_paints_what_it_shows(results):
    w = results["scroll_wheel"]["up_filmed"]
    assert w["flashes"] == 0 and w["shift"]["unexpected_px"] == 0, w
    assert w["stall"]["p95"] <= STALL_P95_MS, w["stall"]
    assert w["stall"]["over_16ms"] <= max(1, w["stall"]["n"] // 100), w["stall"]  # ≤ 1 frame in 100
    assert w["cold_paints"] <= w["stall"]["n"] // 10, w  # placeholders in at most a tenth of the frames


@full_only
def test_view_mode_cycle_is_quick(results):
    for mode, r in results["view_mode_cycle"].items():
        if isinstance(r, dict):
            assert r["to_paint_ms"] <= TO_PAINT_MS, (mode, r)
            assert r["widgets_made"] == 0, (mode, r)


@full_only
def test_dragging_the_scrollbar_and_the_seam_and_showing_a_tab_stay_under_their_budgets(results):
    d = results["scroll_drag"]
    assert d["step"]["p95"] <= DRAG_STEP_P95_MS and d["step"]["max"] <= DRAG_STEP_MAX_MS, d["step"]
    assert d["cold_paints"] <= DRAG_COLD_PAINTS and d["widgets_made"] == 0, d
    tabs = results["panel_tabs"]
    assert tabs["drag"]["step"]["p95"] <= SEAM_STEP_P95_MS and tabs["drag"]["layout"] == 0, tabs["drag"]
    slowest = max(t["first_turn_ms"] for t in tabs["tabs"].values())
    assert slowest <= TAB_FIRST_TURN_MS, tabs["tabs"]


@full_only
def test_a_selection_drag_repaints_what_changed_and_a_panel_fold_is_under_a_frame(results):
    s = results["select_drag"]
    assert s["paint_px_per_move"] <= SELECT_PX_PER_MOVE and s["layout"] == 0, s
    f = results["tasks_fold_toggle"]
    assert f["stall_max_ms"] <= FOLD_STALL_MAX_MS, f


@full_only
def test_a_busy_looking_conversation_costs_little_while_nothing_happens(results):
    r = results["idle"]
    assert r["busy_look_cpu_pct"] <= IDLE_BUSY_CPU_PCT, r
