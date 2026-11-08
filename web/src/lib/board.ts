// The workboard's pure logic: which lane a session sits in, what can be done to it there, and the
// ledger of what happened to it. No React, no store; the Qt client ports this file one to one
// (qt/pilcrow_qt/lib/board.py) and both run the cases in test/unit/board.test.ts.
import type { Decision, MemoryConflict, PermissionRequest, ScheduledRun, SessionState, SessionSummary, WaitCondition } from './types';
import { relPath } from './format';
import { str } from './tools';

/** Whose hands the ball is in. The order is the direction work flows across the board. */
export type Lane = 'backlog' | 'agent' | 'wait' | 'me' | 'done';

export const LANES: { key: Lane; title: string; hint: string; empty: string }[] = [
  { key: 'backlog', title: 'Backlog', hint: 'Not started', empty: 'Nothing waiting to start.' },
  { key: 'agent', title: 'With Claude', hint: 'Working now', empty: 'Claude is not working on anything.' },
  { key: 'wait', title: 'Waiting', hint: 'On a condition', empty: 'Nothing is waiting on a condition.' },
  { key: 'me', title: 'Needs you', hint: 'The only lane that interrupts you', empty: 'Nothing needs you.' },
  { key: 'done', title: 'Done', hint: 'Approved today', empty: 'Nothing approved today.' },
];

export const sameDay = (a: number, b: number) => {
  const x = new Date(a);
  const y = new Date(b);
  return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate();
};

export const lastDecision = (s: SessionSummary): Decision | null => (s.decisions?.length ? s.decisions[s.decisions.length - 1] : null);

/** Claude could not go on by itself: the process ended with an error. */
export const isStuck = (s: SessionSummary) => s.status === 'error';

/** Something only you can answer is open: a permission, a question, a plan. */
export const hasPrompt = (s: SessionSummary, rt?: Partial<SessionState>) => s.status === 'requires_action' || (s.pending || 0) > 0 || (rt?.pending?.length || 0) > 0;

/** Claude finished a turn on this card and you have not dealt with it yet. */
export const awaitsReview = (s: SessionSummary) => Boolean(s.intent && s.turnEnd && s.reviewedTurn !== s.turnEnd && s.status !== 'running' && s.status !== 'starting');

/**
 * The lane of a session, or null when it is not on the board: archived, or a plain conversation
 * (no intent) that is not doing anything. Only work is on the board; conversations stay in the sidebar.
 */
export function laneOf(s: SessionSummary, rt?: Partial<SessionState>, now = Date.now()): Lane | null {
  if (s.archived) return null;
  if (s.conflict) return 'me';
  if (hasPrompt(s, rt)) return 'me';
  if (s.status === 'running' || s.status === 'starting') return 'agent';
  if (s.queued) return 'agent';
  if (!s.intent) return null;
  if (isStuck(s)) return 'agent';
  if (s.wait) return 'wait';
  if (awaitsReview(s)) return 'me';
  const last = lastDecision(s);
  if (last?.kind === 'approve' && s.reviewedTurn && s.reviewedTurn === s.turnEnd && sameDay(last.at, now)) return 'done';
  return 'backlog';
}

/** A finer state than the lane, for the chip on the card and the actions in the panel. */
export type CardState = 'review' | 'prompt' | 'running' | 'queued' | 'stuck' | 'wait' | 'done' | 'backlog';
export function cardState(s: SessionSummary, rt?: Partial<SessionState>, now = Date.now()): CardState | null {
  const lane = laneOf(s, rt, now);
  if (!lane) return null;
  if (lane === 'me') return s.conflict || hasPrompt(s, rt) ? 'prompt' : 'review';
  if (lane === 'agent') return s.status === 'running' || s.status === 'starting' ? 'running' : isStuck(s) ? 'stuck' : s.queued ? 'queued' : 'running';
  return lane;
}

