"""Tolerant parser for streaming tool-input JSON (port of web/src/lib/partialJson.ts).

Closes open strings, arrays and objects so fields like `command` or `file_path` can be shown while
they are still arriving. The scan that finds what is open is kept as state (:class:`PartialScanner`)
and advanced by each piece as it arrives, so a tool input that streams costs each piece once and
not the whole text again: a hundred-kilobyte file being written used to cost seconds of the main
thread over its stream (the scan was quadratic); it is now a parse of the text per flush.
"""

from __future__ import annotations

import json
import re
from typing import Any, List

_TRAILING_COMMA = re.compile(r",\s*\Z")
_DANGLING_KEY = re.compile(r',\s*"[^"]*"\s*:?\s*\Z')
_DANGLING_COLON = re.compile(r":\s*\Z")
TAIL = 4096  # the repair looks at the end of the text: a dangling key or separator is never longer


class PartialScanner:
    """Where a JSON text stands: inside a string, after a backslash, and the brackets still open.
    ``feed`` costs the piece; ``parse`` costs one ``json.loads`` of the (repaired) text."""

    __slots__ = ("in_string", "escaped", "stack")

    def __init__(self) -> None:
        self.in_string = False
        self.escaped = False
        self.stack: List[str] = []

    def feed(self, piece: str) -> None:
        in_string, escaped, stack = self.in_string, self.escaped, self.stack
        for c in piece:
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
            elif c == "}" or c == "]":
                if stack:
                    stack.pop()
        self.in_string, self.escaped = in_string, escaped

    @property
    def complete(self) -> bool:
        return not self.in_string and not self.stack

    def repair(self, src: str) -> str:
        """``src`` closed: the open string ended, a dangling key or separator dropped, the brackets
        closed. Only the tail of the text is looked at."""
        cut = max(0, len(src) - TAIL)
        head, tail = src[:cut], src[cut:]
        if self.in_string:
            if self.escaped:
                tail = tail[:-1]
            tail += '"'
        tail = _TRAILING_COMMA.sub("", tail)
        tail = _DANGLING_KEY.sub("", tail)
        tail = _DANGLING_COLON.sub(": null", tail)
        return head + tail + "".join(reversed(self.stack))

    def parse(self, src: str) -> Any:
        """The value of ``src`` so far: {} for empty input, None when it cannot be repaired."""
        if not src:
            return {}
        if self.complete:
            try:
                return json.loads(src)
            except ValueError:
                pass  # a text that closed its brackets but is not valid: repair what can be
        try:
            return json.loads(self.repair(src))
        except ValueError:
            return None


def parse_partial_json(src: str) -> Any:
    """Parse `src`, repairing a truncated tail; {} for empty input, None when it cannot be repaired."""
    sc = PartialScanner()
    sc.feed(src)
    return sc.parse(src)
