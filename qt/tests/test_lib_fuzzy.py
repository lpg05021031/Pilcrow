"""Port of test/unit/fuzzy.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.fuzzy import FuzzyScore, fuzzy_filter, fuzzy_score

# --- fuzzyScore


def test_empty_query_matches_everything_with_no_hits():
    assert fuzzy_score("", "anything") == FuzzyScore(0, [])


def test_null_when_the_characters_are_not_all_there_in_order():
    assert fuzzy_score("xyz", "abc") is None
    assert fuzzy_score("ba", "ab") is None


def test_a_substring_in_the_basename_beats_one_in_the_folder():
    in_base = fuzzy_score("store", "web/src/lib/store.ts")
    in_dir = fuzzy_score("store", "store/src/lib/x.ts")
    assert in_base is not None and in_dir is not None
    assert in_base.score > in_dir.score
    assert in_base.hits == [12, 13, 14, 15, 16]


def test_scattered_matches_score_below_a_contiguous_one():
    contiguous = fuzzy_score("sess", "sessions.ts")
    scattered = fuzzy_score("sess", "src/e/s/sx.ts")
    assert contiguous is not None and scattered is not None
    assert contiguous.score > scattered.score


# --- fuzzyFilter


def test_sorts_by_score_and_respects_the_limit():
    items = ["web/src/lib/store.ts", "server/store.js", "README.md", "scripts/release.mjs"]
    r = fuzzy_filter("store", items, lambda s: s, 2)
    assert [m.item for m in r] == ["server/store.js", "web/src/lib/store.ts"]


def test_leaves_out_items_that_do_not_match():
    assert fuzzy_filter("zzz", ["a", "b"], lambda s: s) == []
