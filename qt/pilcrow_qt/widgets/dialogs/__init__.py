"""Dialogs (web/src/components/*Dialog.tsx, Overlays.tsx).

``open_dialog(window, key, value)`` routes ``Store.dialog_changed``: a truthy value opens the dialog
for ``key`` (replacing one already open), a falsy value closes it. Dialogs are :class:`Sheet`
overlays, so they close themselves through the store and this router does the dismissing.
``install_overlays(window)`` creates the lightbox once; the router also does it on first use.
``quit_app(window)`` quits, asking first when Claude is still working.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from PySide6.QtWidgets import QWidget

from ...store import ConfirmSpec, store
from .sheet import Sheet

_open: Dict[str, Sheet] = {}
_overlays: Dict[int, QWidget] = {}  # window id → Lightbox


def install_overlays(window: QWidget) -> None:
    """Create the overlays that follow the store on their own (the lightbox), once per window."""
    cur = _overlays.get(id(window))
    if cur is not None:
        try:
            cur.objectName()  # raises once the C++ side is gone
            return
        except RuntimeError:
            pass
    from .lightbox import Lightbox

    _overlays[id(window)] = Lightbox(window)


def open_dialog(window: QWidget, key: str, value: Any) -> None:
    install_overlays(window)
    cur = _open.pop(key, None)
    if cur is not None:
        cur.dismiss()
    # None/False close; an empty dict is a request with the defaults (``scheduleDialog: {}`` in the web).
    if value is None or value is False:
        if key not in ("confirm", "palette", "shortcuts", "schedule"):
            _route_other(window, key, value)
        return
    dlg: Optional[Sheet] = None
    if key == "confirm":
        if isinstance(value, ConfirmSpec):
            from .confirm import ConfirmDialog

            dlg = ConfirmDialog(window, value)
    elif key == "palette":
        from .palette import CommandPalette

        dlg = CommandPalette(window)
    elif key == "shortcuts":
        from .shortcuts import ShortcutsDialog

        dlg = ShortcutsDialog(window)
    elif key == "schedule":
        from .schedule import ScheduleDialog

        dlg = ScheduleDialog(window, value if isinstance(value, dict) else {})
    else:
        _route_other(window, key, value)
        return
    if dlg is None:
        return
    _open[key] = dlg
    dlg.present()


def _route_other(window: QWidget, key: str, value: Any) -> None:
    """The settings, new-session and MCP sign-in sheets register themselves in ``registry.ROUTES``
    (each opener takes ``(window, value)`` and closes on a falsy value); until they land, say so."""
    routes: Dict[str, Callable[[QWidget, Any], Any]] = {}
    try:
        from . import registry

        routes = registry.ROUTES
    except Exception:  # noqa: BLE001 — not built yet, or half-landed
        pass
    fn = routes.get(key)
    if fn is None:
        if value:
            print(f"[dialogs] {key} requested (not built yet)")
        return
    fn(window, value)


def current(key: str) -> Optional[Sheet]:
    """The open dialog for a store key, if any (tests look here)."""
    return _open.get(key)


def quit_app(window: QWidget) -> None:
    """Quit, asking first when Claude is still working (quitApp in Overlays.tsx)."""

    def quit_now() -> None:
        store.call("app.quit", {}, quiet=True).then(lambda _r: window.close()).catch(lambda _e: window.close())

    busy = sum(1 for s in store.sessions if s.get("status") in ("running", "requires_action"))
    if not busy:
        quit_now()
        return
    store.confirm_action(
        ConfirmSpec(
            title="Quit Pilcrow?",
            body=f"Claude is still working in {busy} session{'s' if busy > 1 else ''}. Quitting stops {'them' if busy > 1 else 'it'}.",
            confirm="Quit",
            danger=True,
            on_confirm=lambda _checked: quit_now(),
        )
    )
