"""config.toml 這一層的鎖：env > toml > 預設；secrets 不進 toml；save 會備份、記錄、重載。全離線。"""
import json
import tomllib

import pytest
from pydantic import ValidationError

import shared.settings as S


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for _g, _c, _f, info in S.iter_fields():
        for n in S.env_names(info):
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_toml_is_second_source_and_env_still_wins(cfg, monkeypatch):
    (cfg / "config.toml").write_text(
        '[review]\nmax_findings = 7\n[llm]\nmodel = "qwen"\n[knowledge]\nbranches = ["main", "release/*"]\n',
        encoding="utf-8")
    S.reset()
    s = S.settings()
    assert s.review.max_findings == 7 and s.llm.model == "qwen"
    assert s.knowledge.branches == ["main", "release/*"]
    assert S.source_of("review", "max_findings") == "file"
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "9")
    assert S.settings().review.max_findings == 9
    assert S.source_of("review", "max_findings") == "env:REVIEW_MAX_FINDINGS"
    assert S.source_of("review", "max_parallel") == "default"


def test_secret_in_toml_is_ignored_and_warned(cfg):
    (cfg / "config.toml").write_text('[ado]\npat = "leaked"\ndiff_max_files = 5\n', encoding="utf-8")
    S.reset()
    s = S.settings()
    assert s.ado.pat.get_secret_value() == ""          # 沒吃
    assert s.ado.diff_max_files == 5                    # 同表的非 secret 照吃
    assert any("ado.pat" in w for w in S.warnings_for(s))


def test_bad_toml_value_fails_like_bad_env(cfg):
    (cfg / "config.toml").write_text("[review]\nmax_findings = 0\n", encoding="utf-8")
    S.reset()
    with pytest.raises(ValidationError):
        S.settings()


def test_save_writes_backup_log_and_reloads(cfg):
    S.reset()
    assert S.settings().review.max_findings == 30
    log = S.save_toml({"review": {"max_findings": 12}, "llm": {"model": "big"}}, actor="t")
    assert {(e["env"], e["old"], e["new"]) for e in log} == {
        ("REVIEW_MAX_FINDINGS", 30, 12), ("LLM_MODEL", "gpt-4o-mini", "big")}
    assert S.settings().review.max_findings == 12          # save 內部 reset 了
    data = tomllib.loads((cfg / "config.toml").read_text(encoding="utf-8"))
    assert data == {"review": {"max_findings": 12}, "llm": {"model": "big"}} or \
        {k: v for k, v in data.items() if v} == {"review": {"max_findings": 12}, "llm": {"model": "big"}}
    assert not list(cfg.glob("config.toml.bak-*"))         # 第一次沒有舊檔可備

    S.save_toml({"review": {"max_findings": None}}, actor="t")   # None = 回預設
    assert S.settings().review.max_findings == 30
    assert len(list(cfg.glob("config.toml.bak-*"))) == 1
    rows = [json.loads(ln) for ln in (cfg / "config-changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["new"] for r in rows if r["env"] == "REVIEW_MAX_FINDINGS"] == [12, 30]
    assert S.recent_changes(1)[0]["actor"] == "t"


def test_save_refuses_secret_and_bad_value(cfg):
    S.reset()
    with pytest.raises(ValueError):
        S.save_toml({"ado": {"pat": "x"}})
    with pytest.raises(ValidationError):
        S.save_toml({"review": {"min_confidence": 1.5}})
    assert not (cfg / "config.toml").exists()               # 兩次都沒寫檔


def test_render_toml_is_valid_and_documents_every_field(cfg):
    text = S.render_toml({"review": {"max_findings": 3}, "repos": {"repos_root": "/opt/repos"}})
    data = tomllib.loads(text)
    assert data["review"] == {"max_findings": 3} and data["repos"] == {"repos_root": "/opt/repos"}
    for gname, _c, fname, _i in S.iter_fields():
        assert f"# {fname}  (env " in text or f"\n{fname} = " in text, (gname, fname)
    assert "ADO_PAT>" in text and "pat = " not in text.replace("# pat = <secret", "")


def test_migrate_moves_non_default_non_secret_env_into_toml(cfg, monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "5")
    monkeypatch.setenv("REVIEW_MAX_PARALLEL", "6")         # 等於預設 → 不寫
    monkeypatch.setenv("ADO_PAT", "pat123")
    monkeypatch.setenv("KNOWLEDGE_BRANCHES", "master,release/*")
    values, secrets = S.migrate_from_env()
    assert values == {"review": {"max_findings": 5}, "knowledge": {"branches": ["master", "release/*"]}}
    assert secrets == ["ADO_PAT=pat123"]
    S.save_toml(values, actor="migrate")
    assert tomllib.loads((cfg / "config.toml").read_text(encoding="utf-8"))["review"] == {"max_findings": 5}


def test_reload_hooks_fire_on_reset():
    hits = []
    S.on_reload(lambda: hits.append(1))
    S.reset(); S.reset()
    assert hits == [1, 1]
    S._reload_hooks.remove(S._reload_hooks[-1])
