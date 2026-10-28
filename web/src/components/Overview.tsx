import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Bot, Brain, Wrench, MessageSquareQuote, Workflow, PanelLeftOpen, Hourglass, CircleCheck, CircleAlert, Activity, CircleDollarSign } from 'lucide-react';
import { useStore, call, openSession, respond, setUi } from '../lib/store';
import type { PermissionRequest, SessionState, SessionSummary, TaskInfo } from '../lib/types';
import { basename, cost, duration, timeAgo, relPath, plural } from '../lib/format';
import { ContextRing, Spinner, useNow, Num, WorkingMark, Mark } from './ui/bits';
import { suggestionLabel, typingElsewhere } from './tools/Permission';
import { workflowCounts } from '../lib/workflow';
import { str } from '../lib/tools';
import type { PermissionDecision } from '../lib/types';

interface Fleet {
  live: Record<string, SessionState>;
  today: { cost: number; sessions: number; perSession: { id: string; title: string; cost: number }[] };
  spend: { at: number; id: string; delta: number }[];
}

const order = (i: number) => ({ '--i': i }) as React.CSSProperties;

/**
 * The control room: every session that is working or waiting, at a glance, with the things that need
 * you answerable right here. Four tiles say the state of the fleet; under them, what needs you first,
 * then the work in flight, then what finished today and what it cost. Cards update live; nothing here
 * needs the transcript loaded.
 */
