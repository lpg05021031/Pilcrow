// Scheduled sessions: a prompt that runs in a folder on a cadence while nobody is watching, and an
// inbox that collects what each run produced. The server is always on, so this needs no cron.
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { store } from './config.js';
import { sessions, bus, textOfContent } from './sessions.js';
import * as snapshots from './snapshots.js';
import { xdg, readJson, writeJson, uuid, log, slash } from './util.js';

export const events = new EventEmitter();

const FILE = path.join(xdg('config'), 'schedules.json');
const INBOX = path.join(xdg('state'), 'inbox.json');
const MAX_INBOX = 400;
const MAX_PARALLEL = 2;
const DEFAULT_MAX_MIN = 45;

let data = readJson(FILE, { schedules: [] });
let inbox = readJson(INBOX, { items: [] });
const running = new Map(); // scheduleId -> { sessionId, startedAt, timer }

function persist() {
  writeJson(FILE, data);
  events.emit('schedules');
}
function saveInbox() {
  writeJson(INBOX, inbox);
  events.emit('inbox');
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
      while (d.getTime() <= from || d.getDay() === 0 || d.getDay() === 6) d.setTime(d.getTime() + DAY), d.setHours(h, m, 0, 0);
      return d.getTime();
    }
    case 'weekly': {
      const wd = Number.isInteger(c.weekday) ? c.weekday : 1;
      const d = new Date(base);
      d.setHours(h, m, 0, 0);
      while (d.getTime() <= from || d.getDay() !== wd) d.setTime(d.getTime() + DAY), d.setHours(h, m, 0, 0);
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
      while (d.getTime() <= from) d.setTime(d.getTime() + DAY), d.setHours(h, m, 0, 0);
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
  return { ...s, nextRunAt: next, cadenceLabel: describeCadence(s.cadence), error, running: run ? { sessionId: run.sessionId, startedAt: run.startedAt } : null };
}

export function list() {
  return data.schedules.map(withNext);
}

export function save(input) {
  const cur = input.id ? data.schedules.find((s) => s.id === input.id) : null;
  const s = {
    id: cur?.id || uuid(),
    name: String(input.name || '').trim() || 'Untitled schedule',
    cwd: slash(String(input.cwd || cur?.cwd || '')),
    prompt: String(input.prompt || ''),
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
  if (!s.cwd) throw new Error('Pick a folder for the schedule.');
  if (!s.prompt.trim()) throw new Error('Write what Claude should do each time.');
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
  const { id: sessionId } = await sessions.create({ cwd: s.cwd, worktree: s.worktree ? { name: s.name } : null, mode: s.mode === 'deny' ? 'default' : s.mode, model: s.model || undefined, effort: s.effort || undefined, title: `${s.name} · ${stamp}` });
  store.setMeta(sessionId, { scheduled: { id: s.id, name: s.name, at: startedAt, manual } });
  const session = sessions.find(sessionId);
  session.unattended = true;
  const entry = { sessionId, startedAt, timer: null };
  running.set(id, entry);
  s.lastRunAt = startedAt;
  s.runs = (s.runs || 0) + 1;
  persist();
  log('schedule run', s.name, sessionId);
  const finish = (status, reason) => finishRun(s, session, entry, status, reason);
  entry.timer = setTimeout(() => {
    log('schedule timeout', s.name);
    session.interrupt().catch(() => {});
    setTimeout(() => finish('stopped', `Stopped after ${s.maxMinutes} minutes`), 3000);
  }, s.maxMinutes * 60e3);
  const onState = (st) => {
    if (st.id !== sessionId || !running.has(id) || running.get(id) !== entry) return;
    if (st.status === 'idle') finish('completed');
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
  const files = new Map();
  for (const st of snapshots.steps(session).steps) {
    if (st.at < entry.startedAt) continue;
    for (const f of st.files) {
      const cur = files.get(f.path) || { path: f.path, add: 0, del: 0 };
      cur.add += f.add;
      cur.del += f.del;
      files.set(f.path, cur);
    }
  }
  const lastText = session.lastReply?.text || lastAssistantText(session) || '';
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
    summary: lastText.slice(0, 1200),
    cost: session.usage?.costUsd || 0,
    files: [...files.values()].slice(0, 60),
    read: false,
    manual: Boolean(store.getMeta(session.id)?.scheduled?.manual),
  };
  inbox.items.unshift(item);
  if (inbox.items.length > MAX_INBOX) inbox.items.length = MAX_INBOX;
  saveInbox();
  s.lastStatus = status;
  persist();
  bus.emit('list-changed');
  // The process is not needed any more; the transcript stays and the session reopens on demand.
  setTimeout(() => session.close(), 1500);
  log('schedule done', s.name, status);
}

function lastAssistantText(session) {
  for (let i = session.messages.length - 1; i >= 0; i--) {
    const m = session.messages[i];
    if (m.type === 'assistant' && !m.parent_tool_use_id) {
      const t = textOfContent(m.message?.content);
      if (t) return t.replace(/\s+/g, ' ').trim();
    }
  }
  return '';
}

let ticker = null;
export function start() {
  if (ticker) return;
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
          inbox.items.unshift({ id: uuid(), scheduleId: s.id, scheduleName: s.name, sessionId: null, cwd: s.cwd, startedAt: now, endedAt: now, durationMs: 0, status: 'failed', reason: e?.message || 'Could not start', summary: '', cost: 0, files: [], read: false });
          saveInbox();
          persist();
        }
      }
    }
  };
  ticker = setInterval(tick, 30_000);
  setTimeout(tick, 5000);
}

export function stop() {
  clearInterval(ticker);
  ticker = null;
}

// ---------------------------------------------------------------- inbox

export function inboxList() {
  return { items: inbox.items, unread: inbox.items.filter((i) => !i.read).length };
}

export function markRead(ids) {
  for (const i of inbox.items) if (!ids || ids.includes(i.id)) i.read = true;
  saveInbox();
  return inboxList().unread;
}

export function removeItem(id) {
  inbox.items = inbox.items.filter((i) => i.id !== id);
  saveInbox();
  return true;
}
