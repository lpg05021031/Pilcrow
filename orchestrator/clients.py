"""Orchestrator 對外部系統的呼叫層。

S1 起 Azure DevOps 的讀寫是行程內的函式（orchestrator/adapters/ado.py），
這裡只負責兩件事：(1) 用 asyncio.to_thread 把同步的 httpx 呼叫移出 event loop
（抓百檔 diff 是秒到分鐘級，不能卡住 SSE 旁白）；(2) 疊 @observe，瀑布圖裡
「抓 diff 花多久、發評論成不成功」各自成層。

函式名與簽名跟 ①②③ 時代一模一樣：pipeline / knowledge / app 一行沒改，
tests 的 monkeypatch 也照舊。

④ code graph 仍是外部行程（code-review-graph serve --http），走 fastmcp Client。
"""
from __future__ import annotations

import asyncio
import json

from langfuse import observe

from orchestrator.adapters import ado
from shared.settings import settings


# ---------- Azure DevOps（行程內） ----------

@observe()
async def fetch_pr_metadata(pr_url: str) -> dict:
    return await asyncio.to_thread(ado.get_pr_metadata, pr_url)


@observe()
async def fetch_pr_diff(pr_url: str) -> str:
    return await asyncio.to_thread(ado.get_pr_diff, pr_url)


@observe()
async def post_review_comment(pr_url: str, markdown: str) -> dict:
    return await asyncio.to_thread(ado.post_review_comment, pr_url, markdown)


@observe()
async def post_knowledge_draft(pr_url: str, markdown: str,
                               thread_id: int | None = None) -> dict:
    """thread_id 給定 = 回覆進既有 thread（重蒸的更新稿），不開新 thread。"""
    return await asyncio.to_thread(ado.post_knowledge_draft, pr_url, markdown, thread_id)


# ---------- ④ code graph（選配；外部 MCP server） ----------
# 裝好後先 list tools 確認名稱與參數，再設 GRAPH_TOOL_BLAST /
# GRAPH_TOOL_BLAST_ARGS（JSON 樣板，$files / $repo 會被代入）。
# GRAPH_TOOLS_URL 未設 = 整個 graph 功能停用，呼叫端全部優雅降級。


async def _mcp_call(server_url: str, tool: str, args: dict):
    """呼叫遠端 MCP tool 並把結果攤平成 Python 物件。"""
    from fastmcp import Client
    async with Client(server_url) as c:
        res = await c.call_tool(tool, args)
    data = getattr(res, "data", None)          # fastmcp 2.x：優先用結構化 data
    if data is not None:
        return data
    for block in getattr(res, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return None


@observe()
async def graph_call(tool: str, args: dict):
    url = settings().graph.tools_url
    if not url:
        return None
    return await _mcp_call(url, tool, args)


async def graph_blast(files: list[str], repo: str | None = None):
    """受影響範圍摘要。工具名/參數樣板由 env 決定（不同 graph 工具可換皮）。"""
    g = settings().graph
    tool = g.tool_blast
    if not tool:
        return None
    template = g.tool_blast_args
    if "$repo_dir" in template and not (repo or "").strip():
        return None                      # 樣板要 repo、呼叫端沒給 → 靜默跳過
    rendered = (template
                .replace("$files", json.dumps(files))
                .replace("$repo_dir", (repo or "").strip().lower())
                .replace("$repo", json.dumps((repo or "").strip().lower())))
    try:
        args = json.loads(rendered)
    except json.JSONDecodeError:
        return None
    return await graph_call(tool, args)
