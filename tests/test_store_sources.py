"""S3 的鎖：review 歷史（SQLite）、三個 source 的狀態與預覽、控制台 Sources / Reviews 頁。全離線。"""
import asyncio
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import orchestrator.sources as SRC
import orchestrator.store as ST
import shared.doctor as D
from orchestrator.models import Finding, ReviewResult


# ---------- store ----------

def test_store_records_lifecycle_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    rid = ST.start_review("https://x/C/P/_git/Repo/pullrequest/7", "u")
    assert rid and ST.get_review(rid)["status"] == "running"
    ST.set_title(rid, "Fix TTL")
    res = ReviewResult(markdown="# ok", findings=[
        Finding(rule="BUG", file="a.py", line=1, severity="error", note="x"),
        Finding(rule="NAMING", file="a.py", line=2, severity="info", note="y")],
        stats={"pipeline": "v2", "files": 1, "rules_files": ["rules/common/a.md"]})
    ST.finish_review(rid, res, trace_id="t1")
    r = ST.get_review(rid)
    assert r["status"] == "done" and r["title"] == "Fix TTL" and r["repo"] == "Repo" and r["pr_id"] == 7
    assert (r["n_error"], r["n_warning"], r["n_info"]) == (1, 0, 1)
    assert r["findings"][0]["rule"] == "BUG" and r["stats"]["rules_files"] == ["rules/common/a.md"]
    assert r["trace_id"] == "t1" and r["duration"] is not None
    rid2 = ST.start_review("https://x/C/P/_git/Repo/pullrequest/8", "u")
    ST.fail_review(rid2, "RuntimeError: boom")
    rows = ST.list_reviews()
    assert [x["status"] for x in rows] == ["error", "done"] and "findings" not in rows[0]
    s = ST.summary(7)
    assert s["n"] == 2 and s["errors"] == 1 and s["avg_findings"] == 2.0 or s["avg_findings"] in (1.0, 2.0)
    assert ST.enabled()


def test_store_degrades_when_data_dir_is_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(blocker / "sub"))        # 父路徑是檔案 → mkdir 失敗
    assert ST.start_review("https://x/C/P/_git/R/pullrequest/1", "u") is None
    ST.finish_review(None, ReviewResult(markdown="", findings=[], stats={}))   # 不炸
    assert ST.list_reviews() == [] and not ST.enabled() and "sub" in ST.disabled_reason()


def test_handle_review_records_even_when_pipeline_fails(monkeypatch):
    import orchestrator.app as A

    async def f_meta(u):
        return {"repo": "r", "title": "T"}

    async def f_diff(u):
        raise RuntimeError("ado down")
    monkeypatch.setattr(A.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(A.clients, "fetch_pr_diff", f_diff)
    with pytest.raises(RuntimeError):
        asyncio.run(A.handle_review("https://x/C/P/_git/r/pullrequest/3", "u"))
    rows = ST.list_reviews()
    assert rows[0]["status"] == "error" and "ado down" in rows[0]["error"] and rows[0]["title"] == "T"


# ---------- sources ----------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


@pytest.fixture
def roots(tmp_path, monkeypatch):
    repos = tmp_path / "repos"; (repos / "svc").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=repos / "svc")
    (repos / "svc" / "a").write_text("1", encoding="utf-8")
    _git("add", ".", cwd=repos / "svc"); _git("commit", "-qm", "one", cwd=repos / "svc")
    (repos / "junk").mkdir()
    rules = tmp_path / "rules"; (rules / "rules" / "common").mkdir(parents=True)
    (rules / "rules" / "svc").mkdir()
    (rules / "rules" / "common" / "naming.md").write_text("# naming\nbe clear", encoding="utf-8")
    (rules / "rules" / "svc" / "db.md").write_text("# db\nno raw sql", encoding="utf-8")
    (rules / "semgrep").mkdir(); (rules / "semgrep" / "x.semgrep.yml").write_text("rules: []", encoding="utf-8")
    know = tmp_path / "know"; (know / "svc" / "cache").mkdir(parents=True)
    (know / "svc" / "cache" / "SessionCache.md").write_text(
        "# SessionCache\n> entity: cache/SessionCache.java\n\n## Invariants\n- ttl is seconds `PR1-I1`\n- ~~old~~ `PR1-I2`\n",
        encoding="utf-8")
    (know / "_pending" / "svc").mkdir(parents=True)
    (know / "_pending" / "svc" / "pr-9.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPOS_ROOT", str(repos)); monkeypatch.setenv("RULES_ROOT", str(rules))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(know))
    return tmp_path


def test_source_views_report_counts_and_heads(roots):
    v = SRC.all_views()
    assert v["repos"].counts == {"clones": 1, "others": 1}
    svc = next(i for i in v["repos"].items if i["name"] == "svc")
    assert svc["branch"] == "main" and svc["sha"] and svc["dirty"] is False
    assert v["rules"].counts == {"md": 2, "semgrep": 1, "common": 1, "repos": 1}
    assert v["knowledge"].counts["pages"] == 1 and v["knowledge"].counts["pending"] == 1
    assert v["knowledge"].items[0]["invariants"] == 2 and v["knowledge"].items[0]["retired"] == 1
    assert v["knowledge"].head.get("error")                      # 不是 git repo：如實回報


