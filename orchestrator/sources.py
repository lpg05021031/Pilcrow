"""sources —— 餵給 reviewer 的三個 git 資料域（repos / rules / knowledge）的狀態與預覽。

控制台 Sources 頁要回答的問題只有一個：「reviewer 等一下看到的東西新不新、對不對？」
所以這裡的每個函式都是讀狀態或模擬一次注入，不改任何東西；唯一會寫的是 add_repo
（git clone 一顆新 clone 到 REPOS_ROOT），而且它跑完立刻就是 repo-sync job 的管轄範圍。

全部同步、會跑 git 子行程；路由端用 asyncio.to_thread。
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.settings import settings


def _git(path: Path, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 127, f"{type(e).__name__}: {e}"


def _head(path: Path) -> dict[str, Any]:
    """{sha, date, subject, branch} 或 {error}。"""
    rc, out = _git(path, "log", "-1", "--format=%h%x09%cs%x09%s")
    if rc != 0:
        return {"error": out[:200] or "not a git repo"}
    sha, date, subject = (out.split("\t", 2) + ["", ""])[:3]
    _rc, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    return {"sha": sha, "date": date, "subject": subject[:80], "branch": branch}


def _last_touch(path: Path) -> float | None:
    """上次 pull / fetch 的時間：.git/FETCH_HEAD 的 mtime（沒有就 HEAD 的）。"""
    for name in ("FETCH_HEAD", "HEAD"):
        p = path / ".git" / name
        if p.exists():
            return p.stat().st_mtime
    return None


@dataclass
class SourceView:
    kind: str                       # repos | rules | knowledge
    root: str | None
    exists: bool
    head: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ---------- repos ----------

def repos_view() -> SourceView:
    s = settings()
    root = s.repos.repos_root
    if root is None:
        return SourceView("repos", None, False, note="REPOS_ROOT 未設：review 照跑，但沒有 base 上下文")
    if not root.is_dir():
        return SourceView("repos", str(root), False, note="REPOS_ROOT 指向不存在的目錄")
    items = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if not (d / ".git").exists():
            items.append({"name": d.name, "git": False, "note": "非 git clone；repo-sync 略過"})
            continue
        h = _head(d)
        _rc, dirty = _git(d, "status", "--porcelain", "--untracked-files=no")
        items.append({"name": d.name, "git": True, **h,
                      "dirty": bool(dirty.strip()) if _rc == 0 else None,
                      "last_touch": _last_touch(d)})
    return SourceView("repos", str(root), True, items=items,
                      counts={"clones": sum(1 for i in items if i.get("git")),
                              "others": sum(1 for i in items if not i.get("git"))})


_URL_OK = re.compile(r"^https?://[^\s]+$")
_NAME_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def add_repo(name: str, url: str, branch: str) -> list[str]:
    """clone 一顆新 repo 到 REPOS_ROOT/<name>。PAT 走 RUNBOOK 3.3 的方案 A（進 URL；
    .git/config 只有服務帳號讀得到，內網可接受）。回每步一行。"""
    s = settings()
    root = s.repos.repos_root
    name = name.strip().lower()
    if root is None:
        return ["REPOS_ROOT 未設，無處可 clone"]
    if not _NAME_OK.match(name):
        return [f"名字不合法：{name!r}（小寫、數字、. _ -；必須等於 PR URL 裡 _git/<這段> 的小寫）"]
    if not _URL_OK.match(url.strip()):
        return ["clone URL 要是 http(s)://…"]
    dest = root / name
    if dest.exists():
        return [f"{dest} 已存在"]
    root.mkdir(parents=True, exist_ok=True)
    pat = s.ado.pat.get_secret_value()
    auth_url = url.strip()
    if pat and "@" not in auth_url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = auth_url.split("://", 1)
        auth_url = f"{scheme}://anything:{pat}@{rest}"
    cmd = ["git", "clone", "--quiet"]
    if branch.strip():
        cmd += ["--branch", branch.strip()]
    cmd += [auth_url, str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:  # noqa: BLE001
        return [f"clone 例外：{type(e).__name__}: {e}"]
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip().replace(pat, "***") if pat else (r.stderr or r.stdout).strip()
        return [f"clone 失敗：{err[:300]}"]
    h = _head(dest)
    return [f"{name} -> {h.get('sha', '?')}（{h.get('branch', '?')}）",
            "之後由 repo-sync job 保鮮；PR URL 裡 repo 名小寫要等於這個目錄名"]


# ---------- rules ----------

def rules_view() -> SourceView:
    from orchestrator.rules import _git_sha
    s = settings()
    root = s.rules.root
    if root is None:
        return SourceView("rules", None, False, note="RULES_ROOT 未設：規則功能停用")
    if not root.is_dir():
        return SourceView("rules", str(root), False, note="RULES_ROOT 指向不存在的目錄")
    sub = root / s.rules.subdir
    items = []
    for f in sorted(sub.rglob("*.md")) if sub.is_dir() else []:
        rel = f.relative_to(root).as_posix()
        scope = rel.split("/")[1] if rel.count("/") >= 2 else "?"
        items.append({"path": rel, "scope": scope, "chars": f.stat().st_size,
                      "kind": "common" if scope == s.rules.common_dir else "repo"})
    semgrep = [f.relative_to(root).as_posix() for pat in ("**/*.semgrep.yml", "**/*.semgrep.yaml")
               for f in sorted(root.glob(pat))]
    repos = sorted({i["scope"] for i in items if i["kind"] == "repo"})
    return SourceView("rules", str(root), True, head={"sha": _git_sha(root), **_head(root)},
                      items=items + [{"path": p, "scope": "semgrep", "chars": None, "kind": "semgrep"} for p in semgrep],
                      counts={"md": len(items), "semgrep": len(semgrep), "common": sum(1 for i in items if i["kind"] == "common"),
                              "repos": len(repos)},
                      note=("找不到 " + (sub / s.rules.common_dir).as_posix() + "（RULES_SUBDIR / RULES_COMMON_DIR 對不上）"
                            if not (sub / s.rules.common_dir).is_dir() else ""))


def rules_preview(repo: str) -> dict[str, Any]:
    """模擬一次 review 對這個 repo 會載入哪些規則（真的呼叫 load_rules）。"""
    from orchestrator.rules import load_rules
    b = load_rules(repo or None)
    return {"repo": repo, "loaded": b.loaded, "sha": b.sha, "files": b.files,
            "chars": len(b.text), "truncated": b.truncated,
            "cap": settings().rules.max_chars,
            "sections": [{"path": k, "chars": len(v)} for k, v in b.sections.items()]}


# ---------- knowledge ----------

def knowledge_view() -> SourceView:
    s = settings()
    root = s.knowledge.root
    if root is None:
        return SourceView("knowledge", None, False, note="KNOWLEDGE_ROOT 未設：K 段停用")
    if not root.is_dir():
        return SourceView("knowledge", str(root), False, note="KNOWLEDGE_ROOT 指向不存在的目錄")
    items = []
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        if any(part.startswith("_") for part in rel.parts):
            continue
        body = f.read_text(encoding="utf-8", errors="replace")
        items.append({"path": rel.as_posix(), "repo": rel.parts[0] if len(rel.parts) > 1 else "?",
                      "invariants": body.count("\n- ") if "## Invariants" in body else 0,
                      "retired": body.count("\n- ~~"), "chars": len(body)})
    pend = root / "_pending"
    pending = sorted(pend.rglob("pr-*.json")) if pend.is_dir() else []
    cyc = root / "_cycle"
    staged = sorted(cyc.rglob("pr-*.json")) if cyc.is_dir() else []
    return SourceView("knowledge", str(root), True, head=_head(root), items=items,
                      counts={"pages": len(items), "pending": len(pending), "staged": len(staged),
                              "repos": len({i["repo"] for i in items})})


def knowledge_preview(repo: str, paths: list[str]) -> dict[str, Any]:
    """模擬一次 review 對 (repo, 變更檔) 會注入哪些條目（真的呼叫 load_for）。"""
    from orchestrator.knowledge import load_for
    text, stats = load_for(repo or None, paths)
    return {"repo": repo, "paths": paths, "chars": len(text), "stats": stats, "text": text,
            "cap": settings().knowledge.max_chars}


def all_views() -> dict[str, SourceView]:
    return {"repos": repos_view(), "rules": rules_view(), "knowledge": knowledge_view()}


def now() -> float:
    return time.time()
