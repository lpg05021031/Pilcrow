"""status —— 把「檢查結果」翻成維運者要的三個答案：現在能不能審？要處理什麼？最近發生什麼？

doctor 回的是一張表（group / name / OK|WARN|FAIL|SKIP / detail），那是工程師的視角。
維運者打開首頁要的是：
  1. 一句結論：可以審查 / 可以審查但有狀況 / 現在無法審查，加一句為什麼。
  2. 需要處理的清單：每一項用人話說是什麼問題、嚴重到什麼程度、按哪裡去處理。
     正常的項目收起來，不佔版面。
  3. 最近活動：review、對 ADO 的寫入、排程、設定改動合成一條時間軸。
"""
from __future__ import annotations

import time
from typing import Any

from shared import doctor

# FAIL 在這幾組 = 這一刻一條 review 都跑不了
BLOCKING_GROUPS = {"settings", "services", "llm", "ado", "python"}

# (group, name) → (人話標題, 動作標籤, 動作連結)。沒列的用 fallback。
_HUMAN: dict[tuple[str, str], tuple[str, str, str]] = {
    ("settings", "parse"): ("設定值有誤，服務用的是壞掉的設定", "去改設定", "/console/settings"),
    ("settings", "smell"): ("設定有互相矛盾或已無作用的值", "去改設定", "/console/settings"),
    ("python", "interpreter"): ("Python 版本太舊", "", ""),
    ("python", "dependencies"): ("缺少 Python 套件", "", ""),
    ("python", "semgrep"): ("semgrep 不在 PATH，靜態掃描會跳過", "靜態掃描設定", "/console/settings#g-static"),
    ("python", "ruff"): ("ruff 不在 PATH，Python 靜態掃描會跳過", "靜態掃描設定", "/console/settings#g-static"),
    ("paths", "REPOS_ROOT"): ("本地 clone 目錄有問題", "來源設定", "/console/settings#g-repos"),
    ("paths", "RULES_ROOT"): ("團隊規則目錄有問題", "來源設定", "/console/settings#g-rules"),
    ("paths", "KNOWLEDGE_ROOT"): ("知識帳本目錄有問題", "來源設定", "/console/settings#g-knowledge"),
    ("paths", "DATA_DIR"): ("review 歷史目錄不可寫，歷史停用", "服務設定", "/console/settings#g-orchestrator"),
    ("services", "orchestrator"): ("服務本體沒回應", "", ""),
    ("services", "code graph ④"): ("code graph 連不上，影響半徑會缺席", "graph 設定", "/console/settings#g-graph"),
    ("jobs", "repo-sync"): ("repo-sync 排程上次失敗", "看明細", "/console/sources#repos"),
    ("jobs", "harvest"): ("知識收割排程上次失敗", "看明細", "/console/knowledge"),
    ("langfuse", "health"): ("Langfuse 連不上，trace 與 prompt 版控全暗", "Langfuse 設定", "/console/settings#g-langfuse"),
    ("langfuse", "keys"): ("Langfuse key 對不上這個 host", "Langfuse 設定", "/console/settings#g-langfuse"),
    ("llm", "gateway"): ("LLM gateway 連不上", "LLM 設定", "/console/settings#g-llm"),
    ("llm", "LLM_MODEL"): ("主模型不在 gateway 的清單裡", "LLM 設定", "/console/settings#g-llm"),
    ("llm", "LLM_MODEL_ADVERSARY"): ("adversary 模型不在 gateway 的清單裡", "LLM 設定", "/console/settings#g-llm"),
    ("llm", "LLM_MODEL_DIGEST"): ("digest 模型不在 gateway 的清單裡", "LLM 設定", "/console/settings#g-llm"),
    ("ado", "ADO_PAT"): ("ADO_PAT 未設，抓不到也發不了 PR", "secrets.env 說明", "/console/settings#g-ado"),
    ("ado", "reach"): ("打不到 Azure DevOps", "ADO 設定", "/console/settings#g-ado"),
    ("systemd", "retired units"): ("舊的 systemd unit 還裝著，會跟行程內排程打架", "", ""),
}

_SEV_RANK = {"FAIL": 0, "WARN": 1}


