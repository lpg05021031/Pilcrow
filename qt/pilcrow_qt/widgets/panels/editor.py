"""The code editor (a port of web/src/components/panels/Editor.tsx).

CodeMirror in the web interface; here a QPlainTextEdit with a line-number gutter, a tint on the
current line, two-space indentation, and syntax colours from Pygments (lexer picked by
``lib.fmt.lang_from_path``) painted with the theme's ``cm_*`` tokens. The whole document is lexed at
once so multi-line strings and comments colour correctly; each block then takes its runs from that
cache, and a change that re-colours other lines (an opened docstring) re-paints them on the next
tick. Ctrl+S emits ``save_requested``; the owner saves.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from ... import theme
from ...lib.fmt import lang_from_path
from ..ui.signals import bind

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Comment, Error, Generic, Keyword, Name, Number, Operator, Punctuation, String
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover - Pygments is in requirements.txt
    lex = None  # type: ignore[assignment]

# lang_from_path names that Pygments spells differently.
_LEXER_ALIAS: Dict[str, str] = {
    "jsonc": "json",
    "vue": "html",
    "svelte": "html",
    "system-verilog": "systemverilog",
    "proto": "protobuf",
    "hcl": "terraform",
    "fortran-free-form": "fortran",
    "fortran-fixed-form": "fortranfixed",
    "asm": "nasm",
    "csv": "text",
    "latex": "tex",
    "log": "text",
    "fish": "fish",
    "make": "make",
}
# Past this many characters the file is lexed line by line (stateless) to keep typing responsive.
WHOLE_DOC_LIMIT = 300_000
INDENT = "  "

Run = Tuple[int, int, object]  # (column, length, token type)


def lexer_for_path(path: str):
    """The Pygments lexer for a path, or None when the language is unknown (plain text)."""
    if lex is None:
        return None
    lang = lang_from_path(path)
    name = _LEXER_ALIAS.get(lang, lang)
    if name == "text":
        return None
    try:
        return get_lexer_by_name(name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


def _format_for(ttype, t: theme.Tokens) -> Optional[QTextCharFormat]:
    """The text format for a token type: the HighlightStyle in Editor.tsx, in theme tokens."""
    f = QTextCharFormat()
    if ttype in Comment:
        f.setForeground(QColor(t.cm_comment))
        f.setFontItalic(True)
    elif ttype in Keyword or ttype in Operator.Word:
        f.setForeground(QColor(t.cm_keyword))
    elif ttype in String.Regex:
        f.setForeground(QColor(t.cm_regex))
    elif ttype in String.Escape:
        f.setForeground(QColor(t.cm_regex))
    elif ttype in String:
        f.setForeground(QColor(t.cm_string))
    elif ttype in Number:
        f.setForeground(QColor(t.cm_number))
    elif ttype in Name.Function:
        f.setForeground(QColor(t.cm_function))
    elif ttype in Name.Class or ttype in Name.Builtin or ttype in Name.Exception or ttype in Name.Namespace:
        f.setForeground(QColor(t.cm_type))
    elif ttype in Name.Tag:
        f.setForeground(QColor(t.cm_tag))
    elif ttype in Name.Attribute or ttype in Name.Property or ttype in Name.Variable or ttype in Name.Decorator:
        f.setForeground(QColor(t.cm_property))
    elif ttype in Generic.Heading or ttype in Generic.Subheading:
        f.setForeground(QColor(t.cm_function))
        f.setFontWeight(QFont.Weight.DemiBold)
    elif ttype in Generic.Strong:
        f.setFontWeight(QFont.Weight.DemiBold)
    elif ttype in Generic.Emph:
        f.setFontItalic(True)
    elif ttype in Generic.Inserted:
        f.setForeground(QColor(t.ok))
    elif ttype in Generic.Deleted:
        f.setForeground(QColor(t.err))
    elif ttype in Error:
        f.setForeground(QColor(t.err))
    elif ttype in Punctuation or ttype in Operator:
        f.setForeground(QColor(t.cm_punct))
    else:
        return None
    return f


class PygmentsHighlighter(QSyntaxHighlighter):
    """Colours a document with Pygments; ``set_path`` swaps the lexer."""

    def __init__(self, doc: QTextDocument, path: str = "") -> None:
        super().__init__(None)
        self._lexer = lexer_for_path(path)
        self._lines: List[List[Run]] = []
        self._stale = True
        self._formats: Dict[object, Optional[QTextCharFormat]] = {}
        self._fixup = QTimer(self)
        self._fixup.setSingleShot(True)
        self._fixup.setInterval(0)
        self._pending: set = set()
        self._fixup.timeout.connect(self._rehighlight_pending)
        # Connected before setDocument so this runs before Qt's own contentsChange slot re-highlights.
        doc.contentsChange.connect(self._on_contents_change)
        self.setDocument(doc)
        bind(theme.theme.changed, self, lambda _t: self._on_theme())

    @property
    def lexer_name(self) -> str:
        return self._lexer.name if self._lexer is not None else ""

    def set_path(self, path: str) -> None:
        self._lexer = lexer_for_path(path)
        self._stale = True
        self.rehighlight()

    def _on_theme(self) -> None:
        self._formats.clear()
        self.rehighlight()

    def _on_contents_change(self, _pos: int, _removed: int, _added: int) -> None:
        self._stale = True

    def _format(self, ttype) -> Optional[QTextCharFormat]:
        if ttype not in self._formats:
            self._formats[ttype] = _format_for(ttype, theme.tokens())
        return self._formats[ttype]

    def _relex(self) -> None:
        """Lex the whole document into per-line runs; note which lines changed colour."""
        doc = self.document()
        old = self._lines
        text = doc.toPlainText()
        if self._lexer is None:
            self._lines = []
        elif len(text) > WHOLE_DOC_LIMIT:
            self._lines = [self._lex_line(line) for line in text.split("\n")]
        else:
            self._lines = self._lex_text(text)
        self._stale = False
        n = max(len(old), len(self._lines))
        for i in range(n):
            a = old[i] if i < len(old) else None
            b = self._lines[i] if i < len(self._lines) else None
            if a != b:
                self._pending.add(i)

    def _lex_line(self, line: str) -> List[Run]:
        runs: List[Run] = []
        col = 0
        for ttype, value in lex(line, self._lexer):
            if value:
                runs.append((col, len(value), ttype))
                col += len(value)
        return runs

    def _lex_text(self, text: str) -> List[List[Run]]:
        lines: List[List[Run]] = [[]]
        col = 0
        for ttype, value in lex(text, self._lexer):
            parts = value.split("\n")
            for i, part in enumerate(parts):
                if i > 0:
                    lines.append([])
                    col = 0
                if part:
                    lines[-1].append((col, len(part), ttype))
                    col += len(part)
        return lines

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if self._lexer is None:
            return
        if self._stale:
            self._relex()
        n = self.currentBlock().blockNumber()
        self._pending.discard(n)
        if self._pending and not self._fixup.isActive():
            self._fixup.start()
        if n >= len(self._lines):
            return
        for col, length, ttype in self._lines[n]:
            f = self._format(ttype)
            if f is not None:
                self.setFormat(col, length, f)

    def _rehighlight_pending(self) -> None:
        doc = self.document()
        pending, self._pending = self._pending, set()
        for n in sorted(pending):
            block = doc.findBlockByNumber(n)
            if block.isValid():
                self.rehighlightBlock(block)


class LineNumberArea(QWidget):
    """The gutter: line numbers in the faint text colour, the current line's in the mid colour."""

    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, e) -> None:  # noqa: N802
        self._editor.paint_gutter(e)


