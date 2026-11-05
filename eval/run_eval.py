"""跑一輪 eval：golden dataset → run_review（離線、可重現）→ 分數回 Langfuse。

計分是 deterministic 的：scorer 是純函式，沒有 LLM 參與評分；run 間的
分數浮動全部來自被評的 agent 本身（所以定基線時同一 config 跑兩輪量雜訊底）。

expected_output 支援兩種 shape（可混用，preflight 會驗）：

v2 結構比對（建議）——比 findings 的 rule + file（可選行號容差、rule_ref）：
  {
    "expected": [
      {"rule": "BUG", "file": "src/calculator.py"},
      {"rule": "SECURITY", "file": "src/api.py",
       "rule_ref": "rules/common/security.md", "line": 16, "line_tolerance": 5}
    ],
    "allowed_extra": 2,          # 容忍幾條合理的額外 finding，超過算噪音
    "forbidden": ["LGTM"]
  }
  乾淨 PR 負樣本："expected": [] + "allowed_extra": 0 —— 量 false positive。

舊版關鍵字（相容）：{"must_catch": [...], "forbidden": [...], "max_findings": N}

指標：must_catch_recall（兩種 shape 同名，CI gate 讀這個）、precision（僅 v2）、
noise_flag、forbidden_hit。run 的 metadata 會記 rules_sha × prompt_version ×
llm_model —— 每輪自我描述，分數變動才對得回配置。

知識依賴題（kfix-*，Phase 2.2）：expected 的某條 finding 帶 "knowledge_ref"，
量 reviewer 有沒有真的**站在**帳本條目上提出這條發現 → knowledge_recall。
開跑時 run-level 把 KNOWLEDGE_ROOT 覆寫到 eval/fixtures/knowledge（可用
EVAL_KNOWLEDGE_ROOT 改指），每題 input.repo = kfix-<n> 對應一個子目錄；其餘
考題不帶 repo，天然拿不到任何頁，零污染。跑完再對同一批題跑一次
KNOWLEDGE_ROOT="" 的對照組 → knowledge_delta = 有知識 − 無知識。
delta 是整個知識迴圈唯一重要的數：>0 才值得繼續蓋。

用法：
  python -m eval.run_eval v2-structured
  EVAL_MIN_RECALL=0.7 EVAL_MAX_NOISE=0.2 EVAL_MAX_FORBIDDEN=0 \
      python -m eval.run_eval v2-gated        # 三道門檻 = CI gate
  EVAL_KNOWLEDGE_AB=false python -m eval.run_eval quick   # 跳過對照組
退出碼：0 通過；1 gate 擋下；2 preflight 不通過（根本沒開跑）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from langfuse import Evaluation, get_client

from eval.preflight import (check_environment, check_fixture_geometry,
                            check_items, check_knowledge_fixtures,
                            is_knowledge_item)
from orchestrator.core import PROMPT_NAME, run_review
from orchestrator.rules import load_rules
from shared.settings import reset as reset_settings
from shared.settings import settings

DATASET = "pr-review-golden"
KNOWLEDGE_FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"

_recalls: list[float] = []
_noises: list[float] = []
_forbiddens: list[float] = []
# 知識分數按「這一輪是有知識還是對照組」分開收，delta 才算得出來；再按題型
# （單頁 / distractor）分桶，delta=0 時才判得出是 consumption 問題還是排序問題
_PHASE = {"name": "with"}
_k_recalls: dict[str, list[tuple[str, float]]] = {"with": [], "without": []}
_KTYPES = ("single-page", "distractor")


async def task(*, item, **kwargs):
    """對每條 dataset item 跑一次 review（框架支援 async task）。"""
    inp = item.input if isinstance(item.input, dict) else {}
    meta = {k: inp[k] for k in ("repo", "title", "description")
            if inp.get(k)}
    result = await run_review(inp["diff"], meta or None)
    return {"markdown": result.markdown, "findings": result.findings_summary()}


# ---------- v2 結構比對（純函式、確定性） ----------

def _norm_path(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


def _match(exp: dict, f: dict) -> bool:
    if (exp.get("rule", "")).strip().upper() != (f.get("rule") or "").strip().upper():
        return False
    e, g = _norm_path(exp.get("file", "")), _norm_path(f.get("file") or "")
    if e:                                   # 沒填 file = 類別級期望，只比 rule
        if not (g and (g.endswith(e) or e.endswith(g))):
            return False
    if exp.get("line") is not None:
        if f.get("line") is None:
            return False
        if abs(int(f["line"]) - int(exp["line"])) > int(exp.get("line_tolerance", 5)):
            return False
    ref = exp.get("rule_ref")
    if ref:
        got = _norm_path(f.get("rule_ref") or "")
        want = _norm_path(ref)
        if not (got and (got == want or got.endswith(want))):
            return False
    return True


def _score_v2(expected: list[dict], findings: list[dict],
              allowed_extra: int | None) -> tuple[float, float, float, str]:
    """回傳 (recall, precision, noise_flag, comment)。greedy 一對一配對。"""
    used: set[int] = set()
    missed: list[str] = []
    by_src = {"static": 0, "llm": 0}
    for exp in expected:
        hit = next((i for i, f in enumerate(findings)
                    if i not in used and _match(exp, f)), None)
        if hit is None:
            missed.append(f"{exp.get('rule')}@{exp.get('file')}")
        else:
            used.add(hit)
            src = (findings[hit].get("source") or "llm").lower()
            by_src["static" if src == "static" else "llm"] += 1
    matched = len(expected) - len(missed)
    recall = matched / len(expected) if expected else 1.0
    precision = 1.0 if not findings else matched / len(findings)
    extras = len(findings) - len(used)
    noise = float(allowed_extra is not None and extras > allowed_extra)
    comment = ("all caught" if not missed else "missed: " + ", ".join(missed)) \
        + f"; extra findings: {extras}" \
        + (f" (allowed {allowed_extra})" if allowed_extra is not None else "") \
        + f"; matched by source: static {by_src['static']} / llm {by_src['llm']}"
    return recall, precision, noise, comment, by_src


def _score_knowledge(expected: list[dict],
                     findings: list[dict]) -> tuple[float, str] | None:
    """knowledge_recall：expected 裡帶 knowledge_ref 的期望，有幾條被「同一條
    finding 命中 + 真的引了那個 id」滿足。None = 這題不是知識依賴題。

    要求兩件事同時成立是刻意的：只引不抓、或只抓不引，都不算 grounded ——
    前者是應付合約，後者證明不了這條發現是知識帶來的。
    """
    wanted = [e for e in expected if e.get("knowledge_ref")]
    if not wanted:
        return None
    used: set[int] = set()
    hits, missed = 0, []
    for exp in wanted:
        ref = str(exp["knowledge_ref"]).strip().upper()
        idx = next((i for i, f in enumerate(findings)
                    if i not in used and _match(exp, f)
                    and ref in {str(r).strip().upper()
                                for r in (f.get("knowledge_ref") or [])}), None)
        if idx is None:
            missed.append(ref)
        else:
            used.add(idx)
            hits += 1
    comment = (f"grounded {hits}/{len(wanted)}"
               + ("; not grounded: " + ", ".join(missed) if missed else ""))
    return hits / len(wanted), comment


def scorer(*, input, output, expected_output, metadata=None, **kwargs):
    md = (output or {}).get("markdown", "")
    findings = (output or {}).get("findings", [])
    low = md.lower()
    exp = expected_output or {}

    evs: list[Evaluation] = []
    if "expected" in exp:  # v2 結構比對
        recall, precision, noise, comment, by_src = _score_v2(
            exp["expected"], findings, exp.get("allowed_extra"))
        evs.append(Evaluation(name="must_catch_recall", value=recall, comment=comment))
        evs.append(Evaluation(name="precision", value=precision))
        matched = by_src["static"] + by_src["llm"]
        evs.append(Evaluation(                     # headline 的 static 地板占比
            name="recall_static_share",
            value=(by_src["static"] / matched) if matched else 0.0))
    else:                  # 舊版關鍵字（相容）
        must = exp.get("must_catch", [])
        recall = sum(1 for k in must if k.lower() in low) / len(must) if must else 1.0
        noise = float(bool(exp.get("max_findings"))
                      and len(findings) > exp["max_findings"])
        evs.append(Evaluation(name="must_catch_recall", value=recall))

    forbidden = float(any(f.lower() in low for f in exp.get("forbidden", [])))
    evs.append(Evaluation(name="noise_flag", value=noise))
    evs.append(Evaluation(name="forbidden_hit", value=forbidden))

    k = _score_knowledge(exp.get("expected") or [], findings)
    if k is not None:
        evs.append(Evaluation(name="knowledge_recall", value=k[0], comment=k[1]))
        ktype = str((metadata or {}).get("ktype") or "unspecified")
        _k_recalls[_PHASE["name"]].append((ktype, k[0]))

    if _PHASE["name"] == "with":      # 對照組只貢獻 knowledge_recall，不動主指標
        _recalls.append(recall)
        _noises.append(noise)
        _forbiddens.append(forbidden)
    return evs


# ---------- 三道 CI gate（都是選配，設了才生效） ----------

def _gates(recall_avg: float, noise_rate: float, forbidden_rate: float) -> list[str]:
    fails: list[str] = []
    e = settings().eval
    if e.min_recall and recall_avg < e.min_recall:
        fails.append(f"recall {recall_avg:.2f} < 門檻 {e.min_recall}")
    if e.max_noise is not None and noise_rate > e.max_noise:
        fails.append(f"噪音率 {noise_rate:.2f} > 上限 {e.max_noise}")
    if e.max_forbidden is not None and forbidden_rate > e.max_forbidden:
        fails.append(f"禁語率 {forbidden_rate:.2f} > 上限 {e.max_forbidden}")
    return fails


def _knowledge_gate(delta: float) -> list[str]:
    """knowledge_delta 的門檻。刻意預設「沒設 = 不擋」：基線穩定兩輪之前
    這個數只 report，用一個沒有量尺的門把 CI 擋紅是 0.35 那類錯誤的重演。"""
    want = settings().eval.min_knowledge_delta
    if want is None or not _k_recalls["without"]:
        return []
    return ([] if delta >= want else
            [f"knowledge_delta {delta:+.2f} < 門檻 {want:+.2f}"])


def _prompt_version() -> int | None:
    try:
        return get_client().get_prompt(PROMPT_NAME).version
    except Exception:
        return None


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _knowledge_ab(lf, dataset, run_name: str, run_meta: dict) -> None:
    """對照組：同一批知識依賴題、KNOWLEDGE_ROOT="" 再跑一輪。

    只跑那幾題（不是整份考卷）—— delta 要的是同一批題的差，其餘題目沒有 repo，
    有沒有帳本對它們完全一樣，重跑只是燒錢。
    """
    k_items = [it for it in dataset.items if is_knowledge_item(it)]
    if not k_items:
        print("  知識依賴題：0 條（跳過對照組）")
        return
    if not settings().eval.knowledge_ab:
        print("  對照組已關閉（EVAL_KNOWLEDGE_AB=false）—— 只有上界，沒有 delta")
        return
    keep = os.environ.get("KNOWLEDGE_ROOT", "")
    _PHASE["name"] = "without"
    os.environ["KNOWLEDGE_ROOT"] = ""          # 這一輪的 agent 拿不到任何條目
    reset_settings()                           # settings() 有快取，改 env 要清
    try:
        lf.run_experiment(
            name=f"{run_name}-noknowledge",
            run_name=f"{run_name}-noknowledge",
            data=k_items,
            task=task,
            evaluators=[scorer],
            max_concurrency=2,
            metadata={**run_meta, "knowledge_root": "", "arm": "control"},
        )
        lf.flush()
    finally:
        _PHASE["name"] = "with"
        os.environ["KNOWLEDGE_ROOT"] = keep
        reset_settings()


def main() -> None:
    run_name = sys.argv[1] if len(sys.argv) > 1 else "v1-baseline"
    lf = get_client()
    dataset = lf.get_dataset(DATASET)

    items = list(dataset.items)
    problems, stats = check_items(items)           # preflight：驗不過不開跑
    problems = (check_environment() + check_fixture_geometry()
                + check_knowledge_fixtures(items) + problems)
    if problems:
        print(f"Preflight 不通過（{len(problems)} 個問題），不開跑：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)

    # run-level 覆寫（不做 per-item env 切換：load_for 走現成的 repo 隔離，
    # eval 併發也安全）。實際值記進 run metadata，分數才對得回配置。
    kroot = str(settings().eval.knowledge_root or KNOWLEDGE_FIXTURES)
    os.environ["KNOWLEDGE_ROOT"] = kroot
    reset_settings()

    run_meta = {
        "pipeline": settings().orchestrator.pipeline,
        "rules_sha": load_rules(None).sha or None,
        "prompt_version": _prompt_version(),
        "llm_model": settings().llm.model,
        "knowledge_root": kroot,
        "arm": "knowledge",
    }
    dataset.run_experiment(
        name=run_name,
        run_name=run_name,
        task=task,
        evaluators=[scorer],
        max_concurrency=2,   # 對內部 gateway 溫柔一點
        metadata=run_meta,
    )
    lf.flush()
    _knowledge_ab(lf, dataset, run_name, run_meta)

    n = len(_recalls)
    recall_avg = _mean(_recalls)
    noise_rate = _mean(_noises)
    forbidden_rate = _mean(_forbiddens)
    print(f"run '{run_name}'：{n} 條（v2 {stats['v2']}／舊 {stats['legacy']}／"
          f"乾淨 {stats['clean']}／知識 {stats['knowledge']}）")
    print(f"  recall 平均 {recall_avg:.2f}｜噪音率 {noise_rate:.2f}｜"
          f"禁語率 {forbidden_rate:.2f}")
    k_with = _mean([v for _, v in _k_recalls["with"]])
    k_without = _mean([v for _, v in _k_recalls["without"]])
    delta = k_with - k_without
    if _k_recalls["with"]:
        print(f"  knowledge_recall 有知識 {k_with:.2f}"
              f"（{len(_k_recalls['with'])} 題）｜對照組 {k_without:.2f}"
              f"（{len(_k_recalls['without'])} 題）｜"
              f"**knowledge_delta {delta:+.2f}**")
        for kt in _KTYPES:                     # 兩型分開讀 —— 診斷順序靠這兩行
            w = [v for t, v in _k_recalls["with"] if t == kt]
            wo = [v for t, v in _k_recalls["without"] if t == kt]
            if w:
                print(f"    · {kt:<12} 有知識 {_mean(w):.2f}（{len(w)} 題）"
                      f"｜對照組 {_mean(wo):.2f}｜delta {_mean(w) - _mean(wo):+.2f}")
    print(f"  配置：prompt v{run_meta['prompt_version']} × "
          f"rules {run_meta['rules_sha'] or '未啟用'} × {run_meta['llm_model']}"
          f" × knowledge {kroot or '（無）'}")
    print("→ UI：Datasets → pr-review-golden → Runs 看逐條分數與 trace")

    fails = _gates(recall_avg, noise_rate, forbidden_rate)
    fails += _knowledge_gate(delta)
    if fails:
        print("CI gate 擋下：" + "；".join(fails))
        sys.exit(1)


if __name__ == "__main__":
    main()
