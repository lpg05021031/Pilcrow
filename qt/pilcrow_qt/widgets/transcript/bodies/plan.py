"""ExitPlanMode: the plan for review with approve / keep-planning actions (a port of tools/PlanBody.tsx)."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QWidget

from .... import theme
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ....store import store
from ...ui import bits
from ...ui.signals import bind
from ..ctx import ToolCtx
from ..permission import typing_elsewhere
from .common import Badge, Hairline, IconLabel, Json, LinkLabel, Row, Surface, code_more, faint, restyle, vbox
from .generic import markdown_label


class PlanBody(QWidget):
    def __init__(
        self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.item, self.ctx, self.perm = item, ctx, perm
        s = item.structured if isinstance(item.structured, dict) else {}
        self.plan: str = (
            str_(perm.get("input") if perm else None, "plan") or str_(item.input, "plan") or str(s.get("plan") or "")
        )
        self.file: Optional[str] = s.get("filePath") if isinstance(s.get("filePath"), str) else None
        self.busy = False
        self.revising = False
        self.collapsed = perm is None and len(self.plan) > 1200
        self.live = perm is not None
        bind(theme.theme.changed, self, lambda _t: self.update())
        lay = vbox(self, margins=(1, 1, 1, 1), spacing=0)

        head = Row(spacing=10, margins=(20, 12, 16, 12))
        kicker = bits.label("Plan ready for review" if self.live else "Plan", size="small")
        restyle(
            kicker,
            lambda t: (
                f"QLabel {{ font-weight: 600; font-size: {theme.FS_SMALL}px; "
                f"color: {t.accent_text if self.live else t.text_3}; }}"
            ),
        )
        head.add(kicker)
        head.stretch()
        self.outcome: Optional[str] = None
        if not self.live and item.result:
            self.outcome = "kept" if item.status == "denied" or item.result.is_error else "approved"
        if self.outcome == "approved":
            b = Badge("Approved", "ok")
            head.add(b)
        elif self.outcome == "kept":
            head.add(Badge("Kept planning"))
        if self.file:
            link = Row(spacing=4)
            link.add(IconLabel("file-text", "text_2", 13))
            fl = LinkLabel("Plan file")
            fl.setToolTip("Open the plan file")
            fl.clicked.connect(lambda: store.open_file(self.file))
            link.add(fl)
            head.add(link)
        lay.addWidget(head)
        lay.addWidget(Hairline())

        text = QWidget()
        tl = vbox(text, margins=(22, 16, 22, 18), spacing=8)
        if self.plan:
            self.md = markdown_label(self.plan, ctx.cwd)
            tl.addWidget(self.md)
        else:
            tl.addWidget(faint("Claude is ready to leave plan mode."))
        self.text = text
        if self.collapsed:
            text.setMaximumHeight(260)
        lay.addWidget(text)
        if self.collapsed:
            more = code_more("Show the full plan", self._expand)
            more.setContentsMargins(22, 0, 0, 0)
            self.more = more
            lay.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)

        self._shortcuts: List[QShortcut] = []
        if self.live:
            lay.addWidget(Hairline())
            self.actions = Surface("fill_1", "", 0, margins=(16, 12, 16, 14), spacing=8)
            self.choices = Row(spacing=8)
            self.approve_btn = bits.button(
                "Approve and start", kind="primary", on_click=lambda: self.approve("acceptEdits")
            )
            self.approve_btn.setAccessibleName("Approve and start")
            self.ask_btn = bits.button(
                "Approve, ask before edits",
                on_click=lambda: self.approve("default"),
                tooltip="Approve, but ask before each edit",
            )
            self.ask_btn.setAccessibleName("Approve, ask before edits")
            self.keep_btn = bits.button("Keep planning", kind="ghost", on_click=self.start_revising)
            self.keep_btn.setAccessibleName("Keep planning")
            for b, k in ((self.approve_btn, "1"), (self.ask_btn, "2"), (self.keep_btn, "3")):
                self.choices.add(b)
                self.choices.add(_key(k))
            self.choices.stretch()
            self.actions.add(self.choices)
            self.feedback_box = QWidget()
            fl = vbox(self.feedback_box, spacing=8)
            self.feedback = QPlainTextEdit()
            self.feedback.setPlaceholderText("What should change in the plan?")
            self.feedback.setAccessibleName("What should change in the plan?")
            self.feedback.setFixedHeight(64)
            self.feedback.installEventFilter(self)
            fl.addWidget(self.feedback)
            frow = Row(spacing=8)
            frow.add(faint("Ctrl+Enter sends", size="micro"), 1)
            self.back_btn = bits.button("Back", kind="ghost", on_click=self.stop_revising)
            self.back_btn.setAccessibleName("Back")
            self.send_btn = bits.button("Send feedback", kind="primary", on_click=self.keep_planning)
            self.send_btn.setAccessibleName("Send feedback")
            frow.add(self.back_btn)
            frow.add(self.send_btn)
            fl.addWidget(frow)
            self.feedback_box.setVisible(False)
            self.actions.add(self.feedback_box)
            lay.addWidget(self.actions)
            # Same keys as a permission prompt: 1 approve and start, 2 approve but ask before edits, 3 keep planning.
            for key in ("1", "2", "3"):
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(lambda k=key: self._on_key(k))
                self._shortcuts.append(sc)

    def _expand(self) -> None:
        self.collapsed = False
        self.text.setMaximumHeight(16777215)
        self.more.setVisible(False)

    # ---- decisions
    def _set_busy(self, on: bool) -> None:
        self.busy = on
        for b in (self.approve_btn, self.ask_btn, self.keep_btn, self.back_btn, self.send_btn):
            b.setEnabled(not on)

    def decide(self, d: Json) -> None:
        if self.busy or self.perm is None:
            return
        self._set_busy(True)
        store.respond(self.ctx.session_id, str(self.perm.get("requestId")), d).catch(lambda _e: self._set_busy(False))

    def approve(self, mode: str) -> None:
        assert self.perm is not None
        self.decide(
            {
                "behavior": "allow",
                "updatedInput": self.perm.get("input"),
                "updatedPermissions": [{"type": "setMode", "mode": mode, "destination": "session"}],
            }
        )

    def keep_planning(self) -> None:
        text = self.feedback.toPlainText().strip()
        self.decide(
            {
                "behavior": "deny",
                "message": (
                    f"The user wants changes to the plan: {text}"
                    if text
                    else "The user wants to keep planning. Ask what to change."
                ),
            }
        )

    def start_revising(self) -> None:
        self.revising = True
        self.choices.setVisible(False)
        self.feedback_box.setVisible(True)
        self.feedback.setFocus()

    def stop_revising(self) -> None:
        self.revising = False
        self.feedback_box.setVisible(False)
        self.choices.setVisible(True)

    def _on_key(self, key: str) -> None:
        if self.perm is None or self.revising or self.busy or typing_elsewhere(QApplication.focusWidget()):
            return
        if key == "1":
            self.approve("acceptEdits")
        elif key == "2":
            self.approve("default")
        elif key == "3":
            self.start_revising()

    def eventFilter(self, obj: Any, e: Any) -> bool:  # noqa: N802
        if obj is getattr(self, "feedback", None) and e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.keep_planning()
                return True
        return super().eventFilter(obj, e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.color("accent_ring" if self.live else "hair_2"), 1.5 if self.live else 1))
        p.setBrush(t.color("raised"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)


def _key(k: str) -> QWidget:
    lb = bits.label(k, size="micro")
    restyle(lb, lambda t: f"QLabel {{ color: {t.text_4}; font-size: 10.5px; padding: 0 4px; }}")
    return lb
