"""串流旁白匯流排：pipeline 各站往這裡丟一句話，app 層決定播不播。

用 contextvars 而不是把 callback 一路傳參——run_review 的簽名（eval 注入口）
一個字都不動；沒人訂閱時（eval／CLI／非串流 /review）note() 是無聲 no-op，
而且旁白任何失敗都被吞掉，永不影響主流程。
"""
from __future__ import annotations

import contextvars
from typing import Awaitable, Callable, Optional

StatusCb = Optional[Callable[[str], Awaitable[None]]]

_cb: contextvars.ContextVar[StatusCb] = contextvars.ContextVar(
    "review_status_cb", default=None)


def set_status_cb(cb: StatusCb):
    """訂閱旁白（app 的 SSE 直播用）。回傳 token 給 reset 用。"""
    return _cb.set(cb)


def reset_status_cb(token) -> None:
    _cb.reset(token)


async def note(msg: str) -> None:
    cb = _cb.get()
    if cb is None:
        return
    try:
        await cb(msg)
    except Exception:
        pass  # 旁白永不干擾主流程
