// The workboard: a session is a card, and the few things a card knows beyond the session's own
// state live in its meta — the intent it was opened for, the decisions you made on it, and which
// turn you last reviewed. The lane a card sits in is derived on the client (web/src/lib/board.ts);
// the server only records facts and performs the actions a decision implies (commit, resend, stop).
import { store } from './config.js';
import { sessions, bus, costLog } from './sessions.js';
import * as git from './git.js';

const DECISIONS = new Set(['approve', 'reject', 'answer', 'park', 'wait', 'wake', 'stop', 'start', 'steer']);

/** The board's meta fields of one session, as `sessions.list()` carries them. */
export function boardFields(meta) {
  return {
    intent: meta.intent || undefined,
    turnEnd: meta.turnEnd || undefined,
    reviewedTurn: meta.reviewedTurn || undefined,
    decisions: Array.isArray(meta.decisions) && meta.decisions.length ? meta.decisions : undefined,
    costUsd: meta.costUsd || undefined,
    wait: meta.wait || undefined,
    report: meta.report || undefined,
  };
}

/** Everything the board page needs that the session list does not carry: live state, today's figures, the concurrency limit. */
export async function state() {
  const list = await sessions.list();
  const live = Object.fromEntries(sessions.live().map((s) => [s.id, s.state()]));
  const dayStart = new Date();
  dayStart.setHours(0, 0, 0, 0);
  let cost = 0;
  let count = 0;
  let done = 0;
  const perSession = [];
  for (const s of list) {
    const m = store.getMeta(s.id) || {};
    if ((s.lastModified || 0) >= dayStart.getTime() && m.costUsd) {
      cost += m.costUsd;
      count++;
      perSession.push({ id: s.id, title: s.title, cost: m.costUsd });
    }
    const last = Array.isArray(m.decisions) ? m.decisions[m.decisions.length - 1] : null;
    if (last?.kind === 'approve' && last.at >= dayStart.getTime()) done++;
  }
  const running = list.filter((s) => s.status === 'running' || s.status === 'starting').length;
  const cfg = store.getConfig();
  return {
    live,
    today: { cost, sessions: count, done, perSession: perSession.sort((a, b) => b.cost - a.cost).slice(0, 8) },
    spend: costLog.since(Date.now() - 3600e3),
    limit: { max: Number.isFinite(cfg.maxConcurrent) ? cfg.maxConcurrent : 3, running },
  };
}

export function setIntent(id, intent) {
  if (!store.getMeta(id)) throw new Error('Unknown session: ' + id);
  const text = typeof intent === 'string' ? intent.trim() : '';
  store.setMeta(id, { intent: text || null });
  bus.emit('list-changed');
  return { intent: text };
}

function record(id, decision) {
  const meta = store.getMeta(id) || {};
  const decisions = [...(Array.isArray(meta.decisions) ? meta.decisions : []), decision].slice(-200);
  store.setMeta(id, { decisions });
  return decision;
}

/** The short hash from `git commit`'s first line: `[branch 1a2b3c4] message`. */
export function commitHash(output) {
  const m = /^\[[^\]\n]*?\b([0-9a-f]{7,40})\]/m.exec(output || '');
  return m ? m[1] : null;
}

/**
 * A decision on a card. Each kind records itself and then does what it implies:
 *   approve  commit what the session changed, then mark this turn reviewed
 *   reject   mark reviewed and send the note as the next message (Claude picks the work back up)
 *   answer   the same as reject; the note is the answer
 *   park     mark reviewed without sending anything; the card goes back to the backlog
 *   stop     interrupt the turn; whatever turn end it produces counts as reviewed
 */
export async function decide(id, { kind, note = '', turn } = {}) {
  if (!DECISIONS.has(kind)) throw new Error('Unknown decision: ' + kind);
  const s = await sessions.get(id);
  const meta = store.getMeta(id) || {};
  const text = String(note || '').trim();
  const at = Date.now();
  const forTurn = turn || meta.turnEnd || undefined;

  if (kind === 'approve') {
    const message = text || (meta.title || s.title || 'Changes from Pilcrow').split('\n')[0].slice(0, 120);
    const r = await git.commit(s.cwd, { message });
    const nothing = /nothing to commit|nothing added to commit|no changes added/i.test(r.output || '');
    if (!r.ok && !nothing) throw new Error(r.output || 'Commit failed');
    const commit = r.ok ? commitHash(r.output) : null;
    record(id, { at, kind, note: text || undefined, commit: commit || undefined, turn: forTurn });
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    return { ok: true, commit, output: r.output };
  }
  if (kind === 'reject' || kind === 'answer') {
    if (!text) throw new Error(kind === 'reject' ? 'Say what should change.' : 'Write an answer.');
    record(id, { at, kind, note: text, turn: forTurn });
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    await s.send({ text });
    return { ok: true };
  }
  if (kind === 'park') {
    record(id, { at, kind, note: text || undefined, turn: forTurn });
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    return { ok: true };
  }
  if (kind === 'stop') {
    record(id, { at, kind, note: text || undefined, turn: forTurn });
    // The turn end this interrupt produces must not land the card in "Needs you": the session marks
    // it reviewed as it writes it. If no turn end follows (nothing was running), the current one counts.
    s.autoReview = true;
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    await s.interrupt();
    return { ok: true };
  }
  record(id, { at, kind, note: text || undefined, turn: forTurn });
  bus.emit('list-changed');
  return { ok: true };
}

/** Hand a backlog card to Claude: the first message is the text given, or the intent itself. */
export async function start(id, text) {
  const s = await sessions.get(id);
  const meta = store.getMeta(id) || {};
  const body = String(text || meta.intent || '').trim();
  if (!body) throw new Error('Write an intent first, so Claude knows what the work is.');
  record(id, { at: Date.now(), kind: 'start' });
  bus.emit('list-changed');
  const uuid = await s.send({ text: body });
  return { ok: true, uuid };
}
