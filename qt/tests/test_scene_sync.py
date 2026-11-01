"""The transcript scene against its model, without a window: the settled prefix is kept across
flushes and only the tail is touched, yet the scene never shows a unit the model has moved on from.
Two ways that could go wrong: a scene kept in the cache meets the same conversation loaded again (new
items, the same user uuids, so the unit keys agree while the blocks inside are of the old items), and
a conversation arriving in slices has a turn cut in two (its group is made from the first half and must
be synced when the rest comes, although it then counts as settled)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_lib_transcript import assistant, result, tool_result, user_msg  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.canvas.blocks.fold import unit_group_key  # noqa: E402
from pilcrow_qt.canvas.transcript import TranscriptScene  # noqa: E402
from pilcrow_qt.lib.transcript_model import TranscriptModel  # noqa: E402
from pilcrow_qt.store import Runtime  # noqa: E402

SHOWN = ("text", "user", "tool")


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a


def conversation(turns: int) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = []
    for i in range(turns):
        msgs.append(user_msg(f"Turn {i}: check module {i}"))
        if i % 2:
            tid = f"tool-{i}"
            msgs.append(assistant(f"m-{i}a", [{"type": "text", "text": f"Reading module {i}."}]))
            msgs.append(
                assistant(
                    f"m-{i}b", [{"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": f"/p/{i}"}}]
                )
            )
            msgs.append(tool_result(tid, f"line of module {i}\n" * 5))
            msgs.append(assistant(f"m-{i}c", [{"type": "text", "text": f"Module {i} is fine."}]))
        else:
            msgs.append(assistant(f"m-{i}a", [{"type": "text", "text": f"Module {i} parses the configuration."}]))
        msgs.append(result())
    return msgs


def model_of(msgs: List[Dict[str, Any]]) -> TranscriptModel:
    m = TranscriptModel()
    m.push_all(msgs)
    return m


def check_whole(sc: TranscriptScene, m: TranscriptModel) -> None:
    """Every shown item of the model has a block, and the root's children are the units in order."""
    for it in m.items:
        if it.kind in SHOWN:
            assert sc.block_for_item(it.id) is not None, (it.kind, it.id)
    keys = [c.key for c in sc.root.children]
    assert keys == ["skeleton"] + [unit_group_key(u) for u in sc.units] + ["working"]
    # the groups hold the model's own items, not those of an earlier model
    for u, g in zip(sc.units, sc.root.children[1:-1]):
        items = u.work if u.kind == "work" else u.items
        held = getattr(g, "items", None)
        if held is not None:
            assert all(a is b for a, b in zip(held, items)) and len(held) == len(items), g.key


def settled_groups(sc: TranscriptScene) -> List[Any]:
    return list(sc.root.children[1 : 1 + len(sc.settled_units)])


def test_a_cached_scene_meets_the_conversation_loaded_again(app):
    msgs = conversation(8)
    rt = Runtime()
    rt.loaded = True
    sc = TranscriptScene("s", "/p")
    m1 = model_of(msgs)
    sc.sync(m1, rt, "normal")
    check_whole(sc, m1)
    settled = sc.settled_count
    assert settled and len(sc.settled_units) == 7
    # the same conversation modelled again: new item objects (and ids for the texts), the same uuids
    m2 = model_of(msgs)
    assert [it.id for it in m2.items if it.kind == "text"] != [it.id for it in m1.items if it.kind == "text"]
    sc.sync(m2, rt, "normal")
    check_whole(sc, m2)
    # and a flush with nothing new leaves the history alone (the live tail is always looked at)
    past = settled_groups(sc)
    assert not any(c in past for c in sc.sync(m2, rt, "normal"))
    check_whole(sc, m2)


def test_a_turn_cut_by_a_loading_slice_is_completed_when_the_rest_comes(app):
    msgs = conversation(6)
    # cut inside turn 3 (a tool turn): its user message and first text are in the first slice
    cut = next(i for i, mm in enumerate(msgs) if mm.get("message", {}).get("id") == "m-3b")
    rt = Runtime()
    rt.loading = True
    sc = TranscriptScene("s", "/p")
    m = TranscriptModel()
    m.push_all(msgs[:cut])
    sc.sync(m, rt, "normal")
    half = sc.root.children[-2]
    assert half.key == unit_group_key(sc.units[-1])
    m.push_all(msgs[cut:])
    rt.loading, rt.loaded = False, True
    sc.sync(m, rt, "normal")
    check_whole(sc, m)
    # the turn's tool (it came in the second slice) has its block
    tool = next(it for it in m.items if it.kind == "tool" and it.id == "tool-3")
    assert sc.block_for_item(tool.id) is not None
    # settled since: later flushes do not touch it again
    past = settled_groups(sc)
    assert not any(c in past for c in sc.sync(m, rt, "normal"))


def test_flushes_on_a_long_history_touch_the_tail_only(app):
    msgs = conversation(40)
    rt = Runtime()
    rt.loaded = True
    rt.st = {"status": "running"}
    sc = TranscriptScene("s", "/p")
    m = model_of(msgs)
    sc.sync(m, rt, "normal")
    check_whole(sc, m)
    # a live reply streams: the text item grows, the flush reports its block and nothing of the past
    m.push(user_msg("Turn 40: one more"))
    sc.sync(m, rt, "normal")
    m.push(assistant("m-40a", [{"type": "text", "text": "Starting"}]))
    changed = sc.sync(m, rt, "normal")
    assert changed, "the new text"
    assert not any(c in settled_groups(sc) for c in changed)
    check_whole(sc, m)
