"""Azure DevOps adapter —— 原 ① ado_pr_tools（讀）與 ③ review_result_tools（寫）合一。

讀：get_pr_metadata / list_pr_files / get_pr_diff
寫：post_review_comment / post_knowledge_draft / get_past_reviews

寫這一側保留原本的雙開關語意，只是住在同一個行程裡：
  review 留言   AUTO_POST（orchestrator 點不點火）× DRY_RUN（這裡煞不煞車）
  知識稿        KNOWLEDGE_POST（獨立開關，可先行試點）
出廠全安全側；誤設任何單顆都不會誤發。每一次真發都留一筆稽核（audit_log），
S3 的控制台直接讀它。

全部是同步函式（httpx.Client）；async 呼叫端見 clients.py。
"""
from __future__ import annotations

import difflib
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from shared.azdo import PrRef, client, get_json, get_text, parse_pr_url
from shared.settings import settings

PAGE = 200          # ADO 單頁上限；靠 $skip 迴圈把全部撈完

MARKER = "<!-- pr-review-bot -->"                       # review 留言的隱形浮水印
KNOWLEDGE_MARKER = "<!-- pr-review-knowledge-draft -->"  # 知識稿的浮水印
REDISTILL_PREFIX = ("_Draft updated after new commits; earlier author notes "
                    "were kept._")

# 最近 200 筆對 ADO 的寫入（含 dry-run）：(時間, 種類, pr_url, posted?, 說明)
audit_log: deque[dict] = deque(maxlen=200)


def _audit(kind: str, pr_url: str, posted: bool, note: str) -> None:
    audit_log.append({"ts": time.time(), "kind": kind, "pr_url": pr_url,
                      "posted": posted, "note": note})


# ---------- 讀 ----------

def _latest_iteration(c, ref: PrRef) -> dict | None:
    """最新一次 iteration 的完整物件。

    裡面有三個 commit，選對哪一個決定了 diff 對不對得上 PR 頁面：
      commonRefCommit  merge base —— PR UI 左欄就是它（三點 diff 的基準）
      sourceRefCommit  來源分支 tip —— PR UI 右欄
      targetRefCommit  目標分支 tip —— **不要拿來當 base**，master 一動它就變
    """
    iterations = get_json(c, f"{ref.pr_api}/iterations")["value"]
    return iterations[-1] if iterations else None


def _iteration_changes(c, ref: PrRef, iteration_id: int) -> list[dict]:
    """該 iteration 相對 merge base 的完整變更清單（$skip 分頁，不截斷）。"""
    entries: list[dict] = []
    skip = 0
    while True:
        page = get_json(
            c, f"{ref.pr_api}/iterations/{iteration_id}/changes",
            **{"$top": PAGE, "$skip": skip},
        ).get("changeEntries", [])
        entries += page
        if len(page) < PAGE:
            return entries
        skip += PAGE


def _is_folder(entry: dict) -> bool:
    item = entry.get("item") or {}
    return bool(item.get("isFolder")) or item.get("gitObjectType") == "tree"


def get_pr_metadata(pr_url: str) -> dict:
    """PR 的標題、描述、分支與 head/base commit。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        pr = get_json(c, ref.pr_api)
        it = _latest_iteration(c, ref) or {}
    # source_commit 取自 iteration 而非 lastMergeSourceCommit：兩者平時相同，
    # 但前者才是這份 diff 真正的右欄，快取鍵要跟 diff 綁在一起才不會失準。
    source_commit = ((it.get("sourceRefCommit") or {}).get("commitId")
                     or (pr.get("lastMergeSourceCommit") or {}).get("commitId"))
    return {
        "repo": ref.repo,  # 讓 orchestrator 能載入 per-repo 規則（rules/<repo小寫>/）
        "title": pr.get("title", ""),
        "description": pr.get("description", ""),
        "source_branch": pr.get("sourceRefName", ""),
        "target_branch": pr.get("targetRefName", ""),
        "author": (pr.get("createdBy") or {}).get("displayName", ""),
        "status": pr.get("status", ""),
        "source_commit": source_commit,
        "merge_base": (it.get("commonRefCommit") or {}).get("commitId"),
        "target_commit": (pr.get("lastMergeTargetCommit") or {}).get("commitId"),
    }


def list_pr_files(pr_url: str) -> list[dict]:
    """這條 PR 變更的檔案（路徑 + 變更型態）。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        it = _latest_iteration(c, ref)
        entries = ([e for e in _iteration_changes(c, ref, it["id"]) if not _is_folder(e)]
                   if it else [])
    return [{"path": (e.get("item") or {}).get("path", ""),
             "change_type": e.get("changeType", "")}
            for e in entries if (e.get("item") or {}).get("path")]


