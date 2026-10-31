"""AskUserQuestion: Claude's questions with their options (a port of tools/AskBody.tsx)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QWidget

from .... import icons, theme
from ....lib.transcript_model import ToolItem
from ....store import store
from ...ui import bits
from ...ui.signals import bind
from ..ctx import ToolCtx
from .common import Badge, Hairline, IconLabel, Json, Row, Surface, faint, markdown_label, restyle, vbox


def extract_answer(text: str, q: str) -> str:
    i = text.find(f'"{q}"="')
    if i < 0:
        return "Answered"
    rest = text[i + len(q) + 4 :]
    return rest[: rest.find('"')] if '"' in rest else rest


class Mark(QWidget):
    """`.ask-mark`: a radio ring or a checkbox square, filled with a check when chosen."""

    def __init__(self, box: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.box = box
        self.on = False
        self.setFixedSize(16, 16)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_on(self, on: bool) -> None:
        self.on = on
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.75, 0.75, 14.5, 14.5)
        radius = 4 if self.box else 8
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("text"))
            p.drawRoundedRect(r, radius, radius)
            p.drawPixmap(2, 2, icons.pixmap("check", t.canvas, 11, stroke=3.0))
        else:
            p.setPen(QPen(t.color("hair_3"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)


class OptionRow(QWidget):
    """`.ask-opt`: one choice; a click toggles it, hovering shows its preview."""

    toggled = Signal()
    hovered = Signal()

    def __init__(self, label: str, description: str, multi: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.label = label
        self.on = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(label)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        row = Row(spacing=12, margins=(12, 10, 12, 10))
        lay = vbox(self)
        lay.addWidget(row)
        self.mark = Mark(multi)
        row.lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QWidget()
        tl = vbox(text, spacing=1)
        name = bits.label(label, wrap=True)
        restyle(name, lambda t: f"QLabel {{ font-weight: 500; color: {t.text}; }}")
        tl.addWidget(name)
        if description:
            tl.addWidget(bits.label(description, tone="faint", size="small", wrap=True))
        row.lay.addWidget(text, 1)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_on(self, on: bool) -> None:
        self.on = on
        self.mark.set_on(on)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.hovered.emit()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        self.hovered.emit()
        super().focusInEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggled.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        if self.on or self._hover:
            p = QPainter(self)
            p.fillRect(self.rect(), t.color("fill_2" if self.on else "fill_1"))


class AskBody(QWidget):
    """The live form while Claude waits, or the quiet record of what was asked and answered."""

    def __init__(
        self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.item, self.ctx, self.perm = item, ctx, perm
        raw = (perm.get("input") or {}).get("questions") if perm else None
        if raw is None:
            raw = (item.input or {}).get("questions")
        self.questions: List[Json] = [q for q in raw if isinstance(q, dict)] if isinstance(raw, list) else []
        self.sel: Dict[int, List[str]] = {}
        self.other: Dict[int, str] = {}
        self.notes: Dict[int, str] = {}
        self.busy = False
        self.rows: Dict[int, List[OptionRow]] = {}
        self.other_fields: Dict[int, QLineEdit] = {}
        self.note_fields: Dict[int, QLineEdit] = {}
        self.live = perm is not None
        bind(theme.theme.changed, self, lambda _t: self.update())
        lay = vbox(self, margins=(16, 14, 16, 14) if self.live else (16, 12, 16, 12), spacing=0)
        if not self.live:
            self._build_done(lay)
        else:
            self._build_live(lay)

    # ---- done
    def _build_done(self, lay) -> None:
        item = self.item
        answered: Dict[str, str] = {}
        if isinstance(item.structured, dict) and isinstance(item.structured.get("answers"), dict):
            answered = {str(k): str(v) for k, v in item.structured["answers"].items()}
        result_text = item.result.text if item.result else ""
        head = Row(spacing=8, margins=(0, 0, 0, 8))
        title = bits.label(
            "Claude asked a few questions" if len(self.questions) > 1 else "Claude asked a question", size="small"
        )
        restyle(title, lambda t: f"QLabel {{ font-weight: 600; color: {t.text_2}; font-size: {theme.FS_SMALL}px; }}")
        head.add(title)
        if item.status == "denied":
            head.add(Badge("Skipped"))
        head.stretch()
        lay.addWidget(head)
        self.answers: List[str] = []
        for k, q in enumerate(self.questions):
            qt = str(q.get("question") or "")
            block = QWidget()
            bl = vbox(block, margins=(0, 8 if k else 0, 0, 0), spacing=2)
            ql = bits.label(qt, tone="muted", wrap=True)
            restyle(ql, lambda t: f"QLabel {{ font-weight: 500; color: {t.text_2}; }}")
            bl.addWidget(ql)
            ans = Row(spacing=6)
            if answered.get(qt):
                ans.add(IconLabel("check", "ok", 13))
                ans.add(bits.label(answered[qt], wrap=True), 1)
                self.answers.append(answered[qt])
            else:
                if item.status in ("running", "streaming"):
                    text = "Waiting…"
                elif qt and qt in result_text:
                    text = extract_answer(result_text, qt)
                else:
                    text = "No answer"
                ans.add(faint(text, size="", wrap=True), 1)
                self.answers.append(text)
            bl.addWidget(ans)
            lay.addWidget(block)

    # ---- live
    def _build_live(self, lay) -> None:
        head = Row(spacing=8, margins=(0, 0, 0, 12))
        n = len(self.questions)
        title = bits.label(f"Claude has {n} questions" if n > 1 else "Claude has a question", size="small")
        restyle(
            title, lambda t: f"QLabel {{ font-weight: 600; color: {t.accent_text}; font-size: {theme.FS_SMALL}px; }}"
        )
        head.add(title)
        head.stretch()
        lay.addWidget(head)
        body = Row(spacing=16)
        body.lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        qs = QWidget()
        ql = vbox(qs, spacing=18)
        for qi, q in enumerate(self.questions):
            ql.addWidget(self._question(qi, q))
        body.lay.addWidget(qs, 1, Qt.AlignmentFlag.AlignTop)
        self.preview = Surface("inset", "hair", theme.R3, margins=(12, 10, 12, 10))
        self.preview.setVisible(False)
        host = QWidget()
        self.preview_host = vbox(host)
        self.preview.add(host)
        body.lay.addWidget(self.preview, 1, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(body)
        actions = Row(spacing=8, margins=(0, 16, 0, 0))
        self.send_btn = bits.button("Send answers" if n > 1 else "Send answer", kind="primary", on_click=self.submit)
        self.send_btn.setAccessibleName(self.send_btn.text())
        self.skip_btn = bits.button("Skip", kind="ghost", on_click=self.skip)
        self.skip_btn.setAccessibleName("Skip")
        actions.add(self.send_btn)
        actions.add(self.skip_btn)
        actions.stretch()
        lay.addWidget(actions)
        self._update_complete()

    def _question(self, qi: int, q: Json) -> QWidget:
        multi = bool(q.get("multiSelect"))
        box = QWidget()
        bl = vbox(box, spacing=9)
        line = Row(spacing=8)
        if q.get("header"):
            tag = bits.label(str(q["header"]), size="micro")
            restyle(tag, lambda t: f"QLabel {{ font-weight: 600; color: {t.text_3}; font-size: {theme.FS_MICRO}px; }}")
            line.add(tag)
        qt = bits.label(str(q.get("question") or ""), wrap=True)
        restyle(qt, lambda t: f"QLabel {{ font-size: {theme.FS_BODY}px; font-weight: 600; color: {t.text}; }}")
        line.add(qt, 1)
        if multi:
            line.add(faint("Choose any"))
        bl.addWidget(line)
        options = Surface("fill_1", "hair", theme.R3, margins=(0, 0, 0, 0), spacing=0)
        self.rows[qi] = []
        for oi, o in enumerate(q.get("options") or []):
            if not isinstance(o, dict):
                continue
            if oi:
                options.add(Hairline())
            row = OptionRow(str(o.get("label") or ""), str(o.get("description") or ""), multi)
            row.toggled.connect(lambda qi=qi, label=row.label, multi=multi: self.toggle(qi, label, multi))
            row.hovered.connect(lambda qi=qi, oi=oi: self._preview(qi, oi))
            options.add(row)
            self.rows[qi].append(row)
        options.add(Hairline())
        other = Row(spacing=12, margins=(12, 6, 12, 6))
        other_mark = Mark(multi)
        other.add(other_mark)
        field = QLineEdit()
        field.setPlaceholderText("Something else…")
        field.setProperty("quiet", "true")
        field.setAccessibleName("Something else…")
        field.textChanged.connect(lambda v, qi=qi, multi=multi, m=other_mark: self._on_other(qi, v, multi, m))
        field.returnPressed.connect(self.submit)
        other.add(field, 1)
        self.other_fields[qi] = field
        options.add(other)
        bl.addWidget(options)
        note_host = QWidget()
        nl = vbox(note_host)
        link = bits.button("Add a note", kind="more", size="sm")
        link.setAccessibleName("Add a note")
        link.override = {"px": 6}
        notes = QLineEdit()
        notes.setPlaceholderText("A note for Claude about this answer")
        notes.setAccessibleName("A note for Claude about this answer")
        notes.setVisible(False)
        notes.textChanged.connect(lambda v, qi=qi: self.notes.__setitem__(qi, v))
        self.note_fields[qi] = notes

        def open_note() -> None:
            link.setVisible(False)
            notes.setVisible(True)
            notes.setFocus()

        link.clicked.connect(open_note)
        nl.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
        nl.addWidget(notes)
        bl.addWidget(note_host)
        return box

    def toggle(self, qi: int, label: str, multi: bool) -> None:
        cur = self.sel.get(qi, [])
        if multi:
            nxt = [x for x in cur if x != label] if label in cur else cur + [label]
        else:
            nxt = [label]
            self.other[qi] = ""
            f = self.other_fields.get(qi)
            if f is not None and f.text():
                f.blockSignals(True)
                f.setText("")
                f.blockSignals(False)
        self.sel[qi] = nxt
        for row in self.rows.get(qi, []):
            row.set_on(row.label in nxt)
        self._update_complete()

    def _on_other(self, qi: int, v: str, multi: bool, mark: Mark) -> None:
        self.other[qi] = v
        mark.set_on(bool(v.strip()))
        if v and not multi:
            self.sel[qi] = []
            for row in self.rows.get(qi, []):
                row.set_on(False)
        self._update_complete()

    def _preview(self, qi: int, oi: int) -> None:
        opts = self.questions[qi].get("options") or []
        preview = opts[oi].get("preview") if oi < len(opts) and isinstance(opts[oi], dict) else None
        while self.preview_host.count():
            it = self.preview_host.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if not preview:
            self.preview.setVisible(False)
            return
        text = preview if re.match(r"^\s*(```|#|[-*] )", preview) else "```\n" + preview + "\n```"
        self.preview_host.addWidget(markdown_label(text, self.ctx.cwd, compact=True))
        self.preview.setVisible(True)

    @property
    def complete(self) -> bool:
        return all(
            len(self.sel.get(qi, [])) > 0 or (self.other.get(qi) or "").strip() for qi in range(len(self.questions))
        )

    def _update_complete(self) -> None:
        self.send_btn.setEnabled(self.complete and not self.busy)

    def build_decision(self) -> Json:
        """The decision dict the web client sends: the input with answers (and notes) filled in."""
        answers: Dict[str, str] = {}
        annotations: Dict[str, Dict[str, str]] = {}
        for qi, q in enumerate(self.questions):
            parts = list(self.sel.get(qi, []))
            if (self.other.get(qi) or "").strip():
                parts.append(self.other[qi].strip())
            answers[str(q.get("question"))] = ", ".join(parts)
            if (self.notes.get(qi) or "").strip():
                annotations[str(q.get("question"))] = {"notes": self.notes[qi].strip()}
        updated: Json = {**(self.perm.get("input") or {}), "answers": answers}
        if annotations:
            updated["annotations"] = annotations
        return {"behavior": "allow", "updatedInput": updated}

    def submit(self) -> None:
        if not self.complete or self.busy or self.perm is None:
            return
        self.busy = True
        self._update_complete()
        self.skip_btn.setEnabled(False)

        def failed(_e: Any) -> None:
            self.busy = False
            self._update_complete()
            self.skip_btn.setEnabled(True)

        store.respond(self.ctx.session_id, str(self.perm.get("requestId")), self.build_decision()).catch(failed)

    def skip(self) -> None:
        if self.perm is None:
            return
        store.respond(
            self.ctx.session_id,
            str(self.perm.get("requestId")),
            {
                "behavior": "deny",
                "message": "The user chose not to answer these questions. Continue with your best judgment.",
            },
        )

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.color("accent_ring" if self.live else "hair_2"), 1.5 if self.live else 1))
        p.setBrush(t.color("raised"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)
