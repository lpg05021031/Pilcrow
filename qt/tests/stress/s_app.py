"""The whole window against the mock server: a reply streaming into the real session view (what the
header and the composer do per flush), the side panel and the sidebar toggled while a long
conversation is open (placeholders during the width animation), a resize storm of the window."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core import Probe, pump, timed  # noqa: E402
from harness import Client, start_mock  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


class Counter:
    def __init__(self, obj, name: str) -> None:
        self.n = 0
        self.ms = 0.0
        self.obj, self.name = obj, name
        self.orig = getattr(obj, name)
        c = self

        def wrapped(*a, **k):
            t0 = time.perf_counter()
            try:
                return c.orig(*a, **k)
            finally:
                c.n += 1
                c.ms += (time.perf_counter() - t0) * 1000

        setattr(obj, name, wrapped)

    def stop(self) -> None:
        setattr(self.obj, self.name, self.orig)

    def __str__(self) -> str:
        return f"{self.name}: {self.n} calls, {self.ms:.0f}ms total, {self.ms / max(1, self.n):.2f}ms each"


def main() -> None:
    port = 5641
    mock = start_mock(port, 400)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        from pilcrow_qt.store import store

        c.open("sess-long")
        c.wait_until(lambda: (store.rt.get("sess-long") or None) and store.rt["sess-long"].loaded, 20, "loaded")
        c.sleep(800)
        sv = c.win.session_view
        tr = sv.transcript
        v = tr.view
        comp = sv.composer if hasattr(sv, "composer") else None
        # ---- 1. a streamed reply in the real window
        c.call("mock.pace", {"ms": 4})
        probe = Probe(v)
        probe.start()
        cs = [Counter(sv, "render"), Counter(tr, "_sync")]
        if comp is not None:
            cs.append(Counter(comp, "refresh"))
        store.send_message("sess-long", "slow please")
        c.wait_until(
            lambda: not store.rt["sess-long"].busy and store.get_model("sess-long").items[-1].kind == "text",
            60,
            "reply",
        )
        c.sleep(300)
        probe.stop()
        print("stream in the real window:", probe.report(""))
        for x in cs:
            print("   ", x)
            x.stop()
        # ---- 2. toggle the panel and the sidebar with a long conversation visible
        v.verticalScrollBar().setValue(v.verticalScrollBar().maximum() // 2)
        c.sleep(400)
        for label, key in (("panel", Qt.Key_Backslash), ("sidebar", Qt.Key_B)):
            for _ in range(2):
                v.cold_paints = 0
                probe = Probe(v)
                probe.start()
                QTest.keyClick(c.win, key, Qt.KeyboardModifier.ControlModifier)
                c.sleep(700)
                probe.stop()
                print(f"toggle {label}: cold placeholders painted={v.cold_paints} col_w={v.col_w} | {probe.report('')}")
        # ---- 3. a resize storm of the whole window
        v.cold_paints = 0
        probe = Probe(v)
        probe.start()
        steps = []
        for w in list(range(1480, 980, -25)) + list(range(980, 1480, 25)):
            steps.append(timed(lambda w=w: c.win.resize(w, 940)))
            pump(8)
        c.sleep(500)
        probe.stop()
        print(
            f"window resize storm 40 steps: per-step avg={sum(steps) / len(steps):.1f}ms max={max(steps):.0f}ms cold placeholders={v.cold_paints} | {probe.report('')}"
        )
        # ---- 4. the find bar
        probe = Probe(v)
        probe.start()
        t_find = timed(tr.open_find)
        c.sleep(200)
        fb = tr.find_bar
        edit = getattr(fb, "edit", None)
        t_type = 0.0
        if edit is not None:
            t_type = timed(lambda: QTest.keyClicks(edit, "module"))
            c.sleep(300)
        probe.stop()
        print(f"find: open={t_find:.0f}ms type 'module'={t_type:.0f}ms | {probe.report('')}")
        c.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    main()
