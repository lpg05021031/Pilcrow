"""Grep / Glob: the matches as a file list (SearchBody in ToolCard.tsx)."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import QWidget

from ....lib.fmt import plural, rel_path, truncate
from ....lib.transcript_model import ToolItem
from ....store import store
from ..ctx import ToolCtx
from .common import LinkLabel, Section, faint, pre, result_text


class SearchBody(Section):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(spacing=1, parent=parent)
        s = item.structured if isinstance(item.structured, dict) else None
        r = item.result
        self.files: List[str] = []
        if r and r.is_error:
            self.add(result_text(item))
            return
        files = s.get("filenames") if s else None
        self.files = [str(f) for f in files] if isinstance(files, list) else []
        content = s.get("content") if s else None
        if isinstance(content, str) and content:
            self.add(pre(truncate(content, 40000)))
            return
        if not self.files:
            if r and r.text:
                self.add(pre(truncate(r.text, 40000)))
            else:
                self.add(faint("No matches" if r else "Searching…"))
            return
        n = s.get("numFiles") if s else None
        count = int(n) if isinstance(n, (int, float)) else len(self.files)
        self.add(faint(plural(count, "file") + (" (truncated)" if s and s.get("truncated") else "")))
        for f in self.files[:200]:
            full = f if f.startswith("/") or (len(f) > 1 and f[1] == ":") else f"{ctx.cwd}/{f}"
            link = LinkLabel(rel_path(f, ctx.cwd), mono=True)
            link.clicked.connect(lambda p=full: store.open_file(p))
            self.add(link)
