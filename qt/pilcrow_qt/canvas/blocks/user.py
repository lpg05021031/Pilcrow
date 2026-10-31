"""What the person said: a bubble on the right that hugs its words, attachments above the words,
quiet actions under it (copy, edit and resend, restore files) that show on hover without moving
anything, and the queued note while the message waits."""

from __future__ import annotations

import html
import re
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QTextDocument

from ... import markdown, theme
from ...lib.fmt import rel_path
from ...lib.transcript_model import UserItem
from ...store import ConfirmSpec, store
from ..paint import Measurer, PaintState, doc_height, doc_pos_at, draw_doc, rounded
from ..scene import GRADE_EXACT, Block, Target

BUBBLE_LINE = markdown.px(theme.FS_BODY * 1.55)


def bubble_line() -> int:
    return markdown.px(theme.FS_BODY * theme.scale * 1.55)


MENTION = re.compile(r"(@[\w./~-]+[\w/-])")
PAD_W, PAD_H = 14, 9
ACTIONS_H = 28
ACTION_SZ = 24
THUMB_W, THUMB_H = 220, 160


def render_mentions(text: str, t: theme.Tokens) -> str:
    mono = theme._MONO_FAMILY or "monospace"
    out = []
    for i, part in enumerate(MENTION.split(text)):
        esc = html.escape(part)
        if i % 2 == 1 and len(part) > 1:
            out.append(
                f"<span style=\"font-family:'{mono}';font-size:{int(theme.FS_BODY * 0.88 + 0.5)}px;color:{t.info}\">{esc}</span>"
            )
        else:
            out.append(esc)
    return f'<p style="white-space:pre-wrap;margin:0;line-height:{bubble_line()}px">{"".join(out)}</p>'


def _pixmap_of(src: str) -> QPixmap:
    pm = QPixmap()
    if src.startswith("data:"):
        try:
            _head, data = src.split(",", 1)
            pm.loadFromData(QByteArray.fromBase64(data.encode("ascii")))
        except ValueError:
            pass
    else:
        pm.load(src)
    return pm


