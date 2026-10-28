import { useMemo, useRef, useState } from 'react';
import { ArrowUp, Folder, CircleAlert, ChevronDown, KeyRound, RefreshCw, PanelLeftOpen } from 'lucide-react';
import { useStore, setDialog, createSession, refreshInfo, toast, setUi } from '../lib/store';
import { basename, tildify } from '../lib/format';
import { Spinner, CopyButton, AppIcon } from './ui/bits';

function greeting(name?: string) {
  const h = new Date().getHours();
  const part = h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  if (!name) return part;
  // An OS login like "tester" reads better as a name; leave real names ("Lin Wei-Ting") alone.
  const shown = /^[a-z][a-z0-9._-]*$/.test(name) ? name.charAt(0).toUpperCase() + name.slice(1) : name;
  return `${part}, ${shown}`;
}

export function Welcome() {
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const sessions = useStore((s) => s.sessions);
  const connected = useStore((s) => s.connected);
  const recents = useMemo(() => {
    const set = new Set<string>(config?.recentProjects || []);
    for (const s of sessions) if (set.size < 8 && !s.archived) set.add(s.project || s.cwd);
    return [...set].filter(Boolean).slice(0, 6);
  }, [config?.recentProjects, sessions]);
  const [picked, setCwd] = useState<string>('');
  const cwd = picked || recents[0] || '';
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);

  const go = async () => {
    if (!prompt.trim()) return;
    if (!cwd) {
      setDialog('newSession', { prompt });
      return;
    }
    setBusy(true);
    try {
      await createSession({ cwd, prompt, mode: config?.defaultMode, model: config?.defaultModel, effort: config?.defaultEffort });
    } finally {
      setBusy(false);
    }
  };

  const sidebar = useStore((s) => s.ui.sidebar);
  const root = useRef<HTMLDivElement>(null);
  return (
    <div className="welcome" ref={root}>
      {!sidebar && (
        <button className="icon-btn head-sidebar welcome-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
          <PanelLeftOpen size={16} />
        </button>
      )}
      <div className="welcome-inner">
        <AppIcon size={72} className="welcome-icon" />
        <h1 className="welcome-title">{greeting(info?.displayName || info?.user)}</h1>
        <p className="welcome-sub">{cwd ? <>What should Claude work on in <b>{basename(cwd)}</b>?</> : 'Pick a folder and tell Claude what to work on.'}</p>
        {info && !info.claude.path && (
          <div className="notice error">
            <CircleAlert size={14} />
            <div>Claude Code was not found. Install it so `claude` is on your PATH, or reinstall Pilcrow with its bundled copy. Settings → Diagnostics has details.</div>
          </div>
        )}
        {info?.claude.path && info.gateway && !info.gateway.error && !info.gateway.hasToken && !info.gateway.loggedIn && <SetupCard />}
        <div className="composer welcome-composer">
          <div className="composer-field">
            <textarea
              className="composer-input"
              rows={2}
              autoFocus
              value={prompt}
              placeholder="Describe a task, ask about the code, or paste an error…"
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  go();
                }
              }}
            />
          </div>
          <div className="composer-bar">
            <button className="cbtn" onClick={() => setDialog('newSession', { cwd, prompt })} title={cwd ? tildify(cwd, info?.home) : 'Choose a folder'}>
              <Folder size={14} />
              <span>{cwd ? basename(cwd) : 'Choose a folder'}</span>
              <ChevronDown size={12} className="cbtn-chev" />
            </button>
            <div className="grow" />
            <button className={`send${prompt.trim() ? ' ready' : ''}`} disabled={!prompt.trim() || busy || !connected} onClick={go} title="Start (Enter)">
              {busy ? <Spinner size={14} /> : <ArrowUp size={16} strokeWidth={2.25} className="send-arrow" />}
            </button>
          </div>
        </div>
        {recents.length > 1 && (
          <div className="welcome-folders">
            {recents.map((r) => (
              <button key={r} className={`folder-chip${r === cwd ? ' on' : ''}`} onClick={() => setCwd(r)} title={tildify(r, info?.home)}>
                {basename(r)}
              </button>
            ))}
          </div>
        )}
        <div className="welcome-keys">
          <span>
            <kbd>Ctrl</kbd> <kbd>N</kbd> new session with options
          </span>
          <span>
            <kbd>Ctrl</kbd> <kbd>K</kbd> commands and search
          </span>
        </div>
      </div>
    </div>
  );
}

/** First run without any API settings: say exactly what Claude Code needs and where it goes. */
function SetupCard() {
  const [checking, setChecking] = useState(false);
  const snippet = `{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-gateway.example.com",
    "ANTHROPIC_AUTH_TOKEN": "your-api-key"
  }
}`;
  return (
    <div className="setup-card">
      <div className="setup-head">
        <KeyRound size={15} />
        <span>Connect Claude Code to your API</span>
      </div>
      <p>
        Pilcrow runs the same <code>claude</code> program you use in the terminal, with the same settings. No endpoint or key was found yet. Add them to <code>~/.claude/settings.json</code>:
      </p>
      <div className="setup-snippet">
        <pre>{snippet}</pre>
        <CopyButton text={snippet} title="Copy" />
      </div>
      <div className="setup-actions">
        <span className="faint">ANTHROPIC_* environment variables work too.</span>
        <button
          className="btn sm"
          disabled={checking}
          onClick={async () => {
            setChecking(true);
            try {
              const i = await refreshInfo();
              toast(i.gateway?.hasToken || i.gateway?.loggedIn ? 'Claude Code is connected' : 'Still no API key found', i.gateway?.hasToken || i.gateway?.loggedIn ? 'success' : 'warning');
            } finally {
              setChecking(false);
            }
          }}
        >
          {checking ? <Spinner size={12} /> : <RefreshCw size={13} />} Check again
        </button>
      </div>
    </div>
  );
}
