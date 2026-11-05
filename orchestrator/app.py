"""review-orchestrator —— 唯一的大腦、唯一接 Langfuse 的服務。

檔位一（狀態旁白串流）：/v1/chat/completions 在 stream=true 時，
一邊執行 pipeline 一邊把進度旁白即時吐給聊天室（🔍 解析 → 📋 metadata →
📄 diff → 🤖 review），最後串出 markdown —— 使用者看直播，不再盯黑箱。
追蹤結構不變：handle_review 仍是 @observe 的最外層，旁白只是旁路。

啟動：uvicorn orchestrator.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langfuse import observe, propagate_attributes
from pydantic import BaseModel

from orchestrator import clients
from orchestrator.core import run_review
from orchestrator.models import ReviewResult
from orchestrator import console, jobs, knowledge, progress
from shared.azdo import find_pr_url
from shared.settings import fail_fast, settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    fail_fast()          # 設定壞值在啟動就報，不等第一條 review
    for st in jobs.start():
        print(f"[jobs] {st.name}: " + (f"每 {st.interval_min} 分" if st.enabled else "off"))
    try:
        yield
    finally:
        jobs.stop()


app = FastAPI(title="review-orchestrator", lifespan=_lifespan)
console.mount(app)          # /console/*：控制台（S2 起）

MODEL_ID = "pr-reviewer"
HELP_TEXT = (
    "把 Azure DevOps 的 PR 連結貼給我（形如 https://.../_git/<repo>/pullrequest/<id>），"
    "我會抓 diff 做 code review。"
)

# ---- 元任務盾 + 去重快取（防 Open WebUI 的 title/tags/follow-up 偷跑 pipeline）----

_META_MARKERS = (
    "### task:", "generate a concise", "3-5 word title", "concise title",
    "generate 1-3 broad tags", "categorizing the main themes", "broad tags",
    "follow-up", "suggest 3", "emoji",
)

def _is_meta_task(body: dict, last_text: str) -> bool:
    """Open WebUI 的元任務（標題/標籤/追問生成）辨識：
    新版會帶 metadata.task 欄位；舊版靠模板字樣。命中就不進 review pipeline。"""
    task = ((body.get("metadata") or {}).get("task")
            or body.get("task") or "")
    if task:
        return True
    low = last_text.lower()
    return any(m in low for m in _META_MARKERS)


_RECENT: dict[str, tuple[float, str]] = {}   # pr_url -> (時間戳, markdown)


def _ttl() -> float:
    return settings().orchestrator.dedupe_ttl


def _cache_get(pr_url: str, source_commit: str = "") -> str | None:
    hit = _RECENT.get(pr_url)
    if hit and (time.time() - hit[0]) < _ttl():
        cached_commit = hit[2] if len(hit) > 2 else ""
        if source_commit and cached_commit and source_commit != cached_commit:
            return None                    # push 了新 commit → 舊 review 作廢
        return hit[1]
    return None

def _cache_put(pr_url: str, markdown: str, source_commit: str = "") -> None:
    _RECENT[pr_url] = (time.time(), markdown, source_commit)
    if len(_RECENT) > 200:   # 粗糙但夠用的清理
        cutoff = time.time() - _ttl()
        for k in [k for k, (t, *_rest) in _RECENT.items() if t < cutoff]:
            _RECENT.pop(k, None)

StatusCb = Optional[Callable[[str], Awaitable[None]]]

_BG_TASKS: set = set()          # F5：event loop 只持弱引用，強引用收在這


def _bg(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


_EVAL_BUSY = {"flag": False}    # eval-webhook 併發鎖（防連點疊考）


def _check_auth(authorization: str | None):
    """設定 ORCH_API_KEY 時才驗；沒設就開放（內網 POC 模式）。"""
    want = settings().orchestrator.api_key.get_secret_value()
    if not want:
        return
    got = (authorization or "").removeprefix("Bearer ").strip()
    if got != want:
        raise HTTPException(status_code=401, detail="invalid api key")


@observe(name="pr-review")
async def handle_review(pr_url: str, user: str,
                        on_status: StatusCb = None) -> ReviewResult:
    """一條 PR review 的完整過程 —— trace 的最外層。

    on_status：旁白回呼（SSE 直播用）；不給就是原本的安靜模式（/review 用）。
    """
    async def note(msg: str):
        if on_status:
            await on_status(msg)

    token = progress.set_status_cb(on_status)  # v2 pipeline 各站旁白由此播出
    try:
        return await _handle_review_inner(pr_url, user, note)
    finally:
        progress.reset_status_cb(token)


async def _handle_review_inner(pr_url: str, user: str, note) -> ReviewResult:
    with propagate_attributes(session_id=pr_url, user_id=user, tags=["live"]):
        await note("Resolving pull request URL")
        metadata = await clients.fetch_pr_metadata(pr_url)
        title = (metadata or {}).get("title", "")[:50]
        await note(f"PR located — \"{title}\"; fetching diff")

        diff = await clients.fetch_pr_diff(pr_url)
        n_files = len(re.findall(r"^\+\+\+ b", diff, re.M))
        await note(f"Diff retrieved — {n_files} file(s), {len(diff)} characters; running review pipeline")

        result = await run_review(diff, metadata)

        if settings().orchestrator.auto_post:
            await clients.post_review_comment(pr_url, result.markdown)
            await note("Review comment posted to the PR (subject to the results service DRY_RUN setting)")

        # K 段：非同步蒸餾 + 貼稿 + _pending —— 不擋使用者、失敗只進 journal
        try:
            files = [r["path"] for r in (result.stats.get("file_table") or [])]
            # reviewer 看的是這份 diff、報告裡的編號是 F1..Fn（render 的順序）——
            # 蒸餾吃同一份，證據才驗得動；不另造 diff 表示、不重 parse markdown
            finding_ids = {f"F{i}" for i in range(1, len(result.findings) + 1)}
            finding_files = [f.file for f in result.findings if f.file]
            # 矛盾偵測（確定性）：報告裡「踩到既有不變式」的 finding 引了哪些 id。
            # 這批 id 之後會跟著稿走：PR 若帶著矛盾完成，代表實作推翻了規則，
            # 驗收窗要為它做一次裁決（見 knowledge_harvest 的 contradiction 段）。
            contradicted = {ref for f in result.findings
                            if f.severity in ("error", "warning")
                            for ref in (f.knowledge_ref or [])}
            _bg(knowledge.distill_and_stage(
                pr_url, metadata, files, result.markdown,
                list(result.stats.get("graph_impacted") or []),
                diff=diff, finding_ids=finding_ids,
                finding_files=finding_files, contradicted=contradicted))
        except Exception as e:
            print(f"[knowledge] 排程失敗：{e}")

        return result


# ---------- OpenAI 相容層（Open WebUI 用） ----------

@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return {"object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "owned_by": "pr-review-suite"}]}


def _delta(rid: str, **delta) -> str:
    payload = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk",
               "created": int(time.time()), "model": MODEL_ID,
               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finish(rid: str) -> str:
    payload = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk",
               "created": int(time.time()), "model": MODEL_ID,
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"


async def _stream_text(rid: str, text: str,
                       chunk: int = 800) -> AsyncIterator[str]:
    """靜態文字（help / 錯誤）也走 SSE。"""
    yield _delta(rid, role="assistant")
    for i in range(0, len(text), chunk):
        yield _delta(rid, content=text[i:i + chunk])
    yield _finish(rid)


async def _stream_review(rid: str, pr_url: str, user: str,
                         commit_hint: str = "") -> AsyncIterator[str]:
    """檔位一的心臟：queue 直播 —— pipeline 每過一站就吐一行旁白。"""
    q: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    async def on_status(msg: str):
        await q.put(("status", msg))

    async def runner():
        try:
            result = await handle_review(pr_url, user, on_status=on_status)
            _cache_put(pr_url, result.markdown, commit_hint)
            await q.put(("result", result.markdown))
        except Exception as e:  # 串流已開場，不能再回 500 —— 把錯誤演出來
            await q.put(("error",
                         f"❌ review 失敗：{type(e).__name__}: {e}\n"
                         f"（細節見伺服器 journalctl -u review-orchestrator）"))
            raise  # 讓 traceback 照常進 journal
        finally:
            await q.put(None)

    task = asyncio.create_task(runner())
    yield _delta(rid, role="assistant")
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            kind, text = item
            if kind == "status":
                yield _delta(rid, content=text + "\n\n")
            elif kind == "result":
                yield _delta(rid, content="---\n\n")
                for i in range(0, len(text), 800):
                    yield _delta(rid, content=text[i:i + 800])
            else:  # error
                yield _delta(rid, content=text)
    finally:
        task.cancel()  # client 斷線時別讓 runner 變孤兒
    yield _finish(rid)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_openwebui_user_email: str | None = Header(default=None),
    x_openwebui_user_name: str | None = Header(default=None),
):
    _check_auth(authorization)
    body = await request.json()
    user = x_openwebui_user_email or x_openwebui_user_name or "anonymous"

    last_user_msg = ""
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content", "")
            last_user_msg = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break

    pr_url = find_pr_url(last_user_msg)
    rid = uuid.uuid4().hex[:12]
    stream = bool(body.get("stream"))

    def _reply(text: str):
        """靜態文字回覆（盾牌與 help 共用），串流/非串流都吃。"""
        if stream:
            return StreamingResponse(_stream_text(rid, text),
                                     media_type="text/event-stream")
        return {
            "id": f"chatcmpl-{rid}", "object": "chat.completion",
            "created": int(time.time()), "model": MODEL_ID,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
        }

    # 盾一：元任務（title/tags/follow-up）給靜態短答，絕不進 pipeline
    if _is_meta_task(body, last_user_msg):
        return _reply(f"PR Review {pr_url.rsplit('/', 1)[-1]}" if pr_url else "PR Review")

    # 盾二：同一條 PR 在 TTL 內重複觸發 → 重播快取，不再燒一輪
    commit_hint = ""
    if pr_url:
        try:
            commit_hint = (await clients.fetch_pr_metadata(pr_url)) \
                .get("source_commit") or ""
        except Exception:
            pass                            # metadata 失敗 → 照舊 TTL 語意
    if pr_url and (cached := _cache_get(pr_url, commit_hint)) is not None:
        return _reply(cached)

    if not pr_url:
        return _reply(HELP_TEXT)

    if stream:
        return StreamingResponse(_stream_review(rid, pr_url, user,
                                                commit_hint),
                                 media_type="text/event-stream")

    result = await handle_review(pr_url, user)
    _cache_put(pr_url, result.markdown, commit_hint)
    return _reply(result.markdown)


# ---------- 腳本 / CI 用的乾淨介面 ----------

class ReviewRequest(BaseModel):
    pr_url: str
    user: str = "api"


@app.post("/review")
async def review(req: ReviewRequest,
                 authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    result = await handle_review(req.pr_url, req.user)
    return {"pr_url": req.pr_url,
            "markdown": result.markdown,
            "findings": result.findings_summary()}


class RedistillRequest(BaseModel):
    pr_url: str


@app.post("/knowledge/redistill", status_code=202)
async def knowledge_redistill(req: RedistillRequest,
                              authorization: str | None = Header(default=None)):
    """stale 補救：harvester 偵測到「draft 之後又有 push」時打這裡。

    harvester 只偵測，蒸餾這件事整個留在 orchestrator（`_land` 仍然零 LLM，
    不把半個 orchestrator 搬進收割器）。這條端點有 LLM 花費與寫檔語意，
    不是純讀——所以照 `_check_auth` 走；部署時務必把 ORCH_API_KEY 設起來。
    收到即回 202、背景執行；重試上限由 harvester 的請求戳守（≤ 2 次）。
    """
    _check_auth(authorization)
    if not knowledge.redistill_enabled():
        return {"ok": False, "pr_url": req.pr_url,
                "note": "KNOWLEDGE_REDISTILL=false —— 重蒸關閉"}
    _bg(knowledge.redistill(req.pr_url))
    return {"ok": True, "pr_url": req.pr_url,
            "note": "重蒸已在背景開跑；結果覆寫 _pending，看 journal"}


# ---------- Langfuse UI「Custom Experiment」按鈕的落點 ----------

@app.post("/eval-webhook")
async def eval_webhook(request: Request):
    """Langfuse Remote Experiment webhook：收到即回、背景開考。

    Langfuse 只等 10 秒，所以這裡用 subprocess 丟背景跑 run_eval，
    分數由 run_eval 自己回寫成新的 dataset run。注意：此端點刻意不走
    ORCH_API_KEY（Langfuse 觸發時無法帶自訂 Authorization），內網環境
    可接受；要更嚴就在 Langfuse 設定裡開 HMAC 簽章再於此驗證。
    """
    import sys
    from pathlib import Path

    try:
        body = await request.json()
    except Exception:
        body = {}
    cfg = {}
    for key in ("config", "payload"):
        if isinstance(body.get(key), dict):
            cfg.update(body[key])
    q = request.query_params
    # pipeline 可由 query（?pipeline=v2）或 body 指定；預設跟服務 env 走。
    # 在 Langfuse 存兩個 Remote Experiment 設定（v1 / v2 各一）= UI 一鍵 A/B。
    pipeline = (q.get("pipeline") or cfg.get("pipeline")
                or settings().orchestrator.pipeline).strip().lower()
    if pipeline not in ("v1", "v2"):
        pipeline = "v1"
    run_name = (q.get("run_name") or cfg.get("run_name")
                or f"ui-{pipeline}-{time.strftime('%m%d-%H%M%S')}")

    if _EVAL_BUSY["flag"]:
        raise HTTPException(status_code=409,
                            detail="上一輪 eval 還在跑（併發鎖）；等它收尾再觸發")
    _EVAL_BUSY["flag"] = True
    env = os.environ.copy()
    env["REVIEW_PIPELINE"] = pipeline
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "eval.run_eval", run_name,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )
    async def _reap():
        try:
            await proc.wait()
        finally:
            _EVAL_BUSY["flag"] = False
    _bg(_reap())                       # 收屍 + 釋放併發鎖（強引用防 GC）
    return {"ok": True, "run_name": run_name, "pipeline": pipeline,
            "note": "eval 已在背景開跑，稍後到 Datasets → Runs 看分數"}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "pipeline": settings().orchestrator.pipeline,
            "jobs": jobs.snapshot()}
