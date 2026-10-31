"""Presentation metadata for Claude Code tools: icon, a verb, and a one-line target (port of web/src/lib/tools.ts)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .fmt import first_line, rel_path, truncate

ToolInput = Dict[str, Any]


def str_(i: Optional[ToolInput], k: str) -> str:
    """A string field of a tool input, or '' — inputs are loose JSON and a wrong type must not throw."""
    v = i.get(k) if isinstance(i, dict) else None
    return v if isinstance(v, str) else ""


def num(i: Optional[ToolInput], k: str) -> Optional[float]:
    v = i.get(k) if isinstance(i, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _js_str(v: Any) -> str:
    """String(v) for the scalar JSON values a tool input carries."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@dataclass
class ToolMeta:
    icon_name: str  # the Lucide kebab-case name, e.g. 'square-terminal'
    verb: str
    target: str
    mono: bool = False


def _short(p: str, cwd: Optional[str] = None) -> str:
    return rel_path(p, cwd) if p else ""


def tool_meta(name: str, tool_input: Optional[ToolInput], cwd: Optional[str] = None) -> ToolMeta:
    i: ToolInput = tool_input if isinstance(tool_input, dict) else {}

    def s(k: str) -> str:
        return str_(i, k)

    def n(k: str) -> Optional[float]:
        return num(i, k)

    if name in ("Bash", "PowerShell"):
        verb = "" if s("description") else ("Run in background" if i.get("run_in_background") else "Run")
        return ToolMeta(
            "square-terminal", verb, s("description") or first_line(s("command") or ""), mono=not s("description")
        )
    if name == "BashOutput":
        return ToolMeta("square-terminal", "Read output", s("bash_id") or s("shell_id") or "")
    if name in ("KillShell", "KillBash", "TaskStop"):
        return ToolMeta("circle-stop", "Stop task", s("task_id") or s("shell_id") or "")
    if name == "Read":
        rng = ""
        if n("offset"):
            rng = f":{_js_str(n('offset'))}"
            if n("limit"):
                rng += "–" + _js_str(n("offset") + n("limit"))  # type: ignore[operator]
        return ToolMeta("file-text", "Read", _short(s("file_path"), cwd) + rng, mono=True)
    if name in ("Edit", "MultiEdit"):
        return ToolMeta("file-pen", "Edit", _short(s("file_path"), cwd), mono=True)
    if name == "Write":
        return ToolMeta("file-plus", "Write", _short(s("file_path"), cwd), mono=True)
    if name == "NotebookEdit":
        return ToolMeta("book-open", "Edit notebook", _short(s("notebook_path"), cwd), mono=True)
    if name == "Grep":
        where = " in " + _short(s("path"), cwd) if s("path") else ""
        glob = " (" + s("glob") + ")" if s("glob") else ""
        return ToolMeta("search", "Search", f"{s('pattern') or ''}{where}{glob}", mono=True)
    if name == "Glob":
        where = " in " + _short(s("path"), cwd) if s("path") else ""
        return ToolMeta("folder-search", "Find files", f"{s('pattern') or ''}{where}", mono=True)
    if name == "WebFetch":
        return ToolMeta("globe", "Fetch", s("url") or "")
    if name in ("WebSearch", "web_search"):
        return ToolMeta("globe", "Search the web", s("query") or "")
    if name == "TodoWrite":
        return ToolMeta("list-checks", "Update to-dos", _todo_summary(i.get("todos")))
    if name == "TaskCreate":
        return ToolMeta("list-todo", "Add task", s("subject") or "")
    if name == "TaskUpdate":
        status = " → " + s("status").replace("_", " ", 1) if s("status") else ""
        subject = " " + s("subject") if s("subject") else ""
        return ToolMeta("list-todo", "Update task", f"#{s('taskId')}{status}{subject}")
    if name in ("TaskList", "TaskGet"):
        return ToolMeta("list-todo", "Check tasks", f"#{s('taskId')}" if s("taskId") else "")
    if name in ("Workflow", "RunWorkflow"):
        script = i.get("script")
        m = re.search(r"\bname\s*:\s*['\"`]([^'\"`]+)['\"`]", script) if isinstance(script, str) else None
        return ToolMeta("workflow", "Workflow", s("name") or (m.group(1) if m else "") or s("description") or "")
    if name in ("Agent", "Task"):
        sub = s("subagent_type")
        verb = f"Agent · {sub}" if sub and sub != "general-purpose" else "Agent"
        return ToolMeta("bot", verb, s("description") or truncate(first_line(s("prompt") or ""), 80))
    if name == "SendMessage":
        return ToolMeta("bot", "Message agent", s("to") or "")
    if name == "ExitPlanMode":
        return ToolMeta("clipboard-check", "Plan ready", "")
    if name == "EnterPlanMode":
        return ToolMeta("map", "Enter plan mode", "")
    if name == "AskUserQuestion":
        qs = i.get("questions")
        headers = [q.get("header") for q in qs if isinstance(q, dict)] if isinstance(qs, list) else []
        return ToolMeta("message-circle-question-mark", "Question", " · ".join(h for h in headers if h))
    if name == "Skill":
        return ToolMeta("sparkles", "Use skill", s("skill") or s("name") or s("command") or "")
    if name == "SlashCommand":
        return ToolMeta("sparkles", "Run command", s("command") or "")
    if name == "ToolSearch":
        return ToolMeta("layers", "Load tools", s("query") or "")
    if name in ("EnterWorktree", "ExitWorktree"):
        verb = "Enter worktree" if name == "EnterWorktree" else "Leave worktree"
        return ToolMeta("git-branch", verb, s("name") or s("path") or "")
    if name in ("CronCreate", "CronDelete", "CronList", "ScheduleWakeup"):
        verb = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
        return ToolMeta("clock", verb, s("schedule") or s("cron_expression") or s("prompt") or "")
    if name == "PushNotification":
        return ToolMeta("bell", "Notify", s("message") or "")
    if name in ("ListMcpResourcesTool", "ReadMcpResourceTool"):
        verb = "Read resource" if name == "ReadMcpResourceTool" else "List resources"
        return ToolMeta("plug", verb, s("uri") or s("server") or "")
    if name.startswith("mcp__helpers__"):
        # the lead's tools (HELPERS.md): named by what happened, not by the server
        tool = name[len("mcp__helpers__") :]
        icon, verb = {
            "start": ("arrow-up-right", "Started helper"),
            "wait": ("clock", "Waited for"),
            "read": ("eye", "Read helper"),
            "send": ("send", "Told helper"),
            "stop": ("square", "Stopped helper"),
            "list": ("list", "Listed helpers"),
        }.get(tool, ("arrow-up-right", f"Helpers · {tool}"))
        if tool == "wait":
            names = i.get("names") if isinstance(i.get("names"), list) else None
            target = ", ".join(str(n) for n in names) if names else "all helpers"
        elif tool == "list":
            target = ""
        else:
            target = s("name")
            where = s("repo") or (_short(s("cwd"), cwd) if s("cwd") else "")
            if tool == "start" and where:
                target = f"{target} in {where}"
        return ToolMeta(icon, verb, target)
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else ""
        tool = "__".join(parts[2:]).replace("_", " ")
        # Servers are often registered under an id rather than a name; an id tells the reader nothing.
        opaque = (
            re.match(r"^[0-9a-f-]{20,}$", server, re.I) is not None
            or re.match(r"^[0-9a-z]{24,}$", server, re.I) is not None
        )
        verb = tool if opaque else f"{re.sub(r'[_-]+', ' ', server)} · {tool}"
        verb = re.sub(r"^.", lambda m: m.group(0).upper(), verb, count=1)
        return ToolMeta("plug", verb, _summarize_mcp(i))
    return ToolMeta("wrench", name, _summarize_input(i))