export interface CardAction {
  id: 'open' | 'approve' | 'reject' | 'answer' | 'park' | 'stop' | 'start' | 'send' | 'wake' | 'wait' | 'unwait' | 'schedule-edit' | 'schedule-run' | 'note-forget' | 'note-keep';
  label: string;
  key: string;
  primary?: boolean;
  danger?: boolean;
}

/**
 * What can be done to a card in its current state. The first action is what Enter does; the keys
 * are single letters so a backlog can be cleared without the mouse: e e r … e.
 */
export function actionsFor(state: CardState, rt?: Partial<SessionState>): CardAction[] {
  switch (state) {
    case 'review':
      return [
        { id: 'approve', label: 'Approve and commit', key: 'e', primary: true },
        { id: 'reject', label: 'Send back', key: 'r' },
        { id: 'park', label: 'Set aside', key: 'w' },
        { id: 'open', label: 'Open', key: '↵' },
      ];
    case 'prompt': {
      const p = rt?.pending?.[0];
      const label = p?.toolName === 'AskUserQuestion' ? 'Answer the question' : p?.toolName === 'ExitPlanMode' ? 'Review the plan' : 'Open to respond';
      return [{ id: 'open', label, key: '↵', primary: true }];
    }
    case 'running':
      return [
        { id: 'open', label: 'Open', key: '↵', primary: true },
        { id: 'stop', label: 'Stop', key: 's', danger: true },
      ];
    case 'queued':
      return [
        { id: 'open', label: 'Open', key: '↵', primary: true },
        { id: 'stop', label: 'Leave the queue', key: 's', danger: true },
      ];
    case 'stuck':
      return [
        { id: 'send', label: 'Give it what it needs', key: 'r', primary: true },
        { id: 'open', label: 'Open', key: '↵' },
        { id: 'park', label: 'Set aside', key: 'w' },
      ];
    case 'wait':
      return [
        { id: 'wake', label: 'Wake now', key: '↵', primary: true },
        { id: 'wait', label: 'Change the wait', key: 'w' },
        { id: 'unwait', label: 'Stop waiting', key: 's', danger: true },
        { id: 'open', label: 'Open', key: 'o' },
      ];
    case 'backlog':
      return [
        { id: 'start', label: 'Hand to Claude', key: '↵', primary: true },
        { id: 'wait', label: 'Wait for…', key: 'w' },
        { id: 'open', label: 'Open', key: 'o' },
      ];
    case 'done':
      return [{ id: 'open', label: 'Open', key: '↵', primary: true }];
  }
}

/**
 * Dropping a card on a lane means "do that lane's action". Only lanes with a legal action accept;
 * "Needs you" never does, since a card gets there by Claude handing work back, not by being put there.
 */
export function dropAction(state: CardState, lane: Lane): CardAction['id'] | null {
  switch (lane) {
    case 'agent':
      return state === 'backlog' ? 'start' : state === 'wait' ? 'wake' : state === 'stuck' ? 'send' : null;
    case 'wait':
      return state === 'backlog' || state === 'review' || state === 'wait' ? 'wait' : null;
    case 'done':
      return state === 'review' ? 'approve' : null;
    case 'backlog':
      return state === 'running' || state === 'queued' ? 'stop' : state === 'wait' ? 'unwait' : state === 'review' || state === 'stuck' ? 'park' : null;
    case 'me':
      return null;
  }
}

// ---- schedules as cards

export const SCHEDULE_PREFIX = 'sched:';
export const isSchedule = (id: string | null | undefined) => Boolean(id && id.startsWith(SCHEDULE_PREFIX));

/** A schedule that will fire, as a card in the Waiting lane. It is not a session; its actions open the schedule. */
export function scheduleCard(r: ScheduledRun): SessionSummary {
  return { id: SCHEDULE_PREFIX + r.id, title: r.name, cwd: r.cwd, project: r.cwd, archived: false, pinned: false, worktree: null, status: 'idle', live: false, createdAt: r.createdAt, lastModified: r.at, intent: r.prompt, wait: { kind: 'time', at: r.at } };
}

