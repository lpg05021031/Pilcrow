"""install —— 一台乾淨 Linux 機從零到可開控制台，一條指令。

    venv/bin/python -m shared.install   (root shell)      # 建目錄、寫空設定、寫 unit、跑 doctor
    venv/bin/python -m shared.install --dry-run            # 只印會做什麼
    venv/bin/python -m shared.install --no-unit            # 不碰 systemd（容器 / 手動跑）

做的事（全部冪等，已存在就跳過）：
  1. CONFIG_DIR（預設 /etc/pr-review-suite）：config.toml（由 schema 渲染、全是註解）、
     secrets.env（600，含該填的鍵名）。
  2. DATA_DIR（預設 /var/lib/pr-review-suite）：review 歷史。
  3. /etc/systemd/system/review-orchestrator.service：由 deploy/systemd 樣板代入
     --prefix / --user / CONFIG_DIR。
  4. chown 給 --user（預設 svc-review；不存在就只提醒）。
  5. 最後跑一次 doctor --no-network，印出接下來該去控制台設定精靈填什麼。

之後：把 secrets.env 填好 → systemctl enable --now review-orchestrator →
開 http://<主機>:8080/console/setup 一步步綠燈。RUNBOOK 就這一頁。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from shared import settings as S

SECRETS_TEMPLATE = """# pr-review-suite secrets —— 由 systemd EnvironmentFile 載入；chmod 600；不進 git、不進 config.toml
ADO_PAT=
LLM_API_KEY=not-needed
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
ORCH_API_KEY=
# 有 proxy / 自簽 CA 才打開：
# HTTPS_PROXY=http://proxy.corp:3128
# ADO_VERIFY_SSL=/etc/ssl/certs/ca-certificates.crt
"""

UNIT_TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "review-orchestrator.service.example"


def _say(dry: bool, msg: str) -> None:
    print(("[dry-run] " if dry else "") + msg)


def _write_if_absent(p: Path, text: str, mode: int | None, dry: bool) -> None:
    if p.exists():
        _say(dry, f"保留既有 {p}")
        return
    _say(dry, f"寫入 {p}")
    if dry:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        os.chmod(p, mode)


def render_unit(prefix: Path, user: str, config_dir: Path) -> str:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("/opt/pr-review-suite", prefix.as_posix())
    text = text.replace("Environment=CONFIG_DIR=/etc/pr-review-suite", f"Environment=CONFIG_DIR={config_dir.as_posix()}")
    text = text.replace("EnvironmentFile=-/etc/pr-review-suite/", f"EnvironmentFile=-{config_dir.as_posix()}/")
    text = text.replace("User=svc-review", f"User={user}")
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("# 複製為")) + "\n"


def _chown(p: Path, user: str, dry: bool) -> None:
    if os.name != "posix":
        return
    import pwd
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        _say(dry, f"帳號 {user} 不存在：跳過 chown（之後 useradd 再 chown -R {user}: {p}）")
        return
    _say(dry, f"chown -R {user}: {p}")
    if dry:
        return
    for q in [p, *p.rglob("*")]:
        try:
            os.chown(q, pw.pw_uid, pw.pw_gid)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m shared.install")
    ap.add_argument("--prefix", default=str(Path(__file__).resolve().parents[1]),
                    help="安裝根（含 venv/）；預設 = 這份程式碼所在目錄")
    ap.add_argument("--config-dir", default=os.getenv("CONFIG_DIR") or S.DEFAULT_CONFIG_DIR)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR") or "/var/lib/pr-review-suite")
    ap.add_argument("--user", default="svc-review")
    ap.add_argument("--unit-path", default="/etc/systemd/system/review-orchestrator.service")
    ap.add_argument("--no-unit", action="store_true", help="不寫 systemd unit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    dry = a.dry_run
    cfg, data, prefix = Path(a.config_dir), Path(a.data_dir), Path(a.prefix)

    print(f"安裝根 {prefix}；設定 {cfg}；資料 {data}；服務帳號 {a.user}")
    _write_if_absent(cfg / "config.toml", S.render_toml({}), 0o644, dry)
    _write_if_absent(cfg / "secrets.env", SECRETS_TEMPLATE, 0o600, dry)
    _say(dry, f"建立 {data}")
    if not dry:
        data.mkdir(parents=True, exist_ok=True)
    for p in (cfg, data):
        _chown(p, a.user, dry)
    if not a.no_unit:
        unit = render_unit(prefix, a.user, cfg)
        _write_if_absent(Path(a.unit_path), unit, 0o644, dry)
    if shutil.which("semgrep") is None:
        print("提醒：semgrep 不在 PATH —— static 地板會跳過；venv/bin/pip install semgrep ruff")

    print("\n接下來：")
    print(f"  1. 編輯 {cfg / 'secrets.env'}（ADO_PAT、LANGFUSE_*；要登入控制台就設 ORCH_API_KEY）")
    if not a.no_unit:
        print("  2. (root) systemctl daemon-reload; systemctl enable --now review-orchestrator")
    print("  3. 開 http://<主機>:8080/console/setup，照精靈一步步綠燈")
    print("  4. 任何時候：venv/bin/python -m shared.doctor")
    if not dry:
        os.environ["CONFIG_DIR"] = str(cfg)
        os.environ["DATA_DIR"] = str(data)
        from shared import doctor
        checks, _code = doctor.run(network=False)
        print("\n" + doctor.render(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
