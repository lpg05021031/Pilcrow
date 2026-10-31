"""The Session tab (a port of web/src/components/panels/InfoPanel.tsx): how full the context window
is and what fills it, cost, the model and mode, Claude Code's version, tools and skills, the project's
memory notes, the MCP servers with their status and actions, the worktree and the API endpoint.

Measurements follow panels.css (``.ps``, ``.kv``, ``.mcp-row``), motion.css (``.gauge-*``, ``.stat``)
and app.css (``.mem-peek``, ``.chip``)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import cost, short_model, tildify, tokens
from ...store import ConfirmSpec, store
from ..ui.bits import Box, ContextRing, Spinner, copy_text
from ..ui.menu import show_menu_at
from ..ui.signals import bind
from .right_panel import Btn, Refresher, Section, elide, empty_text, icon_btn, lbl

Json = Dict[str, Any]

MODES = [
    {"id": "default", "label": "Ask before edits"},
    {"id": "acceptEdits", "label": "Edit automatically"},
    {"id": "plan", "label": "Plan mode"},
    {"id": "auto", "label": "Auto"},
    {"id": "bypassPermissions", "label": "Bypass permissions"},
]
# Token names per context category (InfoPanel.tsx CAT_COLORS); the two literal web colours map to the nearest tokens.
CAT_TONES = {
    "System prompt": "text_3",
    "System tools": "info",
    "MCP tools": "plan",
    "Memory files": "ok",
    "Messages": "accent",
    "Custom agents": "think",
    "Skills": "warn",
}


def model_label(value: str, aliases: Optional[List[Json]] = None) -> str:
    """A short name for a model value: the endpoint's display name, Claude Code's alias name, or a tidied ID."""
    gw = store.gateway_models if isinstance(store.gateway_models, dict) else {}
    for m in gw.get("models") or []:
        if m.get("id") == value and m.get("displayName"):
            return re.sub(r"^Claude\s+", "", str(m["displayName"]), flags=re.I)
    for a in aliases or []:
        if a.get("value") == value and a.get("displayName"):
            return str(a["displayName"])
    return short_model(value) if re.search(r"claude-", value, re.I) else value


def cat_tone(c: Json) -> Optional[str]:
    if c.get("kind") == "free":
        return None
    if c.get("kind") == "buffer":
        return "hair_2"
    return CAT_TONES.get(str(c.get("name")), "text_2")


class RingGauge(ContextRing):
    """The big context ring (``.gauge``: 88px, 7px stroke) with the figure in the middle (``.gauge-num``
    17px 600); the tone warms as it fills."""

    def __init__(self, size: int = 88, width: int = 7, parent: Optional[QWidget] = None) -> None:
        super().__init__(size, parent)
        self._w = width
        self.text = ""
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_text(self, text: str) -> None:
        self.text = text
        self.update()

    def tone(self) -> str:
        t = theme.tokens()
        pct = self.used / self.total if self.total else 0
        return t.err if pct > 0.85 else t.accent if pct > 0.65 else t.text_2

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        half = self._w / 2
        rect = QRectF(half, half, self._size - self._w, self._size - self._w)
        p.setPen(QPen(theme.blend(t.fill_2, t.canvas), self._w))
        p.drawEllipse(rect)
        if self.total > 0:
            pct = max(0.0, min(1.0, self.used / self.total))
            pen = QPen(QColor(self.tone()), self._w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -int(pct * 360 * 16))
        p.setPen(QColor(t.text))
        f = theme.font_ui(17, 600)
        f.setLetterSpacing(f.SpacingType.PercentageSpacing, 98)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)


class StatTile(QWidget):
    """``.stat``: a quiet tile with a small key over a 14px 600 figure."""

    def __init__(self, key: str, value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 7)
        lay.setSpacing(2)
        self.key = lbl(key, theme.FS_MICRO, tone="faint")
        self.value = lbl(value, 14, 600)
        lay.addWidget(self.key)
        lay.addWidget(self.value)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.blend(t.hair, t.canvas), 1))
        p.setBrush(theme.blend(t.fill_1, t.canvas))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10, 10)


class CatBar(QWidget):
    """The stacked bar of context categories (``.ctx-bar``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cats: List[Json] = []
        self._max = 1
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_usage(self, usage: Json) -> None:
        self._cats = [
            c for c in usage.get("categories") or [] if c.get("kind") != "deferred" and int(c.get("tokens") or 0) > 0
        ]
        self._max = max(1, int(usage.get("maxTokens") or 1))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_2, t.canvas))
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)
        x = 0.0
        for c in self._cats:
            w = int(c.get("tokens") or 0) / self._max * self.width()
            tone = cat_tone(c)
            if tone:
                p.setBrush(theme.qcolor(getattr(t, tone)))
                p.drawRoundedRect(QRectF(x, 0, max(0.0, w - 2), 6), 1, 1)
            x += w


