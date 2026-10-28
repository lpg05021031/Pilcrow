"""Minimal ANSI SGR → styled segments, for command output (port of web/src/lib/ansi.ts).

Other escape sequences are dropped. Colours stay as the `var(--ansi-…)` strings the web interface
uses so the UI can map them to theme tokens.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Seg:
    text: str
    cls: Optional[str] = None
    style: Optional[Dict[str, str]] = None


_BASIC = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]


def _xterm256(n: int) -> str:
    if n < 16:
        return f"var(--ansi-{_BASIC[n] if n < 8 else 'bright-' + _BASIC[n - 8]})"
    if n >= 232:
        v = 8 + (n - 232) * 10
        return f"rgb({v},{v},{v})"
    i = n - 16

    def c(x: int) -> int:
        return 0 if x == 0 else 55 + x * 40

    return f"rgb({c(i // 36)},{c((i // 6) % 6)},{c(i % 6)})"


ESC_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]|\r(?!\n)")


def _number(x: str) -> float:
    """JavaScript Number(x || 0): '' is 0, digits parse, anything else is NaN."""
    if not x:
        return 0
    try:
        return int(x)
    except ValueError:
        return math.nan


def _or0(v: Optional[float]) -> int:
    """`v || 0` for a possibly missing or NaN code."""
    if v is None or v != v or not v:
        return 0
    return int(v)


def parse_ansi(text: str) -> List[Seg]:
    out: List[Seg] = []
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold = dim = italic = underline = False
    last = 0

    def push(s: str) -> None:
        if not s:
            return
        cls = " ".join(c for c, on in (("a-b", bold), ("a-d", dim), ("a-i", italic), ("a-u", underline)) if on)
        style: Dict[str, str] = {}
        if fg:
            style["color"] = fg
        if bg:
            style["background"] = bg
        out.append(Seg(s, cls or None, style if (fg or bg) else None))

    for m in ESC_RE.finditer(text):
        push(text[last : m.start()])
        last = m.end()
        if m.group(2) != "m":
            continue
        codes = [_number(x) for x in (m.group(1) or "0").split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            nxt = codes[i + 1] if i + 1 < len(codes) else None
            if c == 0:
                fg, bg, bold, dim, italic, underline = None, None, False, False, False, False
            elif c == 1:
                bold = True
            elif c == 2:
                dim = True
            elif c == 3:
                italic = True
            elif c == 4:
                underline = True
            elif c == 22:
                bold, dim = False, False
            elif c == 23:
                italic = False
            elif c == 24:
                underline = False
            elif 30 <= c <= 37:
                fg = f"var(--ansi-{_BASIC[int(c) - 30]})"
            elif c == 39:
                fg = None
            elif 40 <= c <= 47:
                bg = f"var(--ansi-{_BASIC[int(c) - 40]})"
            elif c == 49:
                bg = None
            elif 90 <= c <= 97:
                fg = f"var(--ansi-bright-{_BASIC[int(c) - 90]})"
            elif 100 <= c <= 107:
                bg = f"var(--ansi-bright-{_BASIC[int(c) - 100]})"
            elif c in (38, 48) and nxt == 5:
                col = _xterm256(_or0(codes[i + 2] if i + 2 < len(codes) else None))
                if c == 38:
                    fg = col
                else:
                    bg = col
                i += 2
            elif c in (38, 48) and nxt == 2:
                r, g, b = (_or0(codes[i + k] if i + k < len(codes) else None) for k in (2, 3, 4))
                col = f"rgb({r},{g},{b})"
                if c == 38:
                    fg = col
                else:
                    bg = col
                i += 4
            i += 1
    push(text[last:])
    return out


def strip_ansi(s: str) -> str:
    return ESC_RE.sub("", s)
