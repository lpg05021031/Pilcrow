"""The workboard's pure logic (a port of web/src/lib/board.ts): which lane a session sits in, what
can be done to it there, and the ledger of what happened to it.

No Qt here. Sessions are the server's ``SessionSummary`` dicts, live state the ``SessionState`` dict
(or ``None``), times are milliseconds. ``test/unit/board-cases.json`` is run by both this module's
tests and the web's.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .fmt import rel_path
from .tools_meta import str_

Json = Dict[str, Any]
Lane = str  # 'backlog' | 'agent' | 'wait' | 'me' | 'done'
CardState = str  # 'review' | 'prompt' | 'running' | 'queued' | 'stuck' | 'wait' | 'done' | 'backlog'
Action = Dict[str, Any]  # {"id", "label", "key", "primary"?, "danger"?}

LANES: List[Json] = [
    {"key": "backlog", "title": "Backlog", "hint": "Not started", "empty": "Nothing waiting to start."},
    {"key": "agent", "title": "With Claude", "hint": "Working now", "empty": "Claude is not working on anything."},
    {"key": "wait", "title": "Waiting", "hint": "On a condition", "empty": "Nothing is waiting on a condition."},
    {"key": "me", "title": "Needs you", "hint": "The only lane that interrupts you", "empty": "Nothing needs you."},
    {"key": "done", "title": "Done", "hint": "Approved today", "empty": "Nothing approved today."},
]
LANE_KEYS: List[Lane] = [str(lane["key"]) for lane in LANES]

SCHEDULE_PREFIX = "sched:"


def _now_ms() -> float:
    return datetime.now().timestamp() * 1000


def same_day(a: float, b: float) -> bool:
    x, y = datetime.fromtimestamp(a / 1000), datetime.fromtimestamp(b / 1000)
    return (x.year, x.month, x.day) == (y.year, y.month, y.day)


def last_decision(s: Json) -> Optional[Json]:
    ds = s.get("decisions") or []
    return ds[-1] if ds else None


def is_stuck(s: Json) -> bool:
    """Claude could not go on by itself: the process ended with an error."""
    return s.get("status") == "error"


def has_prompt(s: Json, rt: Optional[Json] = None) -> bool:
    """Something only you can answer is open: a permission, a question, a plan."""
    return bool(
        s.get("status") == "requires_action" or (s.get("pending") or 0) > 0 or len((rt or {}).get("pending") or []) > 0
    )


def awaits_review(s: Json) -> bool:
    """Claude finished a turn on this card and you have not dealt with it yet."""
    return bool(
        s.get("intent")
        and s.get("turnEnd")
        and s.get("reviewedTurn") != s.get("turnEnd")
        and s.get("status") not in ("running", "starting")
    )


def lane_of(s: Json, rt: Optional[Json] = None, now: Optional[float] = None) -> Optional[Lane]:
    """The lane of a session, or None when it is not on the board: archived, or a plain conversation
    (no intent) that is not doing anything. Only work is on the board; conversations stay in the sidebar."""
    now = _now_ms() if now is None else now
    if s.get("archived"):
        return None
    if has_prompt(s, rt):
        return "me"
    if s.get("status") in ("running", "starting"):
        return "agent"
    if s.get("queued"):
        return "agent"
    if not s.get("intent"):
        return None
    if is_stuck(s):
        return "agent"
    if s.get("wait"):
        return "wait"
    if awaits_review(s):
        return "me"
    last = last_decision(s)
    if (
        last
        and last.get("kind") == "approve"
        and s.get("reviewedTurn")
        and s.get("reviewedTurn") == s.get("turnEnd")
        and same_day(float(last.get("at") or 0), now)
    ):
        return "done"
    return "backlog"


def card_state(s: Json, rt: Optional[Json] = None, now: Optional[float] = None) -> Optional[CardState]:
    """A finer state than the lane, for the chip on the card and the actions in the panel."""
    lane = lane_of(s, rt, now)
    if lane is None:
        return None
    if lane == "me":
        return "prompt" if has_prompt(s, rt) else "review"
    if lane == "agent":
        if s.get("status") in ("running", "starting"):
            return "running"
        if is_stuck(s):
            return "stuck"
        return "queued" if s.get("queued") else "running"
    return lane


def _a(id_: str, label: str, key: str, primary: bool = False, danger: bool = False) -> Action:
    out: Action = {"id": id_, "label": label, "key": key}
    if primary:
        out["primary"] = True
    if danger:
        out["danger"] = True
    return out


def actions_for(state: CardState, rt: Optional[Json] = None) -> List[Action]:
    """What can be done to a card in its current state. The first action is what Enter does; the keys
    are single letters so a backlog can be cleared without the mouse: e e r … e."""
    if state == "review":
        return [
            _a("approve", "Approve and commit", "e", primary=True),
            _a("reject", "Send back", "r"),
            _a("park", "Set aside", "w"),
            _a("open", "Open", "↵"),
        ]
    if state == "prompt":
        pending = (rt or {}).get("pending") or []
        tool = (pending[0] or {}).get("toolName") if pending else None
        label = (
            "Answer the question"
            if tool == "AskUserQuestion"
            else "Review the plan"
            if tool == "ExitPlanMode"
            else "Open to respond"
        )
        return [_a("open", label, "↵", primary=True)]
    if state == "running":
        return [_a("open", "Open", "↵", primary=True), _a("stop", "Stop", "s", danger=True)]
    if state == "queued":
        return [_a("open", "Open", "↵", primary=True), _a("stop", "Leave the queue", "s", danger=True)]
    if state == "stuck":
        return [
            _a("send", "Give it what it needs", "r", primary=True),
            _a("open", "Open", "↵"),
            _a("park", "Set aside", "w"),
        ]
    if state == "wait":
        return [
            _a("wake", "Wake now", "↵", primary=True),
            _a("wait", "Change the wait", "w"),
            _a("unwait", "Stop waiting", "s", danger=True),
            _a("open", "Open", "o"),
        ]
    if state == "backlog":
        return [
            _a("start", "Hand to Claude", "↵", primary=True),
            _a("wait", "Wait for…", "w"),
            _a("open", "Open", "o"),
        ]
    if state == "done":
        return [_a("open", "Open", "↵", primary=True)]
    return []


def drop_action(state: CardState, lane: Lane) -> Optional[str]:
    """Dropping a card on a lane means "do that lane's action". Only lanes with a legal action accept;
    "Needs you" never does, since a card gets there by Claude handing work back, not by being put there."""
    if lane == "agent":
        return "start" if state == "backlog" else "wake" if state == "wait" else "send" if state == "stuck" else None
    if lane == "wait":
        return "wait" if state in ("backlog", "review", "wait") else None
    if lane == "done":
        return "approve" if state == "review" else None
    if lane == "backlog":
        if state in ("running", "queued"):
            return "stop"
        if state == "wait":
            return "unwait"
        return "park" if state in ("review", "stuck") else None
    return None


# ---- schedules as cards


def is_schedule(sid: Optional[str]) -> bool:
    return bool(sid and str(sid).startswith(SCHEDULE_PREFIX))


def schedule_card(r: Json) -> Json:
    """A schedule that will fire, as a card in the Waiting lane. It is not a session; its actions open the schedule."""
    return {
        "id": SCHEDULE_PREFIX + str(r.get("id")),
        "title": r.get("name"),
        "cwd": r.get("cwd"),
        "project": r.get("cwd"),
        "archived": False,
        "pinned": False,
        "worktree": None,
        "status": "idle",
        "live": False,
        "createdAt": r.get("createdAt"),
        "lastModified": r.get("at"),
        "intent": r.get("prompt"),
        "wait": {"kind": "time", "at": r.get("at")},
    }


def schedule_actions() -> List[Action]:
    return [_a("schedule-edit", "Edit the schedule", "↵", primary=True), _a("schedule-run", "Run now", "r")]


# ---- the ledger

DECISION_LABEL = {
    "approve": "You approved",
    "reject": "You sent it back",
    "answer": "You answered",
    "park": "You set it aside",
    "wait": "Waiting",
    "wake": "Woken",
    "stop": "You stopped it",
    "start": "Handed to Claude",
    "steer": "You steered",
}


def _short_model(m: Optional[str]) -> str:
    if not m:
        return ""
    return re.sub(r"-\d{8}$", "", re.sub(r"^claude-", "", str(m)))


def ledger_of(s: Json, rt: Optional[Json] = None) -> List[Json]:
    """Everything that happened to a card, oldest first, from the summary alone (no transcript needed).
    Events are ``{"at", "kind", "label", "note"?, "tone"}``."""
    out: List[Json] = []
    if s.get("createdAt"):
        out.append(
            {
                "at": float(s["createdAt"]),
                "kind": "intent",
                "label": "Intent written" if s.get("intent") else "Session opened",
                "note": s.get("intent"),
                "tone": "me",
            }
        )
    for d in s.get("decisions") or []:
        kind = str(d.get("kind") or "")
        tone = "err" if kind == "stop" else "ok" if kind == "approve" else "" if kind in ("wake", "wait") else "me"
        out.append(
            {
                "at": float(d.get("at") or 0),
                "kind": "decision",
                "label": DECISION_LABEL.get(kind, kind),
                "note": d.get("note"),
                "tone": tone,
            }
        )
        if d.get("commit"):
            out.append(
                {
                    "at": float(d.get("at") or 0) + 1,
                    "kind": "commit",
                    "label": f"Committed {str(d['commit'])[:7]}",
                    "tone": "ok",
                }
            )
    if s.get("turnEnd"):
        parts = [
            p
            for p in (
                _short_model((rt or {}).get("activeModel")),
                f"${float(s['costUsd']):.2f}" if s.get("costUsd") else "",
            )
            if p
        ]
        err = s.get("status") == "error"
        out.append(
            {
                "at": float(s["turnEnd"]),
                "kind": "turn",
                "label": "Turn ended with an error" if err else "Turn ended",
                "note": " · ".join(parts) or None,
                "tone": "err" if err else "run",
            }
        )
    out.sort(key=lambda e: e["at"])
    return out


# ---- the one line under the title

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _when(ms: float) -> str:
    d = datetime.fromtimestamp(ms / 1000)
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.hour:02d}:{d.minute:02d}"


def describe_wait(w: Json, titles: Optional[Dict[str, str]] = None) -> str:
    kind = w.get("kind")
    if kind == "time":
        return f"Until {_when(float(w.get('at') or 0))}"
    if kind == "ci":
        return f"Until CI finishes on {w.get('branch')}"
    t = (titles or {}).get(str(w.get("id")))
    return f"Until “{t}” is approved" if t else "Until another card is approved"


def _first_sentence(t: str) -> str:
    m = re.match(r"^[^.。!?！？\n]*[.。!?！？]?", t.strip())
    return (m.group(0) if m else t).strip()


def card_line(
    s: Json, state: CardState, rt: Optional[Json] = None, titles: Optional[Dict[str, str]] = None
) -> Tuple[str, str]:
    """The one line under a card's title and its tone ('ask' | 'err' | ''): what it waits for, does, or asks."""
    rt = rt or {}
    if state == "prompt":
        pending = rt.get("pending") or []
        p = pending[0] if pending else None
        if p:
            text = perm_summary(p, str(s.get("cwd") or ""))[0] or str(p.get("toolName") or "")
        else:
            text = "Waiting for your input"
        return text, "ask"
    if state == "review":
        report = s.get("report") or {}
        reply = rt.get("lastReply") or {}
        return str(report.get("ask") or reply.get("text") or "Finished; review the result"), "ask"
    if state == "running":
        a = rt.get("activity") or {}
        if a.get("kind") == "tool":
            return f"{a.get('tool')} · {a.get('label')}", ""
        if a.get("kind") == "text":
            return str(a.get("label") or ""), ""
        return ("Starting Claude Code…" if s.get("status") == "starting" else "Thinking…"), ""
    if state == "queued":
        return "Queued: Claude is at the limit; it starts when a slot frees", ""
    if state == "stuck":
        return str(rt.get("error") or "Stopped with an error"), "err"
    if state == "wait":
        return (describe_wait(s["wait"], titles) if s.get("wait") else "Waiting"), ""
    if state == "done":
        d = last_decision(s) or {}
        return (f"Committed {str(d['commit'])[:7]}" if d.get("commit") else "Approved"), ""
    if state == "backlog":
        return (_first_sentence(str(s["intent"])) if s.get("intent") else "No intent yet"), ""
    return "", ""


