"""Port of test/unit/partialJson.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.partial_json import parse_partial_json


def test_returns_empty_object_for_empty_input():
    assert parse_partial_json("") == {}


def test_parses_complete_json_as_is():
    assert parse_partial_json('{"a":1,"b":[1,2]}') == {"a": 1, "b": [1, 2]}


def test_closes_an_open_string():
    assert parse_partial_json('{"command": "ls -l') == {"command": "ls -l"}


def test_closes_nested_arrays_and_objects():
    assert parse_partial_json('{"a": {"b": [1, 2') == {"a": {"b": [1, 2]}}


def test_drops_a_dangling_key_with_or_without_its_colon():
    assert parse_partial_json('{"file_path": "/x.ts", "old') == {"file_path": "/x.ts"}
    assert parse_partial_json('{"file_path": "/x.ts", "old_string":') == {"file_path": "/x.ts"}


def test_a_lone_value_less_key_becomes_null():
    assert parse_partial_json('{"a":') == {"a": None}


def test_drops_a_trailing_comma():
    assert parse_partial_json('{"a": 1,') == {"a": 1}


def test_handles_an_escape_cut_in_half():
    assert parse_partial_json('{"s": "a\\') == {"s": "a"}
    assert parse_partial_json('{"s": "a\\"b') == {"s": 'a"b'}


def test_returns_null_for_garbage():
    assert parse_partial_json("{{{") is None


# ---- the scanner: a text fed a piece at a time costs the piece, and parses to the same value


def test_the_scanner_fed_in_pieces_parses_like_the_whole():
    from pilcrow_qt.lib.partial_json import PartialScanner

    src = (
        '{"file_path": "/x.ts", "old_string": "a \\"quoted\\" line\\\\n", "new_string": "b", "list": [1, {"k": null}]}'
    )
    for step in (1, 3, 7):
        sc = PartialScanner()
        fed = ""
        for i in range(0, len(src), step):
            piece = src[i : i + step]
            sc.feed(piece)
            fed += piece
            assert sc.parse(fed) == parse_partial_json(fed), (step, fed)
        assert sc.complete and sc.parse(src) == parse_partial_json(src)


def test_the_scanner_repairs_from_the_tail_of_a_long_text():
    from pilcrow_qt.lib.partial_json import TAIL, PartialScanner

    body = "x" * (TAIL * 3)
    src = '{"file_path": "/x.ts", "content": "' + body + '", "old'
    sc = PartialScanner()
    sc.feed(src)
    assert sc.parse(src) == {"file_path": "/x.ts", "content": body}
    src2 = '{"content": "' + body + "\\"
    sc2 = PartialScanner()
    sc2.feed(src2)
    assert sc2.parse(src2) == {"content": body}


def test_feeding_a_piece_costs_the_piece_not_the_text():
    import time

    from pilcrow_qt.lib.partial_json import PartialScanner

    sc = PartialScanner()
    sc.feed('{"content": "' + "y" * 200_000)
    t0 = time.perf_counter()
    for _ in range(200):
        sc.feed("more text ")
    assert (time.perf_counter() - t0) < 0.05  # 200 pieces of 10 characters: microseconds each
