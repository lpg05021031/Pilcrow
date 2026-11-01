"""How a turn's work reads: the pure helpers behind the folds (the functions at the top of
web/src/components/Transcript.tsx). What a stretch of work is called, what Claude is doing right
now, which steps are quiet enough to fold into one "Explored" line, and how items group into runs.
No Qt here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Set, Union

from .fmt import basename, truncate
from .tools_meta import str_
from .transcript_model import Item, ToolItem

if TYPE_CHECKING:  # pragma: no cover
    from ..widgets.transcript.ctx import ToolCtx


SPECIAL = ("AskUserQuestion", "ExitPlanMode")
QUIET = {
    "Read",
    "Grep",
    "Glob",
    "LS",
    "WebSearch",
    "WebFetch",
    "ToolSearch",
    "BashOutput",
    "TaskList",
    "TaskGet",
    "NotebookRead",
}


def tool_word(t: ToolItem) -> str:
    n = t.name
    if n in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "edit"
    if n in ("Read", "NotebookRead"):
        return "read"
    if n in ("Grep", "Glob", "LS", "WebSearch", "WebFetch", "ToolSearch"):
        return "search"
    if n in ("Bash", "PowerShell", "BashOutput"):
        return "command"
    if n in ("Task", "Agent", "Workflow", "RunWorkflow"):
        return "agent"
    if n.startswith("mcp__helpers__"):
        return "helper"
    if n == "AskUserQuestion":
        return "ask"
    if n == "ExitPlanMode":
        return "plan"
    if n.startswith("mcp__"):
        return "mcp"
    return "other"


HELPER_LIVE = {
    "start": "Starting a helper",
    "wait": "Waiting for helpers",
    "read": "Reading a helper's result",
    "send": "Telling a helper",
    "stop": "Stopping a helper",
    "list": "Listing helpers",
}


def live_label(work: List[Item]) -> Optional[str]:
    """What Claude is doing right now, for the fold line of a turn that is still running."""
    for it in reversed(work):
        if it.kind == "tool" and it.status in ("running", "streaming"):
            file = str_(it.input, "file_path") or str_(it.input, "notebook_path")
            w = tool_word(it)
            if w == "edit":
                return f"Editing {basename(file)}" if file else "Editing a file"
            if w == "read":
                return f"Reading {basename(file)}" if file else "Reading a file"
            if w == "search":
                return "Searching"
            if w == "command":
                d = it.input.get("description") if isinstance(it.input, dict) else None
                return truncate(str(d), 64) if d else "Running a command"
            if w == "agent":
                return "Running an agent"
            if w == "mcp":
                return "Using a connector"
            if w == "helper":
                return HELPER_LIVE.get(it.name[len("mcp__helpers__") :], "Working with helpers")
            if w == "ask":
                return "Asking you a question"
            if w == "plan":
                return "Waiting for your review of the plan"
            return f"Using {it.name}"
        if it.kind == "thinking" and it.streaming:
            return "Thinking"
    return None


@dataclass
class Summary:
    text: str
    failed: int
    steps: int


def work_summary(work: List[Item]) -> Summary:
    tools = [it for it in work if it.kind == "tool"]
    files: Set[str] = set()
    reads = searches = commands = agents = mcp = other = failed = 0
    starts = helper_steps = asks = plans = 0
    for t in tools:
        if t.status in ("error", "denied"):
            failed += 1
        w = tool_word(t)
        if w == "helper":
            if t.name.endswith("__start"):
                starts += 1
            else:
                helper_steps += 1
        elif w == "ask":
            asks += 1
        elif w == "plan":
            plans += 1
        elif w == "edit":
            files.add(str_(t.input, "file_path") or str_(t.input, "notebook_path") or t.id)
        elif w == "read":
            reads += 1
        elif w == "search":
            searches += 1
        elif w == "command":
            commands += 1
        elif w == "agent":
            agents += 1
        elif w == "mcp":
            mcp += 1
        else:
            other += 1
    for it in work:
        if it.kind == "notice" and it.level == "error":
            failed += 1
    parts: List[str] = []
    if files:
        parts.append(f"edited {'a file' if len(files) == 1 else f'{len(files)} files'}")
    if commands:
        parts.append(f"ran {'a command' if commands == 1 else f'{commands} commands'}")
    if reads:
        parts.append(f"read {'a file' if reads == 1 else f'{reads} files'}")
    if searches:
        parts.append(f"searched {'once' if searches == 1 else f'{searches} times'}")
    if agents:
        parts.append("ran an agent" if agents == 1 else f"ran {agents} agents")
    if starts:
        parts.append(f"started {'a helper' if starts == 1 else f'{starts} helpers'}")
    if helper_steps and not starts:
        parts.append("worked with helpers")
    if asks:
        parts.append(f"asked you {'a question' if asks == 1 else f'{asks} questions'}")
    if plans:
        parts.append("proposed a plan")
    if mcp:
        parts.append(f"used {'a connector' if mcp == 1 else f'{mcp} connector tools'}")
    if other:
        parts.append(f"{other} other {'step' if other == 1 else 'steps'}")
    if not parts:
        parts.append("thought it through" if any(it.kind == "thinking" for it in work) else "worked")
    text = ", ".join(parts)
    return Summary(text[:1].upper() + text[1:], failed, len(tools))


def is_quiet(t: ToolItem, ctx: ToolCtx) -> bool:
    if t.id in ctx.pending or t.status != "done":
        return False
    bg = isinstance(t.input, dict) and bool(t.input.get("run_in_background"))
    return t.name in QUIET or (t.name in ("Bash", "PowerShell") and not (t.result and t.result.is_error) and not bg)


def explore_label(items: List[ToolItem]) -> str:
    def n(names: tuple) -> int:
        return sum(1 for t in items if t.name in names)

    parts: List[str] = []
    cmds, reads = n(("Bash", "PowerShell", "BashOutput")), n(("Read", "NotebookRead"))
    searches, web, tasks = (
        n(("Grep", "Glob", "LS", "ToolSearch")),
        n(("WebSearch", "WebFetch")),
        n(("TaskList", "TaskGet")),
    )
    if cmds:
        parts.append("ran a command" if cmds == 1 else f"ran {cmds} commands")
    if reads:
        parts.append("read a file" if reads == 1 else f"read {reads} files")
    if searches:
        parts.append("searched once" if searches == 1 else f"searched {searches} times")
    if web:
        parts.append("checked the web" if web == 1 else f"checked {web} web pages")
    if tasks:
        parts.append("checked tasks")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


@dataclass
class StepsGroup:
    key: str
    items: List[ToolItem]


@dataclass
class ItemGroup:
    key: str
    item: Item


Group = Union[StepsGroup, ItemGroup]


def group_items(items: List[Item]) -> List[Group]:
    out: List[Group] = []
    run: List[ToolItem] = []

    def flush() -> None:
        nonlocal run
        if run:
            out.append(StepsGroup("s-" + run[0].id, run))
        run = []

    for it in items:
        if it.kind == "tool" and it.name not in SPECIAL:
            run.append(it)
        else:
            flush()
            out.append(ItemGroup(it.id, it))
    flush()
    return out


@dataclass
class ToolSeg:
    item: ToolItem


@dataclass
class ExploreSeg:
    key: str
    items: List[ToolItem]


Seg = Union[ToolSeg, ExploreSeg]
