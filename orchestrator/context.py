"""context（v2 的餵料工程）—— 確定性預抓，不做 agentic tool loop。

刻意的設計決策：reviewer 不拿工具自己逛，而是由 code 先把上下文備好餵進去。
理由：(1) 內部 gateway 的 native tool calling 支援未知（README 已記載這是
LLM-as-judge 的阻礙），確定性預抓不吃這個能力；(2) 可重現 —— 同一份 diff
永遠拿到同一份上下文；(3) 離線 eval 優雅降級（沒有 clone/graph 就少這塊，
不炸）。之後要升級成 agentic loop，再把 ②④ 接給模型即可，介面都備著。

兩個來源：
  A. 本地乾淨 clone（target branch / base 側）：REPOS_ROOT/<repo小寫>/
     —— 取每個 hunk 舊行號附近 ±N 行。分工原則：改了什麼看 diff，
     周邊長什麼樣看 base。
  B. code graph（選配）：GRAPH_TOOLS_URL 設定後，用 clients.graph_blast
     拿受影響範圍摘要。工具名/參數樣板用 env 設定（裝好後 list tools 再填）。

設定：settings().repos（REPOS_ROOT / CONTEXT_LINES / CONTEXT_MAX_CHARS）與 settings().graph。
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.anatomy import Anatomy, FileChange
from shared.settings import settings


def _repo_root(repo: str | None) -> Path | None:
    return settings().repos.root_for(repo)


def _safe(root: Path, rel: str) -> Path | None:
    p = (root / rel.lstrip("/")).resolve()
    return p if (root == p or root in p.parents) else None


def _excerpt(root: Path, fc: FileChange, ctx: int) -> str | None:
    p = _safe(root, fc.path)
    if p is None or not p.is_file():
        return None
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return None
    wanted: set[int] = set()
    for h in fc.hunks:
        lo = max(1, h.old_start - ctx)
        hi = min(len(lines), h.old_start + max(h.old_count, 1) + ctx)
        wanted.update(range(lo, hi + 1))
    if not wanted:
        return None
    out: list[str] = [f"### base（target branch）{fc.path}"]
    prev = None
    for i in sorted(wanted):
        if prev is not None and i != prev + 1:
            out.append("      …")
        out.append(f"{i:>5}  {lines[i - 1]}")
        prev = i
    return "\n".join(out)


def base_context(repo: str | None, ana: Anatomy) -> tuple[str, dict]:
    """回傳 (組好的上下文文字, 統計)。clone 不在 = 空字串 + 註記，不炸。"""
    root = _repo_root(repo)
    stats = {"base_files": 0, "base_available": root is not None}
    if root is None:
        return "", stats
    ctx = settings().repos.context_lines
    budget = settings().repos.context_max_chars
    parts: list[str] = []
    total = 0
    for fc in ana.files:
        ex = _excerpt(root, fc, ctx)
        if not ex:
            continue
        if total + len(ex) > budget:
            parts.append("（base 上下文達預算上限，其餘省略）")
            break
        parts.append(ex)
        total += len(ex)
        stats["base_files"] += 1
    return "\n\n".join(parts), stats


async def graph_context(repo: str | None, ana: Anatomy) -> tuple[str, dict]:
    """選配：code graph 的 blast radius 摘要。任何失敗 = 空字串 + 註記。"""
    stats = {"graph_used": False, "graph_note": ""}
    g = settings().graph
    if not g.tools_url:
        stats["graph_note"] = "GRAPH_TOOLS_URL 未設定"
        return "", stats
    bound = g.bound_repo
    if bound and (repo or "").strip().lower() != bound:
        # graph server 一次只服務一個 repo；別的 repo 寧可沒有 graph，
        # 也不能拿錯的圖出「無跨檔影響」的假證詞
        stats["graph_note"] = f"graph 綁定 repo '{bound}'，本 repo 未附掛"
        return "", stats
    try:
        from orchestrator import clients
        files = [fc.path.lstrip("/") for fc in ana.files]
        out = await clients.graph_blast(files=files, repo=repo)
    except Exception as e:  # graph 是加分項，不是依賴
        stats["graph_note"] = f"graph 呼叫失敗：{type(e).__name__}"
        return "", stats
    if not out:
        stats["graph_note"] = "graph 無回應或工具名未設定"
        return "", stats
    impacted: list[str] = []
    if isinstance(out, dict):        # 例如 get_impact_radius_tool 的回傳
        summary = str(out.get("summary") or "").strip()
        for key in ("impacted_files", "additional_files", "affected_files",
                    "impacted_nodes", "affected_nodes"):
            for item in out.get(key) or []:
                name = (item.get("qualified_name") or item.get("name")
                        or item.get("file_path")
                        ) if isinstance(item, dict) else str(item)
                if name:
                    impacted.append(str(name))
        detail = json.dumps(out, ensure_ascii=False, default=str)
        text = (summary + "\n" + detail) if summary else detail
        if not summary:
            summary = detail
    else:
        text = out if isinstance(out, str) else str(out)
        summary = text.strip()
    budget = settings().repos.context_max_chars
    stats["graph_used"] = True
    stats["graph_summary"] = summary           # 報告：血脈摘要
    stats["graph_impacted"] = list(dict.fromkeys(impacted))[:12]  # 報告：受波及清單
    return "### code graph（受影響範圍）\n" + text[:budget], stats
