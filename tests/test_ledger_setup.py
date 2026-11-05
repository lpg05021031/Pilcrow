"""S4 的鎖：控制台裁決與 PR 留言同義且收口時用掉；Knowledge / Setup 頁；install 冪等。全離線。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import orchestrator.ledger as L
import shared.doctor as D


def _seed_knowledge(root: Path) -> None:
    (root / "repo-a" / "a").mkdir(parents=True)
    (root / "repo-a" / "a" / "Fee.md").write_text(
        "# Fee\n> entity: a/Fee.java\n\n## Invariants\n- fee in cents `PR1-I1`\n", encoding="utf-8")
    (root / "_pending" / "repo-a").mkdir(parents=True)
    (root / "_pending" / "repo-a" / "pr-5.json").write_text(json.dumps({
        "pr_url": "https://x/C/P/_git/repo-a/pullrequest/5", "pr_id": 5, "repo": "repo-a",
        "target_branch": "release/1", "base_url": "https://x/C/P", "posted": False, "dry_run": True,
        "created": "2026-11-06T01:00:00", "contradicted": ["PR1-I1"],
        "draft": {"primary_entity": "a/Fee.java", "ledger_line": "rounds half-even",
                  "decisions": [{"what": "use HALF_EVEN", "why": "spec", "evidence": ["F1"]}],
                  "invariants": ["fee rounding is half-even"]}}), encoding="utf-8")
    (root / "_cycle" / "repo-a").mkdir(parents=True)
    (root / "_cycle" / "repo-a" / "pr-6.json").write_text(json.dumps({
        "pr_url": "https://x/C/P/_git/repo-a/pullrequest/6", "pr_id": 6, "repo": "repo-a",
        "target_branch": "release/1", "base_url": "https://x/C/P", "provenance": "silent",
        "contradicted": ["PR1-I1"],
        "draft": {"primary_entity": "a/Tax.java", "ledger_line": "tax is exclusive",
                  "decisions": [{"what": "exclusive tax", "why": "law", "evidence": ["PR"]}],
                  "invariants": ["tax never negative"]}}), encoding="utf-8")
    (root / "_state").mkdir()
    (root / "_state" / "cycle.json").write_text(json.dumps({"repo-a#99": {"listed": ["PR6"], "digest": True}}),
                                               encoding="utf-8")


@pytest.fixture
def kroot(tmp_path, monkeypatch):
    root = tmp_path / "know"; root.mkdir()
    _seed_knowledge(root)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    return root


# ---------- ledger ----------

def test_ledger_reads_pending_staged_cycle_and_renders_digest(kroot):
    p = L.pending()
    assert len(p) == 1 and p[0]["pr_id"] == 5 and p[0]["dry_run"] and p[0]["contradicted"] == ["PR1-I1"]
    assert p[0]["draft"]["decisions"][0]["id"] == "PR5-D1" and p[0]["draft"]["invariants"][0]["id"] == "PR5-I1"
    s = L.staged()
    assert len(s) == 1 and s[0]["pr_id"] == 6 and s[0]["provenance"] == "silent"
    assert list(L.cycle_state()) == ["repo-a#99"]
    d = L.digest_preview("repo-a")
    assert "PR#6" in d and "PR6-I1" in d and "`keep PR1-I1`" in d
    assert L.digest_preview("nope") == ""


def test_verdicts_add_remove_peek_consume(kroot):
    assert "已記下" in L.add_verdict("Repo-A", "reject", "PR6-I1", by="me")
    assert "已記下" in L.add_verdict("repo-a", "keep", "PR1-I1")
    assert "只認條目 ID" in L.add_verdict("repo-a", "keep", "Fee")
    assert "缺" in L.add_verdict("repo-a", "nuke", "x")
    v = L.verdicts()
    assert set(v["repo-a"]["reject"]) == {"PR6-I1"} and set(v["repo-a"]["keep"]) == {"PR1-I1"}
    assert L.peek_verdicts(kroot, "repo-a") == ({"pr6-i1"}, {"PR1-I1"})
    assert L.verdicts()["repo-a"]["reject"]                      # peek 不清
    assert "已撤回" in L.remove_verdict("repo-a", "keep", "PR1-I1")
    assert "沒有這筆" in L.remove_verdict("repo-a", "keep", "PR1-I1")
    assert L.consume_verdicts(kroot, "repo-a") == ({"pr6-i1"}, set())
    assert L.consume_verdicts(kroot, "repo-a") == (set(), set())   # 用掉了


def test_harvester_applies_console_verdicts_at_cycle_close(kroot, monkeypatch):
    """控制台 reject PR6-I1 + keep PR1-I1：收口後 Tax 頁沒有那條不變式、Fee 的 PR1-I1 沒被畫掉。"""
    from orchestrator import knowledge_harvest as H
    monkeypatch.setenv("KNOWLEDGE_CYCLE_SOURCE", "release/*")
    monkeypatch.setenv("KNOWLEDGE_CYCLE_TARGET", "master")
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "false")
    (kroot / "_state" / "repos.json").write_text(json.dumps({"repo-a": "https://x/C/P"}), encoding="utf-8")
    (kroot / "_state" / "cycle.json").write_text(json.dumps({"repo-a#1": {"closed": True}}), encoding="utf-8")
    L.add_verdict("repo-a", "reject", "PR6-I1")
    L.add_verdict("repo-a", "keep", "PR1-I1")
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 99, "sourceRefName": "refs/heads/release/1", "targetRefName": "refs/heads/master"}])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_git", lambda root, *a: None)
    lines = H._cycle_close(object(), kroot)
    assert any("收口" in ln for ln in lines)
    tax = (kroot / "repo-a" / "a" / "Tax.md").read_text(encoding="utf-8")
    assert "PR6-D1" in tax and "tax never negative" not in tax        # reject 那一行沒落地（History 會記 rejected）
    fee = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "~~" not in fee                                           # keep 否決了拔除
    assert L.verdicts().get("repo-a") in (None, {}) or not any(L.verdicts()["repo-a"].values())


# ---------- console pages ----------

@pytest.fixture
def client(kroot, monkeypatch, tmp_path):
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


def test_knowledge_page_lists_and_records_verdicts(client):
    r = client.get("/console/knowledge")
    assert r.status_code == 200 and "repo-a#5" in r.text and "repo-a#6" in r.text
    assert "keep PR1-I1" in r.text and "repo-a#99" in r.text
    r = client.post("/console/knowledge/verdict", data={"repo": "repo-a", "kind": "reject", "token": "PR6"},
                    follow_redirects=True)
    assert "已記下" in r.text and "<code>PR6</code>" in r.text
    r = client.post("/console/knowledge/verdict/remove", data={"repo": "repo-a", "kind": "reject", "token": "PR6"},
                    follow_redirects=True)
    assert "已撤回" in r.text
    r = client.get("/console/knowledge", params={"digest_repo": "repo-a"})
    assert "Knowledge cycle digest" in r.text


def test_setup_page_groups_doctor_checks_into_steps(client):
    r = client.get("/console/setup")
    assert r.status_code == 200
    for title in ("LLM gateway", "Langfuse", "Azure DevOps", "服務本體", "本地 clone", "團隊規則", "知識迴圈"):
        assert title in r.text
    assert "REPOS_ROOT" in r.text and "/console/settings#g-llm" in r.text


def test_overview_points_to_setup_when_failing(client, monkeypatch):
    monkeypatch.setattr(D, "_http_get", lambda url, **kw: (None, None, "down"))
    import orchestrator.console.router as R
    R._doctor_cache.update(ts=0.0, checks=[])
    r = client.get("/console?refresh=1")
    assert "設定精靈" in r.text


# ---------- install ----------

def test_install_is_idempotent_and_writes_unit(tmp_path, monkeypatch, capsys):
    from shared import install
    cfg, data, unit = tmp_path / "etc", tmp_path / "var", tmp_path / "unit.service"
    args = ["--config-dir", str(cfg), "--data-dir", str(data), "--unit-path", str(unit),
            "--prefix", "/opt/x", "--user", "svc"]
    assert install.main([*args, "--dry-run"]) == 0
    assert not cfg.exists() and not unit.exists()
    assert install.main(args) == 0
    assert (cfg / "config.toml").exists() and (cfg / "secrets.env").exists() and data.is_dir()
    u = unit.read_text(encoding="utf-8")
    assert "User=svc" in u and "/opt/x/venv/bin/uvicorn" in u and f"CONFIG_DIR={cfg.as_posix()}" in u
    (cfg / "secrets.env").write_text("ADO_PAT=keepme\n", encoding="utf-8")
    assert install.main(args) == 0
    assert "keepme" in (cfg / "secrets.env").read_text(encoding="utf-8")       # 不覆寫
    out = capsys.readouterr().out
    assert "保留既有" in out and "[settings]" in out                            # 末尾跑了 doctor
