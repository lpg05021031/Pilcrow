"""Which function opens which Store.dialogs key. ``open_dialog`` in this package routes through it:
each opener takes ``(window, value)`` and closes its sheet when ``value`` is falsy."""

from __future__ import annotations

from typing import Any, Callable, Dict

from PySide6.QtWidgets import QWidget

from .mcp_auth import open_mcp_auth
from .new_session import open_new_session
from .settings import open_settings

ROUTES: Dict[str, Callable[[QWidget, Any], Any]] = {
    "settings": open_settings,
    "newSession": open_new_session,
    "mcpAuth": open_mcp_auth,
}
