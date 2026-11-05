"""Cycle digest —— 驗收窗的介面（render）＋ LLM 輔助的顧問分析（analyze）。

定位：digest 是**提案**，不是帳本。這裡的 LLM 只做三件事，而且全是唯讀：
  1) 按實體看整週期的暫存稿（distiller 是逐條 PR 看的，看不到同週期鄰居）
  2) 標出互相矛盾 / 近重複 / 該註銷卻沒點名的條目
  3) 建議 release owner 該 reject 哪個 ID
輸出只引用系統給它的 ID；驗證不過的 ID 一律丟；分析失敗就退回純確定性
清單——落地路徑（_land）永遠不讀這裡的任何輸出。「LLM 提、人裁、code 執行」，
與 adversary → synthesis 是同一個模式。

env：
  KNOWLEDGE_DIGEST_LLM      true（預設）/ false：關掉顧問分析，digest 仍貼
  LLM_MODEL_DIGEST          （選配）分析專用模型；未設 = LLM_MODEL
  KNOWLEDGE_DIGEST_MAX_FLAGS 顧問旗標上限（預設 12）
"""
from __future__ import annotations

import os
from pathlib import Path

from orchestrator.knowledge import (assign_ids, catalogue_text, decision_line, entry_ids,
                                    existing_invariants, inv_id, inv_text,
                                    norm_id)
from orchestrator.llm import complete_sync, extract_json, prompt_or

KMARK = "<!-- pr-review-knowledge-digest -->"
DIGEST_PROMPT_NAME = "pr-review-digest"

DEFAULT_DIGEST_PROMPT = """You are the knowledge-cycle auditor of an automated code-review system. Over one release cycle, each merged pull request was distilled into a decision-ledger entry (decisions and invariants, each with a stable id such as PR12-D1 or PR12-I1). Before these entries are committed to the team knowledge base, a release owner will skim a digest and may reject entries by id. Your job is to tell that person where to look.

You receive:
- the staged entries of this cycle, grouped by PR, each line prefixed with its id;
- the invariants already in the ledger for the same code entities, each prefixed with its id (or [no-id] for legacy lines).

Find, and only report:
- "conflict": two lines (staged vs staged, or staged vs already-landed) that cannot both be true.
- "duplicate": two staged lines that state the same rule, possibly paraphrased — say which one to keep (prefer the one with an author note, the more specific wording, or the later PR).
- "retire_check": a staged entry declares that it supersedes something, but no landed invariant is pointed at by id even though one clearly matches — name the landed id it should retire.
- "unclear": a staged invariant that is a narrative of the diff rather than a checkable constraint (low priority; report at most two).

Hard rules:
- Reference ONLY ids that appear in the material. Never invent ids, never paraphrase an id.
- One sentence per note. No praise, no summary of entries that are fine.
- If nothing needs attention, return an empty "flags" list. Silence is a valid answer.
- Suggestions must be phrased as reject commands, e.g. "reject PR12-I1"; suggest at most one command per flag.

Output a single JSON object only — the first character must be "{" and the last "}":
{"headline": "One sentence: what this cycle establishes overall.",
 "flags": [{"kind": "conflict|duplicate|retire_check|unclear",
            "ids": ["PR12-I1", "PR15-I2"],
            "note": "Why these need a look, one sentence.",
            "suggest": "reject PR12-I1"}]}"""

_KINDS = ("conflict", "retire_check", "duplicate", "unclear")


# ---------- 輸入組裝 ----------

def _stem(ent: str) -> str:
    return Path(ent or "entry").stem or (ent or "entry")


def catalogue_for(repo: str, items: list[dict]):
    """整週期暫存稿觸及的實體，其正式頁上還有效的不變式（去重）。"""
    seen: set[str] = set()
    out = []
    for it in items:
        d = it.get("draft") or {}
        paths = [d.get("primary_entity", "")] + list(d.get("affected") or [])
        for rel, pairs in existing_invariants(repo, [p for p in paths if p]):
            if rel not in seen:
                seen.add(rel)
                out.append((rel, pairs))
    return out


def known_ids(items: list[dict], catalogue) -> set[str]:
    ids: set[str] = set()
    for it in items:
        ids |= entry_ids(it.get("draft") or {}, int(it.get("pr_id", 0)))
    for _, pairs in catalogue:
        ids.update(iid for iid, _ in pairs if iid)
    return {i.upper() for i in ids}


