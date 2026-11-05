"""Orchestrator → 三個 tool set 的呼叫層。

每個 wrapper 都疊 @observe：瀑布圖裡「抓 diff 花多久、讀 repo 花多久、
發評論成不成功」各自成層 —— 這是唯一接 Langfuse 的服務。

位址來自 settings().tools（ADO_TOOLS_URL 等；未設就由各服務的 PORT 推得）。
"""
from __future__ import annotations

import json

from fastmcp import Client
from langfuse import observe

from shared.settings import settings


async def _call(server_url: str, tool: str, args: dict):
    """呼叫遠端 MCP tool 並把結果攤平成 Python 物件。"""
    async with Client(server_url) as c:
        res = await c.call_tool(tool, args)
    # fastmcp 2.x：優先用結構化 data；否則取第一個 text content
    data = getattr(res, "data", None)
    if data is not None:
        return data
    content = getattr(res, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return None


# ---------- ① ado-pr-tools ----------

@observe()
async def fetch_pr_metadata(pr_url: str) -> dict:
    return await _call(settings().tools.ado_url,
                       "get_pr_metadata", {"pr_url": pr_url})


@observe()
async def fetch_pr_diff(pr_url: str) -> str:
    out = await _call(settings().tools.ado_url,
                      "get_pr_diff", {"pr_url": pr_url})
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


# ---------- ② repo-reader-tools（多 repo：repo=None 走單 repo 舊行為） ----------

@observe()
async def read_repo_file(path: str, start_line: int = 1,
                         end_line: int | None = None,
                         repo: str | None = None) -> str:
    return await _call(settings().tools.reader_url,
                       "read_file",
                       {"path": path, "start_line": start_line,
                        "end_line": end_line, "repo": repo})


@observe()
async def search_repo(pattern: str, glob: str = "**/*", repo: str | None = None):
    return await _call(settings().tools.reader_url,
                       "search_code", {"pattern": pattern, "glob": glob,
                                       "repo": repo})


# ---------- ③ review-result-tools ----------

@observe()
async def post_review_comment(pr_url: str, markdown: str) -> dict:
    return await _call(settings().tools.result_url,
                       "post_review_comment", {"pr_url": pr_url, "markdown": markdown})

@observe()
async def post_knowledge_draft(pr_url: str, markdown: str,
                               thread_id: int | None = None) -> dict:
    """thread_id 給定 = 回覆進既有 thread（重蒸的更新稿），不開新 thread。

    thread_id 為 None 時**不送這個欄位**：跨服務的版本偏移是常態（orchestrator
    先更新、③ 晚一步 restart），少送一個參數就讓舊版 ③ 照常收稿。這不是潔癖——
    ③ 若因未知參數報錯，harvester 會把它當成「真的貼稿失敗」而代貼，那條路徑
    不看 KNOWLEDGE_POST，影子期會因此誤發。
    """
    args = {"pr_url": pr_url, "markdown": markdown}
    if thread_id is not None:
        args["thread_id"] = thread_id
    return await _call(settings().tools.result_url,
                       "post_knowledge_draft", args)


# ---------- ④ code graph（選配；code-review-graph 的 MCP server） ----------
# 裝好後先 list tools 確認名稱與參數，再設 GRAPH_TOOL_BLAST /
# GRAPH_TOOL_BLAST_ARGS（JSON 樣板，$files / $repo 會被代入）。
# GRAPH_TOOLS_URL 未設 = 整個 graph 功能停用，呼叫端全部優雅降級。


@observe()
async def graph_call(tool: str, args: dict):
    url = settings().graph.tools_url
    if not url:
        return None
    return await _call(url, tool, args)


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
