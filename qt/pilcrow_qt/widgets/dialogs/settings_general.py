"""Settings → General (the ``general`` section of SettingsDialog.tsx): theme, text size, send key,
notifications, suggestions, thinking, timeline. Each control saves through the store as the web does."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ... import theme
from ...store import store
from ..ui.bits import hairline
from ..ui.signals import bind
from .parts import Text, sheet_bg
from .settings_ui import Seg, Switch, vbox

Json = Dict[str, Any]
SIZES = [13.5, 14.5, 16]


def nearest_size(px: float) -> float:
    return min(SIZES, key=lambda s: abs(s - px))


class SetRow(QWidget):
    """``.set-row``: a label (with an optional hint) on the left, the control on the right."""

    def __init__(
        self, title: str, control: QWidget, hint_text: str = "", fill: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 13, 0, 13)
        lay.setSpacing(20)
        left = QWidget()
        ll = vbox(left, 2)
        ll.addStretch(1)
        self.title = Text(title, theme.FS_UI, 500)
        ll.addWidget(self.title)
        self.hint = Text(hint_text, theme.FS_SMALL, 400, "text_3", 1.5)
        self.hint.setVisible(bool(hint_text))
        ll.addWidget(self.hint)
        ll.addStretch(1)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(left, 1)
        self.right = QWidget()
        rl = QHBoxLayout(self.right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        if fill:
            control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
            rl.addWidget(control, 1)
        else:
            rl.addStretch(1)
            rl.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.right.setFixedWidth(180)
        lay.addWidget(self.right, 0)
        self.control = control
        self.lined = False

    def resizeEvent(self, e) -> None:  # noqa: N802
        # the web grid: 1fr | minmax(180px, 46%) of the row
        self.right.setFixedWidth(max(180, int(round(self.width() * 0.46))))
        super().resizeEvent(e)

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))


class Rows(QWidget):
    """A column of SetRows with hairlines between them."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self._n = 0

    def add(self, r: QWidget) -> QWidget:
        if self._n and hasattr(r, "lined"):
            r.lined = True
        elif self._n:
            self.lay.addWidget(hairline())
        self.lay.addWidget(r)
        self._n += 1
        return r

    def paintEvent(self, e) -> None:  # noqa: N802
        # ``.set-row + .set-row { box-shadow: 0 -1px 0 var(--hair) }``: the line sits just above the row
        super().paintEvent(e)
        from PySide6.QtGui import QPainter

        t = theme.tokens()
        p = QPainter(self)
        line = theme.blend(t.hair, sheet_bg(t).name())
        for i in range(self.lay.count()):
            w = self.lay.itemAt(i).widget()
            if w is not None and getattr(w, "lined", False) and w.isVisible():
                p.fillRect(0, w.y() - 1, self.width(), 1, line)


def config() -> Json:
    return store.config or {}


def save(patch: Json) -> None:
    store.set_config(patch).catch(lambda _e: None)


class GeneralPage(Rows):
    def __init__(self, sheet: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        c = config()
        self.theme = Seg(
            [("system", "System"), ("dark", "Dark"), ("light", "Light")], str(c.get("theme") or "system"), name="Theme"
        )
        self.theme.changed.connect(store.set_theme)
        self.add(SetRow("Theme", self.theme))

        self.size = Seg(
            [("13.5", "Small"), ("14.5", "Default"), ("16", "Large")],
            str(nearest_size(float(c.get("fontSize") or 14.5))),
            name="Conversation text size",
        )
        self.size.changed.connect(lambda v: save({"fontSize": float(v)}))
        self.add(SetRow("Conversation text size", self.size))

        self.send = Seg(
            [("enter", "Enter"), ("mod-enter", "Ctrl+Enter")],
            "mod-enter" if c.get("sendKey") == "mod-enter" else "enter",
            name="Send messages with",
        )
        self.send.changed.connect(lambda v: save({"sendKey": v}))
        self.add(SetRow("Send messages with", self.send))

        self.notif = Switch(bool(c.get("notifications")), name="Desktop notifications")
        self.notif.toggled.connect(lambda v: save({"notifications": bool(v)}))
        self.add(
            SetRow(
                "Desktop notifications",
                self.notif,
                "When Claude finishes or needs you while the window is in the background",
            )
        )

        self.suggest = Switch(bool(c.get("promptSuggestions")), name="Suggest a next message")
        self.suggest.toggled.connect(lambda v: save({"promptSuggestions": bool(v)}))
        self.add(
            SetRow("Suggest a next message", self.suggest, "Claude Code predicts a likely follow-up after each turn")
        )

        self.timeline = Switch(c.get("timeline") is not False, name="Timeline")
        self.timeline.toggled.connect(lambda v: save({"timeline": bool(v)}))
        self.add(
            SetRow(
                "Timeline",
                self.timeline,
                "Snapshot the folder after every step, so any point can be revisited or restored (side panel → Timeline)",
            )
        )

        bind(store.config_changed, self, self._sync)

    def _sync(self, c: Json) -> None:
        """The server's config wins when it answers (another window may have changed it)."""
        self.theme.set_value(str(c.get("theme") or "system"))
        self.size.set_value(str(nearest_size(float(c.get("fontSize") or 14.5))))
        self.send.set_value("mod-enter" if c.get("sendKey") == "mod-enter" else "enter")
        for sw, v in (
            (self.notif, bool(c.get("notifications"))),
            (self.suggest, bool(c.get("promptSuggestions"))),
            (self.timeline, c.get("timeline") is not False),
        ):
            sw.blockSignals(True)
            sw.setChecked(v)
            sw.blockSignals(False)
