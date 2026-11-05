"""Preflight：開跑 experiment 前逐條驗 dataset item 的 shape。

目的：UI 收編忘了「整形」的 item（例如 input 缺 diff、expected_output 缺
可計分的期望）應該死在開跑前三秒，而不是燒完半輪 LLM 之後噴 ERROR: 'diff'。

單獨跑：  python -m eval.preflight
被引用：  run_eval 開跑前自動呼叫（驗不過 exit 2、列出兇手、不開跑）

一條合格的 item：
  input            dict，必含非空字串 "diff"；選配 "repo"（str）
  expected_output  dict，必含 "expected"（v2 結構清單，乾淨 PR 給 []）
                   或 "must_catch"（舊版關鍵字清單）兩者之一
  v2 的 expected 每條必含 rule；file 選配（不填=類別級、只比 rule）；選配 line / line_tolerance / rule_ref
"""
from __future__ import annotations

import sys

from langfuse import get_client

DATASET = "pr-review-golden"

# 幾何前提考題：fixture 名 → 斷言（重放預算，驗證前提真的發生）
# 目前是空的：pr-129 / pr-130 隨著預算與科數調大而失效，已移除。機制留著——
# 日後再加「切片預算會破」這類考題時，在這裡登記斷言，才不會有題目安靜地
# 退化成「跟別題考一樣的東西」而沒人發現。
_GEOMETRY: dict[str, str] = {}


def check_environment() -> list[str]:
    """考卷的隱性環境依賴明示化：static 地板題沒有 semgrep = recall gate
    會以「模型漏抓」的誤導姿勢失敗——用真實原因在開跑前擋下。"""
    import shutil
    probs: list[str] = []
    if not shutil.which("semgrep"):
        probs.append("semgrep 不在 PATH —— 多題 SECURITY 期望靠 static 地板"
                     "保底，缺席會讓 recall gate 誤導性失敗。裝 semgrep 再跑。")
    return probs


def check_fixture_geometry() -> list[str]:
    """執行才抓得到的考卷錯：重放 _slices_for 的預算，驗證每題的幾何前提
    （例如「這題的切片預算真的會破」）確實成立。_GEOMETRY 空 = 無題可驗。"""
    from pathlib import Path as _P

    from orchestrator.anatomy import parse_diff
    from orchestrator.pipeline import _slices_for

    class _All:
        files: list = []
        name = "geometry"
        focus = ""
        hypotheses: list = []

    probs: list[str] = []
    fixtures = _P(__file__).parent / "fixtures"
    for fname, expect in _GEOMETRY.items():
        f = fixtures / fname
        if not f.exists():
            continue
        _, skipped = _slices_for(_All(), parse_diff(f.read_text()))
        if expect == "tripped" and not skipped:
            probs.append(f"{fname}: 預算沒被踩到（截斷路徑沒被考）")
        if expect == "dao-omitted" and not any("UserQueryDao" in x
                                               for x in skipped):
            probs.append(f"{fname}: UserQueryDao 未落在省略區——考題退化")
    return probs


def _is_active(item) -> bool:
    status = str(getattr(item, "status", "") or "").upper()
    return "ARCHIVED" not in status


