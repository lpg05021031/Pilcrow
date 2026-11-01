// Scheduled sessions: a prompt that runs in a folder on a cadence while nobody is watching; what each
// run produced lands in the inbox (inbox.js). The server is always on, so this needs no cron.
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { store } from './config.js';
import { sessions, bus } from './sessions.js';
import * as snapshots from './snapshots.js';
import * as inbox from './inbox.js';
import { MAX_FILES, SUMMARY_MAX, capRun, changesOf } from './runs.js';
import { xdg, readJson, writeJson, uuid, log, slash, lastAssistantText } from './util.js';

export const events = new EventEmitter();

const FILE = path.join(xdg('config'), 'schedules.json');
const MAX_PARALLEL = 2;
const DEFAULT_MAX_MIN = 45;

/**
 * @typedef {{ id: string, kind: string, playbookId: string | null, playbookName: string | null, name: string, cwd: string, prompt: string,
 *   cadence: Record<string, any>, model: string, effort: string, mode: string, worktree: boolean, maxMinutes: number, enabled: boolean,
 *   createdAt: number, lastRunAt: number | null, lastStatus: string | null, runs: number }} Schedule
 * @typedef {{ sessionId?: string, jobId?: string, startedAt: number, timer: ReturnType<typeof setTimeout> | null, stopping?: string | null, onState?: (st: any) => void }} Run
 */

/** @type {{ schedules: Schedule[] }} read in start(), not at import: importing this module must not touch the person's files */
let data = { schedules: [] };
/** @type {Map<string, Run>} scheduleId -> its run in progress */
const running = new Map();
/** How a schedule of kind 'dispatch' runs: (playbookId, { scheduleId, name, unattended }) → job summary. Set by main.js. */
let dispatchRunner = null;
export function setDispatchRunner(fn) {
  dispatchRunner = fn;
}

function persist() {
  writeJson(FILE, data);
  events.emit('schedules');
}

// ---------------------------------------------------------------- cadence

const DAY = 86400e3;

function parseTime(t) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t || '');
  return m ? { h: Math.min(23, +m[1]), m: Math.min(59, +m[2]) } : { h: 9, m: 0 };
}

/** Minimal cron: five fields, with *, lists, ranges and steps. Returns a matcher per field. */
function cronMatchers(expr) {
  const fields = String(expr || '').trim().split(/\s+/);
  if (fields.length !== 5) throw new Error('A cron expression has five fields: minute hour day month weekday.');
  const ranges = [
    [0, 59],
    [0, 23],
    [1, 31],
    [1, 12],
    [0, 6],
  ];
  return fields.map((f, i) => {
    const [lo, hi] = ranges[i];
    const set = new Set();
    for (const part of f.split(',')) {
      const m = /^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/.exec(part);
      if (!m) throw new Error(`Cannot read "${part}" in the cron expression.`);
      const step = m[3] ? +m[3] : 1;
      let a = m[1] === '*' ? lo : +m[1];
      let b = m[1] === '*' ? hi : m[2] ? +m[2] : +m[1];
      if (i === 4) {
        a = a % 7;
        b = b % 7;
      }
      for (let v = a; v <= b; v += step) set.add(v);
    }
    return (v) => set.has(v);
  });
}

/** The same time of day, a day later (setHours again: a day is not 24 hours across a DST change). */
function nextDay(d, h, m) {
  d.setTime(d.getTime() + DAY);
  d.setHours(h, m, 0, 0);
}

/** When a schedule fires next, after `from`. */
export function nextRun(cadence, from = Date.now()) {
  const c = cadence || {};
  const { h, m } = parseTime(c.time);
  const base = new Date(from);
  base.setSeconds(0, 0);
  switch (c.kind) {
    case 'hourly':
    case 'interval': {
      const every = Math.max(1, Math.min(168, Number(c.everyHours) || 1));
      const d = new Date(base);
      d.setMinutes(m, 0, 0);
      while (d.getTime() <= from) d.setTime(d.getTime() + 3600e3);
      // Keep multi-hour intervals anchored to the chosen hour of day.
      if (every > 1) while ((d.getHours() - h + 24 * 7) % every !== 0) d.setTime(d.getTime() + 3600e3);
      return d.getTime();
    }
    case 'weekdays': {
      const d = new Date(base);
      d.setHours(h, m, 0, 0);
      while (d.getTime() <= from || d.getDay() === 0 || d.getDay() === 6) nextDay(d, h, m);
      return d.getTime();
    }
    case 'weekly': {
      const wd = Number.isInteger(c.weekday) ? c.weekday : 1;
      const d = new Date(base);
      d.setHours(h, m, 0, 0);
      while (d.getTime() <= from || d.getDay() !== wd) nextDay(d, h, m);
      return d.getTime();
    }
    case 'cron': {
      const match = cronMatchers(c.cron);
      const d = new Date(base);
      d.setTime(d.getTime() + 60e3);
      for (let i = 0; i < 366 * 24 * 60; i++) {
        if (match[0](d.getMinutes()) && match[1](d.getHours()) && match[2](d.getDate()) && match[3](d.getMonth() + 1) && match[4](d.getDay())) return d.getTime();
        d.setTime(d.getTime() + 60e3);
      }
      return null;
    }
    case 'daily':
    default: {
      const d = new Date(base);
      d.setHours(h, m, 0, 0);
      while (d.getTime() <= from) nextDay(d, h, m);
      return d.getTime();
    }
  }
}

