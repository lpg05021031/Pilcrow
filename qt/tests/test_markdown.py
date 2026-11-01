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


# ---- the rest of the audit (AUDIT.md F6–F8, F11, F12)


def _png(w: int, h: int) -> str:
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


def _images(doc):
    out = []
    b = doc.firstBlock()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            it += 1
            if fr.charFormat().isImageFormat():
                out.append(fr.charFormat().toImageFormat())
        b = b.next()
    return out


def _widest_line(doc) -> float:
    lay = doc.documentLayout()
    worst = 0.0
    b = doc.firstBlock()
    while b.isValid():
        tl = b.layout()
        br = lay.blockBoundingRect(b)
        for i in range(tl.lineCount()):
            ln = tl.lineAt(i)
            worst = max(worst, br.left() + ln.x() + ln.naturalTextWidth())
        b = b.next()
    return worst


def test_an_image_wider_than_its_room_is_scaled_down_to_it(app):
    from pilcrow_qt.canvas.view import measurer

    m = measurer()
    big = _png(2000, 60)
    doc = m.markdown_doc(f"![big]({big})", 600)
    (img,) = _images(doc)
    assert img.width() == 600 and img.height() == pytest.approx(18)
    assert _widest_line(doc) <= 600
    # inside a quote the room is less by the quote's indent; in a list item by the list's
    (img,) = _images(m.markdown_doc(f"> ![big]({big})", 600))
    assert img.width() == 600 - markdown.QUOTE_INDENT
    doc = m.markdown_doc(f"- ![big]({big})", 600)
    (img,) = _images(doc)
    assert 400 < img.width() < 600 and _widest_line(doc) <= 600
    # after words on the same line it still fits the column
    assert _widest_line(m.markdown_doc(f"see ![big]({_png(900, 90)}) there", 600)) <= 600
    # an image that fits is left alone (no width set: its own size), and a task list's box too
    assert not _images(m.markdown_doc(f"![s]({_png(100, 30)})", 600))[0].hasProperty(0x5010)  # ImageWidth
    box, img = _images(m.markdown_doc(f"- [x] done ![big]({_png(900, 90)})", 600))
    assert box.height() == markdown.CHECK_SIZE and img.width() < 600


def test_a_fence_inside_a_quote_or_a_list_ends_at_the_column(app):
    from PySide6.QtGui import QTextTable

    from pilcrow_qt.canvas.paint import table_rect
    from pilcrow_qt.canvas.view import measurer

    m = measurer()
    cases = {
        "quote": "> q\n>\n> ```py\n> x = 1\n> ```",
        "quote in quote": "> > q\n> >\n> > ```py\n> > x = 1\n> > ```",
        "list item": "- item\n\n  ```py\n  x = 1\n  ```\n\n- next",
        "nested list": "- a\n  - b\n\n    ```py\n    x = 1\n    ```",
        "quote in list": "- a\n\n  > q\n  >\n  > ```py\n  > x = 1\n  > ```",
    }
    for label, src in cases.items():
        doc = m.markdown_doc(src, 600)
        wells = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
        assert len(wells) == 1, label
        r = table_rect(doc, doc.documentLayout(), wells[0])
        assert r.left() > 0 and r.right() == pytest.approx(600, abs=0.5), (label, r)
    # a fence at the top level is the whole column, as before
    doc = m.markdown_doc("```py\nx = 1\n```", 600)
    (well,) = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
    r = table_rect(doc, doc.documentLayout(), well)
    assert r.left() == 0 and r.right() == 600