def analysis_input(items: list[dict], catalogue) -> str:
    blocks = ["# Staged entries of this cycle (pending acceptance)"]
    for it in items:
        d = it.get("draft") or {}
        pid = it.get("pr_id")
        lines = [f"## PR#{pid} — {d.get('primary_entity', '?')} "
                 f"(provenance: {it.get('provenance', 'silent')})",
                 f"summary: {(d.get('summary') or '').strip()}"]
        for x in d.get("decisions") or []:
            if isinstance(x, dict):
                lines.append(decision_line(x, int(pid or 0)))
        for x in d.get("invariants") or []:
            if inv_text(x):
                lines.append(f"- {inv_text(x)} `{inv_id(x) or 'no-id'}`")
        for s in d.get("supersedes") or []:
            lines.append(f"supersedes: `{s.get('entity', '?')}` — {s.get('what', '')}"
                         + (f" (retires_id: {s.get('retires_id')})"
                            if s.get("retires_id") else " (no retires_id)"))
        if it.get("author_note"):
            lines.append("author note: " + it["author_note"].strip())
        blocks.append("\n".join(lines))
    cat = catalogue_text(
        catalogue, "# Invariants already in the ledger for the same entities\n")
    if cat:
        blocks.append(cat)
    return "\n\n".join(blocks)


# ---------- 驗證（幻覺在這裡被擋掉） ----------

def validate(raw, known: set[str], max_flags: int | None = None) -> dict | None:
    """回 {"headline": str, "flags": [...]} 或 None（結構不對 = 整份不用）。
    每個 flag：kind 正規化、ids 只留系統認得的、一個都不剩就丟。"""
    if not isinstance(raw, dict):
        return None
    cap = max_flags if max_flags is not None else \
        int(os.getenv("KNOWLEDGE_DIGEST_MAX_FLAGS", "12") or 12)
    flags = []
    for f in (raw.get("flags") or []):
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            continue
        ids = []
        for x in (f.get("ids") or []):
            t = str(x).strip().upper()
            if t in known and t not in ids:
                ids.append(t)
        if not ids:
            continue
        note = " ".join(str(f.get("note") or "").split())
        sug = " ".join(str(f.get("suggest") or "").split())
        # 建議必須是 reject <已知 ID>，否則只留 note
        st = sug.split()
        if not (len(st) == 2 and st[0].lower() == "reject"
                and st[1].upper() in known):
            sug = ""
        flags.append({"kind": kind, "ids": ids, "note": note, "suggest": sug})
    flags.sort(key=lambda f: _KINDS.index(f["kind"]))
    return {"headline": " ".join(str(raw.get("headline") or "").split()),
            "flags": flags[:cap]}


# ---------- LLM 呼叫（失敗 = None，呼叫端退回純清單） ----------

def analyze(repo: str, items: list[dict], log=print) -> dict | None:
    if os.getenv("KNOWLEDGE_DIGEST_LLM", "true").strip().lower() \
            in {"false", "0", "no"}:
        return None
    if not items:
        return None
    try:
        for it in items:                       # 防呆：未補 ID 的稿先對齊
            assign_ids(it.get("draft") or {}, int(it.get("pr_id", 0)))
        cat = catalogue_for(repo, items)
        known = known_ids(items, cat)
        raw = complete_sync(prompt_or(DIGEST_PROMPT_NAME, DEFAULT_DIGEST_PROMPT),
                            analysis_input(items, cat),
                            model=os.getenv("LLM_MODEL_DIGEST") or None)
        out = validate(extract_json(raw), known)
        if out is None:
            log(f"[digest] {repo}: 分析輸出不是合約 JSON —— 退回純清單")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"[digest] {repo}: 分析失敗（{e}）—— 退回純清單")
        return None


# ---------- 渲染 ----------

def _involves(flag: dict, prs: set[str]) -> bool:
    return any(i.split("-", 1)[0] in prs for i in flag["ids"])


