"""The document builder (canvas/build.py) against the HTML renderer it replaced: over a corpus of
every construct, the same heights, the same words, the same block formats and lists, the same
character formats run for run, the same tables; and the few places where the builder is right and
the renderer was not (a path in a code span is a link, an indented code block is a well, code in a
list item keeps the text colour)."""

from __future__ import annotations

import base64
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QImage, QTextFormat, QTextTable  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402

W = 600


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a


def measurer():
    from pilcrow_qt.canvas.view import measurer

    return measurer()


def _png(w: int, h: int) -> str:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


CORPUS = {
    "para+inline": "Plain *em* **strong** ~~del~~ `code` [link](https://e.com) and more.  \nhard break\nsoft 中文*斜體*",
    "headings": "# H1 words\n\n## H2\n\n### H3\n\n#### H4\n\n##### H5\n\n###### H6",
    "quote": "> quoted *words*\n>\n> > nested",
    "quote then para": "> q\n\nafter",
    "lists": "- a\n  - b\n    - c\n- [x] done\n- [ ] todo\n\n1. one\n2. two\n\n5. five\n6. six",
    "task pure": "- [x] done\n- [ ] todo",
    "task nested": "- a\n  - [ ] sub",
    "list then para": "- a\n- b\n\nafter",
    "loose list": "- a\n\n- b",
    "two paras item": "- a\n\n  second para\n- b",
    "ordered start": "3. three\n4. four",
    "mixed nest": "1. one\n   - bullet\n   - two\n2. two",
    "heading in quote": "> # H1 in quote\n> words",
    "para in quote in list": "- item\n\n  > quoted\n\n- next",
    "fence": "```py\nx = 1  # c\n    y = 2\n\n\tz = 3\n```",
    "fence no lang": "```\nplain\n```",
    "fence in quote": "> ```py\n> x = 1\n> ```",
    "para then fence": "words\n\n```\nx\n```\n\nmore words",
    "two fences": "```\na\n```\n\n```js\nb\n```",
    "table": "| a | b |\n|---|---:|\n| x | **y** |",
    "center table": "| a |\n|:-:|\n| 1 |",
    "table in list": "- item\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |",
    "table then para": "| a | b |\n|---|---|\n| 1 | 2 |\n\nafter",
    "squeezed table": "| a | b |\n|---|---|\n| x | `" + "/".join(["segment_of_a_long_path"] * 12) + "` |",
    "hr": "above\n\n---\n\nbelow",
    "img": "below ![alt](img.png) and ![b](" + _png(900, 60) + ")",
    "link in heading": "## see [here](https://x.y) now",
    "empty": "",
    "autolink": "see https://auto.link now",
    "escapes": 'a \\* b &amp; c < d > e "q"',
    "cjk": "中文段落，包含`程式碼`與**粗體**。\n\n- 清單項目一\n- 項目二",
    "long para": " ".join(f"word{i}" for i in range(400)),
    "nested quotes list": "> - a\n> - b\n>\n> text",
    "em with code": "*emph `code` more*",
    "link with code": "[`code` link](https://x.y)",
    "image in link": "[![alt](p.png)](https://x.y)",
    "code in heading": "## Heading `code` here",
    "html text": "<div>not html</div> &nbsp;",
    "big table streaming": "| a | b |\n|---|---|\n" + "\n".join(f"| r{i} | {i} |" for i in range(60)),
}


def _cf(cf) -> Tuple:
    f = cf.font()
    fg = cf.foreground().color().name() if cf.hasProperty(QTextFormat.Property.ForegroundBrush) else None
    pill = cf.hasProperty(QTextFormat.Property.BackgroundBrush) and cf.background().color().alpha() == 0
    img = None
    if cf.isImageFormat():
        im = cf.toImageFormat()
        img = (im.name()[:24], im.width(), im.height())
    ls = round(cf.fontLetterSpacing(), 1) if cf.fontLetterSpacing() else None
    return (f.family(), f.pixelSize(), f.weight(), f.italic(), fg, pill, cf.anchorHref(), cf.fontStrikeOut(), ls, img)


def _bf(bf) -> Tuple:
    return (
        round(bf.topMargin()),
        round(bf.bottomMargin()),
        round(bf.leftMargin()),
        round(bf.textIndent()),
        bf.indent(),
        round(bf.lineHeight()),
        bf.lineHeightType(),
        int(bf.alignment()) & 0x1F,
        bf.hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth),
    )


