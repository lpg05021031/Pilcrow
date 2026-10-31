"""Frame pacing: the intervals between the frames the view paints during a fling, a fold opening
and closing, a font-scale change and a panel toggle; and, in the real window, which widgets repaint
while a reply streams (the right panel, the sidebar and the header have no business repainting).

A frame is late when more than 25 ms separates it from the one before (a 60 Hz frame and a half);
the jitter is the spread of the intervals. Runs offscreen; the numbers are relative."""

from __future__ import annotations

import os
import statistics
import sys
import time
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core import Session, app, pump  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from s_scroll import long_session, stale_visible  # noqa: E402

from pilcrow_qt import theme  # noqa: E402
from pilcrow_qt.canvas.scene import Group  # noqa: E402
from pilcrow_qt.store import store  # noqa: E402


class Frames(QObject):
    """The times at which a widget painted."""

    def __init__(self, w) -> None:
        super().__init__()
        self.times: List[float] = []
        self.w = w
        w.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.Paint:
            self.times.append(time.perf_counter())
        return False

    def stop(self) -> None:
        self.w.removeEventFilter(self)

    def report(self) -> str:
        t = self.times
        if len(t) < 2:
            return f"{len(t)} frames"
        gaps = [(b - a) * 1000 for a, b in zip(t, t[1:])]
        late = [g for g in gaps if g > 25]
        return (
            f"{len(t)} frames over {(t[-1] - t[0]) * 1000:.0f} ms: median {statistics.median(gaps):.1f} ms, "
            f"p90 {sorted(gaps)[int(len(gaps) * 0.9)]:.1f} ms, worst {max(gaps):.1f} ms, late {len(late)}"
        )