class CodeEditor(QPlainTextEdit):
    """A source editor for one file. ``save_requested`` fires on Ctrl+S."""

    save_requested = Signal()

    def __init__(self, path: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._path = path
        self.setObjectName("code-editor")
        self.setFont(theme.font_mono())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setCursorWidth(2)
        self.setAccessibleName("Editor")
        self.gutter = LineNumberArea(self)
        self.highlighter = PygmentsHighlighter(self.document(), path)
        self.blockCountChanged.connect(lambda _n: self._update_gutter_width())
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._tint_current_line)
        self._update_gutter_width()
        self._tint_current_line()
        self._apply_theme()
        bind(theme.theme.changed, self, lambda _t: self._apply_theme())

    # ---------------------------------------------------------------- content
    @property
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path
        self.highlighter.set_path(path)

    def set_text(self, text: str) -> None:
        """Replace the document (an external reload); the caret stays where it was when it can."""
        if self.toPlainText() == text:
            return
        cur = self.textCursor()
        pos = cur.position()
        self.setPlainText(text)
        cur = self.textCursor()
        cur.setPosition(min(pos, len(text)))
        self.setTextCursor(cur)
        self.highlighter.rehighlight()

    def goto_line(self, line: int) -> None:
        """Put the caret on a 1-based line, centred in view, and take focus."""
        doc = self.document()
        ln = min(max(1, line), doc.blockCount())
        block = doc.findBlockByNumber(ln - 1)
        cur = QTextCursor(block)
        self.setTextCursor(cur)
        self.centerCursor()
        self.setFocus()

    def line_formats(self, line: int) -> List[Tuple[int, int, QTextCharFormat]]:
        """The highlight runs on a 1-based line as (start, length, format): what the test inspects."""
        block = self.document().findBlockByNumber(line - 1)
        out = []
        if block.isValid():
            for fr in block.layout().formats():
                out.append((fr.start, fr.length, QTextCharFormat(fr.format)))  # copied: the ranges are temporaries
        return out

    # ---------------------------------------------------------------- keys
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        key, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_S:
            self.save_requested.emit()
            e.accept()
            return
        if key == Qt.Key.Key_Tab and not ctrl:
            cur = self.textCursor()
            if cur.hasSelection():
                self._indent_selection(cur, +1)
            else:
                cur.insertText(INDENT)
            e.accept()
            return
        if key == Qt.Key.Key_Backtab:
            self._indent_selection(self.textCursor(), -1)
            e.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            cur = self.textCursor()
            line = cur.block().text()[: cur.positionInBlock()]
            lead = line[: len(line) - len(line.lstrip(" \t"))]
            cur.insertText("\n" + lead)
            e.accept()
            return
        super().keyPressEvent(e)

    def _indent_selection(self, cur: QTextCursor, direction: int) -> None:
        doc = self.document()
        start, end = sorted((cur.anchor(), cur.position()))
        first = doc.findBlock(start)
        last = doc.findBlock(max(start, end - 1))
        cur.beginEditBlock()
        block = first
        while block.isValid() and block.blockNumber() <= last.blockNumber():
            c = QTextCursor(block)
            if direction > 0:
                c.insertText(INDENT)
            else:
                text = block.text()
                n = len(text) - len(text.lstrip(" "))
                c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, min(n, len(INDENT)))
                c.removeSelectedText()
            block = block.next()
        cur.endEditBlock()

    # ---------------------------------------------------------------- gutter and tint
    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, e) -> None:
        t = theme.tokens()
        p = QPainter(self.gutter)
        p.fillRect(e.rect(), theme.qcolor(t.chrome))
        p.setPen(theme.blend(t.hair, t.chrome))
        p.drawLine(self.gutter.width() - 1, e.rect().top(), self.gutter.width() - 1, e.rect().bottom())
        p.setFont(self.font())
        fm = self.fontMetrics()
        cur_line = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        w = self.gutter.width() - 8
        while block.isValid() and top <= e.rect().bottom():
            if block.isVisible() and bottom >= e.rect().top():
                n = block.blockNumber()
                if n == cur_line:
                    p.fillRect(0, top, self.gutter.width() - 1, bottom - top, theme.qcolor(t.raised_2))
                p.setPen(theme.qcolor(t.text_2 if n == cur_line else t.text_3))
                p.drawText(0, top, w, fm.height(), Qt.AlignmentFlag.AlignRight, str(n + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def _tint_current_line(self) -> None:
        t = theme.tokens()
        sel = QTextEdit.ExtraSelection()
        c = theme.qcolor(t.raised_2)
        c.setAlphaF(0.7)
        sel.format.setBackground(c)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])
        self.gutter.update()

    def _apply_theme(self) -> None:
        t = theme.tokens()
        self.setFont(theme.font_mono())
        # The font goes in the stylesheet too: the app stylesheet's ``*`` rule would override setFont.
        mono = theme._MONO_FAMILY or "monospace"
        self.setStyleSheet(
            f"QPlainTextEdit#code-editor {{ background: {t.canvas}; color: {t.text}; border: none; padding: 0; "
            f'font-family: "{mono}"; font-size: {theme.MONO_SIZE}px; '
            f"selection-background-color: {theme.blend(t.selection, t.canvas).name()}; selection-color: {t.text}; }}"
        )
        self._tint_current_line()
        self._update_gutter_width()


if __name__ == "__main__":  # pragma: no cover - development window
    import sys

    from PySide6.QtWidgets import QApplication

    from ... import fonts

    app = QApplication(sys.argv)
    fonts.load()
    theme.theme.apply(True)
    ed = CodeEditor(sys.argv[1] if len(sys.argv) > 1 else "demo.py")
    ed.setPlainText(
        open(sys.argv[1], encoding="utf-8").read()
        if len(sys.argv) > 1
        else 'def hello(name: str) -> str:\n    """Say hi."""\n    return f"hi {name}"  # comment\n'
    )
    ed.resize(720, 520)
    ed.show()
    sys.exit(app.exec())
