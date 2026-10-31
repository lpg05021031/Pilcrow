"""The rest of the window under a storm: the pointer sweeping across tool heads and a long reply,
the sidebar rebuilt for every session-list change with 500 sessions, and the composer growing."""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import Probe, Session, app, assistant, pump, timed, tool_result, tool_use, user  # noqa: E402

from pilcrow_qt.store import store  # noqa: E402


def run_hover() -> str:
    msgs = [user("go")]
    for i in range(12):
        tid = f"t-{i}"
        msgs.append(assistant([tool_use(tid, "Read", {"file_path": f"/proj/src/mod{i}.ts"})]))
        msgs.append(tool_result(tid, "\n".join(f"{k}→line {k}" for k in range(1, 20))))
    msgs.append(
        assistant(
            [
                {
                    "type": "text",
                    "text": "\n\n".join(f"Paragraph {i}. " + "words " * 80 for i in range(10))
                    + "\n\n```ts\nconst a = 1;\n```\n\nSee [a link](https://e.com) here.",
                }
            ]
        )
    )
    s = Session("hover", msgs)
    v = s.view
    from pilcrow_qt.canvas.blocks.fold import FoldGroup

    fold = next(b for b in v.scene.root.walk() if isinstance(b, FoldGroup))
    v.toggle_group(fold, True, animate=False)
    pump(200)
    v.verticalScrollBar().setValue(0)
    pump(100)
    vp = v.viewport()
    probe = Probe(v)
    probe.start()
    t0 = time.perf_counter()
    n = 0
    for y in range(10, vp.height() - 10, 3):  # a sweep down the whole viewport, 3px a step
        QTest.mouseMove(vp, QPoint(v.col_x + 60, y))
        pump(2)
        n += 1
    for y in range(vp.height() - 10, 10, -3):
        QTest.mouseMove(vp, QPoint(v.col_x + 400, y))
        pump(2)
        n += 1
    secs = time.perf_counter() - t0
    probe.stop()
    out = (
        f"hover sweep {n} moves in {secs * 1000:.0f}ms: paints={probe.paints} full-height={sum(1 for h in probe.paint_rects if h >= 600)}\n   "
        + probe.report("")
    )
    s.close()
    return out


def run_sidebar() -> str:
    from pilcrow_qt.widgets.sidebar import Sidebar

    now = time.time() * 1000
    sessions = [
        {
            "id": f"s{i}",
            "title": f"Session {i}: a title that is reasonably long",
            "cwd": f"/home/user/project{i % 7}",
            "touched": now - i * 60000,
            "status": "idle" if i % 5 else "running",
            "archived": False,
        }
        for i in range(500)
    ]
    store.sessions = sessions
    store.sessions_loaded = True
    sb = Sidebar()
    sb.resize(260, 900)
    sb.show()
    pump(200)
    times = []
    for k in range(20):
        sessions[k]["touched"] = now + k  # one row's time changes: the list is re-sorted
        store.sessions = list(sessions)
        times.append(timed(lambda: store.sessions_changed.emit(store.sessions)))
        pump(5)
    sb.close()
    return f"sidebar with 500 sessions: rebuild per sessions_changed avg={sum(times) / len(times):.1f}ms max={max(times):.0f}ms"


def run_composer() -> str:
    from pilcrow_qt.widgets.composer import Composer

    s = Session("composer", [user("hi"), assistant([{"type": "text", "text": "hello"}])])
    comp = Composer(s.sid, "/proj")
    comp.resize(800, 100)
    comp.show()
    pump(100)
    ed = getattr(comp, "edit", None) or getattr(comp, "editor", None) or getattr(comp, "text", None)
    if ed is None:
        s.close()
        return "composer: no editor attribute found"
    big = "\n".join(f"line {i} of a pasted log with some words in it" for i in range(3000))
    t_paste = timed(lambda: ed.setPlainText(big))
    pump(100)
    t_key = timed(lambda: QTest.keyClick(ed, Qt.Key_X))
    pump(50)
    h = comp.height()
    out = f"composer: paste 3000 lines={t_paste:.0f}ms, one keystroke after={t_key:.1f}ms, height={h}"
    comp.close()
    s.close()
    return out


if __name__ == "__main__":
    app()
    import sys

    for w in sys.argv[1:] or ["hover", "sidebar", "composer"]:
        print({"hover": run_hover, "sidebar": run_sidebar, "composer": run_composer}[w](), flush=True)
