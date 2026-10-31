"""Folds opening, a fling through a long conversation, a resize storm, a theme switch and a font
scale change on a long session: the time each takes, the stalls, the cold placeholders painted, and
whether the picture is consistent afterwards (every visible block measured at the current width)."""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWheelEvent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: E402

from core import Probe, Session, app, assistant, pump, timed, tool_result, tool_use, user

from pilcrow_qt import theme
from pilcrow_qt.canvas.scene import GRADE_EXACT, Group


def long_session(turns: int):
    msgs = []
    for i in range(turns):
        msgs.append(user(f"Turn {i}: check module {i}"))
        kind = i % 4
        if kind == 0:
            msgs.append(
                assistant(
                    [
                        {
                            "type": "text",
                            "text": f"Module {i} parses the configuration for stage {i % 7}.\n\n"
                            + "It is small and has no side effects. " * 4,
                        }
                    ]
                )
            )
        elif kind == 1:
            tid = f"r-{i}"
            msgs.append(assistant([{"type": "text", "text": f"Reading file {i}."}]))
            msgs.append(assistant([tool_use(tid, "Read", {"file_path": f"/proj/src/mod{i}.ts"})]))
            msgs.append(tool_result(tid, "\n".join(f"{k}→line {k} of mod{i}" for k in range(1, 30))))
            msgs.append(assistant([{"type": "text", "text": f"File {i} is fine."}]))
        elif kind == 2:
            tid = f"e-{i}"
            msgs.append(
                assistant(
                    [
                        tool_use(
                            tid,
                            "Edit",
                            {"file_path": f"/proj/src/mod{i}.ts", "old_string": "recieve\n", "new_string": "receive\n"},
                        )
                    ]
                )
            )
            msgs.append(tool_result(tid, "ok"))
            tid2 = f"b-{i}"
            msgs.append(assistant([tool_use(tid2, "Bash", {"command": "npm run check"})]))
            msgs.append(tool_result(tid2, "ok\n" * 5))
            msgs.append(
                assistant(
                    [
                        {
                            "type": "text",
                            "text": f"Fixed the typo in module {i}.\n\n```ts\nconst a{i} = 1;\nconst b{i} = 2;\n```",
                        }
                    ]
                )
            )
        else:
            msgs.append(
                assistant([{"type": "thinking", "thinking": "Short thought about module " + str(i) + ". " * 5}])
            )
            msgs.append(
                assistant(
                    [{"type": "text", "text": f"| a | b |\n|---|---|\n| {i} | {i * 2} |\n\n- one {i}\n- two {i}"}]
                )
            )
    return msgs


def stale_visible(v) -> int:
    """Visible leaves measured at another width, or still estimated."""
    n = 0
    for b in v.visible_blocks():
        if isinstance(b, Group):
            continue
        if b.h_width != v.col_w or b.grade < GRADE_EXACT or not b.warm:
            n += 1
    return n


