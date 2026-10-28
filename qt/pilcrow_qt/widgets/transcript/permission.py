"""The permission prompt under a tool row (a port of web/src/components/tools/Permission.tsx).

Allow / Always allow / Deny, a note field that declines with a message, and the number keys for the
first pending request. Decisions go through ``store.respond`` with the same decision dicts the web
client sends. The suggestion helpers are module functions because the control room needs them too.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from ... import theme
from ...lib.fmt import rel_path
from ...lib.tools_meta import str_
from ...lib.transcript_model import ToolItem
from ...store import store
from ..ui import bits
from ..ui.signals import bind
from .bodies.common import Hairline, IconLabel, Row, faint, mono_family, restyle, rich, vbox
from .ctx import ToolCtx

Json = Dict[str, Any]


def _dest_label(d: Optional[str]) -> str:
    return {
        "session": "for this session",
        "localSettings": "in this project (just you)",
        "projectSettings": "in this project",
        "userSettings": "everywhere",
    }.get(d or "", "")


def typing_elsewhere(w: Optional[QWidget]) -> bool:
    """Number keys answer a waiting prompt unless the person is typing somewhere. An empty message box
    counts as not typing: the box has focus by default, and a lone "1" there is never a message.
    The composer marks itself with ``setObjectName("composer")`` (or a ``composer`` property)."""
    if w is None:
        return False
    if isinstance(w, QLineEdit):
        text = w.text()
    elif isinstance(w, (QTextEdit, QPlainTextEdit)):
        text = w.toPlainText()
    else:
        return False
    composer = w.objectName() == "composer" or bool(w.property("composer"))
    return not (composer and not text)


def suggestion_label(s: Json, cwd: Optional[str] = None) -> str:
    """A short button label for a suggestion; the exact rules go on a quiet line under the buttons."""
    if s.get("type") == "addRules" and s.get("behavior") == "allow":
        rules = s.get("rules") or []
        tools = {r.get("toolName") for r in rules}
        scope = _dest_label(s.get("destination"))
        if len(tools) == 1 and ("Bash" in tools or "PowerShell" in tools):
            head = f"Always allow these {len(rules)} commands" if len(rules) > 1 else "Always allow this command"
            return f"{head} {scope}"
        if len(tools) == 1:
            return f"Always allow {next(iter(tools))} {scope}"
        return f"Always allow {len(rules)} rules {scope}"
    return describe_suggestion(s, cwd)


def suggestion_rules(s: Json) -> str:
    if s.get("type") != "addRules":
        return ""
    return "  ·  ".join(
        f"{r.get('toolName')}({r.get('ruleContent')})" if r.get("ruleContent") else str(r.get("toolName"))
        for r in (s.get("rules") or [])
    )


def describe_suggestion(s: Json, cwd: Optional[str] = None) -> str:
    t = s.get("type")
    dest = _dest_label(s.get("destination"))
    if t == "setMode":
        return f"Allow all edits {dest}" if s.get("mode") == "acceptEdits" else f"Switch to {s.get('mode')} {dest}"
    if t == "addDirectories":
        dirs = ", ".join(rel_path(str(d), cwd) for d in (s.get("directories") or []))
        return f"Allow access to {dirs} {dest}"
    if t == "addRules" and s.get("behavior") == "allow":
        rules = [
            f"{r.get('toolName')}({r.get('ruleContent')})" if r.get("ruleContent") else str(r.get("toolName"))
            for r in (s.get("rules") or [])
        ]
        return f"Always allow {', '.join(rules)} {dest}"
    return f"{t} {dest}"


def default_title(perm: Json, cwd: str) -> str:
    i = perm.get("input") or {}
    tool = perm.get("toolName")
    if tool in ("Bash", "PowerShell"):
        return "Allow Claude to run this command?"
    if tool in ("Edit", "MultiEdit"):
        return f"Allow this edit to {rel_path(str_(i, 'file_path'), cwd)}?"
    if tool == "Write":
        return f"Allow Claude to write {rel_path(str_(i, 'file_path'), cwd)}?"
    if tool == "WebFetch":
        return f"Allow Claude to fetch {i.get('url')}?"
    if tool == "NotebookEdit":
        return "Allow this notebook edit?"
    return perm.get("title") or f"Allow Claude to use {perm.get('displayName') or tool}?"


def _rule_scope(s: Json) -> str:
    """Scope words for the "won't ask again" line."""
    scope = _dest_label(s.get("destination"))
    return f" {scope}" if scope else ""


class PermissionCard(QWidget):
    """`.perm`: inside a tool card it is the answer bar under the row; on its own it is a card with a title."""

    def __init__(
        self,
        perm: Json,
        ctx: ToolCtx,
        item: Optional[ToolItem] = None,
        stale: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.perm, self.ctx, self.item, self.stale = perm, ctx, item, stale
        self.busy = False
        self.orphan = item is None
        self.setAccessibleName("Permission request")
        suggestions: List[Json] = list(perm.get("suggestions") or [])
        self.allow_always = not perm.get("suppressAlwaysAllowRule") and len(suggestions) > 0
        first_pending = next(iter(ctx.pending.values()), None)
        self.first = bool(first_pending) and first_pending.get("requestId") == perm.get("requestId")
        cwd = ctx.cwd
        bind(theme.theme.changed, self, lambda _t: self.update())

        lay = vbox(self, margins=(16, 14, 16, 12) if self.orphan else (14, 10, 14, 10), spacing=0)
        if not self.orphan:
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(Hairline("hair_2"))
            inner = QWidget()
            lay.addWidget(inner)
            lay = vbox(inner, margins=(14, 10, 14, 10), spacing=0)

        # Inside a tool card the row above already says what Claude wants to do; only an orphan needs a title.
        title = ""
        if self.orphan:
            title = (
                perm.get("title")
                if perm.get("title") and perm.get("toolName") not in ("Bash", "PowerShell")
                else default_title(perm, cwd)
            )
        self.title_text = title or ""
        if title:
            t = bits.label(title, wrap=True)
            restyle(t, lambda tk: f"QLabel {{ font-size: {theme.FS_UI}px; font-weight: 600; color: {tk.text}; }}")
            lay.addWidget(t)
        desc = perm.get("description") or ""
        sub: List[str] = []
        if desc and self.orphan and desc not in rel_path(str_(perm.get("input"), "file_path"), cwd):
            sub.append(str(desc))
        if perm.get("decisionReason"):
            sub.append(str(perm["decisionReason"]))
        self.sub_lines = sub
        if sub or perm.get("blockedPath"):
            box = QWidget()
            bl = vbox(box, margins=(0, 2, 0, 0), spacing=2)
            for s in sub:
                bl.addWidget(bits.label(s, tone="muted", size="small", wrap=True))
            if perm.get("blockedPath"):
                bl.addWidget(
                    rich(
                        f"Outside the working folder: <span style=\"font-family:'{mono_family()}'\">"
                        f"{html.escape(str(perm['blockedPath']))}</span>",
                        size=theme.FS_SMALL,
                        color="text_2",
                    )
                )
            lay.addWidget(box)

        # .perm-actions: gap 6px (a key sits 5px from its button), no margin under the row above
        # inside a tool card, 12px for a card of its own
        actions = Row(spacing=6, margins=(0, 12 if self.orphan else 0, 0, 0))
        default_no = bool(perm.get("defaultToNo"))
        self.allow_btn = bits.button("Allow", kind="" if default_no else "primary", on_click=self.allow)
        self.allow_btn.setAccessibleName("Allow")
        restyle(self.allow_btn, lambda tk: _perm_button(tk, "" if default_no else "primary"))
        actions.add(self.allow_btn)
        if self.first and not default_no:
            actions.add(_key("1"))
        self.always_btn = None
        if self.allow_always:
            self.always_btn = bits.button(
                "Always allow",
                on_click=self.always,
                tooltip="\n".join(describe_suggestion(s, cwd) for s in suggestions),
            )
            self.always_btn.setAccessibleName("Always allow")
            restyle(self.always_btn, lambda tk: _perm_button(tk, ""))
            actions.add(self.always_btn)
            if self.first:
                actions.add(_key("2"))
        self.deny_btn = bits.button("Deny", kind="primary" if default_no else "ghost", on_click=self.deny)
        self.deny_btn.setAccessibleName("Deny")
        restyle(self.deny_btn, lambda tk: _perm_button(tk, "primary" if default_no else "ghost"))
        actions.add(self.deny_btn)
        if self.first:
            actions.add(_key("3" if self.allow_always else "2"))
        actions.stretch()
        self.note_link = bits.button("Tell Claude what to do instead", kind="ghost", size="sm", on_click=self.show_note)
        self.note_link.setAccessibleName("Tell Claude what to do instead")
        restyle(
            self.note_link,
            lambda tk: (
                f"QPushButton {{ color: {tk.text_3}; padding: 4px 6px; }} QPushButton:hover {{ color: {tk.text}; }}"
            ),
        )
        actions.add(self.note_link)
        lay.addWidget(actions)

        self.rule = suggestion_rules(suggestions[0]) if self.allow_always else ""
        if self.rule:
            extra = f" and {len(suggestions) - 1} more" if len(suggestions) > 1 else ""
            rules = rich(
                f"Always allow covers <span style=\"font-family:'{mono_family()}';font-size:11.5px;"
                f'color:{theme.tokens().text_2}">{html.escape(self.rule)}</span>'
                f"{html.escape(extra)}{html.escape(_rule_scope(suggestions[0]))}.",
                size=theme.FS_SMALL,
                color="text_3",
            )
            rules.setContentsMargins(0, 8, 0, 0)
            lay.addWidget(rules)

        self.feedback_row = Row(spacing=10, margins=(0, 10, 0, 0))
        self.feedback = QLineEdit()
        self.feedback.setPlaceholderText("What should Claude do instead?")
        self.feedback.setAccessibleName("What should Claude do instead?")
        self.feedback.textChanged.connect(self._on_feedback)
        self.feedback.returnPressed.connect(lambda: self.deny() if self.feedback.text().strip() else None)
        self.feedback_row.add(self.feedback, 1)
        hint = Row(spacing=5)
        hint.add(IconLabel("corner-down-left", "text_3", 12))
        hint.add(faint("sends it and declines this step", size="micro"))
        self.feedback_row.add(hint)
        self.feedback_row.setVisible(False)
        lay.addWidget(self.feedback_row)

        self._shortcuts: List[QShortcut] = []
        if self.first and not stale:
            for key in ("1", "2", "3"):
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(lambda k=key: self._on_key(k))
                self._shortcuts.append(sc)
        if stale:
            self.setEnabled(False)

    # ---- decisions
    def _set_busy(self, on: bool) -> None:
        self.busy = on
        for b in (self.allow_btn, self.always_btn, self.deny_btn):
            if b is not None:
                b.setEnabled(not on)

    def decide(self, decision: Json) -> None:
        if self.busy or self.stale:
            return
        self._set_busy(True)
        store.respond(self.ctx.session_id, str(self.perm.get("requestId")), decision).catch(
            lambda _e: self._set_busy(False)
        )

    def allow(self) -> None:
        self.decide({"behavior": "allow", "decisionClassification": "user_temporary"})

    def always(self) -> None:
        self.decide(
            {
                "behavior": "allow",
                "updatedPermissions": list(self.perm.get("suggestions") or []),
                "decisionClassification": "user_permanent",
            }
        )

    def deny(self) -> None:
        text = self.feedback.text().strip()
        self.decide(
            {
                "behavior": "deny",
                "message": f"The user declined and said: {text}" if text else "The user declined this action.",
                "decisionClassification": "user_reject",
            }
        )
        if text:
            store.toast("Sent your note to Claude", "info")

    def show_note(self) -> None:
        self.note_link.setVisible(False)
        self.feedback_row.setVisible(True)
        self.feedback.setFocus()

    def hide_note(self) -> None:
        self.feedback_row.setVisible(False)
        self.note_link.setVisible(True)

    def _on_feedback(self, text: str) -> None:
        self.deny_btn.setText("Deny with note" if text.strip() else "Deny")

    def _on_key(self, key: str) -> None:
        if self.busy or self.stale or typing_elsewhere(QApplication.focusWidget()):
            return
        if key == "1" and not self.perm.get("defaultToNo"):
            self.allow()
        elif key == "2" and self.allow_always:
            self.always()
        elif key == ("3" if self.allow_always else "2"):
            self.deny()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape and self.feedback_row.isVisible():
            self.hide_note()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        # A request that lost its tool row keeps a card of its own (`.perm.orphan`).
        if not self.orphan:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.color("hair_2"), 1))
        p.setBrush(t.color("raised"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


def _key(k: str) -> QWidget:
    """`.perm-key`: the number that answers, a 16px ringed box in text-4 next to its button."""
    lb = bits.label(k, size="micro")
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setFixedHeight(16)
    lb.setMinimumWidth(16)
    restyle(
        lb,
        lambda t: (
            f"QLabel {{ color: {t.text_4}; font-size: 11px; font-weight: 600; padding: 0 3px; border-radius: 4px; "
            f"border: 1px solid {theme.blend(t.hair_2, t.raised).name()}; }}"
        ),
    )
    return lb


def _perm_button(t: theme.Tokens, kind: str) -> str:
    """`.perm-actions .btn`: 28px tall, 11px sides, 13px text, 7px corners; primary in the accent,
    the default on a quiet fill, ghost bare in text-2."""
    base = (
        f"min-height: 28px; max-height: 28px; padding: 0 11px; font-size: {theme.FS_UI}px; font-weight: 500; "
        f"border-radius: 7px; border: none;"
    )
    if kind == "primary":
        return (
            f"QPushButton {{ {base} background: {t.accent}; color: {t.accent_ink}; }} "
            f"QPushButton:hover {{ background: {t.accent_2}; }} "
            f"QPushButton:disabled {{ background: {theme.blend(t.fill_2, t.raised).name()}; color: {t.text_4}; }}"
        )
    if kind == "ghost":
        return (
            f"QPushButton {{ {base} background: transparent; color: {t.text_2}; }} "
            f"QPushButton:hover {{ background: {theme.blend(t.fill_2, t.raised).name()}; color: {t.text}; }}"
        )
    return (
        f"QPushButton {{ {base} background: {theme.blend(t.fill_2, t.raised).name()}; color: {t.text}; }} "
        f"QPushButton:hover {{ background: {theme.blend(t.fill_3, t.raised).name()}; }}"
    )