export function describeCadence(c) {
  const t = c?.time || '09:00';
  switch (c?.kind) {
    case 'hourly':
      return 'Every hour';
    case 'interval':
      return `Every ${c.everyHours || 1} hours`;
    case 'weekdays':
      return `Weekdays at ${t}`;
    case 'weekly':
      return `${['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays'][c.weekday ?? 1]} at ${t}`;
    case 'cron':
      return `cron ${c.cron}`;
    default:
      return `Every day at ${t}`;
  }
}

// ---------------------------------------------------------------- CRUD

function withNext(s) {
  let next = null;
  let error = null;
  try {
    next = s.enabled ? nextRun(s.cadence) : null;
  } catch (e) {
    error = e.message;
  }
  const run = running.get(s.id);
  return { ...s, kind: s.kind || 'prompt', nextRunAt: next, cadenceLabel: describeCadence(s.cadence), error, running: run ? { sessionId: run.sessionId || null, jobId: run.jobId || null, startedAt: run.startedAt } : null };
}

export function list() {
  return data.schedules.map(withNext);
}

export function save(input) {
  const cur = input.id ? data.schedules.find((s) => s.id === input.id) : null;
  const kind = input.kind || cur?.kind || 'prompt';
  const s = {
    id: cur?.id || uuid(),
    kind,
    playbookId: kind === 'dispatch' ? String(input.playbookId || cur?.playbookId || '') : null,
    playbookName: kind === 'dispatch' ? String(input.playbookName || cur?.playbookName || '') : null,
    name: String(input.name || '').trim() || 'Untitled schedule',
    cwd: slash(String(input.cwd || cur?.cwd || '')),
    prompt: String(input.prompt ?? cur?.prompt ?? ''),
    cadence: input.cadence || cur?.cadence || { kind: 'daily', time: '09:00' },
    model: input.model ?? cur?.model ?? '',
    effort: input.effort ?? cur?.effort ?? '',
    mode: input.mode || cur?.mode || 'acceptEdits',
    worktree: Boolean(input.worktree ?? cur?.worktree),
    maxMinutes: Math.max(1, Math.min(240, Number(input.maxMinutes ?? cur?.maxMinutes ?? DEFAULT_MAX_MIN))),
    enabled: input.enabled ?? cur?.enabled ?? true,
    createdAt: cur?.createdAt || Date.now(),
    lastRunAt: cur?.lastRunAt || null,
    lastStatus: cur?.lastStatus || null,
    runs: cur?.runs || 0,
  };
  if (kind === 'dispatch') {
    if (!s.playbookId) throw new Error('Pick a playbook for the schedule.');
  } else {
    if (!s.cwd) throw new Error('Pick a folder for the schedule.');
    if (!s.prompt.trim()) throw new Error('Write what Claude should do each time.');
  }
  nextRun(s.cadence); // validates cron
  if (cur) Object.assign(cur, s);
  else data.schedules.push(s);
  persist();
  return withNext(s);
}

export function remove(id) {
  data.schedules = data.schedules.filter((s) => s.id !== id);
  persist();
  return true;
}

export function toggle(id, enabled) {
  const s = data.schedules.find((x) => x.id === id);
  if (!s) throw new Error('No such schedule.');
  s.enabled = Boolean(enabled);
  persist();
  return withNext(s);
}

// ---------------------------------------------------------------- running

