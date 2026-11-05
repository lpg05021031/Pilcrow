"""jobs —— 行程內排程，取代兩組 systemd timer（repo-sync、knowledge-harvest）。

為什麼搬進行程：兩個 oneshot timer 各要一個 service + 一個 timer 單元、各自的
EnvironmentFile 與 PATH override，失敗只進 journal，沒有任何地方能一眼看到
「上次同步是幾點、成不成功」。搬進 orchestrator 的 event loop 之後：
  - 一個 systemd unit 管全部；
  - 每個 job 的 last_run / last_ok / last_error 住在記憶體，/healthz 直接回、
    doctor 直接讀、S3 的控制台直接畫；
  - 「harvester 是唯一 commit 者」的不變式由單一 event loop 保證：同一個 job
    在上一輪結束前不會再開第二輪（run_forever 是順序的）。

兩個 job 都是同步、會打 git / HTTP 的函式，用 asyncio.to_thread 跑，不卡 SSE。

CLI（過渡期與手動觸發）：
    python -m orchestrator.jobs repo-sync     跑一次 repo-sync 就退出（原 repo-sync.sh）
    python -m orchestrator.jobs harvest       跑一次 harvest（同 python -m orchestrator.knowledge_harvest）

設定：settings().jobs —— REPO_SYNC_INTERVAL_MIN（預設 10；0 = 關）、
HARVEST_INTERVAL_MIN（預設 30；0 = 關）、REPO_SYNC_GRAPH_REBUILD（auto/on/off）。
job 只在對應的 root 有設時才排程（REPOS_ROOT / KNOWLEDGE_ROOT）。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from shared.settings import settings


@dataclass
class JobState:
    name: str
    interval_min: int
    enabled: bool
    running: bool = False
    runs: int = 0
    last_start: float | None = None
    last_end: float | None = None
    last_ok: bool | None = None
    last_error: str = ""
    last_detail: list[str] = field(default_factory=list)   # 最近一輪每個項目一行
    next_run: float | None = None


_STATE: dict[str, JobState] = {}
_TASKS: set[asyncio.Task] = set()


def snapshot() -> dict[str, dict]:
    """給 /healthz、doctor、控制台。"""
    return {k: asdict(v) for k, v in _STATE.items()}


# ---------- repo-sync（原 deploy/bin/repo-sync.sh） ----------

def _git(path: Path, *args: str, timeout: int = 300) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 127, f"{type(e).__name__}: {e}"


def repo_sync_once() -> list[str]:
    """同步 REPOS_ROOT 底下所有乾淨 clone 到 target branch 最新，並（選配）
    增量重建 code graph。每個 repo 一行結果；pull 失敗的 repo 維持原狀、不擋別人。"""
    s = settings()
    root = s.repos.repos_root
    lines: list[str] = []
    if root is None or not root.is_dir():
        return [f"REPOS_ROOT 未設或不存在（{root}）—— 無事可做"]
    graph_bin = shutil.which("code-review-graph") if s.jobs.graph_rebuild != "off" else None
    for d in sorted(p for p in root.iterdir() if (p / ".git").exists()):
        rc, out = _git(d, "pull", "--ff-only", "--quiet")
        if rc != 0:
            lines.append(f"{d.name}: pull 失敗（分支發散或網路）—— 維持原狀：{out[:160]}")
            continue
        _rc, sha = _git(d, "rev-parse", "--short", "HEAD", timeout=15)
        line = f"{d.name} -> {sha}"
        if graph_bin:
            try:
                r = subprocess.run([graph_bin, "build"], cwd=str(d), capture_output=True,
                                   text=True, timeout=1800)
                line += "; graph 增量重建完成" if r.returncode == 0 else \
                    f"; graph build 失敗（不影響 review）：{(r.stderr or r.stdout)[-160:]}"
            except Exception as e:  # noqa: BLE001
                line += f"; graph build 例外：{type(e).__name__}"
        lines.append(line)
    return lines or ["REPOS_ROOT 底下沒有任何 clone"]


# ---------- knowledge-harvest ----------

def harvest_once() -> list[str]:
    from orchestrator import knowledge_harvest
    return knowledge_harvest.main()


# ---------- 排程 ----------

JOBS: dict[str, Callable[[], list[str]]] = {
    "repo-sync": repo_sync_once,
    "harvest": harvest_once,
}


def _plan() -> list[JobState]:
    s = settings()
    return [
        JobState("repo-sync", s.jobs.repo_sync_interval_min,
                 enabled=s.jobs.repo_sync_interval_min > 0 and s.repos.repos_root is not None),
        JobState("harvest", s.jobs.harvest_interval_min,
                 enabled=s.jobs.harvest_interval_min > 0 and s.knowledge.root is not None),
    ]


async def run_once(name: str) -> JobState:
    """跑一輪並更新狀態（控制台的「立刻執行」也走這裡）。同名 job 正在跑就直接回。"""
    st = _STATE.setdefault(name, JobState(name, 0, enabled=False))
    if st.running:
        return st
    st.running, st.last_start = True, time.time()
    try:
        st.last_detail = await asyncio.to_thread(JOBS[name])
        st.last_ok, st.last_error = True, ""
    except Exception as e:  # noqa: BLE001
        st.last_ok, st.last_error = False, f"{type(e).__name__}: {e}"
        st.last_detail = []
    finally:
        st.running, st.last_end, st.runs = False, time.time(), st.runs + 1
    for ln in st.last_detail:
        print(f"[{name}] {ln}")
    if st.last_error:
        print(f"[{name}] 失敗：{st.last_error}")
    return st


async def _run_forever(st: JobState, first_delay_s: float) -> None:
    await asyncio.sleep(first_delay_s)
    while True:
        await run_once(st.name)
        st.next_run = time.time() + st.interval_min * 60
        await asyncio.sleep(st.interval_min * 60)


def start(first_delay_s: float = 60.0) -> list[JobState]:
    """由 app 的 lifespan 呼叫。回傳排了哪些 job（含被關掉的，enabled=False）。"""
    _STATE.clear()
    for st in _plan():
        _STATE[st.name] = st
        if st.enabled:
            st.next_run = time.time() + first_delay_s
            t = asyncio.get_running_loop().create_task(_run_forever(st, first_delay_s))
            _TASKS.add(t)
            t.add_done_callback(_TASKS.discard)
    return list(_STATE.values())


def stop() -> None:
    for t in list(_TASKS):
        t.cancel()
    _TASKS.clear()


# ---------- CLI ----------

def _main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"用法：python -m orchestrator.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    from shared.settings import fail_fast
    fail_fast()
    for ln in JOBS[argv[0]]():
        print(f"[{argv[0]}] {ln}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