def _blocks(doc) -> List[Tuple]:
    out = []
    b = doc.firstBlock()
    while b.isValid():
        lst = b.textList()
        lk = (lst.format().style().name, lst.format().indent(), lst.format().start()) if lst else None
        frags = []
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            it += 1
            if fr.text().strip(" \xa0"):  # the renderer leaves a space after an item with a nested list
                frags.append((fr.text(), _cf(fr.charFormat())))
        out.append((_bf(b.blockFormat()), lk, frags, b.text()))
        b = b.next()
    return out


def _tables(doc) -> List[Tuple]:
    out = []
    for fr in doc.rootFrame().childFrames():
        if isinstance(fr, QTextTable):
            tf = fr.format()
            cells = []
            for r in range(fr.rows()):
                for c in range(fr.columns()):
                    cfm = fr.cellAt(r, c).format().toTableCellFormat()
                    cells.append((cfm.leftPadding(), cfm.rightPadding(), cfm.topPadding(), cfm.bottomPadding()))
            out.append(
                (
                    fr.rows(),
                    fr.columns(),
                    tf.width().type().name,
                    tf.borderCollapse(),
                    tf.topMargin(),
                    tf.bottomMargin(),
                    tf.leftMargin(),
                    round(sum(c.rawValue() for c in tf.columnWidthConstraints())),
                    cells,
                )
            )
    return out


def _norm(text: str) -> str:
    return "\n".join(line.replace("\xa0", "").rstrip() for line in text.split("\n"))


@pytest.mark.parametrize("label", list(CORPUS))
def test_the_builder_matches_the_html_renderer(app, label):
    m = measurer()
    src = CORPUS[label]
    kw: dict = {"cwd": "/proj", "streaming": "streaming" in label}
    a = m.markdown_doc_html(src, W, **kw)
    b = m.markdown_doc(src, W, **kw)
    assert a.size().height() == b.size().height()
    assert _norm(a.toPlainText()) == _norm(b.toPlainText())
    ba, bb = _blocks(a), _blocks(b)
    assert len(ba) == len(bb)
    for (bfa, la, fa, ta), (bfb, lb, fb, tb) in zip(ba, bb):
        if not ta.strip() and not tb.strip():
            continue  # the empty blocks around a frame: the renderer gives them the quote's margins, no one sees them
        assert bfa == bfb, (ta, bfa, bfb)
        assert la == lb, (ta, la, lb)
        assert fa == fb, (ta, fa, fb)
    assert _tables(a) == _tables(b)


def test_the_builder_improves_on_the_renderer_where_it_was_wrong(app):
    m = measurer()
    # a path in a code span is a link (the renderer lost it when the pill got its pads)
    doc = m.markdown_doc("see `path/to/file.py:12` here", W)
    hrefs = set()
    b = doc.firstBlock()
    it = b.begin()
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        if fr.charFormat().isAnchor():
            hrefs.add(fr.charFormat().anchorHref())
    assert hrefs == {"pilcrow-path://open?p=path/to/file.py&l=12"}
    # an indented code block is a well like a fence (the renderer treated it as inline code)
    doc = m.markdown_doc("    indented code\n    more", W)
    wells = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable) and f.format().borderCollapse()]
    assert len(wells) == 1
    assert doc.property("pilcrow_wells") == ["indented code\nmore"]
    # code in a list item is in the text colour (the renderer drew it in the marker's quiet colour)
    doc = m.markdown_doc("- item\n\n  ```py\n  x = 1\n  ```", W)
    well = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)][0]
    code = well.cellAt(1, 0).firstCursorPosition().block()
    first = code.begin().fragment().charFormat()
    assert not first.hasProperty(QTextFormat.Property.ForegroundBrush)


def test_the_built_document_carries_the_code_of_its_wells(app):
    m = measurer()
    doc = m.markdown_doc("```py\nx = 1\n```\n\nwords\n\n```\nb\n```", W)
    assert doc.property("pilcrow_wells") == ["x = 1", "b"]


def test_the_builder_is_not_slower_than_the_renderer(app):
    m = measurer()
    src = CORPUS["fence"] + "\n\n" + CORPUS["lists"] + "\n\n" + CORPUS["table"] + "\n\n" + CORPUS["para+inline"]
    for _ in range(3):  # warm
        m.markdown_doc_html(src, W)
        m.markdown_doc(src, W)

    def timed(fn: Any) -> float:
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0

    # the two interleaved, the collector held, the best of each: drift and a collection fall on both alike
    html = built = 1e9
    gc.disable()
    try:
        for _ in range(9):
            html = min(html, timed(lambda: m.markdown_doc_html(src, W).size()))  # laid out, as the builder's comes
            built = min(built, timed(lambda: m.markdown_doc(src, W)))
    finally:
        gc.enable()
    assert built < html * 1.1, (built, html)  # not slower; the gain is the regex passes and the importer