def wheel(v, dy: int) -> None:
    vp = v.viewport()
    pos = QPoint(vp.width() // 2, vp.height() // 2)
    e = QWheelEvent(
        pos,
        vp.mapToGlobal(pos),
        QPoint(0, dy),
        QPoint(0, dy * 8),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    v.wheelEvent(e)


def run_fling(turns: int = 1500) -> str:
    s = Session("fling", long_session(turns))
    v = s.view
    probe = Probe(v)
    probe.start()
    v.cold_paints = 0
    t0 = time.perf_counter()
    for _ in range(120):  # a long fling upwards: 120 notches at 8 ms
        wheel(v, 120)
        pump(8)
    pump(600)
    t = (time.perf_counter() - t0) * 1000
    probe.stop()
    out = (
        f"fling {turns} turns: {t:.0f}ms cold_paints={v.cold_paints} stale_after={stale_visible(v)} total_h={v.scene.total}\n   "
        + probe.report("")
    )
    # the first paint after a session switch, cold
    s.close()
    return out


def run_open() -> str:
    msgs = [user("do a lot")]
    for i in range(1200):
        tid = f"t-{i}"
        msgs.append(assistant([tool_use(tid, "Bash", {"command": f"echo step {i}"})]))
        msgs.append(tool_result(tid, f"step {i}\n" * 3))
    msgs.append(assistant([{"type": "text", "text": "Done."}]))
    s = Session("open", msgs)
    v = s.view
    from pilcrow_qt.canvas.blocks.fold import FoldGroup

    fold = next(b for b in v.scene.root.walk() if isinstance(b, FoldGroup))
    v.scroll_to_block(fold, 0.1, only_if_hidden=False)
    pump(100)
    probe = Probe(v)
    probe.start()
    t_open = timed(lambda: fold.activate(fold.hit(10, 10), v))
    pump(700)  # the reveal animation
    probe.stop()
    h_after_anim = fold.h
    pump(300)
    out = (
        f"open fold of 1200 steps: activate={t_open:.0f}ms h={h_after_anim}->{fold.h} stale_after={stale_visible(v)}\n   "
        + probe.report("")
    )
    # the Explored line inside: open it too
    from pilcrow_qt.canvas.blocks.fold import Explore

    ex = next((b for b in v.scene.root.walk() if isinstance(b, Explore)), None)
    if ex is not None:
        probe2 = Probe(v)
        probe2.start()
        t2 = timed(lambda: ex.activate(ex.hit(10, 10), v))
        pump(700)
        probe2.stop()
        out += f"\n   open Explored of {len(ex.children)} cards: activate={t2:.0f}ms\n   " + probe2.report("")
    s.close()
    return out


def run_resize(turns: int = 600) -> str:
    s = Session("resize", long_session(turns), width=1480)
    v = s.view
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum() // 2)
    pump(300)
    key_before = v.first_visible_key()
    probe = Probe(v)
    probe.start()
    steps = []
    for w in list(range(1480, 880, -20)) + list(range(880, 1480, 20)):
        t = timed(lambda w=w: s.win.resize(w, 900))
        pump(6)
        steps.append(t)
    pump(600)
    probe.stop()
    out = (
        f"resize storm 60 steps: per-step avg={sum(steps) / len(steps):.1f}ms max={max(steps):.0f}ms "
        f"stale_after={stale_visible(v)} anchor_kept={v.first_visible_key() == key_before}\n   " + probe.report("")
    )
    s.close()
    return out


def run_theme_scale(turns: int = 600) -> str:
    s = Session("theme", long_session(turns))
    v = s.view
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum() // 2)
    pump(300)
    probe = Probe(v)
    probe.start()
    t_theme = timed(lambda: theme.theme.apply(False))
    pump(300)
    t_back = timed(lambda: theme.theme.apply(True))
    pump(300)
    t_scale = timed(lambda: theme.theme.set_scale(1.2))
    pump(300)
    t_scale0 = timed(lambda: theme.theme.set_scale(1.0))
    pump(300)
    probe.stop()
    out = (
        f"theme light={t_theme:.0f}ms dark={t_back:.0f}ms | scale 1.2={t_scale:.0f}ms 1.0={t_scale0:.0f}ms stale_after={stale_visible(v)}\n   "
        + probe.report("")
    )
    s.close()
    return out


def run_switch(turns: int = 1500) -> str:
    """Opening a long session for the first time: how long until the first paint and the end."""
    from pilcrow_qt.canvas.transcript import forget_scenes

    forget_scenes()
    s = Session("switch-a", long_session(turns))
    t_first = 0.0
    s.close()
    # the second open of the same model: the scene is rebuilt (LRU forgot it)
    t0 = time.perf_counter()
    s = Session("switch-b", long_session(turns))
    t_first = (time.perf_counter() - t0) * 1000 - 300  # less the settle pump
    out = f"open {turns}-turn session: build+first paint ~{t_first:.0f}ms, total_h={s.view.scene.total}, warm={len(s.view._warm)}"
    s.close()
    return out


if __name__ == "__main__":
    app()
    which = sys.argv[1:] or ["fling", "open", "resize", "theme", "switch"]
    for w in which:
        fn = {
            "fling": run_fling,
            "open": run_open,
            "resize": run_resize,
            "theme": run_theme_scale,
            "switch": run_switch,
        }[w]
        print(fn(), flush=True)
