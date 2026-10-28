"""Pilcrow's native desktop client (PySide6). See qt/ARCHITECTURE.md."""

from __future__ import annotations

import json
from pathlib import Path


def _version() -> str:
    # The client ships beside package.json in the install folder; the version is the app's.
    for p in (
        Path(__file__).resolve().parents[2] / "package.json",
        Path(__file__).resolve().parents[1] / "package.json",
    ):
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("version") or "0.0.0")
        except Exception:  # noqa: BLE001
            continue
    return "0.0.0"


__version__ = _version()
