"""Folding a transcript into units: the words stay out, each stretch of work folds (port of web/src/lib/units.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List, Union

from .transcript_model import Item


@dataclass
class ItemsUnit:
    kind: ClassVar[str] = "items"
    key: str
    items: List[Item] = field(default_factory=list)


@dataclass
class WorkUnit:
    kind: ClassVar[str] = "work"
    key: str
    work: List[Item] = field(default_factory=list)
    live: bool = False


Unit = Union[ItemsUnit, WorkUnit]


def is_work(it: Item) -> bool:
    return it.kind == "tool" or it.kind == "thinking"


def group_turns(items: List[Item], running: bool) -> List[Unit]:
    """
    Whatever Claude says stays on the page, every time it says something: a turn can think, write a
    paragraph, think and work some more, then write again, and each paragraph is part of the answer.
    Each stretch of work between those paragraphs — reading, searching, editing, the thinking in
    between — folds to its own one-line summary. The stretch under way is live until the turn ends.
    """
    out: List[Unit] = []
    run: List[Item] = []
    plain: List[Item] = []

    def flush_plain() -> None:
        nonlocal plain
        if plain:
            out.append(ItemsUnit(key="P-" + plain[0].id, items=plain))
        plain = []

    def flush_run(live: bool) -> None:
        nonlocal run
        if not run:
            return
        if any(is_work(it) for it in run):
            flush_plain()
            out.append(WorkUnit(key="W-" + run[0].id, work=run, live=live))
        else:
            plain.extend(run)  # only notices: nothing to fold
        run = []

    n = len(items)
    for i, it in enumerate(items):
        last = i == n - 1
        boundary = it.kind == "user" or it.kind == "compact" or (it.kind == "notice" and it.tag == "divider")
        if is_work(it) or (it.kind == "notice" and run and not boundary):
            run.append(it)
        else:
            flush_run(False)
            plain.append(it)
        if last:
            flush_run(running)
    flush_plain()
    return out


def live_turn_folds(items: List[Item]) -> bool:
    """True when the stretch under way has work of its own on the page to carry the working mark."""
    for it in reversed(items):
        if is_work(it):
            return True
        if it.kind != "notice":
            return False
    return False
