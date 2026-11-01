"""Accessibility for the canvas: the viewport reads as a list, the blocks on screen as its items.

Qt asks the factory for an interface when a screen reader (or a test) queries the viewport; the
list interface walks the view's visible blocks and hands out one item interface per block: its name
a one-line summary (the fold line, the card's head, a paragraph's first line), its value the whole
text, its rect on screen, and a Press action where the block has a head that opens and closes it.
The view tells assistive technology what changed through :func:`announce` (the focus ring moved, a
fold opened) and :func:`blocks_changed` (rows arrived); both cost one ``isActive()`` call when
nothing is listening. Nothing here is built unless something asks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import shiboken6
from PySide6.QtCore import QObject, QPoint, QRect
from PySide6.QtGui import (
    QAccessible,
    QAccessibleActionInterface,
    QAccessibleEvent,
    QAccessibleInterface,
    QAccessibleStateChangeEvent,
)
from PySide6.QtWidgets import QAccessibleWidget, QWidget

NAME_MAX = 120  # a name is what a screen reader says on arrival; the rest is the value

_installed = False
_lists: Dict[int, CanvasListAccessible] = {}  # by id(viewport): the interfaces Qt holds, kept alive here


def _summary(text: str) -> str:
    line = next((s.strip() for s in text.splitlines() if s.strip()), "")
    return line if len(line) <= NAME_MAX else line[: NAME_MAX - 1].rstrip() + "…"


def _forget(iface: QAccessibleInterface) -> None:
    """Drop an interface Qt may have registered (an AT bridge registers what it is handed): Qt
    deletes it from its cache, so no pointer outlives the Python object."""
    if shiboken6.isValid(iface):
        QAccessible.deleteAccessibleInterface(QAccessible.uniqueId(iface))


class BlockAccessible(QAccessibleInterface, QAccessibleActionInterface):
    """One block on screen: a list item (a block with a head reads as a button and can be pressed)."""

    def __init__(self, owner: CanvasListAccessible, key: str) -> None:
        QAccessibleInterface.__init__(self)
        QAccessibleActionInterface.__init__(self)
        self._owner = owner
        self._key = key

    # ---- identity
    def isValid(self) -> bool:
        return self._block() is not None

    def object(self) -> Optional[QObject]:
        return None

    def window(self) -> Any:
        return self._owner.window()

    def parent(self) -> Optional[QAccessibleInterface]:
        return self._owner

    def child(self, _index: int) -> Optional[QAccessibleInterface]:
        return None

    def childCount(self) -> int:
        return 0

    def indexOfChild(self, _child: QAccessibleInterface) -> int:
        return -1

    def childAt(self, _x: int, _y: int) -> Optional[QAccessibleInterface]:
        return None

    def interface_cast(self, kind: QAccessible.InterfaceType) -> Any:
        # Qt asks for the action interface by pointer; with two wrapped bases the second C++ object
        # is the QAccessibleActionInterface, and its virtuals land on this Python object.
        if kind == QAccessible.InterfaceType.ActionInterface:
            return shiboken6.VoidPtr(shiboken6.getCppPointer(self)[1])
        return shiboken6.VoidPtr(0)

    # ---- what it says
    def text(self, t: QAccessible.Text) -> str:
        b = self._block()
        if b is None:
            return ""
        if t == QAccessible.Text.Name:
            return _summary(b.text()) or b.key
        if t == QAccessible.Text.Value:
            return b.text()
        return ""

    def setText(self, _t: QAccessible.Text, _text: str) -> None:
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
        b = self._block()
        if b is not None and hasattr(b, "children") and b.children:
            s.expandable = True
            s.expanded = bool(b.open)
            s.collapsed = not b.open
        return s

    # ---- what it does
    def actionNames(self) -> List[str]:
        return [QAccessibleActionInterface.pressAction()] if self._head() is not None else []

    def doAction(self, name: str) -> None:
        view, b, t = self._owner.view, self._block(), self._head()
        if name == QAccessibleActionInterface.pressAction() and view is not None and b is not None and t is not None:
            view.activate(b, t)

    def keyBindingsForAction(self, _name: str) -> List[str]:
        return []

    def _head(self) -> Any:
        view, b = self._owner.view, self._block()
        return view.head_target(b) if view is not None and b is not None else None

    def _block(self) -> Any:
        view = self._owner.view
        return view.scene.block(self._key) if view is not None else None


class CanvasListAccessible(QAccessibleWidget):
    """The viewport: a list whose children are the blocks on screen, in reading order."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport, QAccessible.Role.List, "Conversation")
        self._viewport = viewport
        self._items: Dict[str, BlockAccessible] = {}
        self._prune_at = 512
        self._shown: Optional[Set[str]] = None  # the rows last announced (None: nothing announced yet)
        self._rev = -1  # the scene structure they were read from

    @property
    def view(self) -> Any:
        try:
            v = self._viewport.parentWidget()
        except RuntimeError:  # the viewport is gone
            return None
        return v if v is not None and hasattr(v, "visible_blocks") else None

    def _keys(self) -> List[str]:
        view = self.view
        if view is None:
            return []
        return [b.key for b in view.visible_blocks() if b.h > 0 and (b.text() or hasattr(b, "children"))]

    def _item(self, key: str) -> BlockAccessible:
        it = self._items.get(key)
        if it is None:
            if len(self._items) >= self._prune_at:
                self._prune()
            it = self._items[key] = BlockAccessible(self, key)
        return it

    def _prune(self) -> None:
        """Forget the items of blocks the scene dropped; the threshold doubles so this stays amortised."""
        view = self.view
        for key in [k for k in self._items if view is None or view.scene.block(k) is None]:
            _forget(self._items.pop(key))
        self._prune_at = max(512, 2 * len(self._items))

    def childCount(self) -> int:
        return len(self._keys())

    def child(self, index: int) -> Optional[QAccessibleInterface]:
        keys = self._keys()
        if 0 <= index < len(keys):
            return self._item(keys[index])
        return None

    def indexOfChild(self, child: QAccessibleInterface) -> int:
        key = getattr(child, "_key", None)
        keys = self._keys()
        return keys.index(key) if key in keys else -1

    def childAt(self, x: int, y: int) -> Optional[QAccessibleInterface]:
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