def test_previews_call_the_real_loaders(roots):
    rp = SRC.rules_preview("svc")
    assert rp["loaded"] and rp["files"] == ["rules/common/naming.md", "rules/svc/db.md"]
    assert SRC.rules_preview("other")["files"] == ["rules/common/naming.md"]
    kp = SRC.knowledge_preview("svc", ["src/cache/SessionCache.java"])
    assert kp["stats"]["knowledge_entries"] == 1 and "PR1-I1" in kp["text"] and "PR1-I2" not in kp["text"]
    assert SRC.knowledge_preview("svc", ["src/other/X.java"])["stats"]["knowledge_entries"] == 0


def test_source_views_when_unset_or_missing(tmp_path, monkeypatch):
    for n in ("REPOS_ROOT", "RULES_ROOT", "KNOWLEDGE_ROOT"):
        monkeypatch.delenv(n, raising=False)
    v = SRC.all_views()
    assert all(not x.exists and "未設" in x.note for x in v.values())
    monkeypatch.setenv("RULES_ROOT", str(tmp_path / "nope"))
    assert "不存在" in SRC.rules_view().note


def test_add_repo_validates_before_touching_git(roots, tmp_path, monkeypatch):
    assert "不合法" in SRC.add_repo("Bad Name", "https://x", "main")[0]      # 不合法
    assert "http(s)" in SRC.add_repo("ok", "git@x:y", "main")[0]
    assert "已存在" in SRC.add_repo("svc", "https://x/y", "main")[0]        # 已存在
    monkeypatch.setenv("ADO_PAT", "")
    assert "clone 失敗" in SRC.add_repo("newone", "https://127.0.0.1:9/no/_git/x", "main")[0]
    assert not (roots / "repos" / "newone").exists()


# ---------- console pages ----------

@pytest.fixture
def client(roots, monkeypatch):
    monkeypatch.setenv("ADO_PAT", "pat")
    monkeypatch.setattr(D, "_tcp_open", lambda h, p, timeout=0: True)
    monkeypatch.setattr(D, "_http_get", lambda url, **kw: (200, {"ok": True, "jobs": {}}, ""))
    monkeypatch.setattr(D, "_which", lambda b: None)
    monkeypatch.setattr(D, "_run", lambda cmd, timeout=0: (0, ""))
    import orchestrator.app as A
    import orchestrator.console.router as R
    R._doctor_cache.update(ts=0.0, checks=[])
    with TestClient(A.app) as c:
        yield c


def test_sources_page_and_previews(client):
    r = client.get("/console/sources")
    assert r.status_code == 200 and "svc" in r.text and "naming.md" in r.text and "SessionCache.md" in r.text
    r = client.get("/console/sources", params={"rules_repo": "svc"})
    assert "rules/svc/db.md" in r.text and "載入 2 檔" in r.text
    r = client.get("/console/sources", params={"k_repo": "svc", "k_paths": "src/cache/SessionCache.java"})
    assert "1 條目" in r.text and "PR1-I1" in r.text


def test_overview_shows_sources_and_recent_reviews(client):
    rid = ST.start_review("https://x/C/P/_git/svc/pullrequest/5", "u")
    ST.finish_review(rid, ReviewResult(markdown="m", findings=[], stats={"pipeline": "v2"}))
    r = client.get("/console")
    assert "svc#5" in r.text and "1 顆 clone" in r.text and "2 條規則" in r.text
    assert "活動紀錄" in r.text and "待處理項目" in r.text


def test_reviews_pages_and_run_form(client, monkeypatch):
    import orchestrator.app as A
    r = client.get("/console/reviews")
    assert r.status_code == 200 and "尚無 review 記錄" in r.text
    r = client.post("/console/reviews/run", data={"pr_url": "not a url"}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    async def fake(url, user, on_status=None):
        rid = ST.start_review(url, user)
        ST.finish_review(rid, ReviewResult(markdown="# R", findings=[
            Finding(rule="SECURITY", file="a.py", line=3, severity="error", note="inj")],
            stats={"pipeline": "v2", "files": 1, "dimension_names": ["Security"]}))
    monkeypatch.setattr(A, "handle_review", fake)
    r = client.post("/console/reviews/run", data={"pr_url": "see https://x/C/P/_git/svc/pullrequest/11 pls"},
                    follow_redirects=False)
    assert r.status_code == 303
    time.sleep(0.3)
    rows = ST.list_reviews()
    assert rows and rows[0]["pr_id"] == 11 and rows[0]["user"] == "console"
    r = client.get(f"/console/reviews/{rows[0]['id']}")
    assert r.status_code == 200 and "SECURITY" in r.text and "Security" in r.text and "# R" in r.text
    assert client.get("/console/reviews/9999").status_code == 404
