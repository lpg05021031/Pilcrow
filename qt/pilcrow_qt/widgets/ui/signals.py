"""Connecting widgets to the app-wide signals (Store, Theme) without leaking them.

A lambda connected to a long-lived signal keeps running after the widget it captured is gone, and
then touches a deleted C++ object. ``bind(signal, owner, fn)`` connects ``fn`` and disconnects it
again when ``owner`` is destroyed. Use it for every connection whose sender outlives the widget.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject


def bind(signal: Any, owner: QObject, fn: Callable[..., Any]) -> Callable[[], None]:
    """Connect ``fn`` to ``signal`` for the lifetime of ``owner``; returns the unbind."""
    alive = {"on": True}

    def slot(*args: Any) -> None:
        if alive["on"]:
            fn(*args)

    signal.connect(slot)

    def off(*_a: Any) -> None:
        if not alive["on"]:
            return
        alive["on"] = False
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    owner.destroyed.connect(off)
    return off
