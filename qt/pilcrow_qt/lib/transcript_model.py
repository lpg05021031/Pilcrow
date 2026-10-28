"""Incremental transcript model (port of web/src/lib/transcript.ts).

Turns raw SDK messages (live or from history) into render items. Items are mutable objects with a
`rev` counter so a widget can re-render only what changed. Raw messages are the server's dicts with
the camelCase/snake_case keys of web/src/lib/types.ts (RawMsg); timestamps are ms since the epoch.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Union

from .partial_json import parse_partial_json

log = logging.getLogger(__name__)

# 'streaming' | 'running' | 'done' | 'error' | 'denied' | 'interrupted' | 'background'
ToolStatus = str

RawMsg = Dict[str, Any]
ContentBlock = Dict[str, Any]
ToolInput = Dict[str, Any]
StructuredResult = Dict[str, Any]


@dataclass
class UserCommand:
    name: str
    args: str


@dataclass(eq=False)
class UserItem:
    kind: ClassVar[str] = "user"
    id: str
    uuid: str
    text: str
    images: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    local: bool = False
    command: Optional[UserCommand] = None
    prev_uuid: Optional[str] = None
    rev: int = 0
    ts: Optional[float] = None
    # The subagent tool this item sits under, or None at the top level (set by the model, read by bump).
    _parent: Optional[str] = None


@dataclass(eq=False)
class TextItem:
    kind: ClassVar[str] = "text"
    id: str
    text: str
    streaming: bool
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass(eq=False)
class ThinkingItem:
    kind: ClassVar[str] = "thinking"
    id: str
    text: str
    streaming: bool
    start_ts: float
    end_ts: Optional[float] = None
    redacted: bool = False
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass
class ToolResult:
    text: str
    images: List[str] = field(default_factory=list)
    is_error: bool = False


@dataclass
class TaskInfoPatch:
    """What a task_started / task_progress / task_notification message says about a background task."""

    task_id: str
    status: Optional[str] = None
    summary: Optional[str] = None
    output_file: Optional[str] = None
    usage: Any = None


@dataclass(eq=False)
class ToolItem:
    kind: ClassVar[str] = "tool"
    id: str
    name: str
    input: ToolInput = field(default_factory=dict)
    input_json: str = ""
    input_done: bool = False
    status: ToolStatus = "running"
    result: Optional[ToolResult] = None
    structured: Optional[StructuredResult] = None
    children: List[Item] = field(default_factory=list)
    start_ts: float = 0
    end_ts: Optional[float] = None
    elapsed: Optional[float] = None
    parent_id: Optional[str] = None
    task_id: Optional[str] = None
    task: Optional[TaskInfoPatch] = None  # status is always set once a task is attached
    summary: Optional[str] = None
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass(eq=False)
class NoticeItem:
    kind: ClassVar[str] = "notice"
    id: str
    level: str  # 'info' | 'warning' | 'error' | 'success'
    text: str
    detail: Optional[str] = None
    tag: Optional[str] = None
    count: Optional[int] = None
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass(eq=False)
class ResultItem:
    kind: ClassVar[str] = "result"
    id: str
    ok: bool
    subtype: str
    duration_ms: float
    cost_usd: float
    turns: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    text: Optional[str] = None
    errors: Optional[List[str]] = None
    chain_uuid: Optional[str] = None
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass(eq=False)
class CompactItem:
    kind: ClassVar[str] = "compact"
    id: str
    trigger: str
    pre_tokens: Optional[int] = None
    post_tokens: Optional[int] = None
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


@dataclass(eq=False)
class CommandOutputItem:
    kind: ClassVar[str] = "command-output"
    id: str
    text: str
    rev: int = 0
    ts: Optional[float] = None
    _parent: Optional[str] = None


Item = Union[UserItem, TextItem, ThinkingItem, ToolItem, NoticeItem, ResultItem, CompactItem, CommandOutputItem]

_seq = 0


def _nid(p: str) -> str:
    global _seq
    _seq += 1
    return f"{p}-{_seq}"


def _now() -> float:
    return time.time() * 1000


def _parse_ts(s: Any) -> float:
    """Date.parse for the ISO timestamps history carries; now when it cannot be read."""
    if not isinstance(s, str) or not s:
        return _now()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return _now()


def _js_str(v: Any) -> str:
    """JavaScript String(v) for the JSON values a raw message can carry."""
    if isinstance(v, str):
        return v
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, dict):
        return "[object Object]"
    if isinstance(v, list):
        return ",".join(_js_str(x) for x in v)
    return str(v)


def _content_to_result(content: Any, is_error: bool) -> ToolResult:
    if isinstance(content, str):
        return ToolResult(content, [], is_error)
    texts: List[str] = []
    images: List[str] = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        src = b.get("source") or {}
        if b.get("type") == "text":
            texts.append(b.get("text") or "")
        elif b.get("type") == "image" and isinstance(src, dict) and src.get("type") == "base64":
            images.append(f"data:{src.get('media_type')};base64,{src.get('data')}")
        elif b.get("type") == "tool_reference":
            texts.append(f"Loaded {b.get('tool_name')}")
    return ToolResult("\n".join(texts), images, is_error)


def _strip_tags(s: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{re.escape(tag)}>([\s\S]*?)</{re.escape(tag)}>", s)
    return m.group(1) if m else None


class TranscriptModel:
    def __init__(self) -> None:
        self.items: List[Item] = []
        self.tools: Dict[str, ToolItem] = {}
        self._seen: set = set()
        self._drafts: Dict[str, Dict[int, Item]] = {}
        self._ordinals: Dict[str, int] = {}
        self._current: Dict[Optional[str], str] = {}
        self._pending_tasks: Dict[str, TaskInfoPatch] = {}
        self.rev = 0
        self.last_turn_tools: List[str] = []
        self._last_api_error: Optional[str] = None
        self._active_retry: Optional[NoticeItem] = None
        self._retry_status: Optional[int] = None
        self.last_chain: Optional[str] = None
        self._info_seen: set = set()
        # Items that may still need settling when a turn ends: every tool, text and thought is entered
        # here when it starts and leaves once settled, so the end of a turn touches a turn's worth of
        # items rather than the whole conversation (a history of thousands of turns is replayed on open,
        # and a scan of everything per turn made that quadratic). A dict keeps insertion order like a JS Set.
        self._unsettled: Dict[Item, None] = {}

    def _settle_retry(self, failed: bool = False) -> None:
        """A response arrived after retries: turn the warning into a quiet note."""
        r = self._active_retry
        if not r:
            return
        self._active_retry = None
        n = r.count or 1
        r.level = "info"
        r.tag = "retry-ok"
        status = f" (HTTP {self._retry_status})" if self._retry_status else ""
        r.text = f"{'Gave up' if failed else 'Connected'} after {n} {'retries' if n > 1 else 'retry'}{status}"
        self._bump(r)

    def _bump(self, item: Item) -> None:
        item.rev += 1
        self.rev += 1
        # Propagate to enclosing subagent tool items so memoised parents re-render.
        p = getattr(item, "parent_id", None)
        if p is None:
            p = item._parent
        guard = 0
        while p and guard < 10:
            guard += 1
            t = self.tools.get(p)
            if not t:
                break
            t.rev += 1
            p = t.parent_id

    def _container(self, parent: Optional[str]) -> List[Item]:
        if not parent:
            return self.items
        t = self.tools.get(parent)
        return t.children if t else self.items

    def _add(self, item: Item, parent: Optional[str]) -> None:
        item._parent = parent
        self._container(parent).append(item)
        self._bump(item)

    def push_all(self, msgs: Iterable[RawMsg]) -> None:
        for m in msgs:
            self.push(m)

    def push(self, m: RawMsg) -> None:
        try:
            t = m.get("type")
            if t == "stream_event":
                self._on_stream(m)
            elif t == "assistant":
                self._on_assistant(m)
            elif t == "user":
                self._on_user(m)
            elif t == "result":
                self._on_result(m)
            elif t == "system":
                self._on_system(m)
            elif t == "tool_progress":
                self._on_tool_progress(m)
            elif t == "rate_limit_event":
                self._on_rate_limit(m)
            elif t == "tool_use_summary":
                ids = m.get("preceding_tool_use_ids") or []
                first = self.tools.get(ids[0] if ids else "")
                if first:
                    first.summary = m.get("summary")
                    self._bump(first)
            elif t == "conversation_reset":
                self._add(NoticeItem(id=_nid("n"), level="info", text="Conversation cleared", tag="divider"), None)
        except Exception:  # noqa: BLE001 - one bad message must not take the transcript down
            log.exception("transcript push failed: %r", m)

    def _seen_before(self, m: RawMsg) -> bool:
        uuid = m.get("uuid")
        if uuid:
            if uuid in self._seen:
                return True
            self._seen.add(uuid)
        return False

    def _on_stream(self, m: RawMsg) -> None:
        if self._seen_before(m):
            return
        e = m.get("event")
        parent: Optional[str] = m.get("parent_tool_use_id") or None
        if not e:
            return
        etype = e.get("type")
        if etype == "message_start":
            msg_id = (e.get("message") or {}).get("id")
            if not msg_id:
                return
            if not parent:
                self._settle_retry()
            self._current[parent] = msg_id
            if msg_id not in self._drafts:
                self._drafts[msg_id] = {}
            return
        msg_id = self._current.get(parent)
        if not msg_id:
            return
        drafts = self._drafts[msg_id]
        index = e.get("index")
        if index is None:
            index = 0
        if etype == "content_block_start":
            if (self._ordinals.get(msg_id) or 0) > index:
                return  # final block already shown
            cb: ContentBlock = e.get("content_block") or {"type": ""}
            cbt = cb.get("type")
            item: Optional[Item] = None
            if cbt == "text":
                item = TextItem(id=_nid("t"), text=cb.get("text") or "", streaming=True, ts=_now())
                self._unsettled[item] = None
            elif cbt in ("thinking", "redacted_thinking"):
                item = ThinkingItem(
                    id=_nid("th"),
                    text=cb.get("thinking") or "",
                    streaming=True,
                    start_ts=_now(),
                    redacted=cbt == "redacted_thinking",
                )
                self._unsettled[item] = None
            elif cbt in ("tool_use", "server_tool_use", "mcp_tool_use") and cb.get("id"):
                existing = self.tools.get(cb["id"])
                if existing:
                    item = existing
                else:
                    t = ToolItem(
                        id=cb["id"],
                        name=cb.get("name") or cbt,
                        input={},
                        input_json="",
                        input_done=False,
                        status="streaming",
                        children=[],
                        start_ts=_now(),
                        parent_id=parent,
                    )
                    self.tools[cb["id"]] = t
                    self._unsettled[t] = None
                    item = t
            if not item:
                return
            drafts[index] = item
            if not (item.kind == "tool" and self.tools.get(item.id) and item.rev > 0):
                self._add(item, parent)
            return
        item = drafts.get(index)
        if not item:
            return
        if etype == "content_block_delta":
            d = e.get("delta") or {}
            dt = d.get("type")
            if dt == "text_delta" and item.kind == "text":
                item.text += d.get("text") or ""
            elif dt == "thinking_delta" and item.kind == "thinking":
                item.text += d.get("thinking") or ""
            elif dt == "input_json_delta" and item.kind == "tool":
                item.input_json += d.get("partial_json") or ""
                parsed = parse_partial_json(item.input_json)
                if parsed and isinstance(parsed, dict):
                    item.input = parsed
            else:
                return
            self._bump(item)
        elif etype == "content_block_stop":
            if item.kind == "thinking":
                item.streaming = False
                item.end_ts = _now()
            elif item.kind == "tool":
                item.input_done = True
                try:
                    parsed = json.loads(item.input_json or "{}")
                    if isinstance(parsed, dict):
                        item.input = parsed
                except ValueError:
                    pass  # keep partial
                if item.status == "streaming":
                    item.status = "running"
            elif item.kind == "text":
                item.streaming = False
            self._bump(item)

    def _on_assistant(self, m: RawMsg) -> None:
        if self._seen_before(m):
            return
        parent: Optional[str] = m.get("parent_tool_use_id") or None
        msg = _message_of(m)
        msg_id: str = msg.get("id") or m.get("uuid") or _nid("msg")
        ts = _parse_ts(m["timestamp"]) if m.get("timestamp") else _now()
        content = _blocks_of(msg.get("content"))
        error = m.get("error")
        if not parent:
            self._settle_retry(bool(error))
        for b in content:
            k = self._ordinals.get(msg_id) or 0
            self._ordinals[msg_id] = k + 1
            draft = self._drafts.get(msg_id, {}).get(k)
            btype = b.get("type")
            if btype == "text":
                if error:
                    continue  # API failure placeholder text; shown as an error notice below
                if draft and draft.kind == "text":
                    draft.text = b.get("text") or ""
                    draft.streaming = False
                    self._bump(draft)
                elif (b.get("text") or "").strip():
                    self._add(TextItem(id=_nid("t"), text=b["text"], streaming=False, ts=ts), parent)
            elif btype in ("thinking", "redacted_thinking"):
                if draft and draft.kind == "thinking":
                    if b.get("thinking"):
                        draft.text = b["thinking"]
                    draft.streaming = False
                    draft.end_ts = draft.end_ts or _now()
                    self._bump(draft)
                elif (b.get("thinking") or "").strip() or btype == "redacted_thinking":
                    self._add(
                        ThinkingItem(
                            id=_nid("th"),
                            text=b.get("thinking") or "",
                            streaming=False,
                            start_ts=ts,
                            end_ts=ts,
                            redacted=btype == "redacted_thinking",
                        ),
                        parent,
                    )
            elif btype in ("tool_use", "server_tool_use", "mcp_tool_use") and b.get("id"):
                t = self.tools.get(b["id"])
                if not t:
                    t = ToolItem(
                        id=b["id"],
                        name=b.get("name") or btype,
                        input=b.get("input") or {},
                        input_json="",
                        input_done=True,
                        status="running",
                        children=[],
                        start_ts=ts,
                        parent_id=parent,
                    )
                    self._unsettled[t] = None
                    self.tools[b["id"]] = t
                    self._add(t, parent)
                else:
                    t.input = b.get("input") or t.input
                    t.input_done = True
                    if t.status == "streaming":
                        t.status = "running"
                    self._bump(t)
                if not parent:
                    self.last_turn_tools.append(b["id"])
                pending = self._pending_tasks.get(b["id"])
                if pending:
                    self._apply_task(t, pending)
                    del self._pending_tasks[b["id"]]
            elif btype in ("web_search_tool_result", "web_fetch_tool_result") or (
                isinstance(btype, str) and btype.endswith("_tool_result")
            ):
                t = self.tools.get(b["tool_use_id"]) if b.get("tool_use_id") else None
                if t:
                    bc = b.get("content")
                    error_code = bc.get("error_code") if isinstance(bc, dict) else None
                    t.result = ToolResult(_summarize_server_result(b), [], bool(error_code))
                    t.structured = {"content": bc}
                    t.status = "done"
                    t.end_ts = ts
                    self._bump(t)
        if m.get("uuid") and not parent:
            self.last_chain = m["uuid"]
        if error:
            detail = "\n".join(c.get("text") for c in content if c.get("text"))
            self._last_api_error = detail
            self._add(
                NoticeItem(id=_nid("n"), level="error", text=_assistant_error_text(_js_str(error)), detail=detail), None
            )

    def _on_user(self, m: RawMsg) -> None:
        if self._seen_before(m):
            return
        parent: Optional[str] = m.get("parent_tool_use_id") or None
        msg = _message_of(m)
        ts = _parse_ts(m["timestamp"]) if m.get("timestamp") else _now()
        content = _blocks_of(msg.get("content"))
        results = [b for b in content if b.get("type") == "tool_result"]
        prev_chain = self.last_chain
        if m.get("uuid") and not parent:
            self.last_chain = m["uuid"]
        for r in results:
            t = self.tools.get(r["tool_use_id"]) if r.get("tool_use_id") else None
            if not t:
                continue
            t.result = _content_to_result(r.get("content"), bool(r.get("is_error")))
            tur = m.get("tool_use_result")
            if len(results) == 1 and tur and isinstance(tur, (dict, list)):
                t.structured = tur
            t.end_ts = ts
            t.status = _classify_result(t)
            self._bump(t)
        if results:
            return
        if parent:
            return  # a subagent's own prompt; the Agent card already shows it
        texts: List[str] = []
        images: List[str] = []
        files: List[str] = []
        for b in content:
            if b.get("type") == "text":
                text = b.get("text") or ""
                af = re.match(r'^<attached_file name="([^"]+)">', text)
                if af:
                    files.append(af.group(1))
                else:
                    texts.append(text)
            elif b.get("type") == "image":
                src = b.get("source") or {}
                if isinstance(src, dict) and src.get("type") == "base64":
                    images.append(f"data:{src.get('media_type')};base64,{src.get('data')}")
        text = "\n".join(texts).strip()
        if not text and not images and not files:
            return
        # A background task reporting back. Claude Code feeds this to the model as a prompt; in the
        # transcript it is a status line on the task, not something the person typed.
        if _origin_kind(m) == "task-notification" or text.startswith("<task-notification>"):
            tool_id = _strip_tags(text, "tool-use-id")
            tool_id = tool_id.strip() if tool_id is not None else None
            task_id = (_strip_tags(text, "task-id") or "").strip()
            status = _strip_tags(text, "status")
            status = status.strip() if status is not None else None
            summary = _strip_tags(text, "summary")
            summary = summary.strip() if summary is not None else None
            if tool_id:
                t = self.tools.get(tool_id)
            else:
                t = next((x for x in self.tools.values() if x.task_id == task_id), None)
            if t:
                self._apply_task(t, TaskInfoPatch(task_id=task_id, status=status, summary=summary), ts)
            self._add_task_line(t, summary, status)
            return
        # Claude Code's own messages: meta notes and the summary a compaction leaves for the model.
        if m.get("isMeta") or m.get("is_meta") or m.get("isSynthetic") or m.get("isCompactSummary"):
            if text.startswith("[Request interrupted"):
                self._add(NoticeItem(id=_nid("n"), level="info", text="Interrupted", tag="interrupt"), None)
            return
        if text.startswith("[Request interrupted by user"):
            self._add(NoticeItem(id=_nid("n"), level="info", text="Interrupted", tag="interrupt"), None)
            return
        if "<local-command-caveat>" in text:
            return
        stdout = _strip_tags(text, "local-command-stdout")
        if stdout is not None:
            if stdout.strip():
                self._add(CommandOutputItem(id=_nid("co"), text=stdout.strip(), ts=ts), None)
            return
        cmd_name = _strip_tags(text, "command-name")
        uuid = m.get("uuid")
        if cmd_name is not None:
            command = UserCommand(
                name=re.sub(r"^/", "", cmd_name), args=(_strip_tags(text, "command-args") or "").strip()
            )
            self._add(
                UserItem(
                    id=uuid or _nid("u"), uuid=uuid or "", text="", images=images, files=files, command=command, ts=ts
                ),
                None,
            )
            return
        self._add(
            UserItem(
                id=uuid or _nid("u"),
                uuid=uuid or "",
                text=text,
                images=images,
                files=files,
                local=bool(m.get("_local")),
                ts=ts,
                prev_uuid=prev_chain,
            ),
            None,
        )
        self.last_turn_tools = []

    def _settle_turn(self, status: str, all_: bool = False) -> None:
        """The turn is over: nothing may keep spinning. Subagent tools are included when the process died."""
        for it in list(self._unsettled):
            if it.kind == "tool":
                live = it.status in ("running", "streaming") or (all_ and it.status == "background")
                if live and ((not it.parent_id and not it.task_id) or all_):
                    it.status = status
                    self._bump(it)
                if it.status not in ("running", "streaming", "background"):
                    self._unsettled.pop(it, None)
            elif it.kind in ("text", "thinking"):
                if it.streaming:
                    it.streaming = False
                    self._bump(it)
                self._unsettled.pop(it, None)
            else:
                self._unsettled.pop(it, None)
        if all_:
            self._drafts.clear()

    def _on_result(self, m: RawMsg) -> None:
        if self._seen_before(m):
            return
        ok = m.get("subtype") == "success" and not m.get("is_error")
        self._settle_turn("done" if ok else "interrupted")
        reason = m.get("terminal_reason")
        aborted = isinstance(reason, str) and reason.startswith("aborted")
        if aborted:
            last = self.items[-1] if self.items else None
            if not (last and last.kind == "notice" and last.tag == "interrupt"):
                self._add(NoticeItem(id=_nid("n"), level="info", text="Interrupted", tag="interrupt"), None)
            self.last_turn_tools = []
            return
        api_error = self._last_api_error
        self._last_api_error = None
        # The API error was already shown as a notice; do not repeat it as the turn's result.
        if not ok and api_error and _js_str(m.get("result") or "").strip() == api_error.strip():
            self.last_turn_tools = []
            return
        usage = m.get("usage") or {}
        self._add(
            ResultItem(
                id=m.get("uuid") or _nid("r"),
                ok=ok,
                subtype=m.get("subtype") or "",
                duration_ms=m.get("duration_ms") or 0,
                cost_usd=m.get("total_cost_usd") or 0,
                turns=m.get("num_turns") or 0,
                input_tokens=(usage.get("input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0),
                output_tokens=usage.get("output_tokens") or 0,
                text=None if ok else m.get("result"),
                errors=m.get("errors"),
                chain_uuid=self.last_chain,
            ),
            None,
        )
        self.last_turn_tools = []

    def _on_system(self, m: RawMsg) -> None:
        if self._seen_before(m):
            return
        subtype = m.get("subtype")
        content = m.get("content")
        if subtype == "compact_boundary":
            meta = m.get("compact_metadata") or {}
            self._add(
                CompactItem(
                    id=_nid("c"),
                    trigger=meta.get("trigger") or "auto",
                    pre_tokens=meta.get("pre_tokens"),
                    post_tokens=meta.get("post_tokens"),
                ),
                None,
            )
        elif subtype == "informational":
            if m.get("level") == "info" or not content:
                return
            if content in self._info_seen:
                return
            self._info_seen.add(content)
            level = "warning" if m.get("level") == "warning" else "info"
            self._add(NoticeItem(id=_nid("n"), level=level, text=content, tag="cli-info"), None)
        elif subtype == "local_command_output":
            if isinstance(content, str) and content.strip():
                self._add(CommandOutputItem(id=_nid("co"), text=content), None)
        elif subtype == "api_retry":
            last = self.items[-1] if self.items else None
            status = m.get("error_status")
            text = f"Retrying request (attempt {_js_str(m.get('attempt'))} of {_js_str(m.get('max_retries'))})"
            if status:
                text += f" after HTTP {status}"
            self._retry_status = status or None
            if last and last.kind == "notice" and last.tag == "retry" and last is self._active_retry:
                last.text = text
                last.count = (last.count or 1) + 1
                self._bump(last)
            else:
                err = m.get("error")
                n = NoticeItem(
                    id=_nid("n"), level="warning", text=text, tag="retry", detail=None if err is None else _js_str(err)
                )
                self._active_retry = n
                self._add(n, None)
        elif subtype == "permission_denied":
            t = self.tools.get(m["tool_use_id"]) if m.get("tool_use_id") else None
            if t:
                t.status = "denied"
                self._bump(t)
            message = m.get("message")
            detail = m.get("decision_reason") or (message if isinstance(message, str) else None)
            self._add(
                NoticeItem(
                    id=_nid("n"), level="warning", text=f"{_js_str(m.get('tool_name'))} was not allowed", detail=detail
                ),
                None,
            )
        elif subtype in ("model_refusal_fallback", "model_refusal_no_fallback"):
            self._add(
                NoticeItem(id=_nid("n"), level="warning", text=content or "The model declined this request."), None
            )
        elif subtype == "pilcrow_error":
            self._settle_turn("interrupted", True)
            self._add(
                NoticeItem(id=_nid("n"), level="error", text="Claude Code stopped unexpectedly", detail=content), None
            )
        elif subtype in ("task_started", "task_notification", "task_updated"):
            tool_id = m.get("tool_use_id")
            patch = m.get("patch") or {}
            info = TaskInfoPatch(
                task_id=m.get("task_id") or "",
                status="running" if subtype == "task_started" else (m.get("status") or patch.get("status")),
                summary=m.get("summary"),
                output_file=m.get("output_file"),
                usage=m.get("usage"),
            )
            if tool_id:
                t = self.tools.get(tool_id)
            else:
                t = next((x for x in self.tools.values() if x.task_id == m.get("task_id")), None)
            # Work that was already running in the background gets a line when it reports back, because
            # Claude usually picks the conversation up again right after it.
            was_background = bool(t and (t.status == "background" or t.result is not None))
            if t:
                self._apply_task(t, info)
            elif tool_id:
                self._pending_tasks[tool_id] = info
            if subtype == "task_notification" and was_background:
                self._add_task_line(t, m.get("summary"), info.status)

    def _add_task_line(self, t: Optional[ToolItem], summary: Optional[str], status: Optional[str] = None) -> None:
        if status == "failed":
            outcome = "failed"
        elif status in ("killed", "stopped"):
            outcome = "was stopped"
        else:
            outcome = "finished"
        inp = t.input if t and isinstance(t.input, dict) else {}
        desc = (inp.get("description") if isinstance(inp.get("description"), str) else "").strip()
        text = summary or ""
        if t and t.name in ("Agent", "Task"):
            text = f"Agent “{desc}” {outcome}" if desc else f"Agent {outcome}"
        elif t and t.name in ("Bash", "PowerShell"):
            cmd = _js_str(inp.get("command") or "").split("\n")[0][:60]
            text = f"Background command “{desc or cmd}” {outcome}"
        if not text:
            return
        level = "warning" if status == "failed" else "info"
        self._add(NoticeItem(id=_nid("n"), level=level, text=text, tag="task"), None)

    def _apply_task(self, t: ToolItem, info: TaskInfoPatch, at: Optional[float] = None) -> None:
        if at is None:
            at = _now()
        t.task_id = info.task_id
        prev = t.task
        # Fields the message gave override what the tool already knew; unknown ones keep the old value.
        t.task = TaskInfoPatch(
            task_id=info.task_id,
            status=info.status or (prev.status if prev else None) or "running",
            summary=info.summary if info.summary is not None else (prev.summary if prev else None),
            output_file=info.output_file if info.output_file is not None else (prev.output_file if prev else None),
            usage=info.usage if info.usage is not None else (prev.usage if prev else None),
        )
        if info.status and info.status in ("completed", "failed", "stopped", "killed"):
            t.status = "done" if info.status == "completed" else "error" if info.status == "failed" else "interrupted"
            t.end_ts = at
        elif info.status == "running" and t.status != "done":
            t.status = "background" if t.result is not None else "running"
        self._bump(t)

    def _on_tool_progress(self, m: RawMsg) -> None:
        t = self.tools.get(m["tool_use_id"]) if m.get("tool_use_id") else None
        if not t:
            return
        t.elapsed = m.get("elapsed_time_seconds")
        if t.status == "streaming":
            t.status = "running"
        self._bump(t)

    def _on_rate_limit(self, m: RawMsg) -> None:
        info = m.get("rate_limit_info") or {}
        if info.get("status") == "rejected":
            resets = info.get("resetsAt")
            detail = f"Resets {datetime.fromtimestamp(resets).strftime('%c')}" if resets else None
            self._add(NoticeItem(id=_nid("n"), level="error", text="Usage limit reached", detail=detail), None)

    def latest_tool(self, name: Union[str, Sequence[str]]) -> Optional[ToolItem]:
        """Find the most recent tool item with this name (used for plans and todos)."""
        names = [name] if isinstance(name, str) else list(name)
        found: Optional[ToolItem] = None
        for t in self.tools.values():
            if t.name in names and (not found or t.start_ts >= found.start_ts):
                found = t
        return found


def _classify_result(t: ToolItem) -> ToolStatus:
    r = t.result
    assert r is not None
    s = t.structured
    if isinstance(s, dict):
        if s.get("isAsync") or s.get("status") == "async_launched" or (s.get("backgroundTaskId") and not r.is_error):
            if t.task and t.task.status != "running":
                return "done" if t.task.status == "completed" else "interrupted"
            return "background"
    if not r.is_error:
        return "done"
    txt = r.text or ""
    if re.search(r"interrupted by user", txt, re.I):
        return "interrupted"
    if re.search(
        r"doesn't want to proceed|user declined|was rejected|permission to use .* (has been|was) denied|not allowed",
        txt,
        re.I,
    ):
        return "denied"
    return "error"


_ASSISTANT_ERRORS = {
    "authentication_failed": "Authentication failed — check the API key or gateway token Claude Code uses.",
    "billing_error": "The API account has a billing problem.",
    "rate_limit": "Rate limited by the API. Claude Code will retry.",
    "overloaded": "The API is overloaded right now.",
    "invalid_request": "The API rejected the request.",
    "model_not_found": "The selected model is not available through this endpoint.",
    "server_error": "The API returned a server error.",
    "max_output_tokens": "The response hit the output token limit.",
}


def _assistant_error_text(code: str) -> str:
    return _ASSISTANT_ERRORS.get(code) or f"Request failed ({code})"


def _summarize_server_result(b: ContentBlock) -> str:
    c = b.get("content")
    if isinstance(c, list):
        lines = []
        for r in c:
            if not isinstance(r, dict):
                continue
            lines.append(f"{r['title']} — {r.get('url')}" if r.get("title") else r.get("url") or "")
        return "\n".join(x for x in lines if x)
    if isinstance(c, dict) and c.get("error_code"):
        return f"Error: {c['error_code']}"
    if isinstance(c, str):
        return c
    return json.dumps(c, indent=2, ensure_ascii=False) if c is not None else ""


# ---------------------------------------------------------------- raw message shapes


def _message_of(m: RawMsg) -> Dict[str, Any]:
    """The message body of a raw assistant/user message; the SDK sometimes sends a bare string."""
    msg = m.get("message")
    if not msg:
        return {}
    if isinstance(msg, str):
        return {"content": msg}
    return msg


def _blocks_of(content: Any) -> List[ContentBlock]:
    """Content as blocks: a plain string is one text block."""
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _origin_kind(m: RawMsg) -> Optional[str]:
    """`origin` is an object on task notifications and a string elsewhere."""
    o = m.get("origin")
    if isinstance(o, dict):
        return o.get("kind")
    return o if isinstance(o, str) else None
