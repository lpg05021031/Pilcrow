import { useMemo, useState } from 'react';
import { Workflow, Check, X, Ban, RotateCcw, ChevronRight, CircleStop, Bot, GitBranch, Cloud, PanelRight } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import type { TaskInfo, WorkflowAgent } from '../../lib/types';
import { useStore, call, toast, setUi, models } from '../../lib/store';
import { duration, shortModel, tokens, truncate } from '../../lib/format';
import { parseWorkflowMeta, workflowColumns, workflowCounts, agentState, type AgentState } from '../../lib/workflow';
import { Markdown } from '../Markdown';
import { Spinner, useNow } from '../ui/bits';
import { Collapse } from '../ui/Collapse';
import type { ToolCtx } from './ToolCard';
import { str } from '../../lib/tools';
import type { LucideIcon } from 'lucide-react';

function useWorkflowTask(sessionId: string, toolUseId: string) {
  return useStore((s) => (s.rt[sessionId]?.tasks || []).find((t) => t.toolUseId === toolUseId || (t.type === 'local_workflow' && t.status === 'running' && !t.toolUseId)));
}

/**
 * In the conversation a workflow is one quiet row like any other tool; its body is a sentence and a
 * way into the Tasks panel, where the whole run is drawn.
 */
export function WorkflowInline({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const task = useWorkflowTask(ctx.sessionId, item.id);
  const script = str(item.input, 'script');
  const meta = useMemo(() => parseWorkflowMeta(script), [script]);
  const counts = workflowCounts(task?.workflow?.agents || []);
  const phases = Math.max(meta.phases.length, (task?.workflow?.phases || []).filter(Boolean).length);
  const description = meta.description || str(item.input, 'description');
  const [showResult, setShowResult] = useState(false);
  return (
    <div className="wfi">
      {description && <div className="wfi-desc">{description}</div>}
      <div className="wfi-row">
        <span className="faint tnum">
          {phases ? `${phases} phase${phases > 1 ? 's' : ''}` : ''}
          {counts.total ? ` · ${counts.done} of ${counts.total} agents done` : ''}
          {counts.running ? ` · ${counts.running} running` : ''}
          {counts.error ? ` · ${counts.error} failed` : ''}
        </span>
        <button className="btn xs" onClick={() => setUi({ panel: true, panelTab: 'tasks' })}>
          <PanelRight size={12} /> Open in Tasks
        </button>
      </div>
      {item.result?.text && (
        <>
          <button className="wf-foot-btn" onClick={() => setShowResult(!showResult)} aria-expanded={showResult}>
            <ChevronRight size={12} className={`tool-chev${showResult ? ' open' : ''}`} /> {item.result.isError ? 'Error' : 'Result'}
          </button>
          <Collapse open={showResult}>
            {() => (
              <div className="wf-result">
                <Markdown text={truncate(item.result?.text || '', 30000)} cwd={ctx.cwd} className="md-compact" />
              </div>
            )}
          </Collapse>
        </>
      )}
    </div>
  );
}

/** The row's right-hand text: how far the run is, while it runs and after. */
export function WorkflowMeta({ sessionId, toolUseId }: { sessionId: string; toolUseId: string }) {
  const task = useWorkflowTask(sessionId, toolUseId);
  const counts = workflowCounts(task?.workflow?.agents || []);
  if (!task || !counts.total) return null;
  return (
    <span className="tool-time tnum" title={`${counts.done} done · ${counts.running} running · ${counts.queued} queued${counts.error ? ` · ${counts.error} failed` : ''}`}>
      {counts.done}/{counts.total} agents
    </span>
  );
}

const STATE_META: Record<AgentState, { label: string; icon: LucideIcon | null }> = {
  queued: { label: 'Queued', icon: null },
  running: { label: 'Running', icon: null },
  done: { label: 'Done', icon: Check },
  cached: { label: 'Reused', icon: RotateCcw },
  error: { label: 'Failed', icon: X },
  blocked: { label: 'Blocked', icon: Ban },
};

function StateMark({ st }: { st: AgentState }) {
  const Icon = STATE_META[st].icon;
  return (
    <span className={`wf-mark ${st}`} aria-label={STATE_META[st].label}>
      {st === 'running' ? <Spinner size={12} /> : st === 'queued' ? <span className="wf-dot" /> : Icon ? <Icon size={12} /> : null}
    </span>
  );
}

function AgentRow({ a, now, subs }: { a: WorkflowAgent; now: number; subs: TaskInfo[] }) {
  const [open, setOpen] = useState(false);
  const st = agentState(a);
  const ran = a.startedAt ? (st === 'running' ? now : a.lastProgressAt || a.updatedAt || now) - a.startedAt : 0;
  const what = st === 'running' ? a.lastToolSummary || (a.lastToolName ? `Using ${a.lastToolName}` : 'Thinking…') : st === 'error' || st === 'blocked' ? a.error?.split('\n')[0] : st === 'queued' ? 'Waiting for a slot' : a.resultPreview?.split('\n')[0];
  return (
    <div className={`wfa ${st}${open ? ' open' : ''}`}>
      <button className="wfa-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <StateMark st={st} />
        <span className="wfa-main">
          <span className="wfa-top">
            <span className="wfa-label ellipsis">{a.label || `agent ${a.index + 1}`}</span>
            {a.attempt && a.attempt > 1 ? (
              <span className="wf-chip" title={a.lastAttemptReason ? `Retried: ${a.lastAttemptReason}` : 'Retried'}>
                ×{a.attempt}
              </span>
            ) : null}
            {a.isolation === 'worktree' && <GitBranch size={10} className="wf-iso" />}
            {a.isolation === 'remote' && <Cloud size={10} className="wf-iso" />}
            <span className="wfa-end tnum">
              {a.model && <span>{shortModel(a.model)}</span>}
              {ran > 1500 && <span>{duration(ran)}</span>}
            </span>
          </span>
          {what && <span className="wfa-detail ellipsis">{what}</span>}
        </span>
      </button>
      <Collapse open={open}>
        {() => (
          <div className="wfa-body">
            {a.agentType && (
              <div className="wf-kv">
                <Bot size={11} /> {a.agentType}
                {a.agentId ? <span className="faint mono"> · {a.agentId.slice(0, 8)}</span> : null}
              </div>
            )}
            {a.promptPreview && (
              <div className="wf-block">
                <div className="tool-label">Prompt</div>
                <div className="wf-pre">{a.promptPreview}</div>
              </div>
            )}
            {(a.resultPreview || a.error) && (
              <div className="wf-block">
                <div className="tool-label">{a.error ? 'Error' : 'Result'}</div>
                <div className={`wf-pre${a.error ? ' err' : ''}`}>{a.error || a.resultPreview}</div>
              </div>
            )}
          </div>
        )}
      </Collapse>
      {subs.length > 0 && (
        <div className="wfa-subs">
          {subs.map((t) => (
            <SubAgentRow key={t.id} t={t} now={now} />
          ))}
        </div>
      )}
    </div>
  );
}

function SubAgentRow({ t, now }: { t: TaskInfo; now: number }) {
  const st: AgentState = t.status === 'running' ? 'running' : t.status === 'completed' ? 'done' : 'error';
  const ran = t.startedAt ? (t.endedAt || now) - t.startedAt : 0;
  return (
    <div className={`wfs ${st}`}>
      <span className="wfs-elbow" aria-hidden="true" />
      <StateMark st={st} />
      <span className="wfs-label ellipsis" title={t.description}>
        {t.description || t.subagentType || 'sub-agent'}
      </span>
      <span className="wfs-end tnum">
        {t.subagentType && t.subagentType !== 'general-purpose' && <span>{t.subagentType}</span>}
        {t.usage ? <span>{tokens(t.usage.total_tokens)}</span> : null}
        {ran > 1500 && <span>{duration(ran)}</span>}
      </span>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = { completed: 'Completed', failed: 'Failed', stopped: 'Stopped', running: 'Running' };

/** The whole run, for the Tasks panel: phases down the page, each with its agents and the sub-agents they spawned. */
export function WorkflowDetail({ task, sessionId, allTasks }: { task: TaskInfo; sessionId: string; allTasks: TaskInfo[] }) {
  const rev = useStore((s) => s.rt[sessionId]?.rev);
  const script = useMemo(() => {
    void rev;
    return task.toolUseId ? (models.get(sessionId)?.tools.get(task.toolUseId)?.input?.script as string | undefined) : undefined;
  }, [sessionId, task.toolUseId, rev]);
  const meta = useMemo(() => parseWorkflowMeta(script), [script]);
  const cols = useMemo(() => workflowColumns(task, meta), [task, meta]);
  const counts = workflowCounts(task.workflow?.agents || []);
  const live = task.status === 'running';
  const now = useNow(1000, live);
  const elapsed = task.startedAt ? (task.endedAt || now) - task.startedAt : 0;
  const failed = task.status === 'failed';
  const stopped = task.status === 'stopped';
  const [collapsed, setCollapsed] = useState(!live);
  const [showLog, setShowLog] = useState(false);
  const [busy, setBusy] = useState(false);
  const logs = task.workflow?.logs || [];
  // Sub-agents the workflow's agents spawned. Claude Code does not say which agent launched them, so
  // each one hangs off the agent that was running when it started.
  const subs = useMemo(() => allTasks.filter((t) => t.type === 'local_agent' && t.ownedBySubagent && t.startedAt && task.startedAt && t.startedAt >= task.startedAt && (!task.endedAt || t.startedAt <= task.endedAt + 2000)), [allTasks, task.startedAt, task.endedAt]);
  const subsByAgent = useMemo(() => {
    const m = new Map<number, TaskInfo[]>();
    for (const t of subs) {
      const owner = (task.workflow?.agents || []).filter((a): a is WorkflowAgent => Boolean(a && a.startedAt && t.startedAt! >= a.startedAt && (agentState(a) === 'running' || !a.lastProgressAt || t.startedAt! <= a.lastProgressAt + 2000))).sort((x, y) => (y.startedAt || 0) - (x.startedAt || 0))[0];
      const key = owner ? owner.index : -1;
      m.set(key, [...(m.get(key) || []), t]);
    }
    return m;
  }, [subs, task.workflow?.agents]);

  const stop = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await call('session.stopTask', { id: sessionId, taskId: task.id });
      toast(`Stopping ${task.workflowName || 'the workflow'}`, 'info');
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };
  const activeIdx = cols.findIndex((c) => c.agents.some((a) => ['running', 'queued'].includes(agentState(a))));

  return (
    <div className={`wfd ${task.status}${collapsed ? ' collapsed' : ''}`}>
      <button className="wfd-head" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed}>
        <span className={`wfd-ico ${stopped || failed ? 'err' : live ? 'live' : 'ok'}`}>
          <Workflow size={14} />
        </span>
        <span className="wfd-title">
          <span className="wfd-name ellipsis">{task.workflowName || task.description || 'Workflow'}</span>
          <span className="wfd-sub faint tnum">
            {counts.total ? `${counts.done} of ${counts.total} agents done` : live ? 'Starting' : STATUS_LABEL[task.status] || task.status}
            {counts.running ? ` · ${counts.running} running` : ''}
            {counts.error ? ` · ${counts.error} failed` : ''}
            {elapsed > 0 ? ` · ${duration(elapsed)}` : ''}
          </span>
        </span>
        <span className="wfd-end">
          {live ? <Spinner size={12} /> : failed || stopped ? <X size={13} className="st-err" /> : <Check size={13} className="st-ok" />}
          <ChevronRight size={13} className={`tool-chev${collapsed ? '' : ' open'}`} />
        </span>
      </button>
      {counts.total > 0 && (
        <div className="wf-bar" aria-hidden="true">
          <div className="wf-bar-done" style={{ width: `${(counts.done / counts.total) * 100}%` }} />
          <div className="wf-bar-run" style={{ width: `${(counts.running / counts.total) * 100}%` }} />
          {counts.error > 0 && <div className="wf-bar-err" style={{ width: `${(counts.error / counts.total) * 100}%` }} />}
        </div>
      )}
      <Collapse open={!collapsed}>
        {() => (
          <div className="wfd-body">
            {cols.length === 0 && <div className="wfd-empty faint">{live ? 'Waiting for the first agent…' : 'No agents were reported for this run.'}</div>}
            <ol className="wfd-phases">
              {cols.map((c, i) => {
                const states = c.agents.map(agentState);
                const state = c.agents.length === 0 ? (activeIdx !== -1 && i < activeIdx ? 'done' : !live && counts.total > 0 && activeIdx === -1 ? 'done' : 'pending') : states.every((s) => s === 'done' || s === 'cached') ? 'done' : states.some((s) => s === 'running') ? 'running' : states.some((s) => s === 'error' || s === 'blocked') && !states.some((s) => s === 'queued') ? 'error' : states.some((s) => s === 'queued') && activeIdx === i ? 'running' : 'pending';
                return (
                  <li key={c.phase.index} className={`wfd-phase ${state}`}>
                    <span className="wfd-node tnum" aria-hidden="true">
                      {state === 'done' ? <Check size={11} /> : state === 'error' ? <X size={11} /> : c.phase.index + 1}
                    </span>
                    <div className="wfd-phase-head">
                      <span className="wfd-phase-title ellipsis">{c.phase.title}</span>
                      {c.phase.kind && c.phase.kind !== 'sequential' && <span className="wf-phase-kind">{c.phase.kind}</span>}
                      {state !== 'done' && state !== 'error' && <span className={`wfd-phase-state ${state}`}>{state === 'running' ? (states.filter((s) => s === 'error' || s === 'blocked').length ? `running · ${states.filter((s) => s === 'error' || s === 'blocked').length} failed` : 'running') : i === activeIdx ? 'queued' : 'pending'}</span>}
                    </div>
                    <div className="wfd-agents">
                      {c.agents.map((a) => (
                        <AgentRow key={a.index} a={a} now={now} subs={subsByAgent.get(a.index) || []} />
                      ))}
                      {c.agents.length === 0 && <div className="wfa ghost">{state === 'done' ? 'No agents' : 'Not started yet'}</div>}
                    </div>
                  </li>
                );
              })}
            </ol>
            {(subsByAgent.get(-1) || []).length > 0 && (
              <div className="wfd-orphans">
                <div className="tool-label">Sub-agents</div>
                {(subsByAgent.get(-1) || []).map((t) => (
                  <SubAgentRow key={t.id} t={t} now={now} />
                ))}
              </div>
            )}
            <div className="wfd-foot">
              {logs.length > 0 && (
                <button className="wf-foot-btn" onClick={() => setShowLog(!showLog)} aria-expanded={showLog}>
                  <ChevronRight size={12} className={`tool-chev${showLog ? ' open' : ''}`} /> Log · {logs.length}
                </button>
              )}
              {task.usage && task.usage.total_tokens > 0 && <span className="faint tnum wf-usage">{tokens(task.usage.total_tokens)} tokens</span>}
              {live && (
                <button className="btn xs ghost danger" disabled={busy} onClick={stop} title="Stop this workflow">
                  <CircleStop size={12} /> Stop
                </button>
              )}
            </div>
            <Collapse open={showLog}>
              {() => (
                <div className="wf-log">
                  {logs.map((l, i) => (
                    <div key={i} className="wf-log-line">
                      <span className="faint tnum">{new Date(l.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}</span>
                      <span>{l.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </Collapse>
          </div>
        )}
      </Collapse>
    </div>
  );
}