def wheel(v, dy: int) -> None:
    pos = QPoint(v.viewport().width() // 2, v.viewport().height() // 2)
    ev = QWheelEvent(
        pos,
        v.viewport().mapToGlobal(pos),
        QPoint(0, dy),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(v.viewport(), ev)


def run_fling(turns: int = 1500) -> str:
    s = Session("smooth-fling", long_session(turns), width=1000, height=800)
    v = s.view
    pump(600)
    vb = v.verticalScrollBar()
    vb.setValue(vb.maximum())
    pump(300)
    f = Frames(v.viewport())
    for _ in range(60):  # a fling: a notch every 16 ms
        wheel(v, 120)
        pump(16)
    pump(400)  # the easing runs out
    f.stop()
    out = f"fling 60 notches on {turns} turns: {f.report()}; placeholders {v.cold_paints}, stale {stale_visible(v)}"
    s.close()
    return out


def run_fold(turns: int = 600) -> str:
    s = Session("smooth-fold", long_session(turns), width=1000, height=800)
    v = s.view
    pump(600)
    folds = [b for b in v.scene.root.walk() if isinstance(b, Group) and b.KIND == "fold"]
    if not folds:
        s.close()
        return "fold: none"
    g = folds[len(folds) // 2]
    v.scroll_to_block(g, 0.2, only_if_hidden=False)
    pump(500)
    key = v.first_visible_key()
    f = Frames(v.viewport())
    v.toggle_group(g, not g.open, animate=True)
    pump(700)
    opened = f.report()
    kept = v.first_visible_key() == key
    f.times.clear()
    v.toggle_group(g, not g.open, animate=True)
    pump(700)
    closed = f.report()
    f.stop()
    s.close()
    return f"fold open: {opened}; anchor kept {kept}\nfold close: {closed}"


def run_scale(turns: int = 600) -> str:
    s = Session("smooth-scale", long_session(turns), width=1000, height=800)
    v = s.view
    pump(600)
    vb = v.verticalScrollBar()
    vb.setValue(vb.maximum() // 2)
    pump(400)
    f = Frames(v.viewport())

    def top() -> tuple:
        """The leaf under the viewport's top edge and how far into it the edge sits."""
        hit = v.scene.leaf_at(v.scene_y(0))
        return (hit[0].key, v.scene_y(0) - hit[1]) if hit else ()

    key = top()

    def step(fn) -> str:
        """The call itself, then the time to the first frame after it and the frames that follow."""
        f.times.clear()
        t0 = time.perf_counter()
        fn()
        sync = (time.perf_counter() - t0) * 1000
        while not f.times and time.perf_counter() - t0 < 2:
            pump(2)
        first = (f.times[0] - t0) * 1000 if f.times else -1
        pump(400)
        return f"call {sync:.0f} ms, first frame at {first:.0f} ms, then {f.report()}"

    up = step(lambda: store.zoom(1))
    kept = top() == key  # the same block, the same pixel into it
    stale = stale_visible(v)
    down = step(lambda: store.zoom(-1))
    dark = theme.tokens().name == "dark"
    th = step(lambda: theme.theme.apply(not dark))
    theme.theme.apply(dark)
    pump(300)
    f.stop()
    s.close()
    return f"zoom in: {up}; anchor kept {kept}, stale {stale}\nzoom out: {down}\ntheme switch: {th}"


def run_window() -> str:
    """The real window: who repaints while a reply streams, and the panel toggle's frames."""
    from harness import Client, start_mock

    port = 5661
    mock = start_mock(port, 400)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        c.open("sess-long")
        c.wait_until(lambda: store.rt.get("sess-long") and store.rt["sess-long"].loaded, 20, "loaded")
        c.sleep(1500)
        store.set_ui(panel=True)
        c.sleep(1500)
        win = c.win
        sv = win.session_view
        tr = sv.transcript
        watched: Dict[str, object] = {"transcript": tr.view.viewport(), "sidebar": win.sidebar, "window": win}
        if win.panel is not None:
            watched["panel"] = win.panel
        for name in ("header", "composer"):
            w = getattr(sv, name, None)
            if w is not None:
                watched[name] = w
        counts: Dict[str, int] = {k: 0 for k in watched}
        # paints anywhere under a watched widget count for it (children paint on their own)
        app_ = QApplication.instance()

        class F(QObject):
            def eventFilter(self, obj, e) -> bool:  # noqa: N802
                if e.type() == QEvent.Type.Paint and hasattr(obj, "isWidgetType") and obj.isWidgetType():
                    for name, w in watched.items():
                        if obj is w or (name != "window" and w.isAncestorOf(obj)):
                            counts[name] += 1
                    if obj is win:
                        counts["window"] += 1
                return False

        f = F()
        app_.installEventFilter(f)
        c.call("mock.pace", {"ms": 4})
        store.send_message("sess-long", "slow please")
        c.wait_until(
            lambda: not store.rt["sess-long"].busy and store.get_model("sess-long").items[-1].kind == "text",
            60,
            "reply",
        )
        c.sleep(300)
        app_.removeEventFilter(f)
        items = store.get_model("sess-long").items
        last = next((it for it in reversed(items) if it.kind == "text"), None)
        tokens = len((last.text if last is not None else "").split())
        stream = f"stream of ~{tokens} words: repaints " + ", ".join(f"{k} {v}" for k, v in counts.items())
        # the panel closed and opened: frames of the view and of the panel
        fv = Frames(tr.view.viewport())
        fp = Frames(win.panel_host)
        store.set_ui(panel=False)
        c.sleep(600)
        store.set_ui(panel=True)
        c.sleep(600)
        fv.stop()
        fp.stop()
        toggles = f"panel close+open: view {fv.report()} | panel host {fp.report()}"
        c.close()
        return stream + "\n" + toggles
    finally:
        mock.kill()


if __name__ == "__main__":
    app()
    which = sys.argv[1:] or ["fling", "fold", "scale", "window"]
    for name in which:
        fn = globals()[f"run_{name}"]
        print(fn(), flush=True)
