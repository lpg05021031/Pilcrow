"""Minimal ANSI SGR → styled segments, for command output (port of web/src/lib/ansi.ts).

Before the colours are read, the text is shown the way a terminal would have shown it
(:func:`overwrite`): a carriage return goes back to the start of the line and what follows writes
over it, so a progress bar keeps its last state instead of every state it went through; ``ESC[K``
and ``ESC[2K`` erase; ``ESC[nA`` moves up to rewrite an earlier line. Other escape sequences are
dropped. Colours stay as the `var(--ansi-…)` strings the web interface uses so the UI can map them
to theme tokens.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Dict, List, Optional, Tuple


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

# ---------------------------------------------------------------- what a terminal would show

# The controls that write over text already shown: a bare carriage return, erase in line, the cursor
# up or down a line, or to a column. Text without any of them is shown as it came.
_CTRL_RE = re.compile(r"\r(?!\n)|\x1b\[[0-9;]*[ABCDGK]")
_TOK_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]|[^\x1b]+")
_NL_RE = re.compile(r"([\r\n])")


def _sgr_units(params: str) -> List[str]:
    """One SGR parameter string as its attributes: ``"1;38;5;196"`` is bold and a colour, two units."""
    codes = params.split(";") if params else [""]
    out: List[str] = []
    i = 0
    while i < len(codes):
        c = codes[i]
        n = 1
        if c in ("38", "48", "58") and i + 1 < len(codes):
            n = 3 if codes[i + 1] == "5" else 5 if codes[i + 1] == "2" else 1
        out.append(";".join(codes[i : i + n]))
        i += n
    return out


_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_LINE_CTRL_RE = re.compile(r"\x1b\[[0-9;]*[ABCDGK]")


def _advance(state: str, params: str) -> str:
    """The SGR state after one ``ESC[…m``."""
    for unit in _sgr_units(params):
        state = "" if unit in ("", "0") else f"{state};{unit}" if state else unit
    return state


class _Screen:
    """Lines and a cursor. A line that nothing writes over is kept as it came (escapes and all); a
    line a control touches becomes cells, each character with the SGR state it was written in."""

    def __init__(self) -> None:
        self.rows: List[Any] = [""]  # str, or [chars, states]
        self.row = 0
        self.col: Optional[int] = 0  # None: at the end of a line kept as it came
        self.state = ""  # the SGR attributes in force since the last reset, ``;``-joined
        self._unfolded: List[str] = []  # lines kept as they came whose SGRs the state has yet to take

    def run(self, text: str) -> str:
        for k, line in enumerate(text.split("\n")):
            if k:
                self._down()
                self.col = 0
            cur = self.rows[self.row]
            if "\r" in line or _LINE_CTRL_RE.search(line):
                self._emulate(line)
            elif cur == "" and self.col == 0:
                self.rows[self.row] = line
                self.col = None
                if "\x1b[" in line:
                    self._unfolded.append(line)
            else:
                self._emulate(line)
        return self._text()

    def _down(self) -> None:
        self.row += 1
        if self.row == len(self.rows):
            self.rows.append("")

    def _emulate(self, line: str) -> None:
        for raw in self._unfolded:
            for m in _SGR_RE.finditer(raw):
                self.state = _advance(self.state, m.group(1))
        self._unfolded = []
        for m in _TOK_RE.finditer(line):
            s = m.group(0)
            if s[0] != "\x1b":
                for k, part in enumerate(s.split("\r")):
                    if k:
                        self.col = 0  # a carriage return: back to the start, the text writes over
                    if part:
                        self._put(part)
            elif m.group(2) is not None:
                self._csi(m.group(1) or "", m.group(2))

    def _cells(self) -> Tuple[List[str], List[str]]:
        cur = self.rows[self.row]
        if isinstance(cur, str):
            chars: List[str] = []
            states: List[str] = []
            st = ""
            for m in _TOK_RE.finditer(cur):
                s = m.group(0)
                if s[0] != "\x1b":
                    chars.extend(s)
                    states.extend([st] * len(s))
                elif m.group(2) == "m":
                    st = _advance(st, m.group(1) or "")
            cur = self.rows[self.row] = [chars, states]
        if self.col is None:
            self.col = len(cur[0])
        return cur[0], cur[1]

    def _put(self, s: str) -> None:
        chars, states = self._cells()
        col = self.col or 0
        if col > len(chars):
            pad = col - len(chars)
            chars.extend(" " * pad)
            states.extend([""] * pad)
        end = col + len(s)
        chars[col:end] = s
        states[col:end] = [self.state] * len(s)
        self.col = end

    def _csi(self, params: str, final: str) -> None:
        if final == "m":
            self.state = _advance(self.state, params)
            return
        if final not in "ABCDGK":
            return
        first = params.split(";")[0]
        n = int(first) if first.isdigit() else 0
        if final == "A":
            self.row = max(0, self.row - max(1, n))
            return
        if final == "B":
            for _ in range(max(1, n)):
                self._down()
            return
        chars, states = self._cells()
        col = self.col or 0
        if final == "K":
            if n == 0:
                del chars[col:]
                del states[col:]
            elif n == 1:
                k = min(col, len(chars))
                chars[:k] = " " * k
                states[:k] = [""] * k
            else:
                chars.clear()
                states.clear()
        elif final == "G":
            self.col = max(0, n - 1)
        elif final == "C":
            self.col = col + max(1, n)
        elif final == "D":
            self.col = max(0, col - max(1, n))

    def _text(self) -> str:
        out: List[str] = []
        cur = ""
        for row in self.rows:
            if isinstance(row, str):
                out.append(row)
                if "\x1b[" in row:
                    for m in _SGR_RE.finditer(row):
                        cur = _advance(cur, m.group(1))
            else:
                for st, group in groupby(zip(row[0], row[1]), key=lambda cs: cs[1]):
                    if st != cur:
                        if cur:
                            out.append("\x1b[0m")
                        if st:
                            out.append(f"\x1b[{st}m")
                        cur = st
                    out.append("".join(c for c, _s in group))
            out.append("\n")
        out.pop()
        if cur:
            out.append("\x1b[0m")
        return "".join(out)


def overwrite(text: str) -> str:
    """Command output as a terminal would have shown it (see the module doc). ``\\r\\n`` is a line end;
    text with no overwriting controls comes back as it is."""
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    if not _CTRL_RE.search(text):
        return text
    return _Screen().run(text)


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
    text = overwrite(text)
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
    return ESC_RE.sub("", overwrite(s))
