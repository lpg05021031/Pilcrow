"""The conversation (a port of the Transcript component in web/src/components/Transcript.tsx).

A scroll area whose centred column holds the conversation in pages of a few units each. A page
far from the viewport is an empty box of about the right height until the reader comes near; then
it is drawn once and stays drawn (remembered per conversation, like the scroll position). The
live end is always drawn. New output is followed smoothly while the reader is at the bottom; the
moment they scroll up the follow stops and a "Latest" pill offers the way back. Every render
compares each item's ``rev`` with what was drawn and touches only the rows that changed.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from shiboken6 import isValid

from ... import icons, theme
from ...lib.tools_meta import tool_meta
from ...lib.transcript_model import Item
from ...lib.units import ItemsUnit, Unit, WorkUnit, group_turns, live_turn_folds
from ...store import store
from ..ui.signals import bind
from .ctx import ToolCtx
from .folds import UnitBox, forget_folds, make_permission_card, turn_open, unit_sig
from .items import BUSY, LoadingSkeleton, WorkingLine, pt

PAGE = 8
EST = {"items": 260, "work": 44}  # stand-in heights, matching transcript.css
NEAR = 1600  # how far ahead of the viewport pages are drawn


@dataclass
class ScrollPos:
    index: int
    offset: int
    bottom: bool


# Where each conversation was scrolled to, so switching away and back keeps your place: the unit at
# the top of the view and how far it was from the edge (an offset alone would drift, since pages
# far off screen only have estimated heights until they have been seen).
scroll_memory: Dict[str, ScrollPos] = {}
# Which pages have been drawn, per conversation.
drawn_pages: Dict[str, Set[str]] = {}


class PageBox(QWidget):
    """One box of a few units. Far from the viewport it is empty and ``est`` tall, with faint lines
    where its rows will be; drawn, it stacks its UnitBoxes with collapsed margins."""

    def __init__(self, key: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        self.far = True
        self.est = 0
        self.boxes: List[UnitBox] = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_est(self, est: int) -> None:
        self.est = est
        if self.far:
            self.setFixedHeight(est)

    def draw(self, units: List[Unit], sigs: List[str], ctx: ToolCtx, prev_bottom: int) -> None:
        if self.far:
            self.far = False
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        keep = 0
        for keep, (u, b) in enumerate(zip(units, self.boxes)):  # noqa: B007
            if u.key != b.key:
                break
        else:
            keep = min(len(units), len(self.boxes))
        while len(self.boxes) > keep:
            b = self.boxes.pop()
            while self.lay.count() and self.lay.itemAt(self.lay.count() - 1).widget() is not b:
                del_item = self.lay.takeAt(self.lay.count() - 1)
                del del_item
            self.lay.removeWidget(b)
            b.deleteLater()
        for i, u in enumerate(units):
            if i < len(self.boxes):
                self.boxes[i].update_unit(u, ctx, sigs[i])
            else:
                b = UnitBox(u, ctx)
                if i > 0:
                    self.lay.addSpacing(0)
                self.lay.addWidget(b)
                self.boxes.append(b)
        self._space(prev_bottom)
        self.update()

    def _space(self, prev_bottom: int) -> None:
        """Block margins collapse: the gap before a unit is the larger of the two margins that meet there."""
        for i, b in enumerate(self.boxes):
            top, _bottom = b.margins()
            gap = max(prev_bottom, top)
            if i == 0:
                self.lay.setContentsMargins(0, gap, 0, 0)
            else:
                sp = self.lay.itemAt(2 * i - 1).spacerItem()
                if sp is not None and sp.sizeHint().height() != gap:
                    sp.changeSize(0, gap, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
                    self.lay.invalidate()
            prev_bottom = b.margins()[1]

    def last_margin(self) -> int:
        return self.boxes[-1].margins()[1] if self.boxes else 10

    def paintEvent(self, e) -> None:  # noqa: N802
        if not self.far:
            super().paintEvent(e)
            return
        # faint lines where rows will be, so a fast fling shows pages taking shape rather than nothing
        p = QPainter(self)
        c = theme.blend(theme.tokens().fill_1, theme.tokens().canvas)
        w = int(self.width() * 0.38)
        y = 10
        while y < self.height() - 8:
            p.fillRect(8, y, w, 8, c)
            y += 200


class Jump(QPushButton):
    """The "Latest" pill over the bottom of the view while the reader is up in the history."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(" Latest", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Latest")
        self.setFixedHeight(30)
        self.setIconSize(QSize(14, 14))
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setIcon(icons.icon("arrow-down", t.text, 14))
        self.setStyleSheet(
            f"QPushButton {{ background: {t.raised}; color: {t.text}; border: 1px solid {theme.blend(t.hair_2, t.raised).name()}; border-radius: 15px; "
            f"padding: 0 12px 0 10px; font-size: {pt(theme.FS_SMALL)}; font-weight: 550; }} QPushButton:hover {{ background: {t.raised_2}; }}"
        )


