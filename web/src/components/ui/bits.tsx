// Small controls and indicators shared across the app.
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { LoaderCircle, PanelLeftOpen } from 'lucide-react';
import { setUi, useStore } from '../../lib/store';
import { isWorking, type SessionStatus } from '../../lib/types';
import { tokens } from '../../lib/format';
import { useExitGhost, useCountUp } from '../../lib/motion';
import { useInk } from './hooks';

/** A page header's way back to the sidebar while it is hidden (Ctrl+B does the same). */
export function ShowSidebarButton() {
  const sidebar = useStore((s) => s.ui.sidebar);
  return sidebar ? null : (
    <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
      <PanelLeftOpen size={16} />
    </button>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" />;
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
  const thumb = useInk(ref, 'button.on', `${value}:${options.length}`);
  const [settled, setSettled] = useState(false);
  // The thumb's first position is not a move.
  useEffect(() => {
    const t = requestAnimationFrame(() => setSettled(true));
    return () => cancelAnimationFrame(t);
  }, []);
  return (
    <div ref={ref} className={`seg${size ? ` ${size}` : ''}${settled ? ' settled' : ''} ${className}`} role="radiogroup" aria-label={label}>
      {thumb && <span className="seg-thumb" aria-hidden="true" style={{ transform: `translateX(${thumb.left}px)`, width: thumb.width }} />}
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} className={o.value === value ? 'on' : ''} disabled={o.disabled} title={o.title} onClick={(e) => onChange(o.value, e)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A labelled form row: the label names the control that takes the `id` it is handed (a <Select> also
 * keeps its current value in its name). Rows with several controls name each one with aria-label.
 */
export function Field({ label, children }: { label: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id} id={`${id}-label`}>
        {label}
      </label>
      {children(id)}
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

/**
 * Status mark for lists: only states that deserve attention get one; idle shows nothing. It is the
 * row's only word on its state, so it is an image with a name a screen reader says, besides the tip.
 */
export function StatusGlyph({ status, pending, unread }: { status: SessionStatus | undefined; pending?: number; unread?: boolean }) {
  const [kind, name] = pending || status === 'requires_action' ? ['needs', 'Needs your input'] : isWorking(status) ? ['running', 'Working'] : status === 'error' ? ['error', 'Stopped with an error'] : unread ? ['unread', 'New activity'] : [];
  return kind ? <span className={`glyph ${kind}`} role="img" aria-label={name} title={name} /> : null;
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
