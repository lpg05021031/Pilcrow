"""Opening a long conversation against the mock server: what the person sees between the click and
the settled view. Frames are sampled every 20 ms: the scroll value, the scene's height, whether
content is visible at the top before the jump to the end (a flash), and the time to settle."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from harness import Client, start_mock  # noqa: E402


def main(turns: int) -> None:
    port = 5631
    mock = start_mock(port, turns)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        from pilcrow_qt.store import store

        t0 = time.perf_counter()
        store.open_session("sess-long")
        samples = []
        settled_at = None
        last = None
        while time.perf_counter() - t0 < 12:
            c.sleep(20)
            sv = getattr(c.win, "session_view", None)
            tr = getattr(sv, "transcript", None)
            if tr is None or getattr(tr, "sid", None) != "sess-long":
                samples.append((round((time.perf_counter() - t0) * 1000), "no-transcript"))
                continue
            vb = tr.view.verticalScrollBar()
            r = store.rt.get("sess-long")
            st = (vb.value(), vb.maximum(), tr.scene.total, bool(r and r.loaded), tr._placed)
            samples.append((round((time.perf_counter() - t0) * 1000), st))
            if r is not None and r.loaded and tr._placed and vb.value() == vb.maximum() and st == last:
                settled_at = samples[-1][0]
                break
            last = st
        flash = [t for t, st in samples if isinstance(st, tuple) and st[0] == 0 and st[2] > 900]
        print(
            f"open sess-long ({turns} turns): settled at {settled_at}ms; frames showing the TOP with content taller than the viewport: {len(flash)} ({flash[:1]}..{flash[-1:]} ms)"
        )
        for t, st in samples[:: max(1, len(samples) // 25)]:
            print("  ", t, st)
        c.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1500)
