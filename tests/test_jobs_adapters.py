"""S1 的鎖：①③ 併入行程後行為不變；排程迴圈的狀態機正確。全離線。"""
import asyncio
import contextlib
import subprocess

import orchestrator.jobs as J
from orchestrator.adapters import ado


# ---------- adapters/ado：寫入的雙開關與稽核 ----------

def test_review_post_respects_dry_run_and_audits(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    ado.audit_log.clear()
    url = "https://x/C/P/_git/repo-a/pullrequest/5"
    out = ado.post_review_comment(url, "# review")
    assert out["dry_run"] is True and out["posted"] is False
    assert out["would_post_to"].endswith("/pullRequests/5/threads")
    assert out["preview"].startswith(ado.MARKER)
    assert ado.audit_log[-1]["kind"] == "review" and ado.audit_log[-1]["posted"] is False


def test_review_post_really_posts_when_dry_run_off(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ADO_PAT", "pat")
    posted = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": 77}

    class _Client:
        def post(self, url, params=None, json=None):
            posted["url"], posted["json"] = url, json
            return _Resp()
    monkeypatch.setattr(ado, "client", lambda: contextlib.nullcontext(_Client()))
    out = ado.post_review_comment("https://x/C/P/_git/R/pullrequest/9", "body")
    assert out == {"posted": True, "dry_run": False, "thread_id": 77}
    assert posted["url"].endswith("/pullRequests/9/threads")
    assert posted["json"]["comments"][0]["content"].startswith(ado.MARKER)
    assert ado.audit_log[-1]["posted"] is True


def test_clients_wrappers_keep_their_names_for_callers():
    """pipeline / knowledge / app 與既有測試都 monkeypatch 這幾個名字——簽名不能動。"""
    import inspect
    from orchestrator import clients
    for name in ("fetch_pr_metadata", "fetch_pr_diff", "post_review_comment",
                 "post_knowledge_draft", "graph_blast", "graph_call"):
        assert inspect.iscoroutinefunction(getattr(clients, name)), name
    assert list(inspect.signature(clients.post_knowledge_draft).parameters) == \
        ["pr_url", "markdown", "thread_id"]


# ---------- jobs：repo-sync 真的對 git 動手 ----------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


def test_repo_sync_pulls_fast_forward_and_reports_each_clone(tmp_path, monkeypatch):
    upstream = tmp_path / "up"; upstream.mkdir()
    _git("init", "-q", "-b", "main", cwd=upstream)
    (upstream / "a.txt").write_text("1", encoding="utf-8")
    _git("add", "a.txt", cwd=upstream); _git("commit", "-qm", "one", cwd=upstream)
    repos = tmp_path / "repos"; repos.mkdir()
    _git("clone", "-q", str(upstream), str(repos / "svc"), cwd=tmp_path)
    (repos / "notgit").mkdir()
    (upstream / "a.txt").write_text("2", encoding="utf-8")
    _git("commit", "-qam", "two", cwd=upstream)
    monkeypatch.setenv("REPOS_ROOT", str(repos))
    monkeypatch.setenv("REPO_SYNC_GRAPH_REBUILD", "off")
    lines = J.repo_sync_once()
    assert len(lines) == 1 and lines[0].startswith("svc -> ")
    assert (repos / "svc" / "a.txt").read_text(encoding="utf-8") == "2"


def test_repo_sync_without_root_is_a_noop_message(monkeypatch):
    monkeypatch.delenv("REPOS_ROOT", raising=False)
    assert "無事可做" in J.repo_sync_once()[0]


# ---------- jobs：排程狀態機 ----------

def test_run_once_records_success_failure_and_refuses_overlap(monkeypatch):
    calls = []
    monkeypatch.setitem(J.JOBS, "repo-sync", lambda: calls.append(1) or ["x -> 1"])
    monkeypatch.setitem(J.JOBS, "harvest", lambda: (_ for _ in ()).throw(RuntimeError("git lock")))

    async def go():
        J._STATE.clear()
        a = await J.run_once("repo-sync")
        b = await J.run_once("harvest")
        return a, b
    a, b = asyncio.run(go())
    assert a.last_ok is True and a.last_detail == ["x -> 1"] and a.runs == 1
    assert b.last_ok is False and "git lock" in b.last_error and b.runs == 1
    snap = J.snapshot()
    assert snap["harvest"]["last_error"].startswith("RuntimeError")

    async def overlap():
        st = J._STATE["repo-sync"]
        st.running = True
        return await J.run_once("repo-sync")
    assert asyncio.run(overlap()).runs == 1          # 正在跑 → 不再開第二輪
    assert calls == [1]


def test_start_schedules_only_jobs_whose_root_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOS_ROOT", str(tmp_path))
    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
    monkeypatch.setenv("REPO_SYNC_INTERVAL_MIN", "5")

    async def go():
        sts = J.start(first_delay_s=3600)
        try:
            return {s.name: s.enabled for s in sts}, len(J._TASKS)
        finally:
            J.stop()
    enabled, n_tasks = asyncio.run(go())
    assert enabled == {"repo-sync": True, "harvest": False}
    assert n_tasks == 2                      # 關著的也有迴圈（每分鐘看設定），只是不跑
    assert J.snapshot()["repo-sync"]["next_run"] is not None
    assert J.snapshot()["harvest"]["next_run"] is None


def test_zero_interval_disables_job(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOS_ROOT", str(tmp_path))
    monkeypatch.setenv("REPO_SYNC_INTERVAL_MIN", "0")

    async def go():
        try:
            return {s.name: s.enabled for s in J.start(first_delay_s=3600)}
        finally:
            J.stop()
    assert asyncio.run(go())["repo-sync"] is False


def test_cli_rejects_unknown_job(capsys):
    assert J._main(["nope"]) == 2
    assert "用法" in capsys.readouterr().err
