"""Accessibility for the canvas: the viewport reads as a list, the blocks on screen as its items.

Qt asks the factory for an interface when a screen reader (or a test) queries the viewport; the
list interface walks the view's visible blocks and hands out one item interface per block that has
something to say (:func:`describe`): a group with a head (a work fold, a card, a thought) is a
Button named by its head line, with a Press action that opens and closes it; a row of words (a
paragraph, a message, a notice) is a ListItem named by the first line of its words as they read
(the laid-out document, never the Markdown source), its value all of them; a group with no head of
its own is only its rows, and a block with no words is left out, so nothing is read twice and no
internal key is ever a name. What Tab visits inside a row (a code well's Copy, a card's Open in
Files, the buttons, links and toggles of a body) are the row's children: Buttons and Links with a
Press action and a name of their own.
The view tells assistive technology what changed through :func:`announce` (the focus ring moved
to a row or a target, a fold opened) and :func:`blocks_changed` (rows arrived; the words a
streamed row gained, at most once a second); both cost one ``isActive()`` call when nothing is
listening. Nothing here is built unless something asks.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple

import shiboken6
from PySide6.QtCore import QObject, QPoint, QRect
from PySide6.QtGui import (
    QAccessible,
    QAccessibleActionInterface,
    QAccessibleEvent,
    QAccessibleInterface,
    QAccessibleStateChangeEvent,
    QAccessibleTextInsertEvent,
)
from PySide6.QtWidgets import QAccessibleWidget, QWidget

from .scene import Group, Target

NAME_MAX = 120  # a name is what a screen reader says on arrival; the rest is the value
SAY_EVERY = 1.0  # seconds between two announcements of the words a streamed row gained

_installed = False
_lists: Dict[int, CanvasListAccessible] = {}  # by id(viewport): the interfaces Qt holds, kept alive here


def _summary(text: str) -> str:
    line = next((s.strip() for s in text.splitlines() if s.strip()), "")
    return line if len(line) <= NAME_MAX else line[: NAME_MAX - 1].rstrip() + "…"


def words(b: Any) -> str:
    """A row's words as they read: what Copy gives (the document's text, with no Markdown marks)."""
    return b.selected_text(None, None)


def describe(b: Any) -> Optional[Tuple[QAccessible.Role, str]]:
    """The role and the name a block is read with, or None when it is not read at all (a group with
    no head: its rows are; a block with no words)."""
    if isinstance(b, Group):
        if b.head_h <= 0:
            return None
        name = _summary(b.head_text())
        return (QAccessible.Role.Button, name) if name else None
    name = _summary(words(b))
    return (QAccessible.Role.ListItem, name) if name else None


def target_name(b: Any, t: Target) -> str:
    """What a target inside a row is read as: its label, else the block's tooltip for it."""
    return t.name or b.tooltip_for(t) or t.kind


def _forget(iface: QAccessibleInterface) -> None:
    """Drop an interface Qt may have registered (an AT bridge registers what it is handed): Qt
    deletes it from its cache, so no pointer outlives the Python object."""
    if shiboken6.isValid(iface):
        QAccessible.deleteAccessibleInterface(QAccessible.uniqueId(iface))


class _Item(QAccessibleInterface, QAccessibleActionInterface):
    """What a row and a target inside it share: no QObject, a parent in the list, one Press action."""

    def __init__(self) -> None:
        QAccessibleInterface.__init__(self)
        QAccessibleActionInterface.__init__(self)

    def object(self) -> Optional[QObject]:
        return None

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

    def setText(self, _t: QAccessible.Text, _text: str) -> None:
        pass

    def keyBindingsForAction(self, _name: str) -> List[str]:
        return []


