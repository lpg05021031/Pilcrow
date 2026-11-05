"""Azure DevOps 共用工具：PR URL 解析 + 帶 PAT/proxy 的 HTTP client。

設計原則：這裡是「笨」的一層 —— 不 import langfuse、不含任何 review 邏輯。
設定：settings().ado（ADO_PAT 必填；ADO_VERIFY_SSL 可為 true/false 或 CA bundle 路徑）。
HTTPS_PROXY 等標準 proxy 變數由 httpx trust_env 自動吃。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from shared.settings import settings

API_VERSION = "7.1"


@dataclass(frozen=True)
class PrRef:
    """一條 PR 的座標。base_url 形如 https://dev.azure.com/{org}/{project}
    （on-prem 則是 https://server/{collection}/{project}），repo 與 pr_id 自明。"""

    base_url: str
    repo: str
    pr_id: int

    @property
    def repo_api(self) -> str:
        return f"{self.base_url}/_apis/git/repositories/{quote(self.repo)}"

    @property
    def pr_api(self) -> str:
        return f"{self.repo_api}/pullRequests/{self.pr_id}"


_PR_URL_RE = re.compile(
    r"(?P<base>https?://[^\s]+?)/_git/(?P<repo>[^/\s]+)/pullrequest/(?P<id>\d+)",
    re.IGNORECASE,
)


def parse_pr_url(pr_url: str) -> PrRef:
    """支援 dev.azure.com 與 on-prem Azure DevOps Server 兩種 URL 形態。"""
    m = _PR_URL_RE.search(pr_url.strip())
    if not m:
        raise ValueError(
            f"無法解析 PR URL：{pr_url!r}（預期含 /_git/<repo>/pullrequest/<id>）"
        )
    return PrRef(base_url=m.group("base"), repo=m.group("repo"), pr_id=int(m.group("id")))


def find_pr_url(text: str) -> str | None:
    """從任意文字（例如聊天訊息）撈出第一個 PR URL。"""
    m = _PR_URL_RE.search(text or "")
    if not m:
        return None
    return f"{m.group('base')}/_git/{m.group('repo')}/pullrequest/{m.group('id')}"


def _verify() -> bool | str:
    return settings().ado.verify_ssl


def client() -> httpx.Client:
    pat = settings().ado.pat.get_secret_value()
    if not pat:
        raise RuntimeError("環境變數 ADO_PAT 未設定")
    return httpx.Client(
        auth=("", pat),          # ADO 的 PAT 走 basic auth，username 留空
        trust_env=True,           # 自動吃 HTTPS_PROXY / NO_PROXY
        verify=_verify(),
        timeout=httpx.Timeout(30.0),
        headers={"Accept": "application/json"},
    )


def get_json(c: httpx.Client, url: str, **params) -> dict:
    params.setdefault("api-version", API_VERSION)
    r = c.get(url, params=params)
    r.raise_for_status()
    return r.json()


def get_text(c: httpx.Client, url: str, **params) -> str | None:
    """抓檔案內容；404（該版本不存在此檔，新增/刪除情境）回 None。"""
    params.setdefault("api-version", API_VERSION)
    r = c.get(url, params=params, headers={"Accept": "text/plain"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text
