"""What a person does to the canvas with the keyboard and the pointer, offscreen and without a
server: Tab over the targets on screen with a painted ring, Enter opening a fold, double and triple
clicks selecting a word and a block, Ctrl+A, the right-click menu's items, and the text scale."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.lib.transcript_model import TextItem, ToolItem, TranscriptModel  # noqa: E402
from pilcrow_qt.store import store  # noqa: E402
from pilcrow_qt.widgets.transcript.ctx import ToolCtx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


def pump(ms: int = 30) -> None:
    import time

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()


def tool(name: str, inp: dict, result: str) -> ToolItem:
    m = TranscriptModel()
    tid = f"tool-{name}"
    m.push(
        {
            "type": "assistant",
            "uuid": f"u-{tid}",
            "message": {
                "id": f"m-{tid}",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
            },
        }
    )
    m.push(
        {
            "type": "user",
            "uuid": f"r-{tid}",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]},
        }
    )
    return m.tools[tid]


@pytest.fixture()
def view(app):
    """A view over a user line, a paragraph with a link and a closed tool card."""
    from pilcrow_qt.canvas.blocks.fold import Flat
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.blocks.tool import ToolBlock
    from pilcrow_qt.canvas.blocks.user import UserBlock
    from pilcrow_qt.canvas.scene import Scene
    from pilcrow_qt.canvas.view import CanvasView
    from pilcrow_qt.lib.transcript_model import UserItem

    scene = Scene()
    v = CanvasView(scene)
    v.resize(760, 600)
    ctx = ToolCtx("sess", "/proj")
    blocks = [
        UserBlock(UserItem(id="u1", uuid="uu1", text="Please look at the parser."), "sess", "/proj"),
        TextGroup(
            TextItem(
                id="t1",
                text="The parser lives in [lib](https://example.com/lib) and reads lines.",
                streaming=False,
                ts=0,
            )
        ),
        ToolBlock(
            tool("Read", {"file_path": "/proj/src/a.ts"}, "1→const a = 1;"),
            ctx,
            None,
            lambda items, c: Flat(items, c, "sess", in_work=True),
        ),
    ]
    v.begin()
    try:
        for b in blocks:
            scene.root.append(b)
    finally:
        v.end()
    v.show()
    v.activateWindow()
    v.setFocus()
    pump(60)
    yield v
    v.close()


def _blocks(v, cls):
    return [b for b in v.scene.root.walk() if isinstance(b, cls)]


def test_tab_moves_the_focus_over_targets_and_enter_opens(view):
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    v = view
    assert v.focus is None
    ring = v.focusables()
    assert ring, "the bubble's actions, the link and the card's head are targets"
    kinds = [t.kind for _b, t in ring]
    assert "head" in kinds
    assert v.focusNextPrevChild(True)
    assert v.focus is not None and v.focus[1] == ring[0][1]
    # Tab walks to the card's head; Enter opens it
    for _ in range(len(ring)):
        if v.focus[1].kind == "head":
            break
        assert v.focusNextPrevChild(True)
    card = _blocks(v, ToolBlock)[0]
    assert v.focus[0] == card.key and not card.open
    QTest.keyClick(v, Qt.Key_Return)
    pump(50)
    assert card.open
    # past the last target, Tab leaves the view and the marker goes
    while v.focusNextPrevChild(True):
        pass
    assert v.focus is None


def test_focus_ring_is_painted_in_the_accent(view):
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    v = view
    card = _blocks(v, ToolBlock)[0]
    head = card.hit(2, 2)
    v._set_focus((card.key, head))
    v.setFocus()
    pump(30)
    img = v.viewport().grab().toImage()
    r = v.block_rect(card)
    accent = theme.qcolor(theme.tokens().accent)

    def near_accent(img) -> bool:
        for y in range(r.top() - 5, r.top() + 1):
            for x in range(r.left() - 6, r.right() + 6, 2):
                c = img.pixelColor(x, y)
                if abs(c.red() - accent.red()) + abs(c.green() - accent.green()) + abs(c.blue() - accent.blue()) < 40:
                    return True
        return False

    assert near_accent(img), "a ring just above the head"
    v._set_focus(None)
    pump(30)
    assert not near_accent(v.viewport().grab().toImage())


def test_double_click_selects_a_word_and_a_third_click_the_block(view):
    from pilcrow_qt.canvas.blocks.prose import ProseBlock

    v = view
    para = _blocks(v, ProseBlock)[0]
    r = v.block_rect(para)
    pos = QPoint(r.left() + 30, r.top() + 10)  # inside "parser"
    QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    pump(20)
    assert v.selection is not None and not v.selection.empty
    word = v.selected_text()
    assert word.strip() and " " not in word.strip() and word.strip() in "The parser lives"
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    pump(20)
    assert v.selected_text().startswith("The parser lives in lib")
    QTest.keyClick(v, Qt.Key_Escape)
    assert v.selection is None


def test_select_all_and_copy(view):
    v = view
    QTest.keyClick(v, Qt.Key_A, Qt.KeyboardModifier.ControlModifier)
    text = v.selected_text()
    assert "Please look at the parser." in text and "reads lines." in text
    v.copy_selection()
    assert "reads lines." in QGuiApplication.clipboard().text()


def test_context_menu_offers_the_link_and_the_file(view):
    from pilcrow_qt.canvas.blocks.prose import ProseBlock
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    v = view
    v.clear_selection()
    para = _blocks(v, ProseBlock)[0]
    r = v.block_rect(para)
    labels: List[str] = [str(it.get("label", "—")) for it in v.context_items(QPoint(r.left() + 10, r.top() + 10))]
    assert labels[:3] == ["Copy", "Copy this message", "Select all"]
    assert "Open file" not in labels
    # on the link itself
    doc = para.doc
    layout = doc.documentLayout()
    found = None
    for x in range(0, r.width(), 4):
        if layout.anchorAt(QPoint(x, 10)):
            found = x
            break
    assert found is not None
    labels = [str(it.get("label", "—")) for it in v.context_items(QPoint(r.left() + found, r.top() + 10))]
    assert "Open link" in labels and "Copy link" in labels
    # on the card: its file
    card = _blocks(v, ToolBlock)[0]
    rc = v.block_rect(card)
    labels = [str(it.get("label", "—")) for it in v.context_items(QPoint(rc.left() + 10, rc.top() + 10))]
    assert "Open file" in labels and "Copy path" in labels


def test_text_scale_grows_the_prose_and_the_bubble(app):
    from pilcrow_qt.canvas.blocks.prose import ProseBlock
    from pilcrow_qt.canvas.blocks.user import UserBlock, bubble_line
    from pilcrow_qt.canvas.view import measurer
    from pilcrow_qt.lib.transcript_model import UserItem

    m = measurer()
    try:
        theme.theme.set_scale(1.3)
        m.refresh()
        b = ProseBlock("p", "one line")
        assert b.measure(700, m) == 31  # 14.5 × 1.3 on 1.65
        assert bubble_line() == 29
        u = UserBlock(UserItem(id="u9", uuid="uu9", text="Hi"), "sess", "/proj")
        u.measure(742, m)
        assert u.bubble.height() == 9 + 29 + 9
    finally:
        theme.theme.set_scale(1.0)
        m.refresh()
    assert ProseBlock("q", "one line").measure(700, m) == 24


def test_accessibility_lists_the_blocks_on_screen(view):
    from PySide6.QtGui import QAccessible

    v = view
    iface = QAccessible.queryAccessibleInterface(v.viewport())
    assert iface is not None and iface.role() == QAccessible.Role.List
    n = iface.childCount()
    assert n == len([b for b in v.visible_blocks() if b.h > 0 and (b.text() or hasattr(b, "children"))]) and n >= 3
    names = [iface.child(i).text(QAccessible.Text.Name) for i in range(n)]
    assert names[0] == "Please look at the parser."
    assert any("reads lines" in s for s in names)
    card = iface.child(n - 1)
    assert card.role() == QAccessible.Role.Button and iface.indexOfChild(card) == n - 1
    assert card.parent() is iface and card.rect().width() > 100
    assert "rows on screen" in iface.text(QAccessible.Text.Description)


def test_dragging_over_words_selects_them(view):
    from pilcrow_qt.canvas.blocks.prose import ProseBlock

    v = view
    v.clear_selection()
    para = _blocks(v, ProseBlock)[0]
    r = v.block_rect(para)
    a = QPoint(r.left() + 2, r.top() + 10)
    b = QPoint(r.left() + 120, r.top() + 10)
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(v.viewport(), b)
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    pump(20)
    text = v.selected_text()
    assert text.startswith("The parser") and len(text) < 40
    v.clear_selection()
