"""Measure the client on a long conversation, offscreen, against the mock server.

    python qt/tests/perf.py [--turns 400] [--port 5323]

Prints: time to open a conversation of N turns (RPC answer → rows on screen), the time the first
paint after opening takes, average frame cost while a reply streams, and the process CPU share
while idle. These are the numbers the Qt client exists for.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=400)
    ap.add_argument("--port", type=int, default=5323)
    args = ap.parse_args()
    from harness import Client, start_mock

    mock = start_mock(args.port, args.turns)
    try:
        c = Client(args.port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.call("mock.pace", {"ms": 8})
        # ---- open a long conversation
        t0 = time.perf_counter()
        c.store.open_session("sess-long")
        c.wait_until(lambda: "sess-long" in c.store.rt and c.store.rt["sess-long"].loaded, 30, "loaded")
        t_loaded = time.perf_counter() - t0
        from pilcrow_qt.canvas.transcript import Transcript

        c.wait_until(lambda: c.find(Transcript), 10, "transcript widget")
        tv = c.find(Transcript)[0]
        c.wait_until(lambda: tv.units(), 10, "units")
        t_rows = time.perf_counter() - t0
        c.app.processEvents()
        p0 = time.perf_counter()
        tv.grab()  # forces a full paint of what is on screen
        t_paint = time.perf_counter() - p0
        drawn = sum(1 for u in getattr(tv, "pages", []) if getattr(u, "drawn", True)) if hasattr(tv, "pages") else -1
        print(
            f"open {args.turns} turns: transcript loaded {t_loaded * 1000:.0f} ms, rows on screen {t_rows * 1000:.0f} ms, first paint {t_paint * 1000:.1f} ms"
            + (f", pages drawn {drawn}" if drawn >= 0 else "")
        )

        # ---- idle CPU: a real event loop (the harness's polling sleep would be the cost itself)
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(3000, loop.quit)
        proc_t0, wall0 = time.process_time(), time.perf_counter()
        loop.exec()
        idle = (time.process_time() - proc_t0) / (time.perf_counter() - wall0) * 100
        print(f"idle (3 s in the event loop, a long conversation open): {idle:.1f}% of one core")

        # ---- streaming
        frames = []
        c.store.send_message("sess-long", "slow reply please")
        c.wait_until(lambda: c.store.rt["sess-long"].busy, 10, "busy")
        proc_t0, wall0 = time.process_time(), time.perf_counter()
        while c.store.rt["sess-long"].busy and time.perf_counter() - wall0 < 30:
            f0 = time.perf_counter()
            c.app.processEvents()
            tv.viewport().repaint()
            frames.append(time.perf_counter() - f0)
            time.sleep(0.016)
        stream_cpu = (time.process_time() - proc_t0) / (time.perf_counter() - wall0) * 100
        frames.sort()
        p50, p95 = frames[len(frames) // 2], frames[int(len(frames) * 0.95)]
        print(
            f"streaming: {len(frames)} frames, repaint p50 {p50 * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms, CPU {stream_cpu:.0f}% of one core (includes the mock's wire traffic)"
        )

        # ---- scrolling through the history
        bar = tv.verticalScrollBar()
        steps = 0
        t0 = time.perf_counter()
        for v in range(bar.maximum(), -1, -max(300, bar.maximum() // 60)):
            bar.setValue(v)
            c.app.processEvents()
            tv.viewport().repaint()
            steps += 1
        print(
            f"scroll to the top in {steps} steps: {(time.perf_counter() - t0) * 1000 / max(1, steps):.1f} ms per step (pages drawn on the way)"
        )
        c.close()
        return 0
    finally:
        mock.kill()


if __name__ == "__main__":
    sys.exit(main())
