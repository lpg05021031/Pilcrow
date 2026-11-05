"""render（v2 第 6 段）—— 專業英文標準報告 v3，markdown 由 code 產、格式恆定。

v3 升級：finding 編號（F1..Fn）全報告交叉引用；At-a-glance 統計行 +
Required actions（blocking 的行動清單）；Review Dimensions 改表格
（Dimension / Verdict / Findings / Assessment，含 Static analysis 合成列）；
Rule Compliance 的違反依據直接引 F 編號；Review Coverage 改成表格。
不變的底線：零發現照樣完整、無變更可解析明講不代表通過、全程無 emoji。
"""
from __future__ import annotations

from orchestrator.models import Finding

_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEV_HDR = [("error", "Blocking — must fix"),
            ("warning", "Non-blocking — should fix"),
            ("info", "Informational")]
_SRC = {"static": "static", "graph": "graph"}
_VERDICT_MARK = {"pass": "Pass", "violation": "Violation",
                 "unchecked": "Not assessed"}


def sort_key(f: Finding) -> tuple:
    return (_ORDER.get(f.severity, 2), -f.confidence)


def _norm(p: str) -> str:
    return (p or "").strip().lstrip("/").lower()


def _cell(text: str, limit: int | None = None) -> str:
    """表格儲存格：拔掉會拆表的字元（裸 | 與換行會拆掉 markdown 表格）。

    limit 預設 None＝不截斷——表格寬度由渲染端自動換行處理，不該在這裡砍內容。
    只有「本來就該短」的欄位（例如科名）才明確傳 limit。
    """
    s = (text or "").replace("|", "/").replace("\n", " ").strip()
    return s[:limit] if limit else s


def _partial(stats: dict) -> bool:
    return bool(stats.get("context_omitted") or stats.get("skipped_notes"))


def _verdict(findings: list[Finding], stats: dict | None = None) -> str:
    st = stats or {}
    if not findings and st.get("reviewer_errors") \
            and not st.get("static_findings"):
        n = st.get("reviewer_errors")
        return (f"**REVIEW INCOMPLETE** — {n} reviewer pass(es) failed and "
                "static analysis reported nothing; this is NOT an approval. "
                "Re-run the review.")
    if any(f.severity == "error" for f in findings):
        return ("**REQUIRES CHANGES** — blocking issues must be resolved "
                "before merge")
    if any(f.severity == "warning" for f in findings):
        return "**APPROVED WITH SUGGESTIONS** — non-blocking issues noted below"
    return "**APPROVED** — no issues requiring action were identified"


def _counts_line(findings: list[Finding]) -> str:
    n = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        n[f.severity] = n.get(f.severity, 0) + 1
    return (f"Blocking {n['error']} · Non-blocking {n['warning']} · "
            f"Informational {n['info']}")


def _required_actions(ordered: list[Finding], ids: dict[int, str]) -> list[str]:
    blocking = [f for f in ordered if f.severity == "error"]
    if not blocking:
        return []
    out = ["**Required actions before merge**:", ""]
    for i, f in enumerate(blocking, 1):
        loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
        action = f.suggestion or f.note
        out.append(f"{i}. {ids[id(f)]} {loc} — {_cell(action)}")
    out.append("")
    return out


def _file_table(stats: dict) -> list[str]:
    table = stats.get("file_table") or []
    if not table:
        return []
    out = ["### Changed Files", "",
           "| File | Lines added | Hunks |", "|---|---:|---:|"]
    for row in table[:15]:
        out.append(f"| `{row['path']}` | {row['added']} | {row['hunks']} |")
    if len(table) > 15:
        out.append(f"| … {len(table) - 15} more file(s) | | |")
    for n in (stats.get("skipped_notes") or [])[:4]:
        out.append(f"\n> Not reviewed: {n}")
    out.append("")
    return out


def _graph_section(stats: dict) -> list[str]:
    if not stats.get("graph_summary"):
        return []
    out = ["### Impact Analysis (Code Graph)", ""]
    out += [f"> {ln}" for ln in stats["graph_summary"].strip().splitlines()[:4]]
    impacted = stats.get("graph_impacted") or []
    if impacted:
        out += ["", "Affected outside this diff:"]
        out += [f"- `{name}`" for name in impacted]
    else:
        out += ["", "**No cross-file impact detected** — the dependency graph "
                "shows no other callers or files affected by these changes."]
    out.append("")
    return out


