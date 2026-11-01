import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { spring, EASE, takeLaunch } from '../lib/motion';
import type { TranscriptModel } from '../lib/transcript';
import { useStore, memo } from '../lib/store';

// Where each conversation was scrolled to, so switching away and back keeps your place: the box at
// the top of the view and how far it was from the edge (a scroll offset would drift, since boxes far
// off screen only have estimated heights until they have been seen). Kept in the session's memo, so a
// conversation evicted from memory (store.ts) or deleted takes its place with it.

/** Sent to the conversation's scroller by revealItem (Transcript): the reader is jumping into the history. */
export const REVEAL_EVENT = 'pilcrow:reveal';

/**
 * The conversation's scrolling: it lands at the end (or back where the reader was) when it opens,
 * follows new output smoothly while the reader is at the live end, stops following the moment they
 * scroll up themselves, and offers the way back down ("Latest"). New rows ease in as they arrive.
 * `rev` and `extra` (rows outside the list, such as orphan permission cards) mark new content.
 */
export function useFollow({ sessionId, model, loaded, rev, extra, onScrolled }: { sessionId: string; model: TranscriptModel | undefined; loaded: boolean; rev: number; extra: number; onScrolled?: (scrolled: boolean) => void }) {
  const scroller = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const lastSet = useRef(0);
  const raf = useRef<number | null>(null);
  const anchorRaf = useRef<number | null>(null);
  const landing = useRef(false);
  // When the reader last moved the page themselves (wheel, keys, the scrollbar, a finger). Never,
  // to begin with: at 0 it would read as "just now" for the first half second of the page's life,
  // and a far page taking its real height then (the position drops) counted as the reader leaving
  // the live end, so a long conversation opened at start-up landed short of its end.
  const userScrollAt = useRef(-Infinity);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const mark = () => (userScrollAt.current = performance.now());
    const onWheel = (e: WheelEvent) => e.deltaY < 0 && mark();
    const onKey = (e: KeyboardEvent) => ['ArrowUp', 'PageUp', 'Home'].includes(e.key) && mark();
    const onDown = (e: PointerEvent) => e.target === el && mark(); // the scrollbar itself
    // revealItem: a jump into the history leaves the live end at once, before the page it draws grows.
    const onReveal = () => {
      mark();
      atBottom.current = false;
      if (raf.current != null) cancelAnimationFrame(raf.current);
      raf.current = null;
    };
    el.addEventListener(REVEAL_EVENT, onReveal);
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('keydown', onKey);
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('touchmove', mark, { passive: true });
    return () => {
      el.removeEventListener(REVEAL_EVENT, onReveal);
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('keydown', onKey);
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('touchmove', mark);
    };
  }, [sessionId]);
  const [showJump, setShowJump] = useState(false);
  // Items already on screen when a conversation opens must not all animate in at once.
  const [settling, setSettling] = useState(true);
  // Up in the history (not following the live end): the browser then keeps the rows in view still
  // while rows above take their real size (transcript.css). State, not a class set by hand, so a
  // re-render cannot wipe it.
  const [reading, setReading] = useState(false);
  const readingRef = useRef(false);
  const markReading = (v: boolean) => {
    if (readingRef.current === v) return;
    readingRef.current = v;
    setReading(v);
  };

  // Follow new output smoothly instead of snapping to the bottom on every streamed token.
  const follow = () => {
    if (raf.current != null) return;
    const step = () => {
      const el = scroller.current;
      if (!el || !atBottom.current) return void (raf.current = null);
      const target = el.scrollHeight - el.clientHeight;
      const d = target - el.scrollTop;
      if (d <= 0.5) return void (raf.current = null);
      // While a conversation is still landing after opening, go straight there: easing from a
      // stand-in height to the real one would read as the page scrolling itself.
      el.scrollTop = d < 1.5 || landing.current ? target : el.scrollTop + Math.max(1, d * 0.24);
      lastSet.current = el.scrollTop;
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
  };

  const settled = useRef(false);
  useLayoutEffect(() => {
    atBottom.current = true;
    settled.current = false;
    setSettling(true);
    const el = scroller.current;
    const saved = memo(sessionId).scroll;
    const anchor = saved && !saved.bottom && loaded ? (inner.current?.querySelector('.units')?.children[saved.index] as HTMLElement | undefined) : undefined;
    if (el && saved && anchor) {
      // Coming back to a conversation you had scrolled up in: return to that spot, not the bottom.
      // Boxes that have not been laid out yet only have stand-in heights, so the first placing is
      // approximate; once the frame has painted the boxes around the spot have their real heights,
      // and the same measure lands exactly. Two more passes cover the boxes that fill in after that.
      const place = () => {
        if (!anchor.isConnected) return;
        el.scrollTop = anchor.offsetTop - saved.offset;
        lastSet.current = el.scrollTop;
      };
      place();
      atBottom.current = false;
      markReading(true);
      setShowJump(true);
      let passes = 0;
      const again = () => {
        if (atBottom.current || passes++ >= 3) return;
        place();
        requestAnimationFrame(again);
      };
      requestAnimationFrame(again);
    } else if (el) {
      // Pages far from the viewport take their stand-in heights only once the browser has decided
      // which are off screen, which happens after this first layout; so land at the bottom now and
      // again over the next frames, until the height stops changing.
      const land = () => {
        el.scrollTop = el.scrollHeight;
        lastSet.current = el.scrollTop;
      };
      land();
      markReading(false);
      // The browser settles which pages are off screen in its own time (it can take a while on a
      // long conversation), so keep landing until the height has held still for a few frames.
      landing.current = true;
      let passes = 0;
      let still = 0;
      let lastH = el.scrollHeight;
      const again = () => {
        if (!atBottom.current || !el.isConnected || passes++ >= 120 || still >= 12) {
          landing.current = false;
          return;
        }
        const h = el.scrollHeight;
        if (h === lastH) still++;
        else {
          still = 0;
          lastH = h;
          land();
        }
        requestAnimationFrame(again);
      };
      requestAnimationFrame(again);
    }
    // History that is already there appears at once; a brand-new conversation animates from its first row.
    const fresh = (model?.items.length || 0) === 0 && loaded;
    const t = setTimeout(
      () => {
        settled.current = true;
        setSettling(false);
      },
      fresh ? 0 : 450,
    );
    return () => clearTimeout(t);
  }, [sessionId, loaded, model]);

  // New rows ease in where they land. Only rows added after the conversation has settled animate,
  // so opening a long session or switching between sessions never replays the whole history.
  useEffect(() => {
    const body = inner.current;
    if (!body || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const mo = new MutationObserver((records) => {
      if (!settled.current) return;
      for (const r of records) {
        const parent = r.target as HTMLElement;
        // A history box being drawn as the reader approaches is not news: only new rows in the live
        // boxes at the end, and new boxes themselves, ease in.
        if (parent !== body && !parent.matches?.('.units, .page.live, .page.live > .unit, .steps, .explore-body, .agent-children')) continue;
        r.addedNodes.forEach((added) => {
          let n = added;
          // A new unit (or page) arrives with its first row already inside; the row is what moves.
          if (n instanceof HTMLElement && n.classList.contains('page')) n = n.firstElementChild as Node;
          if (n instanceof HTMLElement && n.classList.contains('unit')) n = n.firstElementChild as Node;
          if (!(n instanceof HTMLElement) || n.classList.contains('working') || n.classList.contains('transcript-loading')) return;
          // Your message flies up out of the composer to its place in the conversation, settling on a
          // spring, the way a sent message does; Claude's rows simply rise.
          const user = n.classList.contains('msg-user');
          const { snappy, snappyT } = spring();
          const from = user ? takeLaunch() : null;
          const bubble = user ? n.querySelector<HTMLElement>('.msg-bubble') : null;
          if (from && bubble) {
            const to = bubble.getBoundingClientRect();
            const dx = Math.min(0, from.right - 16 - to.right);
            const dy = from.top - to.top;
            bubble.animate(
              [
                { opacity: 0.5, transform: `translate(${dx}px, ${dy}px) scale(0.94)`, transformOrigin: 'right bottom' },
                { opacity: 1, transform: 'none', transformOrigin: 'right bottom' },
              ],
              { duration: snappyT, easing: snappy },
            );
            return;
          }
          n.animate(
            [
              { opacity: 0, transform: `translateY(${user ? 12 : 5}px)${user ? ' scale(0.96)' : ''}`, transformOrigin: 'right bottom' },
              { opacity: 1, transform: 'none', transformOrigin: 'right bottom' },
            ],
            user ? { duration: snappyT, easing: snappy } : { duration: 240, easing: EASE },
          );
        });
      }
    });
    mo.observe(body, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [sessionId]);

  useLayoutEffect(() => {
    if (atBottom.current) return follow();
    const el = scroller.current;
    // The reader is up in the history: offer "Latest" — unless the page has in fact ended up at the
    // bottom (a fold above closed, the window grew), in which case there is nowhere to go.
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
      atBottom.current = true;
      lastSet.current = el.scrollTop;
      markReading(false);
      setShowJump(false);
    } else setShowJump(true);
  }, [rev, extra]);

  useEffect(() => {
    const body = inner.current;
    if (!body) return;
    // The content growing, or the view shrinking (the composer growing as you type, a panel opening,
    // the window resized): either leaves room under the last row, and both are followed.
    const ro = new ResizeObserver((entries) => {
      if (!atBottom.current) return;
      const el = scroller.current;
      // The view itself changing size (the composer growing as you type, a panel opening) is kept
      // in step at once, in the same frame: easing after it would make the conversation twitch with
      // every line you type. New content below is what the smooth follow is for.
      if (el && entries.some((e) => e.target === el)) {
        el.scrollTop = el.scrollHeight - el.clientHeight;
        lastSet.current = el.scrollTop;
        return;
      }
      follow();
    });
    ro.observe(body);
    if (scroller.current) ro.observe(scroller.current);
    return () => {
      ro.disconnect();
      if (raf.current != null) cancelAnimationFrame(raf.current);
      if (anchorRaf.current != null) cancelAnimationFrame(anchorRaf.current);
      raf.current = anchorRaf.current = null;
    };
  }, [sessionId]);

  const onScroll = () => {
    const el = scroller.current!;
    // Raised by the follow itself (the position is exactly the one it set): reading scrollHeight here
    // would force a layout for every streamed frame, and all it would say is what we already know —
    // we are at the bottom. A reader's scroll lands anywhere else.
    if (atBottom.current && el.scrollTop === lastSet.current) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom) {
      atBottom.current = true;
      setShowJump(false);
    } else if (el.scrollTop < lastSet.current - 2 && performance.now() - userScrollAt.current < 500) {
      // The reader scrolled up: stop following. Only their own input counts — the position can
      // also fall when a page above takes its real, smaller height, and that is not a reader leaving.
      atBottom.current = false;
    }
    lastSet.current = Math.max(lastSet.current, el.scrollTop);
    if (el.scrollTop < lastSet.current - 2) lastSet.current = el.scrollTop;
    onScrolled?.(el.scrollTop > 4);
    markReading(!atBottom.current);
    if (!loaded) return;
    if (atBottom.current) {
      // Following the live end: nothing to anchor to, and a layout read here (once per streamed
      // frame, since the follow itself scrolls) would be the costliest thing on the page.
      memo(sessionId).scroll = { index: 0, offset: 0, bottom: true };
      return;
    }
    if (anchorRaf.current != null) return;
    anchorRaf.current = requestAnimationFrame(() => {
      anchorRaf.current = null;
      const kids = inner.current?.querySelector('.units')?.children;
      let index = 0;
      if (kids && kids.length) {
        // The boxes are stacked in order, so the first one reaching below the top edge is found by
        // bisection: a scan of every box on every scroll event forced a layout read per box.
        let lo = 0;
        let hi = kids.length - 1;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          const k = kids[mid] as HTMLElement;
          if (k.offsetTop + k.offsetHeight > el.scrollTop) hi = mid;
          else lo = mid + 1;
        }
        index = lo;
      }
      const k = kids?.[index] as HTMLElement | undefined;
      memo(sessionId).scroll = { index, offset: k ? k.offsetTop - el.scrollTop : 0, bottom: atBottom.current };
    });
  };

  const jump = () => {
    atBottom.current = true;
    setShowJump(false);
    markReading(false);
    follow();
  };
  // Your own message goes to the end of the conversation, and so do you.
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.sent !== prev.sent && s.sent?.id === sessionId) jump();
      }),
    [sessionId], // eslint-disable-line react-hooks/exhaustive-deps -- jump reads refs only; one subscription per conversation
  );
  return { scroller, inner, onScroll, jump, showJump, settling, reading };
}
