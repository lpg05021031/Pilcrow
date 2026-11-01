"""The performance budgets of qt/PERF.md §5, asserted on a run of the bench (120 heavy turns, 200 mixed).

The normal ``pytest`` pass asserts what does not move with the machine's load: the counts that must
be zero at zero (layout passes, flashes, unexpected shift, widgets made, scroll corrections) and the
medians of the client's own work — the main thread's CPU time in a turn of the event loop, a step
of a drag or a resize, a switch to its first paint — each at 1.5× its target. Wall-clock stalls, a
p95 or a longest turn are a property of the machine as much as of the client (another process on
the machine lengthens them), so those budgets run in the full set (``PILCROW_PERF=1``, which
``npm run release`` sets) with the scenarios that take longer.
``python qt/tests/bench/run.py`` prints every number.
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
    "view_mode_cycle",
    "scroll_wheel",
    "scroll_drag",
)
MORE = (
    "tool_toggle",
    "typing",
    "streaming_scrolled_up",
    "select_drag",
    "panel_tabs",
    "tasks_fold_toggle",
    "idle",
)

# medians of the main thread's CPU time, in every run (1.5× the target)
TURN_CPU_P50_MS = 6  # target 4: half the turns of the loop while something moves
TO_PAINT_CPU_P50_MS = 150  # target 100: the median of six view mode and six theme switches
RESIZE_STEP_CPU_P50_MS = 25  # target 16: one frame a step
DRAG_STEP_CPU_P50_MS = 25  # scrollbar drag, a screen a step, target 16: every row on screen is new
# single timings far inside their budgets, in every run
SWITCH_P50_MS = 45  # target 30 (measured ≈ 16)
FIRST_PAINT_MS = 150  # target 150 after the snapshot is modelled (measured ≈ 6)
FIND_MS = 150  # target 100 over the whole conversation (measured ≈ 19)
TO_PAINT_P50_MS = 150  # target 100, wall clock: the full set
# maxima and p95s, in the full set
STALL_P95_MS = 12  # target 8
STALL_MAX_MS = 36  # target 24 while streaming
TOGGLE_STALL_MAX_MS = 15  # target 10 for a fold or a card
FPS_MIN = 45  # target 55
MODELLING_STALL_MS = 75  # target 50: no long task while a snapshot is modelled
RESIZE_STEP_P95_MS = 50  # target 33, two frames a step; none over 50
DRAG_STEP_P95_MS = 50  # target 33: every row on screen is new each step (measured 24-36 on a quiet machine)
DRAG_STEP_MAX_MS = 150  # target 100: one heavy row measured past the step's deadline
DRAG_COLD_PAINTS = 30  # placeholders over the 81 steps, target 20
SEAM_STEP_P95_MS = 70  # the panel seam dragged 12px a step, target 45: both panes reflow (measured 33-38)
TAB_FIRST_TURN_MS = 100  # the slowest tab's first show, target 66
SELECT_PX_PER_MOVE = 60_000  # a selection drag repaints the rows it changed (the viewport is 460k)
FOLD_STALL_MAX_MS = 25  # a Collapse in the Tasks tab, target 16
IDLE_BUSY_CPU_PCT = 4.5  # target 3: a busy-looking conversation, the clock at 20 Hz


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


# ---------------------------------------------------------------- counts and medians: every run


def test_opening_paints_at_once_and_makes_no_widgets(results):
    for name in ("heavy", "mixed"):
        r = results["open_cold"][name]
        assert r["first_paint_ms"] <= FIRST_PAINT_MS, (name, r)
        assert r["widgets"] == 0, (name, r)


def test_a_fold_toggle_makes_no_layouts_does_not_flash_and_moves_only_what_it_animates(results):
    s = results["fold_toggle"]["summary"]
    assert s["layout_per_toggle"] <= 1 and s["flashes"] == 0, s
    # once its body is built: off the live end no scroll correction, and the row below moves by
    # what the fold grew (at the end the view follows the growing fold, the easing)
    assert s["scroll_sets_off_end"] == 0 and s["below_unexplained_px"] == 0, s
    assert s["turn_cpu_p50_ms"] <= TURN_CPU_P50_MS, s


def test_streaming_while_following_costs_the_paragraph(results):
    r = results["streaming_follow"]
    assert r["layout_per_s"] == 0, r
    assert r["transcript"]["widgets_made"] == 0, r["transcript"]
    assert r["filmed"]["flashes"] == 0, r["filmed"]
    assert r["stall"]["cpu_p50"] <= TURN_CPU_P50_MS, r["stall"]


def test_hover_moves_nothing(results):
    r = results["hover_sweep"]
    assert r["layout"] == 0 and r["widgets_made"] == 0, r
    assert r["stall"]["cpu_p50"] <= TURN_CPU_P50_MS, r["stall"]


def test_resizing_the_window_reflows_the_conversation_in_place(results):
    r = results["resize_window"]
    assert r["flashes"] == 0 and r["layout"] == 0 and r["widgets_made"] == 0, r
    assert r["step_cpu"]["p50"] <= RESIZE_STEP_CPU_P50_MS, r["step_cpu"]


def test_switching_makes_nothing_and_is_quick(results):
    r = results["switch_session"]
    assert r["widgets_made_per_switch"] == 0, r
    assert r["switch"]["p50"] <= SWITCH_P50_MS, r
    assert r["rss_growth_mb"] <= 2, r


def test_find_is_quick(results):
    f = results["find"]
    assert f["search_ms"] <= FIND_MS and f["widgets"] == 0, f


def test_the_theme_and_the_view_mode_switch_make_nothing_and_are_quick(results):
    for name in ("theme_toggle", "view_mode_cycle"):
        r = results[name]
        assert r["widgets_made"] == 0, (name, r["switches"])
        assert r["to_paint_cpu"]["p50"] <= TO_PAINT_CPU_P50_MS, (name, r["to_paint_cpu"])
    assert results["theme_toggle"]["retained"] >= 5, results["theme_toggle"]["retained"]


def test_a_fling_paints_what_it_shows(results):
    w = results["scroll_wheel"]["up_filmed"]
    assert w["flashes"] == 0 and w["shift"]["unexpected_px"] == 0, w
    assert w["stall"]["cpu_p50"] <= TURN_CPU_P50_MS, w["stall"]


def test_dragging_the_scrollbar_makes_nothing(results):
    d = results["scroll_drag"]
    assert d["widgets_made"] == 0, d
    assert d["step_cpu"]["p50"] <= DRAG_STEP_CPU_P50_MS, d["step_cpu"]


# ---------------------------------------------------------------- maxima, p95s and the long scenarios


@full_only
def test_no_long_turns_while_opening_toggling_or_streaming(results):
    for name in ("heavy", "mixed"):
        r = results["open_cold"][name]
        assert r["modelling_stall_max_ms"] <= MODELLING_STALL_MS, (name, r)
    for name in ("fold_toggle", "tool_toggle"):
        s = results[name]["summary"]
        assert s["layout_per_toggle"] <= 1 and s["flashes"] == 0, (name, s)
        assert s["scroll_sets_off_end"] == 0 and s["below_unexplained_px"] == 0, (name, s)
        assert s["stall_max_ms"] <= TOGGLE_STALL_MAX_MS, (name, s)
        assert s["fps_min_animated"] >= FPS_MIN, (name, s)
    r = results["streaming_follow"]
    assert r["stall"]["p95"] <= STALL_P95_MS and r["stall"]["max"] <= STALL_MAX_MS, r["stall"]
    h = results["hover_sweep"]
    assert h["stall"]["p95"] <= STALL_P95_MS, h["stall"]


@full_only
def test_resize_steps_stay_under_two_frames(results):
    r = results["resize_window"]
    assert r["over_50ms"] == 0 and r["step"]["p95"] <= RESIZE_STEP_P95_MS, r


@full_only
def test_scrolled_up_streaming_and_typing_move_nothing(results):
    up = results["streaming_scrolled_up"]
    assert up["shift"]["unexpected_px"] == 0 and up["scroll_sets"] == 0, up
    assert up["stall"]["p95"] <= STALL_P95_MS, up["stall"]
    t = results["typing"]
    assert t["layout"] == 0 and t["shift"]["unexpected_px"] == 0, t
    assert t["stall"]["p95"] <= STALL_P95_MS, t["stall"]


@full_only
def test_a_fling_and_a_scrollbar_drag_stay_under_their_frames(results):
    w = results["scroll_wheel"]["up_filmed"]
    assert w["stall"]["p95"] <= STALL_P95_MS, w["stall"]
    assert w["stall"]["over_16ms"] <= max(1, w["stall"]["n"] // 100), w["stall"]  # ≤ 1 frame in 100
    assert w["cold_paints"] <= w["stall"]["n"] // 10, w  # placeholders in at most a tenth of the frames
    d = results["scroll_drag"]
    assert d["step"]["p95"] <= DRAG_STEP_P95_MS and d["step"]["max"] <= DRAG_STEP_MAX_MS, d["step"]
    assert d["cold_paints"] <= DRAG_COLD_PAINTS, d


@full_only
def test_the_switches_have_no_slow_one(results):
    for name in ("theme_toggle", "view_mode_cycle"):
        r = results[name]
        assert r["to_paint"]["p50"] <= TO_PAINT_P50_MS, (name, r["to_paint"])
        assert r["to_paint"]["max"] <= 1.5 * TO_PAINT_P50_MS, (name, r["to_paint"])


@full_only
def test_the_seam_and_a_tab_stay_under_their_budgets(results):
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
