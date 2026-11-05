"""③ review-result-tools —— 把 review 結果寫回 / 查回 Azure DevOps（FastMCP）。

刻意跟 ①（唯讀）分開：讀與寫的權限等級不同，未來 gateway 治理可以只放行讀、
寫要審批。預設 DRY_RUN=true —— 內測期間絕不誤發真評論。

環境變數：ADO_PAT（必填）、DRY_RUN（預設 true）、HTTPS_PROXY / ADO_VERIFY_SSL 同 ①
啟動：python -m tools.review_result_tools.server   （PORT 預設 8093）
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from shared.azdo import client, get_json, parse_pr_url

mcp = FastMCP("review-result-tools", mask_error_details=False)

MARKER = "<!-- pr-review-bot -->"  # 隱形浮水印：讓我們認得自己發過的評論


def _dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").strip().lower() not in {"false", "0", "no"}


@mcp.tool()
def post_review_comment(pr_url: str, markdown: str) -> dict:
    """把 review markdown 發成 PR 上的一條 comment thread。

    DRY_RUN=true（預設）時不打 API，只回傳預覽 —— 內測安全氣囊。
    """
    ref = parse_pr_url(pr_url)
    content = f"{MARKER}\n{markdown}"
    if _dry_run():
        return {
            "posted": False,
            "dry_run": True,
            "would_post_to": ref.pr_api + "/threads",
            "preview": content[:500],
        }
    with client() as c:
        r = c.post(
            f"{ref.pr_api}/threads",
            params={"api-version": "7.1"},
            json={
                "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
                "status": "active",
            },
        )
        r.raise_for_status()
        thread = r.json()
    return {"posted": True, "dry_run": False, "thread_id": thread.get("id")}


KNOWLEDGE_MARKER = "<!-- pr-review-knowledge-draft -->"


def _knowledge_post() -> bool:
    # 跟 review 的 DRY_RUN 刻意分家：知識稿可先行試點真貼，review 留言繼續乾跑
    return os.getenv("KNOWLEDGE_POST", "false").strip().lower() in {"true", "1", "yes"}


REDISTILL_PREFIX = ("_Draft updated after new commits; earlier author notes "
                    "were kept._")


@mcp.tool()
def post_knowledge_draft(pr_url: str, markdown: str,
                         thread_id: int | None = None) -> dict:
    """把知識蒸餾稿發成 PR 上的一條 thread（KNOWLEDGE_POST=true 才真貼）。

    thread_id 給定時改成「回覆進那條既有 thread」而不是開新的——重蒸（stale
    後用最終 diff 重蒸）的更新稿要接在作者回過話的那串下面，不然同一條 PR
    會出現兩份互相矛盾的草稿，作者也不知道該回哪一份。
    """
    ref = parse_pr_url(pr_url)
    reply = thread_id is not None
    body = f"{REDISTILL_PREFIX}\n\n{markdown}" if reply else markdown
    content = f"{KNOWLEDGE_MARKER}\n{body}"
    target = (f"{ref.pr_api}/threads/{thread_id}/comments" if reply
              else f"{ref.pr_api}/threads")
    if not _knowledge_post():
        return {"posted": False, "dry_run": True, "reply": reply,
                "thread_id": thread_id, "would_post_to": target,
                "preview": content[:500]}
    with client() as c:
        if reply:
            r = c.post(target, params={"api-version": "7.1"},
                       json={"content": content, "commentType": 1})
            r.raise_for_status()
            return {"posted": True, "dry_run": False, "reply": True,
                    "thread_id": thread_id}
        r = c.post(
            target,
            params={"api-version": "7.1"},
            json={"comments": [{"parentCommentId": 0, "content": content,
                                "commentType": 1}],
                  "status": "active"},
        )
        r.raise_for_status()
        thread = r.json()
    return {"posted": True, "dry_run": False, "reply": False,
            "thread_id": thread.get("id")}


@mcp.tool()
def get_past_reviews(pr_url: str) -> list[str]:
    """撈出這條 PR 上「本 bot 發過」的歷史評論（靠隱形浮水印過濾）。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        threads = get_json(c, f"{ref.pr_api}/threads")["value"]
    out: list[str] = []
    for t in threads:
        for cm in t.get("comments", []):
            body = cm.get("content") or ""
            if MARKER in body:
                out.append(body.replace(MARKER, "").strip())
    return out


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8093")),
    )