def headline(checks: list[doctor.Check]) -> dict[str, Any]:
    fails = [c for c in checks if c.status == doctor.FAIL]
    warns = [c for c in checks if c.status == doctor.WARN]
    hard = [c for c in fails if c.group in BLOCKING_GROUPS]
    if hard:
        why = _title(hard[0])
        more = f"，另 {len(hard) - 1} 項" if len(hard) > 1 else ""
        return {"state": "down", "title": "現在無法審查", "why": f"{why}{more}。",
                "fails": len(fails), "warns": len(warns)}
    if fails or warns:
        parts = []
        if fails:
            parts.append(f"{len(fails)} 個故障")
        if warns:
            parts.append(f"{len(warns)} 個警告")
        return {"state": "degraded", "title": "可以審查，但有狀況", "why": "、".join(parts) + "，review 照跑、部分能力缺席。",
                "fails": len(fails), "warns": len(warns)}
    return {"state": "ok", "title": "可以審查", "why": "所有檢查通過。", "fails": 0, "warns": 0}


def _title(c: doctor.Check) -> str:
    h = _HUMAN.get((c.group, c.name))
    if h:
        return h[0]
    if c.group == "systemd":
        return f"systemd unit {c.name} 不是 active"
    return f"{c.group}：{c.name}"


def blockers(checks: list[doctor.Check]) -> list[dict[str, Any]]:
    """FAIL 與 WARN，按嚴重度、再按「擋不擋 review」排。"""
    out = []
    for c in checks:
        if c.status not in _SEV_RANK:
            continue
        h = _HUMAN.get((c.group, c.name), (_title(c), "", ""))
        out.append({"sev": c.status, "blocking": c.group in BLOCKING_GROUPS and c.status == doctor.FAIL,
                    "title": h[0], "detail": c.detail, "action": h[1], "href": h[2],
                    "group": c.group, "name": c.name})
    out.sort(key=lambda b: (_SEV_RANK[b["sev"]], not b["blocking"]))
    return out


def healthy(checks: list[doctor.Check]) -> list[doctor.Check]:
    return [c for c in checks if c.status in (doctor.OK, doctor.SKIP)]


def feed(reviews: list[dict], audit: list[dict], jobs: dict[str, dict],
         changes: list[dict], limit: int = 20) -> list[dict[str, Any]]:
    """四種事件合成一條時間軸，新的在前。每筆：ts / kind / title / detail / href / tone。"""
    ev: list[dict[str, Any]] = []
    for r in reviews:
        tone = {"done": "ok", "error": "fail"}.get(r["status"], "neutral")
        if r["status"] == "done":
            detail = f"{r['n_error']} error · {r['n_warning']} warning · {r['n_info']} info"
            if r.get("duration"):
                detail += f" · {int(r['duration'])}s"
        elif r["status"] == "error":
            detail = (r.get("error") or "")[:120]
        else:
            detail = "進行中"
        ev.append({"ts": r["started"], "kind": "review", "tone": tone,
                   "title": f"review {r['repo']}#{r['pr_id']}" + (f"：{r['title']}" if r.get("title") else ""),
                   "detail": detail, "href": f"/console/reviews/{r['id']}"})
    for a in audit:
        ev.append({"ts": a["ts"], "kind": "write", "tone": "ok" if a["posted"] else "neutral",
                   "title": ("發出" if a["posted"] else "預覽（未發）") + ("review 留言" if a["kind"] == "review" else "知識稿"),
                   "detail": f"PR {a['pr_url'].rsplit('/', 1)[-1]} · {a['note']}", "href": ""})
    for name, st in jobs.items():
        if st.get("last_end"):
            ev.append({"ts": st["last_end"], "kind": "job", "tone": "ok" if st.get("last_ok") else "fail",
                       "title": f"{name} 跑完" if st.get("last_ok") else f"{name} 失敗",
                       "detail": (st.get("last_error") or f"{len(st.get('last_detail') or [])} 項")[:120],
                       "href": "/console/sources#repos" if name == "repo-sync" else "/console/knowledge"})
    for c in changes:
        ev.append({"ts": c["ts"], "kind": "config", "tone": "neutral",
                   "title": f"改了 {c['env']}", "detail": f"{c['old']} → {c['new']} · {c['actor']}",
                   "href": "/console/settings#changes"})
    ev.sort(key=lambda e: e["ts"], reverse=True)
    return ev[:limit]


def ago(ts: float, now: float | None = None) -> str:
    d = int((now or time.time()) - ts)
    if d < 60:
        return "剛剛"
    if d < 3600:
        return f"{d // 60} 分前"
    if d < 86400:
        return f"{d // 3600} 小時前"
    return f"{d // 86400} 天前"
