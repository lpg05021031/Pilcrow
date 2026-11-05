"""LLM 共用層：client、受版控 prompt 的取用、健壯的 JSON 擷取。

v2 pipeline 的鐵律在這裡落實：每個 LLM 階段都是「JSON 輸出 + 驗證 +
確定性 fallback」——LLM 是不可靠零件，任何階段故障都是降級、不是炸掉。
"""
from __future__ import annotations

import json
import asyncio
import os
import re
from typing import Any

from langfuse import get_client
from langfuse.openai import AsyncOpenAI  # drop-in：呼叫自動變 generation span

llm = AsyncOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:8000"),
    api_key=os.getenv("LLM_API_KEY", "not-needed"),
)


def model_name() -> str:
    return os.getenv("LLM_MODEL", "gpt-4o-mini")


def prompt_or(name: str, default: str) -> str:
    """優先用 Langfuse 受版控的 prompt（UI 改版、code 零改動）；拿不到用內建版。"""
    try:
        return get_client().get_prompt(name).prompt
    except Exception:
        return default


async def complete(system: str, user: str, temperature: float = 0.2,
                   model: str | None = None) -> str:
    """model=None 走 LLM_MODEL；adversary 可用 LLM_MODEL_ADVERSARY 換一顆腦
    （檢察官與被告律師分家，去掉自我同意偏差）。
    LLM_TIMEOUT_S（預設 180）+ 一次重試：gateway 卡住不吊死整場 review——
    重試仍失敗就把例外交還呼叫端，走各站既有的降級路徑。"""
    timeout = float(os.getenv("LLM_TIMEOUT_S", "180") or 180)

    async def _once() -> str:
        resp = await llm.chat.completions.create(
            model=model or model_name(),
            temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    try:
        return await asyncio.wait_for(_once(), timeout=timeout)
    except (asyncio.TimeoutError, Exception) as first:  # noqa: BLE001
        await asyncio.sleep(1.5)
        try:
            return await asyncio.wait_for(_once(), timeout=timeout)
        except Exception:
            raise first


def complete_sync(system: str, user: str, temperature: float = 0.2,
                  model: str | None = None) -> str:
    """給非 async 的 oneshot 程序（knowledge_harvest）用。

    每次呼叫開一個新 event loop 並建一個新 client：模組層那顆 `llm` 的連線池
    綁在第一個 loop 上，跨 `asyncio.run` 重用會炸 "Event loop is closed"。
    同樣走 LLM_TIMEOUT_S + 一次重試；失敗把例外交還呼叫端走降級。"""
    timeout = float(os.getenv("LLM_TIMEOUT_S", "180") or 180)

    async def _go() -> str:
        client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:8000"),
            api_key=os.getenv("LLM_API_KEY", "not-needed"))

        async def _once() -> str:
            resp = await client.chat.completions.create(
                model=model or model_name(), temperature=temperature,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            return resp.choices[0].message.content or ""
        try:
            try:
                return await asyncio.wait_for(_once(), timeout=timeout)
            except (asyncio.TimeoutError, Exception) as first:  # noqa: BLE001
                await asyncio.sleep(1.5)
                try:
                    return await asyncio.wait_for(_once(), timeout=timeout)
                except Exception:
                    raise first
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    return asyncio.run(_go())


_FENCE = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.DOTALL)


def extract_json(text: str) -> Any | None:
    """從模型輸出撈 JSON：先試 fenced 區塊（取最後一個），再試裸 JSON，
    最後試「第一個 [ 或 { 到最後一個 ] 或 }」。全失敗回 None（呼叫端走 fallback）。"""
    m = None
    for m in _FENCE.finditer(text):
        pass
    candidates = []
    if m:
        candidates.append(m.group(1))
    candidates.append(text.strip())
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    end = max(text.rfind("]"), text.rfind("}"))
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
    return None
