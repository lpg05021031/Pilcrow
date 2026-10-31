"""The side panel and the sidebar toggled through the store with a long conversation open: the
cost of each toggle (the first open builds the panel), placeholders, and whether the anchor holds."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core import Probe, timed  # noqa: E402
from harness import Client, start_mock  # noqa: E402


def main() -> None:
    port = 5651
    mock = start_mock(port, 400)
    try:
        c = Client(port)
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
        c.wait_until(lambda: not c.win.boot.isVisible(), 5, "boot")
        c.sleep(300)
        from pilcrow_qt.store import store

        c.open("sess-long")
        c.wait_until(lambda: store.rt.get("sess-long") and store.rt["sess-long"].loaded, 20, "loaded")
        c.sleep(800)
        v = c.win.session_view.transcript.view
        v.verticalScrollBar().setValue(v.verticalScrollBar().maximum() // 2)
        c.sleep(400)
        steps = [
            ("panel open", lambda: store.set_ui(panel=True)),
            ("panel close", lambda: store.set_ui(panel=False)),
            ("sidebar hide", lambda: store.set_ui(sidebar=False)),
            ("sidebar show", lambda: store.set_ui(sidebar=True)),
        ]
        for label, fn in steps:
            v.cold_paints = 0
            probe = Probe(v)
            probe.start()
            key = v.first_visible_key()
            t = timed(fn)
            c.sleep(500)
            probe.stop()
            kept = v.first_visible_key() == key
            print(
                f"{label}: {t:.0f}ms col_w={v.col_w} cold placeholders={v.cold_paints} anchor kept={kept} | "
                + probe.report(""),
                flush=True,
            )
        c.close()
    finally:
        mock.kill()


if __name__ == "__main__":
    main()
