"""Menus (a port of the Menu in web/src/components/ui/Popover.tsx).

An item is a dict: ``{"label", "icon"?, "hint"?, "checked"?, "danger"?, "disabled"?, "on_select"}``,
``{"separator": True}`` or ``{"heading": str}``. ``show_menu(items, anchor)`` opens it under a widget;
``show_menu_at(items, global_pos)`` at a point (context menus).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMenu, QWidget, QWidgetAction

from ... import icons, theme

Item = Dict[str, Any]


def build_menu(items: List[Item], parent: Optional[QWidget] = None, width: int = 0) -> QMenu:
    t = theme.tokens()
    m = QMenu(parent)
    m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    if width:
        m.setMinimumWidth(width)
    for it in items:
        if it.get("separator"):
            m.addSeparator()
            continue
        if it.get("heading"):
            wa = QWidgetAction(m)
            lb = QLabel(str(it["heading"]))
            lb.setProperty("tone", "faint")
            lb.setProperty("size", "micro")
            lb.setContentsMargins(10, 6, 10, 2)
            lb.setStyleSheet(
                f"color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600; letter-spacing: 0.4px;"
            )
            wa.setDefaultWidget(lb)
            m.addAction(wa)
            continue
        a = QAction(str(it.get("label", "")), m)
        hint = it.get("hint")
        if hint:
            a.setToolTip(str(hint))
            a.setStatusTip(str(hint))
        if it.get("checked"):
            a.setIcon(icons.icon("check", t.text, 14))
        elif it.get("icon"):
            a.setIcon(icons.icon(str(it["icon"]), t.err if it.get("danger") else t.text_2, 14))
        if it.get("disabled"):
            a.setEnabled(False)
        if it.get("danger"):
            a.setProperty("danger", True)
        fn = it.get("on_select")
        if fn:
            a.triggered.connect(lambda _c=False, f=fn: f())
        m.addAction(a)
    m.setToolTipsVisible(True)
    return m


def show_menu(items: List[Item], anchor: QWidget, width: int = 210, align_right: bool = False) -> None:
    m = build_menu(items, anchor, width)
    pos = anchor.mapToGlobal(QPoint(anchor.width() - m.sizeHint().width() if align_right else 0, anchor.height() + 4))
    m.exec(pos)


def show_menu_at(items: List[Item], pos: QPoint, parent: Optional[QWidget] = None, width: int = 210) -> None:
    build_menu(items, parent, width).exec(pos)
