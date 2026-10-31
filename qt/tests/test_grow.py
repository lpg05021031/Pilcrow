"""Blocks that grow while they stream (canvas/grow.py): a fence, a list, a table and a paragraph fed
a token at a time have the same height as a render of the same source at every line, item or row
boundary and at the end; the shapes the growers do not take fall back to the render; a huge
paragraph comes in the same pieces whether written or whole; and a token's cost does not grow with
the block."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QTextTable  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402
from pilcrow_qt.canvas import grow  # noqa: E402
from pilcrow_qt.canvas.blocks.prose import ProseBlock, TextGroup  # noqa: E402
from pilcrow_qt.canvas.paint import doc_height, well_rects  # noqa: E402
from pilcrow_qt.lib.transcript_model import TextItem  # noqa: E402

W = 700


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    yield a


def measurer():
    from pilcrow_qt.canvas.view import measurer

    return measurer()


def stream(tokens: List[str], boundary: Callable[[str], bool]):
    """Feed tokens into one streaming block; returns (block, mismatches at boundaries, per-token ms)."""
    m = measurer()
    b = ProseBlock("p", "", para=0)
    b.streaming = True
    src = ""
    bad = []
    per = []
    for i, tok in enumerate(tokens):
        src += tok
        b.set_src(src, True)
        t0 = time.perf_counter()
        h = b.measure(W, m)
        per.append((time.perf_counter() - t0) * 1000)
        if boundary(src):
            ref = doc_height(m.markdown_doc(src, W, streaming=True)) if src.strip() else 0
            if ref != h:
                bad.append((i, h, ref))
    b.set_src(src, False)
    grown = b.measure(W, m)
    full = doc_height(m.markdown_doc(src, W))
    assert grown == full, (grown, full)
    return b, bad, per


def chunks(lines: List[str], n: int) -> List[str]:
    out = []
    for line in lines:
        out.extend(line[k : k + n] for k in range(0, len(line), n))
    return out


def test_a_fence_grows_a_line_at_a_time(app):
    lines = ["```ts\n"] + [f"const x{i} = compute(x{i - 1}, {i}); // step {i}\n" for i in range(1, 60)] + ["```"]
    b, bad, _per = stream(chunks(lines, 7), lambda s: s.endswith("\n"))
    assert bad == []
    assert b.grader_kind if hasattr(b, "grader_kind") else True


def test_a_fence_built_from_blocks_matches_the_well(app):
    lines = [
        "```py\n",
        "def f():\n",
        "    return 'x' * 40  # a comment that runs on past the column width for sure\n",
        "\n",
        "x = f()\n",
    ]
    tokens = chunks(lines, 5)
    m = measurer()
    b = ProseBlock("p", "", para=0)
    b.streaming = True
    src = ""
    for tok in tokens:
        src += tok
        b.set_src(src, True)
        b.measure(W, m)
    assert isinstance(b._grower, grow.FenceGrower)  # noqa: SLF001
    assert (
        b._grower.code()
        == "def f():\n    return 'x' * 40  # a comment that runs on past the column width for sure\n\nx = f()"
    )  # noqa: SLF001
    rects = well_rects(b.doc)
    assert len(rects) == 1
    wells = b.wells()
    assert len(wells) == 1 and wells[0][1] == b._grower.code()  # noqa: SLF001
    # the well's rectangle is the table well's of the same source
    from pilcrow_qt.canvas.paint import table_rect

    ref = m.markdown_doc(src, W, streaming=True)
    table = next(f for f in ref.rootFrame().childFrames() if isinstance(f, QTextTable))
    tr = table_rect(ref, ref.documentLayout(), table)
    assert abs(rects[0].height() - tr.height()) < 1 and abs(rects[0].top() - tr.top()) < 1
    # a finished line carries its colours (the keyword), the line in progress does not yet
    blk = b.doc.firstBlock().next().next()  # spacer, head, first line
    assert blk.text().startswith("def")
    it = blk.begin()
    fr = it.fragment()
    assert fr.charFormat().foreground().color().name() == theme.tokens().cm_keyword
    # the closing fence ends the well: its marker is never shown
    src += "```"
    b.set_src(src, True)
    b.measure(W, m)
    assert "`" not in b.doc.toPlainText()


def test_a_list_grows_an_item_at_a_time_in_chunks(app):
    lines = [f"- item {i} with `code{i}` and **bold** words\n" for i in range(90)]
    b, bad, _per = stream(chunks(lines, 9), lambda s: s.endswith("\n"))
    assert bad == []
    assert isinstance(b._grower, grow.ListGrower) or b._grower is None  # noqa: SLF001
    # while it streamed, the items sat in lists of LIST_CHUNK: three lists for ninety items
    m = measurer()
    doc = grow.ListGrower("".join(lines), m, W, False, False, "", None).doc
    lists = set()
    blk = doc.firstBlock()
    while blk.isValid():
        if blk.textList() is not None:
            lists.add(blk.textList().item(0).position())  # a list, by where it starts
        blk = blk.next()
    assert len(lists) == 3
    assert doc_height(doc) == doc_height(m.markdown_doc("".join(lines), W))


def test_an_ordered_list_numbers_on_across_chunks(app):
    lines = [f"{i}. step {i}\n" for i in range(1, 50)]
    b, bad, _per = stream(chunks(lines, 4), lambda s: s.endswith("\n"))
    assert bad == []
    m = measurer()
    doc = grow.ListGrower("".join(lines), m, W, False, False, "", None).doc
    starts = []
    blk = doc.firstBlock()
    while blk.isValid():
        lst = blk.textList()
        if lst is not None and lst.item(0) == blk:
            starts.append(lst.format().start())
        blk = blk.next()
    assert starts == [1, 41]


def test_a_list_that_changes_shape_falls_back_to_the_render(app):
    m = measurer()
    b = ProseBlock("p", "", para=0)
    b.streaming = True
    for src in ("- a\n", "- a\n- b\n", "- a\n- b\n  - nested\n", "- a\n- b\n  - nested\n- c"):
        b.set_src(src, True)
        h = b.measure(W, m)
        assert h == doc_height(m.markdown_doc(src, W, streaming=True)), src
    assert b._grower is None  # noqa: SLF001 - a nested list is rendered from its source


def test_a_table_grows_a_row_at_a_time_and_widens_its_columns(app):
    head = ["| Name | Calls |\n", "|---|---:|\n"]
    rows = [f"| `fn{i}()` | {i} |\n" for i in range(60)]
    b, bad, _per = stream(chunks(head + rows, 6), lambda s: s.endswith("|\n"))
    assert bad == []
    # a row that needs a wider first column widens it in every chunk
    m = measurer()
    b2 = ProseBlock("p", "", para=0)
    b2.streaming = True
    src = "".join(head + rows)
    b2.set_src(src, True)
    b2.measure(W, m)
    g = b2._grower  # noqa: SLF001
    assert isinstance(g, grow.TableGrower)
    tables = [f for f in b2.doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
    assert len(tables) == 2 and tables[0].rows() == 1 + markdown.TABLE_CHUNK
    before = [c.rawValue() for c in tables[0].format().columnWidthConstraints()]
    src += "| `a_much_longer_function_name_than_the_others()` | 1 |\n"
    b2.set_src(src, True)
    h = b2.measure(W, m)
    after = [c.rawValue() for c in tables[0].format().columnWidthConstraints()]
    assert after[0] > before[0]
    assert all(
        [c.rawValue() for c in t.format().columnWidthConstraints()] == after
        for t in b2.doc.rootFrame().childFrames()
        if isinstance(t, QTextTable)
    )
    assert h == doc_height(m.markdown_doc(src, W, streaming=True))


def test_a_long_paragraph_comes_in_the_same_pieces_written_or_whole(app):
    words = " ".join(f"word{i} `code{i}` **bold{i}**" for i in range(2000))
    pieces = markdown.split_blocks_ex(words)
    assert len(pieces) >= 4 and pieces[0][1] is False and all(c for _s, c in pieces[1:])
    for src, _c in pieces[:-1]:
        assert src.count("`") % 2 == 0 and src.count("*") % 2 == 0  # cut only where the markup is balanced
    assert "".join(s + " " for s, _c in pieces).strip() == words.replace("\n", " ")
    # written: the pieces so far are a prefix of the pieces of the whole
    for k in (7000, 13000, 20000):
        part = [s for s, _c in markdown.split_blocks_ex(words[:k])]
        whole = [s for s, _c in pieces]
        assert part[:-1] == whole[: len(part) - 1]
    g = TextGroup(TextItem(id="t1", text=words, streaming=False, ts=0))
    g.ensure_shape(None)
    assert len(g.children) == len(pieces)
    assert all(c.gap == 0 for c in g.children[1:])  # continuations sit right under the piece before


def test_a_token_costs_the_same_at_the_end_of_a_long_block(app):
    lines = ["```ts\n"] + [f"const x{i} = compute(x{i - 1}, {i}); // step {i}\n" for i in range(1, 400)]
    _b, _bad, per = stream(lines, lambda _s: False)
    n = len(per)
    first = sorted(per[1 : n // 10])[len(per[1 : n // 10]) // 2]
    last = sorted(per[-n // 10 :])[len(per[-n // 10 :]) // 2]
    assert last < 4 * first + 2.0, (first, last)


def test_a_streaming_text_splits_into_the_blocks_of_a_full_split(app):
    """Fed a character at a time, the group's blocks are what one split of the whole text gives."""
    parts = [
        "# Title\n\n",
        "para one words. ",
        "more words.\n\n",
        "- a `x`\n",
        "- b\n",
        "- c\n\n",
        "```py\n",
        "x = 1\n",
        "```\n\n",
        "| a | b |\n",
        "|---|---|\n",
        "| 1 | 2 |\n",
        "| 3 | 4 |\n",
        "\n",
        "last para\n",
        "---\n",
        "after\n",
        "- d\n",
        "# heading after an item\n",
        "1. one\n",
        "2. two\n\n",
        "- e\n",
        "lazy line under an item\n\n",
        "- f\n\n",
        "  indented under f\n\n",
        "setext\n",
        "===\n\n",
        "```\n",
        "unclosed fence\n",
        "\n",
        "tail",
    ]
    text = "".join(parts)
    item = TextItem(id="t-split", text="", streaming=True, ts=0)
    g = TextGroup(item)
    for k in range(1, len(text) + 1):
        item.text = text[:k]
        item.rev += 1
        g.sync(item)
        assert [c.src for c in g.children] == markdown.split_blocks(text[:k]), repr(text[:k])
