import { useEffect, useId, useLayoutEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useExitGhost } from '../../lib/motion';
import { useEscape } from './keys';
import { ErrorBoundary } from './ErrorBoundary';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal sheet. While it is open the rest of the page is inert, Tab and Shift+Tab go round inside it,
 * Esc closes the innermost layer (a menu in the sheet first), and the focus goes back where it was.
 * It starts on the field already focused (autoFocus), else the first field, else the primary button,
 * else the sheet itself, so an informational sheet is read from its title. Named by its `title`, or by
 * `label` when it has none (the palette).
 */
export function Dialog({ title, label, onClose, onSubmit, children, footer, width = 560, className = '' }: { title?: ReactNode; label?: string; onClose: () => void; onSubmit?: () => void; children: ReactNode; footer?: ReactNode; width?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const scrim = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // Callers pass fresh closures on every render; keep the latest without re-running the focus effect,
  // which would otherwise pull focus back to the first field on each keystroke.
  const handlers = useRef({ onClose, onSubmit });
  useLayoutEffect(() => {
    handlers.current = { onClose, onSubmit };
  });
  // However the sheet is closed (Esc, the scrim, a button, or the app moving on), it leaves with its
  // exit: the sheet settles back and the scrim clears.
  useExitGhost(scrim, { duration: 200, pin: false });
  const closed = useRef(false);
  const dismiss = () => {
    if (closed.current) return;
    closed.current = true;
    handlers.current.onClose();
  };
  useEscape(dismiss);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const box = ref.current;
    if (!box) return;
    // Everything else on the page sleeps while the sheet is up (a sheet over a sheet puts the first to
    // sleep too), except the announcer, so a toast raised from the sheet is still read out.
    const asleep = [...document.body.children].filter((n): n is HTMLElement => n instanceof HTMLElement && n !== scrim.current && !n.inert && !('awake' in n.dataset));
    asleep.forEach((n) => (n.inert = true));
    if (!box.contains(document.activeElement)) (box.querySelector<HTMLElement>('input, textarea, select') || box.querySelector<HTMLElement>('button.primary') || box).focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Tab') {
        const all = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.getClientRects().length);
        const first = all[0];
        const last = all[all.length - 1];
        const at = document.activeElement;
        const to = !first ? box : e.shiftKey && (at === first || at === box) ? last : !e.shiftKey && at === last ? first : null;
        if (to) {
          e.preventDefault();
          to.focus();
        }
      } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && handlers.current.onSubmit && box.contains(e.target as Node)) {
        e.preventDefault();
        e.stopPropagation();
        handlers.current.onSubmit();
      }
    };
    box.addEventListener('keydown', onKey);
    return () => {
      box.removeEventListener('keydown', onKey);
      asleep.forEach((n) => (n.inert = false));
      if (prev?.isConnected) prev.focus();
    };
  }, []);
  return createPortal(
    <div ref={scrim} className="scrim" onMouseDown={(e) => e.target === e.currentTarget && dismiss()}>
      <div ref={ref} className={`dialog ${className}`} style={{ width }} role="dialog" aria-modal="true" aria-labelledby={title !== undefined ? titleId : undefined} aria-label={title === undefined ? label : undefined} tabIndex={-1}>
        {title !== undefined && (
          <div className="dialog-head">
            <div className="dialog-title" id={titleId}>
              {title}
            </div>
            <button className="icon-btn sm" onClick={dismiss} title="Close (Esc)" aria-label="Close">
              <X size={15} />
            </button>
          </div>
        )}
        <div className="dialog-body">
          <ErrorBoundary label="This dialog">{children}</ErrorBoundary>
        </div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
