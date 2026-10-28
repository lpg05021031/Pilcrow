"""Workflow runs: the script's meta literal, agent states and phase columns (port of web/src/lib/workflow.ts).

Agents, phases and tasks are the plain dicts the server sends (camelCase keys, see web/src/lib/types.ts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

WorkflowAgent = Dict[str, Any]
WorkflowPhase = Dict[str, Any]
TaskInfo = Dict[str, Any]


@dataclass
class PhaseMeta:
    title: str
    kind: Optional[str] = None


@dataclass
class WorkflowMeta:
    name: Optional[str] = None
    description: Optional[str] = None
    phases: List[PhaseMeta] = field(default_factory=list)


_UNESCAPE = re.compile(r"\\(.)")


def parse_workflow_meta(script: Optional[str]) -> WorkflowMeta:
    """
    The workflow script opens with `export const meta = { name, description, phases }` as a pure
    literal. Read it without executing anything so the card has a title and columns before the
    first progress event arrives.
    """
    out = WorkflowMeta()
    if not script:
        return out
    m = re.search(r"export\s+const\s+meta\s*=\s*\{", script)
    if not m:
        return out
    # The body runs to the brace that closes the literal, not the first `}` inside it (phases are
    # objects of their own), so walk the braces, skipping over strings.
    start = m.end()
    depth = 1
    i = start
    quote: Optional[str] = None
    while i < len(script) and depth > 0:
        c = script[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in ("'", '"', "`"):
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    body = script[start : i - 1 if depth == 0 else i]

    def string(key: str) -> Optional[str]:
        r = re.search(rf"\b{key}\s*:\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|`((?:[^`\\]|\\.)*)`)", body)
        if not r:
            return None
        raw = next(g for g in (r.group(1), r.group(2), r.group(3)) if g is not None)
        return _UNESCAPE.sub(r"\1", raw)

    out.name = string("name")
    out.description = string("description")
    ph = re.search(r"\bphases\s*:\s*\[([\s\S]*?)\]", body)
    if ph:
        for x in re.finditer(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|\{([^}]*)\}", ph.group(1)):
            if x.group(3) is not None:
                t = re.search(r"\b(?:title|name)\s*:\s*(?:'([^']*)'|\"([^\"]*)\")", x.group(3))
                k = re.search(r"\bkind\s*:\s*(?:'([^']*)'|\"([^\"]*)\")", x.group(3))
                title = (t.group(1) if t.group(1) is not None else t.group(2)) if t else f"Phase {len(out.phases) + 1}"
                kind = (k.group(1) if k.group(1) is not None else k.group(2)) if k else None
                out.phases.append(PhaseMeta(title=title, kind=kind))
            else:
                raw = x.group(1) if x.group(1) is not None else x.group(2)
                out.phases.append(PhaseMeta(title=_UNESCAPE.sub(r"\1", raw)))
    return out


# 'queued' | 'running' | 'done' | 'error' | 'cached' | 'blocked'
AgentState = str


def agent_state(a: WorkflowAgent) -> AgentState:
    state = a.get("state")
    if state == "done":
        return "cached" if a.get("cached") else "done"
    if state == "error":
        return "blocked" if a.get("blocked") else "error"
    if state == "start" and "queuedAt" in a and "startedAt" not in a:
        return "queued"
    return "running"


@dataclass
class WorkflowCounts:
    total: int = 0
    queued: int = 0
    running: int = 0
    done: int = 0
    error: int = 0


def workflow_counts(agents: Sequence[Optional[WorkflowAgent]]) -> WorkflowCounts:
    c = WorkflowCounts()
    for a in agents:
        if not a:
            continue
        c.total += 1
        s = agent_state(a)
        if s == "queued":
            c.queued += 1
        elif s == "running":
            c.running += 1
        elif s in ("done", "cached"):
            c.done += 1
        else:
            c.error += 1
    return c


@dataclass
class WorkflowColumn:
    phase: WorkflowPhase
    agents: List[WorkflowAgent] = field(default_factory=list)


def workflow_columns(task: Optional[TaskInfo], meta: WorkflowMeta) -> List[WorkflowColumn]:
    """Phases as columns: the announced ones, then any the script declared that have not started yet."""
    workflow = (task or {}).get("workflow") or {}
    phases: Dict[int, WorkflowPhase] = {}
    seen = set()
    for p in workflow.get("phases") or []:
        if p:
            phases[p["index"]] = p
            seen.add(p["index"])
    for i, p in enumerate(meta.phases):
        if i not in seen:
            phases[i] = {"index": i, "title": p.title, "kind": p.kind}
    cols = [WorkflowColumn(phase=phases[k]) for k in sorted(phases)]
    by_index = {c.phase["index"]: c for c in cols}
    for a in workflow.get("agents") or []:
        if not a:
            continue
        pi = a.get("phaseIndex")
        if pi is None:
            pi = 0
        col = by_index.get(pi)
        if not col:
            col = WorkflowColumn(phase={"index": pi, "title": a.get("phaseTitle") or f"Phase {pi + 1}"})
            by_index[pi] = col
            cols.append(col)
            cols.sort(key=lambda c: c.phase["index"])
        col.agents.append(a)
    for c in cols:
        c.agents.sort(key=lambda x: x["index"])
    return cols
