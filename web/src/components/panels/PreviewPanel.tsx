import { useMemo, useRef, useState } from 'react';
import { RotateCw, ExternalLink, AppWindow, ArrowRight } from 'lucide-react';
import { useStore, models, call } from '../../lib/store';
import type { SessionSummary } from '../../lib/types';

const urls = new Map<string, string>();

function findUrls(sessionId: string): string[] {
  const m = models.get(sessionId);
  const found = new Set<string>();
  if (!m) return [];
  for (const t of m.tools.values()) {
    if (t.name !== 'Bash' && t.name !== 'PowerShell') continue;
    const s = t.structured;
    const text = `${s?.stdout || ''}\n${s?.stderr || ''}\n${t.result?.text || ''}`;
    for (const u of text.match(/https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:\/[^\s'"<>)]*)?/g) || []) found.add(u.replace('0.0.0.0', 'localhost').replace(/[.,;]+$/, ''));
  }
  return [...found].slice(-6).reverse();
}

export function PreviewPanel({ session }: { session: SessionSummary }) {
  const rev = useStore((s) => s.rt[session.id]?.rev);
  const [url, setUrl] = useState(() => urls.get(session.id) || '');
  const [draft, setDraft] = useState(url || 'http://localhost:3000');
  const [nonce, setNonce] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const detected = useMemo(() => findUrls(session.id), [session.id, rev]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (u: string) => {
    let v = u.trim();
    if (!v) return;
    if (!/^https?:\/\//.test(v)) v = `http://${v}`;
    urls.set(session.id, v);
    setUrl(v);
    setDraft(v);
    setNonce((n) => n + 1);
  };

  return (
    <div className="preview">
      <div className="panel-toolbar">
        <form
          className="url-bar"
          onSubmit={(e) => {
            e.preventDefault();
            go(draft);
          }}
        >
          <input className="input mono" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="http://localhost:3000" spellCheck={false} />
          <button className="icon-btn sm" type="submit" title="Open">
            <ArrowRight size={14} />
          </button>
        </form>
        <button className="icon-btn sm" title="Reload" disabled={!url} onClick={() => setNonce((n) => n + 1)}>
          <RotateCw size={13} />
        </button>
        <button className="icon-btn sm" title="Open in your browser" disabled={!url} onClick={() => call('shell.openExternal', { url })}>
          <ExternalLink size={13} />
        </button>
      </div>
      {detected.length > 0 && (
        <div className="chips">
          {detected.map((u) => (
            <button key={u} className={`chip mono${u === url ? ' on' : ''}`} onClick={() => go(u)}>
              {u.replace(/^https?:\/\//, '')}
            </button>
          ))}
        </div>
      )}
      {url ? (
        <iframe key={nonce} ref={frame} className="preview-frame" src={url} title="App preview" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads" />
      ) : (
        <div className="empty">
          <AppWindow size={22} />
          <div>Preview a local web app next to the conversation.</div>
          <div className="hint">Ask Claude to start your dev server — addresses it prints show up above as shortcuts.</div>
        </div>
      )}
    </div>
  );
}