def test_tab_stops_are_four_code_columns_on_every_document(app):
    from pilcrow_qt.canvas.view import measurer

    m = measurer()
    ch = markdown.font_metrics(markdown.px(markdown.mono_size()), mono=True).horizontalAdvance("0")
    doc = m.markdown_doc("```go\n\tfor i := 0; i < 3; i++ {\n\t\tfmt.Println(i)\n```", 600)
    assert doc.defaultTextOption().tabStopDistance() == pytest.approx(markdown.TAB_COLS * ch)
    blk = doc.firstBlock()
    while blk.isValid() and not blk.text().startswith("\t\t"):
        blk = blk.next()
    ln = blk.layout().lineAt(0)
    # the text after two tabs sits eight columns in, and a wrapped continuation would sit two past it
    assert ln.cursorToX(2)[0] - ln.cursorToX(0)[0] == pytest.approx(2 * markdown.TAB_COLS * ch, abs=0.5)
    bf = blk.blockFormat()  # the hanging indent (the well's own padding is the cell's)
    assert bf.leftMargin() == pytest.approx((2 * markdown.TAB_COLS + markdown.WRAP_INDENT) * ch, abs=1)
    assert -bf.textIndent() == pytest.approx(bf.leftMargin(), abs=0.01)
    # a plain mono document (command output) measures its tabs in its own face
    f = m.font(12, mono=True)
    plain = m.plain_doc("a\tb", 300, f)
    assert plain.defaultTextOption().tabStopDistance() == pytest.approx(
        markdown.TAB_COLS * m.fm(f).horizontalAdvance("0")
    )
    # a grower's document too (the well of a fence being written)
    assert m.blank_doc(600).defaultTextOption().tabStopDistance() == pytest.approx(markdown.TAB_COLS * ch)


def test_a_table_too_wide_for_the_room_lets_its_code_break_and_ends_at_the_column(app):
    from PySide6.QtGui import QTextTable

    from pilcrow_qt.canvas.paint import table_rect
    from pilcrow_qt.canvas.view import measurer

    m = measurer()
    code = "`" + "/".join(["segment_of_a_long_path"] * 12) + "`"
    doc = m.markdown_doc("| a | b |\n|---|---|\n| x | " + code + " |", 600)
    lay = doc.documentLayout()
    (table,) = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
    assert table_rect(doc, lay, table).right() == pytest.approx(600, abs=0.5)
    cell = table.cellAt(1, 1).firstCursorPosition().block()
    assert markdown.NO_BREAK not in cell.text()  # the joiners are gone: the span may break
    assert cell.layout().lineCount() > 1
    widths = [c.rawValue() for c in table.format().columnWidthConstraints()]
    # the narrow column keeps what Qt would not go under: its widest character and the padding
    assert widths[0] >= markdown.font_metrics(m.prose.table).horizontalAdvance("x") + markdown.CELL_PAD
    assert sum(widths) == 600
    # a table that fits keeps its joiners (a code span stays on one line)
    doc = m.markdown_doc("| a | b |\n|---|---|\n| x | `one_two` |", 600)
    (table,) = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
    assert markdown.NO_BREAK in table.cellAt(1, 1).firstCursorPosition().block().text()


def test_column_widths_squeeze_down_to_the_floor_and_no_further():
    from pilcrow_qt.markdown import column_widths, squeezed

    w = column_widths([100, 1000], [100, 1000], 600, [30, 20])
    assert sum(w) == 600 and w[0] >= 30 and w[0] < 100  # the room above the floors is shared out
    assert column_widths([22, 1000], [22, 1000], 600, [22, 20]) == [22, 578]  # nothing to spare: the floor
    assert column_widths([50, 50], [50, 50], 60, [40, 40]) == [40, 40]  # as wide as it must be
    assert squeezed([30, 570], [100, 1000]) == (True, True)
    assert squeezed([100, 500], [100, 300]) == (False, False)


def test_the_highlight_cache_is_bounded_by_source_size_not_count(app, monkeypatch):
    t = theme.tokens()
    monkeypatch.setattr(markdown, "HL_CACHE_CHARS", 200_000)
    markdown._hl_cache.clear()
    markdown._hl_chars = 0
    big = "\n".join(f"line {i} of a long plain fence" for i in range(1000))  # ~30 KB
    for k in range(30):
        markdown.highlight_lines(big + f"\n{k}", "", t.name)
    entries, chars = markdown.highlight_cache_size()
    assert chars <= 200_000 and entries < 30 and entries >= 5
    # a hit is the same object; the newest stays, the oldest went
    assert markdown.styled_lines(big + "\n29", "", t.name) is markdown.styled_lines(big + "\n29", "", t.name)
    assert (big + "\n0", "", t.name) not in markdown._hl_cache
    # a source over a quarter of the budget is highlighted but not kept
    n = markdown.highlight_cache_size()[0]
    huge = "z\n" * 60_000
    assert len(markdown.highlight_lines(huge, "", t.name)) == 60_001
    assert markdown.highlight_cache_size()[0] == n