export function scheduleActions(): CardAction[] {
  return [
    { id: 'schedule-edit', label: 'Edit the schedule', key: '↵', primary: true },
    { id: 'schedule-run', label: 'Run now', key: 'r' },
  ];
}

// ---- contradicted notes as cards (docs/PLAYBOOK.md §4)

export const NOTE_PREFIX = 'note:';
export const isConflict = (id: string | null | undefined) => Boolean(id && id.startsWith(NOTE_PREFIX));

/**
 * A note in project memory that a correction of yours contradicted, as a card in "Needs you". It is
 * not a session: the note is its intent, and its actions keep or forget the note.
 */
export function conflictCard(c: MemoryConflict): SessionSummary {
  return { id: NOTE_PREFIX + c.id, title: 'A note was contradicted', cwd: c.cwd, project: c.cwd, archived: false, pinned: false, worktree: null, status: 'idle', live: false, createdAt: c.at, lastModified: c.at, intent: c.text, conflict: c };
}

export function conflictActions(): CardAction[] {
  return [
    { id: 'note-forget', label: 'Forget the note', key: '↵', primary: true },
    { id: 'note-keep', label: 'Keep it', key: 'e' },
  ];
}

/** The actions of any card: a schedule's, a contradicted note's, or a session's by its state. */
export function actionsOf(s: SessionSummary, state: CardState, rt?: Partial<SessionState>): CardAction[] {
  return isSchedule(s.id) ? scheduleActions() : s.conflict ? conflictActions() : actionsFor(state, rt);
}

export interface LedgerEvent {
  at: number;
  kind: 'intent' | 'decision' | 'turn' | 'commit';
  label: string;
  note?: string;
  tone: 'me' | 'run' | 'ok' | 'err' | '';
}

const DECISION_LABEL: Record<Decision['kind'], string> = {
  approve: 'You approved',
  reject: 'You sent it back',
  answer: 'You answered',
  park: 'You set it aside',
  wait: 'Waiting',
  wake: 'Woken',
  stop: 'You stopped it',
  start: 'Handed to Claude',
  steer: 'You steered',
};

/** Everything that happened to a card, oldest first, from the summary alone (no transcript needed). */
export function ledgerOf(s: SessionSummary, rt?: Partial<SessionState>): LedgerEvent[] {
  const out: LedgerEvent[] = [];
  if (s.createdAt) out.push({ at: s.createdAt, kind: 'intent', label: s.intent ? 'Intent written' : 'Session opened', note: s.intent, tone: 'me' });
  for (const d of s.decisions || []) {
    out.push({ at: d.at, kind: 'decision', label: DECISION_LABEL[d.kind] || d.kind, note: d.note, tone: d.kind === 'stop' ? 'err' : d.kind === 'approve' ? 'ok' : d.kind === 'wake' || d.kind === 'wait' ? '' : 'me' });
    if (d.commit) out.push({ at: d.at + 1, kind: 'commit', label: `Committed ${d.commit.slice(0, 7)}`, tone: 'ok' });
  }
  if (s.turnEnd) {
    const parts = [rt?.activeModel ? rt.activeModel.replace(/^claude-/, '').replace(/-\d{8}$/, '') : '', s.costUsd ? `$${s.costUsd.toFixed(2)}` : ''].filter(Boolean);
    out.push({ at: s.turnEnd, kind: 'turn', label: s.status === 'error' ? 'Turn ended with an error' : 'Turn ended', note: parts.join(' · ') || undefined, tone: s.status === 'error' ? 'err' : 'run' });
  }
  return out.sort((a, b) => a.at - b.at);
}

