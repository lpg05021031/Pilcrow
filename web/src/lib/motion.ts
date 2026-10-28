// Shared motion helpers. Durations and curves mirror the tokens in styles/tokens.css.
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';

export const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';
export const EASE_EXIT = 'cubic-bezier(0.4, 0, 1, 1)';

/** The spring curves from tokens.css, for the Web Animations API (which takes the same linear() syntax). */
function token(name: string, fallback: string) {
  if (typeof getComputedStyle !== 'function') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
let springs: { soft: string; softT: number; snappy: string; snappyT: number; bouncy: string; bouncyT: number } | null = null;
export function spring() {
  if (!springs) {
    springs = {
      soft: token('--spring-soft', EASE),
      softT: parseInt(token('--spring-soft-t', '560'), 10) || 560,
      snappy: token('--spring-snappy', EASE),
      snappyT: parseInt(token('--spring-snappy-t', '600'), 10) || 600,
      bouncy: token('--spring-bouncy', EASE),
      bouncyT: parseInt(token('--spring-bouncy-t', '760'), 10) || 760,
    };
  }
  return springs;
}

export function reducedMotion() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Leaving gracefully. React removes an element the instant its state says so; a menu, a sheet or a
 * toast then vanishes between two frames, which is the single thing that makes an interface feel
 * abrupt. This hook keeps a still copy of the element on screen for a moment after it unmounts and
 * plays its exit on that: the copy is pinned exactly where the element was, cannot be clicked, and
 * is removed when its animation ends. Nothing in the component tree has to know.
 *
 * The exit itself is CSS: the copy gets `leaving` added to its class list, and the element's own
 * stylesheet says what leaving looks like (`.popover.leaving { animation: … }`).
 */
export function useExitGhost(ref: RefObject<HTMLElement | null>, opts: { duration?: number; className?: string; pin?: boolean } = {}) {
  const o = useRef(opts);
  o.current = opts;
  useLayoutEffect(() => {
    // The element is read at unmount on purpose: that is the last frame to copy.
    return () => {
      const el = ref.current; // eslint-disable-line react-hooks/exhaustive-deps
      if (!el || !el.isConnected || reducedMotion() || document.hidden) return;
      ghost(el, o.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Make the still copy of an element and play its exit on it. See useExitGhost. */
export function ghost(el: HTMLElement, { duration = 220, className = 'leaving', pin = true }: { duration?: number; className?: string; pin?: boolean } = {}) {
  if (el.dataset.ghost !== undefined) return; // already a copy
  const rect = el.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  // A copy of a very long page would cost more than the moment it buys; such a page just goes.
  if (el.querySelectorAll('*').length > 3000) return;
  const copy = el.cloneNode(true) as HTMLElement;
  copy.dataset.ghost = '';
  copy.setAttribute('aria-hidden', 'true');
  copy.inert = true;
  // What was typed and where lists were scrolled do not survive cloning; carry them over so the
  // copy is a faithful last frame.
  const srcFields = el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
  const dstFields = copy.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
  srcFields.forEach((f, i) => dstFields[i] && (dstFields[i].value = f.value));
  const srcAll = el.querySelectorAll<HTMLElement>('*');
  const dstAll = copy.querySelectorAll<HTMLElement>('*');
  srcAll.forEach((s, i) => {
    if (s.scrollTop || s.scrollLeft) {
      const d = dstAll[i];
      if (d) {
        d.scrollTop = s.scrollTop;
        d.scrollLeft = s.scrollLeft;
      }
    }
  });
  copy.querySelectorAll('canvas, iframe, video').forEach((n) => n.remove());
  if (pin) {
    copy.style.position = 'fixed';
    copy.style.left = `${rect.left}px`;
    copy.style.top = `${rect.top}px`;
    copy.style.width = `${rect.width}px`;
    copy.style.height = `${rect.height}px`;
    copy.style.margin = '0';
    copy.style.transform = 'none';
    copy.style.zIndex = String(Math.max(2000, zIndexOf(el)));
  }
  copy.style.pointerEvents = 'none';
  copy.classList.add(className);
  document.body.append(copy);
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    copy.remove();
  };
  copy.addEventListener('animationend', (e) => e.target === copy && finish());
  window.setTimeout(finish, duration + 80);
}

/**
 * A number that rolls to its new value instead of jumping: a cost that just went up, a context
 * window filling. Eases out over `ms`; the first value shows at once.
 */
export function useCountUp(value: number, ms = 640): number {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  const target = useRef(value);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (value === target.current) return;
    target.current = value;
    if (raf.current != null) cancelAnimationFrame(raf.current);
    if (reducedMotion() || !Number.isFinite(value)) {
      shownRef.current = value;
      setShown(value);
      return;
    }
    const from = shownRef.current;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      const e = 1 - Math.pow(1 - p, 3);
      const v = p >= 1 ? target.current : from + (target.current - from) * e;
      shownRef.current = v;
      setShown(v);
      raf.current = p < 1 ? requestAnimationFrame(tick) : null;
    };
    raf.current = requestAnimationFrame(tick);
  }, [value, ms]);
  useEffect(
    () => () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    },
    [],
  );
  return shown;
}

/**
 * The send: where the message was when it left the composer, so the bubble that arrives in the
 * conversation can fly up from there (Transcript picks it up). Stale launches are ignored.
 */
let launchRect: DOMRect | null = null;
let launchAt = 0;
export function markLaunch(rect: DOMRect) {
  launchRect = rect;
  launchAt = performance.now();
}
export function takeLaunch(): DOMRect | null {
  const r = launchRect;
  launchRect = null;
  return r && performance.now() - launchAt < 2500 ? r : null;
}

/**
 * Rows that change place glide there (FLIP). Each row carries `data-flip="<key>"`; after every render
 * in which `deps` changed, a row whose position moved is animated from where it was to where it is.
 */
export function useFlipRows(ref: RefObject<HTMLElement | null>, selector: string, deps: unknown) {
  const last = useRef(new Map<string, DOMRect>());
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const next = new Map<string, DOMRect>();
    root.querySelectorAll<HTMLElement>(selector).forEach((el) => {
      const k = el.dataset.flip;
      if (k) next.set(k, el.getBoundingClientRect());
    });
    if (!reducedMotion()) {
      for (const [k, r] of next) {
        const b = last.current.get(k);
        if (!b || (Math.abs(b.top - r.top) < 1 && Math.abs(b.left - r.left) < 1)) continue;
        const el = root.querySelector<HTMLElement>(`[data-flip="${CSS.escape(k)}"]`);
        el?.animate([{ transform: `translate(${b.left - r.left}px, ${b.top - r.top}px)` }, { transform: 'none' }], { duration: 320, easing: EASE });
      }
    }
    last.current = next;
  }, [deps]); // eslint-disable-line react-hooks/exhaustive-deps
}

