"""Port of test/unit/ansi.test.ts."""

from __future__ import annotations

from pilcrow_qt.lib.ansi import Seg, overwrite, parse_ansi, strip_ansi


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


def test_drops_cursor_visibility_and_osc_sequences():
    segs = parse_ansi("a\x1b[?25lb\x1b]0;title\x07c\x1b[Hd")
    assert "".join(s.text for s in segs) == "abcd"
    assert all(not s.cls and not s.style for s in segs)


# ---- what a terminal would show (overwrite)


def test_a_carriage_return_writes_over_the_line_so_a_progress_bar_keeps_its_last_state():
    out = "".join(f"\rprogress {i}%" for i in range(0, 101, 10)) + "\ndone\n"
    assert strip_ansi(out) == "progress 100%\ndone\n"
    assert strip_ansi("x\ry") == "y"
    assert strip_ansi("tail\r") == "tail"  # a return with nothing after it (a stream mid-line)


def test_crlf_is_a_line_end():
    assert strip_ansi("a\rb\r\nc") == "b\nc"
    assert overwrite("plain\r\ntext") == "plain\ntext"


def test_text_shorter_than_what_it_writes_over_leaves_the_rest_unless_the_line_is_erased():
    assert strip_ansi("progress 100%\rdone") == "doneress 100%"  # what a terminal shows too
    assert strip_ansi("progress 100%\rdone\x1b[K") == "done"  # erase to the end of the line
    assert strip_ansi("abc\x1b[2K\rxy") == "xy"  # erase the whole line
    assert strip_ansi("abc\x1b[1Kd") == "   d"  # erase to the cursor keeps the columns


def test_cursor_up_rewrites_an_earlier_line():
    assert strip_ansi("line1\nline2\n\x1b[1A\x1b[2Kreplaced\n") == "line1\nreplaced\n"
    assert strip_ansi("one\ntwo\nthree\x1b[2A\rONE\n") == "ONE\ntwo\nthree"
    assert strip_ansi("\x1b[32mgreen\x1b[0m\n\x1b[1A\x1b[2Kover\n") == "over\n"


def test_column_moves():
    assert strip_ansi("col\x1b[10Gten") == "col      ten"
    assert strip_ansi("abc\x1b[2D-") == "a-c"
    assert strip_ansi("ab\x1b[3Cf") == "ab   f"


def test_colours_survive_an_overwrite_character_by_character():
    segs = parse_ansi("\x1b[31mred\x1b[0m plain\rX")
    assert [(s.text, s.style) for s in segs] == [
        ("X", None),
        ("ed", {"color": "var(--ansi-red)"}),
        (" plain", None),
    ]
    # a 256-colour parameter that happens to be 0 is not a reset
    segs = parse_ansi("\x1b[38;5;0mblack\x1b[0m\rB")
    assert [(s.text, s.style) for s in segs][1] == ("lack", {"color": "var(--ansi-black)"})


def test_text_without_overwriting_controls_comes_back_as_it_came():
    text = "\x1b[1mbold\x1b[0m and \x1b[31mred\x1b[0m\nnext line"
    assert overwrite(text) is text
    assert strip_ansi("a\x1b[2Kb") == " b"  # erase in line leaves the cursor where it was
