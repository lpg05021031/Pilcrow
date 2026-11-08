// The workboard: a session is a card, and the few things a card knows beyond the session's own
// state live in its meta — the intent it was opened for, the decisions you made on it, which turn
// you last reviewed, a condition it is waiting on, and a place in the queue when Claude is at the
// limit. The lane a card sits in is derived on the client (web/src/lib/board.ts); the server only
// records facts and performs the actions a decision implies (commit, resend, stop, wake).
import { store } from './config.js';
import { sessions, bus, costLog } from './sessions.js';
import * as git from './git.js';
import * as schedules from './schedules.js';
import * as memory from './memory.js';
import { which, run, log } from './util.js';

const DECISIONS = new Set(['approve', 'reject', 'answer', 'park', 'wait', 'wake', 'stop', 'start', 'steer']);
const WAIT_KINDS = new Set(['time', 'ci', 'session']);
/** How often a CI condition is asked about, per session. */
const CI_POLL_MS = 120_000;
const WAKE_PROMPT = {
  time: 'The time you were waiting for has come. Continue the work as the intent describes.',
  ci: (conclusion) => `CI finished with "${conclusion}" on the branch you were waiting for. Continue the work as the intent describes: if it failed, read the failing run and fix it.`,
  session: (title) => `The card you were waiting for, "${title}", has been approved. Continue the work as the intent describes.`,
};

let ghPath;
/** Which conditions this machine can watch. `gh` is looked for once; a later install needs a restart. */
export function caps() {
  if (ghPath === undefined) ghPath = which('gh');
  return { ci: Boolean(ghPath) };
}

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
    queued: meta.queued || undefined,
  };
}

const limit = () => {
  const n = store.getConfig().maxConcurrent;
  return Number.isFinite(n) && n >= 0 ? n : 3;
};
const runningCount = () => sessions.live().filter((s) => s.status === 'running' || s.status === 'starting').length;

/** Everything the board page needs that the session list does not carry: live state, today's figures, the limit, upcoming schedules. */
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
  // Schedules that will fire are cards in the Waiting lane until they do; a run in flight is an ordinary session.
  const scheduled = schedules
    .list()
    .filter((s) => s.enabled && s.nextRunAt && !s.running)
    .map((s) => ({ id: s.id, name: s.name, cwd: s.cwd, prompt: s.prompt, at: s.nextRunAt, cadence: s.cadenceLabel, createdAt: s.createdAt }));
  return {
    live,
    today: { cost, sessions: count, done, perSession: perSession.sort((a, b) => b.cost - a.cost).slice(0, 8) },
    spend: costLog.since(Date.now() - 3600e3),
    limit: { max: limit(), running: runningCount() },
    scheduled,
    // Notes in project memory that a correction of yours contradicted: cards in "Needs you" (docs/PLAYBOOK.md §4).
    conflicts: memory.conflicts(),
    caps: caps(),
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

/** The first sentence of a report line, trimmed to a commit subject. */
function firstSentence(t) {
  if (!t) return '';
  const m = /^[^.!?\n]*[.!?]?/.exec(t.trim());
  return (m ? m[0] : t).trim().replace(/[.]$/, '').slice(0, 120);
}

/** The short hash from `git commit`'s first line: `[branch 1a2b3c4] message`. */
export function commitHash(output) {
  const m = /^\[[^\]\n]*?\b([0-9a-f]{7,40})\]/m.exec(output || '');
  return m ? m[1] : null;
}

// ---------------------------------------------------------------- the limit and the queue

/**
 * Send `text` to the session, or hold it in the queue when Claude is already working on as many
 * sessions as the limit allows. What you send by hand in a conversation never queues; only the
 * board's starts and wakes do.
 */
async function startOrQueue(s, text) {
  const max = limit();
  if (max > 0 && runningCount() >= max && !(s.status === 'running' || s.status === 'starting')) {
    store.setMeta(s.id, { queued: { text, at: Date.now() } });
    bus.emit('list-changed');
    return { queued: true };
  }
  store.setMeta(s.id, { queued: null });
  bus.emit('list-changed');
  const uuid = await s.send({ text });
  return { queued: false, uuid };
}

