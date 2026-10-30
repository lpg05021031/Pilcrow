"""Print a bench run as a Markdown table, or two runs side by side.

    python qt/tests/bench/report.py test/out/bench.json
    python qt/tests/bench/report.py test/out/bench-baseline.json test/out/bench-canvas.json

One row per headline number of each scenario; with two files, a column per run. The headline
numbers are the ones the targets in qt/PERF.md are written against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Json = Dict[str, Any]


def g(d: Any, *path: str, default: Any = None) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def ms(v: Any) -> str:
    return "" if v is None else f"{v:g} ms"


def n(v: Any) -> str:
    return "" if v is None else f"{v:g}"


def pc(v: Any) -> str:
    return "" if v is None else f"{v:g} %"


# (scenario, label, getter, formatter)
ROWS: List[Tuple[str, str, Callable[[Json], Any], Callable[[Any], str]]] = [
    ("open_cold", "mixed 2000 turns: rows on screen", lambda s: g(s, "mixed", "rows_ms"), ms),
    ("open_cold", "mixed: widgets under the content", lambda s: g(s, "mixed", "widgets"), n),
    ("open_cold", "heavy 600 turns: rows on screen", lambda s: g(s, "heavy", "rows_ms"), ms),
    ("open_cold", "heavy: first paint", lambda s: g(s, "heavy", "first_paint_ms"), ms),
    ("open_cold", "heavy: RSS added", lambda s: g(s, "heavy", "rss_delta_mb"), lambda v: f"{v:g} MB"),
    ("view_mode_cycle", "→ verbose: to first paint", lambda s: g(s, "verbose", "to_paint_ms"), ms),
    ("view_mode_cycle", "→ verbose: widgets made", lambda s: g(s, "verbose", "widgets_made"), n),
    ("theme_toggle", "→ light: to first paint", lambda s: g(s, "light", "to_paint_ms"), ms),
    ("fold_toggle", "layout passes per toggle", lambda s: g(s, "summary", "layout_per_toggle"), n),
    ("fold_toggle", "scroll sets per toggle", lambda s: g(s, "summary", "scroll_sets_per_toggle"), n),
    ("fold_toggle", "stall max", lambda s: g(s, "summary", "stall_max_ms"), ms),
    ("fold_toggle", "animation fps (min)", lambda s: g(s, "summary", "fps_min_animated"), n),
    ("fold_toggle", "row below: max jump", lambda s: g(s, "summary", "below_max_jump_px"), lambda v: f"{v:g} px"),
    ("fold_toggle", "flashes", lambda s: g(s, "summary", "flashes"), n),
    ("thinking_toggle", "layout passes per toggle", lambda s: g(s, "summary", "layout_per_toggle"), n),
    ("thinking_toggle", "animation fps (min)", lambda s: g(s, "summary", "fps_min_animated"), n),
    ("tool_toggle", "layout passes per toggle", lambda s: g(s, "summary", "layout_per_toggle"), n),
    ("tool_toggle", "stall max", lambda s: g(s, "summary", "stall_max_ms"), ms),
    ("tool_toggle", "animation fps (min)", lambda s: g(s, "summary", "fps_min_animated"), n),
    ("hover_sweep", "layout passes (240 moves)", lambda s: g(s, "layout"), n),
    ("hover_sweep", "stall p95", lambda s: g(s, "stall", "p95"), ms),
    ("streaming_follow", "stall p95", lambda s: g(s, "stall", "p95"), ms),
    ("streaming_follow", "stall max", lambda s: g(s, "stall", "max"), ms),
    ("streaming_follow", "stalls over 16 ms", lambda s: g(s, "stall", "over_16ms"), n),
    ("streaming_follow", "layout passes per second", lambda s: g(s, "layout_per_s"), n),
    ("streaming_follow", "CPU", lambda s: g(s, "stall", "cpu_pct"), pc),
    ("streaming_follow", "flashes (filmed turn)", lambda s: g(s, "filmed", "flashes"), n),
    ("streaming_scrolled_up", "unexpected shift", lambda s: g(s, "shift", "unexpected_px"), lambda v: f"{v:g} px"),
    ("streaming_scrolled_up", "scroll sets", lambda s: g(s, "scroll_sets"), n),
    ("streaming_scrolled_up", "stall p95", lambda s: g(s, "stall", "p95"), ms),
    ("typing", "transcript layout passes", lambda s: g(s, "layout"), n),
    ("typing", "stall p95", lambda s: g(s, "stall", "p95"), ms),
    ("resize_window", "step p95", lambda s: g(s, "step", "p95"), ms),
    ("resize_window", "steps over 50 ms", lambda s: g(s, "over_50ms"), n),
    ("switch_session", "to first paint p50", lambda s: g(s, "switch", "p50"), ms),
    ("switch_session", "widgets made per switch", lambda s: g(s, "widgets_made_per_switch"), n),
    ("switch_session", "RSS growth over 10 switches", lambda s: g(s, "rss_growth_mb"), lambda v: f"{v:g} MB"),
    ("panel_tabs", "tasks tab: first turn", lambda s: g(s, "tabs", "tasks", "first_turn_ms"), ms),
    ("panel_tabs", "changes tab: first turn", lambda s: g(s, "tabs", "changes", "first_turn_ms"), ms),
    ("panel_tabs", "seam drag step p95", lambda s: g(s, "drag", "step", "p95"), ms),
    ("panel_while_streaming", "tasks: panel widgets made / s", lambda s: g(s, "tasks", "panel_widgets_made_per_s"), n),
    (
        "panel_while_streaming",
        "changes: panel widgets made / s",
        lambda s: g(s, "changes", "panel_widgets_made_per_s"),
        n,
    ),
    ("panel_while_streaming", "info: panel widgets made / s", lambda s: g(s, "info", "panel_widgets_made_per_s"), n),
    ("panel_while_streaming", "tasks: stall p95", lambda s: g(s, "tasks", "stall", "p95"), ms),
    ("scroll_wheel", "fling up: stall p95", lambda s: g(s, "up", "stall", "p95"), ms),
    ("scroll_wheel", "fling up: stall max", lambda s: g(s, "up", "stall", "max"), ms),
    ("scroll_wheel", "fling up: px/s", lambda s: g(s, "up", "px_per_s"), n),
    ("scroll_wheel", "fling up: blank fraction", lambda s: g(s, "up_filmed", "blank_fraction"), n),
    ("scroll_drag", "step p95", lambda s: g(s, "step", "p95"), ms),
    ("scroll_drag", "steps over 16 ms (of 81)", lambda s: g(s, "over_16ms"), n),
    ("find", "search over the whole conversation", lambda s: g(s, "search_ms"), ms),
    ("find", "widgets after", lambda s: g(s, "widgets"), n),
    ("idle", "CPU, quiet", lambda s: g(s, "quiet_cpu_pct"), pc),
    ("idle", "CPU, busy-looking", lambda s: g(s, "busy_look_cpu_pct"), pc),
]


def table(runs: List[Tuple[str, Json]]) -> str:
    head = "| scenario | measure | " + " | ".join(label for label, _ in runs) + " |"
    sep = "|---|---|" + "---|" * len(runs)
    lines = [head, sep]
    for scenario, label, getter, fmt in ROWS:
        cells = []
        for _name, run in runs:
            s = g(run, "scenarios", scenario, default={})
            if "error" in s and getter(s) is None:
                cells.append("error")
                continue
            try:
                v = getter(s)
            except Exception:  # noqa: BLE001
                v = None
            cells.append(fmt(v) if v is not None else "—")
        lines.append(f"| {scenario} | {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    runs = []
    for a in args:
        d = json.loads(Path(a).read_text(encoding="utf-8"))
        runs.append((d.get("label") or Path(a).stem, d))
    print(table(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
