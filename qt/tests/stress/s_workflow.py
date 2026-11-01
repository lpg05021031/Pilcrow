"""A workflow under load, in the real window: a run of sixty agents whose progress arrives ten times a
second while the Tasks tab is in front — what the panel builds per update (widgets made), what repaints,
what one update costs on the main thread; then the same with the conversation's Workflow card in view
and the panel behind another tab.

    python qt/tests/stress/s_workflow.py
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
from harness import Client, start_mock  # noqa: E402
from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt.store import store  # noqa: E402

N_AGENTS = 60
PHASES = 4


def agents_at(tick: int, t0: float) -> List[Dict[str, Any]]:
    """Sixty agents in four phases; each starts a few ticks after the previous and runs for twelve."""
    out = []
    for i in range(N_AGENTS):
        start = i // 2
        end = start + 12
        a: Dict[str, Any] = {
            "type": "workflow_agent",
            "index": i,
            "label": f"agent-{i:02d}",
            "phaseIndex": i * PHASES // N_AGENTS,
            "phaseTitle": f"Phase {i * PHASES // N_AGENTS + 1}",
            "model": "claude-mock-1",
            "state": "start",
            "queuedAt": t0,
            "promptPreview": "Review the module and report what you find as JSON. " * 4,
        }
        if tick >= start:
            a["startedAt"] = t0 + start * 100
            a["state"] = "progress"
            a["lastProgressAt"] = t0 + tick * 100
            a["lastToolName"] = ("Read", "Grep", "Bash")[tick % 3]
            a["lastToolSummary"] = f"{a['lastToolName']} src/mod{(tick + i) % 23}.ts"
            a["toolUseCount"] = tick - start
            a["totalTokens"] = (tick - start) * 900
        if tick >= end:
            a["state"] = "error" if i % 17 == 16 else "done"
            a["endedAt"] = t0 + end * 100
            a["resultPreview"] = f"agent-{i:02d}: done, two findings." if a["state"] == "done" else None
            if a["state"] == "error":
                a["error"] = "Hit the turn limit."
        a["updatedAt"] = t0 + tick * 100
        if tick >= start:  # an agent is reported once it starts, the way Claude Code does
            out.append(a)
    return out


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
                if obj is w or (hasattr(w, "isAncestorOf") and w.isAncestorOf(obj)):
                    self.paints[k] += 1
                    break
            else:
                self.paints["other"] += 1
        elif t == QEvent.Type.LayoutRequest:
            self.layouts += 1
        elif t == QEvent.Type.ChildAdded and hasattr(obj, "isAncestorOf"):
            try:
                if obj is self.made_root or self.made_root.isAncestorOf(obj):
                    self.made += 1
            except RuntimeError:
                pass
        return False

    def report(self) -> str:
        return f"paints {self.paints} | layout requests {self.layouts} | widgets made under the panel {self.made}"


def main() -> None:
    port = 5661
    mock = start_mock(port, 60)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        sid = "sess-tests"
        c.open(sid)
        c.call("mock.pace", {"ms": 0})
        # the mock's own run gives the conversation a Workflow card
        c.call("session.send", {"id": sid, "text": "run the review workflow"})
        c.wait_until(
            lambda: (
                store.rt[sid].status == "idle"
                and any(t.get("status") == "completed" for t in store.rt[sid].st.get("tasks") or [])
            ),
            30,
            "the mock run",
        )
        c.sleep(500)
        t0 = time.time() * 1000
        task = {
            "id": "task-stress",
            "runId": "wf_stress",
            "description": "sixty-agent review",
            "type": "local_workflow",
            "workflowName": "sixty-agent review",
            "status": "running",
            "startedAt": t0,
            "workflow": {
                "phases": [{"index": i, "title": f"Phase {i + 1}"} for i in range(PHASES)],
                "agents": [],
                "logs": [],
            },
        }
        base = list(store.rt[sid].st.get("tasks") or [])
        cur = store.rt[sid].st

        def push(tick: int) -> None:
            task["workflow"]["agents"] = agents_at(tick, t0)
            task["workflow"]["updatedAt"] = t0 + tick * 100
            task["usage"] = {
                "total_tokens": sum(a.get("totalTokens") or 0 for a in task["workflow"]["agents"]),
                "tool_uses": sum(a.get("toolUseCount") or 0 for a in task["workflow"]["agents"]),
                "duration_ms": tick * 100,
            }
            store._on_state(
                {
                    "id": sid,
                    "status": cur.get("status"),
                    "live": cur.get("live"),
                    "pending": cur.get("pending") or [],
                    "tasks": base + [task],
                }
            )

        # ---- 1. the Tasks tab in front
        store.set_ui(panel=True, panel_tab="tasks")
        c.sleep(600)
        push(0)
        c.sleep(700)
        sv = c.win.session_view
        panel = c.win.panel
        regions = {
            "viewport": sv.transcript.view.viewport(),
            "panel": panel,
            "sidebar": c.win.sidebar,
            "composer": sv.composer,
        }
        where = Where(regions, panel)
        QApplication.instance().installEventFilter(where)
        probe = Probe(sv.transcript.view)
        probe.start()
        costs = []
        for tick in range(1, 45):
            costs.append(timed(lambda t=tick: push(t)))
            c.sleep(100)
        c.sleep(300)
        probe.stop()
        QApplication.instance().removeEventFilter(where)
        print(
            f"workflow: 44 progress updates at 10 Hz, 60 agents, Tasks tab in front: per update avg {sum(costs) / len(costs):.1f} ms max {max(costs):.1f} ms | {where.report()} | {probe.report('')}"
        )
        # ---- 2. the Files tab in front: the Tasks tab does nothing
        store.set_ui(panel_tab="files")
        c.sleep(400)
        where = Where(regions, panel)
        QApplication.instance().installEventFilter(where)
        costs = []
        for tick in range(45, 65):
            costs.append(timed(lambda t=tick: push(t)))
            c.sleep(100)
        c.sleep(300)
        QApplication.instance().removeEventFilter(where)
        print(
            f"workflow: 20 updates with the Files tab in front: per update avg {sum(costs) / len(costs):.1f} ms max {max(costs):.1f} ms | {where.report()}"
        )
        # ---- 3. the panel closed: the conversation's card alone
        store.set_ui(panel=False)
        c.sleep(400)
        where = Where(regions, panel)
        QApplication.instance().installEventFilter(where)
        probe = Probe(sv.transcript.view)
        probe.start()
        costs = []
        for tick in range(65, 85):
            costs.append(timed(lambda t=tick: push(t)))
            c.sleep(100)
        c.sleep(300)
        probe.stop()
        QApplication.instance().removeEventFilter(where)
        print(
            f"workflow: 20 updates with the panel closed: per update avg {sum(costs) / len(costs):.1f} ms max {max(costs):.1f} ms | {where.report()} | {probe.report('')}"
        )
        c.win.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    main()
