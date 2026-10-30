"""A small retained layout for the inside of a block: columns, rows, text, documents, surfaces,
icons, badges, buttons, links and toggles, measured once per width and painted with QPainter.

Nodes are data, not widgets: ``layout(x, y, w, m)`` assigns rectangles (in block coordinates),
``paint`` draws, ``hit`` finds the target under a point. Tool bodies, notices and footers are node
trees built by small functions; the block that owns a tree measures and paints it and routes the
targets back to its handlers. A tree is built when its block warms and dropped when it cools.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QTextDocument

from .. import theme
from .paint import FULL, Measurer, PaintState, doc_height, draw_doc, find_ranges, paint_decor, rounded, selection
from .scene import Target

Pad = Tuple[int, int, int, int]  # left, top, right, bottom
NO_PAD: Pad = (0, 0, 0, 0)


class Node:
    __slots__ = ("x", "y", "w", "h", "hidden")

    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.w = 0
        self.h = 0
        self.hidden = False

    # ---- layout
    def natural_w(self, m: Measurer) -> int:
        return 0

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        self.h = 0
        return 0

    def shift(self, dx: int, dy: int) -> None:
        self.x += dx
        self.y += dy

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    # ---- drawing and hits
    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        pass

    def hit(self, x: int, y: int) -> Optional[Target]:
        return None

    def targets(self) -> Iterator[Target]:
        return iter(())

    def text(self) -> str:
        return ""

    def cool(self) -> None:
        pass

    def live(self) -> bool:
        return False


class Container(Node):
    __slots__ = ("children", "spacing", "pad")

    def __init__(self, children: Sequence[Node] = (), spacing: int = 0, pad: Pad = NO_PAD) -> None:
        super().__init__()
        self.children: List[Node] = [c for c in children if c is not None]
        self.spacing = spacing
        self.pad = pad

    def add(self, c: Optional[Node]) -> Optional[Node]:
        if c is not None:
            self.children.append(c)
        return c

    def shift(self, dx: int, dy: int) -> None:
        super().shift(dx, dy)
        for c in self.children:
            c.shift(dx, dy)

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        clip = st.clip
        for c in self.children:
            if c.hidden or c.h <= 0:
                continue
            if clip is not None and (oy + c.y > clip.bottom() or oy + c.y + c.h < clip.top()):
                continue
            c.paint(p, ox, oy, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        for c in self.children:
            if not c.hidden and c.contains(x, y):
                t = c.hit(x, y)
                if t is not None:
                    return t
        return None

    def targets(self) -> Iterator[Target]:
        for c in self.children:
            if not c.hidden:
                yield from c.targets()

    def text(self) -> str:
        return "\n".join(t for t in (c.text() for c in self.children if not c.hidden) if t)

    def cool(self) -> None:
        for c in self.children:
            c.cool()

    def live(self) -> bool:
        return any(c.live() for c in self.children if not c.hidden)


class Column(Container):
    __slots__ = ()

    def natural_w(self, m: Measurer) -> int:
        return max([c.natural_w(m) for c in self.children if not c.hidden] or [0]) + self.pad[0] + self.pad[2]

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        pl, t, r, b = self.pad
        inner = max(1, w - pl - r)
        cy = y + t
        first = True
        for c in self.children:
            if c.hidden:
                continue
            if not first:
                cy += self.spacing
            h = c.layout(x + pl, cy, inner, m)
            if h <= 0 and not c.hidden:
                cy -= 0 if first else self.spacing  # an empty child takes no gap
                first = first  # noqa: PLW0127 - keep "first" as it was
                continue
            cy += h
            first = False
        self.h = (cy - y) + b if not first else 0
        return self.h


class Row(Container):
    """Children side by side. ``stretch`` marks the children that share the remaining width;
    ``valign`` is 'center' (default), 'top' or 'bottom'."""

    __slots__ = ("stretch", "valign", "min_h")

    def __init__(
        self,
        children: Sequence[Node] = (),
        spacing: int = 8,
        pad: Pad = NO_PAD,
        stretch: Sequence[int] = (),
        valign: str = "center",
        min_h: int = 0,
    ) -> None:
        super().__init__(children, spacing, pad)
        self.stretch = list(stretch)
        self.valign = valign
        self.min_h = min_h

    def natural_w(self, m: Measurer) -> int:
        shown = [c for c in self.children if not c.hidden]
        return sum(c.natural_w(m) for c in shown) + self.spacing * max(0, len(shown) - 1) + self.pad[0] + self.pad[2]

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        pl, t, r, b = self.pad
        shown = [(i, c) for i, c in enumerate(self.children) if not c.hidden]
        if not shown:
            self.h = 0
            return 0
        fixed = 0
        n_stretch = 0
        for i, c in shown:
            if i in self.stretch:
                n_stretch += 1
            else:
                fixed += c.natural_w(m)
        avail = w - pl - r - self.spacing * (len(shown) - 1) - fixed
        each = max(24, avail // n_stretch) if n_stretch else 0
        cx = x + pl
        heights: List[int] = []
        for i, c in shown:
            cw = each if i in self.stretch else min(c.natural_w(m), max(1, w - pl - r))
            h = c.layout(cx, y + t, cw, m)
            heights.append(h)
            cx += cw + self.spacing
        row_h = max(max(heights), self.min_h)
        for (_i, c), h in zip(shown, heights):
            if self.valign == "center":
                c.shift(0, (row_h - h) // 2)
            elif self.valign == "bottom":
                c.shift(0, row_h - h)
        self.h = row_h + t + b
        return self.h


class Spacer(Node):
    __slots__ = ("sw", "sh")

    def __init__(self, w: int = 0, h: int = 0) -> None:
        super().__init__()
        self.sw, self.sh = w, h

    def natural_w(self, m: Measurer) -> int:
        return self.sw

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, self.sh
        return self.h


class Hairline(Node):
    __slots__ = ("token",)

    def __init__(self, token: str = "hair") -> None:
        super().__init__()
        self.token = token

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, 1
        return 1

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        p.fillRect(QRect(ox + self.x, oy + self.y, self.w, 1), theme.blend(getattr(st.t, self.token), st.t.canvas))


class Text(Node):
    """Words. Wrapping text is a document; a single line is static text, elided to its width."""

    __slots__ = (
        "_text",
        "size",
        "weight",
        "color",
        "mono",
        "wrap",
        "html",
        "line_h",
        "italic",
        "_doc",
        "_font",
        "strike",
        "max_h",
        "align",
    )

    def __init__(
        self,
        text: str,
        size: float = theme.FS_UI,
        weight: int = 400,
        color: Any = "text",
        mono: bool = False,
        wrap: bool = True,
        html: bool = False,
        line_h: Optional[int] = None,
        italic: bool = False,
        strike: bool = False,
        max_h: int = 0,
        align: str = "left",
    ) -> None:
        super().__init__()
        self._text = text
        self.size, self.weight, self.color, self.mono = size, weight, color, mono
        self.wrap, self.html, self.line_h, self.italic, self.strike = wrap, html, line_h, italic, strike
        self.max_h = max_h
        self.align = align
        self._doc: Optional[QTextDocument] = None
        self._font: Optional[QFont] = None

    def font(self, m: Measurer) -> QFont:
        if self._font is None:
            f = QFont(m.font(self.size, self.weight, self.mono, self.italic))
            if self.strike:
                f.setStrikeOut(True)
            self._font = f
        return self._font

    def natural_w(self, m: Measurer) -> int:
        if not self._text:
            return 0
        if self.html and self.wrap:
            doc = m.html_doc(self._text, 100000, self.font(m))
            return int(math.ceil(doc.idealWidth())) + 2
        return int(math.ceil(m.text_w(self._text, self.font(m)))) + 2

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        if not self._text:
            self.h = 0
            return 0
        f = self.font(m)
        if self.wrap:
            if self._doc is None or int(self._doc.textWidth()) != w:
                if self.html:
                    css = f"p {{ margin: 0; line-height: {self.line_h}px; }}" if self.line_h else "p { margin: 0; }"
                    self._doc = m.html_doc(self._text, w, f, css)
                else:
                    self._doc = m.plain_doc(self._text, w, f)
                    if self.line_h:
                        from PySide6.QtGui import QTextBlockFormat, QTextCursor

                        cur = QTextCursor(self._doc)
                        cur.select(QTextCursor.SelectionType.Document)
                        bf = QTextBlockFormat()
                        bf.setLineHeight(self.line_h, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                        cur.mergeBlockFormat(bf)
            self.h = doc_height(self._doc)
            if self.max_h and self.h > self.max_h:
                self.h = self.max_h
        else:
            self.h = self.line_h or m.line_h(f)
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        if not self._text:
            return
        color = st.m.color(self.color)
        f = self.font(st.m)
        if self.wrap and self._doc is not None:
            clip = QRect(ox + self.x, oy + self.y, self.w, self.h)
            if st.clip is not None:
                clip = clip.intersected(st.clip)
            sels = _find_selections(self._doc, st)
            draw_doc(p, self._doc, ox + self.x, oy + self.y, clip, color, sels or None)
            return
        shown = st.m.elide(self._text, f, self.w)
        tw = st.m.text_w(shown, f)
        x = ox + self.x
        if self.align == "right":
            x = ox + self.x + self.w - tw
        elif self.align == "center":
            x = ox + self.x + (self.w - tw) / 2
        y = oy + self.y + (self.h - st.m.line_h(f)) / 2
        st.m.draw_text(p, x, y, shown, f, color)

    def text(self) -> str:
        if self.html:
            import re

            return re.sub(r"<[^>]+>", "", self._text)
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._doc = None

    def cool(self) -> None:
        self._doc = None
        self._font = None


def _find_selections(doc: QTextDocument, st: PaintState) -> List[Any]:
    sels: List[Any] = []
    t = st.t
    if st.needle:
        for k, (a, b) in enumerate(find_ranges(doc, st.needle)):
            if k == st.needle_current:
                sels.append(selection(doc, a, b, QColor(t.accent), QColor("#ffffff")))
            else:
                sels.append(selection(doc, a, b, theme.blend("rgba(217, 119, 87, 0.28)", t.canvas)))
    if st.selection is not None:
        bg = theme.blend(t.selection, t.canvas)
        if st.selection == FULL:
            sels.append(selection(doc, 0, doc.characterCount(), bg))
        else:
            a, b = st.selection
            sels.append(selection(doc, a, b, bg))
    return sels


class Markdown(Node):
    """A Markdown text as a document with its decorations (code wells, pills, quotes, tables)."""

    __slots__ = ("src", "compact", "quiet", "cwd", "streaming", "line_h", "_doc", "color")

    def __init__(
        self,
        src: str,
        compact: bool = False,
        quiet: bool = False,
        cwd: str = "",
        streaming: bool = False,
        line_h: Optional[int] = None,
        color: Any = None,
    ) -> None:
        super().__init__()
        self.src, self.compact, self.quiet, self.cwd, self.streaming, self.line_h = (
            src,
            compact,
            quiet,
            cwd,
            streaming,
            line_h,
        )
        self.color = color
        self._doc: Optional[QTextDocument] = None

    @property
    def doc(self) -> Optional[QTextDocument]:
        return self._doc

    def natural_w(self, m: Measurer) -> int:
        doc = m.markdown_doc(self.src, 100000, self.compact, self.quiet, self.streaming, self.cwd, self.line_h)
        return int(math.ceil(doc.idealWidth())) + 2

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        if not self.src:
            self.h = 0
            return 0
        if self._doc is None or int(self._doc.textWidth()) != w:
            self._doc = m.markdown_doc(self.src, w, self.compact, self.quiet, self.streaming, self.cwd, self.line_h)
        self.h = doc_height(self._doc)
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        if self._doc is None:
            return
        clip = QRect(ox + self.x, oy + self.y, self.w, self.h)
        if st.clip is not None:
            clip = clip.intersected(st.clip)
        paint_decor(p, self._doc, ox + self.x, oy + self.y, clip, st.t)
        color = st.m.color(self.color) if self.color is not None else QColor(st.t.text_2 if self.quiet else st.t.text)
        sels = _find_selections(self._doc, st)
        draw_doc(p, self._doc, ox + self.x, oy + self.y, clip, color, sels or None)

    def text(self) -> str:
        return self.src

    def cool(self) -> None:
        self._doc = None


class Surface(Node):
    """A rounded panel around one child: ``bg`` and ``border`` are token names (``"light:hair"``
    draws the border in the light theme only)."""

    __slots__ = ("child", "bg", "border", "radius", "pad")

    def __init__(
        self,
        child: Node,
        bg: Optional[str] = "inset",
        border: Optional[str] = "light:hair",
        radius: int = theme.R3,
        pad: Pad = (12, 10, 12, 10),
    ) -> None:
        super().__init__()
        self.child, self.bg, self.border, self.radius, self.pad = child, bg, border, radius, pad

    def natural_w(self, m: Measurer) -> int:
        return self.child.natural_w(m) + self.pad[0] + self.pad[2]

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        pl, t, r, b = self.pad
        h = self.child.layout(x + pl, y + t, max(1, w - pl - r), m)
        self.h = h + t + b if h > 0 else 0
        return self.h

    def shift(self, dx: int, dy: int) -> None:
        super().shift(dx, dy)
        self.child.shift(dx, dy)

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        if self.h <= 0:
            return
        t = st.t
        border = self.border
        if border and border.startswith("light:"):
            border = None if t.dark else border[6:]
        rounded(
            p,
            QRect(ox + self.x, oy + self.y, self.w, self.h),
            t.color(self.bg) if self.bg else None,
            t.color(border) if border else None,
            self.radius,
        )
        self.child.paint(p, ox, oy, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return self.child.hit(x, y) if self.child.contains(x, y) else None

    def targets(self) -> Iterator[Target]:
        return self.child.targets()

    def text(self) -> str:
        return self.child.text()

    def cool(self) -> None:
        self.child.cool()

    def live(self) -> bool:
        return self.child.live()


class Icon(Node):
    __slots__ = ("name", "color", "size", "alpha")

    def __init__(self, name: str, color: Any = "text_2", size: int = 14, alpha: float = 1.0) -> None:
        super().__init__()
        self.name, self.color, self.size, self.alpha = name, color, size, alpha

    def natural_w(self, m: Measurer) -> int:
        return self.size

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, self.size
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        pm = st.m.icon(self.name, st.m.color(self.color), self.size)
        if self.alpha < 1.0:
            p.setOpacity(self.alpha)
        p.drawPixmap(ox + self.x, oy + self.y, pm)
        if self.alpha < 1.0:
            p.setOpacity(1.0)


class Badge(Node):
    """A small pill: tones '', 'err', 'ok', 'info', 'accent', 'plan'."""

    __slots__ = ("label", "tone")

    def __init__(self, label: str, tone: str = "") -> None:
        super().__init__()
        self.label, self.tone = label, tone

    def _font(self, m: Measurer) -> QFont:
        return m.font(11, 600)

    def natural_w(self, m: Measurer) -> int:
        return int(math.ceil(m.text_w(self.label, self._font(m)))) + 14

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, 19
        return 19

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        t = st.t
        fg, bg = {
            "err": (t.err, t.err_wash),
            "ok": (t.ok, t.ok_wash),
            "info": (t.info, t.info_wash),
            "accent": (t.accent_text, t.accent_wash),
            "plan": (t.plan, t.plan_wash),
        }.get(self.tone, (t.text_2, t.fill_2))
        rounded(p, QRect(ox + self.x, oy + self.y, self.w, self.h), theme.blend(bg, t.canvas), None, 9)
        f = self._font(st.m)
        st.m.draw_text(p, ox + self.x + 7, oy + self.y + (19 - st.m.line_h(f)) / 2, self.label, f, QColor(fg))

    def text(self) -> str:
        return self.label


BUTTON_METRICS = {
    "": {"h": 30, "px": 12, "gap": 6, "fs": theme.FS_UI, "fw": 500, "r": theme.R2},
    "sm": {"h": 26, "px": 10, "gap": 6, "fs": theme.FS_SMALL, "fw": 500, "r": theme.R2},
    "xs": {"h": 24, "px": 9, "gap": 5, "fs": theme.FS_SMALL, "fw": 500, "r": theme.R2},
    "perm": {"h": 28, "px": 11, "gap": 6, "fs": theme.FS_UI, "fw": 500, "r": 7},
}


class Button(Node):
    """A painted button: kinds '' (quiet fill), 'primary', 'ghost', 'danger', 'link', 'more'."""

    __slots__ = ("label", "icon", "kind", "size", "tid", "enabled", "icon_px")

    def __init__(
        self,
        label: str,
        tid: str,
        icon: str = "",
        kind: str = "",
        size: str = "sm",
        enabled: bool = True,
        icon_px: int = 13,
    ) -> None:
        super().__init__()
        self.label, self.tid, self.icon, self.kind, self.size, self.enabled, self.icon_px = (
            label,
            tid,
            icon,
            kind,
            size,
            enabled,
            icon_px,
        )

    def _m(self) -> dict:
        if self.kind in ("link", "more"):
            return {
                "h": 22 if self.kind == "more" else 18,
                "px": 6 if self.kind == "more" else 0,
                "gap": 4,
                "fs": theme.FS_SMALL,
                "fw": 400,
                "r": theme.R2,
            }
        return BUTTON_METRICS.get(self.size, BUTTON_METRICS["sm"])

    def _font(self, m: Measurer) -> QFont:
        mm = self._m()
        return m.font(mm["fs"], mm["fw"])

    def natural_w(self, m: Measurer) -> int:
        mm = self._m()
        w = mm["px"] * 2
        if self.icon:
            w += self.icon_px + (mm["gap"] if self.label else 0)
        if self.label:
            w += int(math.ceil(m.text_w(self.label, self._font(m))))
        return w

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, self._m()["h"]
        return self.h

    def target(self) -> Target:
        return Target("button", self.tid, (self.x, self.y, self.w, self.h))

    def hit(self, x: int, y: int) -> Optional[Target]:
        return self.target() if self.enabled else None

    def targets(self) -> Iterator[Target]:
        if self.enabled:
            yield self.target()

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "button" and st.hover.id == self.tid and self.enabled
        pressed = st.pressed is not None and st.pressed.kind == "button" and st.pressed.id == self.tid
        mm = self._m()
        k = self.kind
        bg: Optional[QColor]
        if k == "primary":
            bg = QColor(t.accent_2 if (hover or pressed) else t.accent)
            fg = QColor(t.accent_ink)
            if not self.enabled:
                bg, fg = theme.blend(t.fill_2, t.canvas), QColor(t.text_4)
        elif k == "ghost":
            bg = theme.blend(t.fill_2, t.canvas) if hover else None
            fg = QColor(t.text if hover else t.text_2)
        elif k == "danger":
            bg = theme.blend(t.err_wash, t.canvas) if hover else None
            fg = QColor(t.err)
        elif k in ("link", "more"):
            bg = theme.blend(t.fill_2, t.canvas) if (hover and k == "more") else None
            fg = QColor(t.text if hover else t.text_3)
        else:
            bg = theme.blend(t.fill_3 if (hover or pressed) else t.fill_2, t.canvas)
            fg = QColor(t.text)
        if not self.enabled and k != "primary":
            p.setOpacity(0.45)
        r = QRect(ox + self.x, oy + self.y, self.w, self.h)
        if bg is not None:
            rounded(p, r, bg, None, mm["r"])
        f = self._font(st.m)
        x = r.x() + mm["px"]
        if self.icon:
            pm = st.m.icon(self.icon, fg, self.icon_px)
            p.drawPixmap(x, r.y() + (self.h - self.icon_px) // 2, pm)
            x += self.icon_px + (mm["gap"] if self.label else 0)
        if self.label:
            st.m.draw_text(p, x, r.y() + (self.h - st.m.line_h(f)) / 2, self.label, f, fg)
        if st.focus is not None and st.focus.kind == "button" and st.focus.id == self.tid:
            rounded(p, r.adjusted(-2, -2, 2, 2), None, QColor(t.accent_ring), mm["r"] + 2, 1.5)
        p.setOpacity(1.0)

    def text(self) -> str:
        return self.label


class Link(Node):
    """Quiet text that acts on a click (a file path, "Plan file", a search hit)."""

    __slots__ = ("label", "tid", "mono", "size", "data")

    def __init__(self, label: str, tid: str, mono: bool = False, size: float = 0, data: Any = None) -> None:
        super().__init__()
        self.label, self.tid, self.mono, self.size, self.data = label, tid, mono, size, data

    def _font(self, m: Measurer) -> QFont:
        return m.font(self.size or (12 if self.mono else theme.FS_UI), 400, self.mono)

    def natural_w(self, m: Measurer) -> int:
        return int(math.ceil(m.text_w(self.label, self._font(m)))) + 2

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, m.line_h(self._font(m)) + 4
        return self.h

    def target(self) -> Target:
        return Target("link", self.tid, (self.x, self.y, self.w, self.h), self.data)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return self.target()

    def targets(self) -> Iterator[Target]:
        yield self.target()

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        hover = st.hover is not None and st.hover.kind == "link" and st.hover.id == self.tid
        f = self._font(st.m)
        color = QColor(st.t.text if hover else st.t.text_2)
        shown = st.m.elide(self.label, f, self.w)
        st.m.draw_text(p, ox + self.x, oy + self.y + 2, shown, f, color)
        if hover:
            w = st.m.text_w(shown, f)
            p.fillRect(QRectF(ox + self.x, oy + self.y + self.h - 2, w, 1), color)

    def text(self) -> str:
        return self.label


class Image(Node):
    __slots__ = ("pm", "max_w", "tid", "_scaled")

    def __init__(self, pm: QPixmap, tid: str, max_w: int = 520) -> None:
        super().__init__()
        self.pm, self.tid, self.max_w = pm, tid, max_w
        self._scaled: Optional[QPixmap] = None

    def natural_w(self, m: Measurer) -> int:
        return min(self.max_w, self.pm.width())

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y = x, y
        tw = min(self.max_w, w, self.pm.width()) if not self.pm.isNull() else 0
        if tw <= 0:
            self.w = self.h = 0
            return 0
        if self._scaled is None or self._scaled.width() != tw:
            self._scaled = self.pm.scaledToWidth(tw, Qt.TransformationMode.SmoothTransformation)
        self.w, self.h = self._scaled.width(), self._scaled.height()
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        if self._scaled is not None:
            p.drawPixmap(ox + self.x, oy + self.y, self._scaled)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("image", self.tid, (self.x, self.y, self.w, self.h))

    def cool(self) -> None:
        self._scaled = None


class Toggle(Node):
    """A chevron and a label that show or hide a child (``Details``, ``Instructions``, ``Result``)."""

    __slots__ = ("label", "tid", "child", "open", "count")

    def __init__(self, label: str, tid: str, child: Node, open_: bool = False) -> None:
        super().__init__()
        self.label, self.tid, self.child, self.open = label, tid, child, open_

    def _font(self, m: Measurer) -> QFont:
        return m.font(theme.FS_SMALL, 400)

    def natural_w(self, m: Measurer) -> int:
        return int(math.ceil(m.text_w(self.label, self._font(m)))) + 12 + 4 + 8

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        head = 22
        if self.open:
            h = self.child.layout(x, y + head + 4, w, m)
            self.h = head + (4 + h if h > 0 else 0)
        else:
            self.h = head
        return self.h

    def shift(self, dx: int, dy: int) -> None:
        super().shift(dx, dy)
        self.child.shift(dx, dy)

    def head_target(self) -> Target:
        return Target("toggle", self.tid, (self.x, self.y, self.w, 22))

    def hit(self, x: int, y: int) -> Optional[Target]:
        if y < self.y + 22:
            return self.head_target()
        if self.open and self.child.contains(x, y):
            return self.child.hit(x, y)
        return None

    def targets(self) -> Iterator[Target]:
        yield self.head_target()
        if self.open:
            yield from self.child.targets()

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "toggle" and st.hover.id == self.tid
        color = QColor(t.text if hover else t.text_3)
        f = self._font(st.m)
        nat = self.natural_w(st.m)
        if hover:
            rounded(
                p, QRect(ox + self.x - 4, oy + self.y, nat + 4, 22), theme.blend(t.fill_1, t.canvas), None, theme.R2
            )
        pm = st.m.icon("chevron-down" if self.open else "chevron-right", color, 12)
        p.drawPixmap(ox + self.x, oy + self.y + 5, pm)
        st.m.draw_text(p, ox + self.x + 16, oy + self.y + (22 - st.m.line_h(f)) / 2, self.label, f, color)
        if self.open:
            self.child.paint(p, ox, oy, st)

    def text(self) -> str:
        return self.label + ("\n" + self.child.text() if self.open else "")

    def cool(self) -> None:
        self.child.cool()


class Spinner(Node):
    """A turning arc; it asks for clock ticks through ``live()``."""

    __slots__ = ("size", "tone")

    def __init__(self, size: int = 14, tone: str = "text_3") -> None:
        super().__init__()
        self.size, self.tone = size, tone

    def natural_w(self, m: Measurer) -> int:
        return self.size

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, self.size
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(st.t.color(self.tone), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        ang = int((st.now % 1.0) * 360)
        p.drawArc(QRectF(ox + self.x + 1.5, oy + self.y + 1.5, self.size - 3, self.size - 3), -ang * 16, 270 * 16)

    def live(self) -> bool:
        return True


class Painted(Node):
    """A node drawn by a function: ``fn(p, rect, st)``; fixed size."""

    __slots__ = ("fn", "sw", "sh", "tid", "kind")

    def __init__(
        self, fn: Callable[[QPainter, QRect, PaintState], None], w: int, h: int, tid: str = "", kind: str = "mark"
    ) -> None:
        super().__init__()
        self.fn, self.sw, self.sh, self.tid, self.kind = fn, w, h, tid, kind

    def natural_w(self, m: Measurer) -> int:
        return self.sw

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w, self.h = x, y, w, self.sh
        return self.h

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        self.fn(p, QRect(ox + self.x, oy + self.y, self.sw, self.sh), st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target(self.kind, self.tid, (self.x, self.y, self.w, self.h)) if self.tid else None


def faint(text: str, size: float = theme.FS_SMALL, wrap: bool = False) -> Text:
    return Text(text, size, 400, "text_3", wrap=wrap)


def note(text: str, wrap: bool = True) -> Text:
    return Text(text, theme.FS_SMALL, 400, "text_2", wrap=wrap)


def label(text: str) -> Text:
    """`.tool-label`: a micro, semibold heading in text_3."""
    return Text(text, theme.FS_MICRO, 600, "text_3", wrap=False)


def pre(text: str, err: bool = False, limit: int = 60000, size: int = 12) -> Surface:
    """Preformatted mono text on an inset well."""
    import html as _html

    from ..lib.fmt import truncate

    lh = int(size * 1.55 + 0.5)
    body = (
        f'<div style="white-space:pre-wrap;line-height:{lh}px">{_html.escape(truncate(text, limit)) or "&nbsp;"}</div>'
    )
    return Surface(
        Text(body, size, 400, "text_2" if err else "text", mono=True, html=True),
        "inset",
        "light:hair",
        theme.R3,
        (12, 10, 12, 10),
    )


def vstack(*children: Optional[Node], spacing: int = 8, pad: Pad = NO_PAD) -> Column:
    return Column([c for c in children if c is not None], spacing, pad)


def hstack(
    *children: Optional[Node], spacing: int = 8, stretch: Sequence[int] = (), pad: Pad = NO_PAD, valign: str = "center"
) -> Row:
    return Row([c for c in children if c is not None], spacing, pad, stretch, valign)


__all__ = [
    "Badge",
    "Button",
    "Column",
    "Container",
    "Hairline",
    "Icon",
    "Image",
    "Link",
    "Markdown",
    "Node",
    "Painted",
    "Row",
    "Spacer",
    "Spinner",
    "Surface",
    "Text",
    "Toggle",
    "faint",
    "hstack",
    "label",
    "note",
    "pre",
    "vstack",
]
