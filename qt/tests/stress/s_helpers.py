"""Helpers (HELPERS.md) under load. Two parts:

1. The canvas: a lead's conversation of 300 turns carrying 60 helper notes (handoffs up to 2,000
   characters) and 70 helpers tool cards, opened cold and flung through — stalls, paints, cold
   placeholders, and what a note costs to measure.
2. The real window against the mock server: eight helpers whose state ticks ten times a second
   (elapsed time, a new last line, one needing action) while the lead is quiet, then while a reply
   streams — which widgets repaint, which receive a layout pass, what one update costs on the main
   thread. The bar should repaint alone; the conversation, the sidebar and the composer should see
   nothing (HELPERS.md §11).

    python qt/tests/stress/s_helpers.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core import Probe, Session, app, assistant, pump, timed, tool_result, tool_use, user  # noqa: E402
from harness import Client, start_mock  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt.canvas.blocks.note import NoteBlock  # noqa: E402
from pilcrow_qt.store import store  # noqa: E402

HANDOFF = (
    "## Handoff\n\nThe parser now accepts the new header and its tests pass. Changed `src/parse.ts` "
    "(the tokenizer's state table) and `test/parse.test.ts` (eleven cases). The caller in `src/index.ts` "
    "is unchanged; it reads the same shape. Watch for the `strict` flag: it is off by default. "
)


def note(k: int, state: str = "done", length: int = 400) -> Dict[str, Any]:
    body = (HANDOFF * 8)[:length]
    m = user(
        f'<helper-note name="helper-{k}" state="{state}" elapsed="{k % 9 + 1}m {k % 60}s" cost="${(k % 7) * 0.11:.2f}" '
        f'files="{k % 5}" add="{k * 3 % 97}" del="{k % 13}">\n{body}\n</helper-note>'
    )
    m["_note"] = "helper"
    return m


def helper(k: int, state: str = "running", tick: int = 0) -> Dict[str, Any]:
    return {
        "id": f"h{k}",
        "name": f"helper-{k}",
        "cwd": f"/home/tester/code/repo{k}",
        "branch": f"pilcrow/lead/helper-{k}",
        "worktree": f"/home/tester/code/repo{k}/.worktrees/lead",
        "state": state,
        "since": 1,
        "elapsedMs": 1000 * (tick + 3 * k),
        "cost": round(0.013 * k + 0.0001 * tick, 4),
        "files": 3 if state == "done" else 0,
        "add": 40 if state == "done" else 0,
        "del": 12 if state == "done" else 0,
        "pending": 1 if state == "needs_action" else 0,
        "lastLine": f"Read src/mod{(tick + k) % 23}.ts",
        "after": [],
    }


def lead_session(turns: int = 300, notes: int = 60) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    n = 0
    for i in range(turns):
        msgs.append(user(f"Turn {i}: split the work on module {i}"))
        kind = i % 5
        if kind == 0:
            tid = f"hs-{i}"
            msgs.append(assistant([{"type": "text", "text": f"Two parts; a helper takes module {i}."}]))
            msgs.append(
                assistant(
                    [
                        tool_use(
                            tid,
                            "mcp__helpers__start",
                            {
                                "name": f"helper-{i}",
                                "repo": "api",
                                "brief": "Edit the parser and check its tests.\n" * 6,
                            },
                        )
                    ]
                )
            )
            msgs.append(
                tool_result(
                    tid,
                    json.dumps(
                        {
                            "id": f"h{i}",
                            "name": f"helper-{i}",
                            "state": "queued",
                            "cwd": "/home/tester/code/api",
                            "worktree": {
                                "path": "/home/tester/code/api/.worktrees/lead",
                                "branch": f"pilcrow/lead/helper-{i}",
                            },
                            "lastLine": "",
                        }
                    ),
                )
            )
        elif kind == 1:
            tid = f"hw-{i}"
            msgs.append(
                assistant([tool_use(tid, "mcp__helpers__wait", {"names": ["helper-1", "helper-2"], "until": "done"})])
            )
            msgs.append(
                tool_result(tid, json.dumps({"helpers": [helper(k, "done" if k % 3 else "running") for k in range(8)]}))
            )
        elif kind == 2:
            tid = f"hr-{i}"
            msgs.append(assistant([tool_use(tid, "mcp__helpers__read", {"name": "helper-1", "what": "summary"})]))
            msgs.append(tool_result(tid, json.dumps({"text": (HANDOFF * 10)[:3000], "truncated": False})))
        else:
            msgs.append(
                assistant(
                    [{"type": "text", "text": f"Module {i} is done on the lead's side.\n\n" + "The pieces agree. " * 6}]
                )
            )
        if n < notes and i % max(1, turns // notes) == 0:
            msgs.append(note(n, ("done", "failed", "stopped", "needs_action")[n % 4], 2000 if n % 10 == 0 else 400))
            msgs.append(assistant([{"type": "text", "text": f"Noted: helper-{n} finished; I read the handoff."}]))
            n += 1
    return msgs


def wheel(v, dy: int) -> None:
    vp = v.viewport()
    pos = QPointF(vp.width() / 2, vp.height() / 2)
    e = QWheelEvent(
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, e)


class Count:
    """Wraps a method: calls and time."""

    def __init__(self, cls, name: str) -> None:
        self.n = 0
        self.ms = 0.0
        self.cls, self.name = cls, name
        self.orig = getattr(cls, name)
        c = self

        def wrapped(*a, **k):
            t0 = time.perf_counter()
            try:
                return c.orig(*a, **k)
            finally:
                c.n += 1
                c.ms += (time.perf_counter() - t0) * 1000

        setattr(cls, name, wrapped)

    def stop(self) -> None:
        setattr(self.cls, self.name, self.orig)

    def __str__(self) -> str:
        return f"{self.name}: {self.n} calls, {self.ms:.0f} ms total, {self.ms / max(1, self.n):.2f} ms each"


def run_canvas() -> None:
    app()
    msgs = lead_session()
    notes = sum(1 for m in msgs if m.get("_note"))
    cards = sum(
        1
        for m in msgs
        if any(c.get("name", "").startswith("mcp__helpers__") for c in m["message"]["content"] if isinstance(c, dict))
    )
    meas = Count(NoteBlock, "measure")
    t_open = timed(lambda: globals().__setitem__("_S", Session("lead", msgs)))
    s: Session = globals()["_S"]
    v = s.view
    print(
        f"canvas: {len(msgs)} messages, {notes} notes, {cards} helpers cards: open+settle {t_open:.0f} ms (300 ms of it is the settle)"
    )
    v.cold_paints = 0
    probe = Probe(v)
    probe.start()
    sb = v.verticalScrollBar()
    sb.setValue(0)
    pump(100)
    steps = 0
    while sb.value() < sb.maximum() and steps < 4000:
        wheel(v, -120 * 3)
        pump(8)
        steps += 1
    pump(300)
    probe.stop()
    print(f"canvas: fling top→bottom in {steps} wheel steps: {probe.report('')} | cold placeholders {v.cold_paints}")
    print(f"canvas: NoteBlock.{meas}")
    meas.stop()
    # hover the notes near the end (a note with the pointer over it paints direct, not from its pixmap)
    probe = Probe(v)
    probe.start()
    vp = v.viewport()
    from PySide6.QtGui import QMouseEvent

    for y in range(20, vp.height() - 20, 6):
        e = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(vp.width() * 0.3, y),
            QPointF(vp.mapToGlobal(QPoint(int(vp.width() * 0.3), y))),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(vp, e)
        pump(4)
    pump(100)
    probe.stop()
    print(f"canvas: pointer sweep over the end (notes, cards): {probe.report('')}")
    s.close()


class Where(QObject):
    """Paints by region and layout requests by receiver, over the whole application."""

    def __init__(self, regions: Dict[str, Any]) -> None:
        super().__init__()
        self.regions = regions
        self.paints: Dict[str, int] = {k: 0 for k in regions}
        self.paints["other"] = 0
        self.other: Dict[str, int] = {}
        self.layouts: Dict[str, int] = {}

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        t = e.type()
        if t == QEvent.Type.Paint:
            for k, w in self.regions.items():
                if obj is w or (hasattr(w, "isAncestorOf") and w.isAncestorOf(obj)):
                    self.paints[k] += 1
                    break
            else:
                self.paints["other"] += 1
                name = f"{type(obj).__name__}:{obj.objectName() or ''}"
                self.other[name] = self.other.get(name, 0) + 1
        elif t == QEvent.Type.LayoutRequest:
            name = f"{type(obj).__name__}:{obj.objectName() or ''}"
            self.layouts[name] = self.layouts.get(name, 0) + 1
        return False

    def report(self) -> str:
        top = ", ".join(f"{k} {n}" for k, n in sorted(self.other.items(), key=lambda kv: -kv[1])[:4])
        lay = ", ".join(f"{k} {n}" for k, n in sorted(self.layouts.items(), key=lambda kv: -kv[1])) or "none"
        return f"paints {self.paints} (other: {top or 'none'}) | layout requests: {lay}"


def run_window() -> None:
    port = 5651
    mock = start_mock(port, 400)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        sid = "sess-long"
        c.open(sid)
        c.wait_until(lambda: store.rt.get(sid) and store.rt[sid].loaded, 20, "loaded")
        c.sleep(800)
        sv = c.win.session_view
        v = sv.transcript.view

        def push(tick: int) -> None:
            # the server sends the whole state each time; the lead's own fields are the live ones
            cur = store.rt[sid].st
            hs = [helper(k, "running" if k < 6 else ("needs_action" if k == 6 else "done"), tick) for k in range(8)]
            store._on_state(
                {
                    "id": sid,
                    "status": cur.get("status"),
                    "live": cur.get("live"),
                    "pending": cur.get("pending") or [],
                    "helpers": hs,
                    "lead": {"since": 1, "cost": 0.5 + tick * 0.001, "caps": {}, "rules": []},
                }
            )

        t_first = timed(lambda: push(0))
        c.sleep(300)
        print(f"window: the bar's arrival (8 chips, one layout of the view): {t_first:.1f} ms on the main thread")
        regions = {
            "viewport": v.viewport(),
            "bar": sv.helpers_bar,
            "sidebar": c.win.sidebar,
            "composer": sv.composer,
            "header": sv.head,
        }
        # ---- 1. the lead is quiet; its helpers tick
        where = Where(regions)
        QApplication.instance().installEventFilter(where)
        probe = Probe(v)
        probe.start()
        costs = []
        for tick in range(1, 31):
            costs.append(timed(lambda t=tick: push(t)))
            c.sleep(100)
        c.sleep(200)
        probe.stop()
        QApplication.instance().removeEventFilter(where)
        print(
            f"window: 30 helper updates at 10 Hz, lead idle: per update avg {sum(costs) / len(costs):.2f} ms max {max(costs):.1f} ms | "
            f"{where.report()} | {probe.report('')}"
        )
        # ---- 1b. what ten updates spend their time on
        import cProfile
        import io
        import pstats

        pr = cProfile.Profile()
        pr.enable()
        for tick in range(40, 50):
            push(tick)
            pump(30)
        pr.disable()
        out = io.StringIO()
        pstats.Stats(pr, stream=out).sort_stats("cumulative").print_stats(14)
        print("window: profile of 10 updates (cumulative):")
        for line in out.getvalue().splitlines():
            if "pilcrow_qt" in line or "function calls" in line:
                print("   ", line.strip())
        # ---- 2a. baseline: a reply streams, no helper updates
        c.call("mock.pace", {"ms": 4})
        where = Where(regions)
        QApplication.instance().installEventFilter(where)
        probe = Probe(v)
        probe.start()
        store.send_message(sid, "slow please")
        c.wait_until(lambda: not store.rt[sid].busy, 60, "reply")
        c.sleep(300)
        probe.stop()
        QApplication.instance().removeEventFilter(where)
        print(f"window: a reply streams, helpers quiet (baseline): {where.report()} | {probe.report('')}")
        # ---- 2. a reply streams while the helpers tick
        where = Where(regions)
        QApplication.instance().installEventFilter(where)
        probe = Probe(v)
        probe.start()
        store.send_message(sid, "slow please")
        tick = 100
        costs = []
        end = time.monotonic() + 4.0
        while time.monotonic() < end:
            tick += 1
            costs.append(timed(lambda t=tick: push(t)))
            c.sleep(100)
        c.wait_until(lambda: not store.rt[sid].busy, 60, "reply")
        c.sleep(300)
        probe.stop()
        QApplication.instance().removeEventFilter(where)
        print(
            f"window: helpers ticking while a reply streams: per update avg {sum(costs) / len(costs):.2f} ms max {max(costs):.1f} ms | "
            f"{where.report()} | {probe.report('')}"
        )
        # ---- 3. the Changes panel open on the lead: sections follow, hidden tabs do nothing
        store.set_ui(panel=True, panel_tab="changes")
        c.sleep(600)
        where = Where(regions)
        QApplication.instance().installEventFilter(where)
        for t in range(200, 220):
            push(t)
            c.sleep(100)
        c.sleep(200)
        QApplication.instance().removeEventFilter(where)
        print(f"window: 20 updates with the Changes panel open: {where.report()}")
        store.set_ui(panel_tab="files")
        c.sleep(300)
        where = Where(regions)
        QApplication.instance().installEventFilter(where)
        for t in range(300, 320):
            push(t)
            c.sleep(100)
        c.sleep(200)
        QApplication.instance().removeEventFilter(where)
        print(f"window: 20 updates with the Files tab in front (Changes hidden): {where.report()}")
        c.win.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    run_canvas()
    run_window()
