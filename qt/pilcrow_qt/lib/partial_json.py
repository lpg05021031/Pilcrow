"""Tolerant parser for streaming tool-input JSON (port of web/src/lib/partialJson.ts).

Closes open strings, arrays and objects so fields like `command` or `file_path` can be shown while
they are still arriving.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_partial_json(src: str) -> Any:
    """Parse `src`, repairing a truncated tail; {} for empty input, None when it cannot be repaired."""
    if not src:
        return {}
    try:
        return json.loads(src)
    except ValueError:
        pass  # fall through to repair
    stack: list = []
    in_string = False
    escaped = False
    for c in src:
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in "}]":
            if stack:
                stack.pop()
    repaired = src
    if in_string:
        if escaped:
            repaired = repaired[:-1]
        repaired += '"'
    # Drop a dangling key or separator before closing.
    repaired = re.sub(r",\s*\Z", "", repaired)
    repaired = re.sub(r',\s*"[^"]*"\s*:?\s*\Z', "", repaired)
    repaired = re.sub(r":\s*\Z", ": null", repaired)
    repaired += "".join(reversed(stack))
    try:
        return json.loads(repaired)
    except ValueError:
        return None
