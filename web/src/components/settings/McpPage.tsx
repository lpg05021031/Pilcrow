import { useEffect, useMemo, useState } from 'react';
import { Plus, Search, Plug, Globe, Terminal, RefreshCw, Ellipsis, Trash, KeyRound, LogOut, Power, Wrench, FileJson, CircleCheck, CircleAlert, CircleDashed, ShieldCheck, ShieldOff } from 'lucide-react';
import { useStore, call, confirmAction, toast, setDialog } from '../../lib/store';
import type { McpConfigSummary, McpEntry, McpList, McpLive } from '../../lib/types';

/** Claude Code reports a server's tools as a count or as the list itself, depending on version. */
const toolCount = (s: McpLive) => (typeof s.tools === 'number' ? s.tools : Array.isArray(s.tools) ? s.tools.length : 0);
import { tildify } from '../../lib/format';
import { MenuButton } from '../ui/Popover';
import { Dialog } from '../ui/Dialog';
import { Select } from '../ui/Select';
import { Spinner } from '../ui/bits';
import { errText } from '../../lib/format';

type Scope = 'user' | 'project' | 'local';
const SCOPE_LABEL: Record<Scope, string> = { user: 'All projects', project: 'Project (.mcp.json)', local: 'This project, just you' };

export function useActiveSession() {
  const activeId = useStore((s) => s.activeId);
  const rt = useStore((s) => (activeId ? s.rt[activeId] : null));
  const session = useStore((s) => s.sessions.find((x) => x.id === activeId));
  return { id: activeId, rt, cwd: rt?.cwd || session?.cwd || '' };
}

export function statusMeta(st?: string) {
  switch (st) {
    case 'connected':
      return { label: 'Connected', icon: CircleCheck, cls: 'ok' };
    case 'needs-auth':
      return { label: 'Needs sign-in', icon: KeyRound, cls: 'warn' };
    case 'failed':
      return { label: 'Failed', icon: CircleAlert, cls: 'err' };
    case 'pending':
      return { label: 'Connecting', icon: CircleDashed, cls: 'muted' };
    case 'disabled':
      return { label: 'Disabled', icon: Power, cls: 'muted' };
    default:
      return { label: 'Not running', icon: CircleDashed, cls: 'muted' };
  }
}

/** One line that says what the server is: its URL, or its command. */
export function describeConfig(c: McpEntry['config']) {
  if (c.type === 'stdio') return [c.command, ...(c.args || [])].join(' ');
  return c.url || '';
}