def _findings_section(ordered: list[Finding], ids: dict[int, str]) -> list[str]:
    out = [f"### Findings ({len(ordered)})", ""]
    for sev, hdr in _SEV_HDR:
        group = [f for f in ordered if f.severity == sev]
        if not group:
            continue
        out.append(f"#### {hdr} ({len(group)})")
        for f in group:
            loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
            tag = f" `[{_SRC[f.source]}]`" if f.source in _SRC else ""
            ref = f" (per {f.rule_ref})" if f.rule_ref else ""
            # 知識引用比照 rule_ref：讀者看得到這條發現站在哪一條既有決策上
            if f.knowledge_ref:
                ref += " (knowledge: " + ", ".join(f.knowledge_ref) + ")"
            note = " ".join(f.note.split())          # 換行/控制字元收斂
            out.append(f"- **{ids[id(f)]} · {f.rule}** {loc}{tag} — "
                       f"{note}{ref}")
            if f.suggestion:
                out.append(f"  Recommendation: {f.suggestion}")
        out.append("")
    return out


def _dimension_table(stats: dict, ordered: list[Finding],
                     ids: dict[int, str]) -> list[str]:
    rows = stats.get("assessments") or []
    if not rows and not stats.get("static_findings"):
        return []
    out = ["### Review Dimensions", "",
           "| Dimension | Verdict | Findings | Assessment |",
           "|---|---|---|---|"]
    static_ids = [ids[id(f)] for f in ordered if f.source == "static"]
    if stats.get("static_findings") is not None:
        verdict = "Issues found" if static_ids else "Clear"
        out.append("| Static analysis (deterministic) | " + verdict + " | "
                   + (", ".join(static_ids) or "—")
                   + " | semgrep / ruff / heuristic checks; findings carry "
                     "confidence 1.0 |")
    for a in rows:
        fids = [ids[id(f)] for f in ordered
                if f.source == "llm" and f.dimension == a["name"]]
        if a.get("assessment") == "Failed to execute":
            verdict = "Failed"
        elif fids or a.get("count"):
            verdict = "Issues found"
        else:
            verdict = "Clear"
        assessment = _cell(a.get("assessment") or "(no assessment provided)")
        out.append(f"| {_cell(a['name'], 80)} | {verdict} | "
                   f"{', '.join(fids) or '—'} | {assessment} |")
    out.append("")
    return out


def _rule_audit_section(stats: dict, ordered: list[Finding],
                        ids: dict[int, str]) -> list[str]:
    rows = stats.get("rule_audit") or []
    if not rows:
        return []
    sha = stats.get("rules_sha") or "?"
    out = [f"### Rule Compliance (rules {sha})", "",
           "| Rule | Status | Basis |", "|---|---|---|"]
    shown = [r for r in rows if r["verdict"] != "unchecked"][:14]
    for r in shown:
        basis = r["evidence"] or "—"
        if r["verdict"] == "violation":
            hit = next((f for f in ordered
                        if _norm(f.rule_ref) == _norm(r["ref"])), None)
            if hit is not None:
                basis = f"{ids[id(hit)]} — {basis}"
        out.append(f"| `{r['ref']}` | {_VERDICT_MARK[r['verdict']]} "
                   f"| {_cell(basis)} |")
    unchecked = sum(1 for r in rows if r["verdict"] == "unchecked")
    if unchecked:
        out.append(f"| … {unchecked} more | Not assessed | Out of scope for "
                   "this change or not covered by a dimension |")
    out.append("")
    return out


