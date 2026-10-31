"""A conversation still arriving shows its skeleton alone (no frames of its beginning before the
view opens at the end), the side panel's file cards come in batches, one lexer serves a language,
and the composer keeps a keystroke cheap with a long draft."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtGui import QTextBlockFormat  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.lib.transcript_model import TranscriptModel  # noqa: E402
from pilcrow_qt.store import Runtime, store  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()


def user(i: int) -> dict:
    return {
        "type": "user",
        "uuid": f"u{i}",
        "message": {"role": "user", "content": [{"type": "text", "text": f"Q {i}"}]},
    }


def reply(i: int) -> dict:
    return {
        "type": "assistant",
        "uuid": f"a{i}",
        "message": {
            "id": f"m{i}",
            "role": "assistant",
            "content": [{"type": "text", "text": f"Reply {i}. " + "words " * 40}],
        },
    }


def test_a_loading_conversation_shows_only_its_skeleton_then_opens_at_the_end(app):
    from pilcrow_qt.canvas.transcript import Transcript, forget_scenes

    forget_scenes()
    sid = "sess-loading"
    m = store.models[sid] = TranscriptModel()
    r = store.rt[sid] = Runtime(loaded=False, loading=True, st={"status": "idle", "cwd": "/proj"})
    win = QWidget()
    win.setObjectName("canvas")
    win.resize(900, 600)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    tr = Transcript(sid, "/proj")
    lay.addWidget(tr)
    win.show()
    pump(100)
    v = tr.view
    try:
        # slices arrive: the scene fills, the page does not
        for k in range(3):
            for i in range(k * 20, k * 20 + 20):
                m.push(user(i))
                m.push(reply(i))
            r.rev = m.rev
            store.rt_changed.emit(sid)
            pump(60)
            assert tr.scene.loading and tr.scene.total > 600
            assert v.verticalScrollBar().maximum() == 0
            img = v.viewport().grab().toImage()
            # the picture is the canvas and the skeleton's faint bars: nothing of the words' colour
            text = theme.qcolor(theme.tokens().text).rgb()
            assert not any(img.pixel(x, y) == text for y in range(0, img.height(), 6) for x in range(0, img.width(), 6))
        r.loaded, r.loading = True, False
        store.rt_changed.emit(sid)
        pump(150)
        assert not tr.scene.loading
        vb = v.verticalScrollBar()
        assert vb.maximum() > 0 and vb.value() == vb.maximum()
        assert all(b.warm for b in v.visible_blocks() if b.KIND == "prose")
    finally:
        tr.close()
        win.close()
        store.models.pop(sid, None)
        store.rt.pop(sid, None)


def test_file_cards_come_in_batches_in_order(app):
    from pilcrow_qt.widgets.panels.diff_view import DiffView

    v = DiffView(simple=True)
    v.show()
    files = [
        {
            "path": f"/proj/src/f{i}.py",
            "status": "M",
            "hunks": [
                {
                    "oldStart": 1,
                    "oldLines": 1,
                    "newStart": 1,
                    "newLines": 1,
                    "lines": [{"t": "+", "s": f"x = {i}", "n": 1}],
                }
            ],
        }
        for i in range(30)
    ]
    v.set_files(files)
    first = len(v.rows())
    assert 0 < first < 30, "the first batch is on the page before the rest exist"
    pump(200)
    assert [r.path for r in v.rows()] == [f["path"] for f in files]
    v.close()


def test_one_lexer_per_language(app):
    from pilcrow_qt.widgets.panels.editor import lexer_for_path

    assert lexer_for_path("/a/b.py") is lexer_for_path("/c/d.py")
    assert lexer_for_path("/a/b.txt") is None


def test_a_keystroke_in_a_long_draft_reads_the_text_once_and_keeps_the_line_height(app):
    from pilcrow_qt.widgets.composer import Composer

    sid = "sess-draft"
    store.rt[sid] = Runtime(loaded=True, st={"status": "idle", "cwd": "/proj"})
    comp = Composer(sid, "/proj")
    comp.resize(800, 100)
    comp.show()
    pump(60)
    try:
        ed = comp.edit
        ed.setPlainText("\n".join(f"line {i} of a pasted log" for i in range(2000)))
        pump(60)
        doc = ed.document()
        blk = doc.firstBlock()
        kind = QTextBlockFormat.LineHeightTypes.LineDistanceHeight.value
        while blk.isValid():
            assert blk.blockFormat().lineHeightType() == kind
            blk = blk.next()
        width = doc.textWidth()
        calls = {"n": 0}
        orig = ed.toPlainText

        def counting() -> str:
            calls["n"] += 1
            return orig()

        ed.toPlainText = counting  # type: ignore[method-assign]
        t0 = time.perf_counter()
        QTest.keyClick(ed, Qt.Key_X)
        pump(10)
        ms = (time.perf_counter() - t0) * 1000
        ed.toPlainText = orig  # type: ignore[method-assign]
        assert calls["n"] <= 1, calls
        assert doc.textWidth() == width
        assert doc.lastBlock().blockFormat().lineHeightType() == kind
        assert ms < 60, ms
    finally:
        comp.close()
        store.rt.pop(sid, None)
