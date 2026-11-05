"""① ado-pr-tools —— 抓 Azure DevOps PR 的工具組（FastMCP）。

紀律：無狀態、唯讀、不 import langfuse。PAT/proxy 只活在本服務的環境變數裡，
永遠不會流進 orchestrator 或 trace。

啟動：python -m tools.ado_pr_tools.server   （PORT 預設 8091）
"""
from __future__ import annotations

import difflib
import os
from concurrent.futures import ThreadPoolExecutor

from fastmcp import FastMCP

from shared.azdo import PrRef, client, get_json, get_text, parse_pr_url

mcp = FastMCP("ado-pr-tools", mask_error_details=False)

MAX_FILES = int(os.getenv("ADO_DIFF_MAX_FILES", "300"))  # 先於一切層（含 static）的最外圈上限
MAX_FILE_BYTES = int(os.getenv("ADO_DIFF_MAX_FILE_BYTES", "600000"))
# 每個檔案要抓 base/head 兩份內容；序列化跑百檔會拖到分鐘級，用執行緒併發。
# httpx.Client 是 thread-safe 的，共用同一顆 client 即可（連線池也一起共用）。
FETCH_CONCURRENCY = max(1, int(os.getenv("ADO_DIFF_FETCH_CONCURRENCY", "8")))


PAGE = 200          # ADO 單頁上限；靠 $skip 迴圈把全部撈完


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


def _latest_iteration_changes(c, ref: PrRef) -> list[dict]:
    it = _latest_iteration(c, ref)
    if it is None:
        return []
    return [e for e in _iteration_changes(c, ref, it["id"]) if not _is_folder(e)]


@mcp.tool()
def get_pr_metadata(pr_url: str) -> dict:
    """取得 PR 的標題、描述、分支與 head/base commit。"""
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


@mcp.tool()
def list_pr_files(pr_url: str) -> list[dict]:
    """列出這條 PR 變更的檔案（路徑 + 變更型態）。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        entries = _latest_iteration_changes(c, ref)
    return [
        {
            "path": (e.get("item") or {}).get("path", ""),
            "change_type": e.get("changeType", ""),
        }
        for e in entries
        if (e.get("item") or {}).get("path")
    ]


@mcp.tool()
def get_pr_diff(pr_url: str) -> str:
    """組出這條 PR 的 unified diff（純文字）。

    做法：取最新 iteration 的變更清單，逐檔抓 base/head 兩個 commit 的內容，
    用 difflib 產生 unified diff。過大或疑似二進位的檔案會標註略過。
    （若你們既有 daemon 已有更完整的 diff 邏輯，可把內部實作換成它，
    對外簽名 get_pr_diff(pr_url) -> str 維持不變即可。）
    """
    ref = parse_pr_url(pr_url)
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
        if total > MAX_FILES:
            out.append(f"# 注意：共 {total} 個檔案，僅呈現前 {MAX_FILES} 個\n")
            entries = entries[:MAX_FILES]

        item_url = f"{ref.repo_api}/items"

        def _content(commit_id: str | None, p: str) -> str | None:
            if not commit_id:
                return None
            return get_text(
                c,
                item_url,
                path=p,
                **{
                    "versionDescriptor.version": commit_id,
                    "versionDescriptor.versionType": "commit",
                },
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

        # executor.map 依輸入順序回傳 —— 併發抓取，輸出仍是 deterministic 的
        with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as pool:
            fetched = list(pool.map(_pair, targets))

        for (path, old_path), (old, new) in zip(targets, fetched):
            if old_path != path:
                out.append(f"# {old_path} → {path}: renamed\n")
            if (old and len(old) > MAX_FILE_BYTES) or (new and len(new) > MAX_FILE_BYTES):
                out.append(f"# {path}: file too large — diff skipped\n")
                continue
            if (old and "\x00" in old[:1024]) or (new and "\x00" in new[:1024]):
                out.append(f"# {path}: binary file suspected — skipped\n")
                continue

            diff = difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"a{old_path}",
                tofile=f"b{path}",
            )
            out.append("".join(diff))

    return "\n".join(s for s in out if s.strip()) or "# 這條 PR 沒有可呈現的文字變更"


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8091")),
    )
