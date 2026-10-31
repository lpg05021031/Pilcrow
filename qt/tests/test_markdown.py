"""Markdown → QTextDocument HTML (pilcrow_qt/markdown.py): the pixel sizes of transcript.css, the
markers the view paints from, and the Pygments colouring."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a


def test_metrics_follow_the_css():
    m = markdown.PROSE
    assert (m.base, m.line, m.para, m.code) == (15, 24, 11, 12)  # 14.5px, 1.65, 0.75em, 0.86em
    assert m.heading(2) == (17, 22, 23, 8)  # 1.16em on 1.3, margins 1.35em / 0.45em
    assert m.heading(1)[0] == 19 and m.heading(3)[0] == 15
    c = markdown.COMPACT
    assert (c.base, c.line, c.code) == (13, 21, 11)
    assert markdown.metrics(False) is markdown.PROSE and markdown.metrics(True) is markdown.COMPACT


def test_inline_code_is_a_marked_padded_span(app):
    out = markdown.render_html("use `next` here")
    assert "background-color:rgba(0,0,0,0)" in out  # the pill marker the view paints from
    pad = f'<span style="font-size:{markdown.pill_pad_px(markdown.PROSE)}px">{markdown.PILL_PAD}</span>'
    assert f">{pad}next{pad}</code>" in out.replace(chr(0x2060), "")  # minus the joiners
    assert 6 <= markdown.pill_pad_px(markdown.PROSE) <= 9  # a 0.38em pad in the mono face
    assert "font-size:12px" in out
    assert '<p style="line-height:24px">' in out


def test_fence_has_a_language_head_and_mono_lines(app):
    out = markdown.render_html("```ts\nconst a = 1;\n```")
    assert 'width="100%"' in out and "border-collapse:collapse" in out  # the well the view rounds
    assert ">TypeScript</p>" in out
    assert "line-height:20px" in out  # 12.5px mono on 1.6 lines
    assert "white-space:pre-wrap" in out and "const" in out
    assert "font-style:italic" in markdown.render_html("```py\n# note\nx = 1\n```")


def test_fence_without_a_language_is_text(app):
    out = markdown.render_html("```\nplain\n```")
    assert ">Text</p>" in out and "plain" in out


def test_headings_become_sized_paragraphs(app):
    out = markdown.render_html("## Title\n\ntext")
    assert "<h2>" not in out
    assert "font-size:17px;font-weight:600;line-height:22px;margin-top:23px;margin-bottom:8px" in out
    h4 = markdown.render_html("#### Small")
    assert f"color:{theme.tokens().text_2}" in h4


def test_tables_and_quotes(app):
    out = markdown.render_html("| a | b |\n| --- | --- |\n| 1 | 2 |", width=600)
    assert 'width="360"' in out  # a small table still spans 60% of the room (min-width: 60%)
    assert (
        'style="padding-left:0;padding-right:14px;padding-top:7px;padding-bottom:7px;font-size:13px;text-align:left"'
        in out
    )
    css = markdown.stylesheet(theme.tokens(), markdown.PROSE)
    assert f"blockquote {{ margin-left: {markdown.QUOTE_INDENT}px" in css
    assert "strong, b { font-weight: 620; }" in css
    assert f"a {{ color: {theme.tokens().info}; text-decoration: none; }}" in css


def test_calls_are_coloured_like_the_web(app):
    t = theme.tokens()
    out = markdown.highlight_html("walk(src)", "ts", "dark")
    assert f'<span style="color:{t.cm_function}">walk</span>' in out
    assert f'<span style="color:{t.cm_number}">null</span>' in markdown.highlight_html("x = null", "ts", "dark")


def test_lang_label_and_path_refs():
    assert markdown.lang_label("py") == "Python" and markdown.lang_label("") == "Text"
    assert markdown.as_path_ref("web/src/lib/a.ts:42") == {"path": "web/src/lib/a.ts", "line": 42}
    assert markdown.as_path_ref("not a path") is None


def test_table_columns_are_sized_like_a_browser(app):
    from pilcrow_qt.markdown import column_widths

    # everything fits: each column as wide as it wants, stretched to 60% of the room
    assert column_widths([20, 20], [100, 100], 1000) == [300, 300]
    # the widest lines do not fit: each column at least its widest word, the rest shared
    assert sum(column_widths([100, 100], [600, 200], 500)) == 500
    assert column_widths([100, 100], [600, 200], 500)[0] > column_widths([100, 100], [600, 200], 500)[1]
    # a heading never wraps: its column is at least as wide as its name in semibold
    wide = (
        "| Approach | Allocations | Speed | Risk |\n|---|---|---|---|\n"
        + "| keep | 1 per char | 1× | "
        + "words " * 60
        + "|"
    )
    out = markdown.render_html(wide, width=600)
    widths = [int(w) for w in re.findall(r'<th width="(\d+)"', out)]
    assert len(widths) == 4 and sum(widths) <= 600
    head = markdown.font_metrics(markdown.PROSE.table, weight=600).horizontalAdvance("Allocations")
    assert widths[1] >= head + markdown.CELL_PAD
    assert f'<table width="{sum(widths)}"' in out


def test_fence_lines_hang_their_continuations(app):
    out = markdown.render_html("```py\nx = 1\n    y = 2\n\nz\n```")
    ps = re.findall(r"<p style=\"[^\"]*\">", out)[1:]  # after the language head
    assert len(ps) == 4
    assert "margin-left:0px;text-indent:-0px" in ps[2]  # an empty line hangs nothing
    ch = markdown.font_metrics(markdown.px(markdown.mono_size()), mono=True).horizontalAdvance("0")
    assert f"margin-left:{int(2 * ch + 0.5)}px" in ps[0]  # a flush line continues two characters in
    assert f"margin-left:{int(6 * ch + 0.5)}px" in ps[1]  # an indented one past its own indentation
    assert "white-space:pre-wrap" in ps[1]
    assert markdown.fence_sources("a\n\n```py\nx = 1\n```\n\n```\nplain\n```") == ["x = 1", "plain"]


def test_task_lists_draw_boxes_in_the_markers_place(app):
    out = markdown.render_html("- [x] done\n- [ ] todo")
    assert "list-style-type:none" in out
    assert f'<img src="{markdown.CHECK_ON}"' in out and f'<img src="{markdown.CHECK_OFF}"' in out
    assert "[x]" not in out and "[ ]" not in out
    assert f"text-indent:-{markdown.PROSE.list_indent}px" in out
    mixed = markdown.render_html("- [x] done\n- plain")
    assert "list-style-type:none" not in mixed and "[x]" not in mixed  # the bullets stay; the box follows one
    assert f'<img src="{markdown.CHECK_ON}"' in mixed


def test_bullets_change_shape_as_they_nest_and_markers_are_quiet(app):
    t = theme.tokens()
    out = markdown.render_html("- one\n  - two\n    - three\n\n1. seven")
    assert "list-style-type:disc" in out and "list-style-type:circle" in out and "list-style-type:square" in out
    assert f'<li style="color:{t.text_3}">' in out  # the marker's colour (li::marker)
    assert f'<span style="color:{t.text}">one</span>' in out  # the words keep theirs
    assert f'<span style="color:{t.text}">seven</span>' in out


def test_cjk_stays_upright_in_italic_comments(app):
    t = theme.tokens()
    (line,) = markdown.highlight_lines("# 註解 note", "python", t.name)
    assert f'<span style="color:{t.cm_comment}">註解</span>' in line  # no font-style on the CJK run
    assert f'<span style="color:{t.cm_comment};font-style:italic"> note</span>' in line
    lines = markdown.highlight_lines('s = """a\nb"""\nx = 1', "python", t.name)
    assert len(lines) == 3 and "b" in lines[1] and "x" in lines[2]  # a token across lines is split per line
