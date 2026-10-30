"""The bodies of tool cards as node trees: one builder per tool (a port of the widget bodies in
widgets/transcript/bodies/*). Each builder takes the item, the context and the body block (for its
state and its handlers) and returns a node."""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from ... import theme
from ...lib.fmt import duration, plural, rel_path, truncate
from ...lib.tools_meta import str_
from ...lib.transcript_model import ToolItem
from ...lib.workflow import parse_workflow_meta, workflow_counts
from ...store import store
from ...widgets.transcript.bodies.code import ansi_html, compute_hunks, hunks_from_patch
from ...widgets.transcript.bodies.common import plain_error, split_error, unwrap_error
from ...widgets.transcript.ctx import ToolCtx
from .. import nodes as nd
from ..paint import Measurer, PaintState
from ..scene import Target
from .base import NodeBlock
from .codehtml import code_html, diff_html

Json = Dict[str, Any]
TAIL = 18
READ_MAX_LINES = 40


class ToolBodyBlock(NodeBlock):
    """A tool card's body: its node tree is built from the item each time the item changes."""

    KIND = "tool-body"
    __slots__ = ("item", "ctx", "state", "part")

    def __init__(self, item: ToolItem, ctx: ToolCtx, part: str = "body") -> None:
        self.item = item
        self.ctx = ctx
        self.state: Dict[str, Any] = {}
        self.part = part
        super().__init__(f"{part}:{item.id}", self._build, (0, 0, 0, 0), est_h=60)

    def sync(self, item: ToolItem, ctx: ToolCtx) -> bool:
        changed = item.rev != self.rev or ctx is not self.ctx
        self.item, self.ctx = item, ctx
        self.rev = item.rev
        if changed:
            self.rebuild()
        return changed

    def _build(self, m: Measurer) -> nd.Node:
        self.handlers = {}
        try:
            node = (build_reports if self.part == "reports" else build_body)(self.item, self.ctx, self, m)
        except Exception as ex:  # noqa: BLE001 - a body that cannot be built must not take the card down
            print(f"[canvas] body of {self.item.name} failed: {ex!r}")
            node = nd.note(f"Could not show this step: {ex!r}")
        return node or nd.Column()

    def toggle_state(self, key: str) -> Any:
        def fn(_t: Target, view: Any) -> None:
            self.state[key] = not self.state.get(key)
            self.rebuild()
            view.remeasure(self)

        return fn


# ---------------------------------------------------------------- shared pieces


def _esc(s: str) -> str:
    return html.escape(s)


def pre_html(text: str, limit: int = 60000, line_height: float = 1.55, size: int = 12) -> str:
    lh = int(size * line_height + 0.5)
    return f'<div style="white-space:pre-wrap;line-height:{lh}px">{_esc(truncate(text, limit)) or "&nbsp;"}</div>'


