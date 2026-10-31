"""App state plus the RPC event wiring (a port of web/src/lib/store.ts).

Widgets read from :data:`store` and call its actions; they never talk to ``rpc`` themselves. The
store emits Qt signals when something changes; transcript models live outside the signalled state
and are referenced by a ``rev`` counter so streaming updates re-render cheaply (one signal per
frame per session, coalesced).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, Qt

from . import theme as theme_mod
from .lib.fmt import err_text, set_home
from .lib.transcript_model import TranscriptModel
from .rpc import Call, RpcError, rpc

Json = Dict[str, Any]

PANEL_TABS = ["changes", "files", "terminal", "preview", "tasks", "history", "info"]
VIEW_MODES = [
    {"id": "normal", "label": "Normal", "hint": "Steps summarised, thinking folded"},
    {"id": "thinking", "label": "Thinking", "hint": "Thinking opened"},
    {"id": "verbose", "label": "Verbose", "hint": "Every step and its output opened"},
]
BUSY = ("running", "starting", "requires_action")


@dataclass
class Runtime:
    """What the client knows about one session: the server's SessionState plus load bookkeeping."""

    rev: int = 0
    loaded: bool = False
    loading: bool = False
    thinking_tokens: int = 0
    meta: Json = field(default_factory=dict)
    st: Json = field(default_factory=dict)  # SessionState as it arrives (status, pending, held, usage, …)

    @property
    def status(self) -> str:
        return str(self.st.get("status") or "")

    @property
    def busy(self) -> bool:
        return self.status in BUSY

    @property
    def pending(self) -> List[Json]:
        return list(self.st.get("pending") or [])

    @property
    def held(self) -> List[Json]:
        return list(self.st.get("held") or [])

    @property
    def live(self) -> bool:
        return bool(self.st.get("live"))

    @property
    def cwd(self) -> str:
        return str(self.st.get("cwd") or "")

    def get(self, key: str, default: Any = None) -> Any:
        return self.st.get(key, default)


LOAD_SLICE = 300  # messages modelled per event-loop turn while a conversation opens


@dataclass
class Ui:
    sidebar: bool = True
    panel: bool = False
    panel_tab: str = "changes"
    sidebar_w: int = 268
    panel_w: int = 560
    show_archived: bool = False
    diff_mode: str = "unified"
    view: str = "normal"
    font_scale: float = 1.0


@dataclass
class Toast:
    id: int
    text: str
    level: str
    action: Optional[Dict[str, Any]] = None  # {"label": str, "run": callable}
    count: int = 1


@dataclass
class ConfirmSpec:
    title: str
    confirm: str
    on_confirm: Callable[[bool], None]
    body: str = ""
    danger: bool = False
    checkbox: Optional[Dict[str, Any]] = None  # {"label": str, "value": bool}
    list: Optional[List[str]] = None


