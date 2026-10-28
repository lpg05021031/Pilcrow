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
