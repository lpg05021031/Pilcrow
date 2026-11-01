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

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt  # noqa: E402
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
        metrics = m.metrics
        m.refresh()  # every live view asks on a switch; the work is done once
        assert m.metrics is metrics
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
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface

    v = view
    iface = QAccessible.queryAccessibleInterface(v.viewport())
    assert iface is not None and iface.role() == QAccessible.Role.List
    n = iface.childCount()
    from pilcrow_qt.canvas.a11y import describe

    assert n == len([b for b in v.visible_blocks() if b.h > 0 and describe(b) is not None]) and n >= 3
    names = [iface.child(i).text(QAccessible.Text.Name) for i in range(n)]
    assert names[0] == "Please look at the parser."
    assert any("reads lines" in s for s in names)
    card = iface.child(n - 1)
    assert card.role() == QAccessible.Role.Button and iface.indexOfChild(card) == n - 1
    assert card.parent() is iface and card.rect().width() > 100
    assert "rows on screen" in iface.text(QAccessible.Text.Description)
    # the name is a summary, the value the whole text
    para = iface.child(1)
    assert para.text(QAccessible.Text.Name) == para.text(QAccessible.Text.Value).splitlines()[0][:120]
    # the card can be pressed: its head opens it
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    tool_block = _blocks(v, ToolBlock)[0]
    assert not tool_block.open and card.state().collapsed and card.state().expandable
    actions = card.actionInterface()
    assert actions is not None and actions.actionNames() == [QAccessibleActionInterface.pressAction()]
    actions.doAction(QAccessibleActionInterface.pressAction())
    assert tool_block.open and card.state().expanded
    actions.doAction(QAccessibleActionInterface.pressAction())
    assert not tool_block.open
    assert iface.child(0).actionInterface().actionNames() == []  # a plain row has nothing to press


def test_accessibility_announces_focus_folds_and_new_rows(view, monkeypatch):
    from PySide6.QtGui import QAccessible

    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    v = view
    QAccessible.queryAccessibleInterface(v.viewport())  # what a screen reader does first
    heard: list = []

    def update(ev) -> None:
        it = ev.accessibleInterface()
        heard.append((ev.type(), it.text(QAccessible.Text.Name) if it is not None else ""))

    monkeypatch.setattr(QAccessible, "updateAccessibility", update)
    QAccessible.setActive(True)
    try:
        card = _blocks(v, ToolBlock)[0]
        head = v.head_target(card)
        v._set_focus((card.key, head))
        assert heard[-1] == (QAccessible.Event.Focus, card.text())
        v.toggle_group(card, True, animate=False)
        assert heard[-1][0] == QAccessible.Event.StateChanged
        v.toggle_group(card, False, animate=False)
        v.blocks_seen = len(heard)
        v.begin()
        try:
            v.scene.root.append(TextGroup(TextItem(id="t2", text="A new paragraph arrives.", streaming=False, ts=0)))
        finally:
            v.end()
        pump(60)  # the new group takes its shape and its height on the view's next pass
        assert (QAccessible.Event.ObjectShow, "A new paragraph arrives.") in heard[v.blocks_seen :]
    finally:
        QAccessible.setActive(False)


def test_accessibility_forgets_what_is_gone(app):
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QAccessible

    from pilcrow_qt.canvas import a11y
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.scene import Scene
    from pilcrow_qt.canvas.view import CanvasView

    scene = Scene()
    v = CanvasView(scene)
    v.resize(600, 400)
    v.begin()
    try:
        for i in range(3):
            scene.root.append(TextGroup(TextItem(id=f"g{i}", text=f"row {i}", streaming=False, ts=0)))
    finally:
        v.end()
    v.show()
    pump(30)
    lst = QAccessible.queryAccessibleInterface(v.viewport())
    items = {lst.child(i)._key: lst.child(i) for i in range(lst.childCount())}  # each paragraph, not its group
    assert sorted(items) == ["text:g0:0", "text:g1:0", "text:g2:0"]
    # a dropped block's item goes at the next prune
    v.begin()
    try:
        scene.root.truncate(1)
    finally:
        v.end()
    lst._prune()
    assert sorted(lst._items) == ["text:g0:0"]
    assert not shiboken6.isValid(items["text:g2:0"]) and shiboken6.isValid(items["text:g0:0"])
    # the view's list goes once Qt has let go of it with the viewport
    v.close()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(lst)  # Qt's cache deleted it with the viewport
    other = CanvasView(Scene())
    QAccessible.queryAccessibleInterface(other.viewport())
    assert all(shiboken6.isValid(x) for x in a11y._lists.values())
    other.deleteLater()


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


class _Damage(QObject):
    """The area each paint of the viewport covers (the region Qt asks it to repaint)."""

    def __init__(self, vp) -> None:
        super().__init__()
        self.areas: List[int] = []
        vp.installEventFilter(self)

    def eventFilter(self, _obj, e) -> bool:
        if e.type() == QEvent.Type.Paint:
            self.areas.append(sum(r.width() * r.height() for r in e.region()))
        return False


