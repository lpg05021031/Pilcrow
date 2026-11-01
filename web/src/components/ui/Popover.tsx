import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, type LucideIcon } from 'lucide-react';
import { reducedMotion } from '../../lib/motion';
import { useEscape } from './bits';

type Placement = 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end';

/** What a popover hangs from: an element, or a point (a context menu opens where the pointer was). */
export interface Anchor {
  getBoundingClientRect(): DOMRect;
  contains?(node: Node | null): boolean;
  focus?(): void;
}

const EXIT_MS = 160;

/**
 * A floating pane anchored to an element. It carries no role of its own: a menu inside says `menu`;
 * other content passes `role="dialog"` and a `label`. `width="anchor"` matches the anchor's width.
 */
export function Popover({ anchor, open, onClose, children, placement = 'bottom-start', className = '', width, role, label }: { anchor: Anchor | null; open: boolean; onClose: () => void; children: ReactNode; placement?: Placement; className?: string; width?: number | 'anchor'; role?: 'dialog'; label?: string }) {
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
    // Closed with the focus inside it (a choice, Esc, Tab): the focus goes back to what opened it.
    if (ref.current?.contains(document.activeElement)) anchor?.focus?.();
    if (!shown) return;
    if (reducedMotion()) {
      setShown(false);
      return;
    }
    const t = setTimeout(() => setShown(false), EXIT_MS + 20);
    return () => clearTimeout(t);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps -- runs as it opens and closes; `shown` is what it sets

  useLayoutEffect(() => {
    if (!open || !anchor) return;
    const place = () => {
      const el = ref.current;
      if (!el) return;
      const a = anchor.getBoundingClientRect();
      if (width === 'anchor') el.style.width = `${a.width}px`;
      // Layout size, not the on-screen box: the opening animation scales the popover down for a moment.
      const p = { width: el.offsetWidth, height: el.offsetHeight };
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let left = placement.endsWith('end') ? a.right - p.width : a.left;
      let top = placement.startsWith('top') ? a.top - p.height - 6 : a.bottom + 6;
      if (top + p.height > vh - 8) top = Math.max(8, a.top - p.height - 6);
      if (top < 8) top = Math.min(vh - p.height - 8, a.bottom + 6);
      left = Math.max(8, Math.min(left, vw - p.width - 8));
      setPos({ left, top, up: top < a.top });
    };
    place();
    // The window resized under an open popover: it follows its anchor rather than floating off it.
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, anchor, placement, width, children]);

  useEscape(onClose, open);
  useEffect(() => {
    if (!open) return;
    // Pointer, not mouse: a touch or a pen outside closes it too.
    const onDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node) || anchor?.contains?.(e.target as Node)) return;
      onClose();
    };
    window.addEventListener('pointerdown', onDown, true);
    return () => window.removeEventListener('pointerdown', onDown, true);
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
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, width: width === 'anchor' ? undefined : width, visibility: pos ? 'visible' : 'hidden', transformOrigin: `${originX} ${pos?.up ? 'bottom' : 'top'}` }}
      role={role}
      aria-label={label}
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
  /** One choice among several (a menuitemradio); leave it out for a command. */
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

/**
 * A menu that holds the focus: opening puts it on the chosen item (or the first); the arrows, Home and
 * End move it; Enter or Space picks; Tab and Esc close. The focus follows the pointer too, so keyboard
 * and mouse never disagree about the lit row; focus lights a row only when the keyboard put it there.
 */
export function Menu({ items, onClose, id, label }: { items: MenuItem[]; onClose: () => void; id?: string; label?: string }) {
  const [active, setActive] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  const selectable = items.flatMap((it, i) => (it.separator || it.heading || it.disabled ? [] : [i]));
  const focusAt = (i: number | undefined) => ref.current?.querySelector<HTMLElement>(`[data-i="${i}"]`)?.focus();
  // A frame later: the popover is hidden until it has been placed, and a hidden row cannot take focus.
  useEffect(() => {
    const t = requestAnimationFrame(() => focusAt(selectable.find((i) => items[i].checked) ?? selectable[0]));
    return () => cancelAnimationFrame(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once, as the menu opens
  const onKeyDown = (e: React.KeyboardEvent) => {
    const at = selectable.indexOf(active);
    const to = e.key === 'ArrowDown' ? selectable[(at + 1) % selectable.length] : e.key === 'ArrowUp' ? selectable[(at - 1 + selectable.length) % selectable.length] : e.key === 'Home' ? selectable[0] : e.key === 'End' ? selectable[selectable.length - 1] : undefined;
    if (to !== undefined) {
      e.preventDefault();
      focusAt(to);
    } else if (e.key === 'Tab') {
      e.preventDefault();
      onClose();
    }
  };
  // Rows arrive one after another, a few milliseconds apart, so the menu reads as unfolding rather than switching on.
  let n = 0;
  return (
    <div className="menu" ref={ref} role="menu" id={id} aria-label={label} onKeyDown={onKeyDown}>
      {items.map((it, i) => {
        if (it.separator) return <div key={i} className="menu-sep" role="separator" />;
        if (it.heading)
          return (
            <div key={i} className="menu-heading" role="presentation">
              {it.heading}
            </div>
          );
        const Icon = it.icon;
        const order = Math.min(n++, 10);
        return (
          <button
            key={i}
            data-i={i}
            role={it.checked === undefined ? 'menuitem' : 'menuitemradio'}
            aria-checked={it.checked}
            tabIndex={-1}
            className={`menu-item${it.danger ? ' danger' : ''}${active === i ? ' active' : ''}`}
            style={{ '--i': order } as React.CSSProperties}
            disabled={it.disabled}
            onFocus={(e) => setActive(e.currentTarget.matches(':focus-visible') ? i : -1)}
            onMouseEnter={(e) => {
              e.currentTarget.focus();
              setActive(i);
            }}
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

/** A button that opens a menu. Its `title` is also its accessible name: these buttons are icons. */
export function MenuButton({ items, children, className = 'icon-btn', title, placement = 'bottom-end', width }: { items: MenuItem[] | (() => MenuItem[]); children: ReactNode; className?: string; title?: string; placement?: Placement; width?: number }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const id = useId();
  return (
    <>
      <button ref={setAnchor} className={className} title={title} aria-label={title} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}>
        {children}
      </button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} placement={placement} width={width}>
        <Menu id={id} label={title} items={typeof items === 'function' ? items() : items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}
