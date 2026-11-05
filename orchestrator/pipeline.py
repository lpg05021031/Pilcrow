"""v2 pipeline —— plan → coverage gate → parallel reviewers → adversary → synthesis。

分工鐵律（PR-AF 的核心洞見）：能確定性計算的絕不給 LLM。
  anatomy / static / coverage gate / dedupe / 乘數 / 門檻 / render = code
  規劃面向與假說 / 找問題 / 挑戰問題                              = LLM
每個 LLM 階段都是「JSON + 驗證 + fallback」：任何故障是降級、不是炸掉。

本版三個升級（U1–U3）：
  U1 厚 brief：planner 對每個面向產「待驗證假說 + 指派規則」，reviewer 收到
     的是任務書（假說逐條驗、只帶自己該管的規則內容 = targeted context）。
  U2 覆蓋保證（純 code）：plan 後檢查「每個變更檔 ≥ 一個面向」「每條 md 規則
     ≥ 一個面向」，孤兒自動補一個 Coverage Sweep 面向；stats["coverage"] 記帳。
  U3 adversary 精緻化：分塊審（≤ ADVERSARY_CHUNK 條/次，防注意力稀釋）、判決
     必附行號證據與挑戰編號、可用 LLM_MODEL_ADVERSARY 換一顆腦去自我同意偏差。

旋鈕（全進 env、全進 trace metadata）：
  REVIEW_MAX_DIMENSIONS=8    planner 最多開幾科（sweep 可額外 +1，保證優先於上限）
  REVIEW_MAX_PARALLEL=6      reviewer 併發
  REVIEW_LLM_BASE_CONF=0.7   LLM finding 的基礎信心
  REVIEW_MIN_CONFIDENCE=0.55 低於此分的 finding 被過濾
  REVIEW_MAX_FINDINGS=30     輸出上限
  REVIEW_ADVERSARY=true      關掉 = 跳過對抗段（A/B 用）
  ADVERSARY_CHUNK=6          adversary 每次呼叫最多審幾條
  LLM_MODEL_ADVERSARY        （選配）adversary 專用模型；未設 = 同 LLM_MODEL
  REVIEWER_RULES_MAX_CHARS=20000  每個 reviewer 收到的規則文字上限
  REVIEWER_SLICES_MAX_CHARS=30000 每一科的切片總預算（超過的檔降級 static-only）
  REVIEWER_FILE_SLICE_MAX_CHARS=6000 單一檔案的切片上限（超過就檔內截斷）
"""
from __future__ import annotations

import asyncio
import os

from langfuse import observe
from pydantic import BaseModel

from orchestrator import context as ctx
from orchestrator import knowledge
from orchestrator.anatomy import (Anatomy, line_in_view, line_near,
                                 match_path, numbered_slice, parse_diff)
from orchestrator.llm import complete, extract_json, prompt_or
from orchestrator.models import Finding, ReviewResult
from orchestrator.progress import note
from orchestrator.render import render_markdown, sort_key
from orchestrator.rules import RulesBundle
from orchestrator.static_pass import run_static

# ---------- 受版控 prompts（Langfuse 優先，內建為後備；seed_langfuse 會推上去） ----------

PLANNER_PROMPT_NAME = "pr-review-planner"
REVIEWER_PROMPT_NAME = "pr-review-reviewer"
ADVERSARY_PROMPT_NAME = "pr-review-adversary"

DEFAULT_PLANNER_PROMPT = """You are the review planner of an automated pull-request review system. Your output is the investigation brief that individual reviewers will execute, so be specific: a vague plan produces a vague review.

Given the structural summary of a pull request, decide which review dimensions this specific PR warrants. Derive the dimensions from the actual risk profile of the change — for example: database access warrants injection and transactional-integrity scrutiny; public API changes warrant input-validation and backward-compatibility scrutiny; pure refactors warrant behavior-preservation and consistency scrutiny. Do not apply a generic checklist.

Requirements:
- Define 2 to {max_dims} dimensions.
- Taken together, the dimensions must give every category an owner: behavioral correctness (BUG), security (SECURITY), test adequacy (MISSING-TEST), naming/readability (NAMING/STYLE), and performance/resource efficiency (PERF). Fold these lenses into related dimensions or add one — do not leave a category unowned.
- For each dimension provide:
  - "focus": precisely what to investigate and why it matters for this PR.
  - "hypotheses": 2 to 4 concrete, testable suspicions about what could be wrong in THIS change (e.g. "deleteByName builds SQL from the raw name argument", "the new expiry check may treat the boundary instant incorrectly"). Reviewers will verify each one.
  - "files": target files, using the paths that appear in the diff.
  - "rules": the team-rule file paths (from the provided rule index) this dimension is responsible for checking. Every rule plausibly relevant to this change must be assigned to at least one dimension.

Output JSON only — no prose, no code fences:
{"summary": "One or two sentences describing what this PR does, for the report header.",
 "dimensions": [{"name": "Dimension name", "focus": "...", "hypotheses": ["...", "..."], "files": ["/src/x.py"], "rules": ["rules/common/x.md"]}]}"""

