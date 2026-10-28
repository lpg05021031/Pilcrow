"""TodoWrite as a checklist (TodoBody in ToolCard.tsx) and the TaskCreate/TaskUpdate/TaskList tools."""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from .... import icons, theme
from ....lib.transcript_model import ToolItem
from ...ui import bits
from ...ui.signals import bind
from ..ctx import ToolCtx
from .common import Row, Section, note, pre, restyle, result_text, rich

Todo = Dict[str, Any]


class TodoBox(QWidget):
    """`.todo-box`: a ring for pending, a ring with a dot while in progress, a filled check when done."""

    def __init__(self, status: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.status = status
        self.setFixedSize(15, 15)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.75, 0.75, 13.5, 13.5)
        if self.status == "completed":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("fill_3"))
            p.drawRoundedRect(r, 4, 4)
            p.drawPixmap(2, 2, icons.pixmap("check", t.text_2, 11))
        elif self.status == "in_progress":
            p.setPen(QPen(t.color("text_2"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("text"))
            p.drawEllipse(QPointF(7.5, 7.5), 2.5, 2.5)
        else:
            p.setPen(QPen(t.color("hair_3"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 4, 4)


class TodoBody(Section):
    def __init__(self, todos: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(spacing=2, parent=parent)
        self.todos: List[Todo] = [t for t in todos if isinstance(t, dict)] if isinstance(todos, list) else []
        for t in self.todos:
            status = str(t.get("status") or "pending")
            row = Row(spacing=10, margins=(0, 3, 0, 3))
            row.lay.addWidget(TodoBox(status), 0, Qt.AlignmentFlag.AlignTop)
            text = str((t.get("activeForm") or t.get("content")) if status == "in_progress" else t.get("content") or "")
            lb = bits.label(text, wrap=True)
            if status == "completed":
                restyle(lb, lambda tk: f"QLabel {{ color: {tk.text_3}; text-decoration: line-through; }}")
            elif status == "in_progress":
                restyle(lb, lambda tk: f"QLabel {{ color: {tk.text}; font-weight: 500; }}")
            else:
                restyle(lb, lambda tk: f"QLabel {{ color: {tk.text_2}; }}")
            row.lay.addWidget(lb, 1, Qt.AlignmentFlag.AlignTop)
            self.add(row)


class TaskBody(Section):
    """TaskCreate / TaskUpdate / TaskList / TaskGet: the fields that matter, then what came back."""

    FIELDS = ("taskId", "subject", "status", "owner", "description", "activeForm", "blocks", "blockedBy", "metadata")

    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(spacing=4, parent=parent)
        i = item.input or {}
        for k in self.FIELDS:
            v = i.get(k)
            if v in (None, "", [], {}):
                continue
            text = v if isinstance(v, str) else ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
            if k == "status":
                text = text.replace("_", " ")
            self.add(
                rich(
                    f'<span style="color:{theme.tokens().text_3}">{k}</span>&nbsp; {html.escape(text)}',
                    size=theme.FS_SMALL,
                    color="text_2",
                )
            )
        r = item.result
        if r and not r.is_error and r.text:
            self.add(pre(r.text))
        elif r and r.is_error:
            self.add(result_text(item))
        elif item.status == "running":
            self.add(note("Working…"))
