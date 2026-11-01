"""Models (a port of web/src/lib/models.ts): the aliases Claude Code always understands, a short
name for a model value, and the options of a model picker."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .fmt import short_model

Json = Dict[str, Any]

#: Aliases Claude Code always understands; used until a session has reported its own list.
ALIASES: List[Json] = [
    {"value": "opus", "displayName": "Opus", "description": "Most capable"},
    {"value": "sonnet", "displayName": "Sonnet", "description": "Fast and capable"},
    {"value": "haiku", "displayName": "Haiku", "description": "Fastest"},
]


def trim_claude(s: str) -> str:
    """ "Claude Opus 5.5 (1M context)" → "Opus 5.5 (1M context)": every model here is Claude; the chip is small."""
    return re.sub(r"^Claude\s+", "", s, flags=re.I)


def model_label(value: str, gateway: List[Json], aliases: Optional[List[Json]] = None) -> str:
    """A short, human name for a model value: the endpoint's display name, Claude Code's alias name, or a tidied ID."""
    for m in gateway:
        if m.get("id") == value and m.get("displayName"):
            return trim_claude(str(m["displayName"]))
    for a in ALIASES if aliases is None else aliases:
        if a.get("value") == value and a.get("displayName"):
            return str(a["displayName"])
    return short_model(value) if re.search(r"claude-", value, re.I) else value


def model_options(gateway: List[Json]) -> List[Json]:
    """Options for model pickers: Default, then the gateway's own models. Claude Code's built-in
    aliases are not offered: everything here goes through the gateway."""
    out: List[Json] = [{"value": "", "label": "Default", "hint": "Claude Code's configured model", "mono": False}]
    for m in gateway:
        mid, name = str(m.get("id") or ""), str(m.get("displayName") or "")
        out.append(
            {"value": mid, "label": trim_claude(name) if name else mid, "hint": mid if name else "", "mono": True}
        )
    return out