def test_hover_repaints_the_targets_and_a_drag_the_rows_it_changed(view):
    """DESIGN §8.1, §8.3: a hover repaints the old and the new target's rects, not the rows; a
    selection drag repaints the rows whose selection changed, and the rows outside it still paint
    from the pixmap cache."""
    from pilcrow_qt.canvas.blocks.prose import ProseBlock
    from pilcrow_qt.canvas.blocks.tool import ToolBlock
    from pilcrow_qt.canvas.blocks.user import UserBlock

    v = view
    vp = v.viewport()
    v.clear_selection()
    pump(30)
    card = _blocks(v, ToolBlock)[0]
    cr = v.block_rect(card)
    dmg = _Damage(vp)
    # onto the card's head from the words above it, then along it: the head, once
    QTest.mouseMove(vp, QPoint(cr.left() + 40, cr.top() - 30))
    pump(20)
    dmg.areas.clear()
    QTest.mouseMove(vp, QPoint(cr.left() + 40, cr.top() + 8))
    pump(20)
    head = (cr.width() + 16) * card.head_h
    assert dmg.areas and sum(dmg.areas) <= head, (dmg.areas, head)
    dmg.areas.clear()
    QTest.mouseMove(vp, QPoint(cr.left() + 200, cr.top() + 8))  # the same target: nothing to repaint
    pump(20)
    assert sum(dmg.areas) == 0, dmg.areas
    # onto a link in the paragraph: its look does not change under the pointer, so nothing repaints
    para = _blocks(v, ProseBlock)[0]
    pr = v.block_rect(para)
    lx = next(x for x in range(0, pr.width(), 2) if (t := para.hit(x, 10)) is not None and t.kind == "link")
    dmg.areas.clear()
    QTest.mouseMove(vp, QPoint(pr.left() + lx + 2, pr.top() + 10))
    pump(20)
    assert v.hover is not None and v.hover[1].kind == "link" and sum(dmg.areas) <= head, dmg.areas
    # a drag inside the paragraph repaints the paragraph and nothing else
    QTest.mousePress(
        vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(pr.left() + 2, pr.top() + 10)
    )
    pump(20)
    dmg.areas.clear()
    user = _blocks(v, UserBlock)[0]
    painted: list = []
    real = UserBlock.paint
    UserBlock.paint = lambda self, *a: (painted.append(self.key), real(self, *a))[1]
    try:
        for x in (40, 80, 120, 160):
            QTest.mouseMove(vp, QPoint(pr.left() + x, pr.top() + 10))
            pump(20)
        v.viewport().repaint()  # the whole viewport: what is not selected comes from the cache
    finally:
        UserBlock.paint = real
        QTest.mouseRelease(
            vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(pr.left() + 160, pr.top() + 10)
        )
    moves = dmg.areas[:-1]
    assert moves and max(moves) <= vp.width() * para.h, (moves, vp.width() * para.h)
    assert user.key not in painted, "an unselected row paints from its cached pixmap"
    v.clear_selection()


def test_a_painter_that_raises_logs_once_and_the_rest_of_the_frame_paints(app, caplog):
    """DESIGN §6.6: one row's painter raising neither blanks the frame nor floods the log."""
    import logging

    from pilcrow_qt.canvas.scene import Block, Scene
    from pilcrow_qt.canvas.view import CanvasView

    painted: List[str] = []

    class Row(Block):
        __slots__ = ()

        def estimate(self, width, m):
            return 40

        @property
        def static(self) -> bool:
            return False  # painted every frame, not from a cached pixmap

        def paint(self, p, x, y, w, st):
            if self.key == "boom":
                raise ValueError("a painter bug")
            painted.append(self.key)

    scene = Scene()
    v = CanvasView(scene)
    v.resize(600, 400)
    v.begin()
    try:
        for k in ("above", "boom", "below"):
            scene.root.append(Row(k))
    finally:
        v.end()
    try:
        with caplog.at_level(logging.WARNING, logger="pilcrow_qt.canvas.view"):
            v.show()
            pump(30)
            painted.clear()
            for _ in range(10):
                v.viewport().repaint()
        failed = [r for r in caplog.records if "could not paint" in r.getMessage()]
        assert len(failed) == 1 and scene.block("boom").paint_failed, failed
        assert painted.count("above") == 10 and painted.count("below") == 10, painted
    finally:
        v.close()