def is_knowledge_item(item) -> bool:
    """知識依賴題（kfix-*）：input.repo 指到一個種子帳本目錄。
    run_eval 的對照組只重跑這一批。"""
    meta = getattr(item, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get("kind") == "knowledge":
        return True
    inp = getattr(item, "input", None) or {}
    return isinstance(inp, dict) and str(inp.get("repo") or "").startswith("kfix-")


def check_knowledge_fixtures(items) -> list[str]:
    """知識題的種子帳本必須真的在磁碟上——少一個目錄，那題會安靜地退化成
    「沒有知識的對照組」，delta 於是天生為 0 而且看不出原因。"""
    from pathlib import Path as _P
    import os

    root = _P(os.getenv("EVAL_KNOWLEDGE_ROOT", "").strip()
              or (_P(__file__).parent / "fixtures" / "knowledge"))
    probs: list[str] = []
    for item in items:
        if not _is_active(item) or not is_knowledge_item(item):
            continue
        repo = str((item.input or {}).get("repo") or "")
        if repo and not (root / repo).is_dir():
            probs.append(f"知識題 repo '{repo}' 在 {root} 底下沒有帳本目錄"
                         "—— 這題會退化成無知識對照組")
        exp = item.expected_output if isinstance(item.expected_output, dict) else {}
        if not any(e.get("knowledge_ref") for e in (exp.get("expected") or [])
                   if isinstance(e, dict)):
            probs.append(f"知識題 repo '{repo}' 的 expected 沒有任何 "
                         "knowledge_ref —— 量不到 knowledge_recall")
    return probs


def check_item(item) -> list[str]:
    """回傳這條 item 的所有問題（空清單 = 合格）。"""
    probs: list[str] = []
    inp = item.input
    if not isinstance(inp, dict):
        return [f"input 不是 dict（實際是 {type(inp).__name__}）"]
    diff = inp.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        probs.append('input 缺非空字串 "diff"（收編後要整形，見 README）')
    repo = inp.get("repo")
    if repo is not None and not isinstance(repo, str):
        probs.append('"repo" 要是字串')

    exp = item.expected_output
    if not isinstance(exp, dict):
        return probs + [
            "expected_output 不是 dict —— 沒有期望的 item 會無條件滿分，是死重"
        ]

    has_v2 = "expected" in exp
    has_legacy = isinstance(exp.get("must_catch"), list)
    if not has_v2 and not has_legacy:
        probs.append('expected_output 缺 "expected"（v2）或 "must_catch"（舊版）'
                     "—— 乾淨 PR 也要明寫 \"expected\": []")

    if has_v2:
        items = exp["expected"]
        if not isinstance(items, list):
            probs.append('"expected" 要是清單')
        else:
            for i, e in enumerate(items):
                if not isinstance(e, dict):
                    probs.append(f"expected[{i}] 不是 dict")
                    continue
                for key in ("rule",):        # file 選配：類別級期望可不填
                    if not isinstance(e.get(key), str) or not e[key].strip():
                        probs.append(f"expected[{i}] 缺非空字串 \"{key}\"")
                if "line" in e and e["line"] is not None \
                        and not isinstance(e["line"], int):
                    probs.append(f"expected[{i}] 的 \"line\" 要是整數")
                kref = e.get("knowledge_ref")
                if kref is not None and not isinstance(kref, str):
                    probs.append(f"expected[{i}] 的 \"knowledge_ref\" 要是字串"
                                 "（單一條目 id，例如 PR41-I1）")

    if "allowed_extra" in exp and (
            not isinstance(exp["allowed_extra"], int) or exp["allowed_extra"] < 0):
        probs.append('"allowed_extra" 要是 >=0 的整數')
    if "forbidden" in exp and not isinstance(exp["forbidden"], list):
        probs.append('"forbidden" 要是字串清單')
    if "max_findings" in exp and not isinstance(exp["max_findings"], int):
        probs.append('"max_findings" 要是整數')
    return probs


def check_items(items) -> tuple[list[str], dict]:
    """驗一批 items。回傳 (問題清單, 統計)。"""
    problems: list[str] = []
    stats = {"total": 0, "v2": 0, "legacy": 0, "clean": 0, "knowledge": 0}
    for item in items:
        if not _is_active(item):
            continue
        stats["total"] += 1
        if is_knowledge_item(item):
            stats["knowledge"] += 1
        exp = item.expected_output if isinstance(item.expected_output, dict) else {}
        if "expected" in exp:
            stats["v2"] += 1
            if exp["expected"] == []:
                stats["clean"] += 1
        elif isinstance(exp.get("must_catch"), list):
            stats["legacy"] += 1
        short = str(item.id)[:8]
        problems.extend(f"item {short}…: {p}" for p in check_item(item))
    return problems, stats


def main() -> None:
    dataset = get_client().get_dataset(DATASET)
    items = list(dataset.items)
    problems, stats = check_items(items)
    problems = (check_environment() + check_fixture_geometry()
                + check_knowledge_fixtures(items) + problems)
    print(f"dataset '{DATASET}'：{stats['total']} 條有效 item"
          f"（v2 結構 {stats['v2']}、舊版關鍵字 {stats['legacy']}、"
          f"乾淨負樣本 {stats['clean']}、知識依賴 {stats['knowledge']}）")
    if stats["clean"] == 0:
        print("提醒：沒有乾淨 PR 負樣本（expected: []）——只獎不罰，agent 會學會灌水")
    if problems:
        print(f"\nPreflight 不通過，{len(problems)} 個問題：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)
    print("Preflight 通過")


if __name__ == "__main__":
    main()