function zIndexOf(el: HTMLElement) {
  for (let n: HTMLElement | null = el; n; n = n.parentElement) {
    const z = parseInt(getComputedStyle(n).zIndex, 10);
    if (!Number.isNaN(z)) return z;
  }
  return 0;
}

const LATIN = /[A-Za-z0-9_'’-]/;

/**
 * Reveal streamed text at a steady pace. Model output arrives in uneven bursts (more so through a
 * gateway that buffers); showing it as received makes text lurch. Instead the visible
 * length eases toward the received length every frame: quick when a big burst lands, never slower
 * than about 60 characters a second, and it finishes the backlog promptly once the reply is done.
 * Text that is already there when the component mounts (history, switching sessions) shows at once.
 */
export function useSmoothText(text: string, live: boolean): string {
  // A reply that has only just begun eases in from its first character; anything longer is shown as is.
  const initial = live && text.length < 80 ? 0 : text.length;
  const [shown, setShown] = useState(initial);
  const shownRef = useRef(initial);
  const textRef = useRef(text);
  const liveRef = useRef(live);
  const raf = useRef<number | null>(null);
  textRef.current = text;
  liveRef.current = live;

  useEffect(() => {
    // A rewrite (not an append) or a shrink: jump straight to the new text.
    if (text.length < shownRef.current) {
      shownRef.current = text.length;
      setShown(text.length);
      return;
    }
    if (shownRef.current >= text.length || raf.current != null) return;
    if (reducedMotion()) {
      shownRef.current = text.length;
      setShown(text.length);
      return;
    }
    let frame = 0;
    const tick = () => {
      const full = textRef.current.length;
      let cur = shownRef.current;
      if (cur >= full) {
        raf.current = null;
        return;
      }
      // Each reveal re-renders the block being written; for a long reply that is real work, so a
      // long one advances every other frame (in bigger steps) and reads the same.
      const long = full > 6000;
      if (long && frame++ % 2) {
        raf.current = requestAnimationFrame(tick);
        return;
      }
      const backlog = full - cur;
      const step = Math.max(1, Math.ceil((backlog * (long ? 2 : 1)) / (liveRef.current ? 9 : 3)));
      let next = Math.min(full, cur + step);
      // Do not stop in the middle of a Latin word; CJK text can break after any character.
      const s = textRef.current;
      let guard = 0;
      while (next < full && guard++ < 16 && LATIN.test(s[next]) && LATIN.test(s[next - 1])) next++;
      cur = next;
      shownRef.current = cur;
      setShown(cur);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [text, live]);

  useEffect(
    () => () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    },
    [],
  );

  return shown >= text.length ? text : text.slice(0, shown);
}

/** A value that updates at most every `ms`, so a fast-changing ticker reads as a glide, not a flicker. */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  const timer = useRef<number | null>(null);
  const latest = useRef(value);
  latest.current = value;
  useEffect(() => {
    const due = last.current + ms - Date.now();
    if (due <= 0) {
      last.current = Date.now();
      setShown(value);
      return;
    }
    if (timer.current == null) {
      timer.current = window.setTimeout(() => {
        timer.current = null;
        last.current = Date.now();
        setShown(latest.current);
      }, due);
    }
  }, [value, ms]);
  return shown;
}
