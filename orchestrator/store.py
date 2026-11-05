"""store —— review 歷史（SQLite，標準庫）。

為什麼要有它：今天「剛才那條 review 發生了什麼」只能去 Langfuse 翻 trace 或翻
journal。控制台的 Reviews 頁要在本機就能回答：哪條 PR、幾分鐘前、v1 還是 v2、
幾條 finding、注入了哪些 rules / knowledge、報告長什麼樣、trace 連結在哪。

一張表，一個檔（DATA_DIR/reviews.db），WAL 模式，每次呼叫開關連線（行程內多執行緒
安全）。DATA_DIR 開不出來（權限、不存在且建不了）= store 停用、review 照跑：歷史是
加分項，不是依賴。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from shared.settings import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_url      TEXT NOT NULL,
  repo        TEXT,
  pr_id       INTEGER,
  title       TEXT,
  user        TEXT,
  status      TEXT NOT NULL,            -- running | done | error
  started     REAL NOT NULL,
  finished    REAL,
  pipeline    TEXT,
  n_error     INTEGER DEFAULT 0,
  n_warning   INTEGER DEFAULT 0,
  n_info      INTEGER DEFAULT 0,
  findings    TEXT,                     -- JSON list
  stats       TEXT,                     -- JSON dict（含 rules_files / knowledge_injected …）
  markdown    TEXT,
  error       TEXT,
  trace_id    TEXT
);
CREATE INDEX IF NOT EXISTS reviews_started ON reviews(started DESC);
"""

_disabled_reason: str = ""


def db_path() -> Path:
    return settings().orchestrator.data_dir / "reviews.db"


def enabled() -> bool:
    return not _disabled_reason


def disabled_reason() -> str:
    return _disabled_reason


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    global _disabled_reason
    p = db_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(p, timeout=5.0)
    except (OSError, sqlite3.Error) as e:
        _disabled_reason = f"{p.parent}: {type(e).__name__}: {e}"
        raise
    _disabled_reason = ""
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def _safe(fn, default):
    """store 是加分項：任何失敗回 default、記下原因，絕不讓 review 炸。"""
    try:
        return fn()
    except (OSError, sqlite3.Error):
        return default


# ---------- 寫 ----------

def start_review(pr_url: str, user: str, meta: dict | None = None) -> int | None:
    from shared.azdo import parse_pr_url
    try:
        ref = parse_pr_url(pr_url)
        repo, pr_id = ref.repo, ref.pr_id
    except ValueError:
        repo, pr_id = None, None
    meta = meta or {}

    def go():
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO reviews (pr_url, repo, pr_id, title, user, status, started, pipeline)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (pr_url, repo, pr_id, (meta.get("title") or "")[:200], user, "running",
                 time.time(), settings().orchestrator.pipeline))
            return cur.lastrowid
    return _safe(go, None)


def set_title(review_id: int | None, title: str) -> None:
    if review_id is None:
        return
    _safe(lambda: _exec("UPDATE reviews SET title=? WHERE id=?", ((title or "")[:200], review_id)), None)


def finish_review(review_id: int | None, result, trace_id: str | None = None) -> None:
    if review_id is None:
        return
    findings = list(getattr(result, "findings", None) or [])
    stats = dict(getattr(result, "stats", None) or {})
    sev = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        s = getattr(f, "severity", "info")
        sev[s if s in sev else "info"] += 1
    dump = getattr(result, "findings_summary", None)
    summary_list = dump() if callable(dump) else [
        f.model_dump() if hasattr(f, "model_dump") else dict(vars(f)) for f in findings]
    _safe(lambda: _exec(
        "UPDATE reviews SET status='done', finished=?, pipeline=?, n_error=?, n_warning=?, n_info=?,"
        " findings=?, stats=?, markdown=?, trace_id=? WHERE id=?",
        (time.time(), stats.get("pipeline"), sev["error"], sev["warning"], sev["info"],
         json.dumps(summary_list, ensure_ascii=False, default=str),
         json.dumps(stats, ensure_ascii=False, default=str),
         getattr(result, "markdown", ""), trace_id, review_id)), None)


def fail_review(review_id: int | None, error: str) -> None:
    if review_id is None:
        return
    _safe(lambda: _exec("UPDATE reviews SET status='error', finished=?, error=? WHERE id=?",
                        (time.time(), error[:2000], review_id)), None)


def _exec(sql: str, params: tuple) -> None:
    with _conn() as c:
        c.execute(sql, params)


# ---------- 讀 ----------

def _row(r: sqlite3.Row, full: bool = False) -> dict[str, Any]:
    d = dict(r)
    for k in ("findings", "stats"):
        if full and d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = None
        elif not full:
            d.pop(k, None)
    if not full:
        d.pop("markdown", None)
    d["duration"] = (d["finished"] - d["started"]) if d.get("finished") else None
    return d


def list_reviews(limit: int = 50) -> list[dict]:
    def go():
        with _conn() as c:
            rows = c.execute("SELECT * FROM reviews ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
            return [_row(r) for r in rows]
    return _safe(go, [])


def get_review(review_id: int) -> dict | None:
    def go():
        with _conn() as c:
            r = c.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            return _row(r, full=True) if r else None
    return _safe(go, None)


def summary(days: int = 7) -> dict[str, Any]:
    """Overview 用：近 N 天幾條、失敗幾條、平均秒數、平均 finding 數。"""
    since = time.time() - days * 86400

    def go():
        with _conn() as c:
            r = c.execute(
                "SELECT COUNT(*) n, SUM(status='error') errs, AVG(finished-started) secs,"
                " AVG(n_error+n_warning+n_info) finds FROM reviews WHERE started>=?", (since,)).fetchone()
            return {"days": days, "n": r["n"] or 0, "errors": r["errs"] or 0,
                    "avg_seconds": r["secs"], "avg_findings": r["finds"]}
    return _safe(go, {"days": days, "n": 0, "errors": 0, "avg_seconds": None, "avg_findings": None})
