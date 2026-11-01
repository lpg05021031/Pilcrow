// Small hooks shared across components: a handler that keeps its identity, where a chosen item sits, and a clock.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';

/**
 * A function with one identity for the component's life that always runs the latest `fn`. An effect
 * that calls a handler lists it, and is not run again because the handler is a new closure each
 * render; the handler reads the props and state of the render that last committed, never stale ones.
 * Not for calling during render.
 */
export function useEvent<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const latest = useRef(fn);
  useLayoutEffect(() => {
    latest.current = fn;
  });
  return useCallback((...args: A) => latest.current(...args), []);
}

type Ink = { left: number; top: number; width: number; height: number };

/**
 * Where the chosen item (`selector`, inside `ref`) sits in its container, for an ink or a thumb that
 * glides to it (the segmented control, the panel tabs, the settings pages): measured when `key`
 * changes, and again whenever the container or the item changes size.
 */
export function useInk(ref: RefObject<HTMLElement | null>, selector: string, key: unknown): Ink | null {
  const [ink, setInk] = useState<Ink | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const el = ref.current?.querySelector<HTMLElement>(selector);
      const next = el ? { left: el.offsetLeft, top: el.offsetTop, width: el.offsetWidth, height: el.offsetHeight } : null;
      setInk((cur) => (cur && next && cur.left === next.left && cur.top === next.top && cur.width === next.width && cur.height === next.height ? cur : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (ref.current) ro.observe(ref.current);
    const on = ref.current?.querySelector(selector);
    if (on) ro.observe(on);
    return () => ro.disconnect();
  }, [ref, selector, key]);
  return ink;
}

export function useNow(intervalMs = 1000, active = true) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, active]);
  return now;
}
