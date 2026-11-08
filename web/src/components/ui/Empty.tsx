import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Spinner } from './bits';

/** What a panel or page shows when it has nothing: an icon, one line, a hint, an action. */
export function Empty({ icon: Icon, title, hint, children }: { icon?: LucideIcon; title: ReactNode; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {Icon && <Icon size={22} aria-hidden="true" />}
      <div>{title}</div>
      {hint && <div className="hint">{hint}</div>}
      {children}
    </div>
  );
}

/**
 * Something on its way, said once for a screen reader: with `rows`, faint rows where a list's rows
 * will be (the first a little shorter each time); without, a spinner and the label.
 */
export function Loading({ label, rows }: { label: string; rows?: number }) {
  if (!rows)
    return (
      <div className="empty" role="status">
        <Spinner /> {label}
      </div>
    );
  return (
    <div className="loading-rows" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, k) => (
        <div key={k} className="sk-row">
          <div className="sk" style={{ width: `${46 - k * 9}%` }} />
          <div className="sk short" />
          <div className="sk" />
        </div>
      ))}
    </div>
  );
}
