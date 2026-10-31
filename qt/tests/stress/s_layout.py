"""Things that break the layout rather than the frame rate: inline code that cannot wrap, a word
longer than the column, an image wider than it, a 20-column table, tabs in code, carriage returns
in output, emoji, a table with an empty cell, nested quotes with a fence."""

from __future__ import annotations

import base64
import os
import sys

from PySide6.QtGui import QImage, QTextTable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: E402

from core import app

from pilcrow_qt.canvas.blocks.prose import ProseBlock
from pilcrow_qt.canvas.paint import table_rect
from pilcrow_qt.canvas.view import measurer


def overflow(doc) -> float:
    """How far the widest line (or table) reaches past the document's width, in px."""
    w = doc.textWidth()
    worst = 0.0
    b = doc.firstBlock()
    lay = doc.documentLayout()
    while b.isValid():
        tl = b.layout()
        br = lay.blockBoundingRect(b)
        for i in range(tl.lineCount()):
            ln = tl.lineAt(i)
            right = br.left() + ln.x() + ln.naturalTextWidth()
            worst = max(worst, right - w)
        b = b.next()
    for fr in doc.rootFrame().childFrames():
        if isinstance(fr, QTextTable):
            # the layout's frame rectangle starts at the first cell's content: the painted rectangle
            r = table_rect(doc, lay, fr)
            worst = max(worst, r.right() - w)
    return worst


def case(name: str, src: str, width: int = 600) -> str:
    b = ProseBlock("p", src)
    h = b.measure(width, measurer())
    ov = overflow(b.doc)
    return f"{name:28s} width={width} h={h:5d} overflow={ov:6.1f}px {'<-- CLIPPED' if ov > 1 else ''}"


def png(w: int, h: int) -> str:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()


if __name__ == "__main__":
    app()
    long_code = (
        "`"
        + "/home/user/projects/pilcrow/qt/pilcrow_qt/widgets/transcript/bodies/very_long_module_name_" * 3
        + "file.py:1234`"
    )
    print(case("long inline code span", "See " + long_code + " for details."))
    print(case("long inline code in cell", "| a | b |\n|---|---|\n| x | " + long_code + " |"))
    print(case("200-char word", "word " + "x" * 200 + " word"))
    print(case("200-char URL no slashes", "https://example.com/" + "a" * 200))
    print(case("CJK 200 chars no breaks", "中" * 200))
    print(case("image 2000px wide", f"![big]({png(2000, 60)})"))
    print(
        case(
            "20-column table",
            "| "
            + " | ".join(f"col{i}" for i in range(20))
            + " |\n|"
            + "---|" * 20
            + "\n| "
            + " | ".join(f"value {i}" for i in range(20))
            + " |",
        )
    )
    print(case("fence with tabs", "```go\nfunc main() {\n\tfor i := 0; i < 3; i++ {\n\t\tfmt.Println(i)\n\t}\n}\n```"))
    print(case("fence 400-char line", "```\n" + "x" * 400 + "\n```"))
    print(case("emoji line", "Done ✅ 🎉 with 👍🏽 and 🇹🇼 flags and ZWJ 👨‍👩‍👧"))
    print(case("heading very long", "# " + "Heading words " * 30))
    print(case("quote with fence", "> quoted\n>\n> ```py\n> x = 1\n> ```\n>\n> after"))
    print(case("list with table inside", "- item\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n\n- next"))
    print(case("empty cells", "| a |  | c |\n|---|---|---|\n|  | 2 |  |"))
    print(case("html entities", "AT&amp;T &lt;tag&gt; &copy; &#x1F600; &nbsp;x"))
    print(case("hr spam", "---\n\n---\n\n---"))
    print(case("link 300 chars text", "[" + "link text " * 30 + "](https://e.com)"))
    # tabs: where does the text after a tab land vs the hanging indent the fence computed?
    b = ProseBlock("p", "```go\n\tfor i := 0; i < 3; i++ {\n```")
    b.measure(600, measurer())
    blk = b.doc.firstBlock()
    while blk.isValid() and "for" not in blk.text():
        blk = blk.next()
    ln = blk.layout().lineAt(0)
    x_after_tab = ln.cursorToX(1)[0]
    print(
        f"tab stop: text after one tab at x={x_after_tab:.1f}px; hanging indent margin={blk.blockFormat().leftMargin():.1f}px (4 cols would be {4 * 7.5:.0f}px)"
    )
    # carriage returns (a progress bar) in a Bash well
    from pilcrow_qt.canvas.blocks.bodies import bash_body
    from pilcrow_qt.lib.transcript_model import TranscriptModel
    from pilcrow_qt.widgets.transcript.ctx import ToolCtx

    m = TranscriptModel()
    from core import assistant, tool_result, tool_use

    m.push(assistant([tool_use("cr", "Bash", {"command": "npm install"})]))
    m.push(
        tool_result(
            "cr", "".join(f"\rprogress {i}%" for i in range(0, 101, 10)) + "\ndone\n" + "\x1b[2K\x1b[1Aline\n" * 3
        )
    )
    from pilcrow_qt.canvas.blocks.bodies import ToolBodyBlock

    blk = ToolBodyBlock(m.tools["cr"], ToolCtx("s", "/proj", {}))
    h = blk.measure(600, measurer())
    print(f"bash output with \\r progress + CSI erase: body h={h} text={blk.text()[:120]!r}")
