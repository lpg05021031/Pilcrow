import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { EASE_EXIT, reducedMotion, spring } from '../../lib/motion';

/**
 * Height-animated disclosure. Opening grows from nothing to the content's height; closing shrinks
 * it back before the content unmounts, so collapsing feels as deliberate as expanding. Content that
 * is already open when it first mounts appears without animation.
 */
export function Collapse({ open, className = '', children, onDone }: { open: boolean; className?: string; children: ReactNode | (() => ReactNode); onDone?: () => void }) {
  const [mounted, setMounted] = useState(open);
  const ref = useRef<HTMLDivElement>(null);
  const firstRun = useRef(true);
  const anim = useRef<Animation | null>(null);

  if (open && !mounted) setMounted(true);

  useLayoutEffect(() => {
    const el = ref.current;
    const first = firstRun.current;
    firstRun.current = false;
    if (first && open) return;
    if (!el) return;
    if (reducedMotion()) {
      if (!open) setMounted(false);
      return;
    }
    const from = anim.current ? el.getBoundingClientRect().height : open ? 0 : el.offsetHeight;
    anim.current?.cancel();
    el.style.overflow = 'hidden';
    if (open) {
      el.inert = false;
      const to = el.scrollHeight;
      // Opening settles on the soft spring: quick to arrive, no bounce, a long gentle landing.
      const { soft } = spring();
      const a = el.animate(
        [
          { height: `${from}px`, opacity: from ? 1 : 0 },
          { height: `${to}px`, opacity: 1 },
        ],
        { duration: Math.min(420, 240 + to / 5), easing: soft },
      );
      anim.current = a;
      a.onfinish = () => {
        el.style.overflow = '';
        anim.current = null;
        onDone?.();
      };
    } else {
      el.inert = true; // what is folding away can no longer be clicked
      const a = el.animate(
        [
          { height: `${from}px`, opacity: 1 },
          { height: '0px', opacity: 0 },
        ],
        { duration: Math.min(260, 160 + from / 10), easing: EASE_EXIT, fill: 'forwards' },
      );
      anim.current = a;
      a.onfinish = () => {
        anim.current = null;
        setMounted(false);
        onDone?.();
      };
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!mounted) return null;
  return (
    <div ref={ref} className={`collapse ${className}`}>
      {typeof children === 'function' ? children() : children}
    </div>
  );
}