DEFAULT_REVIEWER_PROMPT = """You are a senior software engineer performing a focused code review. You are responsible for exactly one review dimension and you have been handed an investigation brief; execute it exhaustively and ignore concerns outside it.

Ground rules:
- Work through every listed hypothesis explicitly. Your "assessment" must state the outcome of each hypothesis (confirmed as a finding, or refuted and why). Hypotheses are leads, not limits — also report any defect within your dimension that no hypothesis anticipated.
- Review only the changed lines shown in the annotated diff. Base-branch context is provided solely for understanding; do not raise issues about pre-existing code.
- Report the line number shown in the left-hand annotation of the diff slice (these are true new-file line numbers).
- Use uppercase category codes: BUG / SECURITY / MISSING-TEST / NAMING / STYLE / PERF.
- Severity calibration: "error" = would cause incorrect behavior, data loss, or a security exposure — merging should be blocked; "warning" = a real defect or risk that should be addressed but need not block the merge; "info" = advisory.
- MISSING-TEST is decided within the diff: if this diff adds or changes behavior (new or modified methods, endpoints, or logic) and no test file is touched in the same diff, report MISSING-TEST (severity: warning). Attach it to the changed file carrying the main new behavior; one finding is enough — do not repeat it per file. Do not require proof that no tests exist elsewhere. Pure refactors, renames, or formatting-only changes with no behavior change do not warrant it.
- Category boundaries: NAMING is for identifiers whose name misstates their type, polarity, or content (e.g. a Map named accountList, a double-negative boolean method); STYLE is for formatting and layout only.
- Be precise and factual. Every finding must be supported by the code shown. If you are not confident a finding is real, omit it — false positives erode trust in the review.
- Where practical, include a concrete remediation in "suggestion" (one or two sentences; short code is acceptable).
- Team rules: the rules assigned to this dimension are attached. For each assigned rule, take an explicit position. If the change violates it, raise a finding referencing it and add a "violation" entry to rule_checks. If you verified compliance, add a "pass" entry with one sentence of evidence (what you checked and why it is satisfied).
- Team knowledge: a "Knowledge" block may be attached, carrying decisions and invariants distilled from earlier pull requests. Each line ends with an id such as `PR12-I1`. If a finding rests on one of those invariants, list that id in "knowledge_ref" (a list — a finding may stand on two). Cite only ids that appear in the attached block; an id that is not there is dropped. Knowledge entries are leads, not law: verify each against the diff before you rely on it.
- Write all output in clear, professional English.

Output a single JSON object only. The first character of your reply must be "{" and the last must be "}". Never output a bare array. When there are no findings, output "findings": [] and still provide "assessment".
{"assessment": "Hypothesis-by-hypothesis outcome and overall conclusion", "findings": [{"rule": "BUG", "file": "/src/x.py", "line": 12, "severity": "error|warning|info", "note": "The defect and its consequence", "suggestion": "Concrete fix", "rule_ref": "", "knowledge_ref": []}], "rule_checks": [{"rule_ref": "rules/common/x.md", "verdict": "pass|violation", "evidence": "One sentence of evidence"}]}"""

