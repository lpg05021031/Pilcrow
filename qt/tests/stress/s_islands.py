"""Islands (real widgets in the conversation): do they move with the painted content on a scroll,
or a frame late? And the live blocks: how often does the clock repaint while a card runs?"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: E402

from core import Probe, Session, app, assistant, pump, tool_result, tool_use, user

from pilcrow_qt.canvas.blocks.island import IslandBlock
from pilcrow_qt.store import store


def perm_bash(tid: str, request_id: str = "req-1"):
    return {
        "requestId": request_id,
        "toolUseId": tid,
        "toolName": "Bash",
        "input": {"command": "npm run build", "description": "Build"},
        "suggestions": [],
    }


def run_islands() -> str:
    msgs = [user("go")]
    for i in range(30):
        msgs.append(assistant([{"type": "text", "text": f"Paragraph {i}. " + "words " * 50}]))
    msgs.append(assistant([tool_use("wait-1", "Bash", {"command": "npm run build"})]))
    for i in range(30):
        msgs.append(assistant([{"type": "text", "text": f"Later paragraph {i}. " + "words " * 50}]))
    s = Session("islands", msgs)
    store.rt[s.sid].st["pending"] = [perm_bash("wait-1")]
    store.rt[s.sid].st["status"] = "requires_action"
    store.rt_changed.emit(s.sid)
    pump(400)
    v = s.view
    from pilcrow_qt.canvas.blocks.fold import FoldGroup

    fold = next(b for b in v.scene.root.walk() if isinstance(b, FoldGroup))
    v.scroll_to_block(fold, 0.3, only_if_hidden=False)
    pump(500)
    islands = [b for b in v.scene.root.walk() if isinstance(b, IslandBlock)]
    if not islands:
        s.close()
        return "islands: none created"
    isl = islands[0]
    v.scroll_to_block(isl, 0.4, only_if_hidden=False)
    pump(300)
    out = []
    for dy in (120, -80, 300):
        vb = v.verticalScrollBar()
        vb.setValue(vb.value() + dy)  # a person's scroll, synchronous
        r_now = v.block_rect(isl)
        g_now = isl.widget.geometry()
        off_now = g_now.y() - (r_now.y() + isl.margin)
        pump(40)
        r_later = v.block_rect(isl)
        g_later = isl.widget.geometry()
        off_later = g_later.y() - (r_later.y() + isl.margin)
        out.append(f"scroll {dy:+d}: island offset right after={off_now}px after a frame={off_later}px")
    # the card stays put while prose streams above it (anchor), and follows while content grows below
    s.close()
    return "islands:\n   " + "\n   ".join(out)


def run_live() -> str:
    msgs = [user("go")]
    msgs.append(
        assistant(
            [
                tool_use(
                    "agent-1",
                    "Agent",
                    {"description": "Explore", "prompt": "Look around " * 40, "subagent_type": "Explore"},
                )
            ]
        )
    )
    for i in range(40):
        msgs.append(assistant([tool_use(f"sub-{i}", "Grep", {"pattern": f"p{i}"})], parent="agent-1"))
        msgs.append(tool_result(f"sub-{i}", "x", {"filenames": [f"/proj/a{i}.ts", f"/proj/b{i}.ts"]}, parent="agent-1"))
    s = Session("live", msgs)
    store.rt[s.sid].st["status"] = "running"
    store.rt_changed.emit(s.sid)
    pump(300)
    v = s.view
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    card = next(b for b in v.scene.root.walk() if isinstance(b, ToolBlock))
    v.toggle_group(card, True, animate=False)
    pump(300)
    probe = Probe(v)
    probe.start()
    t0 = time.perf_counter()
    pump(2000)
    probe.stop()
    secs = time.perf_counter() - t0
    out = (
        f"running agent card open (h={card.h}): paints/s={probe.paints / secs:.0f}, full-height paints={sum(1 for h in probe.paint_rects if h >= 600)}, rect heights sample={sorted(set(probe.paint_rects))[:6]}\n   "
        + probe.report("")
    )
    s.close()
    return out


if __name__ == "__main__":
    app()
    print(run_islands(), flush=True)
    print(run_live(), flush=True)
