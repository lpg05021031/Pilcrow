"""② repo-reader-tools —— 讀本地 repo 的工具組（FastMCP）。

紀律：嚴格唯讀、鎖在 repo 根目錄底下（防路徑穿越）、不 import langfuse。

多 repo 佈局（v2）：REPOS_ROOT/<repo小寫>/ 一 repo 一顆乾淨 clone，
tool 呼叫帶 repo 參數選 repo。沒帶 repo（或只設 REPO_ROOT）= 舊版單 repo 行為。

設定：settings().repos（REPOS_ROOT 多 repo 根；REPO_ROOT 單 repo 舊版、已淘汰）
啟動：python -m tools.repo_reader_tools.server   （READER_TOOLS_PORT 預設 8095；舊名 PORT 仍認）
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastmcp import FastMCP

from shared.settings import fail_fast, settings

mcp = FastMCP("repo-reader-tools", mask_error_details=False)

MAX_MATCHES = 100
MAX_READ_LINES = 400


def _root(repo: str | None = None) -> Path:
    cfg = settings().repos
    repos_root = cfg.repos_root
    if repo and repos_root:
        p = (repos_root / repo.strip().lower()).resolve()
        if not p.is_dir():
            raise ValueError(f"REPOS_ROOT 底下沒有這個 repo：{repo!r}")
        return p
    if cfg.repo_root:
        return cfg.repo_root.resolve()
    if repos_root:
        raise ValueError("多 repo 模式需要帶 repo 參數（或另設 REPO_ROOT）")
    raise RuntimeError("環境變數 REPOS_ROOT / REPO_ROOT 皆未設定")


def _safe(rel_path: str, repo: str | None = None) -> Path:
    """把使用者給的相對路徑鎖在 repo 根內。"""
    root = _root(repo)
    p = (root / rel_path.lstrip("/")).resolve()
    if root != p and root not in p.parents:
        raise ValueError(f"路徑越界：{rel_path!r}")
    return p


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None,
              repo: str | None = None) -> str:
    """讀取 repo 內單一檔案（可指定行區間，預設最多 400 行）。"""
    p = _safe(path, repo)
    if not p.is_file():
        return f"# 找不到檔案：{path}"
    lines = p.read_text(errors="replace").splitlines()
    end = min(end_line or (start_line + MAX_READ_LINES - 1), len(lines))
    body = "\n".join(
        f"{i}\t{lines[i - 1]}" for i in range(max(start_line, 1), end + 1)
    )
    return f"# {path} (lines {start_line}-{end} of {len(lines)})\n{body}"


@mcp.tool()
def search_code(pattern: str, glob: str = "**/*",
                repo: str | None = None) -> list[str]:
    """在 repo 內做純文字搜尋，回傳 'path:line:內容'（最多 100 筆）。"""
    root = _root(repo)
    hits: list[str] = []
    needle = pattern.lower()
    for f in root.glob(glob):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            text = f.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # 跳過二進位/讀不了的
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{f.relative_to(root)}:{i}:{line.strip()[:200]}")
                if len(hits) >= MAX_MATCHES:
                    return hits
    return hits


@mcp.tool()
def get_file_history(path: str, limit: int = 10,
                     repo: str | None = None) -> str:
    """回傳單一檔案最近的 git log（--oneline）。"""
    p = _safe(path, repo)
    r = subprocess.run(
        ["git", "-C", str(_root(repo)), "log", f"-n{limit}", "--oneline",
         "--", str(p)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return r.stdout or f"# 無歷史或非 git 檔案：{path}\n{r.stderr}"


if __name__ == "__main__":
    t = fail_fast().tools
    mcp.run(transport="http", host=t.host, port=t.reader_port)
