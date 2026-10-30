"""Run the benchmarks against the current client and write the numbers.

    python qt/tests/bench/run.py [--heavy 600] [--mixed 2000] [--port 5360] [--only a,b] [--out test/out/bench.json]

``--heavy`` is the number of turns in the heavy conversation (prose with code and tables, 300-line
command outputs, 48-line diffs, thinking, sub-agents, failures); ``--mixed`` the plain one. The
mock builds both on demand (``mock.generate``). Every scenario runs in the same process, in order,
each on a client that has already opened the conversations once, so the numbers are warm except for
``open_cold``, which runs first.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PILCROW_MOTION", "1")  # animations on, as on a desktop
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heavy", type=int, default=600)
    ap.add_argument("--mixed", type=int, default=2000)
    ap.add_argument("--port", type=int, default=5360)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(ROOT / "test" / "out" / "bench.json"))
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    faulthandler.dump_traceback_later(900, repeat=True, file=sys.stderr)

    from harness import Client, start_mock

    from bench.scenarios import SCENARIOS, Ctx

    names = [n for n in args.only.split(",") if n] or list(SCENARIOS)
    mock = start_mock(args.port, 0)
    results = {"label": args.label, "heavy_turns": args.heavy, "mixed_turns": args.mixed, "scenarios": {}}
    try:
        c = Client(args.port)
        c.wait_until(lambda: c.store.sessions_loaded, 20, "sessions")
        t0 = time.perf_counter()
        gen_h = c.call("mock.generate", {"id": "sess-heavy", "turns": args.heavy, "shape": "heavy"}, timeout=120)
        gen_m = c.call("mock.generate", {"id": "sess-mixed", "turns": args.mixed, "shape": "mixed"}, timeout=120)
        results["generated"] = {"heavy": gen_h, "mixed": gen_m, "ms": round((time.perf_counter() - t0) * 1000)}
        c.wait_until(lambda: any(s.get("id") == "sess-heavy" for s in c.store.sessions), 20, "list")
        ctx = Ctx(c, "sess-heavy", "sess-mixed")
        for name in names:
            fn = SCENARIOS.get(name)
            if fn is None:
                print(f"no scenario named {name}", file=sys.stderr)
                continue
            print(f"--- {name}", flush=True)
            t = time.perf_counter()
            try:
                r = fn(ctx)
            except Exception as e:  # noqa: BLE001 - one scenario failing must not lose the others
                traceback.print_exc()
                r = {"error": repr(e)}
            r["_seconds"] = round(time.perf_counter() - t, 1)
            results["scenarios"][name] = r
            print(json.dumps(r, indent=1, default=str), flush=True)
            # a scenario may leave a turn running; let it end before the next one starts
            try:
                c.wait_until(lambda: not (c.store.rt.get("sess-heavy") and c.store.rt["sess-heavy"].busy), 90, "idle")
            except AssertionError:
                pass
        c.close()
    finally:
        mock.kill()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
