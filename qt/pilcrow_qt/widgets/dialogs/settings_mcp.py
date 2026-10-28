"""Settings → MCP servers (web/src/components/settings/McpPage.tsx): the servers Claude Code is
configured with, their live status in the open session, and the form that adds one."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QStackedWidget, QWidget

from ... import icons, theme
from ...lib.fmt import err_text, tildify
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, Spinner, button, label
from ..ui.menu import show_menu
from ..ui.signals import bind
from .settings_skills import active_cwd, search_box
from .settings_ui import (
    Intro,
    Sheet,
    chip,
    clear_layout,
    combo,
    combo_value,
    elide,
    field,
    hint,
    home,
    icon_box,
    line_edit,
    mono_label,
    text_area,
    two,
    vbox,
)

Json = Dict[str, Any]
SCOPE_LABEL = {"user": "All projects", "project": "Project (.mcp.json)", "local": "This project, just you"}
COLS = (36, 0, 118, 150, 28)


def tool_count(s: Json) -> int:
    """Claude Code reports a server's tools as a count or as the list itself, depending on version."""
    t = s.get("tools")
    return t if isinstance(t, int) else len(t) if isinstance(t, list) else 0


def status_meta(st: Optional[str]) -> Tuple[str, str, str]:
    """(label, icon, tone) for a live status."""
    return {
        "connected": ("Connected", "circle-check", "ok"),
        "needs-auth": ("Needs sign-in", "key-round", "warn"),
        "failed": ("Failed", "circle-alert", "err"),
        "pending": ("Connecting", "circle-dashed", "muted"),
        "disabled": ("Disabled", "power", "muted"),
    }.get(st or "", ("Not running", "circle-dashed", "muted"))


def describe_config(c: Json) -> str:
    """One line that says what the server is: its URL, or its command."""
    if c.get("type") == "stdio":
        return " ".join([str(c.get("command") or ""), *[str(a) for a in (c.get("args") or [])]]).strip()
    return str(c.get("url") or "")


