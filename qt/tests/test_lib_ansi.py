"""Port of test/unit/ansi.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.ansi import Seg, parse_ansi, strip_ansi


def test_plain_text_is_one_segment():
    assert parse_ansi("hello") == [Seg("hello", None, None)]


def test_bold_and_a_basic_colour():
    segs = parse_ansi("\x1b[1;31merr\x1b[0m ok")
    assert segs == [
        Seg("err", "a-b", {"color": "var(--ansi-red)"}),
        Seg(" ok", None, None),
    ]


def test_256_colour_and_truecolour():
    assert parse_ansi("\x1b[38;5;196mx")[0].style == {"color": "rgb(255,0,0)"}
    assert parse_ansi("\x1b[48;2;1;2;3mx")[0].style == {"background": "rgb(1,2,3)"}
    assert parse_ansi("\x1b[38;5;240mx")[0].style == {"color": "rgb(88,88,88)"}


def test_drops_cursor_movement_and_osc_sequences():
    segs = parse_ansi("a\x1b[2Kb\x1b]0;title\x07c")
    assert "".join(s.text for s in segs) == "abc"
    assert all(not s.cls and not s.style for s in segs)


def test_a_bare_carriage_return_is_dropped_crlf_keeps_its_newline():
    assert strip_ansi("a\rb\r\nc") == "ab\r\nc"
    assert strip_ansi("x\ry") == "xy"
