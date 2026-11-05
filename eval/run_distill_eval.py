"""蒸餾考卷 —— 跑一輪 distiller 的離線評測（Phase 4）。

為什麼要有這張考卷：review pipeline 有 32 題押著，K 段的 LLM 輸出過去零考卷
——改 `pr-review-distiller` 的 prompt 等於裸奔。從此改它跟改 reviewer prompt
一樣要先過考。

計分全部確定性（零 LLM 評審）：

| 分數 | 量什麼 | 怎麼判 |
|---|---|---|
| intent_ok        | 「PR 沒講 why 就不要編」守不守得住 | intent_stated == expected |
| no_fabrication   | why 缺席題有沒有冒出 rationale 措辭 | forbidden_mention 不出現 |
| retires_ok       | supersede 的 retires_id 精準度 | 集合完全相等（誤殺別條也扣） |
| evidence_ok      | 產出的 evidence 過不過 Phase 1 驗證器 | 剝掉 0 個 token、丟 0 條 decision |
| must_mention     | 決策關鍵詞有沒有被寫進條目 | 關鍵詞命中率 |
| primary_ok       | primary_entity 選得對不對 | 模型自己就選中變更檔內的正確檔 |
| no_hallucination | 材料撐不起 invariant 時有沒有硬蒸 | invariants 為空 |
| distill_score    | 以上適用項目的平均 | — |

用法：
  python -m eval.run_distill_eval distill-baseline
  EVAL_MIN_DISTILL_SCORE=0.8 python -m eval.run_distill_eval distill-gated
退出碼：0 通過；1 gate 擋下。門檻未設 = 只報數不擋（先蹲基線再升硬門）。
"""
from __future__ import annotations

import sys

from langfuse import Evaluation, get_client

from orchestrator.anatomy import match_path, parse_diff
from orchestrator.knowledge import DISTILLER_PROMPT_NAME, distill_once
from shared.settings import settings

DATASET = "pr-review-distill-golden"

_scores: list[float] = []


async def task(*, item, **kwargs):
    """跑一次真正的蒸餾（同一支 distill_once，含證據驗證與 primary 修正）。

    finding_ids 給空集合：考卷沒有 review 報告，`F<n>` 形式的 evidence 本來就
    不該過——這也順帶考「沒有報告時模型會不會硬掰 finding 編號」。
    """
    inp = item.input if isinstance(item.input, dict) else {}
    diff = inp.get("diff") or ""
    ana = parse_diff(diff)
    meta = {"title": inp.get("title", ""), "description": inp.get("description", ""),
            "target_branch": "release/eval"}
    data, info = await distill_once(
        pr_id=1, repo="eval", meta=meta, ana=ana,
        ana_files=[fc.path for fc in ana.files],
        finding_ids=set(), finding_files=[],
        catalogue=inp.get("catalogue") or "")
    return {"draft": data, "info": {k: v for k, v in info.items()
                                    if k != "slices_skipped"}}


# ---------- 純函式計分 ----------

def _entry_text(d: dict) -> str:
    parts = [str(d.get("summary") or ""), str(d.get("ledger_line") or "")]
    for x in d.get("decisions") or []:
        if isinstance(x, dict):
            parts.append(str(x.get("text") or ""))
    for x in d.get("invariants") or []:
        parts.append(x.get("text", "") if isinstance(x, dict) else str(x))
    return " ".join(parts).lower()


def _retires(d: dict) -> set[str]:
    return {str(s.get("retires_id") or "").strip().upper()
            for s in (d.get("supersedes") or []) if isinstance(s, dict)
            and str(s.get("retires_id") or "").strip()}


