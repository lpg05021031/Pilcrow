import type { DispatchJob, DispatchPending, DispatchRun, RunState } from '../../lib/types';
import { cost, duration, plural } from '../../lib/format';

/** The word of each run state, and the order the bar paints them in. */
export const STATE_WORD: Record<RunState, string> = { queued: 'queued', preparing: 'preparing', running: 'running', needs_action: 'needs action', done: 'done', failed: 'failed', stopped: 'stopped', skipped: 'skipped' };
export const BAR_ORDER: RunState[] = ['done', 'needs_action', 'running', 'preparing', 'queued', 'failed', 'stopped', 'skipped'];
export const ACTIVE: RunState[] = ['preparing', 'running', 'needs_action'];
export const TERMINAL: RunState[] = ['done', 'failed', 'stopped', 'skipped'];

export function clockMmss(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}

export function countsText(job: DispatchJob) {
  const c = job.counts || {};
  const parts: string[] = [];
  for (const [key, word] of [
    ['done', 'done'],
    ['needs_action', 'ask'],
    ['running', 'running'],
    ['preparing', 'preparing'],
    ['queued', 'queued'],
    ['failed', 'failed'],
    ['stopped', 'stopped'],
    ['skipped', 'skipped'],
  ] as [RunState, string][]) {
    const n = c[key] || 0;
    if (n) parts.push(`${n} ${word}`);
  }
  return parts.join(' · ') || plural(job.total || 0, 'repo');
}

export function jobStateText(job: DispatchJob, now: number) {
  switch (job.state) {
    case 'running':
      if (job.paused) return 'paused';
      if (job.capReached) return 'cost cap reached';
      return job.startedAt ? `running ${clockMmss(now - job.startedAt)}` : 'running';
    case 'reviewing':
      return job.canaryWaiting ? 'canary done — read it and continue' : 'review';
    case 'finished':
      return 'finished';
    case 'stopped':
      return (job.stoppedReason || 'stopped').toLowerCase();
    case 'planned':
      return 'planned';
    default:
      return 'draft';
  }
}

export function jobCostText(job: DispatchJob) {
  const cap = job.settings?.costCap || 0;
  return cap ? `${cost(job.cost || 0)} of ${cost(cap)}` : cost(job.cost || 0);
}

export function elapsedText(job: DispatchJob, now: number) {
  if (!job.startedAt) return '';
  return duration((job.finishedAt || now) - job.startedAt);
}

export interface RequestGroup {
  key: string;
  label: string;
  interactive: boolean;
  at: number;
  asks: { run: DispatchRun; pending: DispatchPending }[];
  names: string[];
}

/** The waiting requests across a job's runs, one entry per key: the one most runs asked first. */
export function groupRequests(runs: DispatchRun[]): RequestGroup[] {
  const byKey = new Map<string, RequestGroup>();
  for (const r of runs) {
    for (const p of r.pending || []) {
      const k = p.key || p.requestId;
      let e = byKey.get(k);
      if (!e) byKey.set(k, (e = { key: k, label: p.label || p.toolName, interactive: Boolean(p.interactive), at: p.at || 0, asks: [], names: [] }));
      e.asks.push({ run: r, pending: p });
      e.names.push(r.name);
      e.at = Math.min(e.at || p.at, p.at || e.at);
    }
  }
  return [...byKey.values()].sort((a, b) => b.asks.length - a.asks.length || a.at - b.at);
}

export function askedBy(names: string[]) {
  if (!names.length) return '';
  if (names.length === 1) return `asked by ${names[0]}`;
  if (names.length === 2) return `asked by ${names[0]} and ${names[1]}`;
  return `asked by ${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

/** The job's progress: one segment per run, in the colour of its state. */
export function SegmentBar({ job }: { job: DispatchJob }) {
  const states: RunState[] = [];
  for (const k of BAR_ORDER) for (let i = 0; i < (job.counts?.[k] || 0); i++) states.push(k);
  return (
    <span className="dp-bar" aria-hidden="true">
      {states.length ? states.map((s, i) => <i key={i} className={`dp-seg ${s}`} />) : <i className="dp-seg empty" />}
    </span>
  );
}

export function Glyph({ state }: { state: RunState }) {
  return <span className={`dp-glyph ${state}`} aria-hidden="true" />;
}