DEFAULT_ADVERSARY_PROMPT = """You are the adversarial validator of a code-review pipeline. Other reviewers proposed the findings below against the annotated diff. Attack every finding and keep only what survives scrutiny.

Challenge each finding on:
1. Pre-existence — is the issue visible only in base context rather than in the added lines? If so, it is out of scope.
2. Convention — is this an established project pattern rather than a defect?
3. Severity inflation — does the stated severity match the actual consequence?
4. Substantiation — does the cited code actually support the claim?

Verdicts: "confirmed" only when the finding is clearly real and correctly scoped; "rejected" when it fails any challenge above; otherwise "uncertain". Be strict — confirming a false positive is worse than marking it uncertain — but every "rejected" must cite the specific annotated line your reason relies on and name which challenge (1-4) it failed; if you cannot point to a line, use "uncertain" instead.

Output a JSON array only — no prose. "id" must match the input numbering.
[{"id": 1, "verdict": "confirmed|uncertain|rejected", "challenge": 2, "line": 15, "reason": "One sentence grounded in the cited line"}]"""


class _Dim(BaseModel):
    name: str
    focus: str = ""
    hypotheses: list[str] = []
    files: list[str] = []
    rules: list[str] = []


def _int(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, str(default)))
    except ValueError:
        return default