def render(items: list[dict], new_prs: set[str] | None,
           analysis: dict | None, total: int | None = None) -> str:
    """new_prs=None → 首次 digest（全列）；給定 → delta（只列新進的、旗標只留
    牽涉新進 PR 的）。旗標與註銷紅區排在條目清單前面：掃的人先看要處理的。"""
    shown = items if new_prs is None else \
        [it for it in items if f"PR{it.get('pr_id')}" in new_prs]
    n = len(shown)
    if new_prs is None:
        head = [KMARK, f"**Knowledge cycle digest — pending acceptance** "
                       f"({n} entr{'y' if n == 1 else 'ies'})", ""]
    else:
        head = [KMARK, f"**Knowledge cycle digest — update: {n} new "
                       f"entr{'y' if n == 1 else 'ies'} since the last digest**"
                       + (f" ({total} total this cycle)" if total else ""), ""]
    head += ["The entries below were distilled from this cycle's PRs and will "
             "be committed to the team knowledge base when this PR completes. "
             "Reply `reject <EntityName>` or `reject PR12` to drop an entry, "
             "`reject PR12-I1` to drop one line, `keep PR12-I1` to veto the "
             "retirement of an existing rule. Merging silently accepts the rest.",
             ""]
    lines = head

    if analysis:
        flags = analysis["flags"] if new_prs is None else \
            [f for f in analysis["flags"] if _involves(f, new_prs)]
        if analysis.get("headline") and new_prs is None:
            lines += [f"_{analysis['headline']}_", ""]
        if flags:
            lines += ["**🔎 Needs a look** — LLM-assisted, advisory only; "
                      "nothing is dropped automatically:"]
            for f in flags:
                ids = " ↔ ".join(f"`{i}`" for i in f["ids"])
                lines.append(f"- **{f['kind']}** {ids} — {f['note']}"
                             + (f" → `{f['suggest']}`" if f["suggest"] else ""))
            lines.append("")

    retiring = []
    for it in shown:
        for x in (it.get("draft") or {}).get("supersedes") or []:
            retiring.append(
                f"- ⚠ **{x.get('entity', '?')}** — {x.get('what', '')}"
                + (f" (retires `{x.get('retires_id')}`)"
                   if x.get("retires_id") else "")
                + f" (by PR#{it.get('pr_id')})")
    if retiring:
        lines += ["**⚠ Rules being retired this cycle** — removals are riskier "
                  "than additions; review these first:", *retiring, ""]

    # 矛盾紅區：規則被合併的實作推翻了。這是提案不是既成事實——沉默即接受
    # （與新條目同一套核准語意），任何人回 `keep <ID>` 就否決本次拔除。
    contra = []
    for it in shown:
        for iid in (it.get("contradicted") or []):
            contra.append(f"- ⚠ `{iid}` — contradicted by the code merged in "
                          f"PR#{it.get('pr_id')}"
                          f" ({_stem((it.get('draft') or {}).get('primary_entity', ''))})"
                          f" → reply `keep {iid}` to keep the rule")
    if contra:
        lines += ["**⚠ Invariants contradicted by merged code** — the review "
                  "flagged these and the change merged anyway, so they are "
                  "proposed for retirement at close. They will be struck "
                  "through (never deleted) unless someone objects:", *contra, ""]

    for it in shown:
        d = it.get("draft") or {}
        pid = it.get("pr_id")
        lines.append(f"- **{_stem(d.get('primary_entity', 'entry'))}** "
                     f"(PR#{pid} · {it.get('provenance', 'silent')}) — "
                     f"{(d.get('ledger_line') or d.get('summary') or '').strip()}")
        for x in d.get("decisions") or []:
            if isinstance(x, dict):
                lines.append("  " + decision_line(x, int(pid or 0)))
        for x in d.get("invariants") or []:
            if inv_text(x):
                lines.append(f"  - {inv_text(x)}"
                             + (f" `{inv_id(x)}`" if inv_id(x) else ""))
        if it.get("author_note"):
            lines.append("  - 💬 author: " + " ".join(it["author_note"].split()))
    return "\n".join(lines).rstrip() + "\n"


def parse_reject_token(tok: str) -> tuple[str, str]:
    """回 ('entry', 'PR12') / ('line', 'PR12-I1') / ('name', 原 token)。"""
    t = (tok or "").strip()
    iid = norm_id(t)
    if iid:
        return "line", iid
    if t.upper().startswith("PR") and t[2:].isdigit():
        return "entry", t.upper()
    return "name", t