class BlockAccessible(_Item):
    """One block on screen: a list item (a block with a head reads as a button and can be pressed);
    the targets Tab visits inside it are its children."""

    def __init__(self, owner: CanvasListAccessible, key: str) -> None:
        super().__init__()
        self._owner = owner
        self._key = key
        self._kids: Dict[Target, TargetAccessible] = {}

    # ---- identity
    def isValid(self) -> bool:
        return self._block() is not None

    def window(self) -> Any:
        return self._owner.window()

    def parent(self) -> Optional[QAccessibleInterface]:
        return self._owner

    # ---- the targets inside it
    def targets(self) -> List[Target]:
        """What Tab visits in the block, less the head of a group that reads as a button (the head
        is the row's own Press action)."""
        view, b = self._owner.view, self._block()
        if view is None or b is None:
            return []
        head = self._head() if isinstance(b, Group) else None
        return [t for t in view.block_targets(b) if t != head]

    def kid(self, t: Target) -> TargetAccessible:
        it = self._kids.get(t)
        if it is None:
            it = self._kids[t] = TargetAccessible(self, t)
        return it

    def forget(self) -> None:
        for it in self._kids.values():
            _forget(it)
        self._kids.clear()
        _forget(self)

    def child(self, index: int) -> Optional[QAccessibleInterface]:
        ts = self.targets()
        return self.kid(ts[index]) if 0 <= index < len(ts) else None

    def childCount(self) -> int:
        return len(self.targets())

    def indexOfChild(self, child: QAccessibleInterface) -> int:
        t = getattr(child, "target", None)
        ts = self.targets()
        return ts.index(t) if t in ts else -1

    def childAt(self, x: int, y: int) -> Optional[QAccessibleInterface]:
        for t in self.targets():
            if self.kid(t).rect().contains(x, y):
                return self.kid(t)
        return None

    # ---- what it says
    def text(self, t: QAccessible.Text) -> str:
        b = self._block()
        if b is None:
            return ""
        d = describe(b)
        if t == QAccessible.Text.Name:
            return d[1] if d is not None else ""
        if t == QAccessible.Text.Value:
            # a group's content is read by its own rows; a row's value is all its words
            return (d[1] if d is not None else "") if isinstance(b, Group) else words(b)
        return ""

    def rect(self) -> QRect:
        view = self._owner.view
        b = self._block()
        if view is None or b is None:
            return QRect()
        r = view.block_rect(b)
        return QRect(view.viewport().mapToGlobal(r.topLeft()), r.size())

    def role(self) -> QAccessible.Role:
        d = describe(self._block())
        return d[0] if d is not None else QAccessible.Role.ListItem

    def state(self) -> QAccessible.State:
        s = QAccessible.State()
        s.focusable = True
        s.selectable = True
        view = self._owner.view
        if view is not None and view.focus is not None and view.focus[0] == self._key:
            s.focused = True
        b = self._block()
        if isinstance(b, Group) and b.head_h > 0:
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

    def _head(self) -> Any:
        view, b = self._owner.view, self._block()
        return view.head_target(b) if view is not None and b is not None else None

    def _block(self) -> Any:
        view = self._owner.view
        return view.scene.block(self._key) if view is not None else None


class TargetAccessible(_Item):
    """A target inside a row (a Copy, Open in Files, a body's button, link or toggle): a Button or a
    Link with its own name, a Press action, and its rectangle on screen."""

    def __init__(self, row: BlockAccessible, target: Target) -> None:
        super().__init__()
        self.row = row
        self.target = target

    def _live(self) -> Optional[Target]:
        """The target as the block has it now (its rect moves with the layout), or None when gone."""
        return next((t for t in self.row.targets() if t == self.target), None)

    def isValid(self) -> bool:
        return self.row.isValid() and self._live() is not None

    def window(self) -> Any:
        return self.row.window()

    def parent(self) -> Optional[QAccessibleInterface]:
        return self.row

    def text(self, t: QAccessible.Text) -> str:
        b = self.row._block()
        if t == QAccessible.Text.Name and b is not None:
            return target_name(b, self.target)
        return ""

    def rect(self) -> QRect:
        t, row = self._live(), self.row.rect()
        if t is None or row.isEmpty():
            return QRect()
        x, y, w, h = t.rect
        return QRect(row.x() + x, row.y() + y, w, h)

    def role(self) -> QAccessible.Role:
        return QAccessible.Role.Link if self.target.kind == "link" else QAccessible.Role.Button

    def state(self) -> QAccessible.State:
        s = QAccessible.State()
        s.focusable = True
        view = self.row._owner.view
        if view is not None and view.focus == (self.row._key, self.target):
            s.focused = True
        return s

    def actionNames(self) -> List[str]:
        return [QAccessibleActionInterface.pressAction()]

    def doAction(self, name: str) -> None:
        view, b, t = self.row._owner.view, self.row._block(), self._live()
        if name == QAccessibleActionInterface.pressAction() and view is not None and b is not None and t is not None:
            view.activate(b, t)


