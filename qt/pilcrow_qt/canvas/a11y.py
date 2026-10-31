"""Accessibility for the canvas: the viewport reads as a list, the blocks on screen as its items.

Qt asks the factory for an interface when a screen reader (or a test) queries the viewport; the
list interface walks the view's visible blocks and hands out one item interface per block, with the
block's text as its name and its rect on screen. Nothing here is built unless something asks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QPoint, QRect
from PySide6.QtGui import QAccessible, QAccessibleInterface
from PySide6.QtWidgets import QAccessibleWidget, QWidget

_installed = False
_lists: Dict[int, CanvasListAccessible] = {}  # by id(viewport): the interfaces Qt holds, kept alive here


class BlockAccessible(QAccessibleInterface):
    """One block on screen: a list item (a head reads as a button) named by the block's text."""

    def __init__(self, owner: CanvasListAccessible, key: str) -> None:
        super().__init__()
        self._owner = owner
        self._key = key

    # ---- identity
    def isValid(self) -> bool:  # noqa: N802
        view = self._owner.view
        return view is not None and view.scene.block(self._key) is not None

    def object(self) -> Optional[QObject]:
        return None

    def window(self) -> Any:
        return self._owner.window()

    def parent(self) -> Optional[QAccessibleInterface]:
        return self._owner

    def child(self, _index: int) -> Optional[QAccessibleInterface]:
        return None

    def childCount(self) -> int:  # noqa: N802
        return 0

    def indexOfChild(self, _child: QAccessibleInterface) -> int:  # noqa: N802
        return -1

    def childAt(self, _x: int, _y: int) -> Optional[QAccessibleInterface]:  # noqa: N802
        return None

    # ---- what it says
    def text(self, t: QAccessible.Text) -> str:
        if t in (QAccessible.Text.Name, QAccessible.Text.Value):
            b = self._block()
            return (b.text() or b.key) if b is not None else ""
        if t == QAccessible.Text.Description:
            b = self._block()
            return type(b).__name__ if b is not None else ""
        return ""

    def setText(self, _t: QAccessible.Text, _text: str) -> None:  # noqa: N802
        pass

    def rect(self) -> QRect:
        view = self._owner.view
        b = self._block()
        if view is None or b is None:
            return QRect()
        r = view.block_rect(b)
        return QRect(view.viewport().mapToGlobal(r.topLeft()), r.size())

    def role(self) -> QAccessible.Role:
        b = self._block()
        if b is not None and hasattr(b, "children") and b.children:
            return QAccessible.Role.Button  # a fold or a card: its head opens and closes it
        return QAccessible.Role.ListItem

    def state(self) -> QAccessible.State:
        s = QAccessible.State()
        s.focusable = True
        s.selectable = True
        view = self._owner.view
        if view is not None and view.focus is not None and view.focus[0] == self._key:
            s.focused = True
        return s

    def _block(self) -> Any:
        view = self._owner.view
        return view.scene.block(self._key) if view is not None else None


class CanvasListAccessible(QAccessibleWidget):
    """The viewport: a list whose children are the blocks on screen, in reading order."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport, QAccessible.Role.List, "Conversation")
        self._viewport = viewport
        self._items: Dict[str, BlockAccessible] = {}

    @property
    def view(self) -> Any:
        v = self._viewport.parentWidget() if self._viewport is not None else None
        return v if v is not None and hasattr(v, "visible_blocks") else None

    def _keys(self) -> List[str]:
        view = self.view
        if view is None:
            return []
        return [b.key for b in view.visible_blocks() if b.h > 0 and (b.text() or hasattr(b, "children"))]

    def _item(self, key: str) -> BlockAccessible:
        it = self._items.get(key)
        if it is None:
            it = self._items[key] = BlockAccessible(self, key)
        return it

    def childCount(self) -> int:  # noqa: N802
        return len(self._keys())

    def child(self, index: int) -> Optional[QAccessibleInterface]:
        keys = self._keys()
        if 0 <= index < len(keys):
            return self._item(keys[index])
        return None

    def indexOfChild(self, child: QAccessibleInterface) -> int:  # noqa: N802
        key = getattr(child, "_key", None)
        keys = self._keys()
        return keys.index(key) if key in keys else -1

    def childAt(self, x: int, y: int) -> Optional[QAccessibleInterface]:  # noqa: N802
        view = self.view
        if view is None:
            return None
        local = self._viewport.mapFromGlobal(QPoint(x, y))
        found = view.scene.leaf_at(view.scene_y(local.y()))
        return self._item(found[0].key) if found is not None else None

    def text(self, t: QAccessible.Text) -> str:
        if t == QAccessible.Text.Name:
            return self._viewport.accessibleName() or "Conversation"
        if t == QAccessible.Text.Description:
            n = self.childCount()
            return f"{n} rows on screen"
        return super().text(t)


def _factory(classname: str, obj: QObject) -> Optional[QAccessibleInterface]:
    if classname != "QWidget" or not isinstance(obj, QWidget):
        return None
    parent = obj.parentWidget()
    if parent is None or not hasattr(parent, "visible_blocks") or parent.viewport() is not obj:
        return None
    iface = _lists.get(id(obj))
    if iface is None:
        iface = _lists[id(obj)] = CanvasListAccessible(obj)
    return iface


def install() -> None:
    """Register the factory once per process (the view does this when it is made)."""
    global _installed
    if _installed:
        return
    _installed = True
    QAccessible.installFactory(_factory)
