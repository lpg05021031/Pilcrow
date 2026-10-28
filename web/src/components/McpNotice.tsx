import { useState } from 'react';
import { KeyRound, RefreshCw, X, CircleAlert } from 'lucide-react';
import { useStore, call, setDialog, toast } from '../lib/store';
import { Spinner, Leave } from './ui/bits';

/**
 * One line under the session header while an MCP server this session uses needs attention. The
 * usual cause is an expired sign-in; the fix is one click here rather than a trip to Settings.
 */
export function McpNotice({ sessionId }: { sessionId: string }) {
  const mcp = useStore((s) => s.rt[sessionId]?.mcp);
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const needy = (mcp || []).filter((m) => (m.status === 'needs-auth' || m.status === 'failed') && !dismissed[`${m.name}:${m.status}`]);
  if (!needy.length) return null;

  const retry = async (name: string) => {
    setBusy(name);
    try {
      await call('session.mcp.reconnect', { id: sessionId, name });
      toast(`Reconnected ${name}`, 'success');
    } catch {
      /* toasted */
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mcp-notice-stack">
      {needy.slice(0, 3).map((m) => (
        <Leave key={m.name} className={`mcp-notice ${m.status === 'needs-auth' ? 'auth' : 'fail'}`} role="status">
          {m.status === 'needs-auth' ? <KeyRound size={14} /> : <CircleAlert size={14} />}
          <span className="mcp-notice-text">
            {m.status === 'needs-auth' ? (
              <>
                <b>{m.name}</b> needs you to sign in again before Claude can use it.
              </>
            ) : (
              <>
                <b>{m.name}</b> is not connected{m.error ? <span className="faint"> · {m.error.split('\n')[0].slice(0, 120)}</span> : '.'}
              </>
            )}
          </span>
          {m.status === 'needs-auth' ? (
            <button className="btn xs primary" onClick={() => setDialog('mcpAuth', { sessionId, name: m.name })}>
              Sign in
            </button>
          ) : (
            <button className="btn xs" disabled={busy === m.name} onClick={() => retry(m.name)}>
              {busy === m.name ? <Spinner size={11} /> : <RefreshCw size={12} />} Retry
            </button>
          )}
          <button className="icon-btn sm" title="Dismiss" onClick={() => setDismissed({ ...dismissed, [`${m.name}:${m.status}`]: true })}>
            <X size={13} />
          </button>
        </Leave>
      ))}
    </div>
  );
}