class Transcript(QScrollArea):
    """The conversation of one session."""

    find_requested = Signal()

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        forget_folds()  # opening a conversation starts it clean: every fold closed
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QWidget()
        ol = QHBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)
        self.inner = QWidget()
        self.inner.setMaximumWidth(theme.COL + 48)
        self.inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ol.addStretch(1)
        ol.addWidget(self.inner, 100)
        ol.addStretch(1)
        il = QVBoxLayout(self.inner)
        il.setContentsMargins(24, 22, 24, 44)
        il.setSpacing(0)
        self.loading = LoadingSkeleton()
        self.loading.setVisible(False)
        il.addWidget(self.loading)
        self.empty_host = QWidget()
        QVBoxLayout(self.empty_host).setContentsMargins(0, 0, 0, 0)
        self.empty_host.setVisible(False)
        il.addWidget(self.empty_host)
        self.units_host = QWidget()
        self.units_lay = QVBoxLayout(self.units_host)
        self.units_lay.setContentsMargins(0, 0, 0, 0)
        self.units_lay.setSpacing(0)
        il.addWidget(self.units_host)
        self.orphans_host = QWidget()
        self.orphans_lay = QVBoxLayout(self.orphans_host)
        self.orphans_lay.setContentsMargins(0, 10, 0, 0)
        self.orphans_lay.setSpacing(14)
        il.addWidget(self.orphans_host)
        self.working = WorkingLine(session_id)
        il.addWidget(self.working)
        il.addStretch(1)
        self.setWidget(outer)

        self.jump = Jump(self.viewport())
        self.jump.hide()
        self.jump.clicked.connect(self.scroll_to_bottom)
        self.viewport().installEventFilter(self)

        self.ctx: Optional[ToolCtx] = None
        self._pages: List[PageBox] = []
        self._units: List[Unit] = []
        self._drawn: Set[str] = drawn_pages.setdefault(session_id, set())
        # read now: the first layout passes scroll this view before it has been placed, and those
        # must not overwrite the spot the reader left this conversation at
        self._saved: Optional[ScrollPos] = scroll_memory.get(session_id)
        self._orphans: Dict[str, QWidget] = {}
        self._empty: Optional[QWidget] = None
        self._model_rev = -1
        self._placed = False
        self._placing = False
        self._scrolled_cb: Optional[Callable[[bool], None]] = None
        self.find_bar: Optional[QWidget] = None

        # scrolling
        self._at_bottom = True
        self._last_set = 0
        self._landing = False
        self._last_scroll: Tuple[int, float] = (0, 0.0)
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(16)
        self._follow_timer.timeout.connect(self._step)
        self._land_timer = QTimer(self)
        self._land_timer.setSingleShot(True)
        self._land_timer.setInterval(160)
        self._land_timer.timeout.connect(self._landed)
        # restoring a remembered spot: re-placed as the pages around it take their real heights
        self._restore: Optional[ScrollPos] = None
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.setInterval(450)
        self._restore_timer.timeout.connect(self._end_restore)
        self._near_timer = QTimer(self)
        self._near_timer.setSingleShot(True)
        self._near_timer.setInterval(0)
        self._near_timer.timeout.connect(self._draw_near)
        self._anchor_timer = QTimer(self)
        self._anchor_timer.setSingleShot(True)
        self._anchor_timer.setInterval(0)
        self._anchor_timer.timeout.connect(self._remember)
        vb = self.verticalScrollBar()
        vb.valueChanged.connect(self._on_scroll)
        vb.rangeChanged.connect(self._on_range)

        bind(store.rt_changed, self, self._on_rt)
        bind(store.sent, self, lambda sid: sid == self.sid and self.scroll_to_bottom())
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(store.find_open_changed, self, lambda _v: self._render())
        bind(theme.theme.changed, self, lambda _t: self.viewport().update())
        self._render()
        QTimer.singleShot(0, self._initial_place)

    # ---------------------------------------------------------------- public
    def set_empty(self, widget: QWidget) -> None:
        """What shows when the conversation is empty."""
        if self._empty is not None:
            self.empty_host.layout().removeWidget(self._empty)
            self._empty.deleteLater()
        self._empty = widget
        self.empty_host.layout().addWidget(widget)
        self._render()

    def set_scrolled_callback(self, fn: Optional[Callable[[bool], None]]) -> None:
        """``fn(True)`` when the view is scrolled away from its top (for the header's shadow)."""
        self._scrolled_cb = fn

    def open_find(self) -> None:
        store.set_find_open(True)
        self.find_requested.emit()
        if self.find_bar is not None:
            self.find_bar.setFocus()

    def scroll_to_bottom(self) -> None:
        """Your own message goes to the end of the conversation, and so do you."""
        self._at_bottom = True
        self.jump.hide()
        self._follow()

    def units(self) -> List[Unit]:
        return self._units

    def find_entries(self) -> List[Tuple[int, str, str]]:
        """(unit index, item id, text) for everything the find bar searches, from the models, not the widgets."""
        out: List[Tuple[int, str, str]] = []
        cwd = self.cwd
        for i, u in enumerate(self._units):
            for it in u.work if isinstance(u, WorkUnit) else u.items:
                text = _find_text(it, cwd)
                if text:
                    out.append((i, it.id, text))
        return out

    def reveal_item(self, item_id: str) -> Optional[QWidget]:
        """Draw the page and open the fold around an item; the row, or None when it is not here."""
        idx = -1
        for i, u in enumerate(self._units):
            if any(
                it.id == item_id or getattr(it, "uuid", None) == item_id
                for it in (u.work if isinstance(u, WorkUnit) else u.items)
            ):
                idx = i
                break
        if idx < 0:
            return None
        p = idx // PAGE
        page = self._pages[p] if p < len(self._pages) else None
        if page is None:
            return None
        if page.far:
            self._drawn.add(page.key)
            self._render()
        box = page.boxes[idx - p * PAGE]
        fold = box.fold()
        if fold is not None:
            turn_open[fold.key] = True
            fold.open()
        return box.row_for(item_id) or box

    def scroll_to_row(self, w: QWidget) -> None:
        """Bring a row into the upper part of the view unless it is already comfortably visible."""
        vb = self.verticalScrollBar()
        v, vh = vb.value(), self.viewport().height()
        y = self._y(w)
        if y < v + 56 or y + w.height() > v + vh - 24:
            self._at_bottom = False
            self._set(max(0, int(y - vh * 0.38)))

    def first_visible_unit(self) -> int:
        return self._anchor()[0]

    # ---------------------------------------------------------------- rendering
    def _on_rt(self, sid: str) -> None:
        if sid == self.sid:
            self._render()

    def _on_ui(self) -> None:
        if self.ctx is not None and store.ui.view != self.ctx.view:
            self._render()

    def _alive(self) -> bool:
        # A QWidget deletes its children before its own ``destroyed`` fires (which is what unbinds
        # us), so a store signal raised during that teardown must find nothing to draw into.
        return isValid(self) and isValid(self.units_host)

    def _render(self) -> None:
        if not self._alive():
            return
        r = store.rt.get(self.sid)
        m = store.get_model(self.sid)
        loaded = bool(r and r.loaded)
        self.loading.setVisible(not loaded and bool(r and r.loading))
        items = m.items
        self.empty_host.setVisible(loaded and not items and self._empty is not None)
        running = bool(r and r.status in BUSY)
        units = group_turns(items, running)
        self._units = units
        pend: Dict[str, dict] = {}
        for p in r.pending if r else []:
            if p.get("toolUseId"):
                pend[str(p["toolUseId"])] = p
        view = store.ui.view
        old = self.ctx
        if (
            old is None
            or view != old.view
            or {k: v.get("requestId") for k, v in pend.items()}
            != {k: v.get("requestId") for k, v in old.pending.items()}
        ):
            self.ctx = ToolCtx(self.sid, self.cwd, pend, 0, view)
            if old is not None and view != old.view:
                self._clear_pages()  # the view mode shapes every row: start the drawn pages over
        ctx = self.ctx
        sigs = [unit_sig(u, ctx) for u in units]
        tail = len(units)
        for i in range(len(units) - 1, -1, -1):
            u = units[i]
            if isinstance(u, ItemsUnit) and any(it.kind == "user" for it in u.items):
                tail = i
                break
        n_pages = int(math.ceil(len(units) / PAGE))
        live_from = min(tail // PAGE, n_pages - 1)
        while len(self._pages) > n_pages:
            pg = self._pages.pop()
            self.units_lay.removeWidget(pg)
            pg.deleteLater()
        prev_bottom = 6  # the first row sits 6px under the top padding
        first = True
        for p in range(n_pages):
            sl = units[p * PAGE : (p + 1) * PAGE]
            if p < len(self._pages):
                page = self._pages[p]
            else:
                page = PageBox(f"pg{p}")
                self._pages.append(page)
                self.units_lay.addWidget(page)
            est = sum(EST[u.kind] for u in sl) + 24 * len(sl)
            page.set_est(est)
            if p >= live_from or page.key in self._drawn or store.find_open:
                self._drawn.add(page.key)
                page.draw(sl, sigs[p * PAGE : (p + 1) * PAGE], ctx, prev_bottom if not first else 0)
                prev_bottom = page.last_margin()
            else:
                prev_bottom = 10
            first = False
        self._render_orphans(r, m, ctx)
        turn_start = 0.0
        for it in reversed(items):
            if it.kind == "user":
                turn_start = it.ts or 0
                break
        self.working.update_state(turn_start, live_turn_folds(items))
        # rows arrived (or a request): follow, or offer "Latest" — unless the page has in fact ended up
        # at the bottom (a fold above closed, the window grew), in which case there is nowhere to go
        rev = m.rev + len(self._orphans) * 1_000_003
        if rev != self._model_rev:
            self._model_rev = rev
            if self._at_bottom:
                self._follow()
            else:
                vb = self.verticalScrollBar()
                if vb.maximum() - vb.value() < 60:
                    self._at_bottom = True
                    self.jump.hide()
                else:
                    self._show_jump()
        if loaded and not self._placed and not self._placing:
            self._placing = True
            QTimer.singleShot(0, self._initial_place)
        self._near_timer.start()

    def _clear_pages(self) -> None:
        for pg in self._pages:
            self.units_lay.removeWidget(pg)
            pg.deleteLater()
        self._pages = []

    def _render_orphans(self, r, m, ctx: ToolCtx) -> None:
        pending = r.pending if r else []
        orphans = [p for p in pending if not p.get("toolUseId") or str(p.get("toolUseId")) not in m.tools]
        ids = [str(p.get("requestId")) for p in orphans]
        for k in list(self._orphans):
            if k not in ids:
                w = self._orphans.pop(k)
                self.orphans_lay.removeWidget(w)
                w.deleteLater()
        for p in orphans:
            k = str(p.get("requestId"))
            if k not in self._orphans:
                host = OrphanBox()
                host.layout().addWidget(make_permission_card(p, ctx))
                self._orphans[k] = host
                self.orphans_lay.addWidget(host)
        self.orphans_host.setVisible(bool(orphans))

    # ---------------------------------------------------------------- pages near the viewport
    def _y(self, w: QWidget) -> int:
        return w.mapTo(self.widget(), QPoint(0, 0)).y()

    def _draw_near(self) -> None:
        # Until the conversation has landed where it opens (the bottom, or the remembered spot) the
        # view sits at the top, and pages near there are not the ones the reader is about to see.
        if not self._pages or not self._placed or not self._alive():
            return
        vb = self.verticalScrollBar()
        v, vh = vb.value(), self.viewport().height()
        queue: List[Tuple[int, PageBox]] = []
        for pg in self._pages:
            if not pg.far:
                continue
            y, h = self._y(pg), pg.height()
            dist = v - (y + h) if y + h < v else y - (v + vh) if y > v + vh else 0
            if dist <= NEAR:
                queue.append((dist, pg))
        if not queue:
            return
        # Pages are drawn one per tick, nearest first, and not while the reader is flinging past them.
        last_v, last_at = self._last_scroll
        flinging = last_at and time.monotonic() - last_at < 0.08 and abs(v - last_v) > vh * 1.5
        if not flinging:
            queue.sort(key=lambda q: q[0])
            pg = queue[0][1]
            above = self._y(pg) + pg.height() <= v and not self._at_bottom
            old_h = pg.height()
            self._drawn.add(pg.key)
            self._render()
            if above:
                # keep what the reader is looking at still while the page above takes its real size
                QTimer.singleShot(0, lambda: self._compensate(pg, old_h))
        if len(queue) > 1 or flinging:
            self._near_timer.start()

    def _compensate(self, pg: PageBox, old_h: int) -> None:
        d = pg.height() - old_h
        if d and not self._at_bottom:
            vb = self.verticalScrollBar()
            self._set(vb.value() + d)

    # ---------------------------------------------------------------- scrolling
    def _set(self, v: int) -> None:
        vb = self.verticalScrollBar()
        v = max(0, min(vb.maximum(), int(v)))
        self._last_set = v
        vb.setValue(v)

    def _follow(self) -> None:
        if self._at_bottom and not self._follow_timer.isActive():
            self._step()

    def _step(self) -> None:
        """Follow new output smoothly instead of snapping to the bottom on every streamed token."""
        if not self._at_bottom:
            return
        vb = self.verticalScrollBar()
        target = vb.maximum()
        d = target - vb.value()
        if d <= 0:
            return
        snap = d < 2 or self._landing or store.reduced_motion
        nv = target if snap else vb.value() + max(1, int(d * 0.24))
        self._set(nv)
        if nv < target:
            self._follow_timer.start()

    def _on_range(self, _lo: int, _hi: int) -> None:
        if self._restore is not None:
            self._place_saved()
        elif self._at_bottom:
            self._follow()
        if self._landing:
            self._land_timer.start()
        self._near_timer.start()

    def _on_scroll(self, v: int) -> None:
        vb = self.verticalScrollBar()
        if vb.maximum() <= 0 or self._restore is not None:
            return  # nothing to scroll (or the view being torn down, or placed by us): nothing to follow or remember
        now = time.monotonic()
        own = v == self._last_set
        if not own:
            self._last_scroll = (v, now)
        if self._scrolled_cb is not None:
            self._scrolled_cb(v > 4)
        if self._at_bottom and own:
            return
        if vb.maximum() - v < 60:
            self._at_bottom = True
            self.jump.hide()
        elif not own and v < self._last_set - 2:
            # the reader scrolled up: stop following (our own sets never land here)
            self._at_bottom = False
        self._last_set = v
        r = store.rt.get(self.sid)
        if not (r and r.loaded) or not self._placed:
            return
        if self._at_bottom:
            scroll_memory[self.sid] = ScrollPos(0, 0, True)
        else:
            self._anchor_timer.start()
        self._near_timer.start()

    def _anchor(self) -> Tuple[int, int]:
        """The unit at the top of the view and how far it sits from the edge."""
        v = self.verticalScrollBar().value()
        for p, pg in enumerate(self._pages):
            y = self._y(pg)
            if y + pg.height() <= v:
                continue
            if pg.far or not pg.boxes:
                return p * PAGE, y - v
            for k, b in enumerate(pg.boxes):
                by = self._y(b)
                if by + b.height() > v:
                    return p * PAGE + k, by - v
            return p * PAGE, y - v
        return 0, 0

    def _remember(self) -> None:
        if self._at_bottom or not self._alive():
            return
        index, offset = self._anchor()
        scroll_memory[self.sid] = ScrollPos(index, offset, False)

    def _initial_place(self) -> None:
        self._placing = False
        r = store.rt.get(self.sid)
        if not (r and r.loaded):
            return
        self._placed = True
        saved = self._saved
        if saved and not saved.bottom and saved.index < len(self._units):
            # Coming back to a conversation you had scrolled up in: return to that spot, not the bottom.
            # Pages around it only have stand-in heights until drawn, so the first placing is
            # approximate; every change of the scroll range re-places it until the layout holds still.
            p = saved.index // PAGE
            if p < len(self._pages) and self._pages[p].far:
                self._drawn.add(self._pages[p].key)
                self._render()
            self._at_bottom = False
            self._restore = saved
            self._restore_timer.start()
            self._place_saved()
            self._show_jump()
            return
        # Land at the bottom now and again as pages near the end take their real heights.
        self._at_bottom = True
        self._landing = True
        self._land_timer.start()
        self._set(self.verticalScrollBar().maximum())
        self._follow()
        self._near_timer.start()

    def _place_saved(self) -> None:
        saved = self._restore
        if saved is None or not self._alive():
            return
        p = saved.index // PAGE
        if p >= len(self._pages):
            return
        pg = self._pages[p]
        k = saved.index - p * PAGE
        w = pg.boxes[k] if (not pg.far and k < len(pg.boxes)) else pg
        self._set(self._y(w) - saved.offset)
        self._restore_timer.start()

    def _end_restore(self) -> None:
        self._restore = None
        self._last_set = self.verticalScrollBar().value()
        self._anchor_timer.start()

    def _landed(self) -> None:
        self._landing = False

    def _show_jump(self) -> None:
        self._place_jump()
        self.jump.show()
        self.jump.raise_()

    def _place_jump(self) -> None:
        vp = self.viewport()
        self.jump.adjustSize()
        self.jump.move((vp.width() - self.jump.width()) // 2, vp.height() - 18 - self.jump.height())

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.viewport() and e.type() == QEvent.Type.Resize:
            self._place_jump()
            # the view shrinking (a panel opening, the composer growing) is kept in step at once
            if self._at_bottom:
                self._set(self.verticalScrollBar().maximum())
            self._near_timer.start()
        return super().eventFilter(obj, e)

    def wheelEvent(self, e) -> None:  # noqa: N802
        self._last_scroll = (self.verticalScrollBar().value(), time.monotonic())
        super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_End or (
            e.key() == Qt.Key.Key_Down and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.scroll_to_bottom()
            return
        super().keyPressEvent(e)


class OrphanBox(QWidget):
    """A request that lost its tool row keeps a card of its own (``.tool.awaiting.orphan``)."""

    TOP, BOTTOM = 10, 14

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("orphan")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 0)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#orphan {{ background: {t.raised}; border: 1px solid {theme.blend(t.hair_2, t.raised).name()}; border-radius: 12px; }}"
        )


def _find_text(it: Item, cwd: str) -> str:
    k = it.kind
    if k in ("user", "text", "command-output", "thinking"):
        return it.text
    if k == "tool":
        m = tool_meta(it.name, it.input, cwd)
        return f"{m.verb} {m.target}".strip()
    if k == "notice":
        return it.text
    return ""


def render_items(items, ctx, rev: int = 0, running: bool = False):
    """A flat list of rows for items that live outside the main column (an Agent card's children)."""
    from .folds import Flat

    _ = (rev, running)
    return Flat(list(items), ctx, in_work=True)
