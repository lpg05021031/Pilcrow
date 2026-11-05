"""shared.settings 的鎖：變數名不變、空字串=未設、壞值就炸、example 可往返。"""

import pytest
from pydantic import ValidationError

import shared.settings as S


def _clear_all(monkeypatch):
    for _g, _c, _f, info in S.iter_fields():
        for n in S.env_names(info):
            monkeypatch.delenv(n, raising=False)


def test_defaults_match_legacy_code(monkeypatch):
    _clear_all(monkeypatch)
    s = S.settings()
    assert s.orchestrator.pipeline == "v1"
    assert s.review.max_findings == 30 and s.review.min_confidence == 0.55
    assert s.knowledge.branches == ["master", "main"]
    assert s.posting.dry_run is True and s.posting.knowledge_post is False
    assert s.jobs.repo_sync_interval_min == 10 and s.jobs.harvest_interval_min == 30
    assert s.ado.verify_ssl is True
    assert s.static.semgrep == "auto"
    assert s.knowledge.enabled is False and s.graph.enabled is False
    assert s.rules.enabled is False


def test_env_override_and_reset(monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "2")
    assert S.settings().review.max_findings == 2
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "5")      # conftest 綁了 reset()
    assert S.settings().review.max_findings == 5


def test_blank_string_means_unset(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ROOT", "")
    monkeypatch.setenv("EVAL_MAX_NOISE", "")
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "")
    s = S.settings()
    assert s.knowledge.root is None and s.knowledge.enabled is False
    assert s.eval.max_noise is None
    assert s.review.max_findings == 30


def test_bad_value_fails_loud(monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "abc")
    with pytest.raises(ValidationError):
        S.settings()
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "30")
    monkeypatch.setenv("REVIEW_PIPELINE", "v3")
    with pytest.raises(ValidationError):
        S.settings()


def test_tri_state_and_switch_parsing(monkeypatch):
    monkeypatch.setenv("ADO_VERIFY_SSL", "/etc/ssl/certs/ca.crt")
    monkeypatch.setenv("STATIC_SEMGREP", "false")
    monkeypatch.setenv("STATIC_RUFF", "YES")
    monkeypatch.setenv("REVIEW_PIPELINE", " V2 ")
    monkeypatch.setenv("KNOWLEDGE_BRANCHES", " master , release/* ,")
    s = S.settings()
    assert s.ado.verify_ssl == "/etc/ssl/certs/ca.crt"
    assert s.static.semgrep == "off" and s.static.semgrep_enabled is False
    assert s.static.ruff == "on"
    assert s.orchestrator.pipeline == "v2"
    assert s.knowledge.branches == ["master", "release/*"]
    monkeypatch.setenv("ADO_VERIFY_SSL", "0")
    assert S.settings().ado.verify_ssl is False


def test_retired_tool_names_are_flagged_not_honoured(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("ADO_TOOLS_PORT", "8094")
    monkeypatch.setenv("REPO_ROOT", "/srv/x")
    s = S.settings()                                        # 不得 raise、不得有該欄位
    assert not hasattr(s, "tools")
    w = "\n".join(S.warnings_for(s))
    assert "ADO_TOOLS_PORT" in w and "REPO_ROOT" in w and "已無作用" in w


def test_enabled_flags_follow_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "nope"))
    assert S.settings().knowledge.enabled is False
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    assert S.settings().knowledge.enabled is True
    monkeypatch.setenv("GRAPH_TOOLS_URL", "http://127.0.0.1:8097/mcp")
    assert S.settings().graph.enabled is False               # 少 GRAPH_TOOL_BLAST
    monkeypatch.setenv("GRAPH_TOOL_BLAST", "get_impact_radius_tool")
    assert S.settings().graph.enabled is True
    (tmp_path / "myrepo").mkdir()
    monkeypatch.setenv("REPOS_ROOT", str(tmp_path))
    assert S.settings().repos.root_for("MyRepo") == (tmp_path / "myrepo").resolve()
    assert S.settings().repos.root_for("other") is None


def test_knobs_have_no_secrets_and_use_env_names(monkeypatch):
    monkeypatch.setenv("ORCH_API_KEY", "topsecret")
    k = S.settings().knobs()
    assert "REVIEW_MAX_FINDINGS" in k and "KNOWLEDGE_MAX_CHARS" in k
    assert "ORCH_API_KEY" not in k
    assert "topsecret" not in repr(k)


