// The app's own marks: the pilcrow, the app icon, and the pilcrow at work.

/** The Pilcrow mark: an alignment pilcrow with a safelight core. `tight` crops to the ink so it sits in a row like any other icon. */
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