class UserBlock(Block):
    KIND = "user"
    __slots__ = ("item", "sid", "cwd", "doc", "bubble", "thumbs", "chips", "attach_h", "queued", "busy", "_html")

    def __init__(self, item: UserItem, sid: str, cwd: str) -> None:
        super().__init__(f"user:{item.id}")
        self.item = item
        self.sid = sid
        self.cwd = cwd
        self.doc: Optional[QTextDocument] = None
        self._html = ""
        self.bubble = QRect()  # in block coordinates
        self.thumbs: List[Tuple[QRect, QPixmap, str]] = []
        self.chips: List[Tuple[QRect, str]] = []
        self.attach_h = 0
        self.queued = False
        self.busy = False
        self.sync(item)

    def sync(self, item: UserItem) -> bool:
        self.item = item
        r = store.rt.get(self.sid)
        queued = bool(r and item.uuid and item.uuid in (r.st.get("queued") or []))
        busy = bool(r and r.busy)
        sig = (item.rev, queued, busy, len(item.images), len(item.files), bool(item.command))
        changed = sig != self.sig
        self.sig = sig
        self.rev = item.rev
        self.queued, self.busy = queued, busy
        return changed

    # ---- heights
    def estimate(self, width: int, m: Measurer) -> int:
        if self.item.command:
            return 26 + 8
        avail = int(width * 0.84) - 2 * PAD_W
        lines = (
            max(
                1,
                int(
                    m.fast_height(self.item.text, avail, m.font(theme.FS_BODY * theme.scale), bubble_line())
                    / bubble_line()
                ),
            )
            if self.item.text
            else 0
        )
        attach = (THUMB_H + 10) if self.item.images else (26 + 10 if self.item.files else 0)
        return lines * bubble_line() + 2 * PAD_H + attach + ACTIONS_H + (18 if self.queued else 0)

    fast_measure = estimate

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        it = self.item
        if it.command:
            return 26 + 8
        avail = max(80, int(width * 0.84)) - 2 * PAD_W
        body = render_mentions(it.text, m.tokens)
        if self.doc is None or self._html != body or int(self.doc.textWidth()) != avail:
            self.doc = m.html_doc(body, avail, m.font(theme.FS_BODY))
            self._html = body
        # a wrapping bubble is as wide as it may be; a one-liner hugs its words
        text_h = doc_height(self.doc) if it.text else 0
        lines = 0
        b = self.doc.firstBlock()
        while b.isValid() and lines < 2:
            lines += b.layout().lineCount() if b.layout() is not None else 1
            b = b.next()
        text_w = avail if lines > 1 else min(avail, int(self.doc.idealWidth() + 0.999)) if it.text else 0
        # attachments: thumbnails and file chips, right-aligned above the words
        self.thumbs = []
        self.chips = []
        self.attach_h = 0
        inner_w = text_w
        if it.images or it.files:
            x = 0
            row_h = 0
            for src in it.images:
                pm = _pixmap_of(src)
                if pm.isNull():
                    continue
                pm = pm.scaled(
                    THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.thumbs.append((QRect(x, 0, pm.width(), pm.height()), pm, src))
                x += pm.width() + 6
                row_h = max(row_h, pm.height())
            for f in it.files:
                w = int(m.text_w(f, m.font(theme.FS_SMALL))) + 20 + 12 + 6
                self.chips.append((QRect(x, 0, w, 26), f))
                x += w + 6
                row_h = max(row_h, 26)
            inner_w = max(inner_w, x - 6)
            self.attach_h = row_h + 8
        if self.doc is not None and text_w < inner_w and it.text:
            self.doc.setTextWidth(inner_w)
            text_h = doc_height(self.doc)
            text_w = inner_w
        bw = inner_w + 2 * PAD_W
        bh = (text_h if it.text else 0) + self.attach_h + 2 * PAD_H
        self.bubble = QRect(width - bw, 0, bw, bh)
        # attachments and thumbs are right-aligned inside the bubble
        shift = inner_w - (
            max([r.right() + 1 for r, _pm, _s in self.thumbs] + [r.right() + 1 for r, _f in self.chips])
            if (self.thumbs or self.chips)
            else 0
        )
        self.thumbs = [
            (QRect(r.x() + self.bubble.x() + PAD_W + shift, PAD_H + 2, r.width(), r.height()), pm, s)
            for r, pm, s in self.thumbs
        ]
        self.chips = [
            (QRect(r.x() + self.bubble.x() + PAD_W + shift, PAD_H + 2, r.width(), 26), f) for r, f in self.chips
        ]
        return bh + ACTIONS_H + (18 if self.queued else 0)

    def cool(self) -> None:
        self.doc = None
        self._html = ""
        self.thumbs = []
        self.warm = False

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        it = self.item
        bg = theme.blend(t.fill_2, t.canvas) if t.dark else QColor(t.raised_2)
        bg_s = bg.name()
        if it.command:
            f = st.m.font(12.5, 400, mono=True)
            label = f"/{it.command.name}"
            lw = int(st.m.text_w(label, f)) + 20
            args_w = int(st.m.text_w(it.command.args, f)) if it.command.args else 0
            cx = x + w - lw - (args_w + 8 if args_w else 0)
            rounded(p, QRect(cx, y, lw, 26), bg, None, 13)
            st.m.draw_text(p, cx + 10, y + (26 - st.m.line_h(f)) / 2, label, f, QColor(t.text))
            if args_w:
                st.m.draw_text(p, cx + lw + 8, y + (26 - st.m.line_h(f)) / 2, it.command.args, f, QColor(t.text_2))
            return
        br = self.bubble.translated(x, y)
        rounded(p, br, bg, None, 16)
        for r, pm, _src in self.thumbs:
            p.drawPixmap(r.translated(x, y).topLeft(), pm)
        chip_bg = theme.blend(t.fill_2, bg_s)
        f_small = st.m.font(theme.FS_SMALL)
        for r, name in self.chips:
            rr = r.translated(x, y)
            rounded(p, rr, chip_bg, None, theme.R2)
            p.drawPixmap(rr.x() + 10, rr.y() + 7, st.m.icon("paperclip", QColor(t.text_2), 12))
            st.m.draw_text(p, rr.x() + 28, rr.y() + (26 - st.m.line_h(f_small)) / 2, name, f_small, QColor(t.text))
        if self.doc is not None and it.text:
            tx = br.x() + PAD_W
            ty = br.y() + PAD_H + self.attach_h
            clip = QRect(tx, ty, br.width() - 2 * PAD_W, br.height() - 2 * PAD_H - self.attach_h)
            if st.clip is not None:
                clip = clip.intersected(st.clip)
            color = QColor(t.text_3 if self.queued else t.text)
            sels = None
            if st.selection is not None:
                from ..paint import FULL, selection

                sbg = theme.blend(t.selection, bg_s)
                a, b = (0, self.doc.characterCount()) if st.selection == FULL else st.selection
                sels = [selection(self.doc, a, b, sbg)]
            draw_doc(p, self.doc, tx, ty, clip, color, sels)
        # actions, right-aligned under the bubble, only while the pointer is on the row
        ay = y + br.height() + 4
        if self.queued:
            f = st.m.font(theme.FS_MICRO)
            txt = "Queued — Claude reads this after the current step"
            tw = st.m.text_w(txt, f)
            st.m.draw_text(p, x + w - tw, ay + 6, txt, f, QColor(t.text_3))
            return
        if st.hover is not None:
            for tid, icon in self._actions():
                r = self._action_rect(tid, x, y, w)
                on = st.hover.kind == "button" and st.hover.id == tid
                if on:
                    rounded(p, r, theme.blend(t.fill_2, t.canvas), None, theme.R2)
                p.drawPixmap(r.x() + 5, r.y() + 5, st.m.icon(icon, QColor(t.text if on else t.text_2), 13))

    def _actions(self) -> List[Tuple[str, str]]:
        out = [("copy", "copy"), ("edit", "pencil-line")]
        if self.item.uuid and not self.busy:
            out.append(("rewind", "history"))
        return out

    def _action_rect(self, tid: str, x: int, y: int, w: int) -> QRect:
        acts = self._actions()
        i = [a for a, _ in acts].index(tid)
        n = len(acts)
        right = x + w
        return QRect(right - (n - i) * (ACTION_SZ + 2) + 2, y + self.bubble.height() + 4, ACTION_SZ, ACTION_SZ)

    # ---- interaction
    def hit(self, x: int, y: int) -> Optional[Target]:
        if self.item.command:
            return None
        for r, _pm, src in self.thumbs:
            if r.contains(x, y):
                return Target("image", src, (r.x(), r.y(), r.width(), r.height()), src)
        for tid, _icon in self._actions():
            r = self._action_rect(tid, 0, 0, self.h_width or 0)
            if r.contains(x, y):
                return Target("button", tid, (r.x(), r.y(), r.width(), r.height()))
        if self.bubble.contains(x, y):
            return Target(
                "text", "bubble", (self.bubble.x(), self.bubble.y(), self.bubble.width(), self.bubble.height())
            )
        return Target("row", "row", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        if target.kind in ("button", "image"):
            return Qt.CursorShape.PointingHandCursor
        if target.kind == "text":
            return Qt.CursorShape.IBeamCursor
        return None

    def tooltip_for(self, target: Target) -> str:
        return (
            {
                "copy": "Copy",
                "edit": "Edit and resend in a new branch of this conversation",
                "rewind": "Restore files to how they were before this message",
            }.get(target.id, "")
            if target.kind == "button"
            else ""
        )

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "image":
            store.open_lightbox(str(target.data))
        elif target.kind == "button":
            if target.id == "copy":
                view.copy_text(self.item.text)
            elif target.id == "edit":
                self._edit()
            elif target.id == "rewind":
                self._rewind()

    def text(self) -> str:
        return self.item.text

    def text_pos_at(self, x: int, y: int) -> Optional[int]:
        if self.doc is None or not self.bubble.contains(x, y):
            return None
        lx = x - self.bubble.x() - PAD_W
        ly = y - self.bubble.y() - PAD_H - self.attach_h
        return doc_pos_at(self.doc, lx, ly)

    def selected_text(self, a: Optional[int], b: Optional[int]) -> str:
        if self.doc is None or a is None or b is None:
            return self.item.text
        from PySide6.QtGui import QTextCursor

        cur = QTextCursor(self.doc)
        cur.setPosition(min(a, b))
        cur.setPosition(max(a, b), QTextCursor.MoveMode.KeepAnchor)
        return cur.selectedText().replace(" ", "\n")

    @property
    def static(self) -> bool:
        return True

    # ---- actions (Transcript.tsx UserMessage)
    def _edit(self) -> None:
        sid, item = self.sid, self.item
        if not item.prev_uuid:
            store.insert_into_composer(sid, item.text, True)
            return
        store.fork_session(sid, item.prev_uuid).then(lambda r: store.insert_into_composer(r["id"], item.text, True))

    def _rewind(self) -> None:
        sid, item, cwd = self.sid, self.item, self.cwd

        def dry(r: dict) -> None:
            if not r.get("canRewind"):
                store.toast(
                    r.get("error") or "There is no saved copy of the files from before this message.", "warning"
                )
                return
            files = [rel_path(f, cwd) for f in (r.get("filesChanged") or [])]
            if not files:
                store.toast("No files changed after this message.", "info")
                return
            n = len(files)

            def confirm(branch: bool) -> None:
                def done(d: dict) -> None:
                    if not d.get("canRewind"):
                        store.toast(d.get("error") or "Could not restore the files.", "error")
                        return
                    store.bump_fs()
                    skipped = int(d.get("skippedLinks") or 0)
                    note = f" ({skipped} linked file{'s' if skipped > 1 else ''} skipped)" if skipped else ""
                    store.toast(f"Restored {n} file{'s' if n > 1 else ''}{note}", "success")
                    if branch and item.prev_uuid:
                        store.fork_session(sid, item.prev_uuid).then(
                            lambda f: store.insert_into_composer(f["id"], item.text, True)
                        )

                store.call("session.rewind", {"id": sid, "messageId": item.uuid, "dryRun": False}).then(done)

            store.confirm_action(
                ConfirmSpec(
                    title="Restore files?",
                    body=f"Put {'this file' if n == 1 else f'these {n} files'} back the way {'it was' if n == 1 else 'they were'} before this message. The conversation stays as it is.",
                    list=files,
                    confirm="Restore files",
                    danger=True,
                    checkbox={
                        "label": "Also start a new branch of the conversation from before this message",
                        "value": False,
                    }
                    if item.prev_uuid
                    else None,
                    on_confirm=confirm,
                )
            )

        store.call("session.rewind", {"id": sid, "messageId": item.uuid, "dryRun": True}).then(dry)


_ = GRADE_EXACT
