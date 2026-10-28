"""WebFetch / WebSearch, ToolSearch, MCP tools and the generic body (the default branch of renderBody)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from .... import markdown
from ....lib.fmt import truncate
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ..ctx import ToolCtx
from .common import Section, faint, json_pretty, note, pre, result_text, rich, tool_label


def markdown_label(text: str, cwd: str = "", compact: bool = False) -> QWidget:
    """A Markdown block as rich text (the <Markdown> component), for results and reports."""
    return rich(markdown.render_html(text, base_dir=cwd or None, compact=compact))


class WebBody(Section):
    """WebFetch / WebSearch: the prompt, then the result as Markdown."""

    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        i = item.input or {}
        if str_(i, "prompt"):
            self.add(note(str_(i, "prompt")))
        r = item.result
        if r:
            self.add(markdown_label(truncate(r.text, 20000), ctx.cwd, compact=True))
        else:
            self.add(faint("Waiting for the result…"))


class GenericBody(Section):
    """Any other tool (MCP tools included): the input as JSON, the structured result, the output text."""

    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        i = item.input or {}
        s = item.structured
        r = item.result
        is_err = bool(r and r.is_error)
        if is_err:
            self.add(result_text(item, tool_input=i, session_id=ctx.session_id))
        if not is_err and i:
            self.add(tool_label("Input"))
            self.add(pre(json_pretty(i)))
        if isinstance(s, (dict, list)) and s and not (r and r.text):
            self.add(tool_label("Result"))
            self.add(pre(json_pretty(s)))
        if not is_err:
            self.add(result_text(item, label_=True))
