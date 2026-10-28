import { useEffect, useState } from 'react';
import { Gauge, Plug, Cpu, FolderGit2, Copy, RefreshCw, ChevronRight, FolderOpen, Trash, Wrench, Globe, BookMarked, Sparkles } from 'lucide-react';
import { useStore, call, toast, confirmAction, setDialog } from '../../lib/store';
import type { SessionSummary, MemoryNote } from '../../lib/types';
import { tokens, cost, tildify } from '../../lib/format';
import { modelLabel, useGatewayModels } from '../../lib/models';
import { contextLabel, copyText, Spinner, Num, RingGauge } from '../ui/bits';
import { MODES } from '../Composer';
import type { McpStatus } from '../../lib/types';

interface Usage {
  categories: { name: string; tokens: number; color: string; kind: string }[];
  totalTokens: number;
  maxTokens: number;
  percentage: number;
}

export function InfoPanel({ session, cwd }: { session: SessionSummary; cwd: string }) {
  const rt = useStore((s) => s.rt[session.id]);
  const gw = useGatewayModels();
  const modelId = rt?.activeModel || rt?.model || '';
  const info = useStore((s) => s.info);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [mcp, setMcp] = useState<McpStatus[] | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);
  const init = rt?.init;
  const used = rt?.usage?.contextTokens || 0;
  const total = rt?.usage?.contextWindow || 0;

  const analyze = async () => {
    setLoading('ctx');
    try {
      setUsage(await call<Usage>('session.contextUsage', { id: session.id }));
    } finally {
      setLoading(null);
    }
  };
  const refreshMcp = async () => {
    setLoading('mcp');
    try {
      setMcp(await call<McpStatus[] | null>('session.mcpStatus', { id: session.id }));
    } finally {
      setLoading(null);
    }
  };
  const servers: McpStatus[] = rt?.mcp?.length ? rt.mcp : mcp || init?.mcpServers || [];
  const mode = MODES.find((m) => m.id === rt?.mode);
  const pct = total > 0 ? used / total : 0;
  const tone = pct > 0.85 ? 'var(--err)' : pct > 0.65 ? 'var(--accent)' : 'var(--text-2)';

  return (
    <div className="info-panel">
      <section className="ps">
        <div className="ps-head">
          <Gauge size={14} /> Context window
        </div>
        {/* the gauge fills from empty when the panel opens, and the figures roll to each new value */}
        <div className="gauge-row">
          <RingGauge pct={pct} tone={tone}>
            {total ? <Num value={pct * 100} format={(v) => `${Math.round(v)}%`} /> : <Num value={used} format={tokens} />}
          </RingGauge>
          <div className="gauge-side">
            <div className="gauge-line">
              {total ? (
                <>
                  <b className="tnum">
                    <Num value={used} format={tokens} />
                  </b>{' '}
                  of {tokens(total)} tokens
                </>
              ) : (
                contextLabel(used, total)
              )}
            </div>
            <div className="stat-row">
              <div className="stat">
                <span className="stat-k">Cost so far</span>
                <span className="stat-v tnum">
                  <Num value={rt?.usage?.costUsd || 0} format={cost} />
                </span>
              </div>
              <div className="stat">
                <span className="stat-k">Output tokens</span>
                <span className="stat-v tnum">
                  <Num value={rt?.usage?.outputTokens || 0} format={tokens} />
                </span>
              </div>
            </div>
          </div>
        </div>
        {usage ? (
          <div className="ctx-cats">
            <div className="ctx-bar">
              {usage.categories
                .filter((c) => c.kind !== 'deferred' && c.tokens > 0)
                .map((c) => (
                  <div key={c.name} title={`${c.name}: ${tokens(c.tokens)}`} style={{ width: `${(c.tokens / usage.maxTokens) * 100}%`, background: catColor(c) }} />
                ))}
            </div>
            {usage.categories
              .filter((c) => c.tokens > 0 && c.kind !== 'free')
              .map((c) => (
                <div key={c.name} className="ctx-cat">
                  <span className="ctx-swatch" style={{ background: catColor(c) }} />
                  <span className="grow">{c.name}</span>
                  <span className="mono faint">{tokens(c.tokens)}</span>
                </div>
              ))}
          </div>
        ) : (
          <button className="btn sm ghost ctx-analyze" onClick={analyze} disabled={!rt?.live || loading === 'ctx'} title={rt?.live ? '' : 'Available while Claude Code is running for this session'}>
            {loading === 'ctx' ? <Spinner size={12} /> : <Gauge size={13} />} Break down usage
          </button>
        )}
        <div className="hint">Type /compact to summarize the conversation when it gets long.</div>
      </section>

      <section className="ps">
        <div className="ps-head">
          <Cpu size={14} /> Claude Code
        </div>
        <div className="kv">
          <span>Model</span>
          <span title={modelId || undefined}>
            {modelId ? modelLabel(modelId, gw.models, rt?.models || undefined) : 'Default'}
            {modelId && modelLabel(modelId, gw.models, rt?.models || undefined) !== modelId && <span className="faint mono kv-sub"> {modelId}</span>}
          </span>
          <span>Permission mode</span>
          <span>{mode?.label || rt?.mode}</span>
          <span>Effort</span>
          <span>{rt?.effort || init?.effort || 'Default'}</span>
          <span>Version</span>
          <span className="mono">{init?.version || info?.claude.version || '—'}</span>
          <span>Status</span>
          <span>{rt?.live ? 'Process running' : 'Starts with your next message'}</span>
          <span>Session ID</span>
          <span className="mono row">
            <span className="ellipsis">{session.id.slice(0, 13)}…</span>
            <button className="icon-btn sm" onClick={() => copyText(session.id).then(() => toast('Copied', 'success'), () => toast('Could not copy to the clipboard', 'error'))} title="Copy session ID">
              <Copy size={12} />
            </button>
          </span>
          <span>Folder</span>
          <span className="mono ellipsis" title={cwd}>
            {tildify(cwd, info?.home)}
          </span>
        </div>
        {init?.tools && (
          <>
            <button className="agent-prompt-toggle" onClick={() => setShowTools(!showTools)}>
              <ChevronRight size={13} className={showTools ? 'rot' : ''} /> {init.tools.length} tools, {init.skills?.length || 0} skills, {init.agents?.length || 0} agents
            </button>
            {showTools && (
              <div className="chips wrap">
                {init.tools.map((t) => (
                  <span key={t} className="chip mono">
                    <Wrench size={10} /> {t}
                  </span>
                ))}
                {(init.agents || []).map((a) => (
                  <span key={a} className="chip">
                    agent: {a}
                  </span>
                ))}
                {(init.skills || []).slice(0, 40).map((s) => (
                  <span key={s} className="chip">
                    skill: {s}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <MemorySection session={session} />

      <section className="ps">
        <div className="ps-head">
          <Plug size={14} /> MCP servers
          <div className="grow" />
          <button className="link ps-link" onClick={() => setDialog('settings', 'mcp')}>
            Manage
          </button>
          {rt?.live && (
            <button className="icon-btn sm" title="Refresh" onClick={refreshMcp}>
              {loading === 'mcp' ? <Spinner size={12} /> : <RefreshCw size={12} />}
            </button>
          )}
        </div>
        {servers.length ? (
          servers.map((s) => {
            const nTools = typeof s.tools === 'number' ? s.tools : Array.isArray(s.tools) ? s.tools.length : 0;
            return (
              <div key={s.name} className="mcp-row" title={s.error || undefined}>
                <span className={`mcp-dot ${s.status}`} />
                <span className="grow ellipsis">{s.name}</span>
                {s.status === 'needs-auth' ? (
                  <button className="btn xs primary" onClick={() => setDialog('mcpAuth', { sessionId: session.id, name: s.name })}>
                    Sign in
                  </button>
                ) : s.status === 'failed' ? (
                  <button className="btn xs" onClick={() => call('session.mcp.reconnect', { id: session.id, name: s.name }).then(refreshMcp, () => {})}>
                    Retry
                  </button>
                ) : (
                  <span className="faint">{s.status === 'connected' ? (nTools ? `${nTools} tools` : 'connected') : s.status}</span>
                )}
              </div>
            );
          })
        ) : (
          <div className="ps-empty">{init ? 'No MCP servers configured.' : 'Shown after Claude Code starts.'}</div>
        )}
      </section>

      {session.worktree && (
        <section className="ps">
          <div className="ps-head">
            <FolderGit2 size={14} /> Worktree
          </div>
          <div className="kv">
            <span>Branch</span>
            <span className="mono">{session.worktree.branch}</span>
            <span>Based on</span>
            <span className="mono">{session.worktree.base}</span>
            <span>Location</span>
            <span className="mono ellipsis" title={session.worktree.path}>
              {tildify(session.worktree.path, info?.home)}
            </span>
          </div>
          <div className="row">
            <button className="btn sm" onClick={() => call('shell.openPath', { path: session.worktree!.path })}>
              <FolderOpen size={13} /> Open folder
            </button>
            <button
              className="btn sm ghost danger"
              onClick={() =>
                confirmAction({
                  title: 'Remove this worktree?',
                  body: `Deletes ${session.worktree!.path} and the branch ${session.worktree!.branch}. Uncommitted work there is lost.`,
                  confirm: 'Remove worktree',
                  danger: true,
                  onConfirm: () => call('git.removeWorktree', { repoRoot: session.worktree!.repoRoot, dir: session.worktree!.path, branch: session.worktree!.branch, deleteBranch: true }).then(() => toast('Worktree removed', 'success')),
                })
              }
            >
              <Trash size={13} /> Remove
            </button>
          </div>
        </section>
      )}

      {info?.gateway?.baseUrl && (
        <section className="ps">
          <div className="ps-head">
            <Globe size={14} /> API endpoint
          </div>
          <div className="kv">
            <span>Base URL</span>
            <span className="mono ellipsis" title={info.gateway.baseUrl}>
              {info.gateway.baseUrl}
            </span>
            <span>Credential</span>
            <span>{info.gateway.hasToken ? 'Configured' : 'Not found'}</span>
          </div>
        </section>
      )}
    </div>
  );
}

const CAT_COLORS: Record<string, string> = {
  'System prompt': 'var(--text-3)',
  'System tools': 'var(--info)',
  'MCP tools': 'var(--plan)',
  'Memory files': 'var(--ok)',
  Messages: 'var(--accent)',
  'Custom agents': '#c9a2f2',
  Skills: '#e6c08a',
};

function catColor(c: { name: string; kind: string }) {
  if (c.kind === 'free') return 'transparent';
  if (c.kind === 'buffer') return 'var(--line-2)';
  return CAT_COLORS[c.name] || 'var(--text-2)';
}

/** The notes this project carries, and a way to add this session's lessons to them now. */
function MemorySection({ session }: { session: SessionSummary }) {
  const rev = useStore((s) => s.schedRev);
  const cwd = session.project || session.cwd;
  const [notes, setNotes] = useState<MemoryNote[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    call<{ notes: MemoryNote[] }>('memory.list', { cwd }, { quiet: true })
      .then((r) => alive && setNotes(r.notes))
      .catch(() => alive && setNotes([]));
    return () => {
      alive = false;
    };
  }, [cwd, rev]);
  const distill = async () => {
    setBusy(true);
    try {
      const r = await call<{ added: MemoryNote[] }>('memory.distill', { id: session.id, force: true });
      toast(r.added.length ? `Saved ${r.added.length} note${r.added.length > 1 ? 's' : ''} for ${session.project ? session.project.split('/').pop() : 'this folder'}` : 'Nothing new worth keeping yet', r.added.length ? 'success' : 'info');
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };
  const mine = (notes || []).filter((n) => n.sessionId === session.id).length;
  return (
    <section className="ps">
      <div className="ps-head">
        <BookMarked size={14} /> Project memory
        <div className="grow" />
        <button className="link ps-link" onClick={() => setDialog('settings', 'memory')}>
          {notes?.length ? `${notes.length} note${notes.length > 1 ? 's' : ''}` : 'Manage'}
        </button>
      </div>
      {notes === null ? (
        <Spinner size={13} />
      ) : notes.length ? (
        <div className="mem-peek">
          {notes.slice(0, 3).map((n) => (
            <div key={n.id} className="mem-peek-row">
              <span className="mem-peek-dot" />
              <span className="ellipsis-2">{n.text}</span>
            </div>
          ))}
          {notes.length > 3 && <div className="faint">and {notes.length - 3} more</div>}
        </div>
      ) : (
        <div className="ps-empty">Nothing remembered about this folder yet. Notes are taken at the end of a turn, or you can save this session's lessons now.</div>
      )}
      <div className="row">
        <button className="btn xs" onClick={distill} disabled={busy} title="Distil what this session learned into notes for future sessions">
          {busy ? <Spinner size={11} /> : <Sparkles size={11} />} Save what we learned
        </button>
        {mine > 0 && <span className="faint">{mine} from this session</span>}
      </div>
    </section>
  );
}
