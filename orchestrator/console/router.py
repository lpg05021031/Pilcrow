"""控制台路由。頁面：/console（Overview）、/console/settings、/console/login。

Settings 頁的 Apply 走 POST /console/settings/{group}：
  表單值 → settings.validate_group_values（純 schema 驗證，壞值回表單原地紅字）
  → 危險旋鈕翻向危險側要勾確認 → settings.save_toml（備份、原子寫、change log、reset）
  → 回該組的局部 HTML（console.js 原地替換）。
被環境變數覆寫的欄位是唯讀的：env 永遠贏，改檔無效，頁面直說。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import SecretStr, ValidationError

from orchestrator import jobs, ledger, sources, store
from orchestrator.adapters import ado
from orchestrator.console import auth, status as stat
from orchestrator.console.labels import LABELS
from shared import doctor
from shared import settings as S

router = APIRouter(prefix="/console", include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["datetime"] = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts))

# 依維運者的任務分四頁；sources / setup 仍有路由，從 設定 與 狀態 頁進，不佔導覽。
NAV = [("status", "狀態", "/console"),
       ("reviews", "審查", "/console/reviews"),
       ("knowledge", "知識", "/console/knowledge"),
       ("settings", "設定", "/console/settings")]

# 設定頁依任務分段（不是依模組）：維運者找的是「連線」「發文開關」，不是 ado / posting
TASKS = [
    {"title": "連線", "why": "LLM gateway、Langfuse、Azure DevOps、服務本體。secrets（PAT、key）只在 secrets.env。",
     "groups": ["llm", "langfuse", "ado", "orchestrator"]},
    {"title": "對外發文", "why": "三顆會真的寫到 PR 的開關，出廠全安全側；翻向真發側要勾確認。",
     "groups": ["posting"]},
    {"title": "審查調參", "why": "v2 pipeline 的科數、併發、信心門檻、切片預算，以及靜態掃描。改任何一顆先過 eval。",
     "groups": ["review", "static"]},
    {"title": "來源", "why": "本地 clone、團隊規則、知識帳本、code graph 的路徑與開關。",
     "groups": ["repos", "rules", "knowledge", "graph"], "link": "/console/sources", "link_label": "看來源狀態與預覽注入"},
    {"title": "自動化", "why": "行程內排程的間隔；0 = 關。", "groups": ["jobs"]},
    {"title": "評測", "why": "CI gate 門檻。未設 = 只報數不擋。", "groups": ["eval"]},
]

# 翻向這一側要勾「我確定」：會對外真發、或關掉安全氣囊
DANGEROUS = {("orchestrator", "auto_post"): True,
             ("posting", "dry_run"): False,
             ("posting", "knowledge_post"): True}


def _ctx(request: Request, page: str, **extra: Any) -> dict:
    return {"request": request, "page": page, "nav": NAV,
            "open_mode": auth.is_open(), "warnings": S.warnings_for(), **extra}


# ---------- 登入 ----------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/console", error: str = ""):
    if auth.is_open():
        return RedirectResponse(next, status_code=302)
    return templates.TemplateResponse(request, "login.html",
                                      _ctx(request, "login", next=next, error=error))


@router.post("/login")
async def login(request: Request, token: str = Form(""), next: str = Form("/console")):
    if not auth.token_ok(token):
        return RedirectResponse("/console/login?error=1", status_code=302)
    resp = RedirectResponse(next if next.startswith("/console") else "/console", status_code=302)
    resp.set_cookie(auth.COOKIE, auth.expected_cookie(), httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600)
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/console/login", status_code=302)
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---------- Overview ----------

_doctor_cache: dict[str, Any] = {"ts": 0.0, "checks": []}
DOCTOR_TTL_S = 30.0


async def _doctor_checks(force: bool = False) -> list[doctor.Check]:
    if force or time.time() - _doctor_cache["ts"] > DOCTOR_TTL_S:
        checks, _code = await asyncio.to_thread(doctor.run, network=True, pr_url=None)
        _doctor_cache.update(ts=time.time(), checks=checks)
    return _doctor_cache["checks"]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def overview(request: Request, refresh: int = 0):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    checks = await _doctor_checks(force=bool(refresh))
    groups: dict[str, list[doctor.Check]] = {}
    for c in checks:
        groups.setdefault(c.group, []).append(c)
    counts = {k: sum(1 for c in checks if c.status == k)
              for k in (doctor.OK, doctor.WARN, doctor.FAIL, doctor.SKIP)}
    views = await asyncio.to_thread(sources.all_views)
    js = jobs.snapshot()
    reviews = store.list_reviews(15)
    audit = list(ado.audit_log)[-15:]
    return templates.TemplateResponse(request, "status.html", _ctx(
        request, "status", groups=groups, counts=counts,
        hl=stat.headline(checks), blockers=stat.blockers(checks), healthy=stat.healthy(checks),
        feed=stat.feed(reviews, audit, js, S.recent_changes(10)), ago=stat.ago,
        checked_at=_doctor_cache["ts"], jobs=js, now=time.time(),
        sources=views, reviews=reviews[:8], summary=store.summary(7),
        config_exists=S.config_path().exists()))


@router.post("/jobs/{name}/run")
async def run_job(request: Request, name: str, back: str = "/console?refresh=1"):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    if name in jobs.JOBS:
        asyncio.get_running_loop().create_task(jobs.run_once(name))
    return RedirectResponse(back if back.startswith("/console") else "/console", status_code=303)


# ---------- Sources ----------

@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, rules_repo: str = "", k_repo: str = "", k_paths: str = ""):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    views = await asyncio.to_thread(sources.all_views)
    rp = await asyncio.to_thread(sources.rules_preview, rules_repo) \
        if (rules_repo or "rules_repo" in request.query_params) and views["rules"].exists else None
    paths = [p.strip() for p in k_paths.split(",") if p.strip()]
    kp = await asyncio.to_thread(sources.knowledge_preview, k_repo, paths) \
        if k_repo and paths and views["knowledge"].exists else None
    return templates.TemplateResponse(request, "sources.html", _ctx(
        request, "sources", v=views, jobs=jobs.snapshot(), now=time.time(),
        rules_repo=rules_repo, rp=rp, k_repo=k_repo, k_paths=k_paths, kp=kp,
        add_result=request.query_params.getlist("added")))


@router.post("/sources/repos/add", response_class=HTMLResponse)
async def sources_add_repo(request: Request, name: str = Form(""), url: str = Form(""),
                           branch: str = Form("")):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    lines = await asyncio.to_thread(sources.add_repo, name, url, branch or "main")
    from urllib.parse import urlencode
    return RedirectResponse("/console/sources?" + urlencode([("added", ln) for ln in lines]) + "#repos",
                            status_code=303)


# ---------- Reviews ----------

@router.get("/reviews", response_class=HTMLResponse)
async def reviews_page(request: Request, error: str = ""):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    rows = store.list_reviews(50)
    return templates.TemplateResponse(request, "reviews.html", _ctx(
        request, "reviews", reviews=rows, summary=store.summary(7), now=time.time(),
        store_ok=store.enabled(), store_reason=store.disabled_reason(),
        auto_post=S.settings().orchestrator.auto_post, run_error=error))


@router.post("/reviews/run")
async def reviews_run(request: Request, pr_url: str = Form("")):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    from shared.azdo import find_pr_url
    url = find_pr_url(pr_url)
    if not url:
        return RedirectResponse("/console/reviews?error=不是 PR URL（要含 /_git/<repo>/pullrequest/<id>）",
                                status_code=303)
    from orchestrator import app as A       # 延遲 import：app 先 import console
    asyncio.get_running_loop().create_task(_run_and_log(A.handle_review, url))
    await asyncio.sleep(0.2)                # 讓 start_review 先落帳，列表第一列就是它
    return RedirectResponse("/console/reviews", status_code=303)


async def _run_and_log(handle, url: str) -> None:
    try:
        await handle(url, "console")
    except Exception as e:  # noqa: BLE001  —— 已由 handle_review 落帳為 error
        print(f"[console] review {url} 失敗：{type(e).__name__}: {e}")


# ---------- Knowledge ----------

@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request, digest_repo: str = "", msg: str = ""):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    pend, stg, cyc, verd = await asyncio.to_thread(
        lambda: (ledger.pending(), ledger.staged(), ledger.cycle_state(), ledger.verdicts()))
    dig = await asyncio.to_thread(ledger.digest_preview, digest_repo) if digest_repo else ""
    return templates.TemplateResponse(request, "knowledge.html", _ctx(
        request, "knowledge", root=ledger._root() is not None, pending=pend, staged=stg,
        cycle=cyc, verdicts=verd, digest=dig, digest_repo=digest_repo, msg=msg,
        job=jobs.snapshot().get("harvest"), now=time.time()))


@router.post("/knowledge/verdict")
async def knowledge_verdict(request: Request, repo: str = Form(""), kind: str = Form(""),
                            token: str = Form("")):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    who = request.client.host if request.client else "console"
    msg = ledger.add_verdict(repo, kind, token, by=who)
    from urllib.parse import quote
    return RedirectResponse(f"/console/knowledge?msg={quote(msg)}", status_code=303)


@router.post("/knowledge/verdict/remove")
async def knowledge_verdict_remove(request: Request, repo: str = Form(""), kind: str = Form(""),
                                   token: str = Form("")):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    msg = ledger.remove_verdict(repo, kind, token)
    from urllib.parse import quote
    return RedirectResponse(f"/console/knowledge?msg={quote(msg)}", status_code=303)


# ---------- Setup 精靈 ----------

SETUP_STEPS = [
    {"key": "llm", "title": "LLM gateway", "groups": ["llm"], "doctor": [("llm", None)],
     "why": "reviewer / planner / adversary 都打它；LLM_MODEL 要在 gateway 的清單裡。",
     "secrets": ["LLM_API_KEY"]},
    {"key": "langfuse", "title": "Langfuse", "groups": ["langfuse"], "doctor": [("langfuse", None)],
     "why": "trace、prompt 版控、eval 全靠它；key 對不上這個 host 就全暗。",
     "secrets": ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]},
    {"key": "ado", "title": "Azure DevOps", "groups": ["ado", "posting"], "doctor": [("ado", None)],
     "why": "抓 PR 與回貼都用 ADO_PAT；DRY_RUN / KNOWLEDGE_POST 出廠安全側，確認要真發再翻。",
     "secrets": ["ADO_PAT"]},
    {"key": "service", "title": "服務本體", "groups": ["orchestrator", "jobs"],
     "doctor": [("settings", None), ("python", None), ("services", None), ("jobs", None), ("systemd", None)],
     "why": "行程在不在、依賴齊不齊、排程有沒有在跑。ORCH_API_KEY 設了控制台才要登入。",
     "secrets": ["ORCH_API_KEY"]},
    {"key": "repos", "title": "本地 clone（base 上下文）", "groups": ["repos"], "doctor": [("paths", "REPOS_ROOT")],
     "why": "沒有也能 review，但 reviewer 看不到變更周邊；在 Sources 頁可以直接 clone。",
     "link": "/console/sources#repos", "link_label": "Sources → repos"},
    {"key": "rules", "title": "團隊規則", "groups": ["rules", "static"], "doctor": [("paths", "RULES_ROOT")],
     "why": "RULES_ROOT 指向規則 repo 的 clone；semgrep 規則也從這裡來。", "link": "/console/sources#rules", "link_label": "Sources → rules"},
    {"key": "knowledge", "title": "知識迴圈（選配）", "groups": ["knowledge"],
     "doctor": [("paths", "KNOWLEDGE_ROOT"), ("paths", "DATA_DIR")],
     "why": "KNOWLEDGE_ROOT 設了才會蒸餾、注入、收割；DATA_DIR 是 review 歷史。",
     "link": "/console/knowledge", "link_label": "Knowledge"},
]

_RANK = {"OK": 0, "SKIP": 0, "WARN": 1, "FAIL": 2}


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, refresh: int = 0):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    checks = await _doctor_checks(force=bool(refresh))
    steps = []
    for st in SETUP_STEPS:
        mine = [c for c in checks for (g, n) in st["doctor"] if c.group == g and (n is None or c.name == n)]
        worst = max((_RANK.get(c.status, 0) for c in mine), default=0)
        status = {0: "OK", 1: "WARN", 2: "FAIL"}[worst]
        if mine and all(c.status == "SKIP" for c in mine):
            status = "SKIP"
        steps.append({**st, "checks": mine, "status": status})
    return templates.TemplateResponse(request, "setup.html", _ctx(
        request, "setup", steps=steps, config_path=str(S.config_path()),
        config_exists=S.config_path().exists()))


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
async def review_detail(request: Request, review_id: int):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    r = store.get_review(review_id)
    if not r:
        return HTMLResponse("not found", status_code=404)
    return templates.TemplateResponse(request, "review_detail.html", _ctx(
        request, "reviews", r=r, now=time.time(),
        langfuse_host=S.settings().langfuse.host.rstrip("/")))


# ---------- Settings ----------

def _control(info) -> str:
    """型別 → 控件種類（模板用）。"""
    ann = info.annotation
    if ann is SecretStr:
        return "secret"
    if ann is bool:
        return "bool"
    meta = getattr(ann, "__metadata__", None)
    origin = getattr(ann, "__origin__", None)
    base = getattr(ann, "__args__", (ann,))[0] if meta else ann
    if base is bool:
        return "bool"
    if getattr(base, "__origin__", None) is not None and "Literal" in str(base):
        return "choice"
    if "Literal" in str(ann):
        return "choice"
    if base in (int, float) or ann in (int, float):
        return "number"
    if origin is list or base is list or "list[" in str(ann):
        return "csv"
    return "text"


def _choices(info) -> list[str]:
    import typing
    for t in (info.annotation, *getattr(info.annotation, "__args__", ())):
        if typing.get_origin(t) is typing.Literal:
            return [str(x) for x in typing.get_args(t)]
    return []


def _group_view(gname: str, errors: dict[str, str] | None = None,
                posted: dict[str, str] | None = None) -> dict:
    s = S.settings()
    gcls = S.Settings.model_fields[gname].annotation
    grp = getattr(s, gname)
    fields = []
    for fname, info in gcls.model_fields.items():
        v = getattr(grp, fname)
        src = S.source_of(gname, fname, info)
        kind = _control(info)
        shown = (posted or {}).get(fname)
        if shown is None:
            shown = "" if v is None else (",".join(v) if isinstance(v, list)
                                          else ("true" if v is True else "false" if v is False
                                                else str(v)))
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        desc = (info.description or "").strip()
        env = S.env_names(info)[0]
        default_native = S._native(info.get_default(call_default_factory=True))
        fields.append({
            "label": LABELS.get(env, env), "more": desc,
            "default_raw": S._render_value(default_native),
            "name": fname, "env": S.env_names(info)[0], "kind": kind,
            "choices": _choices(info), "value": shown,
            "masked": S._mask(v) if kind == "secret" else None,
            "source": src, "locked": src.startswith("env:") or kind == "secret",
            "description": info.description or "",
            "default": S._render_value(S._native(info.get_default(call_default_factory=True))) or "(unset)",
            "dangerous": (gname, fname) in DANGEROUS,
            "min": getattr(info, "metadata", None) and next((m.ge for m in info.metadata if hasattr(m, "ge")), None),
            "max": getattr(info, "metadata", None) and next((m.le for m in info.metadata if hasattr(m, "le")), None),
            "error": (errors or {}).get(fname, ""),
            "required": bool(extra.get("required")),
        })
    return {"name": gname, "doc": (gcls.__doc__ or "").strip(), "fields": fields,
            "saved": False}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    views = {g: _group_view(g) for g in S.GROUP_ORDER}
    tasks = []
    for t in TASKS:
        gs = [views[g] for g in t["groups"]]
        tasks.append({**t, "groups": gs,
                      "modified": sum(1 for g in gs for f in g["fields"] if f["source"] == "file")})
    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, "settings", tasks=tasks, groups=list(views.values()),
        config_path=str(S.config_path()),
        config_exists=S.config_path().exists(), changes=S.recent_changes(20)))


@router.post("/settings/{gname}", response_class=HTMLResponse)
async def settings_apply(request: Request, gname: str):
    if not auth.is_authed(request):
        return auth.login_redirect(request)
    if gname not in S.GROUP_ORDER:
        return HTMLResponse("unknown group", status_code=404)
    form = await request.form()
    gcls = S.Settings.model_fields[gname].annotation
    posted: dict[str, str] = {}
    changes: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for fname, info in gcls.model_fields.items():
        if info.annotation is SecretStr or S.source_of(gname, fname, info).startswith("env:"):
            continue                       # 唯讀：secret 走 secrets.env；env 覆寫改檔無效
        kind = _control(info)
        if kind == "bool":
            if f"_b_{fname}" not in form:
                continue                   # checkbox 沒隨表單送來 = 這欄沒在這次表單裡，不動
            raw = "true" if form.get(fname) in ("on", "true", "1") else "false"
        else:
            raw = str(form.get(fname, "")).strip()
        posted[fname] = raw
        default = S._native(info.get_default(call_default_factory=True))
        if raw == "" or raw == "(unset)":
            changes[fname] = None          # 回預設 / 刪鍵
            continue
        if kind == "bool":
            val: Any = raw == "true"
        elif kind == "csv":
            val = [x.strip() for x in raw.split(",") if x.strip()]
        elif kind == "number":
            try:
                val = int(raw) if info.annotation is int or "int" in str(info.annotation) else float(raw)
            except ValueError:
                errors[fname] = "要是數字"
                continue
        else:
            val = raw
        try:                               # 先經 schema 正規化（"true" → True、路徑、清單）再比
            val = S.validate_group_values(gname, {fname: val})[fname]
        except ValidationError as e:
            errors[fname] = e.errors()[0]["msg"]
            continue
        if val == default and fname not in (S.load_toml().get(gname) or {}):
            continue                       # 等於預設且檔裡本來沒有 → 不寫
        changes[fname] = val
        if (gname, fname) in DANGEROUS and val == DANGEROUS[(gname, fname)] \
                and form.get(f"confirm_{fname}") != "on":
            errors[fname] = "翻到這一側會對外真發 / 關掉安全氣囊，要勾「我確定」"
    view: dict
    if errors:
        view = _group_view(gname, errors=errors, posted=posted)
    else:
        log = S.save_toml({gname: changes},
                          actor=request.client.host if request.client else "console") \
            if changes else []
        view = _group_view(gname)
        view["saved"] = True
        view["applied"] = log
        view["restart_needed"] = gname == "orchestrator" and any(
            e["field"] in ("url",) for e in log)
    return templates.TemplateResponse(request, "_group.html", {
        "request": request, "g": view, "warnings": S.warnings_for(),
        "changes": S.recent_changes(20)})