export function McpPage() {
  const { id: sessionId, rt, cwd } = useActiveSession();
  const info = useStore((s) => s.info);
  const [data, setData] = useState<McpList | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState('');
  const [adding, setAdding] = useState(false);
  const [showTools, setShowTools] = useState<string | null>(null);
  const live = useMemo(() => rt?.mcp || [], [rt?.mcp]);
  const liveByName = useMemo(() => new Map(live.map((s) => [s.name, s])), [live]);

  const load = async () => {
    setLoading(true);
    try {
      setData(await call<McpList>('mcp.list', { cwd }, { quiet: true }));
      if (sessionId && rt?.live) call('session.mcpStatus', { id: sessionId }, { quiet: true }).catch(() => {});
    } catch (e) {
      toast(errText(e) || 'Could not read the MCP configuration', 'error');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [cwd]); // eslint-disable-line react-hooks/exhaustive-deps

  const servers = useMemo(() => {
    const n = q.trim().toLowerCase();
    const list = data?.servers || [];
    // Servers Claude Code reports but no file we read declares (plugins, managed policy) still get a row.
    const extra: McpEntry[] = live.filter((s) => !list.some((e) => e.name === s.name)).map((s) => ({ name: s.name, scope: (s.scope as McpEntry['scope']) || 'user', source: '', config: { type: 'http' }, raw: null }));
    const all = [...list, ...extra];
    return n ? all.filter((s) => s.name.toLowerCase().includes(n) || describeConfig(s.config).toLowerCase().includes(n)) : all;
  }, [data, live, q]);

  const needsSession = (what: string) => {
    if (!sessionId || !rt?.live) {
      toast(`${what} needs a session with Claude Code running. Send a message in a session first.`, 'info');
      return false;
    }
    return true;
  };

  const reconnect = async (name: string) => {
    if (!needsSession('Reconnecting')) return;
    try {
      await call('session.mcp.reconnect', { id: sessionId, name });
      toast(`Reconnected ${name}`, 'success');
    } catch {
      /* toasted */
    }
  };
  const toggle = async (name: string, enabled: boolean) => {
    if (!needsSession(enabled ? 'Enabling' : 'Disabling')) return;
    await call('session.mcp.toggle', { id: sessionId, name, enabled }).catch(() => {});
  };
  const signIn = (name: string) => {
    if (!needsSession('Signing in')) return;
    setDialog('mcpAuth', { sessionId: sessionId!, name });
  };
  const signOut = (name: string) =>
    confirmAction({
      title: `Sign out of ${name}?`,
      body: 'The saved token is removed. Claude asks you to sign in again the next time it uses this server.',
      confirm: 'Sign out',
      onConfirm: async () => {
        if (!needsSession('Signing out')) return;
        await call('session.mcp.clearAuth', { id: sessionId, name }).catch(() => {});
        toast(`Signed out of ${name}`, 'success');
      },
    });
  const remove = (s: McpEntry) =>
    confirmAction({
      title: `Remove ${s.name}?`,
      body: s.scope === 'project' ? `It is deleted from ${tildify(s.source, info?.home)}, which is shared with everyone who uses this repository.` : `It is removed from ${tildify(s.source, info?.home)}. Running sessions keep it until they restart.`,
      confirm: 'Remove server',
      danger: true,
      onConfirm: async () => {
        try {
          setData(await call<McpList>('mcp.remove', { scope: s.scope, cwd, name: s.name }));
          toast(`Removed ${s.name}`, 'success');
        } catch {
          /* toasted */
        }
      },
    });
  const approve = async (s: McpEntry, approved: boolean) => {
    setData(await call<McpList>('mcp.approve', { cwd, name: s.name, approved }).catch(() => data!));
  };

  const toolsOf = showTools ? live.find((s) => s.name === showTools) : null;

  return (
    <div className="cust">
      <div className="cust-bar">
        <label className="cust-search">
          <Search size={13} />
          <input placeholder="Search servers" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <div className="grow" />
        <button className="icon-btn sm" title="Refresh" onClick={load}>
          {loading ? <Spinner size={13} /> : <RefreshCw size={13} />}
        </button>
        <button className="btn sm primary" onClick={() => setAdding(true)}>
          <Plus size={14} /> Add server
        </button>
      </div>

      {data && servers.length === 0 && !q && (
        <div className="cust-intro">
          <div className="cust-intro-title">Give Claude more tools</div>
          <div className="cust-intro-body">An MCP server lets Claude use another system: an issue tracker, a database, a design tool, your own scripts. Add one by URL, or as a command Pilcrow starts on your machine.</div>
          <div className="row">
            <button className="btn sm primary" onClick={() => setAdding(true)}>
              <Plus size={13} /> Add server
            </button>
          </div>
        </div>
      )}

      {servers.length > 0 && (
        <div className="cust-table">
          <div className="cust-thead">
            <span>Server</span>
            <span>Scope</span>
            <span>Status</span>
            <span />
          </div>
          {servers.map((s) => {
            const st = liveByName.get(s.name);
            const meta = statusMeta(st?.status);
            const Icon = s.config.type === 'stdio' ? Terminal : Globe;
            return (
              <div key={`${s.scope}:${s.name}`} className="cust-row cust-mcp">
                <span className="cust-ico">
                  <Icon size={16} />
                </span>
                <span className="cust-main">
                  <span className="cust-name">{s.name}</span>
                  <span className="cust-desc mono ellipsis" title={describeConfig(s.config) || s.source}>
                    {describeConfig(s.config) || <span className="faint">{s.scope === 'user' ? 'Provided by a plugin or policy' : s.source}</span>}
                  </span>
                </span>
                <span className="cust-scope">
                  <span className="chip">{s.scope === 'user' ? 'All projects' : s.scope === 'project' ? 'Project' : 'Local'}</span>
                  {s.scope === 'project' && s.approved === false && (
                    <span className="chip warn" title="You rejected this project server; Claude does not start it">
                      rejected
                    </span>
                  )}
                </span>
                <span className={`cust-status ${meta.cls}`} title={st?.error || (st?.status ? '' : 'Status is known once a session has Claude Code running')}>
                  {st?.status === 'needs-auth' ? (
                    <button className="btn xs" onClick={() => signIn(s.name)}>
                      <KeyRound size={12} /> Sign in
                    </button>
                  ) : st?.status === 'failed' ? (
                    <button className="btn xs" onClick={() => reconnect(s.name)} title={st.error}>
                      <RefreshCw size={12} /> Retry
                    </button>
                  ) : (
                    <>
                      <meta.icon size={14} />
                      <span>{meta.label}</span>
                      {st?.status === 'connected' && toolCount(st) > 0 && (
                        <button className="link cust-tools" onClick={() => setShowTools(s.name)}>
                          {toolCount(st)} tools
                        </button>
                      )}
                    </>
                  )}
                </span>
                <MenuButton
                  className="icon-btn sm cust-more"
                  placement="bottom-end"
                  items={[
                    ...(st?.status === 'connected' ? [{ label: `Show ${st.tools} tools`, icon: Wrench, onSelect: () => setShowTools(s.name) }] : []),
                    { label: 'Reconnect', icon: RefreshCw, onSelect: () => reconnect(s.name) },
                    st?.status === 'disabled' ? { label: 'Enable', icon: Power, onSelect: () => toggle(s.name, true) } : { label: 'Disable for this session', icon: Power, onSelect: () => toggle(s.name, false) },
                    { separator: true },
                    ...(s.config.type !== 'stdio' ? [{ label: 'Sign in…', icon: KeyRound, onSelect: () => signIn(s.name) }, { label: 'Sign out', icon: LogOut, onSelect: () => signOut(s.name) }, { separator: true } as const] : []),
                    ...(s.scope === 'project' ? [s.approved === false ? { label: 'Allow this project server', icon: ShieldCheck, onSelect: () => approve(s, true) } : { label: 'Reject this project server', icon: ShieldOff, onSelect: () => approve(s, false) }] : []),
                    { label: 'Show config file', icon: FileJson, disabled: !s.source, onSelect: () => call('shell.openPath', { path: s.source }) },
                    ...(s.raw ? [{ separator: true } as const, { label: 'Remove…', icon: Trash, danger: true, onSelect: () => remove(s) }] : []),
                  ]}
                >
                  <Ellipsis size={14} />
                </MenuButton>
                {st?.status === 'failed' && st.error && <div className="cust-error">{st.error}</div>}
              </div>
            );
          })}
        </div>
      )}

      {data && (
        <div className="cust-foot faint">
          {live.length ? 'Status from the current session.' : 'Statuses appear once a session has Claude Code running.'} Config: <span className="mono">{tildify(data.file, info?.home)}</span>
          {cwd ? (
            <>
              {' '}
              and <span className="mono">{tildify(cwd, info?.home)}/.mcp.json</span>
            </>
          ) : null}
        </div>
      )}

      {adding && (
        <AddMcpDialog
          cwd={cwd}
          onClose={() => setAdding(false)}
          onAdded={(list) => {
            setData(list);
            setAdding(false);
          }}
        />
      )}
      {toolsOf && <ToolsDialog server={toolsOf} sessionId={sessionId!} onClose={() => setShowTools(null)} />}
    </div>
  );
}

function AddMcpDialog({ cwd, onClose, onAdded }: { cwd: string; onClose: () => void; onAdded: (l: McpList) => void }) {
  const info = useStore((s) => s.info);
  const [name, setName] = useState('');
  const [type, setType] = useState<'http' | 'sse' | 'stdio'>('http');
  const [url, setUrl] = useState('');
  const [headers, setHeaders] = useState('');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [env, setEnv] = useState('');
  const [scope, setScope] = useState<Scope>('user');
  const [busy, setBusy] = useState(false);
  const [json, setJson] = useState('');
  const [mode, setMode] = useState<'form' | 'json'>('form');

  // A pasted `claude mcp add …` command or JSON fills the form.
  const fromJson = () => {
    try {
      const o = JSON.parse(json);
      const [n, cfg] = o.mcpServers ? Object.entries(o.mcpServers)[0] : o.name && o.config ? [o.name, o.config] : [name, o];
      if (!n) throw new Error('No server name');
      const c = (cfg || {}) as { command?: string; args?: string[]; env?: Record<string, string>; type?: string; url?: string; headers?: Record<string, string> };
      setName(String(n));
      if (c.command) {
        setType('stdio');
        setCommand(c.command);
        setArgs((c.args || []).join(' '));
        setEnv(
          Object.entries(c.env || {})
            .map(([k, v]) => `${k}=${v}`)
            .join('\n'),
        );
      } else {
        setType(c.type === 'sse' ? 'sse' : 'http');
        setUrl(c.url || '');
        setHeaders(
          Object.entries(c.headers || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join('\n'),
        );
      }
      setMode('form');
    } catch (e) {
      toast(`Could not read that JSON: ${errText(e)}`, 'error');
    }
  };

  const valid = /^[A-Za-z0-9][\w.-]{0,63}$/.test(name.trim()) && (type === 'stdio' ? command.trim() : /^https?:\/\//i.test(url.trim()));
  const submit = async () => {
    if (!valid || busy) return;
    setBusy(true);
    try {
      const list = await call<McpList>('mcp.add', { scope, cwd, name: name.trim(), form: { type, url, headers, command, args, env } });
      toast(`Added ${name.trim()}. Sessions pick it up when Claude Code restarts.`, 'success');
      onAdded(list);
    } catch {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Add an MCP server"
      onClose={onClose}
      onSubmit={submit}
      width={540}
      footer={
        <>
          <button className="btn sm ghost" onClick={() => setMode(mode === 'form' ? 'json' : 'form')}>
            {mode === 'form' ? 'Paste JSON instead' : 'Back to the form'}
          </button>
          <div className="grow" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          {mode === 'form' ? (
            <button className="btn primary" disabled={!valid || busy} onClick={submit}>
              {busy ? <Spinner size={13} /> : 'Add server'}
            </button>
          ) : (
            <button className="btn primary" disabled={!json.trim()} onClick={fromJson}>
              Use this
            </button>
          )}
        </>
      }
    >
      {mode === 'json' ? (
        <div className="cust-form">
          <div className="hint">
            Paste what the server’s docs give you: a <code>mcpServers</code> block, or a single server’s config.
          </div>
          <textarea className="textarea mono" rows={9} autoFocus value={json} onChange={(e) => setJson(e.target.value)} placeholder={'{\n  "mcpServers": {\n    "linear": { "type": "http", "url": "https://mcp.linear.app/mcp" }\n  }\n}'} />
        </div>
      ) : (
        <div className="cust-form">
          <div className="cust-two">
            <div className="field">
              <label>Name</label>
              <input className="input" autoFocus placeholder="linear" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label>Type</label>
              <Select
                value={type}
                onChange={(v) => setType(v as McpConfigSummary['type'])}
                options={[
                  { value: 'http', label: 'HTTP', hint: 'Remote server; signs in with OAuth when needed' },
                  { value: 'sse', label: 'SSE', hint: 'Older remote servers' },
                  { value: 'stdio', label: 'Command', hint: 'A program Claude Code starts on this machine' },
                ]}
              />
            </div>
          </div>
          {type === 'stdio' ? (
            <>
              <div className="field">
                <label>Command</label>
                <input className="input mono" placeholder="npx" value={command} onChange={(e) => setCommand(e.target.value)} />
              </div>
              <div className="field">
                <label>Arguments</label>
                <input className="input mono" placeholder="-y @modelcontextprotocol/server-filesystem /path" value={args} onChange={(e) => setArgs(e.target.value)} />
              </div>
              <div className="field">
                <label>Environment variables</label>
                <textarea className="textarea mono" rows={2} placeholder={'API_KEY=…\nONE_PER_LINE=…'} value={env} onChange={(e) => setEnv(e.target.value)} />
              </div>
            </>
          ) : (
            <>
              <div className="field">
                <label>URL</label>
                <input className="input mono" placeholder="https://mcp.example.com/mcp" value={url} onChange={(e) => setUrl(e.target.value)} />
              </div>
              <div className="field">
                <label>Headers</label>
                <textarea className="textarea mono" rows={2} placeholder={'Authorization: Bearer …\nOne-Per-Line: …'} value={headers} onChange={(e) => setHeaders(e.target.value)} />
                <div className="hint">Leave empty for servers that sign you in with OAuth; Pilcrow asks when the server does.</div>
              </div>
            </>
          )}
          <div className="field">
            <label>Available in</label>
            <Select
              value={scope}
              onChange={(v) => setScope(v as Scope)}
              options={[
                { value: 'user', label: SCOPE_LABEL.user, hint: `Saved in ~/.claude.json` },
                { value: 'local', label: SCOPE_LABEL.local, hint: cwd ? `For ${tildify(cwd, info?.home)}, not shared` : 'Open a session first', disabled: !cwd },
                { value: 'project', label: SCOPE_LABEL.project, hint: cwd ? `Committed with the repository` : 'Open a session first', disabled: !cwd },
              ]}
            />
          </div>
        </div>
      )}
    </Dialog>
  );
}

function ToolsDialog({ server, sessionId, onClose }: { server: McpLive; sessionId: string; onClose: () => void }) {
  const [tools, setTools] = useState<{ name: string; description?: string }[] | null>(null);
  useEffect(() => {
    call<McpLive[] | null>('session.mcpStatus', { id: sessionId }, { quiet: true })
      .then((list) => {
        const t = (list || []).find((s) => s.name === server.name)?.tools;
        setTools(Array.isArray(t) ? (t as { name: string; description?: string }[]) : []);
      })
      .catch(() => setTools([]));
  }, [sessionId, server.name]);
  return (
    <Dialog title={`${server.name} · tools`} onClose={onClose} width={560}>
      {!tools ? (
        <Spinner size={14} />
      ) : tools.length === 0 ? (
        <div className="hint">This server lists no tools.</div>
      ) : (
        <div className="cust-tools-list">
          {tools.map((t) => (
            <div key={t.name} className="cust-tool">
              <span className="mono cust-tool-name">{t.name}</span>
              {t.description && <span className="cust-tool-desc">{t.description}</span>}
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}

export { Plug };
