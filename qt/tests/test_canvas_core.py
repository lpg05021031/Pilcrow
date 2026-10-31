"""The pure parts of the canvas engine: the Fenwick tree against a plain list, groups and heights,
anchors, and the height estimator's calibration. No QApplication."""

from __future__ import annotations

import random

from pilcrow_qt.canvas.anchor import Follow
from pilcrow_qt.canvas.estimate import Calibration, Metrics, estimate_markdown
from pilcrow_qt.canvas.fenwick import Fenwick
from pilcrow_qt.canvas.scene import Block, Group, Scene


class Leaf(Block):
    def __init__(self, key: str, h: int) -> None:
        super().__init__(key)
        self.h = h


def naive_find(vals, y):
    if not vals:
        return 0
    # largest i with prefix(i) <= y, clamped
    pos = 0
    acc = 0
    for i in range(len(vals) + 1):
        if acc <= y:
            pos = i
        if i < len(vals):
            acc += vals[i]
    return min(pos, len(vals) - 1) if y > 0 else 0


def test_fenwick_matches_a_list():
    rnd = random.Random(7)
    for _ in range(60):
        vals = [rnd.choice([0, 0, 12, 24, 44, 260, 900]) for _ in range(rnd.randint(0, 40))]
        f = Fenwick(vals)
        assert f.total() == sum(vals)
        for i in range(len(vals) + 1):
            assert f.prefix(i) == sum(vals[:i])
        for y in range(-5, sum(vals) + 30, 7):
            assert f.find(y) == naive_find(vals, y), (vals, y)
        for _ in range(10):
            if not vals:
                break
            i = rnd.randrange(len(vals))
            v = rnd.choice([0, 10, 33, 500])
            f.set(i, v)
            vals[i] = v
            assert f.values() == vals
            assert f.total() == sum(vals)
        for _ in range(5):
            v = rnd.choice([0, 20, 44, 300])
            f.append(v)
            vals.append(v)
        for i in range(len(vals) + 1):
            assert f.prefix(i) == sum(vals[:i])
        k = rnd.randint(0, len(vals))
        f.truncate(k)
        del vals[k:]
        assert f.values() == vals and f.total() == sum(vals)
        if vals:
            i = rnd.randrange(len(vals))
            f.insert(i, 77)
            vals.insert(i, 77)
            f.remove(min(i + 1, len(vals) - 1))
            del vals[min(i + 1, len(vals) - 1)]
            assert f.values() == vals and f.total() == sum(vals)


def test_find_skips_zero_height_blocks():
    f = Fenwick([10, 0, 0, 20])
    assert f.find(0) == 0
    assert f.find(9) == 0
    assert f.find(10) == 3  # the two empty blocks are skipped
    assert f.find(29) == 3
    assert f.find(1000) == 3


def _scene():
    s = Scene()
    s.width = 600
    a = Leaf("a", 100)
    g = Group("g", head_h=30, pad_top=4, pad_bottom=6, open_=True)
    g.shaped = True
    b = Leaf("b", 50)
    c = Leaf("c", 70)
    s.root.append(a)
    s.root.append(g)
    g.append(b)
    g.append(c)
    d = Leaf("d", 20)
    s.root.append(d)
    s.take_dirty()  # building is not a change the view has to paint from
    return s, a, g, b, c, d


def test_group_heights_and_positions():
    s, a, g, b, c, d = _scene()
    assert g.content_h() == 120
    assert g.h == 30 + 4 + 120 + 6
    assert s.total == 100 + g.h + 20
    assert b.y() == 100 + 34 and c.y() == 100 + 34 + 50 and d.y() == 100 + g.h
    assert s.by_key["c"] is c and c.path() == (1, 1)
    # a child growing moves the group and what follows, through the Fenwick trees
    c.set_height(100, 2, 600)
    assert g.content_h() == 150 and d.y() == 100 + 30 + 4 + 150 + 6
    assert s.dirty_from == c.y() and dict(s.take_dirty()[0]) == {c: 0}
    # closing the group hides its content; the pads go too
    g.set_open(False)
    assert g.h == 30 and d.y() == 130
    g.set_reveal(0.5)
    assert g.h == 30 + 4 + 75 + 6
    g.set_open(True)
    assert g.h == 30 + 4 + 150 + 6


