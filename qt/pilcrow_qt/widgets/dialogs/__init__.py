"""Dialogs (web/src/components/*Dialog.tsx, Overlays.tsx). ``open_dialog(window, key, value)`` routes
Store.dialog_changed to the right dialog; a falsy value closes it."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget


def open_dialog(window: QWidget, key: str, value: Any) -> None:
    if not value:
        return
    print(f"[dialogs] {key} requested (not built yet)")
