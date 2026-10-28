"""Bash / PowerShell: the command and its output in one inset well (BashBody in ToolCard.tsx)."""

from __future__ import annotations

import html
import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from .... import theme
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ....store import store
from ...ui import bits
from ..ctx import ToolCtx
from .code import ansi_label
from .common import Badge, Hairline, Row, Surface, code_more, faint, rich, unwrap_error, vbox

TAIL = 18


class BashBody(QWidget):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item, self.ctx = item, ctx
        self._full = False
        waiting = item.id in ctx.pending
        i = item.input or {}
        s = item.structured if isinstance(item.structured, dict) else None
        r = item.result
        out = ""
        if s:
            stdout, stderr = str(s.get("stdout") or ""), str(s.get("stderr") or "")
            out = "\n".join(x for x in (stdout, stderr) if x) if stdout and stderr else (stdout or stderr)
        if not out and r:
            out = unwrap_error(r.text) if r.is_error else r.text
        exit_m = re.match(r"^(?:Error: )?Exit code (\d+)", r.text) if r and r.is_error else None
        self.exit_code = exit_m.group(1) if exit_m else None
        if self.exit_code and out.startswith("Exit code"):
            out = re.sub(r"^Exit code \d+\n?", "", out)
        if self.exit_code and out.startswith("Error: Exit code"):
            out = re.sub(r"^Error: Exit code \d+\n?", "", out)
        self.out = out
        self.lines = out.split("\n")

        lay = vbox(self, spacing=0)
        self.well = Surface("inset", "light:hair", theme.R3, margins=(0, 0, 0, 0), spacing=0)
        lay.addWidget(self.well)

        command = str_(i, "command")
        cmd = Row(spacing=8, margins=(12, 8, 6, 8))
        prompt = bits.label("$", tone="faint", mono=True)
        prompt.setAlignment(Qt.AlignmentFlag.AlignTop)
        cmd.add(prompt)
        # .bash-cmd: 12.5px mono on 1.55 lines
        self.cmd_label = rich(
            f'<div style="white-space:pre-wrap;line-height:{int(theme.MONO_SIZE * 1.55 + 0.5)}px">{_esc(command)}</div>',
            mono=True,
            size=int(theme.MONO_SIZE + 0.5),
        )
        cmd.lay.addWidget(self.cmd_label, 1, Qt.AlignmentFlag.AlignTop)
        copy = bits.IconButton("copy", "Copy command", 12)
        copy.clicked.connect(lambda: bits.copy_text(command))
        cmd.lay.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
        self.well.add(cmd)

        if item.status == "running" and r is None and not waiting:
            text = f"Running · {round(item.elapsed)}s" if item.elapsed else "Running…"
            running = faint(text)
            running.setContentsMargins(12, 0, 12, 10)
            self.well.add(running)

        self.out_host = QWidget()
        self.out_lay = vbox(self.out_host, margins=(12, 2, 12, 11), spacing=6)
        self.out_label: Optional[QWidget] = None
        self.well.add(self.out_host)
        self.out_host.setVisible(bool(out))
        self._render_out()

        bg_id = s.get("backgroundTaskId") if s else None
        interrupted = bool(s.get("interrupted")) if s else False
        interp = s.get("returnCodeInterpretation") if s else None
        task = item.task
        if self.exit_code or bg_id or interrupted or interp:
            self.well.add(Hairline())
            foot = Row(spacing=8, margins=(12, 7, 12, 7))
            if self.exit_code:
                foot.add(Badge(f"exit {self.exit_code}", "err"))
            if interrupted:
                foot.add(Badge("interrupted"))
            if interp:
                foot.add(faint(str(interp), size="micro", wrap=True), 1)
            if bg_id:
                status = f" · {task.status}" if task and task.status else ""
                lb = rich(
                    f"Background task <span style=\"font-family:'{theme.font_mono().family()}'\">"
                    f"{_esc(str(bg_id))}</span>{_esc(status)}",
                    size=theme.FS_MICRO,
                    color="text_3",
                )
                foot.add(lb)
            if task and task.output_file:
                b = bits.button("Output file", kind="ghost", size="sm", icon="file-text")
                b.setAccessibleName("Output file")
                b.clicked.connect(lambda _c=False, p=task.output_file: store.open_file(p))
                foot.add(b)
            foot.stretch()
            self.well.add(foot)
        git = s.get("gitOperation") if s else None
        if isinstance(git, dict) and git.get("commit"):
            self.well.add(Hairline())
            c = faint(f"Committed {str(git['commit'])[:8]}", size="micro")
            c.setContentsMargins(12, 7, 12, 7)
            self.well.add(c)

    def _render_out(self) -> None:
        if not self.out:
            return
        if self.out_label is not None:
            self.out_label.setParent(None)
            self.out_label.deleteLater()
            self.out_label = None
        while self.out_lay.count():
            it = self.out_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        long = len(self.lines) > TAIL and not self._full
        shown = "\n".join(self.lines[-TAIL:]) if long else self.out
        self.out_label = ansi_label(shown, "text_2")
        self.out_lay.addWidget(self.out_label)
        if long:
            self.out_lay.addWidget(
                code_more(f"Show all {len(self.lines)} lines", self._show_all), 0, Qt.AlignmentFlag.AlignLeft
            )

    def _show_all(self) -> None:
        self._full = True
        self._render_out()


def _esc(s: str) -> str:
    return html.escape(s)