export async function runNow(id, { manual = true } = {}) {
  const s = data.schedules.find((x) => x.id === id);
  if (!s) throw new Error('No such schedule.');
  if (running.has(id)) throw new Error('This schedule is already running.');
  if (running.size >= MAX_PARALLEL && !manual) return null; // the tick retries in a minute
  const startedAt = Date.now();
  const stamp = new Date(startedAt).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  if (s.kind === 'dispatch') {
    // a job from a playbook, unattended: no canary, permissions declined; its report is the inbox item
    if (!dispatchRunner) throw new Error('Dispatch is not available.');
    const job = await dispatchRunner(s.playbookId, { scheduleId: s.id, name: `${s.name} · ${stamp}`, unattended: true, manual });
    running.set(id, { jobId: job.id, startedAt, timer: null });
    s.lastRunAt = startedAt;
    s.runs = (s.runs || 0) + 1;
    persist();
    log('schedule dispatch', s.name, job.id);
    return { jobId: job.id };
  }
  const { id: sessionId } = await sessions.create({ cwd: s.cwd, worktree: s.worktree ? { name: s.name } : null, mode: s.mode === 'deny' ? 'default' : s.mode, model: s.model || undefined, effort: s.effort || undefined, title: `${s.name} · ${stamp}` });
  store.setMeta(sessionId, { scheduled: { id: s.id, name: s.name, at: startedAt, manual } });
  const session = sessions.find(sessionId);
  session.unattended = true;
  /** @type {Run} */
  const entry = { sessionId, startedAt, timer: null, stopping: null };
  running.set(id, entry);
  s.lastRunAt = startedAt;
  s.runs = (s.runs || 0) + 1;
  persist();
  log('schedule run', s.name, sessionId);
  const finish = (status, reason) => finishRun(s, session, entry, status, reason);
  entry.timer = capRun(entry, s.maxMinutes, () => session, (why) => finish('stopped', why));
  const onState = (st) => {
    if (st.id !== sessionId || !running.has(id) || running.get(id) !== entry) return;
    if (st.status === 'idle') finish(entry.stopping ? 'stopped' : 'completed', entry.stopping);
    else if (st.status === 'error') finish('failed', st.error);
    else if (st.status === 'stopped') finish(session.lastReply ? 'completed' : 'failed', 'Claude Code exited');
  };
  entry.onState = onState;
  bus.on('state', onState);
  try {
    await session.send({ text: s.prompt });
  } catch (e) {
    finish('failed', e.message);
    throw e;
  }
  return { sessionId };
}

function finishRun(s, session, entry, status, reason) {
  if (running.get(s.id) !== entry) return;
  running.delete(s.id);
  clearTimeout(entry.timer);
  bus.off('state', entry.onState);
  const endedAt = Date.now();
  const { files } = changesOf({ steps: snapshots.steps(session).steps, since: entry.startedAt });
  const lastText = session.lastReply?.text || lastAssistantText(session.messages);
  const item = {
    id: uuid(),
    scheduleId: s.id,
    scheduleName: s.name,
    sessionId: session.id,
    cwd: session.cwd,
    startedAt: entry.startedAt,
    endedAt,
    durationMs: endedAt - entry.startedAt,
    status,
    reason: reason || null,
    summary: lastText.slice(0, SUMMARY_MAX),
    cost: session.usage?.costUsd || 0,
    files: files.slice(0, MAX_FILES),
    read: false,
    manual: Boolean(store.getMeta(session.id)?.scheduled?.manual),
  };
  inbox.push(item);
  s.lastStatus = status;
  persist();
  bus.emit('list-changed');
  // The process is not needed any more; the transcript stays and the session reopens on demand.
  setTimeout(() => session.close(), 1500);
  log('schedule done', s.name, status);
}

/** @type {ReturnType<typeof setInterval> | undefined} */
let ticker;
/** @type {ReturnType<typeof setTimeout> | undefined} */
let firstTick;
export function start() {
  if (ticker) return;
  data = readJson(FILE, data);
  const tick = async () => {
    const now = Date.now();
    for (const s of data.schedules) {
      if (!s.enabled || running.has(s.id)) continue;
      let due;
      try {
        // Due when the next run after the last run (or creation) is already in the past.
        due = nextRun(s.cadence, Math.max(s.lastRunAt || 0, s.createdAt || 0, now - 2 * DAY));
      } catch {
        continue;
      }
      if (due && due <= now) {
        try {
          await runNow(s.id, { manual: false });
        } catch (e) {
          log('schedule failed to start', s.name, e?.message);
          s.lastRunAt = now;
          s.lastStatus = 'failed';
          inbox.push({ scheduleId: s.id, scheduleName: s.name, sessionId: null, cwd: s.cwd, startedAt: now, endedAt: now, durationMs: 0, status: 'failed', reason: e?.message || 'Could not start', summary: '', cost: 0, files: [] });
          persist();
        }
      }
    }
  };
  ticker = setInterval(tick, 30_000);
  firstTick = setTimeout(tick, 5000);
}

export function stop() {
  clearInterval(ticker);
  clearTimeout(firstTick);
  ticker = firstTick = undefined;
}

/** A dispatch job started by a schedule ended: the schedule's record moves on (the report is the job's own inbox item). */
export function noteDispatchFinished(scheduleId, status) {
  const s = data.schedules.find((x) => x.id === scheduleId);
  running.delete(scheduleId);
  if (!s) return;
  s.lastStatus = status;
  persist();
}
