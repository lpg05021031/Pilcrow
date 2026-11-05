"""Orchestrator 的核心：run_review —— 也是 eval 的注入口。

run_review(diff) 是純函式風格：餵凍結的 diff 就出結果，不碰 ①②③、
不碰網路上的 PR —— eval 可重現的地基。簽名永遠不動。

REVIEW_PIPELINE 決定肚子裡跑哪條路：
  v1（預設）  單次 LLM 呼叫（原始行為，一鍵回滾用）
  v2          anatomy → static → plan → 平行 reviewer → adversary →
              synthesis → render（見 orchestrator/pipeline.py）

設定全部住 shared/settings.py（REVIEW_PIPELINE 在 settings().orchestrator.pipeline）；
每次 review 的旋鈕快照以 settings().knobs() 進 trace metadata。
"""
from __future__ import annotations

import json
import re

from langfuse import get_client, observe

from orchestrator.llm import complete, prompt_or
from orchestrator.models import Finding, ReviewResult
from orchestrator.rules import load_rules, rules_context
from shared.settings import settings

PROMPT_NAME = "pr-review-system"

# Langfuse 上還沒建 prompt 時的內建後備版；seed_langfuse.py 會把同一份推上去受版控
DEFAULT_SYSTEM_PROMPT = """You are a senior code reviewer. Review the unified diff provided by the user, focusing on four categories:
1. BUG — logic errors, boundary conditions, behavior contradicting intent
2. MISSING-TEST — new or changed behavior without corresponding test changes
3. NAMING — unclear or misleading identifiers
4. SECURITY — credential exposure, injection, unsafe defaults

Rules:
- Write in clear, professional English. Comment only on the changed lines; do not lecture on pre-existing code.
- If nothing significant is found, state that explicitly. Never reply with a bare "LGTM".
- Give file and line for every finding (best effort).

Output format (strict): first the human-readable markdown review, then one fenced JSON block containing the findings array:
```json
[{"rule": "BUG", "file": "src/x.py", "line": 12, "severity": "error", "note": "..."}]
```
Output `[]` when there are no findings."""

_JSON_BLOCK = re.compile(r"```json\s*(\[.*?\])\s*```", re.DOTALL)


def _parse_findings(markdown: str) -> list[Finding]:
    m = None
    for m in _JSON_BLOCK.finditer(markdown):  # 取「最後一個」json 區塊
        pass
    if not m:
        return []
    try:
        raw = json.loads(m.group(1))
        return [Finding(**f) for f in raw if isinstance(f, dict)]
    except Exception:
        return []


def _meta_block(meta: dict) -> str:
    """有料的欄位才組進去 —— eval 只帶 repo 時不會生出空欄位雜訊。"""
    lines: list[str] = []
    if meta.get("title"):
        lines.append(f"Title: {meta['title']}")
    if meta.get("description"):
        lines.append(f"Description: {meta['description']}")
    if meta.get("source_branch") or meta.get("target_branch"):
        lines.append(f"{meta.get('source_branch', '')} → {meta.get('target_branch', '')}")
    return ("\n".join(lines) + "\n\n") if lines else ""


async def _run_v1(diff: str, meta: dict, rules) -> ReviewResult:
    """原始單呼叫路徑（保留 = 一鍵回滾 + eval A/B 的對照組）。"""
    md = await complete(
        prompt_or(PROMPT_NAME, DEFAULT_SYSTEM_PROMPT) + rules_context(rules),
        f"{_meta_block(meta)}Review the following diff:\n\n{diff}",
    )
    return ReviewResult(markdown=md, findings=_parse_findings(md),
                        stats={"pipeline": "v1"})


@observe()
async def run_review(diff: str, metadata: dict | None = None) -> ReviewResult:
    """給一段 diff（可附 PR metadata），回 ReviewResult(markdown + findings)。

    metadata 認得的鍵全是選配：title / description / source_branch /
    target_branch 進 prompt；repo 用來載 per-repo 規則、找本地 clone。
    """
    meta = metadata or {}
    rules = load_rules(meta.get("repo"))
    mode = settings().orchestrator.pipeline

    if mode == "v2":
        from orchestrator.pipeline import run_pipeline
        result = await run_pipeline(diff, meta, rules)
    else:
        result = await _run_v1(diff, meta, rules)

    get_client().update_current_span(
        output={"findings_count": len(result.findings),
                "pipeline": result.stats.get("pipeline", mode)},
        metadata={
            "rules_sha": rules.sha or None,
            "rules_files": rules.files,
            "rules_truncated": rules.truncated,
            "config": settings().knobs(),
            **{k: v for k, v in result.stats.items() if k != "rules_sha"},
        },
    )
    return result