/** The one line under a card's title: what it is waiting for, doing, or asking. */
export function cardLine(s: SessionSummary, state: CardState, rt?: Partial<SessionState>, titles?: Record<string, string>): { text: string; tone: 'ask' | 'err' | '' } {
  if (s.conflict) return { text: `Your correction${s.conflict.sessionTitle ? ` on ${s.conflict.sessionTitle}` : ''} says otherwise: keep the note or forget it`, tone: 'ask' };
  switch (state) {
    case 'prompt': {
      const p = rt?.pending?.[0];
      return { text: p ? permSummary(p, s.cwd).text || p.toolName : 'Waiting for your input', tone: 'ask' };
    }
    case 'review':
      return { text: s.report?.ask || rt?.lastReply?.text || 'Finished; review the result', tone: 'ask' };
    case 'running': {
      const a = rt?.activity;
      return { text: a?.kind === 'tool' ? `${a.tool} · ${a.label}` : a?.kind === 'text' ? a.label : s.status === 'starting' ? 'Starting Claude Code…' : 'Thinking…', tone: '' };
    }
    case 'queued':
      return { text: 'Queued: Claude is at the limit; it starts when a slot frees', tone: '' };
    case 'stuck':
      return { text: rt?.error || 'Stopped with an error', tone: 'err' };
    case 'wait':
      return { text: s.wait ? describeWait(s.wait, titles) : 'Waiting', tone: '' };
    case 'done': {
      const d = lastDecision(s);
      return { text: d?.commit ? `Committed ${d.commit.slice(0, 7)}` : 'Approved', tone: '' };
    }
    case 'backlog':
      return { text: s.intent ? firstSentence(s.intent) : 'No intent yet', tone: '' };
  }
}

export function describeWait(w: WaitCondition, titles?: Record<string, string>) {
  if (w.kind === 'time') return `Until ${new Date(w.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}`;
  if (w.kind === 'ci') return `Until CI finishes on ${w.branch}`;
  const t = titles?.[w.id];
  return t ? `Until “${t}” is approved` : 'Until another card is approved';
}

const firstSentence = (t: string) => {
  const m = /^[^.。!?！？\n]*[.。!?！？]?/.exec(t.trim());
  return (m ? m[0] : t).trim();
};

/** What a permission request is about, in one line, for the card and the panel. */
export function permSummary(p: PermissionRequest, cwd: string): { text: string; mono: boolean; note?: string } {
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
      return { text: (i.questions as { question?: string }[] | undefined)?.[0]?.question || 'Claude has a question', mono: false };
    case 'ExitPlanMode':
      return { text: 'A plan is ready for review', mono: false };
    default:
      return { text: p.description || p.title || '', mono: false };
  }
}

/** The card after `id` in the given direction within one lane's list, or the same index in the next lane. */
export function step(lanes: Record<Lane, SessionSummary[]>, id: string | null, dir: 'up' | 'down' | 'left' | 'right'): string | null {
  const order = LANES.map((l) => l.key);
  const li = order.findIndex((k) => lanes[k].some((s) => s.id === id));
  if (li < 0) {
    const first = order.find((k) => lanes[k].length);
    return first ? lanes[first][0].id : null;
  }
  const list = lanes[order[li]];
  const i = list.findIndex((s) => s.id === id);
  if (dir === 'down') return list[Math.min(list.length - 1, i + 1)].id;
  if (dir === 'up') return list[Math.max(0, i - 1)].id;
  const d = dir === 'right' ? 1 : -1;
  for (let j = li + d; j >= 0 && j < order.length; j += d) {
    const next = lanes[order[j]];
    if (next.length) return next[Math.min(i, next.length - 1)].id;
  }
  return id;
}

/** The quick wait conditions the picker offers; a pure list so the keys 1–3 and the buttons agree. */
export function quickWaits(now = Date.now()): { key: string; label: string; hint: string; wait: WaitCondition | 'ci' | 'session' | 'time' }[] {
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(9, 0, 0, 0);
  return [
    { key: '1', label: 'Until CI passes', hint: 'GitHub Actions on this branch', wait: 'ci' },
    { key: '2', label: 'Tomorrow 09:00', hint: 'A one-off time', wait: { kind: 'time', at: tomorrow.getTime() } },
    { key: '3', label: 'In an hour', hint: '', wait: { kind: 'time', at: now + 3600e3 } },
  ];
}