def announce(view: Any, key: str, kind: QAccessible.Event) -> None:
    """Tell assistive technology that a block changed: ``Focus`` (the focus ring moved to it) or
    ``StateChanged`` (it opened or closed)."""
    if not QAccessible.isActive():
        return
    lst = _lists.get(id(view.viewport()))
    if lst is None or view.scene.block(key) is None:
        return
    it = lst._item(key)
    if kind == QAccessible.Event.StateChanged:
        changed = QAccessible.State()
        changed.expanded = changed.collapsed = True
        QAccessible.updateAccessibility(QAccessibleStateChangeEvent(it, changed))
    else:
        QAccessible.updateAccessibility(QAccessibleEvent(it, kind))


def blocks_changed(view: Any) -> None:
    """After the scene's structure changed: ``ObjectShow`` for each row on screen that was not there
    when the list was last read (a streamed answer's new paragraph, a card that arrived)."""
    if not QAccessible.isActive():
        return
    lst = _lists.get(id(view.viewport()))
    if lst is None or lst._rev == view.scene.structure_rev:
        return
    lst._rev = view.scene.structure_rev
    keys = lst._keys()
    if lst._shown is not None:
        for k in keys:
            if k not in lst._shown:
                QAccessible.updateAccessibility(QAccessibleEvent(lst._item(k), QAccessible.Event.ObjectShow))
    lst._shown = set(keys)


def _factory(classname: str, obj: QObject) -> Optional[QAccessibleInterface]:
    if classname != "QWidget" or not isinstance(obj, QWidget):
        return None
    parent = obj.parentWidget()
    if parent is None or not hasattr(parent, "visible_blocks") or parent.viewport() is not obj:
        return None
    # Qt deletes a list interface from its cache when its viewport goes; drop those (and their items)
    # here, before an id is looked up that a new viewport may have reused
    for k in [k for k, v in _lists.items() if not shiboken6.isValid(v)]:
        for it in _lists.pop(k)._items.values():
            _forget(it)
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