def test_every_legacy_env_name_is_still_recognised():
    """程式裡曾經 getenv 過的名字，schema 一個都不能漏。"""
    legacy = {
        "ADO_PAT", "ADO_VERIFY_SSL", "ADO_DIFF_MAX_FILES", "ADO_DIFF_MAX_FILE_BYTES",
        "ADO_DIFF_FETCH_CONCURRENCY", "DRY_RUN", "KNOWLEDGE_POST",
        "REPO_SYNC_INTERVAL_MIN", "HARVEST_INTERVAL_MIN", "REPO_SYNC_GRAPH_REBUILD",
        "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_ADVERSARY",
        "LLM_MODEL_DIGEST", "LLM_TIMEOUT_S", "REVIEW_PIPELINE", "AUTO_POST",
        "REVIEW_DEDUPE_TTL", "ORCH_API_KEY", "ORCH_URL", "REVIEW_MAX_DIMENSIONS",
        "REVIEW_MAX_PARALLEL", "REVIEW_LLM_BASE_CONF", "REVIEW_MIN_CONFIDENCE",
        "REVIEW_MAX_FINDINGS", "REVIEW_ADVERSARY", "ADVERSARY_CHUNK",
        "REVIEWER_RULES_MAX_CHARS", "REVIEWER_SLICES_MAX_CHARS",
        "REVIEWER_FILE_SLICE_MAX_CHARS", "STATIC_SEMGREP", "STATIC_RUFF",
        "STATIC_TIMEOUT", "STATIC_MISSING_TEST", "REPOS_ROOT",
        "CONTEXT_LINES", "CONTEXT_MAX_CHARS", "GRAPH_TOOLS_URL", "GRAPH_TOOL_BLAST",
        "GRAPH_TOOL_BLAST_ARGS", "GRAPH_REPO", "RULES_ROOT", "RULES_SUBDIR",
        "RULES_COMMON_DIR", "RULES_MAX_CHARS", "KNOWLEDGE_ROOT", "KNOWLEDGE_BRANCHES",
        "KNOWLEDGE_MAX_CHARS", "DISTILL_DIFF_MAX_CHARS", "KNOWLEDGE_REDISTILL",
        "KNOWLEDGE_CYCLE_SOURCE", "KNOWLEDGE_CYCLE_TARGET", "KNOWLEDGE_DIGEST",
        "KNOWLEDGE_DIGEST_LLM", "KNOWLEDGE_DIGEST_MAX_FLAGS",
        "KNOWLEDGE_RETIRE_ON_CONTRADICTION", "EVAL_KNOWLEDGE_ROOT", "EVAL_KNOWLEDGE_AB",
        "EVAL_MIN_RECALL", "EVAL_MAX_NOISE", "EVAL_MAX_FORBIDDEN",
        "EVAL_MIN_KNOWLEDGE_DELTA", "EVAL_MIN_DISTILL_SCORE",
    }
    known = {n for _g, _c, _f, info in S.iter_fields() for n in S.env_names(info)}
    assert legacy <= known, legacy - known


def test_example_round_trips_through_the_schema(monkeypatch):
    """產生的 example 每一個正名出現一次，且把它當 env 餵回去可以通過驗證。"""
    text = S.example_text()
    primary = [S.env_names(info)[0] for _g, _c, _f, info in S.iter_fields()]
    for name in primary:
        assert sum(1 for ln in text.splitlines()
                   if ln.lstrip("# ").startswith(name + "=")) == 1, name
    _clear_all(monkeypatch)
    for ln in text.splitlines():
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            monkeypatch.setenv(k, v.strip("'"))
    s = S.settings()                       # 不得 raise
    assert s.orchestrator.pipeline == "v1"
    rows = S.show_rows(s)
    assert any(r[1] == "ADO_PAT" and r[3] == "env:ADO_PAT" for r in rows)  # 必填欄位在 example 是打開的
    secret = {"ADO_PAT", "LLM_API_KEY", "ORCH_API_KEY", "LANGFUSE_SECRET_KEY"}
    assert all("changeme" not in r[2] for r in rows if r[1] in secret)   # 遮罩


def test_warnings_catch_config_smells(tmp_path, monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("AUTO_POST", "true")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "missing"))
    monkeypatch.setenv("GRAPH_TOOLS_URL", "http://127.0.0.1:8097/mcp")
    monkeypatch.setenv("PORT", "8094")
    w = "\n".join(S.warnings_for())
    assert "DRY_RUN=true" in w and "KNOWLEDGE_ROOT" in w
    assert "GRAPH_TOOL_BLAST" in w and "PORT" in w and "已無作用" in w


def test_fail_fast_exits_with_readable_message(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_MIN_CONFIDENCE", "1.5")
    with pytest.raises(SystemExit) as ex:
        S.fail_fast()
    assert ex.value.code == 1
    assert "REVIEW_MIN_CONFIDENCE" in capsys.readouterr().err