def json_pretty(v: Any) -> str:
    try:
        return json.dumps(v, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


def mono_text(body_html: str, color: str = "text", size: int = 12) -> nd.Text:
    return nd.Text(body_html, size, 400, color, mono=True, html=True)


def code_block(
    block: ToolBodyBlock,
    key: str,
    text: str,
    path: str = "",
    lang: str = "",
    start: int = 1,
    max_lines: int = 400,
    hl: Optional[int] = None,
) -> nd.Node:
    expanded = bool(block.state.get(key))
    body, total, shown = code_html(text, path, lang, start, max_lines, hl, expanded)
    col = nd.vstack(nd.Surface(mono_text(body), "inset", "light:hair", theme.R3, (0, 8, 0, 8)), spacing=4)
    if shown < total:
        col.add(nd.Button(f"Show all {total} lines", key, kind="more"))
        block.on(key, block.toggle_state(key))
    return col


def diff_block(
    block: ToolBodyBlock, key: str, hunks, path: str = "", numbers: bool = True, max_rows: int = 16
) -> nd.Node:
    expanded = bool(block.state.get(key))
    body, total, shown = diff_html(hunks, path, numbers, max_rows, expanded)
    col = nd.vstack(nd.Surface(mono_text(body), "inset", "light:hair", theme.R3, (0, 6, 0, 6)), spacing=4)
    if shown < total:
        col.add(nd.Button(f"Show all {total} lines", key, kind="more"))
        block.on(key, block.toggle_state(key))
    return col


def image_nodes(srcs: List[str], block: ToolBodyBlock) -> List[nd.Node]:
    out: List[nd.Node] = []
    for i, src in enumerate(srcs):
        pm = QPixmap()
        mt = re.match(r"^data:[^;]+;base64,(.*)$", src, re.S)
        if mt:
            try:
                pm.loadFromData(base64.b64decode(mt.group(1)))
            except (ValueError, TypeError):
                pass
        if pm.isNull():
            continue
        tid = f"img{i}"
        out.append(nd.Image(pm, tid))
        block.on(f"image:{tid}", lambda _t, _v, s=src: store.open_lightbox(s))
    return out


MCP_PATTERN = (
    r"mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|"
    r"token (has )?expired|not connected|connection (closed|refused|reset)|ECONNREFUSED|"
    r"MCP server .* (failed|unavailable)"
)
MCP_AUTHY = r"mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|token (has )?expired"
MCP_DOWN = r"not connected|connection (closed|refused|reset)|ECONNREFUSED|MCP server .* (failed|unavailable)"


def mcp_fix(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> Optional[nd.Node]:
    parts = item.name.split("__")
    server_key = parts[1] if len(parts) > 1 else ""
    rt = store.rt.get(ctx.session_id)
    live: List[Json] = list((rt.get("mcp") if rt else None) or [])
    text = item.result.text if item.result else ""
    match = next((s for s in live if s.get("name") == server_key), None) or next(
        (s for s in live if s.get("name") and s["name"] in text), None
    )
    name = (match or {}).get("name") or server_key
    status = (match or {}).get("status")
    authy = bool(re.search(MCP_AUTHY, text, re.I)) or status == "needs-auth"
    down = bool(re.search(MCP_DOWN, text, re.I)) or status == "failed"
    textual = bool(re.search(MCP_PATTERN, text, re.I))
    if not name or not textual or (not authy and not down) or (status == "connected" and not authy):
        return None
    if authy:
        block.on("mcp-fix", lambda _t, _v: store.set_dialog("mcpAuth", {"sessionId": ctx.session_id, "name": name}))
        btn = nd.Button(f"Sign in to {name}", "mcp-fix", kind="primary", size="sm")
    else:

        def retry(_t: Target, _v: Any) -> None:
            store.call("session.mcp.reconnect", {"id": ctx.session_id, "name": name}).then(
                lambda _r: store.toast(f"Reconnected {name} — ask Claude to try again", "success")
            ).catch(lambda _e: None)

        block.on("mcp-fix", retry)
        btn = nd.Button(f"Reconnect {name}", "mcp-fix", size="sm")
    return nd.hstack(btn, nd.faint("then ask Claude to try again"), spacing=8)


def error_node(
    block: ToolBodyBlock, text: str, tool_input: Optional[Json] = None, fix: Optional[nd.Node] = None
) -> nd.Node:
    parts = split_error(text)
    col = nd.vstack(nd.Text(parts["title"], theme.FS_UI, 400, "text_2", wrap=True), fix, spacing=4)
    has_input = isinstance(tool_input, dict) and len(tool_input) > 0
    if parts["rest"] or parts["title"].endswith("…") or has_input:
        detail = nd.vstack(spacing=8)
        if parts["rest"] or parts["title"].endswith("…"):
            detail.add(nd.pre(parts["full"], err=True))
        if has_input:
            detail.add(nd.label("Input"))
            detail.add(nd.pre(json_pretty(tool_input)))
        col.add(nd.Toggle("Details", "err-details", detail, bool(block.state.get("err-details"))))
        block.on("toggle:err-details", block.toggle_state("err-details"))
    return col


def result_node(
    item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock, label_: bool = False, tool_input: Optional[Json] = None
) -> Optional[nd.Node]:
    r = item.result
    if r is None:
        return nd.faint("Running…") if item.status == "running" else None
    sec = nd.vstack(spacing=8)
    if label_ and not r.is_error:
        sec.add(nd.label("Output"))
    if r.text:
        if r.is_error:
            plain = plain_error(r.text)
            if plain:
                sec.add(nd.Surface(nd.note(plain), "fill_1", None, theme.R1, (12, 8, 12, 8)))
            else:
                fix = mcp_fix(item, ctx, block) if item.name.startswith("mcp__") else None
                sec.add(error_node(block, r.text, tool_input, fix))
        else:
            sec.add(nd.pre(r.text))
    for n in image_nodes(r.images, block):
        sec.add(n)
    return sec


# ---------------------------------------------------------------- the bodies


def build_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock, m: Measurer) -> Optional[nd.Node]:
    name = item.name
    i = item.input or {}
    if name in ("Bash", "PowerShell"):
        return bash_body(item, ctx, block)
    if name == "Read":
        return read_body(item, ctx, block)
    if name in ("Edit", "MultiEdit"):
        return edit_body(item, ctx, block)
    if name == "Write":
        return write_body(item, ctx, block)
    if name == "NotebookEdit":
        return notebook_body(item, ctx, block)
    if name in ("Grep", "Glob"):
        return search_body(item, ctx, block)
    if name == "TodoWrite":
        return todo_body(i.get("todos"))
    if name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return task_body(item, ctx, block)
    if name in ("Agent", "Task"):
        return agent_top(item, ctx, block)
    if name in ("WebFetch", "WebSearch"):
        return web_body(item, ctx, block)
    if name == "ToolSearch":
        return result_node(item, ctx, block)
    if name in ("Workflow", "RunWorkflow"):
        return workflow_body(item, ctx, block)
    return generic_body(item, ctx, block)


def bash_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    s = item.structured if isinstance(item.structured, dict) else None
    r = item.result
    waiting = item.id in ctx.pending
    out = ""
    if s:
        stdout, stderr = str(s.get("stdout") or ""), str(s.get("stderr") or "")
        out = "\n".join(x for x in (stdout, stderr) if x) if stdout and stderr else (stdout or stderr)
    if not out and r:
        out = unwrap_error(r.text) if r.is_error else r.text
    exit_m = re.match(r"^(?:Error: )?Exit code (\d+)", r.text) if r and r.is_error else None
    exit_code = exit_m.group(1) if exit_m else None
    if exit_code:
        out = re.sub(r"^(?:Error: )?Exit code \d+\n?", "", out)
    lines = out.split("\n")
    command = str_(i, "command")
    well = nd.vstack(spacing=0)
    cmd = nd.hstack(
        nd.Text("$", 12.5, 400, "text_3", mono=True, wrap=False),
        nd.Text(
            f'<div style="white-space:pre-wrap;line-height:{int(theme.MONO_SIZE * 1.55 + 0.5)}px">{_esc(command)}</div>',
            int(theme.MONO_SIZE + 0.5),
            400,
            "text",
            mono=True,
            html=True,
        ),
        nd.Button("", "copy-cmd", icon="copy", kind="ghost", size="xs", icon_px=12),
        spacing=8,
        stretch=[1],
        pad=(12, 8, 6, 8),
        valign="top",
    )
    block.on("copy-cmd", lambda _t, view: view.copy_text(command))
    well.add(cmd)
    if item.status == "running" and r is None and not waiting:
        running = nd.faint(f"Running · {round(item.elapsed)}s" if item.elapsed else "Running…")
        well.add(nd.Column([running], pad=(12, 0, 12, 10)))
    if out:
        full = bool(block.state.get("full"))
        long = len(lines) > TAIL and not full
        shown = "\n".join(lines[-TAIL:]) if long else out
        outcol = nd.vstack(mono_text(ansi_html(shown), "text_2"), spacing=6, pad=(12, 2, 12, 11))
        if long:
            outcol.add(nd.Button(f"Show all {len(lines)} lines", "full", kind="more"))
            block.on("full", block.toggle_state("full"))
        well.add(outcol)
    bg_id = s.get("backgroundTaskId") if s else None
    interrupted = bool(s.get("interrupted")) if s else False
    interp = s.get("returnCodeInterpretation") if s else None
    task = item.task
    if exit_code or bg_id or interrupted or interp:
        well.add(nd.Hairline())
        foot = nd.hstack(spacing=8, pad=(12, 7, 12, 7))
        if exit_code:
            foot.add(nd.Badge(f"exit {exit_code}", "err"))
        if interrupted:
            foot.add(nd.Badge("interrupted"))
        if interp:
            foot.add(nd.Text(str(interp), theme.FS_MICRO, 400, "text_3", wrap=True))
            foot.stretch = [len(foot.children) - 1]
        if bg_id:
            status = f" · {task.status}" if task and task.status else ""
            foot.add(
                nd.Text(
                    f"Background task <span style=\"font-family:'{theme.font_mono().family()}'\">{_esc(str(bg_id))}</span>{_esc(status)}",
                    theme.FS_MICRO,
                    400,
                    "text_3",
                    html=True,
                    wrap=False,
                )
            )
        if task and task.output_file:
            foot.add(nd.Button("Output file", "output-file", icon="file-text", kind="ghost", size="sm"))
            block.on("output-file", lambda _t, _v, p=task.output_file: store.open_file(p))
        well.add(foot)
    git = s.get("gitOperation") if s else None
    if isinstance(git, dict) and git.get("commit"):
        well.add(nd.Hairline())
        well.add(nd.Column([nd.faint(f"Committed {str(git['commit'])[:8]}", theme.FS_MICRO)], pad=(12, 7, 12, 7)))
    return nd.Surface(well, "inset", "light:hair", theme.R3, (0, 0, 0, 0))


def strip_line_numbers(text: str):
    lines = text.split("\n")
    numbered = [ln for ln in lines if re.match(r"^\s*\d+[\t→]", ln)]
    if len(numbered) < len(lines) * 0.6:
        return text, 1
    mt = re.match(r"^\s*(\d+)", lines[0])
    return "\n".join(re.sub(r"^\s*\d+[\t→]", "", ln) for ln in lines), int(mt.group(1)) if mt else 1


def read_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
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
    if r and r.is_error:
        return result_node(item, ctx, block) or nd.Column()
    if (s and s.get("type") == "image") or (r and r.images):
        return result_node(item, ctx, block) or nd.Column()
    bar = nd.hstack(nd.Link(rel_path(path, ctx.cwd), "open-path", mono=True), nd.Spacer(), spacing=8, stretch=[1])
    block.on("link:open-path", lambda _t, _v: store.open_file(path, start))
    total = file.get("totalLines") if file else None
    num = file.get("numLines") if file else None
    if isinstance(total, (int, float)) and isinstance(num, (int, float)) and num < total - 1:
        bar.add(nd.faint(f"Lines {start}–{start + int(num) - 1} of {int(total)}", theme.FS_MICRO))
    col = nd.vstack(bar, spacing=8)
    if content:
        col.add(code_block(block, "all", content, path=path, start=start, max_lines=READ_MAX_LINES))
    else:
        col.add(nd.faint("Waiting…"))
    return col


def edit_hunks(item: ToolItem):
    i: Json = item.input or {}
    s = item.structured if isinstance(item.structured, dict) else None
    patch = s.get("structuredPatch") if s else None
    if isinstance(patch, list) and patch:
        return hunks_from_patch(patch), True
    edits = i.get("edits")
    if isinstance(edits, list):
        out = []
        for e in edits:
            if isinstance(e, dict):
                out.extend(compute_hunks(str(e.get("old_string") or ""), str(e.get("new_string") or "")))
        return out, False
    if "old_string" in i or "new_string" in i:
        return compute_hunks(str(i.get("old_string") or ""), str(i.get("new_string") or "")), False
    return [], False


def edit_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    path = str_(i, "file_path")
    col = nd.vstack(spacing=8)
    if item.status == "streaming":
        edits = i.get("edits") if isinstance(i.get("edits"), list) else None
        if isinstance(i.get("new_string"), str):
            draft = i["new_string"]
        elif edits:
            last = edits[-1] if isinstance(edits[-1], dict) else {}
            draft = str(last.get("new_string") or "")
        else:
            draft = ""
        col.add(nd.faint("Writing the change…"))
        if draft:
            col.add(code_block(block, "draft", draft, path=path, max_lines=16))
        return col
    hunks, numbered = edit_hunks(item)
    if i.get("replace_all"):
        col.add(nd.note("Replaced every occurrence"))
    if hunks:
        col.add(diff_block(block, "diff", hunks, path=path, numbers=numbered, max_rows=16))
    if item.result and item.result.is_error:
        col.add(result_node(item, ctx, block))
    return col


def write_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    s = item.structured if isinstance(item.structured, dict) else None
    path = str_(i, "file_path")
    col = nd.vstack(spacing=8)
    patch = s.get("structuredPatch") if s and s.get("type") == "update" else None
    if isinstance(patch, list) and patch:
        col.add(diff_block(block, "diff", hunks_from_patch(patch), path=path, max_rows=16))
    elif isinstance(i.get("content"), str):
        col.add(code_block(block, "content", i["content"], path=path, max_lines=16))
    if item.result and item.result.is_error:
        col.add(result_node(item, ctx, block))
    return col


def notebook_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    mode = str_(i, "edit_mode") or "replace"
    cell = str_(i, "cell_id")
    kind = str_(i, "cell_type")
    words = {"replace": "Replace", "insert": "Insert", "delete": "Delete"}.get(mode, mode.capitalize())
    col = nd.vstack(nd.note(f"{words} {kind + ' ' if kind else ''}cell{(' ' + cell) if cell else ''}"), spacing=8)
    src = i.get("new_source")
    if isinstance(src, str) and src and mode != "delete":
        col.add(code_block(block, "src", src, lang="markdown" if kind == "markdown" else "python", max_lines=16))
    col.add(result_node(item, ctx, block))
    return col


def search_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    s = item.structured if isinstance(item.structured, dict) else None
    r = item.result
    if r and r.is_error:
        return result_node(item, ctx, block) or nd.Column()
    files = s.get("filenames") if s else None
    files = [str(f) for f in files] if isinstance(files, list) else []
    content = s.get("content") if s else None
    if isinstance(content, str) and content:
        return nd.pre(truncate(content, 40000))
    if not files:
        if r and r.text:
            return nd.pre(truncate(r.text, 40000))
        return nd.faint("No matches" if r else "Searching…")
    n = s.get("numFiles") if s else None
    count = int(n) if isinstance(n, (int, float)) else len(files)
    col = nd.vstack(nd.faint(plural(count, "file") + (" (truncated)" if s and s.get("truncated") else "")), spacing=1)
    for k, f in enumerate(files[:200]):
        full = f if f.startswith("/") or (len(f) > 1 and f[1] == ":") else f"{ctx.cwd}/{f}"
        tid = f"file{k}"
        col.add(nd.Link(rel_path(f, ctx.cwd), tid, mono=True))
        block.on(f"link:{tid}", lambda _t, _v, p=full: store.open_file(p))
    return col


def _todo_box(status: str):
    def paint(p: QPainter, r, st: PaintState) -> None:
        t = st.t
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rr = QRectF(r.x() + 0.75, r.y() + 0.75, 13.5, 13.5)
        if status == "completed":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("fill_3"))
            p.drawRoundedRect(rr, 4, 4)
            p.drawPixmap(r.x() + 2, r.y() + 2, st.m.icon("check", QColor(t.text_2), 11))
        elif status == "in_progress":
            p.setPen(QPen(t.color("text_2"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rr, 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("text"))
            p.drawEllipse(QRectF(r.x() + 5, r.y() + 5, 5, 5))
        else:
            p.setPen(QPen(t.color("hair_3"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rr, 4, 4)

    return nd.Painted(paint, 15, 15)


def todo_body(todos: Any) -> nd.Node:
    col = nd.vstack(spacing=2)
    for t in todos if isinstance(todos, list) else []:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "pending")
        text = str((t.get("activeForm") or t.get("content")) if status == "in_progress" else t.get("content") or "")
        color = "text_3" if status == "completed" else "text" if status == "in_progress" else "text_2"
        col.add(
            nd.hstack(
                _todo_box(status),
                nd.Text(
                    text,
                    theme.FS_UI,
                    500 if status == "in_progress" else 400,
                    color,
                    wrap=True,
                    strike=status == "completed",
                ),
                spacing=10,
                stretch=[1],
                valign="top",
                pad=(0, 3, 0, 3),
            )
        )
    return col


TASK_FIELDS = ("taskId", "subject", "status", "owner", "description", "activeForm", "blocks", "blockedBy", "metadata")


def task_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    col = nd.vstack(spacing=4)
    t = theme.tokens()
    for k in TASK_FIELDS:
        v = i.get(k)
        if v in (None, "", [], {}):
            continue
        text = v if isinstance(v, str) else ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        if k == "status":
            text = text.replace("_", " ")
        col.add(
            nd.Text(
                f'<span style="color:{t.text_3}">{k}</span>&nbsp; {_esc(text)}',
                theme.FS_SMALL,
                400,
                "text_2",
                html=True,
                wrap=True,
            )
        )
    r = item.result
    if r and not r.is_error and r.text:
        col.add(nd.pre(r.text))
    elif r and r.is_error:
        col.add(result_node(item, ctx, block))
    elif item.status == "running":
        col.add(nd.note("Working…"))
    return col


def text_of(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def agent_report(item: ToolItem) -> str:
    s = item.structured if isinstance(item.structured, dict) else None
    if item.task and item.task.summary and item.status != "running":
        return ""
    if s and not s.get("isAsync"):
        return text_of(s.get("content")) or (item.result.text if item.result else "")
    if not (s and s.get("isAsync")):
        return item.result.text if item.result else ""
    return ""


def agent_top(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    s = item.structured if isinstance(item.structured, dict) else None
    usage = (item.task.usage if item.task else None) or (s.get("usage") if s else None)
    running = item.status in ("running", "background", "streaming")
    meta = nd.hstack(spacing=8)
    if str_(i, "subagent_type"):
        meta.add(nd.Badge(str_(i, "subagent_type")))
    if str_(i, "model"):
        meta.add(nd.Badge(str_(i, "model")))
    if str_(i, "isolation") == "worktree":
        meta.add(nd.Badge("worktree"))
    if isinstance(usage, dict):
        uses = usage.get("tool_uses", usage.get("toolUses", 0)) or 0
        total = usage.get("total_tokens", usage.get("totalTokens", 0)) or 0
        ms = usage.get("duration_ms")
        from ...lib.fmt import tokens as fmt_tokens

        meta.add(
            nd.faint(
                f"{plural(uses, 'tool use')} · {fmt_tokens(total)} tokens" + (f" · {duration(ms)}" if ms else ""),
                theme.FS_MICRO,
            )
        )
    if running:
        meta.add(nd.Spinner(12))
        meta.add(nd.faint("Working in the background" if item.status == "background" else "Working", theme.FS_MICRO))
    col = nd.vstack(meta if meta.children else None, spacing=8)
    prompt = str_(i, "prompt")
    if prompt:
        col.add(
            nd.Toggle(
                "Instructions",
                "prompt",
                nd.Surface(nd.note(prompt), "inset", "hair", theme.R2, (12, 10, 12, 10)),
                bool(block.state.get("prompt")),
            )
        )
        block.on("toggle:prompt", block.toggle_state("prompt"))
    agent_id = (s.get("agentId") if s else None) or item.task_id
    if not item.children and not running and agent_id and not block.state.get("loaded"):
        col.add(nd.Button("Show the agent's steps", "load", icon="download", kind="ghost", size="sm"))

        def load(_t: Target, view: Any) -> None:
            def ok(msgs: Any) -> None:
                from ...lib.transcript_model import TranscriptModel

                mdl = TranscriptModel()
                mdl.push_all(msgs or [])
                block.state["loaded"] = mdl
                block.rebuild()
                view.remeasure(block)
                owner = block.parent
                if owner is not None and hasattr(owner, "set_loaded_children"):
                    view.mutate(lambda: owner.set_loaded_children(mdl.items))

            store.call("session.subagent", {"id": ctx.session_id, "agentId": agent_id}).then(ok).catch(lambda _e: None)

        block.on("load", load)
    return col


def build_reports(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock, m: Measurer) -> Optional[nd.Node]:
    """The agent's summary and report, unless its last words already say the same."""
    children = item.children or (block.state.get("children") or [])
    summary = item.task.summary if item.task else None
    report = agent_report(item)
    col = nd.vstack(spacing=8)

    def ends_with(text: str) -> bool:
        last = next((c for c in reversed(children) if c.kind == "text"), None)
        return bool(last is not None and last.text.strip() == text.strip())

    for text in (summary, report):
        if text and not ends_with(text):
            col.add(
                nd.Surface(nd.Markdown(text, compact=True, cwd=ctx.cwd), "fill_1", None, theme.R3, (14, 10, 14, 10))
            )
    return col if col.children else None


def web_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    col = nd.vstack(spacing=8)
    if str_(i, "prompt"):
        col.add(nd.note(str_(i, "prompt")))
    r = item.result
    if r:
        col.add(nd.Markdown(truncate(r.text, 20000), compact=True, cwd=ctx.cwd))
    else:
        col.add(nd.faint("Waiting for the result…"))
    return col


def workflow_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    script = str_(item.input, "script")
    meta = parse_workflow_meta(script)
    description = meta.description or str_(item.input, "description")
    col = nd.vstack(spacing=6)
    if description:
        col.add(nd.note(description))
    rt = store.rt.get(ctx.session_id)
    tasks = [t for t in ((rt.get("tasks") if rt else None) or []) if isinstance(t, dict)]
    task = next(
        (
            t
            for t in tasks
            if t.get("toolUseId") == item.id
            or (t.get("type") == "local_workflow" and t.get("status") == "running" and not t.get("toolUseId"))
        ),
        None,
    )
    wf = (task or {}).get("workflow") or {}
    counts = workflow_counts(wf.get("agents") or [])
    phases = max(len(meta.phases), len([p for p in wf.get("phases") or [] if p]))
    parts = []
    if phases:
        parts.append(f"{phases} phase{'s' if phases > 1 else ''}")
    if counts.total:
        parts.append(f"{counts.done} of {counts.total} agents done")
    if counts.running:
        parts.append(f"{counts.running} running")
    if counts.error:
        parts.append(f"{counts.error} failed")
    row = nd.hstack(
        nd.faint(" · ".join(parts)) if parts else None,
        nd.Button("Open in Tasks", "tasks", icon="panel-right", size="xs"),
        spacing=10,
    )
    block.on("tasks", lambda _t, _v: store.set_ui(panel=True, panel_tab="tasks"))
    col.add(row)
    if item.result and item.result.text:
        text = truncate(item.result.text, 30000)
        col.add(
            nd.Toggle(
                "Error" if item.result.is_error else "Result",
                "result",
                nd.Markdown(text, compact=True, cwd=ctx.cwd),
                bool(block.state.get("result")),
            )
        )
        block.on("toggle:result", block.toggle_state("result"))
    return col


def generic_body(item: ToolItem, ctx: ToolCtx, block: ToolBodyBlock) -> nd.Node:
    i = item.input or {}
    s = item.structured
    r = item.result
    is_err = bool(r and r.is_error)
    col = nd.vstack(spacing=8)
    if is_err:
        col.add(result_node(item, ctx, block, tool_input=i))
    if not is_err and i:
        col.add(nd.label("Input"))
        col.add(nd.pre(json_pretty(i)))
    if isinstance(s, (dict, list)) and s and not (r and r.text):
        col.add(nd.label("Result"))
        col.add(nd.pre(json_pretty(s)))
    if not is_err:
        col.add(result_node(item, ctx, block, label_=True))
    return col
