"""Markdown → QTextDocument HTML (pilcrow_qt/markdown.py): the pixel sizes of transcript.css, the
markers the view paints from, and the Pygments colouring."""

from __future__ import annotations

import os
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
    assert f">{markdown.PILL_PAD}next{markdown.PILL_PAD}</code>" in out.replace(chr(0x2060), "")  # minus the joiners
    assert "font-size:12px" in out
    assert '<p style="line-height:24px">' in out


def test_fence_has_a_language_head_and_mono_lines(app):
    out = markdown.render_html("```ts\nconst a = 1;\n```")
    assert 'width="100%"' in out and "border-collapse:collapse" in out  # the well the view rounds
    assert ">TypeScript</p>" in out
    assert "line-height:20px" in out  # 12.5px mono on 1.6 lines
    assert "<pre" in out and "const" in out
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
    out = markdown.render_html("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert 'width="60%"' in out
    assert (
        '<th style="padding-left:0;padding-right:14px;padding-top:7px;padding-bottom:7px;font-size:13px;text-align:left;'
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
