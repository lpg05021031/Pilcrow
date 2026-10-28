"""The Files (tree + editor), Terminal and Preview panels: structure, highlighting, saving, the pyte
grid and its colours, key mapping, resizing, and the localhost addresses found in a Bash result."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from harness import ROOT, app_client, mock_server  # noqa: F401

MOCK_PORT = 5401

TREE: Dict[str, List[dict]] = {
    "/proj": [
        {"name": "src", "path": "/proj/src", "type": "dir", "size": 0},
        {"name": "README.md", "path": "/proj/README.md", "type": "file", "size": 120},
        {"name": ".env", "path": "/proj/.env", "type": "file", "size": 10},
    ],
    "/proj/src": [
        {"name": "main.py", "path": "/proj/src/main.py", "type": "file", "size": 300},
        {"name": "util.ts", "path": "/proj/src/util.ts", "type": "file", "size": 200},
    ],
}
PY = 'import os\n\ndef main(name: str) -> None:\n    """Say hello."""\n    for i in range(3):\n        print(f"hello {name} {i}")  # greet\n    return None\n'
MD = "# Title\n\nSome *markdown* text.\n"
HISTORY = {
    "src/main.py": [
        {
            "sessionId": "s1",
            "title": "Fix it",
            "about": None,
            "at": 1,
            "n": 1,
            "toolUseId": None,
            "kind": "edit",
            "add": 3,
            "del": 1,
        }
    ]
}


def fake_store_call(monkeypatch, store, write_result: Optional[dict] = None) -> List[Tuple[str, dict]]:
    """Replace ``store.call`` with one that answers fs.* from the fixtures; returns the call log."""
    from pilcrow_qt.rpc import Call

    calls: List[Tuple[str, dict]] = []

    def call(method: str, params: Optional[dict] = None, quiet: bool = False) -> Call:
        params = params or {}
        calls.append((method, params))
        c = Call(method)
        if method == "fs.list":
            c._resolve({"dir": params["dir"], "entries": TREE.get(params["dir"], [])})
        elif method == "fs.read":
            p = params["path"]
            c._resolve({"path": p, "size": 300, "mtime": 1234.0, "text": PY if p.endswith(".py") else MD})
        elif method == "fs.write":
            c._resolve(write_result or {"ok": True, "mtime": 5678.0})
        elif method == "project.fileHistory":
            c._resolve(HISTORY)
        else:
            c._resolve({})
        return c

    monkeypatch.setattr(store, "call", call)
    return calls


def _mount_in_panel(c, panel) -> None:
    """Show a panel where the side panel sits in the window, so the screenshot is the app's."""
    from PySide6.QtCore import Qt

    panel.setObjectName("panel")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    c.win.panel_layout.addWidget(panel)
    c.win.panel_host.setFixedWidth(620)
    c.win.panel_host.show()
    c.win.panel_line.show()
    c.sleep(50)


def _rows(panel) -> List[Tuple[str, str, int]]:
    return [(r.name, r.kind, r.depth) for r in panel.tree.rows()]