let draining = false;
/** Start the oldest queued card for every free slot. Called when a turn ends and on the tick. */
export async function drainQueue() {
  if (draining) return;
  draining = true;
  try {
    const max = limit();
    for (;;) {
      if (max > 0 && runningCount() >= max) return;
      const next = Object.entries(store.meta)
        .filter(([, m]) => m.queued && !m.archived)
        .sort((a, b) => a[1].queued.at - b[1].queued.at)[0];
      if (!next) return;
      const [id, m] = next;
      store.setMeta(id, { queued: null });
      try {
        const s = await sessions.get(id);
        await s.send({ text: m.queued.text });
      } catch (e) {
        log('queue: could not start', id, e?.message);
      }
      bus.emit('list-changed');
    }
  } finally {
    draining = false;
  }
}

// ---------------------------------------------------------------- waiting

/**
 * Set or clear the condition a card waits on. A wake sends `wait.prompt`, or a line that names the condition.
 * @param {string} id
 * @param {{ kind: string, at?: number, branch?: string, id?: string, prompt?: string } | null | undefined} wait
 */
export async function setWait(id, wait) {
  const meta = store.getMeta(id);
  if (!meta) throw new Error('Unknown session: ' + id);
  if (!wait) {
    store.setMeta(id, { wait: null });
    bus.emit('list-changed');
    return { wait: null };
  }
  if (!WAIT_KINDS.has(wait.kind)) throw new Error('Unknown wait condition: ' + wait.kind);
  const prompt = typeof wait.prompt === 'string' && wait.prompt.trim() ? wait.prompt.trim().slice(0, 2000) : undefined;
  let clean;
  if (wait.kind === 'time') {
    const at = Number(wait.at);
    if (!Number.isFinite(at) || at <= Date.now()) throw new Error('Pick a time in the future.');
    clean = { kind: 'time', at, prompt };
  } else if (wait.kind === 'ci') {
    if (!caps().ci) throw new Error('Waiting for CI needs the GitHub CLI (gh) on this machine.');
    const s = await sessions.get(id);
    let branch = typeof wait.branch === 'string' && wait.branch.trim() ? wait.branch.trim() : meta.worktree?.branch || '';
    if (!branch) {
      const info = await git.info(s.cwd);
      branch = info.branch || '';
    }
    if (!branch) throw new Error('This folder has no branch to watch.');
    clean = { kind: 'ci', cwd: s.cwd, branch, since: Date.now(), prompt };
  } else {
    const other = store.getMeta(String(wait.id || ''));
    if (!other) throw new Error('No such card to wait for.');
    if (wait.id === id) throw new Error('A card cannot wait for itself.');
    clean = { kind: 'session', id: String(wait.id), prompt };
  }
  store.setMeta(id, { wait: clean });
  record(id, { at: Date.now(), kind: 'wait', note: describeWait(clean) });
  bus.emit('list-changed');
  return { wait: clean };
}

function describeWait(w) {
  if (w.kind === 'time') return `Until ${new Date(w.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}`;
  if (w.kind === 'ci') return `Until CI finishes on ${w.branch}`;
  const t = store.getMeta(w.id)?.title;
  return `Until ${t ? `"${t}"` : 'another card'} is approved`;
}

/** The condition is met (or you decided not to wait): clear it, note why, and start the card — or queue it. */
export async function wake(id, note, prompt) {
  const meta = store.getMeta(id);
  if (!meta) throw new Error('Unknown session: ' + id);
  const wait = meta.wait;
  store.setMeta(id, { wait: null });
  record(id, { at: Date.now(), kind: 'wake', note });
  const s = await sessions.get(id);
  const text = (wait && wait.prompt) || prompt || meta.intent || WAKE_PROMPT.time;
  return startOrQueue(s, text);
}

const ciChecked = new Map();
/** Ask `gh` whether a run newer than the wait finished on the branch; null while it has not. */
async function ciResult(w) {
  const r = await run(ghPath || 'gh', ['run', 'list', '--branch', w.branch, '--limit', '1', '--json', 'status,conclusion,createdAt,url'], { cwd: w.cwd, timeout: 30_000 });
  if (r.code) {
    log('ci wait: gh failed', r.stderr.slice(0, 200));
    return null;
  }
  let runs;
  try {
    runs = JSON.parse(r.stdout);
  } catch {
    return null;
  }
  const latest = Array.isArray(runs) ? runs[0] : null;
  if (!latest || new Date(latest.createdAt).getTime() < w.since) return null;
  if (latest.status !== 'completed') return null;
  return { conclusion: latest.conclusion || 'unknown', url: latest.url };
}

