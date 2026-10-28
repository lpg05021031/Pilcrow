import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { LoaderCircle, Copy, Check } from 'lucide-react';
import type { SessionStatus } from '../../lib/types';
import { tokens } from '../../lib/format';
import { useExitGhost, useCountUp } from '../../lib/motion';

/** The Pilcrow mark: an alignment pilcrow with a safelight core. */
/** The pilcrow mark. `tight` crops to the ink so it sits in a row like any other icon. */
export function Mark({ size = 20, className = '', tight = false }: { size?: number; className?: string; tight?: boolean }) {
  return (
    <svg className={`mark ${className}`} width={tight ? Math.round((size * 14) / 16) : size} height={size} viewBox={tight ? '4.5 4 14 16' : '0 0 24 24'} aria-hidden="true">
      <circle cx="10.6" cy="9.2" r="4.1" fill="var(--accent)" />
      <rect x="10.6" y="5.1" width="6.6" height="1.9" rx="0.5" fill="currentColor" />
      <rect x="12.4" y="5.1" width="1.9" height="13.8" rx="0.5" fill="currentColor" />
      <rect x="15.3" y="5.1" width="1.9" height="13.8" rx="0.5" fill="currentColor" />
    </svg>
  );
}

/** The app icon itself, as a tile: the mark a person sees in the taskbar, so the empty states feel like the app and not a diagram. */
export function AppIcon({ size = 56, className = '' }: { size?: number; className?: string }) {
  return (
    <svg className={`app-icon ${className}`} width={size} height={size} viewBox="0 0 256 256" aria-hidden="true">
      <rect width="256" height="256" rx="60" fill="#1f1f1f" />
      <circle cx="105" cy="98" r="43.5" fill="#d97757" className="app-icon-bowl" />
      <rect x="105" y="55" width="69" height="20.5" rx="4.5" fill="#fafafa" />
      <rect x="123.5" y="55" width="20.5" height="146" rx="4.5" fill="#fafafa" />
      <rect x="153" y="55" width="20.5" height="146" rx="4.5" fill="#fafafa" />
    </svg>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" />;
}

/** The pilcrow at work: the bowl breathes in Claude's orange while the stems hold still. */
export function WorkingMark({ size = 16 }: { size?: number }) {
  return (
    <svg className="working-mark" width={size} height={size} viewBox="4.5 4 14 16" aria-hidden="true">
      <circle className="wm-bowl" cx="10.6" cy="9.2" r="4.1" />
      <g className="wm-stems" fill="currentColor">
        <rect x="10.6" y="5.1" width="6.6" height="1.9" rx="0.5" />
        <rect x="12.4" y="5.1" width="1.9" height="13.8" rx="0.5" />
        <rect x="15.3" y="5.1" width="1.9" height="13.8" rx="0.5" />
      </g>
    </svg>
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} className={`switch${on ? ' on' : ''}`} onClick={() => onChange(!on)} />;
}

/**
 * Segmented control. One raised thumb slides between the segments on a spring, the way the control
 * does on iOS; the labels never move. The thumb is measured from the chosen segment, so segments can
 * be any width.
 */
export function Seg<T extends string>({ value, options, onChange, size, className = '', label }: { value: T; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[]; onChange: (v: T, e: React.MouseEvent<HTMLButtonElement>) => void; size?: 'sm'; className?: string; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null);
  const [settled, setSettled] = useState(false);
  useLayoutEffect(() => {
    const measure = () => {
      const el = ref.current?.querySelector<HTMLElement>('button.on');
      setThumb(el ? { x: el.offsetLeft, w: el.offsetWidth } : null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  }, [value, options.length]);
  // The thumb's first position is not a move.
  useEffect(() => {
    const t = requestAnimationFrame(() => setSettled(true));
    return () => cancelAnimationFrame(t);
  }, []);
  return (
    <div ref={ref} className={`seg${size ? ` ${size}` : ''}${settled ? ' settled' : ''} ${className}`} role="radiogroup" aria-label={label}>
      {thumb && <span className="seg-thumb" aria-hidden="true" style={{ transform: `translateX(${thumb.x}px)`, width: thumb.w }} />}
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} className={o.value === value ? 'on' : ''} disabled={o.disabled} title={o.title} onClick={(e) => onChange(o.value, e)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A number that rolls to each new value. `format` turns the in-between values into text. */
export function Num({ value, format }: { value: number; format: (n: number) => string }) {
  const v = useCountUp(value);
  return <>{format(v)}</>;
}

/**
 * A ring gauge: the arc fills on a spring from empty when it first appears, and glides to each new
 * value after that. The number sits in the middle.
 */
export function RingGauge({ pct, size = 88, width = 7, tone, children }: { pct: number; size?: number; width?: number; tone: string; children?: ReactNode }) {
  const r = (size - width) / 2;
  const c = 2 * Math.PI * r;
  const [live, setLive] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setLive(true));
    return () => cancelAnimationFrame(t);
  }, []);
  const p = live ? Math.max(0, Math.min(1, pct)) : 0;
  return (
    <span className="gauge-wrap" style={{ width: size, height: size }}>
      <svg className="gauge" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle className="gauge-track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={width} />
        <circle className="gauge-arc" cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={width} strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - p)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <span className="gauge-num">{children}</span>
    </span>
  );
}

/** A block that leaves gracefully: when it unmounts, a still copy plays the `.leaving` exit its stylesheet defines. */
export function Leave({ className = '', duration = 220, children, ...rest }: React.HTMLAttributes<HTMLDivElement> & { duration?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useExitGhost(ref, { duration });
  return (
    <div ref={ref} className={className} {...rest}>
      {children}
    </div>
  );
}

/** Status mark for lists: only states that deserve attention get one; idle shows nothing. */
export function StatusGlyph({ status, pending, unread }: { status: SessionStatus | undefined; pending?: number; unread?: boolean }) {
  if (pending || status === 'requires_action') return <span className="glyph needs" title="Needs your input" />;
  if (status === 'running' || status === 'starting') return <span className="glyph running" title="Working" />;
  if (status === 'error') return <span className="glyph error" title="Stopped with an error" />;
  if (unread) return <span className="glyph unread" title="New activity" />;
  return null;
}

export function ContextRing({ used, total, size = 18 }: { used: number; total: number; size?: number }) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  const r = (size - 3) / 2;
  const c = 2 * Math.PI * r;
  const tone = pct > 0.85 ? 'var(--err)' : pct > 0.65 ? 'var(--accent)' : 'var(--text-3)';
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="ctx-ring" aria-label={`Context ${Math.round(pct * 100)}% used`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--hair-2)" strokeWidth="2" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth="2" strokeDasharray={`${c * pct} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  );
}

export function contextLabel(used: number, total: number) {
  if (!total) return used ? `${tokens(used)} tokens in context` : 'No context used yet';
  return `${tokens(used)} of ${tokens(total)} tokens (${Math.round((used / total) * 100)}%)`;
}

/** Vertical drag handle that reports the pointer x delta. */
export function Resizer({ onResize, side }: { onResize: (dx: number) => void; side: 'left' | 'right' }) {
  const [drag, setDrag] = useState(false);
  const last = useRef(0);
  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      onResize(e.clientX - last.current);
      last.current = e.clientX;
    };
    const up = () => setDrag(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    document.body.classList.add('resizing');
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing');
    };
  }, [drag, onResize]);
  return (
    <div
      className={`resizer ${side}${drag ? ' active' : ''}`}
      onPointerDown={(e) => {
        last.current = e.clientX;
        setDrag(true);
        e.preventDefault();
      }}
    />
  );
}

