"""The lead's helpers in the Changes panel (HELPERS.md §6.5): a row with the finish actions Dispatch
uses — Commit all, Open pull requests, Keep, Discard — and one section per helper worktree, headed by its
state, name, branch and diffstat, holding its diff in the same viewer the session's own changes use."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ... import theme
from ...lib.fmt import err_text, plural
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, StatusGlyph
from ..ui.signals import bind
from .diff_view import DiffView, Num
from .right_panel import Btn, Hairline, lbl

Json = Dict[str, Any]
TERMINAL = ("done", "failed", "stopped", "skipped")
# a helper's state → the session status the glyph knows
GLYPH_STATUS = {
    "running": "running",
    "preparing": "starting",
    "needs_action": "requires_action",
    "done": "idle",
    "failed": "error",
}


class HelpersChanges(QWidget):
    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self.sections: Dict[str, HelperSection] = {}
        self._ids: List[str] = []
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(Hairline())
        self.row = QWidget()
        rl = QHBoxLayout(self.row)
        rl.setContentsMargins(12, 8, 12, 8)
        rl.setSpacing(6)
        self.count = lbl("", theme.FS_SMALL, tone="faint")
        rl.addWidget(self.count, 1)
        self.commit_btn = Btn(
            "Commit all…",
            size="sm",
            on_click=lambda: self.finish("commit"),
            tooltip="A commit in each helper's worktree, on its branch",
        )
        self.pr_btn = Btn(
            "Open pull requests…",
            kind="ghost",
            size="sm",
            on_click=lambda: self.finish("pr"),
            tooltip="Commit, push and open a pull request for each, through gh",
        )
        self.keep_btn = Btn(
            "Keep",
            kind="ghost",
            size="sm",
            on_click=lambda: self.finish("keep"),
            tooltip="Leave the worktrees as they are",
        )
        self.discard_btn = Btn(
            "Discard…",
            kind="ghost",
            size="sm",
            on_click=lambda: self.finish("discard"),
            tooltip="Remove the worktrees and their branches",
        )
        for b in (self.commit_btn, self.pr_btn, self.keep_btn, self.discard_btn):
            rl.addWidget(b)
        lay.addWidget(self.row)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)
        lay.addWidget(self.body)
        self.hide()
        bind(store.rt_changed, self, self._on_rt)
        self.sync()

    def _on_rt(self, sid: str) -> None:
        if sid == self.sid:
            self.sync()

    def helpers(self) -> List[Json]:
        rt = store.rt.get(self.sid)
        return [h for h in ((rt.st.get("helpers") if rt else None) or []) if isinstance(h, dict)]

    def sync(self) -> None:
        helpers = self.helpers()
        if not helpers:
            self.hide()
            return
        ids = [str(h.get("id")) for h in helpers]
        if ids != self._ids:
            for hid in list(self.sections):
                if hid not in ids:
                    sec = self.sections.pop(hid)
                    self.body_lay.removeWidget(sec)
                    sec.hide()
                    sec.deleteLater()
            for i, h in enumerate(helpers):
                hid = str(h.get("id"))
                sec = self.sections.get(hid)
                if sec is None:
                    sec = self.sections[hid] = HelperSection(h)
                if self.body_lay.indexOf(sec) != i:
                    self.body_lay.removeWidget(sec)
                    self.body_lay.insertWidget(i, sec)
            self._ids = ids
        for h in helpers:
            self.sections[str(h.get("id"))].sync(h)
        open_ = [h for h in helpers if h.get("state") in TERMINAL and not h.get("finished") and h.get("worktree")]
        done = [h for h in open_ if h.get("state") == "done"]
        files = sum(int(h.get("files") or 0) for h in helpers)
        self.count.setText(
            f"{plural(len(helpers), 'helper')} · {plural(files, 'file')}"
            + (f" · {len(open_)} to finish" if open_ else "")
        )
        self.commit_btn.setVisible(bool(done))
        self.pr_btn.setVisible(bool(done))
        self.keep_btn.setVisible(bool(open_))
        self.discard_btn.setVisible(bool(open_))
        for b in (self.commit_btn, self.pr_btn, self.keep_btn, self.discard_btn):
            b.setEnabled(not self._busy)
        self.show()

    # ---- finishing
    def finish(self, action: str) -> None:
        helpers = self.helpers()
        open_ = [h for h in helpers if h.get("state") in TERMINAL and not h.get("finished") and h.get("worktree")]
        done = [h for h in open_ if h.get("state") == "done"]
        targets = done if action in ("commit", "pr") else open_
        if not targets or self._busy:
            return
        names = ", ".join(str(h.get("name")) for h in targets)

        def go(_c: bool = True) -> None:
            self._busy = True
            self.sync()

            def ok(r: Any) -> None:
                self._busy = False
                bad = [x for x in ((r or {}).get("results") or []) if not x.get("ok")]
                store.toast(
                    f"{action.capitalize()}: {', '.join(x.get('name') for x in bad)} failed"
                    if bad
                    else {"commit": "Committed", "pr": "Pull requests opened", "keep": "Kept", "discard": "Discarded"}[
                        action
                    ]
                    + f" · {names}",
                    "error" if bad else "success",
                )
                self.sync()

            def fail(e: Any) -> None:
                self._busy = False
                store.toast(err_text(e), "error")
                self.sync()

            store.call("helpers.finish", {"leadId": self.sid, "action": action}).then(ok).catch(fail)

        if action == "keep":
            go()
            return
        spec = {
            "commit": ConfirmSpec(
                title=f"Commit {plural(len(targets), 'helper')}?",
                body=f"Each helper's changes are committed on its own branch; the worktree folder is removed and the branch kept. Helpers: {names}.",
                confirm="Commit all",
                on_confirm=go,
            ),
            "pr": ConfirmSpec(
                title=f"Open {plural(len(targets), 'pull request')}?",
                body=f"Each helper's changes are committed, pushed, and a pull request is opened through gh; the pull requests link to each other. Helpers: {names}.",
                confirm="Open pull requests",
                on_confirm=go,
            ),
            "discard": ConfirmSpec(
                title=f"Discard the work of {plural(len(targets), 'helper')}?",
                body=f"The worktrees and their branches are removed; nothing is kept. Helpers: {names}.",
                confirm="Discard",
                danger=True,
                on_confirm=go,
            ),
        }[action]
        store.confirm_action(spec)


class HelperSection(QWidget):
    """One helper's worktree: a head with the glyph, name, branch and diffstat, and its diff below."""

    def __init__(self, h: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.h: Json = {}
        self.open = True
        self._loaded_sig: Any = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(Hairline())
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 6, 10, 6)
        hl.setSpacing(8)
        self.chev = IconButton("chevron-down", "Fold", 13)
        self.chev.clicked.connect(self.toggle)
        hl.addWidget(self.chev)
        self.glyph = StatusGlyph()
        hl.addWidget(self.glyph)
        self.name = lbl("", theme.FS_SMALL, 600)
        hl.addWidget(self.name)
        self.state = lbl("", theme.FS_MICRO, tone="faint")
        hl.addWidget(self.state)
        self.branch = lbl("", theme.FS_MICRO, tone="faint", mono=True)
        hl.addWidget(self.branch, 1)
        self.add = Num("add")
        self.dele = Num("del")
        hl.addWidget(self.add)
        hl.addWidget(self.dele)
        self.finished = lbl("", theme.FS_MICRO, tone="faint")
        hl.addWidget(self.finished)
        lay.addWidget(head)
        self.view = DiffView(mode="unified", wrap=True, can_comment=False, max_rows=300, dual=False, simple=True)
        self.view.hide()
        lay.addWidget(self.view)
        self.note = lbl("", theme.FS_SMALL, tone="faint")
        self.note.setContentsMargins(40, 2, 12, 8)
        lay.addWidget(self.note)
        self.sync(h)

    def toggle(self) -> None:
        self.open = not self.open
        self.chev.set_icon_name("chevron-down" if self.open else "chevron-right")
        self._apply_visible()

    def _apply_visible(self) -> None:
        has = bool(self._files)
        self.view.setVisible(self.open and has)
        self.note.setVisible(self.open and not has)

    _files: List[Json] = []

    def sync(self, h: Json) -> None:
        changed = h != self.h
        self.h = dict(h)
        if not changed and self._loaded_sig is not None:
            return
        st = str(h.get("state") or "")
        self.glyph.set_state(GLYPH_STATUS.get(st, "stopped"), int(h.get("pending") or 0), False)
        self.name.setText(str(h.get("name") or ""))
        self.state.setText(st.replace("_", " ") + (f" · {h.get('reason')}" if h.get("reason") and st != "done" else ""))
        self.branch.setText(str(h.get("branch") or ""))
        self.add.set_value(int(h.get("add") or 0))
        self.dele.set_value(int(h.get("del") or 0))
        fin = str(h.get("finished") or "")
        self.finished.setText(
            {"commit": f"committed {h.get('commit') or ''}", "discard": "discarded", "keep": "kept"}.get(fin, "")
        )
        self.finished.setVisible(bool(fin))
        sig = (st, int(h.get("files") or 0), int(h.get("add") or 0), fin)
        if sig != self._loaded_sig:
            self._loaded_sig = sig
            self.load()

    def load(self) -> None:
        h = self.h
        wt = str(h.get("worktree") or "")
        if not wt or h.get("finished") in ("commit", "discard"):
            self._files = []
            self.note.setText(
                {"commit": "Committed on its branch.", "discard": "Discarded."}.get(
                    str(h.get("finished") or ""), "No worktree."
                )
            )
            self._apply_visible()
            return
        if h.get("state") not in TERMINAL and h.get("state") != "needs_action":
            self.note.setText("Still working…")

        def ok(r: Any) -> None:
            files = list((r or {}).get("files") or []) if isinstance(r, dict) else []
            self._files = files
            self.view.set_files(files)
            self.note.setText("No changes yet." if h.get("state") not in TERMINAL else "No changes.")
            self._apply_visible()

        def bad(e: Any) -> None:
            self._files = []
            self.note.setText(err_text(e))
            self._apply_visible()

        store.call("git.diff", {"cwd": wt}, quiet=True).then(ok).catch(bad)