class Store(QObject):
    """The application state. One instance, :data:`store`."""

    connected_changed = Signal(bool)
    info_changed = Signal(object)
    config_changed = Signal(object)
    repos_changed = Signal(object)  # the repositories registry (REPOSITORIES.md), a list of entries
    sessions_changed = Signal(object)  # list of SessionSummary dicts
    active_changed = Signal(object)  # session id or None
    rt_changed = Signal(str)  # session id (coalesced to one per frame)
    page_changed = Signal(str)
    ui_changed = Signal(object)  # Ui
    toasts_changed = Signal(object)  # list[Toast]
    unread_changed = Signal(object)
    dialog_changed = Signal(str, object)  # key, value
    file_open = Signal(object)  # {"path", "line", "nonce"}
    composer_insert = Signal(object)  # {"id", "text", "nonce", "replace"}
    fs_changed = Signal(int)
    timeline_changed = Signal(str)
    sched_changed = Signal(int)
    inbox_unread_changed = Signal(int)
    dispatch_changed = Signal(object)  # {"id": job id, "removed"?: bool} per change; None after a full reload
    dispatch_view_changed = Signal(object)  # None = the board, "new" = the composer, else a job id
    playbooks_changed = Signal(object)  # the list of playbooks
    gateway_models_changed = Signal(object)
    lightbox_changed = Signal(object)
    find_open_changed = Signal(bool)
    permission_asked = Signal(str, object)  # session id, request
    notify = Signal(str, str, str)  # session id, title, body
    attention = Signal()
    sent = Signal(str)  # a message went out for this session: the transcript goes to its live end

    def __init__(self) -> None:
        super().__init__()
        self.connected = False
        self.ever_connected = False
        self.info: Optional[Json] = None
        self.config: Optional[Json] = None
        self.sessions: List[Json] = []
        self.sessions_loaded = False
        self.active_id: Optional[str] = None
        self.rt: Dict[str, Runtime] = {}
        self.ui = Ui()
        self.dialogs: Dict[str, Any] = {
            "newSession": None,
            "settings": False,
            "palette": False,
            "shortcuts": False,
            "confirm": None,
            "mcpAuth": None,
            "schedule": None,
        }
        self.toasts: List[Toast] = []
        self.fs_rev = 0
        self.unread: Dict[str, bool] = {}
        self.page = "session"
        self.inbox_unread = 0
        self.sched_rev = 0
        # dispatch: job summaries and their runs, kept in line by dispatch.changed deltas
        self.jobs: Dict[str, Json] = {}
        self.job_runs: Dict[str, Dict[str, Json]] = {}
        self.jobs_loaded = False
        self.dispatch_view: Optional[str] = None
        self.playbooks: List[Json] = []
        self.gateway_models: Any = None  # None | "loading" | GatewayModels dict
        self.timeline: Dict[str, List[Json]] = {}
        self.lightbox: Optional[str] = None
        self.find_open = False
        self.reduced_motion = False
        self.models: Dict[str, TranscriptModel] = {}
        self._dirty: set = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush_dirty)
        self._list_timer = QTimer(self)
        self._list_timer.setSingleShot(True)
        self._list_timer.timeout.connect(self._fetch_sessions)
        self._list_force = False
        self._list_waiters: List[Callable[[], None]] = []
        self._buffered: Dict[str, List[Json]] = {}
        self._opening: set = set()
        self._toast_seq = 0
        self._toast_timers: Dict[int, QTimer] = {}
        self._ui_save_timer = QTimer(self)
        self._ui_save_timer.setSingleShot(True)
        self._ui_save_timer.setInterval(400)
        self._ui_save_timer.timeout.connect(self._save_ui)

    # ---------------------------------------------------------------- models
    def get_model(self, sid: str) -> TranscriptModel:
        m = self.models.get(sid)
        if m is None:
            m = self.models[sid] = TranscriptModel()
        return m

    def mark_dirty(self, sid: str) -> None:
        self._dirty.add(sid)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_dirty(self) -> None:
        ids = list(self._dirty)
        self._dirty.clear()
        for sid in ids:
            m = self.models.get(sid)
            if m is None:
                continue
            m.refresh_inputs()  # the tool inputs that streamed since the last flush, parsed once
            r = self.rt.setdefault(sid, Runtime(loaded=True))
            r.rev = m.rev
            self.rt_changed.emit(sid)

    def patch_rt(self, sid: str, **patch: Any) -> Runtime:
        r = self.rt.setdefault(sid, Runtime())
        for k, v in patch.items():
            setattr(r, k, v)
        self.rt_changed.emit(sid)
        return r

    def rt_of(self, sid: Optional[str]) -> Optional[Runtime]:
        return self.rt.get(sid) if sid else None

    # ---------------------------------------------------------------- toasts
    def toast(self, text: str, level: str = "info", action: Optional[Dict[str, Any]] = None) -> None:
        text, action = self._humanize(text, level, action)
        for t in self.toasts:
            if t.text == text and t.level == level:
                t.count += 1
                self._arm_toast(t.id, level)
                self.toasts_changed.emit(self.toasts)
                return
        self._toast_seq += 1
        t = Toast(self._toast_seq, text, level, action)
        self.toasts = self.toasts[-4:] + [t]
        self._arm_toast(t.id, level)
        self.toasts_changed.emit(self.toasts)

    def _arm_toast(self, tid: int, level: str, ms: Optional[int] = None) -> None:
        old = self._toast_timers.pop(tid, None)
        if old is not None:
            old.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.dismiss_toast(tid))
        timer.start(ms if ms is not None else (9000 if level == "error" else 4500))
        self._toast_timers[tid] = timer

    def hold_toast(self, tid: int) -> None:
        t = self._toast_timers.pop(tid, None)
        if t is not None:
            t.stop()

    def release_toast(self, tid: int) -> None:
        self._arm_toast(tid, "info", 1800)

    def dismiss_toast(self, tid: int) -> None:
        self.toasts = [t for t in self.toasts if t.id != tid]
        t = self._toast_timers.pop(tid, None)
        if t is not None:
            t.stop()
        self.toasts_changed.emit(self.toasts)

    def _humanize(self, text: str, level: str, action: Optional[Dict[str, Any]]) -> tuple:
        """Known failure texts said in plain words, with the way out attached."""
        import re

        if level != "error":
            return text, action
        t = re.sub(r"^Claude Code returned an error result:\s*", "", text, flags=re.I)
        if re.search(
            r"OAuth session expired|Failed to authenticate|not logged in|Invalid API key|authentication_error", t, re.I
        ):
            return (
                "Claude Code is signed out. Sign in again with `claude login` in a terminal, or set a gateway key in Settings → Claude Code.",
                action or {"label": "Settings", "run": lambda: self.set_dialog("settings", "claude")},
            )
        if re.search(r"rate limit|429|overloaded|529", t, re.I):
            return (
                "The model is busy right now. Claude Code will retry; if it keeps happening, try again in a minute.",
                action,
            )
        if re.search(r"ECONNREFUSED|ENOTFOUND|fetch failed|network", t, re.I):
            return f"Could not reach the model endpoint — {t[:120] + '…' if len(t) > 120 else t}", action
        return t, action

    # ---------------------------------------------------------------- calls
    def call(self, method: str, params: Optional[Json] = None, quiet: bool = False) -> Call:
        """An RPC call that toasts its failure unless ``quiet``."""
        c = rpc.call(method, params)
        if not quiet:
            c.catch(lambda e: self.toast(err_text(e), "error"))
        return c

    # ---------------------------------------------------------------- wiring
    def init(self) -> None:
        rpc.on("connection", self._on_connection)
        rpc.on("session.msgs", self._on_msgs)
        rpc.on("session.state", self._on_state)
        rpc.on("session.permission", self._on_permission)
        rpc.on("sessions.changed", lambda _d: self.refresh_sessions())
        rpc.on("schedules.changed", lambda _d: self._bump_sched())
        rpc.on("memory.changed", lambda _d: self._bump_sched())
        rpc.on("repos.changed", self._on_repos)
        rpc.on("inbox.changed", self._on_inbox)
        rpc.on("dispatch.changed", self._on_dispatch)
        rpc.on("dispatch.playbooks", lambda _d: self.load_playbooks())
        rpc.on("timeline.step", self._on_timeline_step)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _s: self.apply_theme())

    def _bump_sched(self) -> None:
        self.sched_rev += 1
        self.sched_changed.emit(self.sched_rev)

    def _on_connection(self, d: Json) -> None:
        connected = bool(d.get("connected"))
        self.connected = connected
        self.ever_connected = self.ever_connected or connected
        self.connected_changed.emit(connected)
        if not connected:
            return
        pending = {"info": None, "config": None}

        def ready() -> None:
            if pending["info"] is None or pending["config"] is None:
                return
            info, config = pending["info"], pending["config"]
            set_home(str(info.get("home") or ""))
            self.info, self.config = info, config
            self.info_changed.emit(info)
            self.config_changed.emit(config)
            self.load_gateway_models()
            rpc.call("inbox.list").then(lambda r: self._set_inbox_unread(int((r or {}).get("unread") or 0)))
            self.load_jobs()
            self.load_playbooks()
            self._apply_config_ui(config)
            self.apply_theme()
            self.refresh_sessions()
            # Re-sync any open session after a reconnect.
            active = self.active_id
            for sid in list(self.models):
                del self.models[sid]
                self.patch_rt(sid, loaded=False)
            if active:
                self.open_session(active)
            else:
                last = (config.get("ui") or {}).get("lastSession")
                if isinstance(last, str):
                    self.open_session(last)

        rpc.call("app.info").then(lambda r: (pending.__setitem__("info", r or {}), ready())).catch(
            lambda e: print("[store] app.info failed", e)
        )
        rpc.call("config.get").then(lambda r: (pending.__setitem__("config", r or {}), ready())).catch(
            lambda e: print("[store] config.get failed", e)
        )

    def _on_msgs(self, d: Json) -> None:
        sid = d.get("id")
        msgs = d.get("msgs") or []
        if not sid:
            return
        r = self.rt.get(sid)
        if r is not None and r.loading:
            self._buffered.setdefault(sid, []).extend(msgs)
            return
        m = self.models.get(sid)
        if m is None:
            return
        for msg in msgs:
            if msg.get("type") == "system" and msg.get("subtype") == "thinking_tokens":
                self.patch_rt(sid, thinking_tokens=int(msg.get("estimated_tokens") or 0))
                continue
            if msg.get("type") == "system" and msg.get("subtype") == "notification":
                self.toast(
                    str(msg.get("text") or ""), "warning" if msg.get("priority") in ("high", "immediate") else "info"
                )
                continue
            if (
                msg.get("type") == "stream_event"
                and (msg.get("event") or {}).get("type") == "message_start"
                and not msg.get("parent_tool_use_id")
            ):
                r2 = self.rt.setdefault(sid, Runtime())
                r2.thinking_tokens = 0
            m.push(msg)
        self.mark_dirty(sid)

    def _on_state(self, st: Json) -> None:
        sid = st.get("id")
        if not sid:
            return
        prev = self.rt.get(sid)
        prev_status = prev.status if prev else ""
        r = self.rt.setdefault(sid, Runtime())
        r.st = {**r.st, **st}
        self.rt_changed.emit(sid)
        changed = False
        for s in self.sessions:
            if s.get("id") == sid:
                s["status"] = st.get("status")
                s["live"] = st.get("live")
                s["pending"] = len(st.get("pending") or [])
                changed = True
        if changed:
            self.sessions_changed.emit(self.sessions)
        was_busy = prev_status in ("running", "requires_action")
        if was_busy and st.get("status") == "idle":
            if self.active_id != sid or not _app_focused():
                self.unread[sid] = True
                self.unread_changed.emit(self.unread)
            self.notify.emit(sid, "Claude finished", self.title_of(sid))
        if st.get("status") == "error" and prev_status != "error" and st.get("error"):
            self.notify.emit(sid, "Session stopped with an error", self.title_of(sid))

    def _on_permission(self, d: Json) -> None:
        sid, req = d.get("id"), d.get("request") or {}
        if not sid:
            return
        tool = req.get("toolName")
        label = (
            "Claude has a question"
            if tool == "AskUserQuestion"
            else "A plan is ready for review"
            if tool == "ExitPlanMode"
            else f"Claude wants to use {req.get('displayName') or tool}"
        )
        self.notify.emit(sid, label, self.title_of(sid))
        if not _app_focused():
            self.attention.emit()
        if self.active_id != sid:
            self.unread[sid] = True
            self.unread_changed.emit(self.unread)
        self.permission_asked.emit(sid, req)

    def _on_inbox(self, unread: Any) -> None:
        if isinstance(unread, int):
            self._set_inbox_unread(unread)
        self._bump_sched()

    def _set_inbox_unread(self, n: int) -> None:
        self.inbox_unread = n
        self.inbox_unread_changed.emit(n)

    # ---------------------------------------------------------------- dispatch
    def load_jobs(self) -> None:
        def ok(lst: Any) -> None:
            self.jobs = {str(j.get("id")): j for j in lst if isinstance(j, dict)} if isinstance(lst, list) else {}
            self.jobs_loaded = True
            self.dispatch_changed.emit(None)

        rpc.call("dispatch.list").then(ok).catch(lambda _e: None)

    def load_playbooks(self) -> None:
        def ok(lst: Any) -> None:
            self.playbooks = [p for p in lst if isinstance(p, dict)] if isinstance(lst, list) else []
            self.playbooks_changed.emit(self.playbooks)

        rpc.call("dispatch.playbooks").then(ok).catch(lambda _e: None)

    def _on_dispatch(self, d: Json) -> None:
        if not isinstance(d, dict):
            return
        jid = str(d.get("id") or "")
        if not jid:
            return
        if d.get("removed"):
            self.jobs.pop(jid, None)
            self.job_runs.pop(jid, None)
            if self.dispatch_view == jid:
                self.dispatch_view = None
                self.dispatch_view_changed.emit(None)
            self.dispatch_changed.emit({"id": jid, "removed": True})
            return
        job = d.get("job")
        if isinstance(job, dict):
            self.jobs[jid] = job
        runs = d.get("runs")
        if isinstance(runs, list):
            cur = self.job_runs.setdefault(jid, {})
            if d.get("all"):
                cur.clear()
            for r in runs:
                if isinstance(r, dict) and r.get("id"):
                    cur[str(r["id"])] = r
        self.dispatch_changed.emit({"id": jid})

    def fetch_job(self, jid: str, done: Optional[Callable[[], None]] = None) -> None:
        def ok(r: Any) -> None:
            if not isinstance(r, dict):
                return
            job = r.get("job")
            if isinstance(job, dict):
                self.jobs[jid] = job
            self.job_runs[jid] = {str(x["id"]): x for x in (r.get("runs") or []) if isinstance(x, dict) and x.get("id")}
            self.dispatch_changed.emit({"id": jid})
            if done is not None:
                done()

        self.call("dispatch.get", {"id": jid}, quiet=True).then(ok).catch(lambda _e: None)

    def runs_of(self, jid: str) -> List[Json]:
        """A job's runs in the server's order, the superseded (retried) ones left out."""
        return [r for r in self.job_runs.get(jid, {}).values() if not r.get("superseded")]

    @property
    def dispatch_needs(self) -> int:
        """Requests waiting across jobs, plus the canaries waiting to be read: the sidebar's badge."""
        n = 0
        for j in self.jobs.values():
            if j.get("state") in ("running", "reviewing"):
                n += int(j.get("needsAction") or 0) + (1 if j.get("canaryWaiting") else 0)
        return n

    def show_dispatch(self, view: Optional[str] = None) -> None:
        """The board (None), the composer ("new") or a job's view (its id)."""
        self.dispatch_view = view
        if view not in (None, "new"):
            self.fetch_job(str(view))
        self.dispatch_view_changed.emit(view)
        if self.page != "dispatch":
            self.show_page("dispatch")

    def open_job(self, jid: str) -> None:
        self.show_dispatch(jid)

    def _on_timeline_step(self, d: Json) -> None:
        sid, step = d.get("id"), d.get("step")
        cur = self.timeline.get(sid or "")
        if cur is None or not step:
            return
        if any(s.get("n") == step.get("n") for s in cur):
            return
        cur.append(step)
        self.timeline_changed.emit(sid)

    def title_of(self, sid: str) -> str:
        for s in self.sessions:
            if s.get("id") == sid:
                return str(s.get("title") or "Session")
        return "Session"

    # ---------------------------------------------------------------- config, theme, ui
    def _apply_config_ui(self, config: Json) -> None:
        ui = config.get("ui") or {}
        if isinstance(ui.get("sidebar"), bool):
            self.ui.sidebar = ui["sidebar"]
        if isinstance(ui.get("panel"), bool):
            self.ui.panel = ui["panel"]
        if ui.get("panelTab") in PANEL_TABS:
            self.ui.panel_tab = ui["panelTab"]
        if isinstance(ui.get("sidebarW"), (int, float)):
            self.ui.sidebar_w = int(ui["sidebarW"])
        if isinstance(ui.get("panelW"), (int, float)):
            self.ui.panel_w = int(ui["panelW"])
        if ui.get("diffMode") in ("unified", "split"):
            self.ui.diff_mode = ui["diffMode"]
        if ui.get("view") in [v["id"] for v in VIEW_MODES]:
            self.ui.view = ui["view"]
        if isinstance(ui.get("fontScale"), (int, float)) and ui["fontScale"] in theme_mod.SCALE_STEPS:
            self.ui.font_scale = float(ui["fontScale"])
            theme_mod.theme.set_scale(self.ui.font_scale)
        self.ui_changed.emit(self.ui)

    def apply_theme(self) -> None:
        pref = (self.config or {}).get("theme") or "system"
        dark = True
        if pref == "light":
            dark = False
        elif pref == "system":
            hints = QGuiApplication.styleHints()
            scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
            dark = scheme != Qt.ColorScheme.Light if scheme is not None else True
        theme_mod.theme.apply(dark)

    def set_theme(self, pref: str) -> None:
        if not self.config:
            return
        self.config = {**self.config, "theme": pref}
        self.apply_theme()
        self.call("config.set", {"theme": pref}).then(self._set_config)

    def _set_config(self, c: Json) -> None:
        self.config = c
        self.config_changed.emit(c)

    def set_config(self, patch: Json) -> Call:
        def done(c: Json) -> None:
            self._set_config(c)
            self._apply_config_ui(c)
            self.apply_theme()

        return self.call("config.set", patch).then(done)

    # ---- the repositories registry (REPOSITORIES.md)
    @property
    def repos(self) -> List[Json]:
        return [e for e in (self.config or {}).get("repos") or [] if isinstance(e, dict)]

    def _on_repos(self, d: Json) -> None:
        if self.config is None:
            return
        self.config = {**self.config, "repos": list((d or {}).get("repos") or [])}
        self.config_changed.emit(self.config)
        self.repos_changed.emit(self.config["repos"])

    def repo_for(self, path: str) -> Optional[Json]:
        """The registered repository whose folder holds ``path`` (the deepest one), or None."""
        p = str(path or "").rstrip("/")
        best: Optional[Json] = None
        for e in self.repos:
            ep = str(e.get("path") or "").rstrip("/")
            if ep and (p == ep or p.startswith(ep + "/")) and (best is None or len(ep) > len(str(best.get("path")))):
                best = e
        return best

    def set_ui(self, **patch: Any) -> None:
        for k, v in patch.items():
            setattr(self.ui, k, v)
        self.ui_changed.emit(self.ui)
        self._ui_save_timer.start()

    def _save_ui(self) -> None:
        u = self.ui
        rpc.call(
            "config.set",
            {
                "ui": {
                    "sidebar": u.sidebar,
                    "panel": u.panel,
                    "panelTab": u.panel_tab,
                    "sidebarW": u.sidebar_w,
                    "panelW": u.panel_w,
                    "diffMode": u.diff_mode,
                    "view": u.view,
                    "fontScale": u.font_scale,
                }
            },
        )

    def zoom(self, d: int) -> None:
        """Ctrl+= / Ctrl+- / Ctrl+0: the conversation's text one step larger or smaller, or back."""
        steps = theme_mod.SCALE_STEPS
        if d == 0:
            nxt = 1.0
        else:
            i = min(range(len(steps)), key=lambda k: abs(steps[k] - self.ui.font_scale))
            nxt = steps[max(0, min(len(steps) - 1, i + d))]
        if nxt == self.ui.font_scale:
            return
        self.set_ui(font_scale=nxt)
        theme_mod.theme.set_scale(nxt)
        self.toast(f"Text {round(nxt * 100)}%", "info")

    def cycle_view(self) -> None:
        ids = [v["id"] for v in VIEW_MODES]
        nxt = ids[(ids.index(self.ui.view) + 1) % len(ids)]
        self.set_ui(view=nxt)
        self.toast(f"View: {next(v['label'] for v in VIEW_MODES if v['id'] == nxt)}", "info")

    def set_dialog(self, key: str, value: Any) -> None:
        self.dialogs[key] = value
        self.dialog_changed.emit(key, value)

    def confirm_action(self, spec: ConfirmSpec) -> None:
        self.set_dialog("confirm", spec)

    # ---------------------------------------------------------------- sessions
    def refresh_sessions(self, force: bool = False, done: Optional[Callable[[], None]] = None) -> None:
        """Re-fetch the list (debounced; ``force`` re-reads the transcripts on disk)."""
        self._list_force = self._list_force or force
        if done:
            self._list_waiters.append(done)
        self._list_timer.start(0 if force else 60)

    def _fetch_sessions(self) -> None:
        force, self._list_force = self._list_force, False
        waiters, self._list_waiters = self._list_waiters, []

        def ok(fresh: Any) -> None:
            fresh = fresh or []
            prev = {s.get("id"): s for s in self.sessions}
            out: List[Json] = []
            for s in fresh:
                p = prev.get(s.get("id"))
                out.append(p if p is not None and _same_summary(p, s) else s)
            self.sessions = out
            self.sessions_loaded = True
            self.sessions_changed.emit(self.sessions)
            for w in waiters:
                w()

        def bad(_e: RpcError) -> None:
            for w in waiters:
                w()

        rpc.call("sessions.refresh" if force else "sessions.list").then(ok).catch(bad)

    def open_session(self, sid: str) -> None:
        if self.page != "session":
            self.show_page("session")
        self.active_id = sid
        self.unread[sid] = False
        self.unread_changed.emit(self.unread)
        self.active_changed.emit(sid)
        rpc.call("config.set", {"ui": {"lastSession": sid}})
        r = self.rt.get(sid)
        if sid in self.models and r is not None and r.loaded:
            return
        if sid in self._opening:
            return
        self._opening.add(sid)
        self._load_session(sid)

    def _load_session(self, sid: str) -> None:
        self.patch_rt(sid, loading=True)

        def ok(snap: Json) -> None:
            # The snapshot goes into the model in slices, one per event-loop turn, so a long
            # conversation never holds the window: the view shows the rows it has under the loading
            # skeleton and the rest follow. Live messages wait in the buffer until the end.
            msgs = list(snap.get("messages") or [])
            inflight = list(snap.get("inflight") or [])
            m = TranscriptModel()
            self.models[sid] = m
            r = self.rt.setdefault(sid, Runtime())
            r.st = {**r.st, **(snap.get("state") or {})}
            r.meta = snap.get("meta") or {}

            def slice_(i: int) -> None:
                if self.models.get(sid) is not m:
                    return  # the session was closed or reopened under us
                m.push_all(msgs[i : i + LOAD_SLICE])
                nxt = i + LOAD_SLICE
                if nxt < len(msgs):
                    m.refresh_inputs()
                    r.rev = m.rev
                    self.rt_changed.emit(sid)
                    QTimer.singleShot(0, lambda: slice_(nxt))
                    return
                m.push_all(inflight)
                m.refresh_inputs()
                self._opening.discard(sid)
                r.loaded, r.loading, r.rev = True, False, m.rev
                self.rt_changed.emit(sid)
                extra = self._buffered.pop(sid, None)
                if extra:
                    self._on_msgs({"id": sid, "msgs": extra})

            slice_(0)

        def bad(e: RpcError) -> None:
            self._opening.discard(sid)
            self.patch_rt(sid, loading=False)
            self.toast(err_text(e), "error")
            if self.active_id == sid:
                self.active_id = None
                self.active_changed.emit(None)

        rpc.call("session.open", {"id": sid}).then(ok).catch(bad)

    def create_session(
        self,
        cwd: str,
        worktree: Optional[Json] = None,
        mode: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        prompt: Optional[str] = None,
        images: Optional[List[Json]] = None,
        title: Optional[str] = None,
    ) -> Call:
        params: Json = {
            "cwd": cwd,
            "worktree": worktree or None,
            "mode": mode,
            "model": model,
            "effort": effort,
            "title": title,
        }
        call = self.call("session.create", {k: v for k, v in params.items() if v is not None})

        def made(r: Json) -> None:
            sid = r["id"]

            def opened() -> None:
                self.open_session(sid)
                if (prompt and prompt.strip()) or images:
                    self.send_message(sid, prompt or "", images=images or []).catch(
                        lambda _e: prompt and self.insert_into_composer(sid, prompt, True)
                    )

            self.refresh_sessions(True, opened)

        call.then(made)
        return call

    def send_message(
        self,
        sid: str,
        text: str,
        images: Optional[List[Json]] = None,
        files: Optional[List[Json]] = None,
        now: bool = False,
    ) -> Call:
        """``now`` steers: the current step is interrupted and this message goes first."""
        self.sent.emit(sid)
        return self.call(
            "session.send", {"id": sid, "text": text, "images": images or [], "files": files or [], "now": bool(now)}
        )

    def remove_held(self, sid: str, uuid: str) -> Call:
        return self.call("session.queue.remove", {"id": sid, "uuid": uuid})

    def update_held(self, sid: str, uuid: str, text: str) -> Call:
        return self.call("session.queue.update", {"id": sid, "uuid": uuid, "text": text})

    def send_held_now(self, sid: str, uuid: str) -> Call:
        return self.call("session.queue.sendNow", {"id": sid, "uuid": uuid})

    def set_cwd(self, sid: str, cwd: str) -> Call:
        return self.call("session.setCwd", {"id": sid, "cwd": cwd}).then(lambda _r: self.refresh_sessions(True))

    def default_cwd(self, explicit: Optional[str] = None) -> str:
        """The folder a one-click new session starts in: the current one, else the most recent project."""
        if explicit:
            return explicit
        active = self.active_session()
        if active:
            return str(active.get("project") or active.get("cwd") or "")
        recent = (self.config or {}).get("recentProjects") or []
        if recent:
            return str(recent[0])
        return str((self.info or {}).get("home") or "")

    def quick_new_session(self, cwd: Optional[str] = None) -> None:
        d = self.default_cwd(cwd)
        if not d:
            self.set_dialog("newSession", {})
            return
        c = self.config or {}
        self.create_session(
            d, mode=c.get("defaultMode"), model=c.get("defaultModel"), effort=c.get("defaultEffort")
        ).catch(lambda _e: self.set_dialog("newSession", {"cwd": d}))

    def interrupt(self, sid: str) -> Call:
        return self.call("session.interrupt", {"id": sid})

    def set_mode(self, sid: str, mode: str) -> Call:
        return self.call("session.setMode", {"id": sid, "mode": mode})

    def set_model(self, sid: str, model: str) -> Call:
        return self.call("session.setModel", {"id": sid, "model": model})

    def set_effort(self, sid: str, effort: str) -> Call:
        return self.call("session.setEffort", {"id": sid, "effort": effort})

    def respond(self, sid: str, request_id: str, decision: Json) -> Call:
        return self.call("session.permission", {"id": sid, "requestId": request_id, "decision": decision})

    def rename_session(self, sid: str, title: str) -> Call:
        return self.call("session.rename", {"id": sid, "title": title}).then(lambda _r: self.refresh_sessions(True))

    def delete_session(self, sid: str, remove_worktree: bool = False, remove_helpers: bool = False) -> Call:
        def done(_r: Any) -> None:
            self.models.pop(sid, None)
            self.rt.pop(sid, None)
            if self.active_id == sid:
                self.active_id = None
                self.active_changed.emit(None)
            self.sessions = [s for s in self.sessions if s.get("id") != sid]
            self.sessions_changed.emit(self.sessions)
            self.refresh_sessions(True)

        return self.call(
            "session.delete", {"id": sid, "removeWorktree": remove_worktree, "removeHelpers": remove_helpers}
        ).then(done)

    def archive_session(self, sid: str, archived: bool) -> Call:
        return self.call("session.archive", {"id": sid, "archived": archived}).then(
            lambda _r: self.refresh_sessions(True)
        )

    def pin_session(self, sid: str, pinned: bool) -> Call:
        return self.call("session.pin", {"id": sid, "pinned": pinned}).then(lambda _r: self.refresh_sessions(True))

    def fork_session(self, sid: str, up_to_message_id: Optional[str] = None, send_after: Optional[str] = None) -> Call:
        def made(r: Json) -> None:
            nid = r["id"]

            def opened() -> None:
                self.open_session(nid)
                if send_after:
                    self.send_message(nid, send_after)
                self.toast("Forked into a new session", "success")

            self.refresh_sessions(True, opened)

        return self.call("session.fork", {"id": sid, "upToMessageId": up_to_message_id}).then(made)

    def open_file(self, path: str, line: Optional[int] = None) -> None:
        self.file_open.emit({"path": path, "line": line, "nonce": time.time()})
        self.set_ui(panel=True, panel_tab="files")

    def show_page(self, page: str) -> None:
        self.page = page
        self.page_changed.emit(page)

    def set_timeline(self, sid: str, steps: List[Json]) -> None:
        self.timeline[sid] = steps
        self.timeline_changed.emit(sid)

    def bump_fs(self) -> None:
        self.fs_rev += 1
        self.fs_changed.emit(self.fs_rev)

    def insert_into_composer(self, sid: str, text: str, replace: bool = False) -> None:
        self.composer_insert.emit({"id": sid, "text": text, "nonce": time.time(), "replace": replace})

    def active_session(self) -> Optional[Json]:
        for s in self.sessions:
            if s.get("id") == self.active_id:
                return s
        return None

    def session(self, sid: str) -> Optional[Json]:
        for s in self.sessions:
            if s.get("id") == sid:
                return s
        return None

    def refresh_info(self) -> Call:
        def done(info: Json) -> None:
            self.info = info
            self.info_changed.emit(info)
            self.load_gateway_models(True)

        return self.call("app.info", {}, quiet=True).then(done)

    def load_gateway_models(self, force: bool = False) -> None:
        prev = self.gateway_models
        if prev == "loading":
            return
        if not force and isinstance(prev, dict) and prev.get("ok"):
            return
        self.gateway_models = "loading"
        self.gateway_models_changed.emit("loading")

        def ok(r: Json) -> None:
            self.gateway_models = r
            self.gateway_models_changed.emit(r)

        def bad(e: RpcError) -> None:
            msg = err_text(e)
            error = (
                "Quit Pilcrow and open it again to finish updating"
                if "unknown method" in msg.lower()
                else (msg or "Could not ask the gateway")
            )
            self.gateway_models = {"ok": False, "error": error}
            self.gateway_models_changed.emit(self.gateway_models)

        rpc.call("models.gateway", {"force": force}).then(ok).catch(bad)

    def open_lightbox(self, src: str) -> None:
        self.lightbox = src
        self.lightbox_changed.emit(src)

    def close_lightbox(self) -> None:
        self.lightbox = None
        self.lightbox_changed.emit(None)

    def set_find_open(self, v: bool) -> None:
        self.find_open = v
        self.find_open_changed.emit(v)


def _same_summary(a: Json, b: Json) -> bool:
    if set(a) != set(b):
        return False
    for k, x in a.items():
        y = b[k]
        if x == y:
            continue
        if isinstance(x, dict) and isinstance(y, dict) and x == y:
            continue
        return False
    return True


def _app_focused() -> bool:
    app = QGuiApplication.instance()
    return bool(app is not None and app.focusWindow() is not None)


store = Store()