def score_distill(expected: dict, draft: dict, info: dict,
                  changed: list[str]) -> tuple[list[tuple[str, float, str]], float]:
    """回 ([(名稱, 分數, 說明)], 綜合分)。純函式 —— pytest 直接吃它。"""
    out: list[tuple[str, float, str]] = []

    want_intent = bool(expected.get("intent_stated", True))
    got_intent = bool(draft.get("intent_stated", True))
    out.append(("intent_ok", float(got_intent == want_intent),
                f"expected {want_intent}, got {got_intent}"))

    forbidden = [w.lower() for w in (expected.get("forbidden_mention") or [])]
    if forbidden:
        text = _entry_text(draft)
        hit = [w for w in forbidden if w in text]
        out.append(("no_fabrication", float(not hit),
                    "fabricated rationale: " + ", ".join(hit) if hit else "clean"))

    want_ret = {str(expected["retires_id"]).upper()} if expected.get("retires_id") \
        else set()
    got_ret = _retires(draft)
    out.append(("retires_ok", float(got_ret == want_ret),
                f"expected {sorted(want_ret) or 'none'}, got {sorted(got_ret) or 'none'}"))

    stripped = int(info.get("evidence_stripped") or 0)
    dropped = len(info.get("dropped") or [])
    n_dec = len(draft.get("decisions") or [])
    out.append(("evidence_ok", float(stripped == 0 and dropped == 0),
                f"{n_dec} decision(s) kept; {stripped} evidence item(s) stripped, "
                f"{dropped} decision(s) dropped"))

    words = [w.lower() for w in (expected.get("must_mention") or [])]
    if words:
        text = _entry_text(draft)
        hits = [w for w in words if w in text]
        out.append(("must_mention", len(hits) / len(words),
                    f"{len(hits)}/{len(words)}: missing "
                    + (", ".join(w for w in words if w not in hits) or "none")))

    ent = str(draft.get("primary_entity") or "")
    want_ent = expected.get("primary_entity")
    ok = not info.get("primary_fixed") and bool(ent)
    if ok and want_ent:
        ok = match_path(ent, want_ent)
    out.append(("primary_ok", float(ok),
                f"got {ent or '(none)'}"
                + ("（fallback 介入）" if info.get("primary_fixed") else "")))

    if expected.get("forbid_invariants"):
        n_inv = len(draft.get("invariants") or [])
        out.append(("no_hallucination", float(n_inv == 0),
                    f"{n_inv} invariant(s) distilled from material that "
                    "supports none"))

    composite = sum(v for _, v, _ in out) / len(out)
    return out, composite


def scorer(*, input, output, expected_output, **kwargs):
    draft = (output or {}).get("draft") or {}
    info = (output or {}).get("info") or {}
    ana = parse_diff((input or {}).get("diff") or "")
    rows, composite = score_distill(expected_output or {}, draft, info,
                                    [fc.path for fc in ana.files])
    _scores.append(composite)
    return [Evaluation(name=n, value=v, comment=c) for n, v, c in rows] \
        + [Evaluation(name="distill_score", value=composite)]


def main() -> None:
    run_name = sys.argv[1] if len(sys.argv) > 1 else "distill-baseline"
    lf = get_client()
    dataset = lf.get_dataset(DATASET)
    items = list(dataset.items)
    if not items:
        print(f"dataset '{DATASET}' 是空的 —— 先跑 python -m eval.seed_langfuse")
        sys.exit(2)
    try:
        prompt_version = lf.get_prompt(DISTILLER_PROMPT_NAME).version
    except Exception:
        prompt_version = None

    dataset.run_experiment(
        name=run_name, run_name=run_name, task=task, evaluators=[scorer],
        max_concurrency=2,
        metadata={"prompt": DISTILLER_PROMPT_NAME,
                  "prompt_version": prompt_version,
                  "llm_model": settings().llm.model},
    )
    lf.flush()

    avg = sum(_scores) / len(_scores) if _scores else 0.0
    print(f"run '{run_name}'：{len(_scores)} 題｜distill_score 平均 {avg:.2f}")
    print(f"  配置：prompt {DISTILLER_PROMPT_NAME} v{prompt_version} × "
          f"{settings().llm.model}")
    print(f"→ UI：Datasets → {DATASET} → Runs 看逐題分數與 trace")

    want = settings().eval.min_distill_score
    if want is not None and avg < want:
        print(f"CI gate 擋下：distill_score {avg:.2f} < 門檻 {want}")
        sys.exit(1)


if __name__ == "__main__":
    main()
