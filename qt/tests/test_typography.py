"""The conversation's typography, offscreen and without a server: a reply holding every case (a
wide table and a narrow one, nested and task lists, a fence with a line too long for the column,
Chinese with English and code, a thought, a Read, a command and an edit, all opened) is painted
through the real transcript widget; the geometry is checked and a screenshot kept in test/out."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QTextTable  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, theme  # noqa: E402
from pilcrow_qt.canvas.scene import Group  # noqa: E402
from pilcrow_qt.lib.transcript_model import TranscriptModel  # noqa: E402
from pilcrow_qt.store import Runtime, store  # noqa: E402

CWD = "/proj"
SID = "sess-typography"
_seq = 0


def uid() -> str:
    global _seq
    _seq += 1
    return f"ty-{_seq}"


def user(text: str) -> Dict[str, Any]:
    return {"type": "user", "uuid": uid(), "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid(),
        "message": {"id": f"m-{uid()}", "role": "assistant", "content": content},
    }


def tool_use(tid: str, name: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid: str, content: Any, structured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid(),
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]},
        "tool_use_result": structured,
    }


LONG_LINE = "      const end = readString(src, i + 1); // a comment that runs well past the width of the column, to see the wrap"
REPLY = f"""# Parser 模組整理

The parser lives in `src/parser.ts` and is used from one place, `src/lib/transcript_model.ts:412`.

| Function | Calls | Time (ms) |
|---|---:|---:|
| `tokens()` | 1 241 | 38.2 |
| `parse()` | 1 241 | 14.9 |

| 模組 | 用途 | 主要函式 | 測試覆蓋 | 備註 |
|---|---|---|---|---|
| `src/parser.ts` | 把 streaming 中的 JSON 片段解析成樹 | `parse()`, `tokens()` | 8 個測試，全部通過 | 熱路徑在 `tokens()`，每個字元都會配置一個新的陣列 |
| `src/lexer.ts` | 處理字串與跳脫字元 | `lex()` | 3 個測試 | 與 parser 共用 `Tok` 型別 |

```ts
export function tokens(src: string): Tok[] {{
  while (i < src.length) {{
{LONG_LINE}
  }}
}}
```

- [x] Read the parser
- [ ] Replace the per-character array

Nested:

- one
  - two, 中文的巢狀項目
    - three

