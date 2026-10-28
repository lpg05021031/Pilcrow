"""Port of test/unit/units.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.transcript_model import Item, NoticeItem, ResultItem, TextItem, ThinkingItem, ToolItem, UserItem
from pilcrow_qt.lib.units import group_turns, live_turn_folds

_n = 0


def _next() -> int:
    global _n
    _n += 1
    return _n


def user(text: str = "hi") -> Item:
    n = _next()
    return UserItem(id=f"u{n}", uuid=f"uu{n}", text=text)


def text(t: str = "answer") -> Item:
    return TextItem(id=f"t{_next()}", text=t, streaming=False)


def tool(name: str = "Read", status: str = "done") -> ToolItem:
    return ToolItem(id=f"tool{_next()}", name=name, input={}, input_json="", input_done=True, status=status, start_ts=0)


def thinking() -> Item:
    return ThinkingItem(id=f"th{_next()}", text="hmm", streaming=False, start_ts=0)


def notice(tag: str = None) -> Item:
    return NoticeItem(id=f"n{_next()}", level="info", text="note", tag=tag)


def result() -> Item:
    return ResultItem(id=f"r{_next()}", ok=True, subtype="success", duration_ms=1, cost_usd=0, turns=1)


# --- groupTurns


def test_a_plain_exchange_is_one_items_unit():
    units = group_turns([user(), text(), result()], False)
    assert [u.kind for u in units] == ["items"]


def test_work_between_what_claude_says_folds_on_its_own_the_words_stay_out():
    items = [user(), text("first I will look"), tool(), tool(), text("done"), result()]
    units = group_turns(items, False)
    assert [u.kind for u in units] == ["items", "work", "items"]
    assert units[1].kind == "work" and len(units[1].work) == 2
    assert units[1].kind == "work" and units[1].live is False


def test_the_stretch_under_way_is_live_while_the_turn_runs():
    units = group_turns([user(), tool("Read", "running")], True)
    assert units[1].kind == "work" and units[1].live is True
    done = group_turns([user(), tool("Read", "done")], False)
    assert done[1].kind == "work" and done[1].live is False


def test_thinking_alone_counts_as_work_a_notice_inside_a_run_stays_in_it():
    units = group_turns([user(), thinking(), notice(), tool(), text()], False)
    assert [u.kind for u in units] == ["items", "work", "items"]
    assert units[1].kind == "work" and [i.kind for i in units[1].work] == ["thinking", "notice", "tool"]


def test_a_divider_notice_is_a_boundary():
    units = group_turns([user(), tool(), notice("divider"), tool()], False)
    assert [u.kind for u in units] == ["items", "work", "items", "work"]


def test_a_run_of_only_notices_is_not_folded():
    units = group_turns([user(), notice(), notice()], False)
    assert [u.kind for u in units] == ["items"]


def test_unit_keys_are_stable_the_first_item_names_the_unit():
    items = [user(), tool()]
    a = group_turns(items, True)
    b = group_turns([*items, tool()], True)
    assert a[1].key == b[1].key
    assert a[0].key == "P-" + items[0].id


# --- liveTurnFolds


def test_true_when_the_tail_is_work_through_notices_false_when_it_is_words():
    assert live_turn_folds([user(), tool("Read", "running")]) is True
    assert live_turn_folds([user(), tool(), notice()]) is True
    assert live_turn_folds([user(), tool(), text()]) is False
    assert live_turn_folds([user()]) is False
    assert live_turn_folds([]) is False
