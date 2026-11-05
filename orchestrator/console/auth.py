"""控制台認證：一把鑰匙（ORCH_API_KEY）換一顆 HMAC cookie。

- ORCH_API_KEY 未設：開放（內網 POC 模式）；頁面頂端掛警示。
- 設了：GET 任何 /console 頁沒 cookie → 302 到 /console/login；POST token 對了就發 cookie。
- cookie 值 = HMAC-SHA256(key, "pr-review-console")；key 一換所有 cookie 立刻失效，
  不需要 session 儲存。
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse

from shared.settings import settings

COOKIE = "prc"


def _key() -> str:
    return settings().orchestrator.api_key.get_secret_value()


def expected_cookie(key: str | None = None) -> str:
    k = key if key is not None else _key()
    return hmac.new(k.encode(), b"pr-review-console", hashlib.sha256).hexdigest()


def is_open() -> bool:
    return not _key()


def is_authed(request: Request) -> bool:
    if is_open():
        return True
    got = request.cookies.get(COOKIE, "")
    return bool(got) and hmac.compare_digest(got, expected_cookie())


def token_ok(token: str) -> bool:
    k = _key()
    return bool(k) and hmac.compare_digest(token.strip(), k)


def login_redirect(request: Request) -> RedirectResponse:
    nxt = request.url.path
    return RedirectResponse(f"/console/login?next={nxt}", status_code=302)