class McpPage(QWidget):
    def __init__(self, sheet: Sheet, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        self.sid = store.active_id
        self.cwd = active_cwd()
        self.data: Optional[Json] = None
        self.lay = vbox(self, 14, (0, 6, 0, 0))

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.search = search_box("Search servers")
        self.search.textChanged.connect(lambda _t: self.render())
        bl.addWidget(self.search)
        bl.addStretch(1)
        self.spinner = Spinner(13)
        self.spinner.hide()
        bl.addWidget(self.spinner)
        self.refresh_btn = IconButton("refresh-cw", "Refresh", 13)
        self.refresh_btn.clicked.connect(self.load)
        bl.addWidget(self.refresh_btn)
        self.add_btn = button("Add server", kind="primary", size="sm", icon="plus", on_click=self.add_server)
        self.add_btn.setAccessibleName("Add server")
        bl.addWidget(self.add_btn)
        self.lay.addWidget(bar)

        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 14)
        self.lay.addWidget(self.list_w)
        bind(store.rt_changed, self, lambda sid: sid == self.sid and self.render())
        self.load()

    # ---- data
    @property
    def rt(self):
        return store.rt_of(self.sid)

    def live(self) -> List[Json]:
        r = self.rt
        return list(r.get("mcp") or []) if r is not None else []

    def load(self) -> None:
        self.spinner.show()

        def ok(d: Json) -> None:
            self.spinner.hide()
            self.data = d or {"file": "", "servers": []}
            self.render()
            r = self.rt
            if self.sid and r is not None and r.live:
                store.call("session.mcpStatus", {"id": self.sid}, quiet=True).catch(lambda _e: None)

        def bad(e: Any) -> None:
            self.spinner.hide()
            store.toast(err_text(e) or "Could not read the MCP configuration", "error")

        store.call("mcp.list", {"cwd": self.cwd}, quiet=True).then(ok).catch(bad)

    def servers(self) -> List[Json]:
        n = self.search.text().strip().lower()
        lst = list((self.data or {}).get("servers") or [])
        # Servers Claude Code reports but no file we read declares (plugins, managed policy) still get a row.
        extra = [
            {
                "name": s.get("name"),
                "scope": s.get("scope") or "user",
                "source": "",
                "config": {"type": "http"},
                "raw": None,
            }
            for s in self.live()
            if not any(e.get("name") == s.get("name") for e in lst)
        ]
        every = lst + extra
        if not n:
            return every
        return [
            s
            for s in every
            if n in str(s.get("name") or "").lower() or n in describe_config(s.get("config") or {}).lower()
        ]

    # ---- actions
    def needs_session(self, what: str) -> bool:
        r = self.rt
        if not self.sid or r is None or not r.live:
            store.toast(f"{what} needs a session with Claude Code running. Send a message in a session first.", "info")
            return False
        return True

    def reconnect(self, name: str) -> None:
        if not self.needs_session("Reconnecting"):
            return
        store.call("session.mcp.reconnect", {"id": self.sid, "name": name}).then(
            lambda _r: store.toast(f"Reconnected {name}", "success")
        )

    def toggle(self, name: str, enabled: bool) -> None:
        if not self.needs_session("Enabling" if enabled else "Disabling"):
            return
        store.call("session.mcp.toggle", {"id": self.sid, "name": name, "enabled": enabled}, quiet=True).catch(
            lambda _e: None
        )

    def sign_in(self, name: str) -> None:
        if not self.needs_session("Signing in"):
            return
        store.set_dialog("mcpAuth", {"sessionId": self.sid, "name": name})

    def sign_out(self, name: str) -> None:
        def go(_c: bool) -> None:
            if not self.needs_session("Signing out"):
                return
            store.call("session.mcp.clearAuth", {"id": self.sid, "name": name}, quiet=True).catch(lambda _e: None)
            store.toast(f"Signed out of {name}", "success")

        store.confirm_action(
            ConfirmSpec(
                title=f"Sign out of {name}?",
                body="The saved token is removed. Claude asks you to sign in again the next time it uses this server.",
                confirm="Sign out",
                on_confirm=go,
            )
        )

    def remove(self, s: Json) -> None:
        src = tildify(str(s.get("source") or ""), home())
        body = (
            f"It is deleted from {src}, which is shared with everyone who uses this repository."
            if s.get("scope") == "project"
            else f"It is removed from {src}. Running sessions keep it until they restart."
        )

        def go(_c: bool) -> None:
            def done(lst: Json) -> None:
                self.data = lst or self.data
                store.toast(f"Removed {s.get('name')}", "success")
                self.render()

            store.call("mcp.remove", {"scope": s.get("scope"), "cwd": self.cwd, "name": s.get("name")}).then(done)

        store.confirm_action(
            ConfirmSpec(
                title=f"Remove {s.get('name')}?", body=body, confirm="Remove server", danger=True, on_confirm=go
            )
        )

    def approve(self, s: Json, approved: bool) -> None:
        def done(lst: Json) -> None:
            self.data = lst or self.data
            self.render()

        store.call("mcp.approve", {"cwd": self.cwd, "name": s.get("name"), "approved": approved}).then(done)

    def add_server(self) -> None:
        def added(lst: Json) -> None:
            self.data = lst or self.data
            self.render()

        AddMcpSheet(self.sheet.parentWidget().window(), self.cwd, on_added=added).open()

    def show_tools(self, s: Json) -> None:
        if self.sid:
            ToolsSheet(self.sheet.parentWidget().window(), s, self.sid).open()

    # ---- rendering
    def render(self) -> None:
        clear_layout(self.list_lay)
        if self.data is None:
            return
        q = self.search.text().strip()
        servers = self.servers()
        live = self.live()
        by_name = {s.get("name"): s for s in live}
        if not servers and not q:
            intro = Intro(
                "Give Claude more tools",
                "An MCP server lets Claude use another system: an issue tracker, a database, a design tool, your own scripts. Add one by URL, or as a command Pilcrow starts on your machine.",
            )
            intro.add_action(button("Add server", kind="primary", size="sm", icon="plus", on_click=self.add_server))
            self.list_lay.addWidget(intro)
        if servers:
            table = QWidget()
            grid = QGridLayout(table)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(0)
            for i, w in enumerate(COLS):
                if w:
                    grid.setColumnMinimumWidth(i, w)
            grid.setColumnStretch(1, 1)
            for i, text in ((0, "Server"), (2, "Scope"), (3, "Status")):
                h = hint(text, wrap=False)
                h.setContentsMargins(0, 0, 0, 6)
                grid.addWidget(h, 0, i, 1, 2 if i == 0 else 1)
            rown = 1
            for s in servers:
                rown = self._row(grid, rown, s, by_name.get(s.get("name")))
            self.list_lay.addWidget(table)
        foot = f"{'Status from the current session.' if live else 'Statuses appear once a session has Claude Code running.'} Config: {tildify(str(self.data.get('file') or ''), home())}"
        if self.cwd:
            foot += f" and {tildify(self.cwd, home())}/.mcp.json"
        self.list_lay.addWidget(hint(foot))

    def _row(self, grid: QGridLayout, rown: int, s: Json, st: Optional[Json]) -> int:
        t = theme.tokens()
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.blend(t.hair, t.raised).name()};")
        grid.addWidget(line, rown, 0, 1, 5)
        rown += 1
        cfg = s.get("config") or {}
        status = (st or {}).get("status")
        lab, ico, tone = status_meta(status)
        grid.addWidget(icon_box("terminal" if cfg.get("type") == "stdio" else "globe"), rown, 0)
        main = QWidget()
        ml = vbox(main, 2)
        name = label(str(s.get("name") or ""))
        name.setStyleSheet("font-weight: 600;")
        ml.addWidget(name)
        desc_text = describe_config(cfg)
        desc = elide(
            mono_label(
                desc_text
                or ("Provided by a plugin or policy" if s.get("scope") == "user" else str(s.get("source") or ""))
            )
        )
        desc.setStyleSheet("font-size: 11.5px;")
        desc.setToolTip(desc_text or str(s.get("source") or ""))
        ml.addWidget(desc)
        grid.addWidget(main, rown, 1)
        scope = QWidget()
        sl = QHBoxLayout(scope)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        sl.addWidget(chip({"user": "All projects", "project": "Project"}.get(str(s.get("scope")), "Local")))
        if s.get("scope") == "project" and s.get("approved") is False:
            rej = chip("rejected", tone="warn")
            rej.setToolTip("You rejected this project server; Claude does not start it")
            sl.addWidget(rej)
        sl.addStretch(1)
        grid.addWidget(scope, rown, 2)
        stat = QWidget()
        stl = QHBoxLayout(stat)
        stl.setContentsMargins(0, 0, 0, 0)
        stl.setSpacing(6)
        name_s = str(s.get("name") or "")
        if status == "needs-auth":
            b = button("Sign in", size="sm", icon="key-round", on_click=lambda: self.sign_in(name_s))
            b.setAccessibleName(f"Sign in to {name_s}")
            stl.addWidget(b)
        elif status == "failed":
            b = button(
                "Retry",
                size="sm",
                icon="refresh-cw",
                on_click=lambda: self.reconnect(name_s),
                tooltip=str((st or {}).get("error") or ""),
            )
            b.setAccessibleName(f"Retry {name_s}")
            stl.addWidget(b)
        else:
            color = {"ok": t.ok, "warn": t.accent_text, "err": t.err}.get(tone, t.text_3)
            ic = QLabel()
            ic.setPixmap(icons.pixmap(ico, color, 14))
            stl.addWidget(ic)
            lb = label(lab, size="small")
            lb.setStyleSheet(f"color: {t.text_2 if tone == 'ok' else color};")
            stl.addWidget(lb)
            if status == "connected" and st is not None and tool_count(st) > 0:
                tools = button(f"{tool_count(st)} tools", kind="link", size="sm", on_click=lambda: self.show_tools(s))
                tools.setAccessibleName(f"Show {name_s} tools")
                stl.addWidget(tools)
        stl.addStretch(1)
        stat.setToolTip(
            str((st or {}).get("error") or ("" if status else "Status is known once a session has Claude Code running"))
        )
        grid.addWidget(stat, rown, 3)
        more = IconButton("ellipsis", f"More for {name_s}", 14)
        more.clicked.connect(lambda: self._row_menu(s, st, more))
        grid.addWidget(more, rown, 4)
        for cell in (main, scope, stat):
            cell.setContentsMargins(0, 10, 0, 10)
        rown += 1
        if status == "failed" and (st or {}).get("error"):
            err = label(str(st["error"]), tone="err", size="small", wrap=True)
            err.setContentsMargins(0, 0, 0, 8)
            grid.addWidget(err, rown, 1, 1, 4)
            rown += 1
        return rown

    def _row_menu(self, s: Json, st: Optional[Json], anchor: QWidget) -> None:
        name = str(s.get("name") or "")
        status = (st or {}).get("status")
        items: List[Json] = []
        if status == "connected":
            items.append(
                {
                    "label": f"Show {tool_count(st or {})} tools",
                    "icon": "wrench",
                    "on_select": lambda: self.show_tools(s),
                }
            )
        items.append({"label": "Reconnect", "icon": "refresh-cw", "on_select": lambda: self.reconnect(name)})
        if status == "disabled":
            items.append({"label": "Enable", "icon": "power", "on_select": lambda: self.toggle(name, True)})
        else:
            items.append(
                {"label": "Disable for this session", "icon": "power", "on_select": lambda: self.toggle(name, False)}
            )
        items.append({"separator": True})
        if (s.get("config") or {}).get("type") != "stdio":
            items += [
                {"label": "Sign in…", "icon": "key-round", "on_select": lambda: self.sign_in(name)},
                {"label": "Sign out", "icon": "log-out", "on_select": lambda: self.sign_out(name)},
                {"separator": True},
            ]
        if s.get("scope") == "project":
            if s.get("approved") is False:
                items.append(
                    {
                        "label": "Allow this project server",
                        "icon": "shield-check",
                        "on_select": lambda: self.approve(s, True),
                    }
                )
            else:
                items.append(
                    {
                        "label": "Reject this project server",
                        "icon": "shield-off",
                        "on_select": lambda: self.approve(s, False),
                    }
                )
        items.append(
            {
                "label": "Show config file",
                "icon": "file-json",
                "disabled": not s.get("source"),
                "on_select": lambda: store.call("shell.openPath", {"path": s.get("source")}),
            }
        )
        if s.get("raw") is not None:
            items += [
                {"separator": True},
                {"label": "Remove…", "icon": "trash", "danger": True, "on_select": lambda: self.remove(s)},
            ]
        show_menu(items, anchor, align_right=True)


NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
JSON_PLACEHOLDER = '{\n  "mcpServers": {\n    "linear": { "type": "http", "url": "https://mcp.linear.app/mcp" }\n  }\n}'


class AddMcpSheet(Sheet):
    """``AddMcpDialog``: name, type (HTTP / SSE / command), its address or command, and the scope;
    or a pasted JSON block that fills the form."""

    def __init__(self, window: QWidget, cwd: str, on_added: Any) -> None:
        super().__init__(window, "", "Add an MCP server", 540, on_submit=self.submit)
        self.cwd, self.on_added = cwd, on_added
        self.busy = False
        self.stack = QStackedWidget()

        form = QWidget()
        fl = vbox(form, 14)
        self.name = line_edit("linear", name="Name")
        self.name.textChanged.connect(lambda _t: self._validate())
        self.type = combo(
            [("http", "HTTP"), ("sse", "SSE"), ("stdio", "Command")],
            "http",
            hints={
                "http": "Remote server; signs in with OAuth when needed",
                "sse": "Older remote servers",
                "stdio": "A program Claude Code starts on this machine",
            },
            name="Type",
        )
        self.type.currentIndexChanged.connect(lambda _i: self._type_changed())
        fl.addWidget(two(field("Name", self.name), field("Type", self.type)))
        self.remote = QWidget()
        rl = vbox(self.remote, 14)
        self.url = line_edit("https://mcp.example.com/mcp", mono=True, name="URL")
        self.url.textChanged.connect(lambda _t: self._validate())
        rl.addWidget(field("URL", self.url))
        self.headers = text_area("Authorization: Bearer …\nOne-Per-Line: …", rows=2, mono=True, name="Headers")
        hf = field("Headers", self.headers)
        hf.layout().addWidget(
            hint("Leave empty for servers that sign you in with OAuth; Pilcrow asks when the server does.")
        )
        rl.addWidget(hf)
        fl.addWidget(self.remote)
        self.local = QWidget()
        ll = vbox(self.local, 14)
        self.command = line_edit("npx", mono=True, name="Command")
        self.command.textChanged.connect(lambda _t: self._validate())
        ll.addWidget(field("Command", self.command))
        self.args = line_edit("-y @modelcontextprotocol/server-filesystem /path", mono=True, name="Arguments")
        ll.addWidget(field("Arguments", self.args))
        self.env = text_area("API_KEY=…\nONE_PER_LINE=…", rows=2, mono=True, name="Environment variables")
        ll.addWidget(field("Environment variables", self.env))
        self.local.hide()
        fl.addWidget(self.local)
        scope_hints = {
            "user": "Saved in ~/.claude.json",
            "local": f"For {tildify(cwd, home())}, not shared" if cwd else "Open a session first",
            "project": "Committed with the repository" if cwd else "Open a session first",
        }
        self.scope = combo(
            [("user", SCOPE_LABEL["user"]), ("local", SCOPE_LABEL["local"]), ("project", SCOPE_LABEL["project"])],
            "user",
            hints=scope_hints,
            name="Available in",
        )
        if not cwd:
            model = self.scope.model()
            for i in (1, 2):
                item = model.item(i)
                if item is not None:
                    item.setEnabled(False)
        fl.addWidget(field("Available in", self.scope))
        self.stack.addWidget(form)

        jsonw = QWidget()
        jl = vbox(jsonw, 14)
        jl.addWidget(hint("Paste what the server’s docs give you: a mcpServers block, or a single server’s config."))
        self.json = text_area(JSON_PLACEHOLDER, rows=9, mono=True, name="Server JSON")
        self.json.textChanged.connect(self._validate)
        jl.addWidget(self.json)
        self.stack.addWidget(jsonw)
        self.stack.setFixedHeight(form.sizeHint().height())
        self.add(self.stack)

        self.mode_btn = button("Paste JSON instead", kind="ghost", size="sm", on_click=self._toggle_mode)
        self.add_foot(self.mode_btn)
        self.foot_stretch()
        self.add_foot(button("Cancel", on_click=self.dismiss))
        self.submit_btn = button("Add server", kind="primary", on_click=self.submit)
        self.submit_btn.setEnabled(False)
        self.add_foot(self.submit_btn)
        self.use_btn = button("Use this", kind="primary", on_click=self.from_json)
        self.use_btn.setEnabled(False)
        self.use_btn.hide()
        self.add_foot(self.use_btn)

    @property
    def json_mode(self) -> bool:
        return self.stack.currentIndex() == 1

    def _toggle_mode(self) -> None:
        self.stack.setCurrentIndex(0 if self.json_mode else 1)
        self.mode_btn.setText("Back to the form" if self.json_mode else "Paste JSON instead")
        self.submit_btn.setVisible(not self.json_mode)
        self.use_btn.setVisible(self.json_mode)
        self.stack.setFixedHeight(self.stack.currentWidget().sizeHint().height())
        self.schedule_relayout()
        (self.json if self.json_mode else self.name).setFocus()

    def _type_changed(self) -> None:
        stdio = combo_value(self.type) == "stdio"
        self.remote.setVisible(not stdio)
        self.local.setVisible(stdio)
        self.stack.setFixedHeight(self.stack.currentWidget().sizeHint().height())
        self._validate()
        self.schedule_relayout()

    def valid(self) -> bool:
        if not NAME_RE.match(self.name.text().strip()):
            return False
        if combo_value(self.type) == "stdio":
            return bool(self.command.text().strip())
        return re.match(r"^https?://", self.url.text().strip(), re.I) is not None

    def _validate(self) -> None:
        self.submit_btn.setEnabled(self.valid() and not self.busy)
        self.use_btn.setEnabled(bool(self.json.toPlainText().strip()))

    def from_json(self) -> None:
        """A pasted ``mcpServers`` block, ``{name, config}`` or a bare config fills the form."""
        try:
            o = json.loads(self.json.toPlainText())
            if not isinstance(o, dict):
                raise ValueError("Not an object")
            if isinstance(o.get("mcpServers"), dict) and o["mcpServers"]:
                n, cfg = next(iter(o["mcpServers"].items()))
            elif o.get("name") and isinstance(o.get("config"), dict):
                n, cfg = o["name"], o["config"]
            else:
                n, cfg = self.name.text(), o
            if not n:
                raise ValueError("No server name")
            c = cfg if isinstance(cfg, dict) else {}
            self.name.setText(str(n))
            if c.get("command"):
                self.type.setCurrentIndex(self.type.findData("stdio"))
                self.command.setText(str(c["command"]))
                self.args.setText(" ".join(str(a) for a in (c.get("args") or [])))
                self.env.setPlainText("\n".join(f"{k}={v}" for k, v in (c.get("env") or {}).items()))
            else:
                self.type.setCurrentIndex(self.type.findData("sse" if c.get("type") == "sse" else "http"))
                self.url.setText(str(c.get("url") or ""))
                self.headers.setPlainText("\n".join(f"{k}: {v}" for k, v in (c.get("headers") or {}).items()))
            self._toggle_mode()
        except Exception as e:  # noqa: BLE001 — whatever was pasted, say why it did not parse
            store.toast(f"Could not read that JSON: {err_text(e)}", "error")

    def submit(self) -> None:
        if self.json_mode:
            self.from_json()
            return
        if not self.valid() or self.busy:
            return
        self.busy = True
        self.submit_btn.setEnabled(False)
        name = self.name.text().strip()
        form = {
            "type": combo_value(self.type),
            "url": self.url.text(),
            "headers": self.headers.toPlainText(),
            "command": self.command.text(),
            "args": self.args.text(),
            "env": self.env.toPlainText(),
        }

        def ok(lst: Json) -> None:
            store.toast(f"Added {name}. Sessions pick it up when Claude Code restarts.", "success")
            self.dismiss()
            self.on_added(lst)

        def bad(_e: Any) -> None:
            self.busy = False
            self._validate()

        store.call("mcp.add", {"scope": combo_value(self.scope), "cwd": self.cwd, "name": name, "form": form}).then(
            ok
        ).catch(bad)


class ToolsSheet(Sheet):
    """``ToolsDialog``: the tools a connected server lists."""

    def __init__(self, window: QWidget, server: Json, sid: str) -> None:
        super().__init__(window, "", f"{server.get('name')} · tools", 560)
        self.spinner = Spinner(14)
        self.add(self.spinner)
        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 10)
        self.add(self.list_w)
        name = server.get("name")

        def got(lst: Any) -> None:
            tools = next((s.get("tools") for s in (lst or []) if s.get("name") == name), None)
            self._render(tools if isinstance(tools, list) else [])

        store.call("session.mcpStatus", {"id": sid}, quiet=True).then(got).catch(lambda _e: self._render([]))

    def _render(self, tools: List[Json]) -> None:
        self.spinner.hide()
        if not tools:
            self.list_lay.addWidget(hint("This server lists no tools."))
        for t in tools:
            box = QWidget()
            bl = vbox(box, 2)
            n = mono_label(str(t.get("name") or ""), tone="")
            n.setStyleSheet("font-size: 12px;")
            bl.addWidget(n)
            if t.get("description"):
                bl.addWidget(label(str(t["description"]), tone="muted", size="small", wrap=True))
            self.list_lay.addWidget(box)
        self.schedule_relayout()


_ = Qt
