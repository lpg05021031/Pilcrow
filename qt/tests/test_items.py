"""The conversation's rows drawn offscreen without a server: the prose view's line boxes and painted
decorations, the person's bubble, the column width and the workflow row's body."""

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

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402
from pilcrow_qt.lib.transcript_model import ToolItem, UserItem  # noqa: E402
from pilcrow_qt.store import Runtime, store  # noqa: E402
from pilcrow_qt.widgets.transcript.ctx import ToolCtx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


def pump() -> None:
    QCoreApplication.processEvents()


def view(text: str, width: int = 700, **kw):
    from pilcrow_qt.widgets.transcript.items import MarkdownView

    v = MarkdownView(**kw)
    v.setFixedWidth(width)
    v.show()
    v.set_text(text)
    pump()
    return v


def test_prose_lines_are_24px_apart(app):
    one = view("one line")
    two = view("word " * 30)  # wraps to two lines at 700px
    assert one.height() == 24, one.height()
    assert two.height() == 48, two.height()
    paras = view("a\n\nb")
    assert paras.height() == 24 + 11 + 24  # 0.75em between paragraphs, none under the last
    compact = view("one line", compact=True)
    assert compact.height() == 21


def test_pill_rects_follow_the_code_spans(app):
    from pilcrow_qt.widgets.transcript.items import MarkdownView

    v = view("see `next` and `/undefined` here")
    doc = v.document()
    b = doc.firstBlock()
    marked = []
    it = b.begin()
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
    img = v.viewport().grab().toImage()
    tl = b.layout()
    start = len("see ")
    x = int(tl.lineAt(0).cursorToX(start + 3)[0]) + 1
    pill = theme.blend(theme.tokens().fill_2, theme.tokens().canvas)
    row = [img.pixelColor(x, y).name() for y in range(0, 24)]
    assert pill.name() in row, row
    assert isinstance(v, MarkdownView)


def test_copied_text_loses_the_pill_padding(app):
    v = view("see `next` here")
    cur = v.textCursor()
    cur.select(cur.SelectionType.Document)
    v.setTextCursor(cur)
    md = v.createMimeDataFromSelection()
    assert md.text() == "see next here"


def test_fence_well_and_quote_are_marked(app):
    v = view("```ts\nconst a = 1;\n```\n\n> quoted\n\nafter")
    doc = v.document()
    tables = [f for f in doc.rootFrame().childFrames()]
    assert len(tables) == 1 and tables[0].format().borderCollapse()
    quotes = []
    b = doc.firstBlock()
    while b.isValid():
        if b.blockFormat().leftMargin() == markdown.QUOTE_INDENT:
            quotes.append(b.text())
        b = b.next()
    assert quotes == ["quoted"]
    # the empty paragraph after the table takes no room: well (30 + 20 + 13 = 63) + 1em + quote + 11 + after
    assert v.height() <= 9 + 63 + 15 + 2 + 24 + 11 + 24 + 2, v.height()


def test_find_lights_matches_and_the_current_one(app):
    v = view("next and next again")
    assert v.set_find("next", 1)
    sels = v.extraSelections()
    assert len(sels) == 2
    assert sels[0].cursor.selectedText() == "next" and sels[1].cursor.selectedText() == "next"
    assert sels[1].format.background().color().name() == theme.tokens().accent
    assert sels[0].format.background().color().name() != theme.tokens().accent
    assert not v.set_find("", -1) and not v.extraSelections()


def test_user_bubble_is_the_webs_size(app):
    from pilcrow_qt.widgets.transcript.items import BUBBLE_LINE, UserMessage

    assert BUBBLE_LINE == 22  # 14.5px on 1.55
    item = UserItem(id="u1", uuid="uu1", text="Please fix it and run the tests.")
    w = UserMessage(item, ToolCtx("sess", "/proj"))
    w.setFixedWidth(742)
    w.show()
    pump()
    w.resize(742, w.sizeHint().height())
    pump()
    assert w.bubble.height() == 9 + 22 + 9, w.bubble.height()
    assert w.bubble.width() < 742 * 0.84 + 1
    long = UserMessage(UserItem(id="u2", uuid="uu2", text="word " * 60), ToolCtx("sess", "/proj"))
    long.setFixedWidth(742)
    long.show()
    pump()
    long.resize(742, long.sizeHint().height())
    pump()
    assert long.bubble.width() == int(742 * 0.84), long.bubble.width()  # max-width 84% of the column
    assert long.bubble.height() > 9 + 22 * 2 + 9


def test_column_width_is_the_golden_section_of_the_pane():
    from pilcrow_qt.widgets.transcript.view import column_width

    assert column_width(1212) == 749
    assert column_width(1202) == 742  # the web's pane at 1480 wide: text from x=499 to 1241
    assert column_width(900) == 640  # never under 640
    assert column_width(660) == 640  # clamp() keeps the floor even when the pane is tighter
    assert column_width(3000) == 1080


def test_workflow_row_body_reads_the_run_from_the_session(app):
    from pilcrow_qt.widgets.transcript.bodies.workflow import WorkflowInline, WorkflowMeta

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
    rt.st = {
        "status": "idle",
        "tasks": [
            {
                "id": "t1",
                "toolUseId": "toolu_wf",
                "type": "local_workflow",
                "status": "completed",
                "workflow": {
                    "phases": [{"index": 0, "title": "A"}],
                    "agents": [
                        {"type": "workflow_agent", "index": 0, "state": "done"},
                        {"type": "workflow_agent", "index": 1, "state": "done"},
                    ],
                    "logs": [],
                },
            }
        ],
    }
    store.rt["sess-wf-test"] = rt
    try:
        w = WorkflowInline(item, ToolCtx("sess-wf-test", "/proj"))
        w.show()
        pump()
        texts = [lb.text() for lb in w.findChildren(QLabel)]
        assert "Review it" in texts
        assert "3 phases · 2 of 2 agents done" in texts
        assert [b.text() for b in w.findChildren(QPushButton)][0] == "Open in Tasks"
        assert w.result is None  # no result text: no toggle
        meta = WorkflowMeta("sess-wf-test", "toolu_wf")
        assert meta.text() == "2/2 agents"
    finally:
        store.rt.pop("sess-wf-test", None)
