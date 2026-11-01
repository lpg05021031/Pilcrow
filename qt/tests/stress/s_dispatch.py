"""Dispatch under load, in the real window: a job of forty repositories whose runs report ten times a
second (a new line of work, cost, a state change every few ticks) while the board is in front, then
the job's view, then another page — what each update costs on the main thread, what repaints, what
receives a layout pass, how many widgets are made. The hidden page should do nothing; the board should
touch the one card; the job view should patch the rows that changed and nothing else.

    python qt/tests/stress/s_dispatch.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core import Probe, timed  # noqa: E402
from harness import Client, free_port, start_mock  # noqa: E402
from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt.store import store  # noqa: E402

N_RUNS = 40
JID = "job-stress"
HOME = "/home/tester"
NOW_LINES = ("Read src/index.ts", "Edit src/config.ts", "Bash npm test", "Grep eslint", "Write docs/CHANGELOG.md")


def runs_at(tick: int, t0: float) -> List[Dict[str, Any]]:
    """Forty runs, three abreast; each starts a few ticks after the previous and runs for fifteen."""
    out = []
    for i in range(N_RUNS):
        start = i // 3 * 2
        end = start + 15
        name = f"repo-{i:02d}"
        r: Dict[str, Any] = {
            "id": f"run-{i:02d}",
            "name": name,
            "path": f"{HOME}/code/{name}",
            "base": "main",
            "wave": "main",
            "level": i % 3,
            "dependsOn": [],
            "state": "queued",
            "pending": [],
            "cost": 0,
            "add": 0,
            "del": 0,
            "files": [],
        }
        if tick >= start:
            r["state"] = "running"
            r["startedAt"] = t0 + start * 100
            r["sessionId"] = f"dj-{i:02d}"
            r["worktree"] = {
                "path": f"{HOME}/wt/{name}",
                "branch": "pilcrow/stress",
                "base": "main",
                "repoRoot": r["path"],
            }
            r["now"] = NOW_LINES[(tick + i) % len(NOW_LINES)]
            r["cost"] = round((tick - start) * 0.004, 4)
        if tick >= end:
            r["state"] = "failed" if i % 13 == 12 else "done"
            r["endedAt"] = t0 + end * 100
            r["now"] = None
            r["files"] = [{"path": "src/config.ts", "add": 3, "del": 1}]
            r["add"], r["del"] = 3, 1
            r["summary"] = "Pinned the version and ran the tests." if r["state"] == "done" else ""
            r["reason"] = "Hit the turn limit." if r["state"] == "failed" else None
        elif tick >= start and i % 7 == 6 and (tick - start) in (5, 6, 7):
            r["state"] = "needs_action"
            r["pending"] = [
                {
                    "requestId": f"req-{i}",
                    "toolName": "Bash",
                    "key": "Bash:npm run build",
                    "label": "Bash: npm run build",
                    "at": t0 + tick * 100,
                }
            ]
            r["now"] = "Bash: npm run build"
        out.append(r)
    return out


def summary(runs: List[Dict[str, Any]], t0: float, tick: int) -> Dict[str, Any]:
    counts = {k: 0 for k in ("queued", "preparing", "running", "needs_action", "done", "failed", "stopped", "skipped")}
    for r in runs:
        counts[r["state"]] += 1
    return {
        "id": JID,
        "name": "Forty repositories",
        "slug": "forty",
        "brief": "Pin the Node version in CI to 22 in {{repo.name}}.",
        "state": "running",
        "wave": "main",
        "paused": False,
        "startedAt": t0,
        "updatedAt": t0 + tick * 100,
        "settings": {"parallel": 3, "costCap": 5, "learn": True},
        "targets": [{"path": r["path"], "name": r["name"]} for r in runs],
        "plan": {"ok": N_RUNS, "warnings": 0, "blocked": 0, "levels": 3},
        "rules": [],
        "counts": counts,
        "total": N_RUNS,
        "cost": round(sum(r["cost"] for r in runs), 4),
        "needsAction": sum(len(r["pending"]) for r in runs),
        "canaryWaiting": False,
    }


class Where(QObject):
    """Paints by region, layout requests by receiver, widgets made under a root."""

    def __init__(self, regions: Dict[str, Any], made_root: Any) -> None:
        super().__init__()
        self.regions = regions
        self.made_root = made_root
        self.paints: Dict[str, int] = {k: 0 for k in regions}
        self.paints["other"] = 0
        self.layouts = 0
        self.made = 0

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.Type.Paint:
            for k, w in self.regions.items():
                try:
                    if w is not None and (obj is w or (hasattr(w, "isAncestorOf") and w.isAncestorOf(obj))):
                        self.paints[k] += 1
                        break
                except RuntimeError:
                    continue
            else:
                self.paints["other"] += 1
        elif t == QEvent.Type.LayoutRequest:
            self.layouts += 1
        elif t == QEvent.Type.ChildAdded and hasattr(obj, "isAncestorOf"):
            try:
                if self.made_root is not None and (obj is self.made_root or self.made_root.isAncestorOf(obj)):
                    self.made += 1
            except RuntimeError:
                pass
        return False

    def report(self) -> str:
        return f"paints {self.paints} | layout requests {self.layouts} | widgets made under the page {self.made}"


def main() -> None:
    port = free_port()
    mock = start_mock(port, 60)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        c.open("sess-tests")
        t0 = time.time() * 1000
        prev: Dict[str, Dict[str, Any]] = {}

        def push(tick: int, all_runs: bool = False) -> None:
            runs = runs_at(tick, t0)
            changed = [r for r in runs if all_runs or prev.get(r["id"]) != r]
            for r in runs:
                prev[r["id"]] = r
            store._on_dispatch({"id": JID, "job": summary(runs, t0, tick), "runs": changed, "all": all_runs})

        def measure(label: str, ticks: range, regions: Dict[str, Any], made_root: Any, probe_view: Any = None) -> None:
            where = Where(regions, made_root)
            QApplication.instance().installEventFilter(where)
            probe = Probe(probe_view) if probe_view is not None else None
            if probe:
                probe.start()
            costs = [timed(lambda t=t: push(t)) for t in ticks if (c.sleep(100) or True)]
            c.sleep(300)
            if probe:
                probe.stop()
            QApplication.instance().removeEventFilter(where)
            print(
                f"dispatch: {len(costs)} updates at 10 Hz, {N_RUNS} runs, {label}: per update avg {sum(costs) / len(costs):.1f} ms max {max(costs):.1f} ms | {where.report()}"
                + (f" | {probe.report('')}" if probe else "")
            )

        # ---- 1. the board in front
        store.show_dispatch(None)
        c.wait_until(lambda: c.win.dispatch is not None and store.jobs_loaded, 5, "board")
        c.sleep(400)
        push(0, all_runs=True)
        c.sleep(400)
        page = c.win.dispatch
        sv = c.win.session_view
        regions = {
            "board": page.board,
            "sidebar": c.win.sidebar,
            "session": sv,
        }
        measure("the board in front", range(1, 41), regions, page)
        # ---- 2. the job's view in front
        store.show_dispatch(JID)
        c.wait_until(lambda: page.job_view is not None and page.job_view.isVisible(), 5, "job view")
        c.sleep(400)
        regions = {"job view": page.job_view, "sidebar": c.win.sidebar, "session": sv}
        measure("the job view in front", range(41, 81), regions, page, page.job_view.list)
        # ---- 3. a session page in front: the dispatch page should do nothing
        store.show_page("session")
        c.sleep(400)
        regions = {"dispatch page": page, "sidebar": c.win.sidebar, "session": sv}
        measure("the conversation in front (dispatch hidden)", range(81, 121), regions, page)
        c.win.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    main()
