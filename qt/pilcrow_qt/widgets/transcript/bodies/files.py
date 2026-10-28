"""Read, Edit/MultiEdit, Write and NotebookEdit bodies (ReadBody/EditBody/WriteBody in ToolCard.tsx)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import QWidget

from ....lib.fmt import rel_path
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ....store import store
from ..ctx import ToolCtx
from .code import CodeView, DiffHunk, DiffView, compute_hunks, hunks_from_patch
from .common import LinkLabel, Row, Section, faint, note, result_text

READ_MAX_LINES = 40


def strip_line_numbers(text: str) -> Tuple[str, int]:
    """Claude Code numbers the lines it reads ("12→code"); the view numbers them itself."""
    lines = text.split("\n")
    numbered = [ln for ln in lines if re.match(r"^\s*\d+[\t→]", ln)]
    if len(numbered) < len(lines) * 0.6:
        return text, 1
    m = re.match(r"^\s*(\d+)", lines[0])
    return "\n".join(re.sub(r"^\s*\d+[\t→]", "", ln) for ln in lines), int(m.group(1)) if m else 1


class ReadBody(Section):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        s = item.structured if isinstance(item.structured, dict) else None
        r = item.result
        path = str_(item.input, "file_path")
        content, start = "", 1
        file = s.get("file") if s and isinstance(s.get("file"), dict) else None
        if file is not None and file.get("content") is not None:
            content = str(file.get("content") or "")
            start = int(file.get("startLine") or 1)
        elif r and r.text:
            cleaned = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>", "", r.text).rstrip()
            content, start = strip_line_numbers(cleaned)
        self.start = start
        if r and r.is_error:
            self.add(result_text(item))
            return
        if (s and s.get("type") == "image") or (r and r.images):
            self.add(result_text(item))
            return
        bar = Row(spacing=8)
        self.link = LinkLabel(rel_path(path, ctx.cwd), mono=True)
        self.link.clicked.connect(lambda: store.open_file(path, start))
        bar.add(self.link)
        bar.stretch()
        # Only worth saying when Claude read part of the file.
        total = file.get("totalLines") if file else None
        num = file.get("numLines") if file else None
        if isinstance(total, (int, float)) and isinstance(num, (int, float)) and num < total - 1:
            bar.add(faint(f"Lines {start}–{start + int(num) - 1} of {int(total)}", size="micro"))
        self.add(bar)
        if content:
            self.code = CodeView(content, path=path, start_line=start, max_lines=READ_MAX_LINES)
            self.add(self.code)
        else:
            self.add(faint("Waiting…"))


def edit_hunks(item: ToolItem) -> Tuple[List[DiffHunk], bool]:
    """The hunks of an edit and whether they carry real line numbers (from structuredPatch) or not."""
    i: Dict[str, Any] = item.input or {}
    s = item.structured if isinstance(item.structured, dict) else None
    patch = s.get("structuredPatch") if s else None
    if isinstance(patch, list) and patch:
        return hunks_from_patch(patch), True
    edits = i.get("edits")
    if isinstance(edits, list):
        out: List[DiffHunk] = []
        for e in edits:
            if isinstance(e, dict):
                out.extend(compute_hunks(str(e.get("old_string") or ""), str(e.get("new_string") or "")))
        return out, False
    if "old_string" in i or "new_string" in i:
        return compute_hunks(str(i.get("old_string") or ""), str(i.get("new_string") or "")), False
    return [], False


class EditBody(Section):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        i = item.input or {}
        path = str_(i, "file_path")
        self.diff: Optional[DiffView] = None
        # While the edit is still arriving, a diff against half a replacement would show phantom deletions.
        # Show the new text as it is written instead, and the real diff once the edit is complete.
        if item.status == "streaming":
            edits = i.get("edits") if isinstance(i.get("edits"), list) else None
            if isinstance(i.get("new_string"), str):
                draft = i["new_string"]
            elif edits:
                last = edits[-1] if isinstance(edits[-1], dict) else {}
                draft = str(last.get("new_string") or "")
            else:
                draft = ""
            self.add(faint("Writing the change…"))
            if draft:
                self.add(CodeView(draft, path=path, max_lines=16))
            return
        hunks, numbered = edit_hunks(item)
        if i.get("replace_all"):
            self.add(note("Replaced every occurrence"))
        if hunks:
            self.diff = DiffView(hunks, path=path, numbers=numbered, max_rows=16)
            self.add(self.diff)
        if item.result and item.result.is_error:
            self.add(result_text(item))


class WriteBody(Section):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        i = item.input or {}
        s = item.structured if isinstance(item.structured, dict) else None
        path = str_(i, "file_path")
        patch = s.get("structuredPatch") if s and s.get("type") == "update" else None
        if isinstance(patch, list) and patch:
            self.add(DiffView(hunks_from_patch(patch), path=path, max_rows=16))
        elif isinstance(i.get("content"), str):
            self.add(CodeView(i["content"], path=path, max_lines=16))
        if item.result and item.result.is_error:
            self.add(result_text(item))


class NotebookBody(Section):
    """NotebookEdit: which cell, what happens to it, and the new source."""

    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        i = item.input or {}
        mode = str_(i, "edit_mode") or "replace"
        cell = str_(i, "cell_id")
        kind = str_(i, "cell_type")
        words = {"replace": "Replace", "insert": "Insert", "delete": "Delete"}.get(mode, mode.capitalize())
        what = f"{words} {kind + ' ' if kind else ''}cell{(' ' + cell) if cell else ''}"
        self.add(note(what))
        src = i.get("new_source")
        if isinstance(src, str) and src and mode != "delete":
            self.add(CodeView(src, lang="markdown" if kind == "markdown" else "python", max_lines=16))
        self.add(result_text(item))