def _float(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except ValueError:
        return default


def _norm(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


# ---------- U1：targeted rules —— 只給每個 reviewer 它該管的規則 ----------

def _rules_for_dim(rules: RulesBundle, dim: _Dim) -> str:
    if not rules.loaded:
        return ""
    cap = _int("REVIEWER_RULES_MAX_CHARS", 20000)
    picked: list[tuple[str, str]] = []
    for want in dim.rules:
        for rel, body in rules.sections.items():
            if match_path(rel, want):
                picked.append((rel, body))
                break
    if picked:
        body = "\n\n".join(f"## {rel}\n{b}" for rel, b in picked)[:cap]
        head = (f"\n\n# Assigned Team Rules (version {rules.sha}; this dimension "
                "is responsible for these — take an explicit pass/violation "
                "position on each in rule_checks; reference the file path in "
                "rule_ref for rule-based findings)\n\n")
    else:  # planner 沒指派（或 fallback）→ 全量，行為同前版
        body = rules.text[:cap]
        head = (f"\n\n# Team Rules (version {rules.sha}; repo-specific overrides "
                "common on conflict; reference the rule file path in rule_ref, "
                "and record pass/violation positions in rule_checks for rules "
                "relevant to your dimension)\n\n")
    return head + body + ("\n(Note: rules truncated at the length limit)"
                          if len(body) >= cap else "")


# ---------- 階段 2：plan（U1 厚 brief） ----------

@observe(name="plan")
async def _plan(ana: Anatomy, meta: dict, rules: RulesBundle,
                graph_text: str, stats: dict) -> tuple[list[_Dim], str]:
    max_dims = _int("REVIEW_MAX_DIMENSIONS", 8)
    files = [fc.path for fc in ana.files]
    fallback = [
        _Dim(name="Correctness",
             focus="Logic errors, boundary conditions, behavior contradicting intent (BUG)",
             hypotheses=["A changed branch or boundary condition contradicts the stated intent",
                         "A changed expression produces a wrong value on edge inputs"],
             files=files),
        _Dim(name="Security",
             focus="Injection, credential exposure, unsafe defaults (SECURITY)",
             hypotheses=["Changed code builds a query or command from unvalidated input",
                         "Changed code embeds or weakens a credential or trust setting"],
             files=files),
        _Dim(name="Test Coverage",
             focus="New or changed behavior lacking corresponding tests (MISSING-TEST)",
             hypotheses=["This diff adds or changes behavior without touching any test file"],
             files=files),
        _Dim(name="Readability",
             focus="Misleading names, inconsistent style (NAMING/STYLE)",
             hypotheses=["A new identifier misstates its type, polarity, or content"],
             files=files),
    ][:max_dims]

    summary = [f"{ana.stats['files']} file(s) changed, "
               f"{ana.stats['added_lines']} line(s) added"]
    if meta.get("title"):
        summary.append(f"PR title: {meta['title']}")
    if meta.get("description"):
        summary.append(f"Description: {meta['description']}")
    summary.append("Files: " + ", ".join(files[:20]))
    for fc in ana.files[:20]:
        heads = [t.strip() for _, n, t in
                 (fc.hunks[0].lines if fc.hunks else []) if n][:3]
        summary.append(f"- {fc.path} ({len(fc.added)} added lines), e.g.: "
                       + " / ".join(heads))
    if rules.files:
        summary.append("Team rule index: " + ", ".join(rules.files[:30]))
    if graph_text:
        summary.append(graph_text[:1500])

    system = prompt_or(PLANNER_PROMPT_NAME,
                       DEFAULT_PLANNER_PROMPT).replace("{max_dims}", str(max_dims))
    pr_summary = ""
    try:
        raw = await complete(system, "\n".join(summary))
        data = extract_json(raw) or {}
        pr_summary = str(data.get("summary", "")).strip()
        dims = [_Dim(**d) for d in data.get("dimensions", [])
                if isinstance(d, dict)]
        dims = [d for d in dims if d.name.strip()][:max_dims]
    except Exception:
        dims = []
    if len(dims) < 2:
        stats["plan"] = "fallback"
        return fallback, pr_summary
    stats["plan"] = "ok"
    return dims, pr_summary


# ---------- U2：coverage gate（純 code 的覆蓋保證） ----------

def _coverage_gate(dims: list[_Dim], ana: Anatomy,
                   rules: RulesBundle, stats: dict) -> list[_Dim]:
    """保證：每個變更檔、每條 md 規則，至少落在一個面向。孤兒 → Coverage Sweep。
    files 為空的面向視為涵蓋全部檔案（planner 明示的全 PR 面向）。"""
    all_files_dim = any(not d.files for d in dims)
    orphan_files = [] if all_files_dim else [
        fc.path for fc in ana.files
        if not any(match_path(fc.path, w) for d in dims for w in d.files)]
    all_rules_dim = any(not d.rules for d in dims)   # 空 rules = 收全量規則
    assigned_rules = {w for d in dims for w in d.rules}
    orphan_rules = [] if all_rules_dim else [
        rel for rel in rules.files
        if not any(match_path(rel, w) for w in assigned_rules)]

    stats["coverage"] = {
        "files": "full" if not orphan_files else "patched",
        "rules": "full" if (not rules.files or not orphan_rules) else "patched",
        "orphan_files": orphan_files,
        "orphan_rules": orphan_rules,
    }
    if not orphan_files and not orphan_rules:
        return dims

    sweep = _Dim(
        name="Coverage Sweep",
        focus=("Deterministic coverage backstop: no planned dimension claimed "
               "the files and/or team rules listed below. Review them with "
               "the same rigor — nothing merges unexamined."),
        hypotheses=["An unclaimed file contains a defect no dimension looked at",
                    "An unassigned team rule is violated by this change"],
        files=orphan_files or [fc.path for fc in ana.files],
        rules=orphan_rules,
    )
    return dims + [sweep]   # 覆蓋保證優先於 REVIEW_MAX_DIMENSIONS（最多 +1）


# ---------- 階段 3：平行 reviewer ----------

def _slices_for(dim: _Dim, ana: Anatomy) -> tuple[str, list[str]]:
    """回 (切片文, 被預算省略的檔案清單)。省略對模型有註記、對人類進 stats——
    「nothing merges unexamined」的誠實版本是：超預算檔案降級為 static-only。"""
    wanted = {_norm(f) for f in dim.files}
    picked = [fc for fc in ana.files
              if not wanted or any(match_path(fc.path, w) for w in wanted)]
    if not picked:
        picked = ana.files
    budget = _int("REVIEWER_SLICES_MAX_CHARS", 30000)
    per_file = _int("REVIEWER_FILE_SLICE_MAX_CHARS", 6000)
    parts, used, skipped = [], 0, []
    for fc in picked:
        chunk = numbered_slice(fc, max_chars=per_file)
        if used + len(chunk) > budget and parts:
            skipped.append(fc.path)
            continue
        parts.append(chunk)
        used += len(chunk)
    if skipped:
        parts.append(f"({len(skipped)} more changed file(s) omitted — this "
                     f"dimension's context budget was reached)")
    return "\n\n".join(parts), skipped


@observe(name="review-dimension")
async def _review_one(dim: _Dim, ana: Anatomy, rules: RulesBundle,
                      base_text: str, graph_text: str):
    system = prompt_or(REVIEWER_PROMPT_NAME, DEFAULT_REVIEWER_PROMPT) \
        + _rules_for_dim(rules, dim)
    brief = [f"# Review dimension: {dim.name}",
             f"Investigative focus: {dim.focus}"]
    if dim.hypotheses:
        brief.append("Hypotheses to test (address each in your assessment):")
        brief += [f"  {i}. {h}" for i, h in enumerate(dim.hypotheses, 1)]
    slices_text, slices_skipped = _slices_for(dim, ana)
    user_parts = ["\n".join(brief),
                  "# Changes (line numbers annotated)", slices_text]
    if base_text:
        user_parts += ["# Base-branch context (for understanding only — "
                       "do not comment on it)", base_text]
    if graph_text:
        user_parts.append(graph_text)
    raw = await complete(system, "\n\n".join(user_parts))
    data = extract_json(raw)
    assessment = ""
    rule_checks: list[dict] = []
    if isinstance(data, dict):                 # 新格式
        assessment = str(data.get("assessment", "")).strip()
        items = data.get("findings", [])
        rule_checks = [c for c in data.get("rule_checks", [])
                       if isinstance(c, dict) and c.get("rule_ref")
                       and c.get("verdict") in ("pass", "violation")]
    elif isinstance(data, list):               # 舊格式（裸陣列）相容
        items = data
    else:
        return "(unparseable reviewer output)", [], [], \
            {"location_dropped": 0, "location_flagged": 0,
             "slices_skipped": slices_skipped}
    base_conf = _float("REVIEW_LLM_BASE_CONF", 0.7)
    out: list[Finding] = []
    loc = {"location_dropped": 0, "location_flagged": 0,
           "slices_skipped": slices_skipped}
    for f in items:
        if not isinstance(f, dict):
            continue
        kr = f.get("knowledge_ref")                   # 型別收斂在建模前做：
        if isinstance(kr, str):                       # 引用格式寫歪不該讓整條
            f["knowledge_ref"] = [kr] if kr else []    # finding 被 pydantic 丟掉
        elif isinstance(kr, list):
            f["knowledge_ref"] = [str(x).strip() for x in kr if str(x).strip()]
        elif kr is not None:
            f.pop("knowledge_ref", None)
        try:
            fin = Finding(**f)
        except Exception:
            continue
        fin.source = "llm"
        fin.dimension = dim.name
        fin.confidence = base_conf
        fin.rule = (fin.rule or "BUG").strip().upper()
        if not any(match_path(fc.path, fin.file) for fc in ana.files):
            loc["location_dropped"] += 1        # 幻覺檔案：明確 drop，不進報告
            continue
        if not line_near(ana, fin.file, fin.line):
            fin.confidence = round(fin.confidence * 0.8, 3)   # 0.7→0.56：過線、帶標記
            fin.note = "[unverified location: line not in added set] " + fin.note
            loc["location_flagged"] += 1
        if fin.note:
            out.append(fin)
    return assessment, out, rule_checks, loc


async def _review_all(dims: list[_Dim], ana: Anatomy, rules: RulesBundle,
                      base_text: str, graph_text: str,
                      stats: dict) -> list[Finding]:
    sem = asyncio.Semaphore(_int("REVIEW_MAX_PARALLEL", 6))

    async def one(d: _Dim):
        async with sem:
            try:
                assessment, out, checks, loc = await _review_one(
                    d, ana, rules, base_text, graph_text)
                for k, v in loc.items():
                    if k == "slices_skipped":
                        merged = set(stats.get("context_omitted") or []) | set(v)
                        stats["context_omitted"] = sorted(merged)   # JSON 友善
                    else:
                        stats[k] = stats.get(k, 0) + v
                await note(f"Reviewing [{d.name}] — done, {len(out)} finding(s)")
                return out, {"name": d.name, "assessment": assessment,
                             "count": len(out)}, checks
            except Exception:
                stats.setdefault("reviewer_errors", 0)
                stats["reviewer_errors"] += 1
                await note(f"Reviewing [{d.name}] — failed, skipped")
                return [], {"name": d.name, "assessment": "Failed to execute",
                            "count": 0}, []

    results = await asyncio.gather(*(one(d) for d in dims))
    stats["assessments"] = [a for _, a, _ in results]     # 依 dims 原順序
    checks: dict[str, dict] = {}                          # violation 蓋過 pass
    for _, _, cs in results:
        for c in cs:
            ref = str(c["rule_ref"]).strip()
            if ref not in checks or c["verdict"] == "violation":
                checks[ref] = {"verdict": c["verdict"],
                               "evidence": str(c.get("evidence", ""))}
    stats["llm_rule_checks"] = checks
    return [f for fs, _, _ in results for f in fs]


# ---------- 階段 4：adversary（U3 分塊 + 行號證據 + 可換腦） ----------

@observe(name="adversary")
async def _adversary(llm_findings: list[Finding], ana: Anatomy,
                     base_text: str, rules: RulesBundle, stats: dict) -> None:
    """就地調整 llm findings 的 confidence。static（確定性）不送審、不動。"""
    if os.getenv("REVIEW_ADVERSARY", "true").strip().lower() in {"false", "0", "no"}:
        stats["adversary"] = "off"
        return
    if not llm_findings:
        stats["adversary"] = "n/a"
        return
    fallback_diff = "\n\n".join(numbered_slice(fc, max_chars=3000)
                                for fc in ana.files)[:12000]
    base_block = ("\n\n# Base-branch context\n" + base_text[:4000]) if base_text else ""

    def _context_for(batch: list[Finding]) -> str:
        # 檢察官至少要看得到被挑戰發現所在的檔 —— 大 PR 不能靠全域截斷碰運氣
        picked, seen = [], set()
        for f in batch:
            for fc in ana.files:
                if fc.path not in seen and match_path(fc.path, f.file):
                    seen.add(fc.path)
                    picked.append(numbered_slice(fc, max_chars=3000))
        text = "\n\n".join(picked)[:15000]
        rule_refs = sorted({f.rule_ref for f in batch if f.rule_ref})
        excerpts = []
        for ref in rule_refs:
            for rel, body in rules.sections.items():
                if (rel == ref or ref.endswith("/" + rel)
                        or rel.endswith("/" + ref)):
                    excerpts.append(f"## {rel}\n{body[:1500]}")
                    break
        if excerpts:
            text += ("\n\n# Referenced team rules (verbatim — a finding that "
                     "cites one of these is grounded in the rule itself; do not "
                     "reject it as 'project convention' without reading it)\n"
                     + "\n\n".join(excerpts)[:4500])
        return text or fallback_diff
    system = prompt_or(ADVERSARY_PROMPT_NAME, DEFAULT_ADVERSARY_PROMPT)
    model = os.getenv("LLM_MODEL_ADVERSARY", "").strip() or None
    chunk = max(1, _int("ADVERSARY_CHUNK", 6))
    chunks = [llm_findings[i:i + chunk]
              for i in range(0, len(llm_findings), chunk)]

    async def judge(batch: list[Finding]) -> list[dict] | None:
        listing = "\n".join(
            f'{i + 1}. [{f.rule}] {f.file}:{f.line} ({f.severity}) {f.note}'
            for i, f in enumerate(batch))
        user = ("# Diff (line numbers annotated)\n" + _context_for(batch)
                + base_block + "\n\n# Findings under challenge\n" + listing)
        try:
            raw = await complete(system, user, model=model)
            data = extract_json(raw)
            assert isinstance(data, list)
        except Exception:
            return None
        slots: list[dict] = [{} for _ in batch]
        for v in data:
            if not isinstance(v, dict):
                continue
            try:
                idx = int(v.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                slots[idx] = v
        return slots

    sem = asyncio.Semaphore(_int("REVIEW_MAX_PARALLEL", 6))

    async def _bounded(b):
        async with sem:                      # 檢察官同樣受併發上限管
            return await judge(b)

    results = await asyncio.gather(*(_bounded(b) for b in chunks))
    confirmed = rejected = degraded = demoted = 0
    for batch, slots in zip(chunks, results):
        if slots is None:
            degraded += 1
            continue                       # 該塊全維持 uncertain（中立）
        for f, v in zip(batch, slots):
            verdict = str(v.get("verdict", "uncertain")).lower()
            if verdict == "confirmed":
                f.confidence = min(0.95, f.confidence * 1.25)
                confirmed += 1
            elif verdict == "rejected":
                # 確定性執法：prompt 說「必引行號+挑戰編號」，這裡驗真。
                # 引不出合法行號的 reject 不配 ×0.35 —— 降為 uncertain。
                ch = str(v.get("challenge", "")).strip()
                line_ok = (line_in_view(ana, f.file, v.get("line"))
                           if ch == "1" else
                           line_near(ana, f.file, v.get("line")))
                if ch in {"1", "2", "3", "4"} and line_ok:
                    f.confidence = round(f.confidence * 0.35, 3)
                    rejected += 1
                else:
                    demoted += 1
    stats["adversary"] = (f"ok({confirmed} confirmed / {rejected} rejected"
                          f" / {len(llm_findings)}; {len(chunks)} chunk(s)"
                          + (f", {demoted} unproven-reject demoted" if demoted else "")
                          + (f", {degraded} degraded" if degraded else "")
                          + (f", model={model}" if model else "") + ")")


# ---------- 階段 5：synthesis（純 code） ----------

def _dedupe(findings: list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: -x.confidence):
        dup = None
        for k in kept:
            if k.rule != f.rule:
                continue
            if k.rule == "MISSING-TEST":       # 全域一條就夠（掛哪個檔不重要）
                dup = k
                break
            if _norm(k.file) != _norm(f.file):
                continue
            if k.line is None or f.line is None:
                if k.line == f.line:
                    dup = k
                    break
            elif abs(k.line - f.line) <= 3:
                dup = k
                break
        if dup is None:
            kept.append(f)
            continue
        if f.knowledge_ref and not dup.knowledge_ref:
            dup.knowledge_ref = f.knowledge_ref   # 去重不該吃掉引用（引用率分子）
        if {dup.source, f.source} == {"static", "llm"}:
            dup.confidence = 1.0
            dup.note = dup.note + " (corroborated: static + LLM)"
            dup.rule_ref = dup.rule_ref or f.rule_ref
    return kept


def _verify_knowledge_refs(findings: list[Finding], stats: dict) -> None:
    """引用率的確定性執法：knowledge_ref ⊆ 本次注入區塊裡真的出現過的 id。

    不在名單的剝掉（不扣分——引錯不代表 finding 是錯的，只代表這條引用不算數）。
    幻覺 id 若留在報告裡，日後沒有人能追回它指的是什麼。
    """
    known = {str(i).upper() for i in (stats.get("knowledge_ids") or [])}
    stats["knowledge_ids_injected"] = len(known)
    stripped = 0
    for f in findings:
        if not f.knowledge_ref:
            continue
        keep, seen = [], set()
        for ref in f.knowledge_ref:
            r = str(ref).strip().upper()
            if r in known and r not in seen:
                seen.add(r)
                keep.append(r)
            else:
                stripped += 1
        f.knowledge_ref = keep
    if stripped:
        stats["knowledge_refs_stripped"] = stripped


@observe(name="synthesis")
def _synthesize(findings: list[Finding], stats: dict) -> list[Finding]:
    merged = _dedupe(findings)
    _verify_knowledge_refs(merged, stats)
    threshold = _float("REVIEW_MIN_CONFIDENCE", 0.55)
    passed = [f for f in merged if f.confidence >= threshold]
    stats["dropped_low_conf"] = len(merged) - len(passed)
    cap = _int("REVIEW_MAX_FINDINGS", 30)
    passed.sort(key=sort_key)
    stats["dropped_over_cap"] = max(0, len(passed) - cap)
    final = passed[:cap]
    # 引用率的分子：最終留在報告裡、且引用有效的條數（線上量尺的上界——
    # 它只證明 reviewer 讀到了知識，不證明 finding 因它而來；下界在 eval 的
    # knowledge_delta）
    stats["knowledge_grounded"] = sum(1 for f in final if f.knowledge_ref)
    return final


def _rule_audit(rules: RulesBundle, findings: list[Finding],
                stats: dict) -> list[dict]:
    """規則稽核表：每條載入的團隊規則一列 —— 違反/通過/未檢核。
    證據優先序：最終發現的違反 > semgrep 機械掃描(0 命中=通過) > LLM 的 pass 表態。"""
    def norm(x: str) -> str:
        return (x or "").strip().lstrip("/").lower()

    violated: dict[str, str] = {}
    for f in findings:
        if f.rule_ref:
            violated.setdefault(norm(f.rule_ref),
                                f"See finding: {f.file}:{f.line} {f.note}")
    llm = {norm(k): v for k, v in (stats.get("llm_rule_checks") or {}).items()}
    rows: list[dict] = []
    for ref in list(stats.get("static_rule_refs") or []) + list(rules.files):
        key = norm(ref)
        if any(r["ref"] == ref for r in rows):
            continue
        if key in violated:
            rows.append({"ref": ref, "verdict": "violation",
                         "evidence": violated[key]})
        elif ref in (stats.get("static_rule_refs") or []):
            rows.append({"ref": ref, "verdict": "pass",
                         "evidence": "Static analysis (semgrep): 0 matches"})
        elif key in llm and llm[key]["verdict"] == "pass":
            rows.append({"ref": ref, "verdict": "pass",
                         "evidence": llm[key]["evidence"]
                         or "Verified by review dimension"})
        else:
            rows.append({"ref": ref, "verdict": "unchecked", "evidence": ""})
    return rows


# ---------- 入口 ----------

async def run_pipeline(diff: str, meta: dict,
                       rules: RulesBundle) -> ReviewResult:
    stats: dict = {"pipeline": "v2", "rules_sha": rules.sha or None}

    ana = parse_diff(diff)
    stats.update(ana.stats)
    stats["skipped_notes"] = ana.skipped_notes
    await note(f"Parsing diff — {ana.stats['files']} file(s), "
               f"{ana.stats['added_lines']} added line(s)"
               + (f", {len(ana.skipped_notes)} skipped"
                  if ana.skipped_notes else ""))

    static_finds, static_notes, static_refs = run_static(ana)
    stats["static_rule_refs"] = static_refs
    stats["static_findings"] = len(static_finds)
    if static_notes:
        stats["static_notes"] = static_notes
    await note(f"Static analysis — {len(static_finds)} finding(s)"
               + (f"; {static_notes[0]}"
                  if static_notes and not static_finds else ""))

    base_text, base_stats = ctx.base_context(meta.get("repo"), ana)
    graph_text, graph_stats = await ctx.graph_context(meta.get("repo"), ana)
    know_text, know_stats = knowledge.load_for(
        meta.get("repo"), [fc.path for fc in ana.files])
    stats.update(base_stats)
    stats.update(graph_stats)
    stats.update(know_stats)
    if know_text:
        graph_text = (graph_text + "\n\n" + know_text) if graph_text else know_text
    await note("Context prefetch — base: "
               + (f"{base_stats['base_files']} file(s)"
                  if base_stats.get("base_available")
                  else "unavailable (no local clone)")
               + ("; code graph: attached"
                  if graph_stats.get("graph_used") else "")
               + (f"; knowledge: {know_stats['knowledge_entries']} entr"
                  + ("y" if know_stats["knowledge_entries"] == 1 else "ies")
                  if know_stats.get("knowledge_entries") else ""))

    dims, pr_summary = await _plan(ana, meta, rules, graph_text, stats)
    dims = _coverage_gate(dims, ana, rules, stats)
    stats["pr_summary"] = pr_summary or (meta.get("title") or "")
    stats["file_table"] = [
        {"path": fc.path, "added": len(fc.added), "hunks": len(fc.hunks)}
        for fc in ana.files]
    stats["dimensions"] = len(dims)
    stats["dimension_names"] = [d.name for d in dims]
    cov = stats["coverage"]
    await note(("Planning" if stats.get("plan") == "ok"
                else "Planning (degraded to fixed dimensions)")
               + f" — {len(dims)} dimensions: "
               + ", ".join(d.name for d in dims))
    await note(f"Coverage gate — files: {cov['files']}; rules: {cov['rules']}"
               + (" (+Coverage Sweep dimension)"
                  if cov["files"] == "patched" or cov["rules"] == "patched"
                  else ""))

    llm_finds = await _review_all(dims, ana, rules, base_text, graph_text, stats)
    stats["llm_findings"] = len(llm_finds)

    await _adversary(llm_finds, ana, base_text, rules, stats)
    await note(f"Adversarial validation — {stats.get('adversary', 'n/a')}")

    final = _synthesize(static_finds + llm_finds, stats)
    stats["final_findings"] = len(final)
    stats["rule_audit"] = _rule_audit(rules, final, stats)
    await note(f"Synthesis — {len(final)} finding(s) retained"
               + (f", {stats['dropped_low_conf']} filtered below confidence"
                  " threshold" if stats.get("dropped_low_conf") else ""))

    return ReviewResult(markdown=render_markdown(final, stats),
                        findings=final, stats=stats)
