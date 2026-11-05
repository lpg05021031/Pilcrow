"""控制台（/console）的鎖：認證、兩頁渲染、Apply 的驗證與危險旋鈕、env 覆寫唯讀。全離線。"""
import pytest
from fastapi.testclient import TestClient

import shared.doctor as D
import shared.settings as S


@pytest.fixture
def client(tmp_path, monkeypatch):
    for _g, _c, _f, info in S.iter_fields():
        for n in S.env_names(info):
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ADO_PAT", "pat")
    # overview 會跑 doctor：把網路探針全部 stub 掉
    monkeypatch.setattr(D, "_tcp_open", lambda h, p, timeout=0: True)
    monkeypatch.setattr(D, "_http_get", lambda url, **kw: (200, {"ok": True, "jobs": {}}, ""))
    monkeypatch.setattr(D, "_which", lambda b: None)
    monkeypatch.setattr(D, "_run", lambda cmd, timeout=0: (0, ""))
    import orchestrator.app as A
    import orchestrator.console.router as R
    R._doctor_cache.update(ts=0.0, checks=[])
    with TestClient(A.app) as c:
        yield c


def test_open_mode_without_key_shows_banner(client):
    r = client.get("/console")
    assert r.status_code == 200 and "開放模式" in r.text and "ORCH_API_KEY 未設" in r.text
    assert client.get("/console/static/console.css").status_code == 200


def test_key_requires_login_and_cookie_works(client, monkeypatch):
    monkeypatch.setenv("ORCH_API_KEY", "k3y")
    r = client.get("/console/settings", follow_redirects=False)
    assert r.status_code == 302 and "/console/login" in r.headers["location"]
    r = client.post("/console/login", data={"token": "wrong", "next": "/console/settings"},
                    follow_redirects=False)
    assert "error=1" in r.headers["location"]
    r = client.post("/console/login", data={"token": "k3y", "next": "/console/settings"},
                    follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/console/settings"
    assert client.get("/console/settings").status_code == 200
    client.post("/console/logout")
    assert client.get("/console/settings", follow_redirects=False).status_code == 302


def test_settings_page_lists_every_group_with_sources(client, monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "4")
    r = client.get("/console/settings")
    assert r.status_code == 200
    for g in S.GROUP_ORDER:
        assert f'data-group="{g}"' in r.text
    assert "env:REVIEW_MAX_FINDINGS" in r.text and "由環境變數覆寫" in r.text
    assert "secret 只能放 secrets.env" in r.text


def test_apply_writes_toml_and_hot_reloads(client, tmp_path):
    r = client.post("/console/settings/review", data={"max_findings": "12"})
    assert r.status_code == 200 and "已套用（1 項）" in r.text
    assert S.settings().review.max_findings == 12
    assert (tmp_path / "config.toml").exists()
    assert "REVIEW_MAX_FINDINGS" in (tmp_path / "config-changes.jsonl").read_text(encoding="utf-8")
    r = client.post("/console/settings/review", data={"max_findings": ""})
    assert "已套用（1 項）" in r.text and S.settings().review.max_findings == 30


def test_apply_rejects_bad_values_inline(client):
    r = client.post("/console/settings/review", data={"max_findings": "abc"})
    assert r.status_code == 200 and "要是數字" in r.text and S.settings().review.max_findings == 30
    r = client.post("/console/settings/review", data={"min_confidence": "1.5"})
    assert "has-err" in r.text and S.settings().review.min_confidence == 0.55


def test_dangerous_flip_needs_confirmation(client):
    r = client.post("/console/settings/posting", data={"_b_dry_run": "1"})     # 關掉 DRY_RUN
    assert "我確定" in r.text and S.settings().posting.dry_run is True
    r = client.post("/console/settings/posting", data={"_b_dry_run": "1", "confirm_dry_run": "on"})
    assert "已套用" in r.text and S.settings().posting.dry_run is False
    # 翻回安全側不用確認
    r = client.post("/console/settings/posting", data={"_b_dry_run": "1", "dry_run": "on"})
    assert "已套用" in r.text and S.settings().posting.dry_run is True


def test_env_locked_and_secret_fields_are_ignored_on_apply(client, monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "4")
    r = client.post("/console/settings/review", data={"max_findings": "99", "max_parallel": "3"})
    assert "已套用（1 項）" in r.text
    assert S.settings().review.max_findings == 4 and S.settings().review.max_parallel == 3
    r = client.post("/console/settings/ado", data={"pat": "newpat", "diff_max_files": "50"})
    assert "已套用（1 項）" in r.text and S.settings().ado.pat.get_secret_value() == "pat"


def test_absent_checkbox_does_not_flip_bool(client):
    r = client.post("/console/settings/review", data={"max_findings": "11"})   # 沒帶 adversary
    assert "已套用（1 項）" in r.text and S.settings().review.adversary is True


def test_run_job_button_redirects_and_unknown_group_404(client):
    r = client.post("/console/jobs/repo-sync/run", follow_redirects=False)
    assert r.status_code == 303
    assert client.post("/console/settings/nope", data={}).status_code == 404


def test_untouched_text_field_with_non_string_default_is_not_written(client, tmp_path):
    """表單把 ADO_VERIFY_SSL 送成字串 "true"；預設是 bool True —— 不該被當成改動寫進 toml。"""
    r = client.post("/console/settings/ado", data={"verify_ssl": "true", "diff_max_files": "250"})
    assert "已套用（1 項）" in r.text
    assert "verify_ssl" not in (tmp_path / "config.toml").read_text(encoding="utf-8").replace("# verify_ssl", "")
    assert S.source_of("ado", "verify_ssl") == "default" and S.source_of("ado", "diff_max_files") == "file"
