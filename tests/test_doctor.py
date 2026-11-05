"""shared.doctor 的鎖：全部離線 —— 網路、PATH、subprocess 探針一律 stub。"""
import pytest

import shared.doctor as D
import shared.settings as S


def _by(checks, group, name=None):
    hits = [c for c in checks if c.group == group and (name is None or c.name == name)]
    assert hits, f"no check {group}/{name}: {[(c.group, c.name) for c in checks]}"
    return hits[0] if name else hits


@pytest.fixture
def offline(monkeypatch):
    """預設一切都通；個別測試再把某個探針弄壞。"""
    monkeypatch.setattr(D, "_tcp_open", lambda host, port, timeout=0: True)
    monkeypatch.setattr(D, "_which", lambda b: f"/venv/bin/{b}" if b in ("semgrep", "ruff") else None)
    monkeypatch.setattr(D, "_run", lambda cmd, timeout=0: (0, "1.2.3"))

    def http(url, **kw):
        if url.endswith("/healthz"):
            return 200, {"ok": True}, ""
        if url.endswith("/api/public/health"):
            return 200, {"status": "OK"}, ""
        if url.endswith("/api/public/projects"):
            return 200, {"data": [{"name": "pr-review"}]}, ""
        if url.endswith("/models"):
            return 200, {"data": [{"id": "gpt-4o-mini"}, {"id": "big"}]}, ""
        return 404, "", ""
    monkeypatch.setattr(D, "_http_get", http)
    for _g, _c, _f, info in S.iter_fields():
        for n in S.env_names(info):
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("ADO_PAT", "x" * 52)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    return http


def test_all_green_exit_0(offline):
    checks, code = D.run()
    assert code == 0
    assert not [c for c in checks if c.status == D.FAIL]
    assert _by(checks, "services", "orchestrator").status == D.OK
    assert _by(checks, "llm", "LLM_MODEL").status == D.OK
    assert _by(checks, "ado", "reach").status == D.SKIP            # 沒給 --pr-url
    assert _by(checks, "paths", "KNOWLEDGE_ROOT").status == D.SKIP  # 未設 = 停用


def test_bad_settings_exit_2_and_nothing_else_runs(offline, monkeypatch):
    monkeypatch.setenv("REVIEW_MIN_CONFIDENCE", "9")
    checks, code = D.run()
    assert code == 2 and len(checks) == 1
    assert checks[0].status == D.FAIL and "REVIEW_MIN_CONFIDENCE" in checks[0].detail


def test_required_service_down_fails_optional_warns(offline, monkeypatch):
    monkeypatch.setattr(D, "_tcp_open", lambda host, port, timeout=0: port not in (8094, 8095))
    checks, code = D.run()
    assert code == 1
    assert _by(checks, "services", "ado_pr_tools ①").status == D.FAIL
    assert _by(checks, "services", "repo_reader ②").status == D.WARN
    assert _by(checks, "services", "review_result ③").status == D.OK


def test_model_not_served_by_gateway_fails(offline, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "nope")
    monkeypatch.setenv("LLM_MODEL_ADVERSARY", "big")
    checks, code = D.run()
    assert code == 1
    assert _by(checks, "llm", "LLM_MODEL").status == D.FAIL
    assert _by(checks, "llm", "LLM_MODEL_ADVERSARY").status == D.OK


def test_gateway_without_models_endpoint_is_a_warning(offline, monkeypatch):
    def http(url, **kw):
        if url.endswith("/models"):
            return 404, "", ""
        return offline(url, **kw)
    monkeypatch.setattr(D, "_http_get", http)
    checks, code = D.run()
    assert code == 0
    assert _by(checks, "llm", "gateway").status == D.WARN


def test_langfuse_bad_keys_fail(offline, monkeypatch):
    def http(url, **kw):
        if url.endswith("/api/public/projects"):
            return 401, {"message": "unauthorized"}, ""
        return offline(url, **kw)
    monkeypatch.setattr(D, "_http_get", http)
    checks, code = D.run()
    assert code == 1 and _by(checks, "langfuse", "keys").status == D.FAIL


def test_paths_report_missing_vs_present(offline, tmp_path, monkeypatch):
    monkeypatch.setenv("REPOS_ROOT", str(tmp_path / "missing"))
    monkeypatch.setenv("RULES_ROOT", str(tmp_path))                  # 存在但沒有 rules/common
    kroot = tmp_path / "k"; (kroot / "_pending" / "repo-a").mkdir(parents=True)
    (kroot / "_pending" / "repo-a" / "pr-1.json").write_text("{}", encoding="utf-8")
    (kroot / "repo-a").mkdir(); (kroot / "repo-a" / "Fee.md").write_text("# x", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(kroot))
    checks, code = D.run()
    assert _by(checks, "paths", "REPOS_ROOT").status == D.FAIL
    rules = _by(checks, "paths", "RULES_ROOT")
    assert rules.status == D.WARN and "對不上" in rules.detail
    k = _by(checks, "paths", "KNOWLEDGE_ROOT")
    assert "1 page(s), 1 pending" in k.detail
    assert code == 1


def test_no_network_skips_probes_and_missing_pat_still_fails(offline, monkeypatch):
    monkeypatch.delenv("ADO_PAT")
    checks, code = D.run(network=False)
    assert _by(checks, "services", "all").status == D.SKIP
    assert _by(checks, "langfuse", "all").status == D.SKIP
    assert _by(checks, "llm", "all").status == D.SKIP
    assert _by(checks, "ado", "ADO_PAT").status == D.FAIL and code == 1


def test_missing_semgrep_warns_unless_switched_off(offline, monkeypatch):
    monkeypatch.setattr(D, "_which", lambda b: None)
    checks, _ = D.run(network=False)
    assert _by(checks, "python", "semgrep").status == D.WARN
    assert _by(checks, "systemd", "all").status == D.SKIP        # 沒 systemctl
    monkeypatch.setenv("STATIC_SEMGREP", "off")
    checks, _ = D.run(network=False)
    assert _by(checks, "python", "semgrep").status == D.SKIP


def test_pr_url_probe_reports_title(offline, monkeypatch):
    import shared.azdo as A
    import contextlib
    monkeypatch.setattr(A, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(A, "get_json", lambda c, url, **p: {"title": "Fix TTL", "status": "active"})
    checks, code = D.run(pr_url="https://dev.azure.com/o/p/_git/R/pullrequest/42")
    r = _by(checks, "ado", "reach")
    assert r.status == D.OK and "Fix TTL" in r.detail and code == 0


def test_render_and_json_shapes(offline):
    checks, code = D.run()
    text = D.render(checks)
    assert "[services]" in text and "OK " in text and text.rstrip().endswith("SKIP " + str(
        sum(1 for c in checks if c.status == D.SKIP)))
    import json
    from dataclasses import asdict
    payload = json.loads(json.dumps([asdict(c) for c in checks], ensure_ascii=False))
    assert {"group", "name", "status", "detail"} <= set(payload[0])
