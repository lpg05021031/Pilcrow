"""What a person does to a long conversation, as benchmark scenarios.

Each scenario is ``def name(ctx) -> dict`` and may be run on its own. ``ctx`` holds the booted client
(``ctx.c``), the counters, the two long sessions (``ctx.heavy`` with prose, big outputs, big diffs,
thinking and sub-agents; ``ctx.mixed`` with the plain shapes), and helpers to find the conversation
view and its rows. Scenarios are written against the behaviour, not the implementation: they find
rows by class name where they must, so the same file runs on the current widget tree and on what
replaces it (an adapter per implementation lives in ``ctx``).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QWidget

from . import rig
from .rig import Film, Paced, click, move, pump, rss_mb, settle, type_text, wheel, y_in

Json = Dict[str, Any]


class RowRef:
    """A row of the conversation as the scenarios see it: a widget (the old transcript) or a block
    of the canvas engine. ``height()`` and ``y`` read the same way either way."""

    def __init__(self, ctx: Ctx, widget: Optional[QWidget] = None, block: Any = None) -> None:
        self.ctx = ctx
        self.widget = widget
        self.block = block

    def height(self) -> int:
        if self.block is not None:
            return int(self.block.h)
        return self.widget.height()

    @property
    def is_open(self) -> bool:
        if self.block is not None:
            return bool(getattr(self.block, "open", False))
        return bool(getattr(self.widget, "is_open", False))

    def head_rect(self):
        """The clickable head, in viewport coordinates."""
        from PySide6.QtCore import QRect

        if self.block is not None:
            r = self.ctx.transcript().view.block_rect(self.block)
            head_h = getattr(self.block, "head_h", None) or min(r.height(), 28)
            return QRect(r.x(), r.y(), r.width(), head_h)
        head = getattr(self.widget, "head", self.widget)
        top = head.mapTo(self.ctx.viewport(), QPoint(0, 0))
        return QRect(top.x(), top.y(), head.width(), head.height())

    def y(self) -> int:
        return self.head_rect().y()


class Ctx:
    """The booted client and what the scenarios share."""

    def __init__(self, client, heavy: str, mixed: str) -> None:
        self.c = client
        self.store = client.store
        self.win = client.win
        self.heavy = heavy
        self.mixed = mixed
        self.counters = rig.Counters()
        self.counters.scope("window", self.win)
        from pilcrow_qt import theme

        self.theme = theme

    # ---- the conversation
    def transcript(self):
        try:
            from pilcrow_qt.canvas.transcript import Transcript as CanvasTranscript

            views = self.c.find(CanvasTranscript)
            shown = [v for v in views if v.isVisible()]  # sessions keep their view; one is on screen
            if shown or views:
                return (shown or views)[0]
        except ImportError:
            pass
        from pilcrow_qt.widgets.transcript.view import Transcript

        views = self.c.find(Transcript)
        return views[0] if views else None

    @property
    def canvas(self) -> bool:
        return hasattr(self.transcript(), "scene")

    def viewport(self) -> QWidget:
        return self.transcript().viewport()

    def canvas_color(self):
        return self.theme.qcolor(self.theme.tokens().canvas)

    def open(self, sid: str, timeout: float = 60.0) -> Dict[str, float]:
        """Open a conversation and time the stages: the answer arriving, rows on screen, first paint."""
        t0 = time.perf_counter()
        self.store.open_session(sid)
        self.c.wait_until(lambda: sid in self.store.rt and self.store.rt[sid].loaded, timeout, f"load {sid}")
        t_loaded = time.perf_counter() - t0
        self.c.wait_until(lambda: self.transcript() is not None and self.transcript().units(), timeout, "rows")
        t_rows = time.perf_counter() - t0
        tv = self.transcript()
        self.c.app.processEvents()
        p0 = time.perf_counter()
        tv.viewport().grab()
        t_paint = time.perf_counter() - p0
        settle(400)
        return {
            "loaded_ms": round(t_loaded * 1000),
            "rows_ms": round(t_rows * 1000),
            "first_paint_ms": round(t_paint * 1000, 1),
            "total_ms": round((time.perf_counter() - t0) * 1000),
        }

    def scope(self, name: str, root: QWidget):
        return self.counters.scope(name, root)

    CANVAS_CLASSES = {"WorkFold": "FoldGroup", "Thinking": "ThinkingBlock", "ToolHead": "ToolBlock"}

    def rows(self, cls_name: str, whole: bool = False) -> List[RowRef]:
        """Rows of a kind (``WorkFold``, ``Thinking``, ``ToolHead``…) that touch the viewport, top to
        bottom; ``whole`` keeps only the ones whose clickable head is entirely on screen."""
        tv = self.transcript()
        vp = tv.viewport()
        out: List[RowRef] = []
        if self.canvas:
            want = self.CANVAS_CLASSES.get(cls_name, cls_name)
            view = tv.view
            for b in view.visible_blocks():
                if type(b).__name__ != want:
                    continue
                ref = RowRef(self, block=b)
                hr = ref.head_rect()
                if whole and not (0 <= hr.top() and hr.bottom() < vp.height()):
                    continue
                out.append(ref)
            out.sort(key=lambda r: r.y())
            return out
        for w in tv.widget().findChildren(QWidget):
            if type(w).__name__ != cls_name or not w.isVisible():
                continue
            top = y_in(w, vp)
            if top + w.height() <= 0 or top >= vp.height():
                continue
            if whole and not rig.visible_in(getattr(w, "head", w), vp):
                continue
            out.append(RowRef(self, widget=w))
        out.sort(key=lambda r: r.y())
        return out

    def click_row(self, row: RowRef) -> None:
        """Click a row's head where it is on the screen (the canvas takes the click on its viewport; a
        widget row takes it on the head widget itself, as QTest delivers to one widget)."""
        hr = row.head_rect()
        if row.widget is not None:
            head = getattr(row.widget, "head", row.widget)
            click(head, QPoint(min(60, head.width() // 3), head.height() // 2))
            return
        click(self.viewport(), QPoint(hr.x() + min(60, hr.width() // 3), hr.y() + hr.height() // 2))

    def bring_on_screen(self, row: RowRef) -> None:
        """Scroll so a row's head sits in the upper third of the viewport (a person scrolls to a row
        before clicking it; QTest delivers a click at the row's place on the screen, so the row must be
        there)."""
        tv = self.transcript()
        vp = tv.viewport()
        hr = row.head_rect()
        if 0 < hr.y() < vp.height() - 120 and hr.bottom() < vp.height():
            return
        bar = tv.verticalScrollBar()
        bar.setValue(max(0, bar.value() + hr.y() - vp.height() // 3))
        settle(250)

    def rows_on_screen(self, cls_name: str, n: int = 3) -> List[RowRef]:
        """At least ``n`` rows of a class with their heads on screen at some point, found by starting
        at the live end (always drawn) and paging up through the history, collecting as it goes."""
        tv = self.transcript()
        tv.scroll_to_bottom()
        settle(500)
        bar = tv.verticalScrollBar()
        found: Dict[Any, RowRef] = {}
        for _ in range(60):
            for r in self.rows(cls_name, whole=True):
                found.setdefault(r.block.key if r.block is not None else id(r.widget), r)
            if len(found) >= n or bar.value() == 0:
                break
            bar.setValue(max(0, bar.value() - tv.viewport().height() // 2))
            settle(250)
        return list(found.values())[:n]

    def head_of(self, row: RowRef) -> RowRef:
        return row

    def scroll_to(self, v: int) -> None:
        self.transcript().verticalScrollBar().setValue(v)

    def wait_busy(self, sid: str, on: bool, timeout: float = 20.0) -> None:
        self.c.wait_until(
            lambda: (self.store.rt.get(sid) is not None and self.store.rt[sid].busy) == on, timeout, "busy"
        )


# ---------------------------------------------------------------- scenarios


def open_cold(ctx: Ctx) -> Json:
    """Open the two long conversations from nothing: time to rows, memory, widgets."""
    out: Json = {}
    for name, sid in (("mixed", ctx.mixed), ("heavy", ctx.heavy)):
        mem0 = rss_mb()
        ctx.counters.reset()
        r = ctx.open(sid)
        tv = ctx.transcript()
        r["rss_delta_mb"] = round(rss_mb() - mem0, 1)
        r["widgets"] = len((tv.viewport() if ctx.canvas else tv.widget()).findChildren(QWidget))
        r["items"] = len(ctx.store.get_model(sid).items)
        r["window"] = ctx.counters.scopes["window"].snapshot()
        out[name] = r
    return out


def scroll_wheel(ctx: Ctx) -> Json:
    """Fling through the history with the wheel at 60 notches a second, then back down."""
    ctx.open(ctx.heavy)
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    ctx.counters.reset()
    bar = tv.verticalScrollBar()

    def fling(direction: int, seconds: float, filmed: bool) -> Json:
        """Notches at 120 a second (a fast wheel) for ``seconds``; the film is optional because grabbing
        slows the loop and would understate what the client manages on its own."""
        ctx.counters.reset()
        film = Film(vp, ctx.canvas_color()) if filmed else None
        positions: List[int] = [bar.value()]
        paced = Paced(120, int(120 * seconds))

        def frame() -> None:
            for _ in range(paced.due()):
                wheel(vp, direction)
            positions.append(bar.value())

        p = pump(seconds + 0.3, until=lambda: paced.done, on_frame=frame, film=film)
        travelled = abs(positions[-1] - positions[0])
        out: Json = {
            "stall": p.report(),
            "px_travelled": travelled,
            "px_per_s": round(travelled / max(0.001, p.wall)),
            **sc.snapshot(),
        }
        if film is not None:
            out["blank_fraction"] = film.blank_fraction()
            out["flashes"] = film.flashes()
            # the content moves up by what the value grew (frame i shows the value recorded after frame i-1)
            out["shift"] = film.shifts(expected=lambda i: positions[i] - positions[i - 1] if i < len(positions) else 0)
        return out

    up = fling(+1, 4.0, filmed=False)
    settle(400)
    down = fling(-1, 3.0, filmed=False)
    settle(400)
    up_filmed = fling(+1, 2.5, filmed=True)
    ctx.counters.drop("transcript")
    return {"up": up, "down": down, "up_filmed": up_filmed}


def scroll_drag(ctx: Ctx) -> Json:
    """Drag the scrollbar from the bottom to the top in 80 steps, like perf.py."""
    ctx.open(ctx.heavy)
    tv = ctx.transcript()
    sc = ctx.scope("transcript", tv)
    bar = tv.verticalScrollBar()
    ctx.counters.reset()
    steps: List[float] = []
    top = bar.maximum()
    for k in range(80, -1, -1):
        t0 = time.perf_counter()
        bar.setValue(top * k // 80)
        ctx.c.app.processEvents()
        tv.viewport().repaint()
        steps.append(time.perf_counter() - t0)
    ctx.counters.drop("transcript")
    return {"step": rig.summary(steps), "over_16ms": sum(1 for s in steps if s > 0.0167), **sc.snapshot()}


def _toggle_rows(ctx: Ctx, cls_name: str, n: int, label: str) -> Json:
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    bar = tv.verticalScrollBar()
    results: List[Json] = []
    rows = ctx.rows_on_screen(cls_name, n)
    if not rows:
        ctx.counters.drop("transcript")
        return {"error": f"no visible {cls_name}"}
    for row in rows[:n]:
        # what grows: the row itself, or for a tool head the card around it (the head stays 28px)
        if row.widget is not None and cls_name == "ToolHead" and row.widget.parentWidget() is not None:
            grow = RowRef(ctx, widget=row.widget.parentWidget())
        else:
            grow = row
        ctx.bring_on_screen(row)
        try:
            below = next((r for r in ctx.rows(cls_name) if r.y() > row.y() + 10), None)
        except RuntimeError:
            below = None
        # The first open of a fold is not animated by design (its body is built then), so each row is
        # toggled twice: the second pair runs with the film on, for flashes and the frames the eye sees.
        for action, filmed in (("open", False), ("close", False), ("open", True), ("close", True)):
            ctx.counters.reset()
            sets: List[int] = []
            conn = bar.valueChanged.connect(lambda v, sets=sets: sets.append(v))
            film = Film(vp, ctx.canvas_color()) if filmed else None
            heights: List[int] = []
            below_y: List[int] = []
            stamps: List[float] = []

            def frame(grow=grow, below=below, heights=heights, below_y=below_y, stamps=stamps) -> None:
                try:
                    heights.append(grow.height())
                    stamps.append(time.perf_counter())
                    if below is not None:
                        below_y.append(below.y())
                except RuntimeError:
                    pass

            t0 = time.perf_counter()
            ctx.click_row(row)
            sync = time.perf_counter() - t0
            p = pump(0.8, on_frame=frame, film=film)
            bar.valueChanged.disconnect(conn)
            changes = [i for i in range(1, len(heights)) if heights[i] != heights[i - 1]]
            anim_ms = (stamps[changes[-1]] - stamps[changes[0]]) * 1000 if len(changes) > 1 else 0
            jumps = [abs(below_y[i] - below_y[i - 1]) for i in range(1, len(below_y))]
            r: Json = {
                "action": action,
                "filmed": filmed,
                "sync_ms": round(sync * 1000, 1),
                "stall": p.report(),
                "layout": sc.layout,
                "resize": sc.resize,
                "move": sc.move,
                "paint": sc.paint,
                "scroll_sets": len(sets),
                "height_frames": len(changes),
                "anim_ms": round(anim_ms),
                "fps": round(len(changes) / (anim_ms / 1000), 1) if anim_ms > 0 else 0,
                "below_max_jump_px": max(jumps, default=0),
            }
            if film is not None:
                r["flashes"] = film.flashes()
                r["distinct_frames"] = film.distinct()
            results.append(r)
    ctx.counters.drop("transcript")
    return {label: results, "summary": _toggle_summary(results)}


def _toggle_summary(results: List[Json]) -> Json:
    if not results:
        return {}
    plain = [r for r in results if not r["filmed"]] or results
    return {
        "toggles": len(results),
        "layout_per_toggle": round(sum(r["layout"] for r in plain) / len(plain), 1),
        "paint_per_toggle": round(sum(r["paint"] for r in plain) / len(plain), 1),
        "scroll_sets_per_toggle": round(sum(r["scroll_sets"] for r in plain) / len(plain), 1),
        "sync_max_ms": max(r["sync_ms"] for r in plain),
        "stall_p95_ms": max(r["stall"]["p95"] for r in plain),
        "stall_max_ms": max(r["stall"]["max"] for r in plain),
        "fps_min_animated": min((r["fps"] for r in plain if r["fps"]), default=0),
        "below_max_jump_px": max(r["below_max_jump_px"] for r in results),
        "flashes": sum(r.get("flashes", 0) for r in results),
    }


def fold_toggle(ctx: Ctx) -> Json:
    """Open and close work folds (the one-line summaries of a turn's steps), twice each."""
    ctx.open(ctx.heavy)
    return _toggle_rows(ctx, "WorkFold", 3, "folds")


def _open_folds(ctx: Ctx, n: int = 4) -> None:
    """Open a few work folds near the live end so the rows inside them are on screen. Each fold is
    brought on screen before its head is clicked: opening one moves the ones below it."""
    for row in ctx.rows_on_screen("WorkFold", n)[:n]:
        if row.is_open:
            continue  # left open by an earlier scenario; a click would close it
        ctx.bring_on_screen(row)
        ctx.click_row(row)
        settle(500)
    settle(300)


def thinking_toggle(ctx: Ctx) -> Json:
    """Open and close thinking rows."""
    ctx.open(ctx.heavy)
    _open_folds(ctx)
    return _toggle_rows(ctx, "Thinking", 3, "thinking")


def tool_toggle(ctx: Ctx) -> Json:
    """Open and close tool cards (a big command output, a big diff)."""
    ctx.open(ctx.heavy)
    _open_folds(ctx)
    return _toggle_rows(ctx, "ToolHead", 3, "tools")


def hover_sweep(ctx: Ctx) -> Json:
    """Move the pointer down the conversation in 6px steps, twice."""
    ctx.open(ctx.heavy)
    _open_folds(ctx, 3)
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    ctx.counters.reset()
    ys = list(range(4, vp.height() - 4, 6)) * 2
    paced = Paced(120, len(ys))  # a pointer moving at 720 px/s

    def frame() -> None:
        for _ in range(paced.due()):
            move(vp, QPoint(vp.width() // 3, ys[paced.sent - 1]))

    p = pump(30, until=lambda: paced.done, on_frame=frame)
    ctx.counters.drop("transcript")
    return {"moves": len(ys), "stall": p.report(), **sc.snapshot()}


def streaming_follow(ctx: Ctx) -> Json:
    """A long turn streams in while the reader sits at the bottom: the view follows."""
    ctx.open(ctx.heavy)
    ctx.c.call("mock.pace", {"ms": 3})
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    win = ctx.counters.scopes["window"]
    render_times: List[float] = []
    attr = "_sync" if ctx.canvas else "_render"
    orig = getattr(tv, attr, None)
    if orig is not None:

        def timed() -> None:
            t = time.perf_counter()
            orig()
            render_times.append(time.perf_counter() - t)

        setattr(tv, attr, timed)
    ctx.counters.reset()
    tv.scroll_to_bottom()
    ctx.store.send_message(ctx.heavy, "marathon please")
    ctx.wait_busy(ctx.heavy, True)
    p = pump(120, until=lambda: not ctx.store.rt[ctx.heavy].busy)
    if orig is not None:
        setattr(tv, attr, orig)
    out: Json = {
        "stall": p.report(),
        "render": rig.summary(render_times),
        "layout_per_s": round(sc.layout / max(0.001, p.wall)),
        "paint_per_s": round(sc.paint / max(0.001, p.wall)),
        "transcript": sc.snapshot(),
        "window_widgets_made": win.children_added,
        "items_after": len(ctx.store.get_model(ctx.heavy).items),
    }
    # a second, filmed turn for what the eye sees (the film slows the loop, so its stalls are not reported)
    settle(600)
    ctx.counters.reset()
    tv.scroll_to_bottom()
    ctx.store.send_message(ctx.heavy, "marathon please")
    ctx.wait_busy(ctx.heavy, True)
    film = Film(vp, ctx.canvas_color())
    pump(120, until=lambda: not ctx.store.rt[ctx.heavy].busy, film=film)
    ctx.counters.drop("transcript")
    out["filmed"] = {"frames": len(film.frames), "flashes": film.flashes(), "blank_fraction": film.blank_fraction()}
    return out


def streaming_scrolled_up(ctx: Ctx) -> Json:
    """The same long turn while the reader is up in the history: nothing on screen may move. (Sending
    takes the reader to their own message first, as it should; they scroll up once the turn runs.)"""
    ctx.open(ctx.heavy)
    ctx.c.call("mock.pace", {"ms": 3})
    tv = ctx.transcript()
    vp = tv.viewport()
    bar = tv.verticalScrollBar()
    sc = ctx.scope("transcript", tv)
    ctx.store.send_message(ctx.heavy, "marathon please")
    ctx.wait_busy(ctx.heavy, True)
    settle(600)
    ctx.scroll_to(max(0, bar.maximum() - 2400))
    settle(400)
    v0 = bar.value()
    sets: List[int] = []
    conn = bar.valueChanged.connect(lambda v: sets.append(v))
    ctx.counters.reset()
    # the film leaves out the bottom 70px, where the "Latest" pill appears (that one may change)
    film = Film(vp, ctx.canvas_color(), region=(0, 0, vp.width(), max(1, vp.height() - 70)))
    p = pump(120, until=lambda: not ctx.store.rt[ctx.heavy].busy, film=film)
    bar.valueChanged.disconnect(conn)
    ctx.counters.drop("transcript")
    return {
        "stall": p.report(),
        "scroll_moved_px": abs(bar.value() - v0),
        "scroll_sets": len(sets),
        "shift": film.shifts(),
        "flashes": film.flashes(),
        "layout": sc.layout,
        "paint": sc.paint,
        "latest_pill_shown": bool(getattr(tv, "jump", None) and tv.jump.isVisible()),
    }


def typing(ctx: Ctx) -> Json:
    """Type 240 characters into the composer: the conversation must not notice."""
    ctx.open(ctx.heavy)
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    view = ctx.win.session_view
    edit = view.composer.edit
    edit.setFocus()
    settle(200)
    ctx.counters.reset()
    film = Film(vp, ctx.canvas_color())
    text = ("the quick brown fox jumps over the lazy dog " * 6)[:240]
    paced = Paced(40, len(text))  # a fast typist

    def frame() -> None:
        for _ in range(paced.due()):
            type_text(edit, text[paced.sent - 1])

    p = pump(30, until=lambda: paced.done, on_frame=frame, film=film)
    edit.clear()
    ctx.counters.drop("transcript")
    return {"chars": len(text), "stall": p.report(), "shift": film.shifts(), **sc.snapshot()}


def resize_window(ctx: Ctx) -> Json:
    """Narrow the window from 1480 to 1000 and back in 24 steps (the column reflows every step)."""
    ctx.open(ctx.heavy)
    tv = ctx.transcript()
    vp = tv.viewport()
    sc = ctx.scope("transcript", tv)
    ctx.counters.reset()
    widths = list(range(1480, 999, -40)) + list(range(1000, 1481, 40))
    film = Film(vp, ctx.canvas_color(), region=(0, 0, 640, min(600, vp.height())))
    steps: List[float] = []
    for w in widths:
        t0 = time.perf_counter()
        ctx.win.resize(w, 940)
        ctx.c.app.processEvents()
        steps.append(time.perf_counter() - t0)
        film.grab()
    settle(300)
    ctx.counters.drop("transcript")
    return {
        "steps": len(widths),
        "step": rig.summary(steps),
        "over_50ms": sum(1 for s in steps if s > 0.05),
        "flashes": film.flashes(),
        **sc.snapshot(),
    }


def panel_tabs(ctx: Ctx) -> Json:
    """Open the side panel, visit every tab, drag it wider and narrower, with real data behind the tabs."""
    ctx.open(ctx.heavy)
    _seed_panel_data(ctx)
    from pilcrow_qt.store import PANEL_TABS

    out: Json = {"tabs": {}}
    ctx.store.set_ui(panel=True, panel_tab="changes")
    settle(600)
    panel_host = ctx.win.panel_host
    sc = ctx.scope("panel", panel_host)
    for tab in PANEL_TABS:
        ctx.counters.reset()
        t0 = time.perf_counter()
        ctx.store.set_ui(panel_tab=tab)
        p = pump(1.2)
        out["tabs"][tab] = {
            "first_turn_ms": round(p.stalls[0] * 1000, 1) if p.stalls else 0,
            "stall_max_ms": p.report()["max"],
            "widgets_made": sc.children_added,
            "layout": sc.layout,
            "paint": sc.paint,
            "total_ms": round((time.perf_counter() - t0) * 1000),
        }
    # drag the panel seam
    ctx.store.set_ui(panel_tab="changes")
    settle(300)
    ctx.counters.reset()
    steps: List[float] = []
    for dx in [-12] * 16 + [12] * 16:
        t0 = time.perf_counter()
        ctx.win._drag_panel(dx)
        ctx.c.app.processEvents()
        steps.append(time.perf_counter() - t0)
    out["drag"] = {"step": rig.summary(steps), "layout": sc.layout, "paint": sc.paint}
    ctx.counters.drop("panel")
    return out


def panel_while_streaming(ctx: Ctx) -> Json:
    """The side panel open on each tab while a long turn streams: how much does the panel redo?"""
    ctx.open(ctx.heavy)
    _seed_panel_data(ctx)
    ctx.c.call("mock.pace", {"ms": 2})
    out: Json = {}
    panel_host = ctx.win.panel_host
    for tab in ("changes", "tasks", "files", "info", "history"):
        ctx.store.set_ui(panel=True, panel_tab=tab)
        settle(700)
        sc = ctx.scope("panel", panel_host)
        tv = ctx.transcript()
        tsc = ctx.scope("transcript", tv)
        ctx.counters.reset()
        tv.scroll_to_bottom()
        ctx.store.send_message(ctx.heavy, "marathon please")
        ctx.wait_busy(ctx.heavy, True)
        p = pump(120, until=lambda: not ctx.store.rt[ctx.heavy].busy)
        out[tab] = {
            "stall": p.report(),
            "panel": sc.snapshot(),
            "panel_widgets_made_per_s": round(sc.children_added / max(0.001, p.wall)),
            "transcript_layout": tsc.layout,
        }
        ctx.counters.drop("panel")
        ctx.counters.drop("transcript")
        settle(500)
    ctx.store.set_ui(panel=False)
    return out


def _seed_panel_data(ctx: Ctx) -> None:
    """Give the tabs something to show: a 40-file diff, 30 tasks, 300 timeline steps, a 400-entry folder."""
    proj = "/home/tester/code/pilcrow"

    def hunk(k: int, n: int) -> Json:
        lines = []
        o = nn = 1
        for j in range(n):
            m = j % 4
            if m == 1:
                lines.append({"t": "-", "s": f"  const v{j} = read({k});", "o": o})
                o += 1
            elif m == 2:
                lines.append({"t": "+", "s": f"  const v{j} = readSafe({k}); // checked", "n": nn})
                nn += 1
            else:
                lines.append({"t": " ", "s": f"  use(v{j});", "o": o, "n": nn})
                o += 1
                nn += 1
        return {"oldStart": 1, "oldLines": o - 1, "newStart": 1, "newLines": nn - 1, "header": "", "lines": lines}

    files = [
        {
            "path": f"src/mod{k}.ts",
            "oldPath": None,
            "status": "M",
            "binary": False,
            "untracked": False,
            "additions": 12,
            "deletions": 12,
            "hunks": [hunk(k, 48), hunk(k + 1, 24)],
            "truncated": False,
        }
        for k in range(40)
    ]
    ctx.c.call(
        "mock.answer",
        {"method": "git.diff", "result": {"isRepo": True, "root": proj, "branch": "feature/bench", "files": files}},
    )
    now = time.time() * 1000
    tasks = []
    for k in range(30):
        running = k < 6
        tasks.append(
            {
                "id": f"t{k}",
                "type": "local_bash" if k % 2 else "local_agent",
                "status": "running" if running else ("completed" if k % 3 else "failed"),
                "description": f"Background task {k}: build module {k}",
                "startedAt": now - 60_000 * (k + 1),
                "endedAt": None if running else now - 30_000 * k,
                "progress": f"compiling src/mod{k}.ts" if running else None,
                "summary": None if running else f"done in {k + 1}s",
                "usage": {"tool_uses": k, "total_tokens": 1200 * k},
            }
        )
    ctx.c.call("mock.patch", {"id": ctx.heavy, "state": {"tasks": tasks}})
    steps = []
    for n in range(300):
        kind = ("message", "tool", "tool", "tool")[n % 4]
        steps.append(
            {
                "n": n,
                "at": now - (300 - n) * 20_000,
                "kind": kind,
                "tool": "Edit" if kind == "tool" else None,
                "label": f"Turn {n // 4 + 1}" if kind == "message" else f"src/mod{n}.ts",
                "changed": kind == "tool",
                "files": [f"src/mod{n}.ts"] if kind == "tool" else [],
                "add": n % 9,
                "del": n % 5,
                "toolUseId": f"toolu_{n}" if kind == "tool" else None,
                "uuid": f"m-{n}" if kind == "message" else None,
            }
        )
    ctx.c.call("mock.answer", {"method": "timeline.steps", "result": {"cwd": proj, "enabled": True, "steps": steps}})
    entries = [{"name": f"dir{k}", "type": "dir", "path": f"{proj}/dir{k}"} for k in range(40)] + [
        {"name": f"file{k}.ts", "type": "file", "size": 1000 + k, "path": f"{proj}/file{k}.ts"} for k in range(360)
    ]
    ctx.c.call("mock.answer", {"method": "fs.list", "result": {"dir": proj, "entries": entries}})
    ctx.store.bump_fs()
    settle(400)


def switch_session(ctx: Ctx) -> Json:
    """Switch between the two long conversations ten times."""
    ctx.open(ctx.heavy)
    ctx.open(ctx.mixed)
    win = ctx.counters.scopes["window"]
    times: List[float] = []
    made: List[int] = []
    mem0 = rss_mb()
    for k in range(10):
        sid = ctx.heavy if k % 2 == 0 else ctx.mixed
        ctx.counters.reset()
        t0 = time.perf_counter()
        ctx.store.open_session(sid)
        ctx.c.wait_until(lambda: ctx.transcript() is not None and ctx.transcript().units(), 30, "switch")
        ctx.c.app.processEvents()
        ctx.transcript().viewport().grab()
        times.append(time.perf_counter() - t0)
        made.append(win.children_added)
        settle(250)
    return {
        "switch": rig.summary(times),
        "widgets_made_per_switch": round(sum(made) / len(made)),
        "rss_growth_mb": round(rss_mb() - mem0, 1),
    }


def view_mode_cycle(ctx: Ctx) -> Json:
    """Cycle Normal → Thinking → Verbose → Normal (every row is drawn again)."""
    ctx.open(ctx.heavy)
    out: Json = {}
    for mode in ("thinking", "verbose", "normal"):
        ctx.counters.reset()
        t0 = time.perf_counter()
        ctx.store.set_ui(view=mode)
        ctx.c.app.processEvents()
        ctx.transcript().viewport().grab()
        to_paint = time.perf_counter() - t0
        p = pump(1.0)
        out[mode] = {
            "to_paint_ms": round(to_paint * 1000),
            "stall_max_ms": p.report()["max"],
            "widgets_made": ctx.counters.scopes["window"].children_added,
        }
    return out


def theme_toggle(ctx: Ctx) -> Json:
    """Dark → light → dark with a long conversation and the panel open."""
    ctx.open(ctx.heavy)
    ctx.store.set_ui(panel=True, panel_tab="changes")
    settle(500)
    out: Json = {}
    for dark in (False, True):
        ctx.counters.reset()
        t0 = time.perf_counter()
        ctx.theme.theme.apply(dark)
        ctx.c.app.processEvents()
        ctx.transcript().viewport().grab()
        to_paint = time.perf_counter() - t0
        p = pump(0.6)
        out["light" if not dark else "dark"] = {
            "to_paint_ms": round(to_paint * 1000),
            "stall_max_ms": p.report()["max"],
            "window": ctx.counters.scopes["window"].snapshot(),
        }
    ctx.store.set_ui(panel=False)
    return out


def find(ctx: Ctx) -> Json:
    """Ctrl+F: the search draws every page before it can count matches."""
    ctx.open(ctx.heavy)
    tv = ctx.transcript()
    t0 = time.perf_counter()
    tv.open_find()
    ctx.c.app.processEvents()
    bar = tv.find_bar
    t_open = time.perf_counter() - t0
    t1 = time.perf_counter()
    if bar is not None:
        type_text(bar.field, "module")
    # the matches are counted once every row the search reads is there (the widget transcript had to
    # draw its placeholder pages first; the canvas reads the model)
    p = pump(
        20,
        until=lambda: (
            (bar is not None and bool(bar.matches) and not any(pg.far for pg in getattr(tv, "_pages", [])))
            or time.perf_counter() - t1 > 19
        ),
    )
    t_search = time.perf_counter() - t1
    entries = len(tv.find_entries())
    if bar is not None:
        bar.close_bar()
    settle(300)
    return {
        "open_ms": round(t_open * 1000),
        "search_ms": round(t_search * 1000),
        "stall_max_ms": p.report()["max"],
        "entries": entries,
        "widgets": len((tv.viewport() if ctx.canvas else tv.widget()).findChildren(QWidget)),
    }


def idle(ctx: Ctx) -> Json:
    """CPU while nothing happens: a finished conversation, then one that looks busy (timers run)."""
    ctx.open(ctx.heavy)
    settle(500)
    p_quiet = pump(3.0)
    ctx.c.call("mock.patch", {"id": ctx.heavy, "state": {"status": "running", "turnStartedAt": time.time() * 1000}})
    settle(500)
    p_busy = pump(3.0)
    ctx.c.call("mock.patch", {"id": ctx.heavy, "state": {"status": "idle", "turnStartedAt": None}})
    settle(300)
    return {"quiet_cpu_pct": p_quiet.report()["cpu_pct"], "busy_look_cpu_pct": p_busy.report()["cpu_pct"]}


SCENARIOS: Dict[str, Callable[[Ctx], Json]] = {
    # in the order a person meets them: the view mode and theme switches run while only the live
    # pages are drawn (the realistic state), before the scroll scenarios draw the history
    "open_cold": open_cold,
    "view_mode_cycle": view_mode_cycle,
    "theme_toggle": theme_toggle,
    "fold_toggle": fold_toggle,
    "thinking_toggle": thinking_toggle,
    "tool_toggle": tool_toggle,
    "hover_sweep": hover_sweep,
    "streaming_follow": streaming_follow,
    "streaming_scrolled_up": streaming_scrolled_up,
    "typing": typing,
    "resize_window": resize_window,
    "switch_session": switch_session,
    "panel_tabs": panel_tabs,
    "panel_while_streaming": panel_while_streaming,
    "scroll_wheel": scroll_wheel,
    "scroll_drag": scroll_drag,
    "find": find,
    "idle": idle,
}

_ = (Optional, QApplication, Qt)
