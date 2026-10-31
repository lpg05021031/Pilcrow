"""Streaming under hostile shapes: one giant paragraph, a thousand-item list, a 500-row table, a
3000-line fence, a long thought, a realistic reply. What each token costs, how that cost grows, how
the main thread stalls, and whether the picture moves when the reader has scrolled up."""

from __future__ import annotations

import sys
import time

from core import Probe, Session, Streamer, app, assistant, pump, user

KINDS = {
    "para": ("text", ["lorem ipsum dolor sit amet consectetur " for _ in range(1200)]),
    "cjk": ("text", ["這是一段很長的中文段落，用來測試串流時每個字元的成本；" for _ in range(800)]),
    "list": ("text", [f"- item {i} with a few words and `code{i}`\n" for i in range(1000)]),
    "table": (
        "text",
        ["| Name | Calls | Time |\n|---|---:|---:|\n"] + [f"| `fn{i}()` | {i} | {i * 0.7:.1f} |\n" for i in range(500)],
    ),
    "fence": (
        "text",
        ["```ts\n"]
        + [
            f"const x{i} = compute(x{i - 1}, {i}); // step {i}\n"
            for i in range(1, int(__import__("os").environ.get("FENCE_N", "3000")))
        ],
    ),
    "think": (
        "thinking",
        [
            "Considering option " + str(i) + ": it would work if the parser kept its offsets. "
            for i in range(int(__import__("os").environ.get("THINK_N", "900")))
        ],
    ),
    "mixed": ("text", []),
}


def mixed_tokens():
    out = []
    for k in range(6):
        out += [f"## Section {k}\n\n"]
        out += [f"Paragraph {k} sentence {j} with `code_{j}` and **bold** words. " for j in range(12)]
        out += ["\n\n"]
        out += [f"- point {j} of section {k}\n" for j in range(8)]
        out += ["\n```py\n"] + [f"    value_{j} = step({j})\n" for j in range(40)] + ["```\n\n"]
        out += ["| a | b |\n|---|---|\n"] + [f"| r{j} | {j} |\n" for j in range(6)] + ["\n"]
    return out


KINDS["mixed"] = ("text", mixed_tokens())


def run(kind: str, scrolled_up: bool = False) -> str:
    kind_of, tokens = KINDS[kind]
    msgs = [user("go")] + [
        assistant([{"type": "text", "text": f"Earlier reply {i}.\n\n" + "words " * 60}]) for i in range(12)
    ]
    msgs.append(user("stream now"))
    s = Session(f"stream-{kind}", msgs)
    v = s.view
    before = None
    if scrolled_up:
        v.verticalScrollBar().setValue(0)
        pump(200)
        before = v.viewport().grab().toImage()
    probe = Probe(v)
    probe.start()
    st = Streamer(s, kind_of)
    per: list = []
    t_all = time.perf_counter()
    for tok in tokens:
        t0 = time.perf_counter()
        st.delta(tok)
        pump(3)
        per.append((time.perf_counter() - t0) * 1000)
    st.stop()
    pump(400)
    total = (time.perf_counter() - t_all) * 1000
    probe.stop()
    n = len(per)
    head = sum(per[: n // 10]) / max(1, n // 10)
    tail = sum(per[-n // 10 :]) / max(1, n // 10)
    worst = max(per)
    moved = ""
    if scrolled_up and before is not None:
        after = v.viewport().grab().toImage()
        moved = " | scrolled-up picture " + ("UNCHANGED" if after == before else "MOVED")
    live = v.scene.block(f"text:{s.model.items[-1].id}")
    blocks = len(live.children) if live is not None and hasattr(live, "children") else -1
    line = (
        f"{kind:6s} tokens={n} total={total / 1000:.1f}s per-token first10%={head:.1f}ms last10%={tail:.1f}ms "
        f"worst={worst:.0f}ms blocks={blocks}{moved}\n   " + probe.report("")
    )
    s.close()
    return line


if __name__ == "__main__":
    app()
    kinds = sys.argv[1:] or ["mixed", "para", "cjk", "list", "table", "fence", "think"]
    for k in kinds:
        up = k.endswith("@up")
        print(run(k[:-3] if up else k, scrolled_up=up), flush=True)
