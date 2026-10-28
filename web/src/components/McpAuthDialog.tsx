import { useEffect, useRef, useState } from 'react';
import { ExternalLink, KeyRound, CircleCheck, ClipboardPaste } from 'lucide-react';
import { useStore, call, setDialog, toast } from '../lib/store';
import { Dialog } from './ui/Dialog';
import { Spinner, copyText } from './ui/bits';

interface AuthStart {
  authUrl?: string;
  requiresUserAction?: boolean;
  callbackExpected?: boolean;
  redirectScheme?: 'localhost' | 'custom';
  callbackPort?: number;
  state?: string;
}

/**
 * OAuth sign-in to an MCP server. Claude Code runs the flow and listens for the browser's redirect on
 * localhost; Pilcrow opens the sign-in page, watches the server's status and offers a paste box for
 * the cases where the redirect cannot reach this machine.
 */
export function McpAuthDialog() {
  const spec = useStore((s) => s.dialogs.mcpAuth)!;
  const rt = useStore((s) => s.rt[spec.sessionId]);
  const status = rt?.mcp?.find((m) => m.name === spec.name)?.status;
  const [start, setStart] = useState<AuthStart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const opened = useRef(false);
  const close = () => setDialog('mcpAuth', null);

  useEffect(() => {
    let alive = true;
    call<AuthStart>('session.mcp.auth', { id: spec.sessionId, name: spec.name }, { quiet: true })
      .then((r) => {
        if (!alive) return;
        setStart(r || {});
        if (r?.authUrl && !opened.current) {
          opened.current = true;
          call('shell.openExternal', { url: r.authUrl }, { quiet: true }).catch(() => window.open(r.authUrl, '_blank', 'noopener'));
        }
        if (r && r.requiresUserAction === false) setDone(true);
      })
      .catch((e) => alive && setError(e.message || 'Could not start the sign-in'));
    return () => {
      alive = false;
    };
  }, [spec.sessionId, spec.name]);

  // The server flips to connected when the flow completes; give the person a beat to read it.
  useEffect(() => {
    if (start && status === 'connected') {
      setDone(true);
      const t = setTimeout(() => {
        toast(`Signed in to ${spec.name}`, 'success');
        close();
      }, 900);
      return () => clearTimeout(t);
    }
  }, [status, start]); // eslint-disable-line react-hooks/exhaustive-deps

  const submitPasted = async () => {
    const url = pasted.trim();
    if (!url || busy) return;
    setBusy(true);
    try {
      await call('session.mcp.authCallback', { id: spec.sessionId, name: spec.name, url });
      setPasted('');
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={
        <span className="row">
          <KeyRound size={15} /> Sign in to {spec.name}
        </span>
      }
      onClose={close}
      width={480}
      footer={
        <>
          {start?.authUrl && !done && (
            <button className="btn sm ghost" onClick={() => copyText(start.authUrl!).then(() => toast('Link copied', 'success'))}>
              Copy link
            </button>
          )}
          <div className="grow" />
          <button className="btn" onClick={close}>
            {done ? 'Done' : 'Cancel'}
          </button>
          {start?.authUrl && !done && (
            <button className="btn primary" onClick={() => call('shell.openExternal', { url: start.authUrl }, { quiet: true }).catch(() => window.open(start.authUrl, '_blank', 'noopener'))}>
              <ExternalLink size={13} /> Open sign-in page
            </button>
          )}
        </>
      }
    >
      <div className="mcp-auth">
        {error ? (
          <div className="mcp-auth-line err">{error}</div>
        ) : !start ? (
          <div className="mcp-auth-line">
            <Spinner size={14} /> Asking {spec.name} where to sign in…
          </div>
        ) : done ? (
          <div className="mcp-auth-line ok">
            <CircleCheck size={16} /> Signed in. Claude can use {spec.name} now.
          </div>
        ) : (
          <>
            <div className="mcp-auth-line">
              <Spinner size={14} /> Waiting for you to finish in the browser.
            </div>
            <div className="hint">
              {start.redirectScheme === 'localhost' && start.callbackPort
                ? `When the provider sends you back, it lands on localhost:${start.callbackPort}, where Claude Code is listening. If the browser can’t open that page, paste its address below.`
                : 'When the provider sends you back, Claude Code finishes the sign-in. If the browser ends on a page it cannot open, paste that page’s address below.'}
            </div>
            <div className="mcp-auth-paste">
              <input className="input mono" placeholder="http://localhost:…/callback?code=…" value={pasted} onChange={(e) => setPasted(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submitPasted()} />
              <button className="btn sm" disabled={!pasted.trim() || busy} onClick={submitPasted}>
                {busy ? <Spinner size={12} /> : <ClipboardPaste size={13} />} Use
              </button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