def get_pr_diff(pr_url: str) -> str:
    """組出這條 PR 的 unified diff（純文字）。

    取最新 iteration 的變更清單，逐檔抓 merge base / head 兩份內容，用 difflib
    產生 unified diff。過大或疑似二進位的檔案會標註略過。
    """
    ref = parse_pr_url(pr_url)
    cfg = settings().ado
    out: list[str] = []
    with client() as c:
        it = _latest_iteration(c, ref)
        if it is None:
            return "# 這條 PR 沒有 iteration，無法取得變更"

        # 三點 diff：merge base ↔ 來源分支 tip，與 PR 頁面左右兩欄一致。
        # 用 targetRefCommit 當 base 會把目標分支上別人的改動反向算進來，
        # 而且目標分支一往前推，同一條沒動過的 PR 就會跑出不同的 diff。
        base = (it.get("commonRefCommit") or {}).get("commitId")
        head = (it.get("sourceRefCommit") or {}).get("commitId")

        entries = [e for e in _iteration_changes(c, ref, it["id"])
                   if not _is_folder(e)]

        total = len(entries)
        if total > cfg.diff_max_files:
            out.append(f"# 注意：共 {total} 個檔案，僅呈現前 {cfg.diff_max_files} 個\n")
            entries = entries[:cfg.diff_max_files]

        item_url = f"{ref.repo_api}/items"

        def _content(commit_id: str | None, p: str) -> str | None:
            if not commit_id:
                return None
            return get_text(
                c, item_url, path=p,
                **{"versionDescriptor.version": commit_id,
                   "versionDescriptor.versionType": "commit"},
            )

        # (新路徑, 舊路徑)；rename 時 base 側要用舊路徑，否則整檔會被誤判成新增
        targets: list[tuple[str, str]] = []
        for e in entries:
            path = (e.get("item") or {}).get("path")
            if path:
                targets.append((path, e.get("sourceServerItem") or path))

        def _pair(t: tuple[str, str]) -> tuple[str | None, str | None]:
            path, old_path = t
            return _content(base, old_path), _content(head, path)

        # 每檔要抓 base/head 兩份；序列化跑百檔會拖到分鐘級，用執行緒併發。
        # httpx.Client 是 thread-safe 的；executor.map 依輸入順序回傳，輸出仍 deterministic。
        with ThreadPoolExecutor(max_workers=cfg.diff_fetch_concurrency) as pool:
            fetched = list(pool.map(_pair, targets))

        for (path, old_path), (old, new) in zip(targets, fetched):
            if old_path != path:
                out.append(f"# {old_path} → {path}: renamed\n")
            if (old and len(old) > cfg.diff_max_file_bytes) \
                    or (new and len(new) > cfg.diff_max_file_bytes):
                out.append(f"# {path}: file too large — diff skipped\n")
                continue
            if (old and "\x00" in old[:1024]) or (new and "\x00" in new[:1024]):
                out.append(f"# {path}: binary file suspected — skipped\n")
                continue
            diff = difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"a{old_path}", tofile=f"b{path}",
            )
            out.append("".join(diff))

    return "\n".join(s for s in out if s.strip()) or "# 這條 PR 沒有可呈現的文字變更"


# ---------- 寫 ----------

def _new_thread(c, ref: PrRef, content: str) -> dict:
    r = c.post(f"{ref.pr_api}/threads", params={"api-version": "7.1"},
               json={"comments": [{"parentCommentId": 0, "content": content,
                                   "commentType": 1}],
                     "status": "active"})
    r.raise_for_status()
    return r.json()


def post_review_comment(pr_url: str, markdown: str) -> dict:
    """把 review markdown 發成 PR 上的一條 comment thread。

    DRY_RUN=true（預設）時不打 API，只回傳預覽 —— 內測安全氣囊。
    """
    ref = parse_pr_url(pr_url)
    content = f"{MARKER}\n{markdown}"
    if settings().posting.dry_run:
        _audit("review", pr_url, False, "DRY_RUN=true，只預覽")
        return {"posted": False, "dry_run": True,
                "would_post_to": ref.pr_api + "/threads", "preview": content[:500]}
    with client() as c:
        thread = _new_thread(c, ref, content)
    _audit("review", pr_url, True, f"thread {thread.get('id')}")
    return {"posted": True, "dry_run": False, "thread_id": thread.get("id")}


def post_knowledge_draft(pr_url: str, markdown: str,
                         thread_id: int | None = None) -> dict:
    """把知識蒸餾稿發成 PR 上的一條 thread（KNOWLEDGE_POST=true 才真貼）。

    thread_id 給定時改成「回覆進那條既有 thread」而不是開新的——重蒸的更新稿要
    接在作者回過話的那串下面，不然同一條 PR 會出現兩份互相矛盾的草稿。
    """
    ref = parse_pr_url(pr_url)
    reply = thread_id is not None
    body = f"{REDISTILL_PREFIX}\n\n{markdown}" if reply else markdown
    content = f"{KNOWLEDGE_MARKER}\n{body}"
    target = (f"{ref.pr_api}/threads/{thread_id}/comments" if reply
              else f"{ref.pr_api}/threads")
    if not settings().posting.knowledge_post:
        _audit("knowledge", pr_url, False, "KNOWLEDGE_POST=false，影子模式")
        return {"posted": False, "dry_run": True, "reply": reply,
                "thread_id": thread_id, "would_post_to": target,
                "preview": content[:500]}
    with client() as c:
        if reply:
            r = c.post(target, params={"api-version": "7.1"},
                       json={"content": content, "commentType": 1})
            r.raise_for_status()
            _audit("knowledge", pr_url, True, f"reply in thread {thread_id}")
            return {"posted": True, "dry_run": False, "reply": True,
                    "thread_id": thread_id}
        thread = _new_thread(c, ref, content)
    _audit("knowledge", pr_url, True, f"thread {thread.get('id')}")
    return {"posted": True, "dry_run": False, "reply": False,
            "thread_id": thread.get("id")}


def get_past_reviews(pr_url: str) -> list[str]:
    """這條 PR 上「本 bot 發過」的歷史評論（靠隱形浮水印過濾）。"""
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