def _todo_summary(todos: Any) -> str:
    if not isinstance(todos, list):
        return ""
    items = [t for t in todos if isinstance(t, dict)]
    done = sum(1 for t in items if t.get("status") == "completed")
    active = next((t for t in items if t.get("status") == "in_progress"), None)
    tail = " · " + str(active.get("activeForm") or active.get("content")) if active else ""
    return f"{done}/{len(todos)} done{tail}"


def _summarize_mcp(i: ToolInput) -> str:
    """The gist of an MCP call: the one or two short values that say what it was asked to do."""
    if not isinstance(i, dict):
        return ""
    pref = ["action", "prompt", "query", "name", "path", "url", "command", "text", "title", "id"]
    entries = [(k, v) for k, v in i.items() if isinstance(v, (str, int, float, bool))]

    def rank(e: tuple) -> int:
        return pref.index(e[0]) + 1 if e[0] in pref else 99

    entries.sort(key=rank)
    return " · ".join(f"{k.replace('_', ' ')} {truncate(first_line(_js_str(v)), 40)}" for k, v in entries[:2])


def _summarize_input(i: ToolInput) -> str:
    if not isinstance(i, dict):
        return ""
    for k in ("path", "file_path", "url", "query", "name", "command", "pattern", "id"):
        v = i.get(k)
        if isinstance(v, str):
            return truncate(v, 90)
    first = next((v for v in i.values() if isinstance(v, str)), None)
    return truncate(first_line(first), 90) if first else ""


def expand_by_default(name: str) -> bool:
    """Tools whose cards should start expanded."""
    return name in ("Edit", "MultiEdit", "Write", "ExitPlanMode", "AskUserQuestion", "TodoWrite", "NotebookEdit")


def is_file_tool(name: str) -> bool:
    return name in ("Read", "Edit", "MultiEdit", "Write", "NotebookEdit")


def file_path_of(name: str, tool_input: Optional[ToolInput]) -> Optional[str]:
    if not tool_input:
        return None
    return str_(tool_input, "file_path") or str_(tool_input, "notebook_path") or None