def test_leaf_at_and_leaves_between():
    s, a, g, b, c, d = _scene()
    assert s.leaf_at(0)[0] is a
    assert s.leaf_at(99)[0] is a
    assert s.leaf_at(100)[0] is g  # the head of the open group
    assert s.leaf_at(100 + 34)[0] is b
    assert s.leaf_at(100 + 34 + 50)[0] is c
    assert s.leaf_at(100 + g.h)[0] is d
    seen = [blk.key for blk, _y in s.leaves_between(0, s.total)]
    assert seen == ["a", "g", "b", "c", "d"]
    seen = [blk.key for blk, _y in s.leaves_between(140, 160)]
    assert seen == ["g", "b"]
    g.set_open(False)
    assert [blk.key for blk, _y in s.leaves_between(0, s.total)] == ["a", "g", "d"]


def test_anchor_survives_height_changes_above_and_vanishing_blocks():
    s, a, g, b, c, d = _scene()
    scroll = 100 + 34 + 10  # ten px into b
    anchor = s.pick_anchor(scroll)
    assert anchor == ("b", 10)
    a.set_height(300, 2, 600)  # something above grew by 200
    assert s.resolve_anchor(anchor) == scroll + 200
    g.truncate(0)  # b is gone: the anchor cannot be resolved
    assert s.resolve_anchor(anchor) is None
    assert "b" not in s.by_key


def test_truncate_and_append_keep_the_registry():
    s, a, g, b, c, d = _scene()
    g.truncate(1)
    assert "c" not in s.by_key and "b" in s.by_key and g.content_h() == 50
    e = Leaf("e", 15)
    g.append(e)
    assert s.by_key["e"] is e and e.index == 1 and e.y() == 100 + 34 + 50


def test_follow_state():
    f = Follow()
    f.on_user_scroll(900, 1000, 1000)  # scrolled up a lot: stop following
    assert not f.following
    f.on_user_scroll(960, 900, 1000)  # back near the end
    assert f.following
    f.on_user_scroll(955, 960, 1000)  # a wobble within the leave threshold keeps following
    assert f.following
    assert f.target(1200, 955) == 1200
    f.following = False
    assert f.target(1200, 955) == 955
    assert f.shows_latest(100, 1200, 700, grew=True)
    assert not f.shows_latest(100, 1200, 700, grew=False)


def test_estimates_scale_with_text_and_calibrate():
    m = Metrics(avg_w=7.0, em_w=14.5, mono_w=7.5, line=24, mono_line=20, para=11, code_pad=51)
    short = estimate_markdown("One short line.", 640, m)
    longer = estimate_markdown("word " * 400, 640, m)
    assert short == 24 and longer > short * 10
    fence = estimate_markdown("```\n" + "\n".join(["x"] * 10) + "\n```", 640, m)
    assert fence == 10 * 20 + 51
    cjk = estimate_markdown("中" * 100, 640, m)
    assert cjk > estimate_markdown("a" * 100, 640, m)
    cal = Calibration()
    for _ in range(20):
        cal.observe("prose", 100, 120)
    assert abs(cal.factor("prose") - 1.2) < 0.02
    assert cal.apply("prose", 200) == 240


class Margined(Leaf):
    MT = 10
    MB = 14


def test_sibling_gaps_collapse_like_block_margins():
    s = Scene()
    s.width = 600
    a = Margined("a", 100)
    b = Margined("b", 50)
    s.root.append(a)
    s.root.append(b)
    assert a.gap == 0 and b.gap == 14  # max(a.MB, b.MT)
    assert b.y() == 100 + 14 and s.total == 100 + 14 + 50
    # a headless group shows its children's margins to its siblings
    g = Group("g", head_h=0)
    g.shaped = True
    s.root.append(g)
    c = Margined("c", 30)
    g.append(c)
    assert g.gap == 14 and c.gap == 0 and c.y() == 100 + 14 + 50 + 14
    s.take_dirty()
    g.truncate(0)
    assert g.h == 0
