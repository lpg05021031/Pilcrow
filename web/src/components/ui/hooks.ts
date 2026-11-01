// Small hooks shared across components: where a chosen item sits, and a clock.
import { useEffect, useLayoutEffect, useState, type RefObject } from 'react';

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
