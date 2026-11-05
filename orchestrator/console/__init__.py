"""console —— 掛在 orchestrator 上的控制台（/console/*）。

為什麼不是獨立服務：S1 把系統縮成一個行程，控制台要看的東西（設定、jobs 狀態、
寫入稽核、之後的 review 歷史）全在這個行程的記憶體裡；再開一個服務就要再開一個
port、一份設定、一條 IPC。

技術選擇全部為了「內網、零 build、一個人維護」：FastAPI + Jinja2 伺服端渲染，
一支 2 KB 的 console.js（表單非同步送出、局部換頁），一支自寫 CSS，無 node、無 CDN。

認證：沿用 ORCH_API_KEY。設了 → 登入頁換一顆 HMAC cookie；沒設 → 開放（內網
POC 模式，頁面頂端掛警示條）。不另造第二把鑰匙。

S2 有兩頁：Overview（doctor 一張表 + jobs）、Settings（schema 生表單、Apply 熱重載、
改動紀錄）。Sources / Reviews / Knowledge 是 S3 / S4。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from orchestrator.console.router import router as console_router

_HERE = Path(__file__).parent


def mount(app: FastAPI) -> None:
    app.mount("/console/static", StaticFiles(directory=str(_HERE / "static")), name="console-static")
    app.include_router(console_router)