class CanvasListAccessible(QAccessibleWidget):
    """The viewport: a list whose children are the blocks on screen, in reading order."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport, QAccessible.Role.List, "Conversation")
        self._viewport = viewport
        self._items: Dict[str, BlockAccessible] = {}
        self._prune_at = 512
        self._shown: Optional[Set[str]] = None  # the rows last announced (None: nothing announced yet)
        self._rev = -1  # the scene structure they were read from
        self._said: Tuple[str, int] = ("", 0)  # the streamed row last announced, and how many characters
        self._said_at = 0.0

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
        return [b.key for b in view.visible_blocks() if b.h > 0 and describe(b) is not None]

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
            self._items.pop(key).forget()
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
        return self._item(found[0].key) if found is not None and describe(found[0]) is not None else None

    def text(self, t: QAccessible.Text) -> str:
        if t == QAccessible.Text.Name:
            return self._viewport.accessibleName() or "Conversation"
        if t == QAccessible.Text.Description:
            n = self.childCount()
            return f"{n} rows on screen"
        return super().text(t)


def announce(view: Any, key: str, kind: QAccessible.Event, target: Optional[Target] = None) -> None:
    """Tell assistive technology that a block changed: ``Focus`` (the focus ring moved to it, or to
    ``target`` inside it) or ``StateChanged`` (it opened or closed)."""
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
        return
    if target is not None and target in it.targets():
        it = it.kid(target)  # a target of its own: the row's head is the row itself
    QAccessible.updateAccessibility(QAccessibleEvent(it, kind))


def blocks_changed(view: Any) -> None:
    """After a commit: ``ObjectShow`` for each row on screen that was not there when the list was
    last read (a streamed answer's new paragraph, a card that arrived), and ``TextInserted`` with
    the words the streamed row gained since it was last announced (once a second at most; at once
    when it finishes)."""
    if not QAccessible.isActive():
        return
    lst = _lists.get(id(view.viewport()))
    if lst is None:
        return
    _say_stream(view, lst)
    if lst._rev == view.scene.structure_rev:
        return
    lst._rev = view.scene.structure_rev
    keys = lst._keys()
    if lst._shown is not None:
        for k in keys:
            if k not in lst._shown:
                QAccessible.updateAccessibility(QAccessibleEvent(lst._item(k), QAccessible.Event.ObjectShow))
    lst._shown = set(keys)


def _say_stream(view: Any, lst: CanvasListAccessible) -> None:
    key, n = lst._said
    prev = view.scene.block(key) if key else None
    live = next((b for b in reversed(view.visible_blocks()) if getattr(b, "streaming", False) is True), None)
    now = time.monotonic()
    if prev is not None and prev is not live:
        _say(lst, prev, n, now)  # the row stopped streaming (or moved off): its last words
        prev = None
    if live is None:
        lst._said = ("", 0)
        return
    if prev is None:
        lst._said = (live.key, 0)
    if now - lst._said_at >= SAY_EVERY:
        _say(lst, live, lst._said[1], now)


def _say(lst: CanvasListAccessible, b: Any, n: int, now: float) -> None:
    text = words(b)
    if len(text) > n:
        QAccessible.updateAccessibility(QAccessibleTextInsertEvent(lst._item(b.key), n, text[n:]))
        lst._said, lst._said_at = (b.key, len(text)), now


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
            it.forget()
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