def test_tree_and_editor_highlighting(app_client, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.files import FilesPanel

    c = app_client
    calls = fake_store_call(monkeypatch, c.store)
    c.store.set_ui(panel=False)  # the window's own side panel stays out of this: the test mounts its panel
    c.open("sess-login")
    panel = FilesPanel("sess-login", "/proj")
    _mount_in_panel(c, panel)
    assert _rows(panel) == [("src", "dir", 0), ("README.md", "file", 0)]  # dotfiles hidden, folders first
    panel.hidden_btn.click()
    assert (".env", "file", 0) in _rows(panel)
    panel.hidden_btn.click()
    panel.tree.activate(0)  # expand src
    assert _rows(panel) == [("src", "dir", 0), ("main.py", "file", 1), ("util.ts", "file", 1), ("README.md", "file", 0)]
    assert [r.touches for r in panel.tree.rows() if r.name == "main.py"] == [1]  # changed in one earlier session
    assert ("fs.list", {"dir": "/proj/src"}) in calls

    panel.filter.setText("util")
    assert [r.name for r in panel.tree.rows()] == ["src", "util.ts"]
    panel.filter.setText("")

    panel.tree.activate(1)  # open main.py
    c.sleep(50)
    assert panel.active is not None and panel.active.path == "/proj/src/main.py"
    assert panel.path_lb.text() == "src/main.py"
    area = panel._areas["/proj/src/main.py"]
    assert area.editor.highlighter.lexer_name == "Python"
    t = theme.tokens()
    runs = {(s, f.foreground().color().name()) for s, _l, f in area.editor.line_formats(3)}
    assert (0, t.cm_keyword) in runs, runs  # "def" is a keyword
    assert (4, t.cm_function) in runs, runs  # "main" is a function name
    assert any(
        f.fontItalic() and f.foreground().color().name() == t.cm_comment for _s, _l, f in area.editor.line_formats(6)
    )
    assert panel.tabs.tab("/proj/src/main.py").on

    # Edit, then Ctrl+S writes the text with the mtime the file was read at.
    area.editor.textCursor().insertText("x = 1\n")
    assert panel.active.dirty and panel.save_btn.isEnabled()
    QTest.keyClick(area.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    c.sleep(50)
    writes = [p for m, p in calls if m == "fs.write"]
    assert writes == [{"path": "/proj/src/main.py", "text": "x = 1\n" + PY, "expectedMtime": 1234.0, "force": False}]
    assert not panel.active.dirty and panel.active.mtime == 5678.0
    assert any(t.text == "Saved main.py" for t in c.store.toasts)

    # Goto-line through store.file_open puts the caret on the line.
    c.store.file_open.emit({"path": "src/main.py", "line": 5, "nonce": 1})
    c.sleep(50)
    assert area.editor.textCursor().blockNumber() == 4
    assert c.shot("panel-files").exists()


def test_save_conflict_asks_before_overwriting(app_client, monkeypatch):
    from pilcrow_qt.store import ConfirmSpec
    from pilcrow_qt.widgets.panels.files import FilesPanel

    c = app_client
    calls = fake_store_call(monkeypatch, c.store, write_result={"ok": False, "conflict": True, "mtime": 9999.0})
    panel = FilesPanel("sess-login", "/proj-conflict")
    panel.open_tab("/proj-conflict/a.py")
    c.sleep(20)
    tab = panel.active
    tab.doc = tab.doc + "pass\n"
    panel.save(tab)
    spec = c.store.dialogs.get("confirm")
    assert (
        isinstance(spec, ConfirmSpec)
        and spec.title == "a.py changed on disk"
        and spec.confirm == "Overwrite"
        and spec.danger
    )
    spec.on_confirm(False)
    assert [p["force"] for m, p in calls if m == "fs.write"] == [False, True]
    c.store.set_dialog("confirm", None)


def test_markdown_opens_rendered_with_source_toggle(app_client, monkeypatch):
    from pilcrow_qt.widgets.panels.files import FilesPanel, doc_view

    c = app_client
    fake_store_call(monkeypatch, c.store)
    panel = FilesPanel("sess-login", "/proj-md")
    panel.resize(700, 500)
    panel.show()
    panel.open_tab("/proj-md/README.md")
    c.sleep(300)  # the page renders on a short debounce
    area = panel._areas["/proj-md/README.md"]
    assert doc_view(panel.active) == "preview"
    assert area.md is not None and area.md.isVisible() and not area.editor.isVisible()
    assert "Title" in area.md.toPlainText()
    assert panel.seg.isVisible()
    panel.seg.changed.emit("edit")
    assert area.editor.isVisible() and not area.md.isVisible()
    panel.seg.changed.emit("split")
    assert area.editor.isVisible() and area.md.isVisible() and not panel.st.show_tree
    panel.close()


def test_terminal_view_colours_keys_and_resize(app_client):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest

    from pilcrow_qt import theme
    from pilcrow_qt.widgets.panels.terminal import TerminalView

    c = app_client
    v = TerminalView(80, 24)
    v.resize(640, 400)
    v.show()
    c.sleep(50)
    v.feed(b"hello\x1b[31m red\x1b[0m\r\n")
    assert v.text() == "hello red"
    assert v.screen.buffer[0][6].fg == "red"
    t = theme.tokens()
    assert v.cell_colors(0, 6)[0].name() == QColor(t.ansi_red).name()
    assert v.cell_colors(0, 0)[0].name() == QColor(t.text).name()

    # The painted pixels of the red word are red; the white word's are not.
    img = v.grab().toImage()
    cw, ch = v.cell_size()

    def reddish(col: int) -> bool:
        x0, y0 = int(col * cw), 0
        for x in range(x0, int(x0 + cw)):
            for y in range(y0, y0 + ch):
                p = img.pixelColor(x, y)
                if p.red() > 150 and p.red() > p.green() + 50 and p.red() > p.blue() + 50:
                    return True
        return False

    assert reddish(6) and reddish(7) and not reddish(0)
    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    v.feed(b"\x1b[1;32mbold green\x1b[0m \x1b[44m blue bg \x1b[0m \x1b[38;5;208m256\x1b[0m \x1b[4munder\x1b[0m\r\n$ ")
    c.sleep(50)
    v.grab().save(str(out / "qt-terminal.png"))

    # Keys become VT input through the output callback.
    got: List[bytes] = []
    v.on_output = got.append
    v.setFocus()
    QTest.keyClick(v, Qt.Key.Key_Return)
    QTest.keyClick(v, Qt.Key.Key_Up)
    QTest.keyClick(v, Qt.Key.Key_Home)
    QTest.keyClick(v, Qt.Key.Key_F5)
    QTest.keyClick(v, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(v, Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier)
    QTest.keyClick(v, Qt.Key.Key_A)
    assert got == [b"\r", b"\x1b[A", b"\x1b[H", b"\x1b[15~", b"\x03", b"\x1bx", b"a"], got

    # Resizing the widget changes the grid (and reports it after the debounce).
    sizes: List[Tuple[int, int]] = []
    v.resized.connect(lambda cols, rows: sizes.append((cols, rows)))
    before = (v.cols, v.rows)
    v.resize(400, 200)
    c.sleep(50)
    assert (v.cols, v.rows) != before and v.cols == int(400 // cw) and v.rows == int(200 // ch)
    assert v.text().startswith("hello red")  # shrinking keeps the lines that fit
    c.wait_until(lambda: sizes, 3, "resize report")
    assert sizes[-1] == (v.cols, v.rows)

    # Scrolling into the history and selecting text.
    for i in range(40):
        v.feed(f"line {i}\r\n".encode())
    assert len(v.screen.history.top) > 0
    v.scroll_lines(5)
    assert v.verticalScrollBar().value() == len(v.screen.history.top) - 5
    v.scroll_lines(-5)
    v._sel = ((v._first_row(), 0), (v._first_row(), 3))
    assert v.selected_text() == v.screen.display[0][:4].rstrip()
    v.close()


def test_terminal_panel_without_pty_explains(app_client):
    from PySide6.QtWidgets import QLabel

    from pilcrow_qt.widgets.panels.terminal import TerminalPanel

    c = app_client
    assert c.store.info and c.store.info["terminal"]["pty"] is False  # the mock has no pty
    panel = TerminalPanel("sess-login", "/proj")
    panel.show()
    c.sleep(20)
    texts = [w.text() for w in panel.findChildren(QLabel) if w.isVisible()]
    assert "The terminal needs python3 (or util-linux `script`) on this machine." in texts
    assert not panel.host.isVisible()
    panel.close()


def test_terminal_panel_routes_events_to_views(app_client, monkeypatch):
    from pilcrow_qt.rpc import Call
    from pilcrow_qt.widgets.panels import terminal as term_mod
    from pilcrow_qt.widgets.panels.terminal import TerminalPanel

    c = app_client
    monkeypatch.setitem(c.store.info, "terminal", {"pty": True, "python": "/usr/bin/python3"})
    calls: List[Tuple[str, dict]] = []

    def call(method: str, params: Optional[dict] = None, quiet: bool = False) -> Call:
        calls.append((method, params or {}))
        k = Call(method)
        if method == "term.open":
            k._resolve({"id": "t-1", "title": "proj"})
        elif method == "term.snapshot":
            k._resolve({"id": "t-1", "buffer": "snap> "})
        else:
            k._resolve({})
        return k

    monkeypatch.setattr(c.store, "call", call)
    monkeypatch.setattr(term_mod, "_per_session", {})
    monkeypatch.setattr(term_mod, "_active_by_session", {})
    panel = TerminalPanel("sess-tests", "/proj")
    panel.resize(700, 400)
    panel.show()
    c.sleep(50)
    assert ("term.open", {"cwd": "/proj", "cols": 100, "rows": 30}) in calls
    view = panel.view("t-1")
    assert view is not None and view.text() == "snap>"
    c.rpc._emit("term.data", {"id": "t-1", "data": "\x1b[32mok\x1b[0m\r\n"})
    assert view.text().startswith("snap> ok")
    view.send(b"ls\r")
    assert ("term.input", {"id": "t-1", "data": "ls\r"}) in calls
    c.rpc._emit("term.exit", {"id": "t-1", "code": 0})
    assert "[process exited with code 0]" in view.text()
    assert panel.terms[0].exited and panel._tabs["t-1"].text == "proj (exited)"
    panel.close_term("t-1")
    assert ("term.close", {"id": "t-1"}) in calls and panel.terms == []
    panel.close()


def test_preview_finds_localhost_urls(app_client):
    from pilcrow_qt.lib.transcript_model import ToolItem, ToolResult
    from pilcrow_qt.widgets.panels.preview import PreviewPanel, find_urls

    c = app_client
    m = c.store.get_model("sess-preview")
    m.tools["t1"] = ToolItem(
        id="t1", name="Bash", result=ToolResult(text="  VITE v5 ready\n  ➜  Local:   http://127.0.0.1:5173/\n")
    )
    m.tools["t2"] = ToolItem(
        id="t2", name="Bash", structured={"stdout": "Listening on http://0.0.0.0:8080.", "stderr": ""}
    )
    m.tools["t3"] = ToolItem(id="t3", name="Read", result=ToolResult(text="http://localhost:9999/ignored"))
    assert find_urls("sess-preview") == ["http://localhost:8080", "http://127.0.0.1:5173/"]
    panel = PreviewPanel("sess-preview", "/proj")
    panel.resize(560, 420)
    panel.show()
    c.sleep(20)
    assert [ch.url for ch in panel._chips] == ["http://localhost:8080", "http://127.0.0.1:5173/"]
    assert panel.chips.isVisible()
    assert "Preview a local web app" in panel.title.text()
    panel._chips[1].click()
    assert panel.url == "http://127.0.0.1:5173/" and panel.field.text() == panel.url
    assert panel.title.text() == "http://127.0.0.1:5173/" and panel.browser_btn.isVisible()
    panel.go("localhost:3000")
    assert panel.url == "http://localhost:3000"
    panel.close()
