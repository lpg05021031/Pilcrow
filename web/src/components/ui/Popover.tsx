import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, type LucideIcon } from 'lucide-react';
import { reducedMotion } from '../../lib/motion';

type Placement = 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end';

const EXIT_MS = 160;

export function Popover({ anchor, open, onClose, children, placement = 'bottom-start', className = '', width }: { anchor: HTMLElement | null; open: boolean; onClose: () => void; children: ReactNode; placement?: Placement; className?: string; width?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null);
  // Closing keeps the popover on screen for its exit, inert, before it goes.
  const [shown, setShown] = useState(open);
  const leaving = !open && shown;

  useEffect(() => {
    if (open) {
      setShown(true);
      return;
    }
    if (!shown) return;
    if (reducedMotion()) {
      setShown(false);
      return;
    }
    const t = setTimeout(() => setShown(false), EXIT_MS + 20);
    return () => clearTimeout(t);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (!open || !anchor || !ref.current) return;
    const a = anchor.getBoundingClientRect();
    // Layout size, not the on-screen box: the opening animation scales the popover down for a moment.
    const p = { width: ref.current.offsetWidth, height: ref.current.offsetHeight };
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = placement.endsWith('end') ? a.right - p.width : a.left;
    let top = placement.startsWith('top') ? a.top - p.height - 6 : a.bottom + 6;
    if (top + p.height > vh - 8) top = Math.max(8, a.top - p.height - 6);
    if (top < 8) top = Math.min(vh - p.height - 8, a.bottom + 6);
    left = Math.max(8, Math.min(left, vw - p.width - 8));
    setPos({ left, top, up: top < a.top });
  }, [open, anchor, placement, children]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node) || anchor?.contains(e.target as Node)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('mousedown', onDown, true);
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('mousedown', onDown, true);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [open, anchor, onClose]);

  useEffect(() => {
    if (!shown) setPos(null);
  }, [shown]);

  if (!open && !shown) return null;
  // The anchor's side decides where the popover grows from (and shrinks back to).
  const originX = placement.endsWith('end') ? 'right' : 'left';
  return createPortal(
    <div
      ref={ref}
      className={`popover ${className}${leaving ? ' leaving' : ''}`}
      data-placement={pos?.up ? 'top' : 'bottom'}
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, width, visibility: pos ? 'visible' : 'hidden', transformOrigin: `${originX} ${pos?.up ? 'bottom' : 'top'}` }}
      role="dialog"
    >
      {children}
    </div>,
    document.body,
  );
}

export interface MenuItem {
  label?: string;
  hint?: string;
  icon?: LucideIcon;
  checked?: boolean;
  danger?: boolean;
  disabled?: boolean;
  separator?: boolean;
  heading?: string;
  kbd?: string;
  /** Show the hint in the code font (model IDs, paths). */
  mono?: boolean;
  onSelect?: () => void;
}

export function Menu({ items, onClose }: { items: MenuItem[]; onClose: () => void }) {
  const selectable = items.map((it, i) => (!it.separator && !it.heading && !it.disabled ? i : -1)).filter((i) => i >= 0);
  const [active, setActive] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const idx = selectable.indexOf(active);
        const next = e.key === 'ArrowDown' ? selectable[(idx + 1) % selectable.length] : selectable[(idx - 1 + selectable.length) % selectable.length];
        setActive(next ?? -1);
      } else if (e.key === 'Enter' && active >= 0) {
        e.preventDefault();
        items[active].onSelect?.();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  // Long lists scroll inside the popover: keep the keyboard selection visible.
  useEffect(() => {
    if (active >= 0) (ref.current?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' });
  }, [active]);
  // Rows arrive one after another, a few milliseconds apart, so the menu reads as unfolding rather than switching on.
  let n = 0;
  return (
    <div className="menu" ref={ref} role="menu">
      {items.map((it, i) => {
        if (it.separator) return <div key={i} className="menu-sep" />;
        if (it.heading) return <div key={i} className="menu-heading">{it.heading}</div>;
        const Icon = it.icon;
        const order = Math.min(n++, 10);
        return (
          <button
            key={i}
            role="menuitem"
            className={`menu-item${it.danger ? ' danger' : ''}${active === i ? ' active' : ''}`}
            style={{ '--i': order } as React.CSSProperties}
            disabled={it.disabled}
            onMouseEnter={() => setActive(i)}
            onClick={() => {
              it.onSelect?.();
              onClose();
            }}
          >
            <span className="menu-icon">{it.checked ? <Check size={14} /> : Icon ? <Icon size={14} /> : null}</span>
            <span className="menu-text">
              <span className="menu-label">{it.label}</span>
              {it.hint && <span className={`menu-hint${it.mono ? ' mono' : ''}`}>{it.hint}</span>}
            </span>
            {it.kbd && <kbd>{it.kbd}</kbd>}
          </button>
        );
      })}
    </div>
  );
}

/** A button that opens a menu. */
export function MenuButton({ items, children, className = 'icon-btn', title, placement = 'bottom-end', width }: { items: MenuItem[] | (() => MenuItem[]); children: ReactNode; className?: string; title?: string; placement?: Placement; width?: number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={ref} className={className} title={title} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {children}
      </button>
      <Popover anchor={ref.current} open={open} onClose={() => setOpen(false)} placement={placement} width={width}>
        <Menu items={typeof items === 'function' ? items() : items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}
