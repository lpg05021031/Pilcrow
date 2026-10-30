"""The conversation's rows measured and painted offscreen without a server: the prose block's line
boxes and painted decorations, the person's bubble, the column width and the workflow body."""

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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402
from pilcrow_qt.lib.transcript_model import TextItem, ToolItem, UserItem  # noqa: E402
from pilcrow_qt.store import Runtime, store  # noqa: E402
from pilcrow_qt.widgets.transcript.ctx import ToolCtx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


def measurer():
    from pilcrow_qt.canvas.view import measurer

    return measurer()


def prose(text: str, width: int = 700, **kw):
    from pilcrow_qt.canvas.blocks.prose import ProseBlock

    b = ProseBlock("p", text, **kw)
    b.set_height(b.measure(width, measurer()), 2, width)
    return b


def painted(b, width: int = 700) -> QImage:
    """The block painted on the canvas colour, as the view would paint it."""
    from pilcrow_qt.canvas.paint import PaintState

    m = measurer()
    img = QImage(width, max(1, b.h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(theme.tokens().canvas))
    p = QPainter(img)
    st = PaintState(m)
    st.now = 0.0
    st.needle = ""
    b.paint(p, 0, 0, width, st.for_block(None, None, None, None))
    p.end()
    return img


def test_prose_lines_are_24px_apart(app):
    from pilcrow_qt.canvas.blocks.prose import TextGroup

    assert prose("one line").h == 24
    assert prose("word " * 30).h == 48  # wraps to two lines at 700px
    g = TextGroup(TextItem(id="t1", text="a\n\nb", streaming=False, ts=0))
    assert not g.shaped and g.estimate(700, measurer()) > 0  # one estimate until the text comes near
    g.ensure_shape(None)
    assert len(g.children) == 2
    for c in g.children:
        c.set_height(c.measure(700, measurer()), 2, 700)
    assert g.children[1].gap == 11 and g.h == 24 + 11 + 24  # 0.75em between paragraphs, none under the last
    assert prose("one line", compact=True).h == 21


def test_pill_rects_follow_the_code_spans(app):
    b = prose("see `next` and `/undefined` here")
    doc = b.doc
    blk = doc.firstBlock()
    marked = []
    it = blk.begin()
    while not it.atEnd():
        fr = it.fragment()
        bg = fr.charFormat().background()
        if bg.style() != Qt.BrushStyle.NoBrush and bg.color().alpha() == 0:
            marked.append(fr.text().replace(chr(0x2060), ""))  # the no-wrap joiners are not text
        it += 1
    assert marked == [
        f"{markdown.PILL_PAD}next{markdown.PILL_PAD}",
        f"{markdown.PILL_PAD}/undefined{markdown.PILL_PAD}",
    ]
    # the painted pill is the fill-2 tint under the first span's glyphs
    img = painted(b)
    tl = blk.layout()
    start = len("see ")
    x = int(tl.lineAt(0).cursorToX(start + 3)[0]) + 1
    pill = theme.blend(theme.tokens().fill_2, theme.tokens().canvas)
    row = [img.pixelColor(x, y).name() for y in range(0, 24)]
    assert pill.name() in row, row


def test_copied_text_loses_the_pill_padding(app):
    b = prose("see `next` here")
    assert b.selected_text(None, None) == "see next here"
    assert b.text() == "see `next` here"  # what find searches: the source


def test_fence_well_and_quote_are_marked(app):
    b = prose("```ts\nconst a = 1;\n```\n\n> quoted\n\nafter")
    doc = b.doc
    tables = [f for f in doc.rootFrame().childFrames()]
    assert len(tables) == 1 and tables[0].format().borderCollapse()
    quotes = []
    blk = doc.firstBlock()
    while blk.isValid():
        if blk.blockFormat().leftMargin() == markdown.QUOTE_INDENT:
            quotes.append(blk.text())
        blk = blk.next()
    assert quotes == ["quoted"]
    # the empty paragraph after the table takes no room: well (30 + 20 + 13 = 63) + 1em + quote + 11 + after
    assert b.h <= 9 + 63 + 15 + 2 + 24 + 11 + 24 + 2, b.h


def test_find_lights_matches_and_the_current_one(app):
    from pilcrow_qt.canvas.paint import PaintState, find_ranges

    b = prose("next and next again")
    ranges = find_ranges(b.doc, "next")
    assert len(ranges) == 2 and all(b.doc.toPlainText()[a:z] == "next" for a, z in ranges)
    # painted: the current match in the accent, the other in the wash
    m = measurer()
    img = QImage(700, b.h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(theme.tokens().canvas))
    p = QPainter(img)
    st = PaintState(m)
    st.now = 0.0
    st.needle = "next"
    bst = st.for_block(None, None, None, None)
    bst.needle_current = 1
    b.paint(p, 0, 0, 700, bst)
    p.end()
    seen = {img.pixelColor(x, 12).name() for x in range(0, 200)}
    assert theme.tokens().accent in seen
    assert theme.blend("rgba(217, 119, 87, 0.28)", theme.tokens().canvas).name() in seen


def test_user_bubble_is_the_webs_size(app):
    from pilcrow_qt.canvas.blocks.user import BUBBLE_LINE, PAD_H, UserBlock

    assert BUBBLE_LINE == 22  # 14.5px on 1.55
    m = measurer()
    b = UserBlock(UserItem(id="u1", uuid="uu1", text="Please fix it and run the tests."), "sess", "/proj")
    b.set_height(b.measure(742, m), 2, 742)
    assert b.bubble.height() == PAD_H + 22 + PAD_H, b.bubble.height()
    assert b.bubble.width() < 742 * 0.84 + 1
    assert b.bubble.right() == 742 - 1, "the bubble sits at the right of the column"
    long = UserBlock(UserItem(id="u2", uuid="uu2", text="word " * 60), "sess", "/proj")
    long.set_height(long.measure(742, m), 2, 742)
    assert long.bubble.width() == int(742 * 0.84), long.bubble.width()  # max-width 84% of the column
    assert long.bubble.height() > PAD_H + 22 * 2 + PAD_H
    # the estimate is close to the measure: the scrollbar of a cold history is honest
    assert abs(long.estimate(742, m) - long.h) <= 22 * 2


def test_column_width_is_the_golden_section_of_the_pane():
    from pilcrow_qt.canvas.view import column_width

    assert column_width(1212) == 749
    assert column_width(1202) == 742  # the web's pane at 1480 wide: text from x=499 to 1241
    assert column_width(900) == 640  # never under 640
    assert column_width(660) == 640  # clamp() keeps the floor even when the pane is tighter
    assert column_width(3000) == 1080


def test_workflow_row_body_reads_the_run_from_the_session(app):
    from pilcrow_qt.canvas import nodes as nd
    from pilcrow_qt.canvas.blocks.bodies import ToolBodyBlock
    from pilcrow_qt.lib.workflow import workflow_counts

    script = "export const meta = { name: 'review', description: 'Review it', phases: ['A', 'B', 'C'] }"
    item = ToolItem(
        id="toolu_wf",
        name="Workflow",
        input={"script": script},
        input_json="",
        input_done=True,
        status="done",
        start_ts=0,
    )
    rt = Runtime(loaded=True)
    agents = [
        {"type": "workflow_agent", "index": 0, "state": "done"},
        {"type": "workflow_agent", "index": 1, "state": "done"},
    ]
    rt.st = {
        "status": "idle",
        "tasks": [
            {
                "id": "t1",
                "toolUseId": "toolu_wf",
                "type": "local_workflow",
                "status": "completed",
                "workflow": {"phases": [{"index": 0, "title": "A"}], "agents": agents, "logs": []},
            }
        ],
    }
    store.rt["sess-wf-test"] = rt
    try:
        body = ToolBodyBlock(item, ToolCtx("sess-wf-test", "/proj"))
        body.measure(640, measurer())
        root = body.root
        assert root is not None

        def leaves(n):
            if getattr(n, "children", None) is not None:
                for c in n.children:
                    yield from leaves(c)
            elif getattr(n, "child", None) is not None:
                yield from leaves(n.child)
            else:
                yield n

        texts = [x.text() for x in leaves(root) if x.text()]
        assert "Review it" in texts
        assert "3 phases · 2 of 2 agents done" in texts
        assert [x.label for x in leaves(root) if isinstance(x, nd.Button)] == ["Open in Tasks"]
        assert not [x for x in leaves(root) if isinstance(x, nd.Toggle)]  # no result text: no toggle
        counts = workflow_counts(agents)
        assert (counts.done, counts.total) == (2, 2)  # the "2/2 agents" at the right of the head
    finally:
        store.rt.pop("sess-wf-test", None)
