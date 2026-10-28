import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { reducedMotion, spring } from '../../lib/motion';

/**
 * A side slot that opens and closes by width. The sidebar lives in one, the side panel in another.
 * The content keeps its full width the whole time and the slot clips it, so nothing inside reflows
 * while the slot glides; the content eases in from the edge it belongs to, settling on a spring with
 * no overshoot, and the slot only unmounts once it has closed. A slot that is open when the app
 * starts simply appears.
 */
export function Slot({ open, side, className = '', children }: { open: boolean; side: 'left' | 'right'; className?: string; children: ReactNode }) {
  const [mounted, setMounted] = useState(open);
  const ref = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  const anim = useRef<Animation | null>(null);
  if (open && !mounted) setMounted(true);

  useLayoutEffect(() => {
    const el = ref.current;
    const isFirst = first.current;
    first.current = false;
    if (isFirst && open) return;
    if (!el) return;
    if (reducedMotion()) {
      if (!open) setMounted(false);
      return;
    }
    const inner = el.firstElementChild as HTMLElement | null;
    const running = anim.current;
    // Mid-glide reversal starts from where the slot is now, not from the end it was heading for.
    const from = running ? el.getBoundingClientRect().width : open ? 0 : el.offsetWidth;
    running?.cancel();
    el.style.width = '';
    el.style.minWidth = '0';
    el.style.overflow = 'hidden';
    el.style.flex = 'none';
    const full = inner ? inner.offsetWidth : el.offsetWidth;
    // The child may carry its own inline width (the sidebar's resizable width is one); it comes back as it was.
    const innerWas = inner ? { flex: inner.style.flex, width: inner.style.width } : null;
    if (inner) {
      inner.style.flex = 'none';
      inner.style.width = `${full}px`;
    }
    const to = open ? full : 0;
    const { soft, softT } = spring();
    const dur = open ? softT : 240;
    const easing = open ? soft : 'cubic-bezier(0.4, 0, 0.6, 1)';
    const dx = side === 'left' ? -1 : 1;
    const shift = Math.min(36, full * 0.12);
    const a = el.animate([{ width: `${from}px` }, { width: `${to}px` }], { duration: dur, easing, fill: 'forwards' });
    anim.current = a;
    inner?.animate(
      open
        ? [
            { transform: `translateX(${dx * shift * (1 - from / Math.max(1, full))}px)`, opacity: 0.4 + 0.6 * (from / Math.max(1, full)) },
            { transform: 'none', opacity: 1 },
          ]
        : [
            { transform: 'none', opacity: 1 },
            { transform: `translateX(${dx * shift}px)`, opacity: 0.4 },
          ],
      { duration: dur, easing, fill: 'forwards' },
    );
    a.onfinish = () => {
      anim.current = null;
      if (open) {
        a.cancel();
        inner?.getAnimations().forEach((x) => x.cancel());
        el.style.overflow = '';
        el.style.minWidth = '';
        el.style.flex = '';
        if (inner && innerWas) {
          inner.style.flex = innerWas.flex;
          inner.style.width = innerWas.width;
        }
      } else setMounted(false);
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!mounted) return null;
  return (
    <div ref={ref} className={`slot ${side} ${className}`}>
      {children}
    </div>
  );
}
