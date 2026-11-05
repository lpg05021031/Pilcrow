"""ledger —— 控制台 Knowledge 頁的讀寫層：看帳本在等誰、替驗收窗下裁決。

讀：_pending/（蒸餾稿，等 PR 終態）、_cycle/（暫存稿，等 release→master 驗收 PR 收口）、
    _state/cycle.json（哪些驗收 PR 開了窗、列過誰、收口了沒）、digest 預覽（純渲染，零 LLM）。
寫：**只寫一個檔** `_state/console-verdicts.json`：
    {repo: {"reject": {token: {"by", "ts"}}, "keep": {ID: {"by", "ts"}}}}
    harvester 收口（與驗收窗內收 reject）時把它跟 PR 留言裡的 `reject …` / `keep …`
    合在一起看，語意完全相同（實體名 / PR12 / PR12-I1 三種粒度、keep 否決拔除），
    用掉之後清掉該 repo 的那一份。控制台是第二個「人裁」的入口，不是第二套規則。

這裡不碰 git、不碰 ADO、不呼叫 LLM；harvester 仍是唯一 commit 者。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from orchestrator.knowledge import assign_ids, decision_line, inv_id, inv_text, write_json_atomic
from shared.settings import settings

VERDICTS = "console-verdicts.json"


def _root() -> Path | None:
    r = settings().knowledge.root
    return r if r is not None and r.is_dir() else None


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _draft_summary(d: dict, pr_id: int) -> dict[str, Any]:
    d = dict(d or {})
    assign_ids(d, pr_id)
    return {
        "primary_entity": d.get("primary_entity", ""),
        "ledger_line": (d.get("ledger_line") or d.get("summary") or "").strip(),
        "decisions": [{"id": (x.get("id") or ""), "text": decision_line(x, pr_id)}
                      for x in d.get("decisions") or [] if isinstance(x, dict)],
        "invariants": [{"id": inv_id(x), "text": inv_text(x)} for x in d.get("invariants") or [] if inv_text(x)],
        "supersedes": d.get("supersedes") or [],
        "intent": d.get("intent", ""),
    }


# ---------- 讀 ----------

def pending() -> list[dict[str, Any]]:
    root = _root()
    if root is None:
        return []
    out = []
    for f in sorted((root / "_pending").rglob("pr-*.json")) if (root / "_pending").is_dir() else []:
        info = _load(f)
        if not info:
            out.append({"file": f.name, "repo": f.parent.name, "broken": True})
            continue
        pid = int(info.get("pr_id") or 0)
        out.append({
            "file": f.name, "repo": info.get("repo") or f.parent.name, "pr_id": pid,
            "pr_url": info.get("pr_url", ""), "created": info.get("created", ""),
            "target_branch": info.get("target_branch", ""), "author": info.get("author", ""),
            "posted": bool(info.get("posted")), "dry_run": bool(info.get("dry_run")),
            "post_failed": bool(info.get("post_failed")), "thread_id": info.get("thread_id"),
            "redistilled": bool(info.get("redistilled")),
            "redistill_attempts": (info.get("redistill_requested") or {}).get("attempts", 0),
            "contradicted": info.get("contradicted") or [],
            "dropped": info.get("distill_dropped") or [], "slices_skipped": info.get("distill_slices_skipped") or [],
            "draft": _draft_summary(info.get("draft") or {}, pid),
        })
    return out


def staged() -> list[dict[str, Any]]:
    root = _root()
    if root is None:
        return []
    out = []
    for f in sorted((root / "_cycle").rglob("pr-*.json")) if (root / "_cycle").is_dir() else []:
        it = _load(f)
        if not it:
            continue
        pid = int(it.get("pr_id") or 0)
        out.append({
            "file": f.name, "repo": it.get("repo") or f.parent.name, "pr_id": pid,
            "pr_url": it.get("pr_url", ""), "target_branch": it.get("target_branch", ""),
            "provenance": it.get("provenance", "silent"), "author_note": it.get("author_note", ""),
            "contradicted": it.get("contradicted") or [], "rejected_ids": it.get("rejected_ids") or [],
            "draft": _draft_summary(it.get("draft") or {}, pid),
            "_raw": it,
        })
    return out


def cycle_state() -> dict[str, Any]:
    root = _root()
    if root is None:
        return {}
    st = _load(root / "_state" / "cycle.json") or {}
    return {k: v for k, v in st.items() if isinstance(v, dict)}


def digest_preview(repo: str) -> str:
    """跟驗收 PR 上那則 digest 同一個渲染，但零 LLM（沒有顧問段）。"""
    from orchestrator import knowledge_digest as digest
    items = [it["_raw"] for it in staged() if it["repo"] == repo]
    if not items:
        return ""
    for it in items:
        assign_ids(it.get("draft") or {}, int(it.get("pr_id") or 0))
    return digest.render(items, None, None, total=len(items))


# ---------- 裁決 ----------

def verdicts() -> dict[str, dict[str, dict[str, dict]]]:
    root = _root()
    if root is None:
        return {}
    return _load(root / "_state" / VERDICTS) or {}


def add_verdict(repo: str, kind: str, token: str, by: str = "console") -> str:
    """kind ∈ reject | keep；token = 實體名 / PR12 / PR12-I1（reject）或 PR12-I1（keep）。"""
    root = _root()
    if root is None:
        return "KNOWLEDGE_ROOT 未設或不存在"
    repo = (repo or "").strip().lower()
    token = (token or "").strip()
    if kind not in ("reject", "keep") or not repo or not token:
        return "缺少 repo / kind / token"
    if kind == "keep" and not token.upper().startswith("PR"):
        return "keep 僅接受條目 ID（例如 PR12-I1）"
    key = token.upper() if token.upper().startswith("PR") else token.lower()
    data = verdicts()
    data.setdefault(repo, {}).setdefault(kind, {})[key] = {"by": by, "ts": time.strftime("%FT%T")}
    write_json_atomic(root / "_state" / VERDICTS, data)
    return f"{kind} {key} 已記錄；下次 harvest 於驗收窗 / 收口時套用"


def remove_verdict(repo: str, kind: str, token: str) -> str:
    root = _root()
    if root is None:
        return "KNOWLEDGE_ROOT 未設或不存在"
    data = verdicts()
    repo = (repo or "").strip().lower()
    key = token.upper() if token.upper().startswith("PR") else token.lower()
    if data.get(repo, {}).get(kind, {}).pop(key, None) is None:
        return "無此裁決"
    write_json_atomic(root / "_state" / VERDICTS, data)
    return f"{kind} {key} 已撤回"


def consume_verdicts(root: Path, repo: str) -> tuple[set[str], set[str]]:
    """harvester 用：取出並清掉該 repo 的裁決。回 (reject tokens, keep ids)。"""
    p = root / "_state" / VERDICTS
    data = _load(p) or {}
    mine = data.pop(repo.lower(), None) or {}
    if mine:
        write_json_atomic(p, data)
    return (set(k.lower() for k in (mine.get("reject") or {})),
            set(k.upper() for k in (mine.get("keep") or {})))


def peek_verdicts(root: Path, repo: str) -> tuple[set[str], set[str]]:
    """harvester 驗收窗內用（不清掉——收口時才算用掉）。"""
    mine = (_load(root / "_state" / VERDICTS) or {}).get(repo.lower()) or {}
    return (set(k.lower() for k in (mine.get("reject") or {})),
            set(k.upper() for k in (mine.get("keep") or {})))