/** Every half minute: wake the cards whose time has come or whose CI finished, and fill free slots from the queue. */
export async function tick() {
  const now = Date.now();
  for (const [id, m] of Object.entries(store.meta)) {
    if (!m.wait || m.archived) continue;
    try {
      if (m.wait.kind === 'time' && m.wait.at <= now) {
        await wake(id, 'Time came', WAKE_PROMPT.time);
      } else if (m.wait.kind === 'ci' && caps().ci && now - (ciChecked.get(id) || 0) >= CI_POLL_MS) {
        ciChecked.set(id, now);
        const res = await ciResult(m.wait);
        if (res) {
          ciChecked.delete(id);
          await wake(id, `CI ${res.conclusion}${res.url ? ` · ${res.url}` : ''}`, WAKE_PROMPT.ci(res.conclusion));
        }
      }
    } catch (e) {
      log('wait: wake failed', id, e?.message);
    }
  }
  await drainQueue();
}

/** A card was approved: anything waiting for it wakes. */
async function wakeDependents(id) {
  const title = store.getMeta(id)?.title || 'a card';
  for (const [other, m] of Object.entries(store.meta)) {
    if (m.wait?.kind === 'session' && m.wait.id === id && !m.archived) {
      try {
        await wake(other, `"${title}" was approved`, WAKE_PROMPT.session(title));
      } catch (e) {
        log('wait: dependent wake failed', other, e?.message);
      }
    }
  }
}

// ---------------------------------------------------------------- decisions

/**
 * A decision on a card. Each kind records itself and then does what it implies:
 *   approve  commit what the session changed, mark this turn reviewed, wake cards waiting for it
 *   reject   mark reviewed and send the note as the next message (Claude picks the work back up)
 *   answer   the same as reject; the note is the answer
 *   park     mark reviewed without sending anything; the card goes back to the backlog
 *   stop     interrupt the turn (or leave the queue); whatever turn end it produces counts as reviewed
 *   wake     stop waiting and start now
 * @param {string} id
 * @param {{ kind: string, note?: string, turn?: number }} d
 */
export async function decide(id, { kind, note = '', turn }) {
  if (!DECISIONS.has(kind)) throw new Error('Unknown decision: ' + kind);
  const s = await sessions.get(id);
  const meta = store.getMeta(id) || {};
  const text = String(note || '').trim();
  const at = Date.now();
  const forTurn = turn || meta.turnEnd || undefined;

  if (kind === 'approve') {
    // The report of this very turn, if there is one: its "Did" names the commit, its files bound it.
    const report = meta.report && meta.report.turn === meta.turnEnd ? meta.report : null;
    const message = text || firstSentence(report?.did) || (meta.title || s.title || 'Changes from Pilcrow').split('\n')[0].slice(0, 120);
    // A worktree is the session's own; in a shared folder only the files this turn changed go in.
    const paths = !meta.worktree && report?.files?.length ? report.files.map((f) => f.path) : undefined;
    const r = await git.commit(s.cwd, { message, paths });
    const nothing = /nothing to commit|nothing added to commit|no changes added/i.test(r.output || '');
    if (!r.ok && !nothing) throw new Error(r.output || 'Commit failed');
    const commit = r.ok ? commitHash(r.output) : null;
    record(id, { at, kind, note: text || undefined, commit: commit || undefined, turn: forTurn });
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    wakeDependents(id).catch(() => {});
    // The notes this session read stood up to your review (docs/PLAYBOOK.md §4).
    try {
      memory.credit(s);
    } catch (e) {
      log('memory credit failed', id, e?.message);
    }
    return { ok: true, commit, output: r.output };
  }
  if (kind === 'reject' || kind === 'answer') {
    if (!text) throw new Error(kind === 'reject' ? 'Say what should change.' : 'Write an answer.');
    record(id, { at, kind, note: text, turn: forTurn });
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at });
    bus.emit('list-changed');
    // A correction may hold a rule for later sessions; the run that reads it must not hold up the reply.
    if (kind === 'reject') memory.reflect(s, text).catch((e) => log('memory reflect failed', id, e?.message));
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
    store.setMeta(id, { reviewedTurn: meta.turnEnd || at, queued: null, wait: null });
    bus.emit('list-changed');
    await s.interrupt();
    drainQueue().catch(() => {});
    return { ok: true };
  }
  if (kind === 'wake') return wake(id, text || 'Woken by you');
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
  return startOrQueue(s, body);
}