> **Note.** 這也是為什麼 `repair()` 不能被移除。
"""

READ = "\n".join(f"line {i} of the file" for i in range(1, 15))


def build_model() -> TranscriptModel:
    m = TranscriptModel()
    m.push(user("請幫我整理一下 parser 模組。Also note where the time goes."))
    m.push(assistant([{"type": "thinking", "thinking": "Plan:\n\n1. Read `src/parser.ts`\n2. Run the tests"}]))
    m.push(assistant([{"type": "text", "text": "I'll read the parser first."}]))
    m.push(assistant([tool_use("ty-read", "Read", {"file_path": f"{CWD}/src/parser.ts"})]))
    m.push(
        tool_result(
            "ty-read",
            "ok",
            {
                "type": "text",
                "file": {
                    "filePath": f"{CWD}/src/parser.ts",
                    "content": READ,
                    "numLines": 14,
                    "startLine": 1,
                    "totalLines": 180,
                },
            },
        )
    )
    m.push(assistant([tool_use("ty-bash", "Bash", {"command": "npm test -- parser"})]))
    m.push(tool_result("ty-bash", "\x1b[32m✓\x1b[0m parser handles comments\n2 passed"))
    m.push(
        assistant(
            [
                tool_use(
                    "ty-edit", "Edit", {"file_path": f"{CWD}/src/parser.ts", "old_string": "a\n", "new_string": "b\n"}
                )
            ]
        )
    )
    m.push(tool_result("ty-edit", "ok"))
    m.push(assistant([{"type": "text", "text": REPLY}]))
    for t in m.tools.values():
        t.start_ts, t.end_ts = 0, 2300
    return m


def pump(ms: int) -> None:
    import time

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = True
    yield a


@pytest.fixture()
def transcript(app):
    from pilcrow_qt.canvas.transcript import Transcript, forget_scenes

    forget_scenes()
    store.models[SID] = build_model()
    store.rt[SID] = Runtime(loaded=True, st={"status": "idle", "cwd": CWD})
    win = QWidget()
    win.setObjectName("canvas")
    win.resize(1220, 2600)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    tr = Transcript(SID, CWD)
    lay.addWidget(tr)
    win.show()
    pump(250)
    for _ in range(4):  # open the folds, the thought, the Explored line and the cards
        opened = False
        for b in list(tr.scene.root.walk()):
            if isinstance(b, Group) and b.head_h and not b.open and b.KIND in ("fold", "thinking", "tool", "explore"):
                tr.view.toggle_group(b, True, animate=False)
                opened = True
        pump(40)
        if not opened:
            break
    pump(250)
    yield tr
    tr.close()
    win.close()
    store.models.pop(SID, None)
    store.rt.pop(SID, None)


def _blocks(tr, kind: str):
    return [b for b in tr.scene.root.walk() if b.KIND == kind]


def test_bodies_sit_under_their_verbs_and_inside_the_column(transcript):
    from pilcrow_qt.canvas.blocks.tool import ToolBlock

    tr = transcript
    v = tr.view
    for tool in [b for b in tr.scene.root.walk() if isinstance(b, ToolBlock)]:
        head = v.block_rect(tool)
        body = v.block_rect(tool.body)
        assert body.x() == head.x() + ToolBlock.CHILD_INDENT, tool.key
        assert body.right() == head.right(), tool.key
        assert tool.body.root is not None and tool.body.root.x == 0, "a node tree starts at its block's edge"
        assert tool.body.root.w == body.width()


def test_tables_fit_the_column_and_wells_hold_their_lines(transcript):
    tr = transcript
    v = tr.view
    for b in _blocks(tr, "prose"):
        if b.doc is None:
            continue
        r = v.block_rect(b)
        for frame in b.doc.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                width = frame.format().width().rawValue()
                assert width <= r.width(), f"a table wider than the column: {width} > {r.width()}"
        if b.src.startswith("```"):
            wells = b.wells()
            assert len(wells) == 1 and "readString" in wells[0][1]
            doc_lines = b.doc.toPlainText().count("\n")
            assert doc_lines >= 5, "one paragraph per code line"
            assert b.h > 30 + 5 * 20, "the long line wrapped onto a second line and the well grew"
    wide = next(b for b in _blocks(tr, "prose") if "測試覆蓋" in b.src)
    cells = [blk.text() for blk in _iter_blocks(wide.doc) if blk.text()]
    assert any("8 個測試" in c for c in cells)
    assert not any(c.strip() == "個" for c in cells), "no column squeezed to one character a line"


def _iter_blocks(doc):
    b = doc.firstBlock()
    while b.isValid():
        yield b
        b = b.next()


def test_thought_lists_and_boxes_and_a_screenshot(transcript):
    from pilcrow_qt.canvas.blocks.thinking import ThinkingBlock

    tr = transcript
    th = next(b for b in tr.scene.root.walk() if isinstance(b, ThinkingBlock))
    body = th.children[0]
    assert body.doc is not None and body._md  # noqa: SLF001
    assert "`" not in body.doc.toPlainText() and "Read src/parser.ts" in body.selected_text(None, None).replace(
        "\n", " "
    )
    tasks = next(b for b in _blocks(tr, "prose") if "[x] Read" in b.src)
    assert "[x]" not in tasks.doc.toPlainText() and "\ufffc" in tasks.doc.toPlainText(), (
        "boxes as images in the markers' place"
    )
    nested = next(b for b in _blocks(tr, "prose") if b.src.startswith("- one"))
    styles = {blk.textList().format().style() for blk in _iter_blocks(nested.doc) if blk.textList() is not None}
    assert len(styles) == 3, "disc, circle and square as the bullets nest"
    tr.view.verticalScrollBar().setValue(0)
    pump(100)
    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "qt-typography.png"
    tr.view.viewport().grab().save(str(path))
    assert path.exists()
