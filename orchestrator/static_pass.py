"""static pass（v2 第 1 段）—— 確定性掃描，零 LLM、零幻覺、行號精準。

做法：對每個變更檔做「稀疏重建」（anatomy.sparse_new_file，行號保真）寫進
暫存目錄 → semgrep / ruff 掃 → 只保留落在「新增行」上的命中（不對未變更
的程式說教）→ 產出 source="static"、confidence=1.0 的 findings。

規則來源：
  1) 專案內建 deploy/semgrep/starter.yml
  2) rules repo 裡的 semgrep 規則（RULES_ROOT 底下 **/*.semgrep.yml 與
     semgrep/**/*.yml）—— 機械可查的團隊規則寫成 semgrep，掛在這層，
     命中自動以該規則檔為 rule_ref（確定性執法，rule_ref 也是確定的）。

環境變數：STATIC_SEMGREP / STATIC_RUFF（預設 auto：裝了就跑，沒裝就跳過並註記）
production 和離線 eval 吃的都是同一份 diff —— 這層在兩邊行為一模一樣。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from orchestrator.anatomy import Anatomy, sparse_new_file
from orchestrator.models import Finding
from shared.settings import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
STARTER = _PROJECT_ROOT / "deploy" / "semgrep" / "starter.yml"

_SEV = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}


def _rules_semgrep_configs() -> list[tuple[Path, str]]:
    """(規則檔路徑, rule_ref) —— 來自 rules repo 的 semgrep 規則。"""
    r = settings().rules.root
    if r is None or not r.is_dir():
        return []
    seen: dict[Path, str] = {}
    for pat in ("**/*.semgrep.yml", "**/*.semgrep.yaml",
                "semgrep/**/*.yml", "semgrep/**/*.yaml"):
        for f in sorted(r.glob(pat)):
            seen.setdefault(f, str(f.relative_to(r)))
    return list(seen.items())


def _materialize(ana: Anatomy, tmp: Path) -> dict[Path, str]:
    """稀疏重建寫進暫存目錄。回傳 暫存路徑 → 原始 diff 路徑 的對照。"""
    mapping: dict[Path, str] = {}
    for fc in ana.files:
        body = sparse_new_file(fc)
        if not body.strip():
            continue
        rel = fc.path.lstrip("/")
        dst = (tmp / rel).resolve()
        if tmp not in dst.parents:            # 防路徑穿越（../../ 之類）
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body)
        mapping[dst] = fc.path
    return mapping


def _timeout() -> int:
    return settings().static.timeout


def _run(cmd: list[str], env: dict | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_timeout(), env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out (STATIC_TIMEOUT={_timeout()}s)"
    except OSError as e:
        return -1, "", str(e)


_RULE_ID = re.compile(r"^\s*-\s*id:\s*([\w.\-]+)", re.M)


def _id_map(configs: list[tuple[Path, str]]) -> dict[str, str]:
    """規則 id → 來源規則檔（rule_ref 用）。id 撞名時先到先贏。"""
    out: dict[str, str] = {}
    for cfg, ref in configs:
        try:
            text = cfg.read_text(errors="replace")
        except OSError:
            continue
        for rid in _RULE_ID.findall(text):
            out.setdefault(rid, ref)
    return out


def _semgrep(tmp: Path, mapping: dict[Path, str], added: dict[str, set[int]],
             notes: list[str]) -> list[Finding]:
    configs: list[tuple[Path, str]] = []
    if STARTER.is_file():
        configs.append((STARTER, ""))
    configs += _rules_semgrep_configs()
    if not configs:
        notes.append("semgrep: no rule configs available, skipped")
        return [], []
    checked_refs = sorted({ref for _, ref in configs if ref})
    ids = _id_map(configs)
    cmd = ["semgrep", "scan", "--json", "--quiet", "--metrics=off",
           "--timeout", "30", "--no-git-ignore"]
    for cfg, _ in configs:
        cmd += ["--config", str(cfg)]
    cmd.append(str(tmp))
    # 內網重點：關掉啟動時的版本檢查（連不上 semgrep.dev 會吊到逾時）
    env = dict(os.environ, SEMGREP_ENABLE_VERSION_CHECK="0")
    code, out, err = _run(cmd, env=env)
    if code < 0 or not out.strip():
        notes.append(f"semgrep failed: {err[:120]}")
        return [], []
    try:
        results = json.loads(out).get("results", [])
    except json.JSONDecodeError:
        notes.append("semgrep: non-JSON output, skipped")
        return [], []
    finds: list[Finding] = []
    for r in results:
        p = Path(r.get("path", "")).resolve()
        orig = mapping.get(p)
        if not orig:
            continue
        line = int((r.get("start") or {}).get("line") or 0)
        if line not in added.get(orig, set()):
            continue                       # 只評新增行，不對未變更程式說教
        extra = r.get("extra") or {}
        meta = extra.get("metadata") or {}
        check = str(r.get("check_id", ""))
        ref = str(meta.get("rule_ref", ""))
        if not ref:                        # check_id 可能帶路徑前綴，取尾段對 id 表
            ref = ids.get(check) or ids.get(check.rsplit(".", 1)[-1], "")
        finds.append(Finding(
            rule=str(meta.get("review_rule", "SECURITY")).upper(),
            file=orig, line=line,
            severity=_SEV.get(str(extra.get("severity", "")).upper(), "warning"),
            note=(extra.get("message") or check),
            rule_ref=ref,
            source="static", confidence=1.0,
        ))
    return finds, checked_refs


def _ruff(mapping: dict[Path, str], added: dict[str, set[int]],
          notes: list[str]) -> list[Finding]:
    py = [p for p in mapping if p.suffix == ".py"]
    if not py:
        return []
    cmd = ["ruff", "check", "--select", "F821,F811,F601,E711,E712",
           "--output-format", "json", "--no-cache", "--isolated",
           *map(str, py)]
    code, out, err = _run(cmd)
    if not out.strip():
        if code not in (0, 1):
            notes.append(f"ruff failed: {err[:160]}")
        return []
    try:
        results = json.loads(out)
    except json.JSONDecodeError:
        notes.append("ruff: non-JSON output, skipped")
        return []
    finds: list[Finding] = []
    for r in results:
        p = Path(r.get("filename", "")).resolve()
        orig = mapping.get(p)
        line = int((r.get("location") or {}).get("row") or 0)
        if not orig or line not in added.get(orig, set()):
            continue
        finds.append(Finding(
            rule="BUG", file=orig, line=line, severity="warning",
            note=f"[{r.get('code')}] {r.get('message', '')}",
            source="static", confidence=1.0,
        ))
    return finds


# 公開名：knowledge 的蒸餾切片排序與 primary_entity fallback 吃同一份判斷——
# 「哪些檔是測試」「哪一行是新宣告」只能有一個定義，否則兩邊會慢慢漂移。
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?)(/|$)|(^|/)(test_[^/]+|[^/]+_test)\.py$"
    r"|(^|/)[A-Za-z0-9_]*Tests?\.java$|(^|/)[A-Za-z0-9_]*Spec\.[a-z]+$"
    r"|\.(spec|test)\.[jt]sx?$", re.I)
_BEHAVIOR_EXT = {".java", ".py", ".kt", ".cs", ".go", ".ts", ".js", ".scala"}
DECL_RE = re.compile(
    r"^\s*(def |class |function |func |fun |"
    r"(public|protected|private)\b[^;=]*\(|"
    r"(static|final)\b[^;=]*\([^)]*\)\s*\{)")


def _missing_test(ana: Anatomy) -> list[Finding]:
    """確定性 MISSING-TEST：新增了可呼叫行為、同 diff 卻沒碰任何測試檔。
    刻意保守：只認「新增宣告」為行為訊號 —— 純重構（改常數/搬呼叫）不觸發。
    STATIC_MISSING_TEST=false 可整段關閉。"""
    if not settings().static.missing_test:
        return []
    if any(TEST_PATH_RE.search(fc.path.lstrip("/")) for fc in ana.files):
        return []                                   # diff 有帶測試 → 不吭聲
    behavior: list[tuple[int, str, int]] = []       # (新增宣告數, path, 首行)
    for fc in ana.files:
        if Path(fc.path).suffix.lower() not in _BEHAVIOR_EXT:
            continue
        decls = [ln for ln, text in fc.added if DECL_RE.match(text)]
        if decls:
            behavior.append((len(decls), fc.path, decls[0]))
    if not behavior:
        return []
    behavior.sort(key=lambda x: (-x[0], x[1]))
    _, primary, line = behavior[0]
    names = ", ".join(b[1] for b in behavior[:3])
    return [Finding(
        rule="MISSING-TEST", file=primary, line=line, severity="warning",
        note=(f"This diff adds or changes behavior in {len(behavior)} file(s) "
              f"({names}) but touches no test files"),
        suggestion="Add or update tests covering the new/changed behavior "
                   "in the same pull request.",
        source="static", confidence=1.0,
    )]


def run_static(ana: Anatomy) -> tuple[list[Finding], list[str], list[str]]:
    """回傳 (findings, notes, checked_rule_refs)。
    checked_rule_refs = 實際掃描過的「團隊 semgrep 規則檔」——0 命中即為
    該規則的機械通過證據。掃描器缺席／故障 = 註記並跳過，絕不炸 review。"""
    notes: list[str] = []
    checked: list[str] = []
    added = {fc.path: fc.added_linenos for fc in ana.files}
    if not any(added.values()):
        return [], notes, checked
    tmp = Path(tempfile.mkdtemp(prefix="crg-static-"))
    try:
        mapping = _materialize(ana, tmp)
        finds: list[Finding] = []
        if settings().static.semgrep_enabled:
            sg, checked = _semgrep(tmp, mapping, added, notes)
            finds += sg
        else:
            notes.append("semgrep: disabled or not installed")
        if settings().static.ruff_enabled:
            finds += _ruff(mapping, added, notes)
        else:
            notes.append("ruff: disabled or not installed")
        finds += _missing_test(ana)
        return finds, notes, checked
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
