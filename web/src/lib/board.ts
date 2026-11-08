// The workboard's pure logic: which lane a session sits in, what can be done to it there, and the
// ledger of what happened to it. No React, no store; the Qt client ports this file one to one
// (qt/pilcrow_qt/lib/board.py) and both run the cases in test/unit/board.test.ts.
import type { Decision, PermissionRequest, SessionState, SessionSummary } from './types';
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
  if (hasPrompt(s, rt)) return 'me';
  if (s.status === 'running' || s.status === 'starting') return 'agent';
  if (!s.intent) return null;
  if (isStuck(s)) return 'agent';
  if (s.wait) return 'wait';
  if (awaitsReview(s)) return 'me';
  const last = lastDecision(s);
  if (last?.kind === 'approve' && s.reviewedTurn && s.reviewedTurn === s.turnEnd && sameDay(last.at, now)) return 'done';
  return 'backlog';
}

/** A finer state than the lane, for the chip on the card and the actions in the panel. */
export type CardState = 'review' | 'prompt' | 'running' | 'stuck' | 'wait' | 'done' | 'backlog';
export function cardState(s: SessionSummary, rt?: Partial<SessionState>, now = Date.now()): CardState | null {
  const lane = laneOf(s, rt, now);
  if (!lane) return null;
  if (lane === 'me') return hasPrompt(s, rt) ? 'prompt' : 'review';
  if (lane === 'agent') return isStuck(s) ? 'stuck' : 'running';
  return lane;
}

export interface CardAction {
  id: 'open' | 'approve' | 'reject' | 'answer' | 'park' | 'stop' | 'start' | 'send';
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
    case 'stuck':
      return [
        { id: 'send', label: 'Give it what it needs', key: 'r', primary: true },
        { id: 'open', label: 'Open', key: '↵' },
        { id: 'park', label: 'Set aside', key: 'w' },
      ];
    case 'wait':
      return [{ id: 'open', label: 'Open', key: '↵', primary: true }];
    case 'backlog':
      return [
        { id: 'start', label: 'Hand to Claude', key: '↵', primary: true },
        { id: 'open', label: 'Open', key: 'o' },
      ];
    case 'done':
      return [{ id: 'open', label: 'Open', key: '↵', primary: true }];
  }
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
    out.push({ at: d.at, kind: 'decision', label: DECISION_LABEL[d.kind] || d.kind, note: d.note, tone: d.kind === 'stop' ? 'err' : d.kind === 'approve' ? 'ok' : 'me' });
    if (d.commit) out.push({ at: d.at + 1, kind: 'commit', label: `Committed ${d.commit.slice(0, 7)}`, tone: 'ok' });
  }
  if (s.turnEnd) {
    const parts = [rt?.activeModel ? rt.activeModel.replace(/^claude-/, '').replace(/-\d{8}$/, '') : '', s.costUsd ? `$${s.costUsd.toFixed(2)}` : ''].filter(Boolean);
    out.push({ at: s.turnEnd, kind: 'turn', label: s.status === 'error' ? 'Turn ended with an error' : 'Turn ended', note: parts.join(' · ') || undefined, tone: s.status === 'error' ? 'err' : 'run' });
  }
  return out.sort((a, b) => a.at - b.at);
}

/** The one line under a card's title: what it is waiting for, doing, or asking. */
export function cardLine(s: SessionSummary, state: CardState, rt?: Partial<SessionState>): { text: string; tone: 'ask' | 'err' | '' } {
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
    case 'stuck':
      return { text: rt?.error || 'Stopped with an error', tone: 'err' };
    case 'wait':
      return { text: s.wait ? describeWait(s.wait) : 'Waiting', tone: '' };
    case 'done': {
      const d = lastDecision(s);
      return { text: d?.commit ? `Committed ${d.commit.slice(0, 7)}` : 'Approved', tone: '' };
    }
    case 'backlog':
      return { text: s.intent ? firstSentence(s.intent) : 'No intent yet', tone: '' };
  }
}

export function describeWait(w: NonNullable<SessionSummary['wait']>) {
  if (w.kind === 'time') return `Until ${new Date(w.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
  if (w.kind === 'ci') return `Until CI passes on ${w.branch}`;
  return 'Until another card is done';
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