export function Collapsible({ open, children }: { open: boolean; children: ReactNode }) {
  return open ? <>{children}</> : null;
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

/** Focus an element unless the user is busy elsewhere (a dialog, the palette, another text field). */
export function focusIfFree(el: HTMLElement | null | undefined) {
  if (!el) return;
  const a = document.activeElement as HTMLElement | null;
  const busy = a && a !== document.body && a !== el && (a.closest('[role="dialog"], .palette, .dialog') || a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable);
  if (busy || document.querySelector('.palette, .dialog')) return;
  el.focus();
}

/** Copy to the clipboard. Falls back to the legacy path when the async API is unavailable or refused. */
export async function copyText(text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* fall through */
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
  const prev = document.activeElement as HTMLElement | null;
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  ta.remove();
  prev?.focus?.();
  if (!ok) throw new Error('Copy was blocked');
}

/** Copy with a check-mark confirmation in place, instead of a toast. */
export function CopyButton({ text, title = 'Copy', size = 13 }: { text: string; title?: string; size?: number }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className={`icon-btn sm copy-btn${done ? ' done' : ''}`}
      title={done ? 'Copied' : title}
      onClick={() =>
        copyText(text).then(
          () => {
            setDone(true);
            setTimeout(() => setDone(false), 1300);
          },
          () => {},
        )
      }
    >
      {done ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}

/** A keyboard shortcut drawn as separate keycaps ("Ctrl+K" → Ctrl K), the same way everywhere. */
export function Keys({ combo, className = '' }: { combo: string; className?: string }) {
  const parts = combo === '+' ? ['+'] : combo.split(/\+(?!$)/);
  return (
    <span className={`keys ${className}`}>
      {parts.map((k, i) => (
        <kbd key={i}>{k}</kbd>
      ))}
    </span>
  );
}
