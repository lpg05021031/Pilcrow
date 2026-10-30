"""Heights without layout: estimates from the text and the fonts, calibrated by what was measured.

``Metrics`` carries the few numbers the estimator needs (average glyph widths, line heights,
margins); the paint layer builds one from the theme's fonts, so this module stays free of Qt.
``Calibration`` keeps, per block kind, the exponential moving average of ``exact / estimate`` over
the blocks that have been measured, and applies it to new estimates: the scrollbar is right from the
start and gets righter as the conversation is read.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List

HAN = re.compile(r"[ᄀ-ᇿ⺀-꓏가-힯豈-﫿︰-﹏＀-￯]")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s")
TABLE_ROW = re.compile(r"^\s*\|")


@dataclass(frozen=True)
class Metrics:
    avg_w: float  # average advance of a Latin glyph in the prose font
    em_w: float  # advance of a CJK glyph in the prose font
    mono_w: float  # advance of a glyph in the mono font
    line: int  # prose line height
    mono_line: int  # code line height
    para: int  # paragraph bottom margin
    code_pad: int  # a code well's head and padding in total
    heading_scale: float = 1.2
    list_indent: float = 24.0


@dataclass
class Calibration:
    """Per block kind, how far the estimates have been from the truth (1.0 = spot on)."""

    alpha: float = 0.1
    factors: Dict[str, float] = field(default_factory=dict)
    samples: Dict[str, int] = field(default_factory=dict)

    def factor(self, kind: str) -> float:
        return self.factors.get(kind, 1.0)

    def observe(self, kind: str, estimate: int, exact: int) -> None:
        if estimate <= 0 or exact <= 0:
            return
        ratio = max(0.25, min(4.0, exact / estimate))
        n = self.samples.get(kind, 0)
        cur = self.factors.get(kind, 1.0)
        # the first few samples count more, so one measurement already helps
        a = max(self.alpha, 1.0 / (n + 1))
        self.factors[kind] = cur + (ratio - cur) * a
        self.samples[kind] = n + 1

    def apply(self, kind: str, estimate: int) -> int:
        return max(1, int(round(estimate * self.factor(kind))))


def text_width(s: str, m: Metrics) -> float:
    """The width of a line of prose: Latin glyphs at the average advance, CJK at the em."""
    if not s:
        return 0.0
    han = len(HAN.findall(s))
    return (len(s) - han) * m.avg_w + han * m.em_w


def lines_for(s: str, width: float, m: Metrics, indent: float = 0.0) -> int:
    avail = max(40.0, width - indent)
    w = text_width(s, m)
    return max(1, int(math.ceil(w / avail)))


def estimate_markdown(src: str, width: int, m: Metrics) -> int:
    """A Markdown text's height at ``width``: paragraphs by estimated wrapping, fences by line count,
    headings a little taller, tables and lists by their rows."""
    if not src:
        return 0
    h = 0
    in_fence = False
    fence_lines = 0
    para: List[str] = []

    def flush_para() -> None:
        nonlocal h, para
        if para:
            text = " ".join(x.strip() for x in para)
            h += lines_for(text, width, m) * m.line + m.para
            para = []

    for raw in src.split("\n"):
        if FENCE.match(raw):
            if in_fence:
                h += fence_lines * m.mono_line + m.code_pad + m.para
                in_fence = False
                fence_lines = 0
            else:
                flush_para()
                in_fence = True
            continue
        if in_fence:
            fence_lines += 1
            continue
        if not raw.strip():
            flush_para()
            continue
        hm = HEADING.match(raw)
        if hm:
            flush_para()
            level = len(hm.group(1))
            scale = {1: 1.32, 2: 1.16, 3: 1.04}.get(level, 1.0)
            h += int(round(m.line * scale * 1.1)) + int(round(m.line * 0.9))
            continue
        if LIST.match(raw):
            flush_para()
            h += lines_for(raw, width, m, m.list_indent) * m.line + int(round(m.line * 0.15))
            continue
        if TABLE_ROW.match(raw):
            flush_para()
            if re.match(r"^\s*\|?\s*:?-{2,}", raw):
                continue  # the header rule
            h += int(round(m.line * 0.93)) + 14
            continue
        para.append(raw)
    flush_para()
    if in_fence:
        h += fence_lines * m.mono_line + m.code_pad
    return max(m.line, h - m.para)  # the last block carries no margin


def estimate_lines(n_lines: int, m: Metrics, pad: int = 0) -> int:
    return max(0, n_lines) * m.mono_line + pad