def perm_summary(p: Json, cwd: str) -> Tuple[str, bool, Optional[str]]:
    """What a permission request is about, in one line: (text, mono, note)."""
    i = p.get("input") or {}
    tool = p.get("toolName")
    if tool in ("Bash", "PowerShell"):
        return str_(i, "command"), True, str_(i, "description") or None
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return rel_path(str_(i, "file_path") or str_(i, "notebook_path"), cwd), True, None
    if tool == "WebFetch":
        return str_(i, "url"), True, None
    if tool == "AskUserQuestion":
        qs = i.get("questions") or []
        q = (qs[0] or {}).get("question") if qs and isinstance(qs[0], dict) else None
        return str(q or "Claude has a question"), False, None
    if tool == "ExitPlanMode":
        return "A plan is ready for review", False, None
    return str(p.get("description") or p.get("title") or ""), False, None


def step(lanes: Dict[Lane, List[Json]], sid: Optional[str], direction: str) -> Optional[str]:
    """The card after ``sid`` in the given direction within one lane's list, or the same index in the next lane."""
    li = next((i for i, k in enumerate(LANE_KEYS) if any(s.get("id") == sid for s in lanes.get(k, []))), -1)
    if li < 0:
        first = next((k for k in LANE_KEYS if lanes.get(k)), None)
        return str(lanes[first][0]["id"]) if first else None
    lst = lanes[LANE_KEYS[li]]
    i = next(j for j, s in enumerate(lst) if s.get("id") == sid)
    if direction == "down":
        return str(lst[min(len(lst) - 1, i + 1)]["id"])
    if direction == "up":
        return str(lst[max(0, i - 1)]["id"])
    d = 1 if direction == "right" else -1
    j = li + d
    while 0 <= j < len(LANE_KEYS):
        nxt = lanes.get(LANE_KEYS[j]) or []
        if nxt:
            return str(nxt[min(i, len(nxt) - 1)]["id"])
        j += d
    return sid


def quick_waits(now: Optional[float] = None) -> List[Json]:
    """The quick wait conditions the picker offers; a pure list so the keys 1–3 and the buttons agree.
    ``wait`` is a condition dict, or ``"ci"`` for the branch the caller knows."""
    now = _now_ms() if now is None else now
    d = datetime.fromtimestamp(now / 1000)
    tomorrow = d.replace(hour=9, minute=0, second=0, microsecond=0)
    tomorrow = datetime.fromtimestamp(tomorrow.timestamp() + 86400)
    return [
        {"key": "1", "label": "Until CI passes", "hint": "GitHub Actions on this branch", "wait": "ci"},
        {
            "key": "2",
            "label": "Tomorrow 09:00",
            "hint": "A one-off time",
            "wait": {"kind": "time", "at": tomorrow.timestamp() * 1000},
        },
        {"key": "3", "label": "In an hour", "hint": "", "wait": {"kind": "time", "at": now + 3600e3}},
    ]
