"""A live turn that has already done a lot of work (N tool steps) and keeps streaming text: every
flush re-groups the live tail, so what does one token cost once the turn is long?"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: E402

from core import Probe, Session, Streamer, app, assistant, pump, tool_result, tool_use, user


def run(steps: int) -> str:
    msgs = [user("do a long task")]
    for i in range(steps):
        tid = f"t-{i}"
        if i % 10 == 0:
            msgs.append(assistant([{"type": "text", "text": f"Now part {i // 10}."}]))
        msgs.append(assistant([tool_use(tid, "Bash", {"command": f"echo step {i}"})]))
        msgs.append(tool_result(tid, f"step {i}\n"))
    s = Session(f"turn-{steps}", msgs)
    v = s.view
    probe = Probe(v)
    probe.start()
    st = Streamer(s, "text")
    per = []
    for i in range(150):
        t0 = time.perf_counter()
        st.delta(f"word {i} of the closing reply, ")
        pump(3)
        per.append((time.perf_counter() - t0) * 1000)
    st.stop()
    pump(300)
    probe.stop()
    out = (
        f"live turn with {steps} steps, 150 tokens: per-token avg={sum(per) / len(per):.1f}ms max={max(per):.0f}ms\n   "
        + probe.report("")
    )
    s.close()
    return out


if __name__ == "__main__":
    app()
    for n in [int(x) for x in sys.argv[1:]] or [50, 400, 1500]:
        print(run(n), flush=True)