export function Overview() {
  const sessions = useStore((s) => s.sessions);
  const rt = useStore((s) => s.rt);
  const unread = useStore((s) => s.unread);
  const sidebar = useStore((s) => s.ui.sidebar);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const now = useNow(1000, true);
  const [focus, setFocus] = useState(-1); // keyboard focus; none until the keys are used
  const box = useRef<HTMLDivElement>(null);

  // Live state for every running session, then the push stream keeps it fresh.
  useEffect(() => {
    let alive = true;
    const load = () =>
      call<Fleet>('fleet.state', {}, { quiet: true })
        .then((f) => {
          if (!alive) return;
          setFleet(f);
          const cur = useStore.getState().rt;
          const next = { ...cur };
          for (const [id, st] of Object.entries(f.live)) next[id] = { ...(cur[id] || { rev: 0, loaded: false, loading: false }), ...st };
          useStore.setState({ rt: next });
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 20_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const byId = useMemo(() => Object.fromEntries(sessions.map((s) => [s.id, s])), [sessions]);
  const st = (id: string) => rt[id] || fleet?.live[id];
  const needs = sessions.filter((s) => (st(s.id)?.pending?.length || 0) > 0 || s.status === 'requires_action');
  const working = sessions.filter((s) => !needs.includes(s) && (s.status === 'running' || s.status === 'starting'));
  const dayStart = new Date(now);
  dayStart.setHours(0, 0, 0, 0);
  const finished = sessions
    .filter((s) => !needs.includes(s) && !working.includes(s) && !s.archived && (s.lastModified || 0) >= dayStart.getTime())
    .sort((a, b) => Number(Boolean(unread[b.id])) - Number(Boolean(unread[a.id])) || (b.lastModified || 0) - (a.lastModified || 0))
    .slice(0, 12);
  const ordered = useMemo(() => [...needs, ...working, ...finished], [needs, working, finished]);

  // j/k or arrows move between cards; Enter opens; the first waiting card also takes 1/2/3.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typingElsewhere(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'ArrowDown') setFocus((f) => Math.min(ordered.length - 1, f + 1));
      else if (e.key === 'Escape' && focus >= 0) setFocus(-1);
      else if (e.key === 'k' || e.key === 'ArrowUp') setFocus((f) => Math.max(0, f - 1));
      else if (e.key === 'Enter' && focus >= 0 && ordered[focus]) openSession(ordered[focus].id);
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ordered, focus]);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>(`[data-card="${ordered[focus]?.id}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [focus]); // eslint-disable-line react-hooks/exhaustive-deps

  const totalCost = fleet?.today.cost || 0;
  const median = useMemo(() => {
    const xs = (fleet?.today.perSession || []).map((p) => p.cost).sort((a, b) => a - b);
    return xs.length ? xs[Math.floor(xs.length / 2)] : 0;
  }, [fleet]);
  const jump = (id: string) => () => box.current?.querySelector(`#${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  let k = 0;

  return (
    <div className="overview" ref={box}>
      <header className="ov-head">
        {!sidebar && (
          <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
            <PanelLeftOpen size={16} />
          </button>
        )}
        <h1 className="ov-title">Control room</h1>
        <span className="ov-sum tnum">
          {working.length ? `${working.length} working` : 'Nothing running'}
          {needs.length ? ` · ${needs.length} waiting for you` : ''}
        </span>
      </header>

      <div className="ov-body">
        <div className="ov-stats" style={order(k++)}>
          <Tile icon={working.length ? <WorkingMark size={18} /> : <Activity size={16} />} label="Working" value={working.length} tone={working.length ? 'live' : ''} onClick={jump('ov-working')} />
          <Tile icon={<Hourglass size={16} />} label="Needs you" value={needs.length} tone={needs.length ? 'accent' : ''} onClick={jump(needs.length ? 'ov-needs' : 'ov-working')} />
          <Tile icon={<CircleCheck size={16} />} label="Finished today" value={finished.length} onClick={jump('ov-finished')} />
          <Tile icon={<CircleDollarSign size={16} />} label="Spent today" text={<Num value={totalCost} format={cost} />} sub={fleet ? plural(fleet.today.sessions, 'session') : ''} onClick={jump('ov-spend')} />
        </div>

        {needs.length > 0 && (
          <section id="ov-needs" className="ov-sec" style={order(k++)}>
            <h2 className="ov-h">
              <span className="glyph needs" aria-hidden="true" /> Needs you <span className="ov-count">{needs.length}</span>
              <span className="ov-h-end faint">Press 1, 2 or 3 to answer the first</span>
            </h2>
            <div className="ov-queue">
              {needs.map((s, i) => (
                <NeedsCard key={s.id} s={s} st={st(s.id)} first={i === 0} focused={ordered[focus]?.id === s.id} now={now} />
              ))}
            </div>
          </section>
        )}

        <section id="ov-working" className="ov-sec" style={order(k++)}>
          <h2 className="ov-h">
            Working <span className="ov-count">{working.length}</span>
          </h2>
          {working.length ? (
            <div className="ov-grid">
              {working.map((s, i) => (
                <WorkingCard key={s.id} s={s} st={st(s.id)} focused={ordered[focus]?.id === s.id} now={now} index={i} hot={median > 0 && (fleet?.today.perSession.find((p) => p.id === s.id)?.cost || 0) > median * 3 && (fleet?.today.perSession.length || 0) >= 4} />
              ))}
            </div>
          ) : (
            <div className="ov-empty">
              <Mark size={18} tight />
              <span>Nothing is running right now. Sessions you start show up here while Claude works, with what it is doing and what it costs.</span>
            </div>
          )}
        </section>

        <div className="ov-two" style={order(k++)}>
          <section id="ov-finished" className="ov-sec">
            <h2 className="ov-h">
              Finished today <span className="ov-count">{finished.length}</span>
            </h2>
            {finished.length ? (
              <div className="ov-list">
                {finished.map((s) => (
                  <FinishedRow key={s.id} s={s} st={st(s.id)} unread={Boolean(unread[s.id])} focused={ordered[focus]?.id === s.id} now={now} />
                ))}
              </div>
            ) : (
              <div className="ov-empty">
                <CircleCheck size={16} />
                <span>No sessions have finished today.</span>
              </div>
            )}
          </section>

          <section id="ov-spend" className="ov-sec">
            <h2 className="ov-h">
              Spend
              {fleet && (
                <span className="ov-h-end tnum">
                  <Num value={totalCost} format={cost} /> today
                </span>
              )}
            </h2>
            {fleet ? <Spend fleet={fleet} byId={byId} now={now} /> : <Spinner size={14} />}
          </section>
        </div>
      </div>
    </div>
  );
}

/** One figure about the fleet. Clicking it scrolls to the matching section. */
function Tile({ icon, label, value = 0, text, sub, tone = '', onClick }: { icon: ReactNode; label: string; value?: number; text?: ReactNode; sub?: string; tone?: string; onClick: () => void }) {
  return (
    <button className={`ov-tile${tone ? ` ${tone}` : ''}`} onClick={onClick}>
      <span className="ov-tile-ico">{icon}</span>
      <span className="ov-tile-main">
        <span className="ov-tile-v tnum">{text ?? <Num value={value} format={(v) => String(Math.round(v))} />}</span>
        <span className="ov-tile-k">
          {label}
          {sub ? <span className="ov-tile-sub"> · {sub}</span> : null}
        </span>
      </span>
    </button>
  );
}

function activityLine(st?: Partial<SessionState>) {
  const a = st?.activity;
  if (!a) return { icon: Hourglass, text: st?.status === 'starting' ? 'Starting Claude Code…' : 'Waiting for the model…', quiet: true };
  if (a.kind === 'tool') return { icon: Wrench, text: `${a.tool} · ${a.label}`, quiet: false };
  if (a.kind === 'text') return { icon: MessageSquareQuote, text: a.label, quiet: false };
  return { icon: Brain, text: 'Thinking…', quiet: true };
}

function runningWorkflow(tasks?: TaskInfo[]) {
  return (tasks || []).find((t) => t.type === 'local_workflow' && t.status === 'running' && !t.ambient);
}

function CardHead({ s, st }: { s: SessionSummary; st?: Partial<SessionState> }) {
  return (
    <div className="ovc-head">
      <span className="ovc-title ellipsis">{s.title}</span>
      <span className="ovc-proj ellipsis">
        {basename(s.project || s.cwd)}
        {s.worktree ? ` · ${s.worktree.branch}` : s.gitBranch ? ` · ${s.gitBranch}` : ''}
        {st?.activeModel ? ` · ${st.activeModel.replace(/^claude-/, '').replace(/-\d{8}$/, '')}` : ''}
      </span>
    </div>
  );
}

function WorkingCard({ s, st, focused, now, hot, index }: { s: SessionSummary; st?: Partial<SessionState>; focused: boolean; now: number; hot: boolean; index: number }) {
  const act = activityLine(st);
  const wf = runningWorkflow(st?.tasks);
  const counts = wf ? workflowCounts(wf.workflow?.agents || []) : null;
  const agents = (st?.tasks || []).filter((t) => t.status === 'running' && t.type === 'local_agent').length;
  const elapsed = st?.turnStartedAt ? now - st.turnStartedAt : 0;
  const u = st?.usage;
  const stale = st?.lastActivity ? now - st.lastActivity > 45_000 : false;
  return (
    <article className={`ovc busy${focused ? ' focused' : ''}${stale ? ' stale' : ''}`} data-card={s.id} onClick={() => openSession(s.id)} role="button" tabIndex={0} style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}>
      <div className="ovc-row">
        <CardHead s={s} st={st} />
        <span className="ovc-live" title={stale ? 'Quiet for a while' : 'Working'}>
          <WorkingMark size={15} />
        </span>
      </div>
      <div className={`ovc-act${act.quiet ? ' quiet' : ''}`}>
        <act.icon size={13} />
        <span className={`ellipsis${act.quiet || stale ? '' : ' shimmer'}`}>{act.text}</span>
      </div>
      {wf && counts && (
        <div className="ovc-wf">
          <Workflow size={12} />
          <span className="ellipsis">{wf.workflowName || 'Workflow'}</span>
          <span className="wf-bar ovc-bar" aria-hidden="true">
            <span className="wf-bar-done" style={{ width: `${(counts.done / Math.max(1, counts.total)) * 100}%` }} />
            <span className="wf-bar-run" style={{ width: `${(counts.running / Math.max(1, counts.total)) * 100}%` }} />
            {counts.error > 0 && <span className="wf-bar-err" style={{ width: `${(counts.error / Math.max(1, counts.total)) * 100}%` }} />}
          </span>
          <span className="tnum faint">
            {counts.done}/{counts.total}
          </span>
        </div>
      )}
      <div className="ovc-foot tnum">
        {elapsed > 1000 && <span title="This turn so far">{duration(elapsed)}</span>}
        {agents > 0 && (
          <span title="Sub-agents running">
            <Bot size={12} /> {agents}
          </span>
        )}
        {u && u.costUsd > 0 && (
          <span title={hot ? 'Well above the median session today' : 'Cost so far'}>
            <Num value={u.costUsd} format={cost} />
            {hot ? <span className="ovc-hot">above median</span> : null}
          </span>
        )}
        <span className="grow" />
        {stale && <span className="faint">quiet for {duration(now - (st?.lastActivity || now))}</span>}
        {u && u.contextWindow > 0 && (
          <span title={`Context ${Math.round((u.contextTokens / u.contextWindow) * 100)}% used`}>
            <ContextRing used={u.contextTokens} total={u.contextWindow} size={14} />
          </span>
        )}
      </div>
    </article>
  );
}

function NeedsCard({ s, st, first, focused, now }: { s: SessionSummary; st?: Partial<SessionState>; first: boolean; focused: boolean; now: number }) {
  const perm = st?.pending?.[0];
  const more = (st?.pending?.length || 1) - 1;
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState<'allow' | 'deny' | null>(null);
  const allowAlways = perm ? !perm.suppressAlwaysAllowRule && perm.suggestions.length > 0 : false;
  const decide = async (decision: PermissionDecision) => {
    if (!perm || busy) return;
    setBusy(true);
    try {
      await respond(s.id, perm.requestId, decision);
      setDecided(decision.behavior === 'allow' ? 'allow' : 'deny');
      setTimeout(() => (setDecided(null), setBusy(false)), 900);
    } catch {
      setBusy(false);
    }
  };
  const allow = () => decide({ behavior: 'allow', decisionClassification: 'user_temporary' });
  const always = () => decide({ behavior: 'allow', updatedPermissions: perm!.suggestions, decisionClassification: 'user_permanent' });
  const deny = () => decide({ behavior: 'deny', message: 'The user declined this action.', decisionClassification: 'user_reject' });
  useEffect(() => {
    if (!first || !perm || perm.interactive) return;
    const onKey = (e: KeyboardEvent) => {
      if (typingElsewhere(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '1' && !perm.defaultToNo) allow();
      else if (e.key === '2' && allowAlways) always();
      else if (e.key === (allowAlways ? '3' : '2')) deny();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  const what = perm ? permSummary(perm, s.cwd) : null;
  const key = (n: number) => (first && perm && !perm.interactive ? <span className="key">{n}</span> : null);
  return (
    <article className={`ovc needs${focused ? ' focused' : ''}${decided ? ' decided' : ''}`} data-card={s.id}>
      <span className="ovc-rail" aria-hidden="true" />
      <div className="ovc-row">
        <CardHead s={s} st={st} />
        <span className="ovc-wait tnum">{perm ? `waiting ${duration(now - perm.at)}` : ''}</span>
        <button className="icon-btn sm" title="Open the session" onClick={() => openSession(s.id)}>
          <ArrowUpRight size={14} />
        </button>
      </div>
      {perm ? (
        <>
          <div className="ovc-well">
            <span className="ovc-perm-tool">{perm.displayName || perm.toolName}</span>
            <span className={`ovc-perm-what${what?.mono ? ' mono' : ''}`}>{what?.text}</span>
          </div>
          {what?.note && <div className="ovc-perm-note">{what.note}</div>}
          {decided ? (
            <div className="ovc-actions ovc-decided">
              <CircleCheck size={14} /> {decided === 'allow' ? 'Allowed — Claude is continuing' : 'Declined'}
            </div>
          ) : perm.interactive ? (
            <div className="ovc-actions">
              <button className="btn sm primary" onClick={() => openSession(s.id)}>
                {perm.toolName === 'AskUserQuestion' ? 'Answer the question' : perm.toolName === 'ExitPlanMode' ? 'Review the plan' : 'Open to respond'}
              </button>
            </div>
          ) : (
            <div className="ovc-actions">
              <button className={`btn sm ${perm.defaultToNo ? '' : 'primary'}`} onClick={allow} disabled={busy}>
                Allow{!perm.defaultToNo && key(1)}
              </button>
              {allowAlways && (
                <button className="btn sm" onClick={always} disabled={busy} title={suggestionLabel(perm.suggestions[0], s.cwd)}>
                  Always allow{key(2)}
                </button>
              )}
              <button className={`btn sm ${perm.defaultToNo ? 'primary' : 'ghost'}`} onClick={deny} disabled={busy}>
                Deny{key(allowAlways ? 3 : 2)}
              </button>
              {more > 0 && <span className="faint">+{more} more waiting</span>}
            </div>
          )}
        </>
      ) : (
        <div className="ovc-actions">
          <span className="faint">Waiting for your input.</span>
          <button className="btn sm" onClick={() => openSession(s.id)}>
            Open
          </button>
        </div>
      )}
    </article>
  );
}

function permSummary(p: PermissionRequest, cwd: string): { text: string; mono: boolean; note?: string } {
  const i = p.input || {};
  switch (p.toolName) {
    case 'Bash':
    case 'PowerShell':
      return { text: str(i, 'command'), mono: true, note: str(i, 'description') || undefined };
    case 'Edit':
    case 'MultiEdit':
    case 'Write':
    case 'NotebookEdit':
      return { text: relPath(str(i, 'file_path') || str(i, 'notebook_path'), cwd), mono: true };
    case 'WebFetch':
      return { text: str(i, 'url'), mono: true };
    case 'AskUserQuestion':
      return { text: i.questions?.[0]?.question || 'Claude has a question', mono: false };
    case 'ExitPlanMode':
      return { text: 'A plan is ready for review', mono: false };
    default:
      return { text: p.description || p.title || '', mono: false };
  }
}

function FinishedRow({ s, st, unread, focused, now }: { s: SessionSummary; st?: Partial<SessionState>; unread: boolean; focused: boolean; now: number }) {
  const reply = st?.lastReply?.text;
  const c = st?.usage?.costUsd;
  return (
    <button className={`ovr${focused ? ' focused' : ''}${unread ? ' unread' : ''}`} data-card={s.id} onClick={() => openSession(s.id)}>
      <span className="ovr-mark">{s.status === 'error' ? <CircleAlert size={14} className="st-err" /> : unread ? <span className="glyph unread" /> : <CircleCheck size={14} className="ovr-ok" />}</span>
      <span className="ovr-main">
        <span className="ovr-title ellipsis">{s.title}</span>
        <span className="ovr-sub ellipsis">{reply ? reply : `${basename(s.project || s.cwd)}`}</span>
      </span>
      <span className="ovr-end tnum">
        {c ? <span>{cost(c)}</span> : null}
        <span>{timeAgo(s.lastModified, now)}</span>
      </span>
    </button>
  );
}

/** Spend in the last hour, five-minute bars (the current one in Claude's colour), and the sessions that cost the most today. */
function Spend({ fleet, byId, now }: { fleet: Fleet; byId: Record<string, SessionSummary>; now: number }) {
  const buckets = useMemo(() => {
    const n = 12;
    const w = 5 * 60e3;
    const start = now - n * w;
    const out = new Array(n).fill(0);
    for (const e of fleet.spend) {
      const i = Math.floor((e.at - start) / w);
      if (i >= 0 && i < n) out[i] += e.delta;
    }
    return out;
  }, [fleet.spend, Math.floor(now / 60e3)]); // eslint-disable-line react-hooks/exhaustive-deps
  const hour = buckets.reduce((a, b) => a + b, 0);
  const max = Math.max(...buckets, 0.0001);
  const top = fleet.today.perSession.slice(0, 5);
  const topMax = top[0]?.cost || 1;
  return (
    <div className="spend">
      <div className="spend-hour">
        <span className="spend-hour-label">
          Last hour{' '}
          <strong className="tnum">
            <Num value={hour} format={cost} />
          </strong>
        </span>
        <svg className="spend-bars" viewBox="0 0 120 30" preserveAspectRatio="none" aria-hidden="true">
          {buckets.map((v, i) => {
            const h = Math.max(1.5, (v / max) * 26);
            return <rect key={i} x={i * 10 + 1} y={28 - h} width={8} height={h} rx={1.5} className={`${v > 0 ? 'on' : ''}${i === buckets.length - 1 ? ' now' : ''}`} />;
          })}
          <rect x={0} y={29.2} width={120} height={0.8} className="base" />
        </svg>
        <span className="spend-axis tnum">
          <span>60 min ago</span>
          <span>now</span>
        </span>
      </div>
      {top.length > 0 && (
        <div className="spend-top">
          {top.map((p) => (
            <button key={p.id} className="spend-row" onClick={() => openSession(p.id)} title="Open the session">
              <span className="ellipsis">{byId[p.id]?.title || p.title}</span>
              <span className="spend-bar" aria-hidden="true">
                <span style={{ width: `${(p.cost / topMax) * 100}%` }} />
              </span>
              <span className="tnum">{cost(p.cost)}</span>
            </button>
          ))}
        </div>
      )}
      {!top.length && <div className="faint">No spend recorded today.</div>}
    </div>
  );
}
