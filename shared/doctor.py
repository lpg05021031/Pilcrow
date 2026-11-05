"""doctor —— 把 RUNBOOK 裡所有「手動 curl / cat /proc / journalctl 判讀」收成一條指令。

    python -m shared.doctor                  全套檢查，印一張表
    python -m shared.doctor --pr-url <URL>   多做一件：拿 ADO_PAT 真打一條 PR 的 metadata
    python -m shared.doctor --no-network     只看設定、依賴、目錄、systemd（CI / 離線用）
    python -m shared.doctor --json           機器可讀（日後狀態頁直接吃）

退出碼：0 全綠或只有 WARN；1 至少一個 FAIL；2 設定本身解析不過（其他檢查沒開跑）。

分工：settings.py 管「值對不對」（型別、範圍、互相矛盾），這裡管「外面的世界
配不配合」——目錄在不在、port 有沒有人聽、PAT 打得通嗎、Langfuse 活著嗎。
每個檢查都是一個回傳 Check 的純函式；網路與 PATH 的探針（_tcp_open / _http_get /
_which / _run）集中在一處好 stub，tests/ 全部離線跑。

狀態語意：
  OK    這一項照設定運作中
  WARN  能跑但有隱患（選配功能沒裝、舊名字還在用、對照不上）
  FAIL  review 一定會壞或已經在壞（必要服務不在、PAT 打不通、依賴缺）
  SKIP  刻意不檢查（功能未啟用、--no-network、沒給 --pr-url）
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from shared import settings as S

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
_RANK = {OK: 0, SKIP: 0, WARN: 1, FAIL: 2}

TCP_TIMEOUT = 2.0
HTTP_TIMEOUT = 5.0


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str = ""


# ---------- 探針（tests 以 monkeypatch 換掉） ----------

def _which(binary: str) -> str | None:
    return shutil.which(binary)


def _tcp_open(host: str, port: int, timeout: float = TCP_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, *, auth: tuple[str, str] | None = None,
              headers: dict | None = None, timeout: float = HTTP_TIMEOUT,
              trust_env: bool = False) -> tuple[int | None, object, str]:
    """(status_code, json 或文字, 錯誤訊息)。連不上 → (None, None, 原因)。"""
    import httpx
    try:
        r = httpx.get(url, auth=auth, headers=headers or {}, timeout=timeout,
                      trust_env=trust_env, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return None, None, f"{type(e).__name__}: {e}"
    try:
        body: object = r.json()
    except ValueError:
        body = r.text
    return r.status_code, body, ""


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 127, f"{type(e).__name__}: {e}"


def _host_port(url: str) -> tuple[str, int] | None:
    u = urlparse(url)
    if not u.hostname:
        return None
    return u.hostname, u.port or (443 if u.scheme == "https" else 80)


def _git_head(path: Path) -> str:
    rc, out = _run(["git", "-C", str(path), "log", "-1", "--format=%h %cs"])
    return out if rc == 0 and out else "not a git repo"


# ---------- 各組檢查 ----------

def check_settings() -> tuple[S.Settings | None, list[Check]]:
    try:
        S.reset()
        s = S.settings()
    except ValidationError as e:
        bad = "; ".join(f"{'.'.join(map(str, err['loc']))}={err.get('input')!r}: {err['msg']}"
                        for err in e.errors())
        return None, [Check("settings", "parse", FAIL, bad)]
    rows = S.show_rows(s)
    from_env = sum(1 for r in rows if r[3] != "default")
    out = [Check("settings", "parse", OK,
                 f"{len(rows)} variables, {from_env} set from env, rest default")]
    out += [Check("settings", "smell", WARN, w) for w in S.warnings_for(s)]
    return s, out


_REQUIRED_DISTS = ("fastmcp", "langfuse", "openai", "fastapi", "uvicorn",
                   "httpx", "pydantic", "pydantic-settings")


def check_python(s: S.Settings) -> list[Check]:
    out = [Check("python", "interpreter", OK,
                 f"{sys.version.split()[0]} at {sys.executable}")]
    if sys.version_info < (3, 10):
        out[0] = Check("python", "interpreter", FAIL,
                       f"{sys.version.split()[0]} < 3.10（v2 用了 3.10+ 語法）")
    missing, have = [], []
    for dist in _REQUIRED_DISTS:
        try:
            have.append(f"{dist} {metadata.version(dist)}")
        except metadata.PackageNotFoundError:
            missing.append(dist)
    detail = ", ".join(have)
    if missing:
        detail = "missing: " + ", ".join(missing) + ("; " + detail if detail else "")
    out.append(Check("python", "dependencies", FAIL if missing else OK, detail))
    for name, mode in (("semgrep", s.static.semgrep), ("ruff", s.static.ruff)):
        path = _which(name)
        if mode == "off":
            out.append(Check("python", name, SKIP, f"STATIC_{name.upper()}=off"))
        elif path:
            rc, ver = _run([path, "--version"])
            out.append(Check("python", name, OK, f"{ver.splitlines()[0] if ver else path}"))
        else:
            out.append(Check("python", name, WARN,
                             f"not on PATH（STATIC_{name.upper()}={mode}）—— static 段會跳過；"
                             "systemd 跑的話確認 unit 的 PATH 含 venv/bin"))
    return out


def check_paths(s: S.Settings) -> list[Check]:
    out: list[Check] = []

    r = s.repos.repos_root
    if r is None:
        out.append(Check("paths", "REPOS_ROOT", SKIP, "unset —— review 照跑，但沒有 base 上下文"))
    elif not r.is_dir():
        out.append(Check("paths", "REPOS_ROOT", FAIL, f"{r} 不存在"))
    else:
        clones = [d for d in sorted(r.iterdir()) if (d / ".git").exists()]
        others = [d.name for d in sorted(r.iterdir()) if d.is_dir() and not (d / ".git").exists()]
        detail = f"{len(clones)} clone(s): " + ", ".join(
            f"{d.name}@{_git_head(d).split()[0]}" for d in clones[:6])
        if len(clones) > 6:
            detail += f", +{len(clones) - 6}"
        if others:
            detail += f"；非 git 目錄會被 repo-sync 跳過：{', '.join(others[:4])}"
        out.append(Check("paths", "REPOS_ROOT", OK if clones else WARN,
                         detail if clones else f"{r} 底下沒有任何 clone"))

    r = s.rules.root
    if r is None:
        out.append(Check("paths", "RULES_ROOT", SKIP, "unset —— 規則功能停用"))
    elif not r.is_dir():
        out.append(Check("paths", "RULES_ROOT", FAIL, f"{r} 不存在"))
    else:
        sub = r / s.rules.subdir
        common = sub / s.rules.common_dir
        n_md = len(list(sub.rglob("*.md"))) if sub.is_dir() else 0
        n_sg = len(list(r.rglob("*.semgrep.yml"))) + len(list(r.rglob("*.semgrep.yaml")))
        status = OK if common.is_dir() and n_md else WARN
        out.append(Check("paths", "RULES_ROOT", status,
                         f"{_git_head(r)}; {n_md} rule md, {n_sg} semgrep rule file(s)"
                         + ("" if common.is_dir() else f"; 找不到 {sub.name}/{s.rules.common_dir}/"
                            "（RULES_SUBDIR / RULES_COMMON_DIR 對不上目錄）")))

    r = s.knowledge.root
    if r is None:
        out.append(Check("paths", "KNOWLEDGE_ROOT", SKIP, "unset —— K 段停用"))
    elif not r.is_dir():
        out.append(Check("paths", "KNOWLEDGE_ROOT", FAIL, f"{r} 不存在"))
    else:
        pend = r / "_pending"
        n_pend = len(list(pend.rglob("pr-*.json"))) if pend.is_dir() else 0
        pages = [p for p in r.rglob("*.md") if not any(part.startswith("_") for part in p.relative_to(r).parts)]
        head = _git_head(r)
        out.append(Check("paths", "KNOWLEDGE_ROOT", OK if head != "not a git repo" else FAIL,
                         f"{head}; {len(pages)} page(s), {n_pend} pending draft(s)"
                         + ("" if head != "not a git repo" else "；harvester 是唯一 commit 者，這裡必須是 git repo")))
    return out


def check_services(s: S.Settings, network: bool) -> list[Check]:
    if not network:
        return [Check("services", "all", SKIP, "--no-network")]
    out: list[Check] = []

    code, body, err = _http_get(f"{s.orchestrator.base_url}/healthz")
    if code == 200 and isinstance(body, dict) and body.get("ok"):
        out.append(Check("services", "orchestrator", OK, f"{s.orchestrator.base_url}/healthz"))
    else:
        out.append(Check("services", "orchestrator", FAIL,
                         f"{s.orchestrator.base_url}/healthz → {code or err}"))

    for name, url, required in (("ado_pr_tools ①", s.tools.ado_url, True),
                                ("repo_reader ②", s.tools.reader_url, False),
                                ("review_result ③", s.tools.result_url, True)):
        hp = _host_port(url)
        if hp is None:
            out.append(Check("services", name, FAIL, f"URL 解析不出 host/port：{url}"))
            continue
        if _tcp_open(*hp):
            out.append(Check("services", name, OK, f"{hp[0]}:{hp[1]} listening"))
        else:
            out.append(Check("services", name, FAIL if required else WARN,
                             f"{hp[0]}:{hp[1]} 沒人聽" + ("" if required else "（v2 零呼叫者，可除役）")))

    if not s.graph.enabled:
        out.append(Check("services", "code graph ④", SKIP,
                         "未啟用" + ("" if not s.graph.tools_url else "（有 URL 但缺 GRAPH_TOOL_BLAST）")))
    else:
        hp = _host_port(s.graph.tools_url or "")
        ok = hp is not None and _tcp_open(*hp)
        out.append(Check("services", "code graph ④", OK if ok else FAIL,
                         f"{s.graph.tools_url}" + ("" if ok else " 沒人聽；pipeline 會優雅降級但沒有影響半徑")))
    return out


def check_langfuse(s: S.Settings, network: bool) -> list[Check]:
    if not network:
        return [Check("langfuse", "all", SKIP, "--no-network")]
    host = s.langfuse.host.rstrip("/")
    code, _body, err = _http_get(f"{host}/api/public/health")
    if code != 200:
        return [Check("langfuse", "health", FAIL,
                      f"{host}/api/public/health → {code or err}；orchestrator 是唯一接它的服務，"
                      "trace / prompt 版控 / eval 全部靠它")]
    out = [Check("langfuse", "health", OK, host)]
    pk, sk = s.langfuse.public_key, s.langfuse.secret_key.get_secret_value()
    if not pk or not sk:
        out.append(Check("langfuse", "keys", WARN, "LANGFUSE_PUBLIC_KEY / SECRET_KEY 未設 —— SDK 會在啟動時抱怨"))
        return out
    code, body, err = _http_get(f"{host}/api/public/projects", auth=(pk, sk))
    if code == 200:
        names = [p.get("name") for p in (body.get("data") if isinstance(body, dict) else []) or [] if isinstance(p, dict)]
        out.append(Check("langfuse", "keys", OK, "project: " + (", ".join(map(str, names)) or "(unnamed)")))
    elif code in (401, 403):
        out.append(Check("langfuse", "keys", FAIL, f"{code}：key 對不上這個 host"))
    else:
        out.append(Check("langfuse", "keys", WARN, f"/api/public/projects → {code or err}（key 無法驗證）"))
    return out


def check_llm(s: S.Settings, network: bool) -> list[Check]:
    if not network:
        return [Check("llm", "all", SKIP, "--no-network")]
    base = s.llm.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {s.llm.api_key.get_secret_value()}"}
    code, body, err = _http_get(f"{base}/models", headers=headers, trust_env=True)
    if code is None:
        return [Check("llm", "gateway", FAIL, f"{base} 連不上：{err}")]
    if code != 200:
        return [Check("llm", "gateway", WARN,
                      f"{base}/models → {code}（gateway 可能不支援 /models；LLM_MODEL={s.llm.model} 未驗證）")]
    ids = [m.get("id") for m in (body.get("data") if isinstance(body, dict) else []) or [] if isinstance(m, dict)]
    out = [Check("llm", "gateway", OK, f"{base}, {len(ids)} model(s)")]
    for label, model in (("LLM_MODEL", s.llm.model),
                         ("LLM_MODEL_ADVERSARY", s.llm.model_adversary),
                         ("LLM_MODEL_DIGEST", s.llm.model_digest)):
        if not model:
            continue
        if not ids:
            out.append(Check("llm", label, WARN, f"{model}（gateway 沒回模型清單，無法對帳）"))
        elif model in ids:
            out.append(Check("llm", label, OK, model))
        else:
            out.append(Check("llm", label, FAIL, f"{model} 不在 gateway 的清單裡：{', '.join(map(str, ids[:8]))}"))
    return out


def check_ado(s: S.Settings, network: bool, pr_url: str | None) -> list[Check]:
    pat = s.ado.pat.get_secret_value()
    if not pat:
        return [Check("ado", "ADO_PAT", FAIL, "未設 —— ①③ 與 harvester 都打不了 Azure DevOps")]
    out = [Check("ado", "ADO_PAT", OK, f"set（{len(pat)} chars）；verify_ssl={s.ado.verify_ssl}")]
    if not network or not pr_url:
        out.append(Check("ado", "reach", SKIP, "給 --pr-url <一條真 PR> 才會真打一次 API"))
        return out
    from shared.azdo import client, get_json, parse_pr_url
    try:
        ref = parse_pr_url(pr_url)
    except ValueError as e:
        out.append(Check("ado", "reach", FAIL, str(e)))
        return out
    try:
        with client() as c:
            pr = get_json(c, ref.pr_api)
        out.append(Check("ado", "reach", OK,
                         f"PR {ref.pr_id} @ {ref.repo}: \"{(pr.get('title') or '')[:50]}\" ({pr.get('status')})"))
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        hint = ""
        if "401" in msg or "403" in msg:
            hint = "；PAT 過期或權限不足（至少 Code > Read）"
        elif "SSL" in msg or "CERTIFICATE" in msg.upper():
            hint = "；公司自簽 CA → ADO_VERIFY_SSL 指向系統合併包"
        elif "Proxy" in msg or "proxy" in msg:
            hint = "；HTTPS_PROXY 設了嗎"
        out.append(Check("ado", "reach", FAIL, f"{type(e).__name__}: {msg[:160]}{hint}"))
    return out


_UNITS = ("review-orchestrator", "pr-review-ado", "pr-review-reader", "pr-review-results")
_TIMERS = ("pr-review-repo-sync.timer", "pr-review-knowledge-harvest.timer")


def check_systemd(s: S.Settings) -> list[Check]:
    if not _which("systemctl"):
        return [Check("systemd", "all", SKIP, "沒有 systemctl（非 Linux 主機或容器）")]
    out: list[Check] = []

    def _state(unit: str) -> tuple[str, str]:
        _rc, load = _run(["systemctl", "show", unit, "-p", "LoadState", "--value"])
        _rc, active = _run(["systemctl", "show", unit, "-p", "ActiveState", "--value"])
        return load.strip(), active.strip()

    for u in _UNITS:                      # 常駐組怕 dead
        load, active = _state(u)
        if load == "not-found":
            out.append(Check("systemd", u, SKIP, "unit 未安裝"))
        elif active == "active":
            out.append(Check("systemd", u, OK, "active"))
        else:
            out.append(Check("systemd", u, FAIL, f"{active or 'unknown'}；journalctl -u {u} -n 30"))
    for t in _TIMERS:                     # 定時組怕 failed；oneshot 平時 inactive 是待命
        load, active = _state(t)
        if load == "not-found":
            out.append(Check("systemd", t, SKIP, "timer 未安裝"))
        elif active == "active":
            _rc, nxt = _run(["systemctl", "show", t, "-p", "NextElapseUSecRealtime", "--value"])
            out.append(Check("systemd", t, OK, f"active; next {nxt.strip() or '?'}"))
        else:
            out.append(Check("systemd", t, WARN, f"{active or 'unknown'}；systemctl list-timers {t}"))
    return out


# ---------- 組裝 ----------

def run(*, network: bool = True, pr_url: str | None = None) -> tuple[list[Check], int]:
    s, checks = check_settings()
    if s is None:
        return checks, 2
    for fn in (lambda: check_python(s), lambda: check_paths(s),
               lambda: check_services(s, network), lambda: check_langfuse(s, network),
               lambda: check_llm(s, network), lambda: check_ado(s, network, pr_url),
               lambda: check_systemd(s)):
        try:
            checks += fn()
        except Exception as e:  # noqa: BLE001  —— 一組炸掉不該讓整張表消失
            checks.append(Check("doctor", "internal", FAIL, f"{type(e).__name__}: {e}"))
    worst = max((_RANK[c.status] for c in checks), default=0)
    return checks, 1 if worst >= 2 else 0


def render(checks: list[Check]) -> str:
    lines: list[str] = []
    cur = None
    w = max((len(c.name) for c in checks), default=10)
    for c in checks:
        if c.group != cur:
            lines.append(f"\n[{c.group}]")
            cur = c.group
        lines.append(f"  {c.status:<4} {c.name:<{w}}  {c.detail}")
    counts = {k: sum(1 for c in checks if c.status == k) for k in (OK, WARN, FAIL, SKIP)}
    lines.append("")
    lines.append("  ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(lines).lstrip("\n")


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m shared.doctor",
                                 description="pr-review-suite 健康檢查：設定、依賴、目錄、服務、Langfuse、LLM、ADO、systemd")
    ap.add_argument("--pr-url", help="一條真 PR 的 URL；給了才會拿 ADO_PAT 真打一次 API")
    ap.add_argument("--no-network", action="store_true", help="跳過所有網路探針（CI / 離線）")
    ap.add_argument("--json", action="store_true", help="機器可讀輸出")
    a = ap.parse_args(argv)
    checks, code = run(network=not a.no_network, pr_url=a.pr_url)
    if a.json:
        print(json.dumps({"exit": code, "checks": [asdict(c) for c in checks]},
                         ensure_ascii=False, indent=2))
    else:
        print(render(checks))
        print({0: "→ 可以上工", 1: "→ 有 FAIL：先修上面標 FAIL 的項目",
               2: "→ 設定解析不過，其他檢查沒開跑"}[code])
    return code


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
