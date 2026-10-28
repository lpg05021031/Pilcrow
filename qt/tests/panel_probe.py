"""Open a session, then every side-panel tab, catching anything that raises; screenshots per tab."""

import os
import sys
import traceback

sys.path.insert(0, "qt")
sys.path.insert(0, "qt/tests")
from harness import Client, start_mock

port = 5333
mock = start_mock(port, 20)
try:
    c = Client(port)
    c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions")
    c.open("sess-login")
    from pilcrow_qt.store import PANEL_TABS

    for tab in PANEL_TABS:
        try:
            c.store.set_ui(panel=True, panel_tab=tab)
            c.sleep(900)
            c.shot(f"tab-{tab}")
            print("ok", tab)
        except Exception:
            print("FAILED", tab)
            traceback.print_exc()
    c.store.show_page("inbox")
    c.sleep(600)
    c.shot("inbox-page")
    c.close()
finally:
    mock.kill()