class ChipCloud(QWidget):
    """A wrapped run of chips (``.chips.wrap``): 24px pills, 11.5px, 6px apart, with an icon for tools."""

    def __init__(self, items: List[tuple], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items  # (text, icon or "")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def _place(self, width: int) -> List[tuple]:
        fm = QFontMetrics(theme.font_ui(11.5))
        out: List[tuple] = []
        x = y = 0
        for text, icon in self.items:
            w = 9 + (10 + 5 if icon else 0) + fm.horizontalAdvance(text) + 9
            if x and x + w > width:
                x, y = 0, y + 24 + 6
            out.append((QRect(x, y, w, 24), text, icon))
            x += w + 6
        return out

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        placed = self._place(max(1, w))
        return min(200, placed[-1][0].bottom() + 1) if placed else 0

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.heightForWidth(max(200, self.width())))

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = theme.font_ui(11.5)
        p.setFont(font)
        for r, text, icon in self._place(self.width()):
            p.setPen(QPen(theme.blend(t.hair, t.canvas), 1))
            p.setBrush(theme.blend(t.fill_1, t.canvas))
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
            x = r.x() + 9
            if icon:
                p.drawPixmap(x, r.y() + 7, icons.pixmap(icon, t.text_2, 10))
                x += 15
            p.setPen(QColor(t.text_2))
            p.drawText(QRect(x, r.y(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter, text)


def _kv(rows: List[tuple]) -> QWidget:
    """The two-column key/value grid (``.kv``): keys faint, 112px wide, 8px between rows, 12px between columns."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(12)
    g.setVerticalSpacing(8)
    g.setColumnMinimumWidth(0, 112)
    g.setColumnStretch(1, 1)
    for i, (k, v) in enumerate(rows):
        g.addWidget(lbl(k, theme.FS_SMALL, tone="faint"), i, 0, Qt.AlignmentFlag.AlignTop)
        g.addWidget(v if isinstance(v, QWidget) else lbl(str(v), theme.FS_SMALL), i, 1)
    return w


def _mono(text: str, tooltip: str = "", shrink: bool = True) -> QLabel:
    """A small monospace value; ``shrink`` lets a long path give way to the column instead of widening it."""
    lb = lbl(text, theme.FS_SMALL, mono=True)
    if shrink:
        elide(lb)
    if tooltip:
        lb.setToolTip(tooltip)
    return lb


def _row(widgets: List[QWidget], gap: int = 8) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(gap)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


class McpRow(QWidget):
    """One MCP server (``.mcp-row``): its status dot, name, and the action its status calls for;
    reconnect, disable and sign-out live in the row's context menu."""

    def __init__(self, s: Json, panel: InfoPanel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.server = s
        self._panel = panel
        status = str(s.get("status") or "")
        if s.get("error"):
            self.setToolTip(str(s["error"]))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.dot = Box(radius=3)
        self.dot.setFixedSize(7, 7)
        lay.addWidget(self.dot)
        lay.addWidget(elide(lbl(str(s.get("name") or ""), theme.FS_SMALL)), 1)
        n_tools = s.get("tools")
        n_tools = n_tools if isinstance(n_tools, int) else len(n_tools) if isinstance(n_tools, list) else 0
        if status == "needs-auth":
            lay.addWidget(Btn("Sign in", kind="primary", size="xs", on_click=lambda: panel.sign_in(s)))
        elif status == "failed":
            lay.addWidget(Btn("Retry", size="xs", on_click=lambda: panel.reconnect(s)))
        else:
            text = (f"{n_tools} tools" if n_tools else "connected") if status == "connected" else status
            lay.addWidget(lbl(text, theme.FS_SMALL, tone="faint"))
        self.setMinimumHeight(24)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: panel.server_menu(s, self, self.mapToGlobal(pos)))
        self._status = status
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        self.dot.set_look(
            bg={"connected": "ok", "failed": "err", "needs-auth": "accent", "pending": "accent"}.get(
                self._status, "text_4"
            )
        )


class InfoPanel(QWidget):
    """Everything about one session that is not the conversation (``.info-panel``)."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.usage: Optional[Json] = None
        self.mcp: Optional[List[Json]] = None
        self.notes: Optional[List[Json]] = None
        self.loading: Optional[str] = None
        self._show_tools = False
        self._key: Any = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self._lay = QVBoxLayout(self.host)
        self._lay.setContentsMargins(16, 4, 16, 24)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll)
        self._sections: List[Section] = []
        self._refresher = Refresher(self, session_id, self.render, fs=False)
        bind(store.info_changed, self, lambda _i: self.render(force=True))
        bind(store.gateway_models_changed, self, lambda _g: self.render(force=True))
        bind(store.sched_changed, self, lambda _r: self.load_notes())
        self.load_notes()
        self.render()

    # ---- data
    def load_notes(self) -> None:
        session = store.session(self.sid) or {}
        cwd = session.get("project") or session.get("cwd") or self.cwd

        def ok(r: Any) -> None:
            self.notes = list((r or {}).get("notes") or [])
            self.render(force=True)

        def bad(_e: Any) -> None:
            self.notes = []
            self.render(force=True)

        store.call("memory.list", {"cwd": cwd}, quiet=True).then(ok).catch(bad)

    def analyze(self) -> None:
        self.loading = "ctx"
        self.render(force=True)

        def done(r: Any) -> None:
            self.loading = None
            self.usage = r if isinstance(r, dict) else None
            self.render(force=True)

        store.call("session.contextUsage", {"id": self.sid}).then(done).catch(lambda _e: done(None))

    def refresh_mcp(self) -> None:
        self.loading = "mcp"
        self.render(force=True)

        def done(r: Any) -> None:
            self.loading = None
            self.mcp = list(r) if isinstance(r, list) else self.mcp
            self.render(force=True)

        store.call("session.mcpStatus", {"id": self.sid}).then(done).catch(lambda _e: done(None))

    def distill(self) -> None:
        self.loading = "mem"
        self.render(force=True)
        session = store.session(self.sid) or {}

        def done(r: Any) -> None:
            self.loading = None
            added = list((r or {}).get("added") or [])
            where = str(session.get("project") or "").split("/")[-1] if session.get("project") else "this folder"
            store.toast(
                f"Saved {len(added)} note{'s' if len(added) > 1 else ''} for {where}"
                if added
                else "Nothing new worth keeping yet",
                "success" if added else "info",
            )
            self.render(force=True)

        def bad(_e: Any) -> None:
            self.loading = None
            self.render(force=True)

        store.call("memory.distill", {"id": self.sid, "force": True}).then(done).catch(bad)

    # ---- MCP actions
    def sign_in(self, s: Json) -> None:
        store.set_dialog("mcpAuth", {"sessionId": self.sid, "name": s.get("name")})

    def reconnect(self, s: Json) -> None:
        store.call("session.mcp.reconnect", {"id": self.sid, "name": s.get("name")}).then(
            lambda _r: self.refresh_mcp()
        ).catch(lambda _e: None)

    def toggle(self, s: Json, enabled: bool) -> None:
        store.call("session.mcp.toggle", {"id": self.sid, "name": s.get("name"), "enabled": enabled}).then(
            lambda _r: self.refresh_mcp()
        )

    def clear_auth(self, s: Json) -> None:
        store.call("session.mcp.clearAuth", {"id": self.sid, "name": s.get("name")}).then(
            lambda _r: (store.toast(f"Signed out of {s.get('name')}", "success"), self.refresh_mcp())
        )

    def server_menu(self, s: Json, anchor: QWidget, pos: Any = None) -> None:
        disabled = s.get("status") == "disabled"
        items = [
            {"label": "Reconnect", "icon": "refresh-cw", "on_select": lambda: self.reconnect(s)},
            {
                "label": "Enable" if disabled else "Disable",
                "icon": "power",
                "on_select": lambda: self.toggle(s, disabled),
            },
            {"separator": True},
            {"label": "Sign in", "icon": "key-round", "on_select": lambda: self.sign_in(s)},
            {"label": "Clear authentication", "icon": "trash", "danger": True, "on_select": lambda: self.clear_auth(s)},
        ]
        show_menu_at(items, pos if pos is not None else anchor.mapToGlobal(anchor.rect().bottomLeft()), anchor, 220)

    # ---- render
    def render(self, force: bool = False) -> None:
        rt = store.rt_of(self.sid)
        session = store.session(self.sid) or {}
        st = rt.st if rt else {}
        key = (
            st.get("usage"),
            st.get("model"),
            st.get("activeModel"),
            st.get("effort"),
            st.get("mode"),
            st.get("live"),
            st.get("init") is not None,
            len(st.get("mcp") or []),
            self.loading,
            self.usage is not None,
            self.mcp is not None,
            self.notes is not None,
            self._show_tools,
        )
        if key == self._key and not force:
            return
        self._key = key
        for s in self._sections:
            self._lay.removeWidget(s)
            s.setParent(None)
            s.deleteLater()
        self._sections = []
        info = store.info or {}
        init: Json = st.get("init") or {}
        usage: Json = st.get("usage") or {}
        used, total = int(usage.get("contextTokens") or 0), int(usage.get("contextWindow") or 0)
        pct = used / total if total else 0.0
        live = bool(st.get("live"))
        t = theme.tokens()

        # context window (.gauge-row)
        s = Section("Context window")
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(16)
        self.ring = RingGauge()
        self.ring.set_values(used, total)
        self.ring.set_text(f"{round(pct * 100)}%" if total else tokens(used))
        rl.addWidget(self.ring)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self.gauge_line = lbl("", theme.FS_SMALL, tone="faint")
        self.gauge_line.setTextFormat(Qt.TextFormat.RichText)
        self.gauge_line.setText(
            f'<b style="color:{t.text}; font-weight:600">{tokens(used)}</b> of {tokens(total)} tokens'
            if total
            else f"{tokens(used)} of {tokens(total)}"
        )
        sl.addWidget(self.gauge_line)
        stats = QHBoxLayout()
        stats.setSpacing(8)
        stats.addWidget(StatTile("Cost so far", cost(float(usage.get("costUsd") or 0))))
        stats.addWidget(StatTile("Output tokens", tokens(int(usage.get("outputTokens") or 0))))
        sl.addLayout(stats)
        rl.addWidget(side, 1)
        s.add(row)
        if self.usage:
            cats = QWidget()
            cl = QVBoxLayout(cats)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(5)
            bar = CatBar()
            bar.set_usage(self.usage)
            bar.setContentsMargins(0, 0, 0, 1)
            cl.addWidget(bar)
            for c in self.usage.get("categories") or []:
                if int(c.get("tokens") or 0) <= 0 or c.get("kind") == "free":
                    continue
                cr = QHBoxLayout()
                cr.setSpacing(8)
                tone = cat_tone(c)
                sw = Box(bg=f"{tone}/canvas" if tone else None, radius=2)
                sw.setFixedSize(8, 8)
                cr.addWidget(sw)
                cr.addWidget(lbl(str(c.get("name") or ""), theme.FS_SMALL, tone="muted"), 1)
                cr.addWidget(lbl(tokens(int(c.get("tokens") or 0)), theme.FS_SMALL, tone="faint", mono=True))
                cl.addLayout(cr)
            s.add(cats)
        else:
            analyze = Btn("Break down usage", icon="gauge", kind="ghost", size="sm", on_click=self.analyze)
            analyze.pad_left = 2  # .ctx-analyze: margin-left -8px pulls the 10px padding back to the edge
            analyze.setEnabled(live and self.loading != "ctx")
            analyze.setToolTip("" if live else "Available while Claude Code is running for this session")
            wrap = QHBoxLayout()
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.setSpacing(6)
            if self.loading == "ctx":
                wrap.addWidget(Spinner(12))
            wrap.addWidget(analyze)
            wrap.addStretch(1)
            w = QWidget()
            w.setLayout(wrap)
            s.add(w)
        s.add(
            lbl(
                "Type /compact to summarize the conversation when it gets long.",
                theme.FS_SMALL,
                tone="faint",
                wrap=True,
                line_height=1.5,
            )
        )
        self._add(s)

        # Claude Code (.kv)
        s = Section("Claude Code")
        model_id = str(st.get("activeModel") or st.get("model") or "")
        name = model_label(model_id, st.get("models") or None) if model_id else "Default"
        model_w = QWidget()
        ml = QHBoxLayout(model_w)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.model_label = lbl(name, theme.FS_SMALL)
        self.model_label.setToolTip(model_id)
        ml.addWidget(self.model_label)
        if model_id and name != model_id:
            sub = lbl(model_id, 11.5, tone="faint", mono=True)
            sub.setContentsMargins(4 + 3, 0, 0, 0)  # .kv-sub margin-left 4px after the space
            ml.addWidget(elide(sub), 1)
        else:
            ml.addStretch(1)
        mode = next((m["label"] for m in MODES if m["id"] == st.get("mode")), str(st.get("mode") or ""))
        sid_w = QWidget()
        il = QHBoxLayout(sid_w)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(8)
        il.addWidget(_mono(f"{self.sid[:13]}…", shrink=False))
        cp = icon_btn("copy", "Copy session ID", 12)
        cp.clicked.connect(lambda: (copy_text(self.sid), store.toast("Copied", "success")))
        il.addWidget(cp)
        il.addStretch(1)
        s.add(
            _kv(
                [
                    ("Model", model_w),
                    ("Permission mode", mode),
                    ("Effort", str(st.get("effort") or init.get("effort") or "Default")),
                    ("Version", _mono(str(init.get("version") or (info.get("claude") or {}).get("version") or "—"))),
                    ("Status", "Process running" if live else "Starts with your next message"),
                    ("Session ID", sid_w),
                    ("Folder", _mono(tildify(self.cwd, info.get("home")), self.cwd)),
                ]
            )
        )
        tools = init.get("tools")
        if isinstance(tools, list):
            n_skills, n_agents = len(init.get("skills") or []), len(init.get("agents") or [])
            tog = Btn(
                f"{len(tools)} tools, {n_skills} skills, {n_agents} agents",
                icon="chevron-down" if self._show_tools else "chevron-right",
                kind="toggle",
                on_click=self._toggle_tools,
            )
            s.add(_row([tog], 0))
            if self._show_tools:
                chips = (
                    [(str(x), "wrench") for x in tools]
                    + [(f"agent: {a}", "") for a in init.get("agents") or []]
                    + [(f"skill: {k}", "") for k in (init.get("skills") or [])[:40]]
                )
                s.add(ChipCloud(chips))
        self._add(s)

        # memory (.mem-peek)
        s = Section("Project memory")
        notes = self.notes
        s.head_add(
            Btn(
                f"{len(notes)} note{'s' if len(notes) > 1 else ''}" if notes else "Manage",
                kind="link",
                on_click=lambda: store.set_dialog("settings", "memory"),
            )
        )
        if notes is None:
            s.add(Spinner(13))
        elif notes:
            peek = QWidget()
            pl = QVBoxLayout(peek)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(5)
            for n in notes[:3]:
                r = QHBoxLayout()
                r.setSpacing(8)
                dot = QWidget()
                dot.setFixedSize(6, 5 + 7)
                d = Box(bg="text_4", radius=2, parent=dot)
                d.setGeometry(0, 7, 5, 5)
                r.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
                r.addWidget(lbl(str(n.get("text") or ""), theme.FS_SMALL, tone="muted", wrap=True), 1)
                pl.addLayout(r)
            if len(notes) > 3:
                pl.addWidget(lbl(f"and {len(notes) - 3} more", theme.FS_SMALL, tone="faint"))
            s.add(peek)
        else:
            s.add(
                empty_text(
                    "Nothing remembered about this folder yet. Notes are taken at the end of a turn, or you can save this session's lessons now."
                )
            )
        save = Btn(
            "Save what we learned",
            icon="sparkles",
            size="xs",
            icon_size=11,
            on_click=self.distill,
            tooltip="Distil what this session learned into notes for future sessions",
        )
        save.setEnabled(self.loading != "mem")
        mine = sum(1 for n in notes or [] if n.get("sessionId") == self.sid)
        s.add(_row([save] + ([lbl(f"{mine} from this session", theme.FS_SMALL, tone="faint")] if mine else [])))
        self._add(s)

        # MCP (.mcp-row)
        s = Section("MCP servers")
        s.head_add(Btn("Manage", kind="link", on_click=lambda: store.set_dialog("settings", "mcp")))
        if live:
            if self.loading == "mcp":
                s.head_add(Spinner(12))
            rf = icon_btn("refresh-cw", "Refresh", 12)
            rf.clicked.connect(self.refresh_mcp)
            s.head_add(rf)
        servers: List[Json] = list(st.get("mcp") or []) or list(self.mcp or []) or list(init.get("mcpServers") or [])
        if servers:
            for sv in servers:
                s.add(McpRow(sv, self))
        else:
            s.add(empty_text("No MCP servers configured." if init else "Shown after Claude Code starts."))
        self._add(s)

        # worktree
        wt = session.get("worktree")
        if wt:
            s = Section("Worktree")
            s.add(
                _kv(
                    [
                        ("Branch", _mono(str(wt.get("branch") or ""))),
                        ("Based on", _mono(str(wt.get("base") or ""))),
                        (
                            "Location",
                            _mono(tildify(str(wt.get("path") or ""), info.get("home")), str(wt.get("path") or "")),
                        ),
                    ]
                )
            )
            s.add(
                _row(
                    [
                        Btn(
                            "Open folder",
                            icon="folder-open",
                            size="sm",
                            on_click=lambda: store.call("shell.openPath", {"path": wt.get("path")}),
                        ),
                        Btn(
                            "Remove",
                            icon="trash",
                            kind="danger-text",
                            size="sm",
                            on_click=lambda: self._remove_worktree(wt),
                        ),
                    ]
                )
            )
            self._add(s)

        gw = info.get("gateway") or {}
        if gw.get("baseUrl"):
            s = Section("API endpoint")
            s.add(
                _kv(
                    [
                        ("Base URL", _mono(str(gw["baseUrl"]), str(gw["baseUrl"]))),
                        ("Credential", "Configured" if gw.get("hasToken") else "Not found"),
                    ]
                )
            )
            self._add(s)
        for i, sec in enumerate(self._sections):
            sec.set_last(i == len(self._sections) - 1)

    def _add(self, s: Section) -> None:
        self._lay.insertWidget(self._lay.count() - 1, s)
        self._sections.append(s)

    def _toggle_tools(self) -> None:
        self._show_tools = not self._show_tools
        self.render(force=True)

    def _remove_worktree(self, wt: Json) -> None:
        def run(_c: bool) -> None:
            store.call(
                "git.removeWorktree",
                {
                    "repoRoot": wt.get("repoRoot"),
                    "dir": wt.get("path"),
                    "branch": wt.get("branch"),
                    "deleteBranch": True,
                },
            ).then(lambda _r: store.toast("Worktree removed", "success"))

        store.confirm_action(
            ConfirmSpec(
                title="Remove this worktree?",
                body=f"Deletes {wt.get('path')} and the branch {wt.get('branch')}. Uncommitted work there is lost.",
                confirm="Remove worktree",
                danger=True,
                on_confirm=run,
            )
        )