def _coverage_table(stats: dict) -> list[str]:
    rows: list[tuple[str, str]] = []
    if stats.get("files"):
        rows.append(("Scope", f"{stats['files']} file(s), "
                              f"{stats.get('added_lines', 0)} added line(s)"))
    static = f"{stats.get('static_findings', 0)} match(es)"
    if stats.get("static_findings", 0) == 0 and stats.get("static_notes"):
        static += " — " + "; ".join(stats["static_notes"][:2])
    rows.append(("Static analysis", static))
    if stats.get("base_available"):
        rows.append(("Base context", f"{stats.get('base_files', 0)} file(s)"))
    rows.append(("Code graph", "enabled" if stats.get("graph_used")
                 else "not attached"))
    if stats.get("knowledge_entries"):
        detail = (f"{stats['knowledge_entries']} entr"
                  + ("y" if stats["knowledge_entries"] == 1 else "ies")
                  + " matched")
        if stats.get("knowledge_invariants_only") or stats.get("knowledge_omitted"):
            # 降級可見：被縮成 invariants-only 或整條沒進去的，攤在報告上
            detail += (f" ({stats.get('knowledge_full', 0)} full, "
                       f"{stats.get('knowledge_invariants_only', 0)} invariants "
                       f"only, {stats.get('knowledge_omitted', 0)} omitted "
                       "— context budget)")
        if stats.get("knowledge_grounded"):
            detail += (f"; {stats['knowledge_grounded']} finding(s) cite a "
                       "ledger entry")
        rows.append(("Knowledge context", detail))
    omitted = stats.get("context_omitted") or []
    if omitted:
        rows.append(("Context budget",
                     f"{len(omitted)} file(s) exceeded reviewer context — "
                     "covered by static analysis only"))
    if stats.get("adversary"):
        rows.append(("Adversarial validation", stats["adversary"]))
    cov = stats.get("coverage") or {}
    if cov:
        rows.append(("Dimension coverage",
                     f"files: {cov.get('files')}; rules: {cov.get('rules')}"))
    if stats.get("dropped_low_conf"):
        rows.append(("Filtered", f"{stats['dropped_low_conf']} low-confidence "
                                 "finding(s) (REVIEW_MIN_CONFIDENCE)"))
    if stats.get("dropped_over_cap"):
        rows.append(("Truncated", f"{stats['dropped_over_cap']} finding(s) "
                                  "over the output cap"))
    if stats.get("reviewer_errors"):
        rows.append(("Warnings", f"{stats['reviewer_errors']} review "
                                 "dimension(s) failed to execute"))
    out = ["### Review Coverage", "", "| Check | Result |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows]
    out.append("")
    return out


def _footer(stats: dict) -> str:
    return ("\n---\n<sub>pipeline v2"
            + (f" · rules {stats['rules_sha']}" if stats.get("rules_sha") else "")
            + "</sub>")


def render_markdown(findings: list[Finding], stats: dict) -> str:
    out: list[str] = ["## Pull Request Review"]

    if not stats.get("files"):
        out.append("\n**No reviewable changes could be extracted from this "
                   "pull request** (empty diff, or all files were skipped). "
                   "This report does **not** constitute approval — "
                   "investigate the retrieval layer first.")
        for n in (stats.get("skipped_notes") or [])[:6]:
            out.append(f"- Skipped: {n}")
        out.append("")
        out += _coverage_table(stats)
        out.append(_footer(stats))
        return "\n".join(out)

    ordered = sorted(findings, key=sort_key)
    ids = {id(f): f"F{i}" for i, f in enumerate(ordered, 1)}

    if stats.get("pr_summary"):
        out.append(f"\n**Summary of Changes**: {stats['pr_summary']}")
    out.append(f"\n**Review Outcome**: {_verdict(ordered, stats)}")
    if _partial(stats):
        out.append("_Partial coverage: some files exceeded the reviewer "
                   "context budget or were skipped at diff level — see the "
                   "Coverage Check table. Static analysis covered all files._")
    if ordered:
        out.append(f"\n{_counts_line(ordered)}")
    out.append("")
    out += _required_actions(ordered, ids)
    out += _file_table(stats)
    out += _graph_section(stats)
    if ordered:
        out += _findings_section(ordered, ids)
    out += _dimension_table(stats, ordered, ids)
    out += _rule_audit_section(stats, ordered, ids)
    out += _coverage_table(stats)
    out.append(_footer(stats))
    return "\n".join(out)