def test_accessibility_exposes_the_targets_inside_a_row_and_announces_each(app, monkeypatch):
    """A card with three buttons: three children with their own names, roles and Press actions, and
    Tab across them announces each button, not the card three times."""
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface

    from pilcrow_qt.canvas import nodes as nd
    from pilcrow_qt.canvas.blocks.base import NodeBlock
    from pilcrow_qt.canvas.scene import Scene
    from pilcrow_qt.canvas.view import CanvasView

    pressed: List[str] = []
    card = NodeBlock(
        "card",
        lambda m: nd.vstack(
            nd.Text("Allow the edit?", 13, 400, "text"),
            nd.hstack(nd.Button("Allow", "allow"), nd.Button("Deny", "deny"), nd.Link("Open the plan", "plan")),
        ),
    )
    for tid in ("allow", "deny", "plan"):
        card.on(tid, lambda t, _v: pressed.append(t.id))
    scene = Scene()
    v = CanvasView(scene)
    v.resize(600, 300)
    v.begin()
    try:
        scene.root.append(card)
    finally:
        v.end()
    v.show()
    v.setFocus()
    pump(40)
    heard: list = []
    monkeypatch.setattr(
        QAccessible, "updateAccessibility", lambda ev: heard.append((ev.type(), ev.accessibleInterface()))
    )
    QAccessible.setActive(True)
    try:
        row = QAccessible.queryAccessibleInterface(v.viewport()).child(0)
        assert row.text(QAccessible.Text.Name) == "Allow the edit?" and row.childCount() == 3
        kids = [row.child(i) for i in range(3)]
        assert [k.text(QAccessible.Text.Name) for k in kids] == ["Allow", "Deny", "Open the plan"]
        assert [k.role() for k in kids] == [QAccessible.Role.Button] * 2 + [QAccessible.Role.Link]
        assert all(k.parent() is row and row.indexOfChild(k) == i for i, k in enumerate(kids))
        rr = row.rect()
        assert all(rr.contains(k.rect()) and not k.rect().isEmpty() for k in kids)
        assert row.childAt(kids[1].rect().center().x(), kids[1].rect().center().y()) is kids[1]
        for _ in range(3):
            assert v.focusNextPrevChild(True)
        focused = [it.text(QAccessible.Text.Name) for kind, it in heard if kind == QAccessible.Event.Focus]
        assert focused == ["Allow", "Deny", "Open the plan"], focused
        assert kids[2].state().focused and not kids[0].state().focused
        kids[1].actionInterface().doAction(QAccessibleActionInterface.pressAction())
        assert pressed == ["deny"]
    finally:
        QAccessible.setActive(False)
        v.close()


def test_accessibility_announces_the_words_a_streamed_row_gains_once_a_second(app, monkeypatch):
    from PySide6.QtGui import QAccessible

    from pilcrow_qt.canvas import a11y
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.scene import Scene
    from pilcrow_qt.canvas.view import CanvasView

    scene = Scene()
    v = CanvasView(scene)
    v.resize(600, 300)
    item = TextItem(id="s1", text="The **answer**", streaming=True, ts=0)
    g = TextGroup(item)
    v.begin()
    try:
        scene.root.append(g)
    finally:
        v.end()
    v.show()
    pump(40)
    said: list = []

    def update(ev) -> None:
        if ev.type() == QAccessible.Event.TextInserted:
            said.append(ev.textInserted())

    monkeypatch.setattr(QAccessible, "updateAccessibility", update)
    QAccessible.setActive(True)
    clock = [100.0]
    monkeypatch.setattr(a11y.time, "monotonic", lambda: clock[0])

    def grow(text: str, streaming: bool = True, dt: float = 0.25) -> None:
        clock[0] += dt
        item.text, item.streaming, item.rev = text, streaming, item.rev + 1
        v.begin()
        try:
            for b in g.sync(item):
                v.remeasure(b)
        finally:
            v.end()

    try:
        QAccessible.queryAccessibleInterface(v.viewport())
        grow("The **answer**")
        assert said == ["The answer"]  # the words, not the Markdown
        grow("The **answer** is")
        grow("The **answer** is forty")  # within the second: nothing yet
        assert said == ["The answer"]
        grow("The **answer** is forty-two", dt=1.0)
        assert said == ["The answer", " is forty-two"]
        grow("The **answer** is forty-two.", streaming=False)  # it finished: the rest at once
        assert said[-1] == "." and "".join(said) == "The answer is forty-two."
    finally:
        QAccessible.setActive(False)
        v.close()


def test_a_hidden_view_leaves_the_theme_work_until_it_is_shown(app):
    """DESIGN §8.8: a retained view behind another neither re-measures on a theme switch nor drains;
    it is marked stale, and measures its screen when it is shown."""
    from pilcrow_qt.canvas.blocks.prose import TextGroup
    from pilcrow_qt.canvas.scene import Scene
    from pilcrow_qt.canvas.view import CanvasView

    scene = Scene()
    v = CanvasView(scene)
    v.resize(600, 400)
    v.begin()
    try:
        for i in range(6):
            scene.root.append(
                TextGroup(TextItem(id=f"h{i}", text=f"Paragraph {i} of a retained view.", streaming=False, ts=0))
            )
    finally:
        v.end()
    v.show()
    pump(60)
    v.hide()
    measured: List[str] = []
    real = v._exact
    v._exact = lambda b: (measured.append(b.key), real(b))[1]
    try:
        theme.theme.apply(False)
        pump(60)
        v._drain()
        assert v.stale and measured == [], measured
        v.show()
        pump(60)
        assert not v.stale and measured, "shown: the screen is measured again in the new theme"
        assert all(b.warm for b in v.visible_blocks())
    finally:
        theme.theme.apply(True)
        v.close()
