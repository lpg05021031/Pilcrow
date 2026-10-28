import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useExitGhost } from '../../lib/motion';

export function Dialog({ title, onClose, onSubmit, children, footer, width = 560, className = '' }: { title?: ReactNode; onClose: () => void; onSubmit?: () => void; children: ReactNode; footer?: ReactNode; width?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const scrim = useRef<HTMLDivElement>(null);
  // Callers pass fresh closures on every render; keep the latest without re-running the focus effect,
  // which would otherwise pull focus back to the first field on each keystroke.
  const handlers = useRef({ onClose, onSubmit });
  handlers.current = { onClose, onSubmit };
  // However the sheet is closed (Esc, the scrim, a button, or the app moving on), it leaves with its
  // exit: the sheet settles back and the scrim clears.
  useExitGhost(scrim, { duration: 200, pin: false });
  const closed = useRef(false);
  const dismiss = () => {
    if (closed.current) return;
    closed.current = true;
    handlers.current.onClose();
  };
  const dismissRef = useRef(dismiss);
  dismissRef.current = dismiss;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>('[autofocus], input, textarea, select, button.primary');
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        dismissRef.current();
      } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && handlers.current.onSubmit && ref.current?.contains(e.target as Node)) {
        e.preventDefault();
        e.stopPropagation();
        handlers.current.onSubmit();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      prev?.focus?.();
    };
  }, []);
  return createPortal(
    <div ref={scrim} className="scrim" onMouseDown={(e) => e.target === e.currentTarget && dismiss()}>
      <div ref={ref} className={`dialog ${className}`} style={{ width }} role="dialog" aria-modal="true">
        {title !== undefined && (
          <div className="dialog-head">
            <div className="dialog-title">{title}</div>
            <button className="icon-btn sm" onClick={dismiss} title="Close (Esc)">
              <X size={15} />
            </button>
          </div>
        )}
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
