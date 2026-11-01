// Helpers (HELPERS.md): a session that leads other sessions. The lead gets six typed tools on an
// in-process MCP server — start, wait, read, send, stop, list — and every helper is a real session in
// its own folder and worktree, with the person's permission cards and a handoff at the end. The lead
// is told when a helper finishes (a note written into its conversation), never polls, and reviews the
// helpers' changes together through the same fan-in Dispatch uses.
//
// `createHelpers` takes its collaborators so the tests can drive it with fake sessions; the instance at
// the bottom is wired to the real server and registers itself on sessions.js's extension points.
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { findRepos, groupHunks, ruleKey, ruleLabel } from './dispatch_core.js';

export const STATES = ['queued', 'preparing', 'running', 'needs_action', 'done', 'failed', 'stopped', 'skipped'];
export const TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);
export const ACTIVE = new Set(['preparing', 'running', 'needs_action']);
export const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
export const HANDOFF_MAX = 4000;
export const READ_DEFAULT = 8000;
export const READ_MAX = 24000;
export const NOTE_COALESCE_MS = 2000;
export const WAIT_DEFAULT_S = 300;
export const WAIT_MAX_S = 600;
export const TOOLS = ['start', 'wait', 'read', 'send', 'stop', 'list'];
export const TOOL_PREFIX = 'mcp__helpers__';
const MODE_WIDTH = { plan: 0, default: 1, acceptEdits: 2, bypassPermissions: 3 };
const DEFAULTS = { enabled: true, total: 8, parallel: 0, maxMinutes: 30, costUsd: 0, wakeLead: true };

/** Prepended to every helper's brief: what a helper is and how to end. */
export const HEADER =
  'You are a helper started by another Pilcrow session (the lead) for one part of a larger task. Work only in this folder. ' +
  'The person can see this conversation and will answer your permission requests; the lead cannot. If you must ask something, ask the person and keep going where you can. ' +
  'When you finish, end your reply with a section titled **Handoff**: what you changed (paths), any interface other parts depend on, what you could not do, and how to verify. Keep it under 40 lines.';

/** The MCP server's instructions: what the lead is told when the tools load. */
export function instructions(caps) {
  return (
    'Helpers are separate Pilcrow sessions you start for parallel work in other folders or repositories, each with its own brief, worktree and permissions. ' +
    'Use them when a task spans repositories or when parts can run at the same time; for a quick question inside this folder use the Agent tool. ' +
    'One helper, one folder, one brief with a definition of done and how to verify; say what it must not touch. ' +
    'Use a folder the person named; otherwise `list` shows the folders known here and which ones start without asking. A folder outside them asks the person on a permission card. ' +
    'Use `after` for ordered work: the handoff is appended for you. ' +
    'After starting helpers, end your turn and say what you are waiting for; Pilcrow will tell you when a helper finishes, fails or stops. Use `wait` only when you have nothing else to do. ' +
    'A helper waiting on a permission is waiting on the person, not on you; do not poll and do not ask the person to approve on its behalf. ' +
    'Read a result with `read` (summary first; diff only where you must check). Report to the person by helper name. Never write the repositories registry yourself; the person registers. ' +
    `Caps: ${caps.parallel} running at once, ${caps.total} in all, ${caps.maxMinutes} minutes each.`
  );
}

/** The Handoff section of a helper's last reply (a heading that says so to the end), else the whole reply, bounded. */
export function extractHandoff(text) {
  const t = String(text || '').trim();
  if (!t) return '';
  const m = /^(?:#{1,6}\s*\**|\*\*)\s*handoff\b.*$/im.exec(t);
  const out = m ? t.slice(m.index).trim() : t;
  return out.length > HANDOFF_MAX ? out.slice(0, HANDOFF_MAX - 1) + '…' : out;
}

export function fmtDuration(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`;
}
const fmtCost = (usd) => `$${(usd || 0).toFixed(2)}`;

/** A unified diff as text from git.diff's files, cut per file at a budget. */
export function renderDiff(files, { maxChars = READ_DEFAULT, only = '' } = {}) {
  const parts = [];
  let used = 0;
  let truncated = false;
  const list = files.filter((f) => !only || f.path === only || f.path.endsWith('/' + only));
  for (const f of list) {
    const head = `--- a/${f.path}\n+++ b/${f.path}\n`;
    const body = (f.hunks || []).map((h) => `@@ -${h.oldStart},${h.oldLines} +${h.newStart},${h.newLines} @@\n` + (h.lines || []).map((l) => l.t + l.s).join('\n')).join('\n');
    const text = head + body + '\n';
    if (used + text.length > maxChars) {
      const room = Math.max(0, maxChars - used - head.length - 40);
      parts.push(head + body.slice(0, room) + `\n… (${f.path} cut; read it alone with path)`);
      truncated = true;
      used = maxChars;
      break;
    }
    parts.push(text);
    used += text.length;
  }
  const left = list.length - parts.length;
  if (left > 0) {
    parts.push(`… ${left} more file${left === 1 ? '' : 's'} not shown: ${list.slice(parts.length).map((f) => f.path).join(', ')}`);
    truncated = true;
  }
  return { text: parts.join('\n').trim(), truncated, files: list.length };
}

export function createHelpers(deps) {
  const {
    sessions, // { create, get, find, remove, mode? } as sessions.js's manager
    bus,
    store, // { getConfig, getMeta, setMeta, meta }
    git,
    registry = null, // repos.js: resolve, policyFor, defaultsFor, notesFor, list, register, nearest
    pushInbox = () => null,
    updateInbox = () => null,
    which = () => null,
    run: exec = null,
    log = () => null,
    now = () => Date.now(),
    uuid = () => crypto.randomUUID(),
    slash = (p) => p,
    expandHome = (p) => p,
    textOfContent = (c) => (Array.isArray(c) ? c.filter((x) => x?.type === 'text').map((x) => x.text).join('\n') : typeof c === 'string' ? c : ''),
    homeDir = slash(os.homedir()),
    cpus = os.cpus().length || 2,
    exists = (p) => fs.existsSync(p),
    noteMs = NOTE_COALESCE_MS,
  } = deps;

  const events = new EventEmitter();
  /** helperId -> H: the helper as the lead and the clients see it (persisted on the helper's meta). */
  const helpers = new Map();
  /** leadId -> { waits: Set<Wait>, noteBuf: string[], noteTimer } */
  const leads = new Map();
  const timers = new Map(); // helperId -> minutes cap timeout
  let started = false;

  const config = () => ({ ...DEFAULTS, ...(store.getConfig().helpers || {}) });
  function caps() {
    const c = config();
    return { total: c.total || DEFAULTS.total, parallel: c.parallel || Math.max(1, Math.min(3, Math.floor(cpus / 2))), maxMinutes: c.maxMinutes || DEFAULTS.maxMinutes, costUsd: c.costUsd || 0 };
  }
  const leadOf = (leadId) => {
    let l = leads.get(leadId);
    if (!l) {
      l = { waits: new Set(), noteBuf: [], noteTimer: null };
      leads.set(leadId, l);
    }
    return l;
  };
  const helpersOf = (leadId) => [...helpers.values()].filter((h) => h.leadId === leadId && !h.removed).sort((a, b) => a.startedAt - b.startedAt);
  const byName = (leadId, name) => helpersOf(leadId).find((h) => h.name === name) || null;
  const absolute = (p) => slash(path.resolve(expandHome(String(p || '').trim()))).replace(/(?<=.)\/+$/, '');
  const leadMeta = (leadId) => store.getMeta(leadId) || {};

  // ---- persistence
  function persist(h) {
    const { id, timer: _t, ...rest } = h;
    store.setMeta(id, { helper: rest });
  }
  function persistLead(leadId, patch = {}) {
    const cur = leadMeta(leadId).lead || { since: now(), helpers: [], rules: [] };
    store.setMeta(leadId, { lead: { ...cur, ...patch, helpers: helpersOf(leadId).map((h) => h.id) } });
    bus.emit('list-changed'); // the sidebar nests helpers under their lead from sessions.list
  }
  /** What a client sees of a helper (HELPERS.md HelperState). */
  function publicState(h) {
    return {
      name: h.name,
      id: h.id,
      cwd: h.cwd,
      branch: h.worktree?.branch || null,
      worktree: h.worktree?.path || null,
      state: h.state,
      since: h.startedAt,
      elapsedMs: (h.endedAt || now()) - (h.launchedAt || h.startedAt),
      cost: Math.round((h.cost || 0) * 10000) / 10000,
      files: h.files || 0,
      add: h.add || 0,
      del: h.del || 0,
      pending: h.pending || 0,
      lastLine: h.lastLine || '',
      after: h.after || [],
      reason: h.reason || undefined,
      finished: h.finished || undefined,
      commit: h.commit || undefined,
      pr: h.pr || undefined,
    };
  }
  function emitLead(leadId) {
    const s = sessions.find?.(leadId);
    s?.emitState?.();
    events.emit('changed', { leadId, helpers: helpersOf(leadId).map(publicState) });
  }
  function setState(h, state, reason = null) {
    if (h.state === state && h.reason === reason) return false;
    h.state = state;
    h.reason = reason;
    h.rev = (h.rev || 0) + 1;
    if (TERMINAL.has(state)) {
      h.endedAt = now();
      clearTimeout(timers.get(h.id));
      timers.delete(h.id);
    }
    persist(h);
    return true;
  }

  // ---- what the lead's session can do
  function leadSessionOf(leadId) {
    return sessions.find?.(leadId) || null;
  }
  function modeFor(leadId, requested, folder) {
    const lead = leadSessionOf(leadId);
    const leadMode = lead?.mode || store.getConfig().defaultMode || 'default';
    const wanted = requested || registry?.defaultsFor(folder)?.mode || leadMode;
    const width = Math.min(MODE_WIDTH[wanted] ?? 1, MODE_WIDTH[leadMode] ?? 1);
    return Object.keys(MODE_WIDTH).find((k) => MODE_WIDTH[k] === width) || 'default';
  }
  function leadProject(leadId) {
    const m = leadMeta(leadId);
    const s = leadSessionOf(leadId);
    return slash(m.worktree?.repoRoot || m.project || m.cwd || s?.cwd || '');
  }
  const under = (dir, p) => Boolean(dir) && (p === dir || p.startsWith(dir.endsWith('/') ? dir : dir + '/'));

  /** The folder a start names, from `repo` (a registered name or alias, or a path) or `cwd`. */
  function resolveFolder({ repo, cwd }) {
    if (repo) {
      const hit = registry?.resolve(repo);
      if (hit) return hit.path;
      if (/[\\/~]/.test(repo)) return absolute(repo);
      const near = registry?.nearest(repo, 3) || [];
      throw new Error(`No repository called “${repo}”.${near.length ? ` Nearest names: ${near.join(', ')}.` : ''} Use list to see the folders known here, or give a path.`);
    }
    if (!cwd) throw new Error('A helper needs a folder: give repo (a registered name) or cwd (a path).');
    return absolute(cwd);
  }
  /** 'allow' when a start needs no card, 'never' when it is refused, null when the person decides. */
  function startPolicy(leadId, folder) {
    if (under(leadProject(leadId), folder)) return 'allow';
    const p = registry?.policyFor(folder);
    if (p === 'allowed') return 'allow';
    if (p === 'never') return 'never';
    return null;
  }

  // ---- the tools
  async function start(leadId, args = {}) {
    const c = config();
    if (!c.enabled) throw new Error('Helpers are turned off in Settings.');
    const lm = leadMeta(leadId);
    if (lm.helper) throw new Error('Helpers cannot start helpers. Report to your lead instead; it can start another.');
    const lead = leadSessionOf(leadId);
    if (lead?.unattended) throw new Error('This session runs unattended; it cannot start helpers.');
    const name = String(args.name || '').trim();
    if (!NAME_RE.test(name)) throw new Error('A helper needs a name: 1–32 characters of a-z, 0-9, - or _.');
    if (byName(leadId, name)) throw new Error(`A helper named ${name} exists; use another name, or send to it.`);
    const brief = String(args.brief || '').trim();
    if (!brief) throw new Error('A helper needs a brief: what to do, what done looks like, how to verify.');
    const folder = resolveFolder(args);
    if (folder === homeDir) throw new Error('Not the home folder itself; pick a repository or a project folder.');
    if (!exists(folder) || !fs.statSync(folder).isDirectory()) throw new Error(`The folder does not exist: ${folder}`);
    if (startPolicy(leadId, folder) === 'never') throw new Error(`${folder} is registered with policy Never (Settings → Repositories); nothing starts there from a lead.`);
    const after = Array.isArray(args.after) ? args.after.map(String) : [];
    for (const a of after) if (!byName(leadId, a)) throw new Error(`after names ${a}, which is not a helper of yours. Helpers: ${helpersOf(leadId).map((h) => h.name).join(', ') || 'none'}.`);
    const cp = caps();
    const mine = helpersOf(leadId);
    if (mine.length >= cp.total) throw new Error(`The cap is ${cp.total} helpers for this session; stop or finish one first.`);
    const cost = mine.reduce((s, h) => s + (h.cost || 0), 0);
    if (cp.costUsd && cost >= cp.costUsd) throw new Error(`This session's helpers have cost ${fmtCost(cost)}, past the cap of ${fmtCost(cp.costUsd)}.`);
    const defaults = registry?.defaultsFor(folder) || {};
    const info = await git.info(folder).catch(() => ({ isRepo: false }));
    const wantWorktree = args.worktree !== undefined ? Boolean(args.worktree) : defaults.worktree !== undefined ? Boolean(defaults.worktree) : Boolean(info.isRepo);
    if (wantWorktree && !info.isRepo) throw new Error(`${folder} is not a git repository, so it cannot have a worktree; start with worktree: false to work in the folder itself.`);
    const base = args.base || defaults.base || undefined;
    const maxMinutes = Math.max(1, Math.min(Number(args.maxMinutes) || cp.maxMinutes, cp.maxMinutes));
    const made = await sessions.create({
      cwd: folder,
      worktree: wantWorktree ? { name, base } : undefined,
      mode: modeFor(leadId, args.mode, folder),
      model: args.model || defaults.model || lead?.model || undefined,
      effort: args.effort || lead?.effort || undefined,
      title: name,
    });
    const h = {
      id: made.id,
      leadId,
      name,
      cwd: slash(made.cwd || folder),
      folder,
      worktree: made.worktree || null,
      brief,
      after,
      maxMinutes,
      startedAt: now(),
      launchedAt: null,
      endedAt: null,
      state: 'queued',
      reason: null,
      handoff: '',
      cost: 0,
      files: 0,
      add: 0,
      del: 0,
      pending: 0,
      lastLine: '',
      rev: 0,
      launched: false,
      noted: false,
    };
    helpers.set(h.id, h);
    const leadTitle = lm.title || lm.firstPrompt || '';
    store.setMeta(h.id, { helper: { ...h, leadTitle } });
    persistLead(leadId);
    const running = mine.filter((x) => ACTIVE.has(x.state));
    const waitingFor = after.filter((a) => byName(leadId, a)?.state !== 'done');
    let queuedBehind = [];
    if (waitingFor.length) queuedBehind = waitingFor;
    else if (running.length >= cp.parallel) queuedBehind = running.map((x) => x.name);
    if (!queuedBehind.length) await launch(h);
    else {
      h.reason = `waits for ${queuedBehind.join(', ')}`;
      persist(h);
      pump(leadId); // a dependency that already failed skips it now, not at the next event
    }
    emitLead(leadId);
    return { name: h.name, id: h.id, state: h.state, cwd: h.cwd, worktree: h.worktree ? { path: h.worktree.path, branch: h.worktree.branch } : null, ...(h.state === 'queued' && queuedBehind.length ? { queuedBehind } : {}), ...(h.reason && h.state !== 'queued' ? { reason: h.reason } : {}) };
  }

  /** Send the brief: the header, the brief, what the helpers it waited for did. */
  async function launch(h) {
    h.launched = true;
    h.launchedAt = now();
    setState(h, 'preparing');
    try {
      const session = await sessions.get(h.id);
      const deps_ = (h.after || []).map((a) => byName(h.leadId, a)).filter((d) => d && d.state === 'done');
      let text = HEADER + '\n\n' + h.brief;
      const notes = registry?.notesFor(h.folder || h.cwd);
      if (notes) text += `\n\n## About this repository\n${notes}`;
      if (deps_.length) {
        text += '\n\n## From helpers you waited for\n' + deps_.map((d) => `- ${d.name} (done, ${fmtDuration((d.endedAt || now()) - (d.launchedAt || d.startedAt))})${d.worktree ? `: worktree ${d.worktree.path} on branch ${d.worktree.branch}. Read the change there before you start.` : ''}\n${(d.handoff || 'no handoff').replace(/^/gm, '  ')}`).join('\n');
      }
      h.briefSent = text;
      setState(h, 'running');
      timers.set(
        h.id,
        setTimeout(() => {
          const s = sessions.find?.(h.id);
          h.stopping = `Stopped after ${h.maxMinutes} minutes`;
          persist(h);
          s?.interrupt?.().catch?.(() => null);
          setTimeout(() => {
            if (ACTIVE.has(h.state)) end(h, 'stopped', h.stopping);
          }, 3000);
        }, h.maxMinutes * 60e3),
      );
      await session.send({ text, from: 'lead' });
    } catch (e) {
      end(h, 'failed', e?.message || 'Could not start');
    }
  }

  /** A helper that was queued behind another: start it when its turn comes, or skip it with the reason. */
  function pump(leadId) {
    const cp = caps();
    for (const h of helpersOf(leadId)) {
      if (h.state !== 'queued') continue;
      const deps_ = (h.after || []).map((a) => byName(leadId, a));
      const bad = deps_.find((d) => !d || (TERMINAL.has(d.state) && d.state !== 'done'));
      if (bad) {
        setState(h, 'skipped', `${bad?.name || 'a helper it waited for'} ${bad ? bad.state : 'is gone'}`);
        note(leadId, h, 'skipped');
        continue;
      }
      if (!deps_.every((d) => d.state === 'done')) continue;
      if (helpersOf(leadId).filter((x) => ACTIVE.has(x.state)).length >= cp.parallel) continue;
      launch(h).catch((e) => log('helper launch failed', e?.message));
    }
  }

  function end(h, state, reason) {
    if (!setState(h, state, reason)) return;
    const leadId = h.leadId;
    // a wait that names this helper carries the news itself; the note is for a lead that was not waiting
    const covered = [...(leads.get(leadId)?.waits || [])].some((w) => w.names.has(h.name));
    collect(h)
      .catch(() => null)
      .finally(() => {
        emitLead(leadId);
        settleWaits(leadId);
        if (!covered) note(leadId, h, state);
        else h.noted = h.rev;
        pump(leadId);
        maybeReport(leadId);
      });
  }

  /** Files, additions and deletions of a helper's worktree against its base. */
  async function collect(h) {
    const dir = h.worktree?.path || h.cwd;
    if (!dir || !exists(dir)) return;
    try {
      const d = await git.diff(dir, { base: h.worktree?.base });
      const files = d.files || [];
      h.files = files.length;
      h.add = files.reduce((s, f) => s + Number(f.additions ?? f.add ?? 0), 0);
      h.del = files.reduce((s, f) => s + Number(f.deletions ?? f.del ?? 0), 0);
      h.fileList = files.slice(0, 200).map((f) => ({ path: f.path, add: Number(f.additions ?? f.add ?? 0), del: Number(f.deletions ?? f.del ?? 0) }));
      persist(h);
    } catch {
      /* not a repository, or gone */
    }
  }

  /** The text of the helper's last reply, whole (the session's lastReply is a one-line gist). */
  function lastAssistantText(session) {
    const msgs = session?.messages || [];
    const parts = [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.parent_tool_use_id) continue;
      const content = Array.isArray(m.message?.content) ? m.message.content : [];
      if (m.type === 'user' && !content.some((c) => c?.type === 'tool_result')) break;
      if (m.type === 'assistant') for (const c of content) if (c?.type === 'text' && c.text?.trim()) parts.unshift(c.text.trim());
    }
    return parts.join('\n\n') || session?.lastReply?.text || '';
  }

  // ---- the session bus
  function onState(st) {
    const h = helpers.get(st.id);
    if (h) {
      onHelperState(h, st);
      return;
    }
    const l = leads.get(st.id);
    if (l && l.pendingNotes?.length && st.live) {
      // the lead's process is back: what its helpers did while it was closed
      const text = l.pendingNotes.join('\n\n');
      l.pendingNotes = [];
      deliverNote(st.id, text).catch((e) => log('helper note failed', e?.message));
    }
  }
  function onHelperState(h, st) {
    if (h.removed) return;
    let changed = false;
    if (st.usage?.costUsd !== undefined && st.usage.costUsd !== h.cost) {
      h.cost = st.usage.costUsd;
      changed = true;
    }
    const pending = Array.isArray(st.pending) ? st.pending.length : 0;
    if (pending !== h.pending) {
      h.pending = pending;
      changed = true;
    }
    const line = st.activity?.label || (st.status === 'idle' ? h.lastLine : h.lastLine);
    if (line && line !== h.lastLine) {
      h.lastLine = line;
      changed = true;
    }
    if (h.launched) {
      if (st.status === 'requires_action' && h.state !== 'needs_action' && !TERMINAL.has(h.state)) changed = setState(h, 'needs_action') || changed;
      else if ((st.status === 'running' || st.status === 'starting') && (h.state === 'needs_action' || h.state === 'preparing' || h.state === 'done')) changed = setState(h, 'running') || changed;
      else if (st.status === 'idle' && ACTIVE.has(h.state)) {
        if (h.stopping) {
          const why = h.stopping;
          h.stopping = null;
          end(h, 'stopped', why);
        } else {
          h.handoff = extractHandoff(lastAssistantText(sessions.find?.(h.id)));
          persist(h);
          end(h, 'done', null);
        }
        return;
      } else if (st.status === 'error' && !TERMINAL.has(h.state)) {
        end(h, 'failed', (st.error || 'The process errored').split('\n')[0].slice(0, 200));
        return;
      }
    }
    if (changed) {
      persist(h);
      emitLead(h.leadId);
      if (h.state === 'needs_action') settleWaits(h.leadId, 'any');
    }
  }
  function onPermission({ id, request }) {
    const h = helpers.get(id);
    if (!h || h.removed) return;
    const rules = leadMeta(h.leadId).lead?.rules || [];
    const key = ruleKey(request, h.worktree?.path || h.cwd);
    if (rules.some((r) => r.key === key)) {
      const s = sessions.find?.(id);
      s?.respond?.(request.requestId, { behavior: 'allow' });
    }
  }
  function onRemoved({ id, meta }) {
    const h = helpers.get(id);
    if (h) {
      h.removed = true;
      clearTimeout(timers.get(id));
      timers.delete(id);
      helpers.delete(id);
      persistLead(h.leadId);
      emitLead(h.leadId);
      settleWaits(h.leadId);
      if (!TERMINAL.has(h.state) || h.state === 'done') queueNote(h.leadId, `<helper-note name="${h.name}" state="removed">The person removed the helper ${h.name}${h.worktree ? ' and its worktree' : ''}.</helper-note>`);
      pump(h.leadId);
      return;
    }
    if (meta?.lead) {
      // the lead is gone: its helpers stay as sessions, with the lead's name on them
      for (const x of helpersOf(id)) {
        x.leadGone = true;
        persist(x);
      }
      leads.delete(id);
    }
  }

  // ---- notes to the lead
  function note(leadId, h, state) {
    if (h.noted === h.rev) return;
    h.noted = h.rev;
    const elapsed = fmtDuration((h.endedAt || now()) - (h.launchedAt || h.startedAt));
    const attrs = `name="${h.name}" state="${state}" elapsed="${elapsed}" cost="${fmtCost(h.cost)}" files="${h.files || 0}" add="${h.add || 0}" del="${h.del || 0}"`;
    let body = '';
    if (state === 'done') body = h.handoff || 'No handoff; the helper ended without a reply.';
    else body = h.reason || state;
    queueNote(leadId, `<helper-note ${attrs}>\n${body}\n</helper-note>`);
  }
  function queueNote(leadId, text) {
    const l = leadOf(leadId);
    l.noteBuf.push(text);
    clearTimeout(l.noteTimer);
    l.noteTimer = setTimeout(() => flushNotes(leadId), noteMs);
  }
  async function flushNotes(leadId) {
    const l = leadOf(leadId);
    l.noteTimer = null;
    if (!l.noteBuf.length) return;
    const still = helpersOf(leadId).filter((h) => ACTIVE.has(h.state) || h.state === 'queued');
    const tail = still.length ? `Helpers still going: ${still.map((h) => `${h.name} (${h.state})`).join(', ')}.` : 'No helpers are running now.';
    const text = l.noteBuf.join('\n\n') + `\n\n${tail} Read more with the helpers read tool.`;
    l.noteBuf = [];
    const s = sessions.find?.(leadId);
    const live = Boolean(s?.q);
    if (!live && !config().wakeLead) {
      l.pendingNotes = [...(l.pendingNotes || []), text];
      return;
    }
    await deliverNote(leadId, text).catch((e) => log('helper note failed', e?.message));
  }
  async function deliverNote(leadId, text) {
    const s = await sessions.get(leadId);
    await s.send({ text, note: 'helper' });
  }

  // ---- wait
  function settleWaits(leadId, trigger = 'change') {
    const l = leads.get(leadId);
    if (!l) return;
    for (const w of [...l.waits]) {
      const mine = helpersOf(leadId).filter((h) => w.names.has(h.name));
      const gone = [...w.names].filter((n) => !mine.some((h) => h.name === n));
      const done = w.until === 'any' ? mine.some((h) => (h.rev || 0) !== w.revs.get(h.name)) || gone.length > 0 : mine.every((h) => TERMINAL.has(h.state)) || gone.length === w.names.size;
      if (done) {
        l.waits.delete(w);
        clearTimeout(w.timer);
        w.resolve({ timedOut: false, helpers: mine.map(publicState) });
      }
    }
    void trigger;
  }
  function wait(leadId, args = {}) {
    const all = helpersOf(leadId);
    if (!all.length) throw new Error('No helpers to wait for. Start one first.');
    const names = Array.isArray(args.names) && args.names.length ? args.names.map(String) : all.map((h) => h.name);
    for (const n of names) if (!byName(leadId, n)) throw new Error(`No helper called ${n}. Helpers: ${all.map((h) => h.name).join(', ')}.`);
    const until = args.until === 'any' ? 'any' : 'done';
    const timeoutSec = Math.max(1, Math.min(Number(args.timeoutSec) || WAIT_DEFAULT_S, WAIT_MAX_S));
    const l = leadOf(leadId);
    return new Promise((resolve) => {
      const w = { names: new Set(names), until, resolve, revs: new Map(all.map((h) => [h.name, h.rev || 0])), timer: null };
      w.timer = setTimeout(() => {
        l.waits.delete(w);
        resolve({ timedOut: true, helpers: helpersOf(leadId).filter((h) => w.names.has(h.name)).map(publicState) });
      }, timeoutSec * 1000);
      l.waits.add(w);
      settleWaits(leadId);
    });
  }

  // ---- read, send, stop, list
  async function read(leadId, args = {}) {
    const h = byName(leadId, String(args.name || ''));
    if (!h) throw new Error(`No helper called ${args.name}. Helpers: ${helpersOf(leadId).map((x) => x.name).join(', ') || 'none'}.`);
    const what = args.what || 'summary';
    const maxChars = Math.max(200, Math.min(Number(args.maxChars) || READ_DEFAULT, READ_MAX));
    const cut = (t) => ({ text: t.length > maxChars ? t.slice(0, maxChars - 1) + '…' : t, truncated: t.length > maxChars });
    const base = { name: h.name, state: h.state };
    if (what === 'summary') return { ...base, ...cut(h.handoff || (TERMINAL.has(h.state) ? h.reason || 'No handoff.' : `Still ${h.state}; nothing to summarize yet.`)) };
    const session = sessions.find?.(h.id) || (await sessions.get(h.id).catch(() => null));
    if (what === 'last') return { ...base, ...cut(lastAssistantText(session) || 'Nothing said yet.') };
    if (what === 'files') {
      await collect(h);
      const list = h.fileList || [];
      return { ...base, ...cut(list.length ? list.map((f) => `${f.path} +${f.add} −${f.del}`).join('\n') : 'No changed files.'), files: list.length };
    }
    if (what === 'diff') {
      const dir = h.worktree?.path || h.cwd;
      const d = await git.diff(dir, { base: h.worktree?.base }).catch(() => ({ files: [] }));
      const r = renderDiff(d.files || [], { maxChars, only: args.path ? String(args.path) : '' });
      return { ...base, text: r.text || 'No changes.', truncated: r.truncated, files: r.files, ...(r.truncated ? { more: 'Ask for one file with path, or raise maxChars.' } : {}) };
    }
    if (what === 'steps') {
      const lines = [];
      for (const m of session?.messages || []) {
        if (m.parent_tool_use_id) continue;
        const content = Array.isArray(m.message?.content) ? m.message.content : [];
        if (m.type === 'assistant') for (const c of content) lines.push(c?.type === 'tool_use' ? `TOOL ${c.name} ${JSON.stringify(c.input || {}).slice(0, 120)}` : c?.type === 'text' && c.text?.trim() ? `SAID ${c.text.trim().slice(0, 300)}` : '');
        else if (m.type === 'user' && !content.some((c) => c?.type === 'tool_result')) lines.push(`GOT ${textOfContent(content).slice(0, 200)}`);
      }
      const text = lines.filter(Boolean).join('\n');
      return { ...base, ...cut(text.length > maxChars ? '…\n' + text.slice(-maxChars + 2) : text || 'No steps yet.') };
    }
    throw new Error('what must be summary, last, files, diff or steps.');
  }
  async function send(leadId, args = {}) {
    const h = byName(leadId, String(args.name || ''));
    if (!h) throw new Error(`No helper called ${args.name}.`);
    const text = String(args.text || '').trim();
    if (!text) throw new Error('Nothing to send.');
    if (h.state === 'queued') throw new Error(`${h.name} has not started yet (${h.reason}); edit its brief by stopping it and starting again.`);
    const session = await sessions.get(h.id);
    const busy = Boolean(session.isBusy?.());
    await session.send({ text, now: Boolean(args.now), from: 'lead' });
    return { name: h.name, state: h.state, delivered: busy && !args.now ? 'held' : 'now' };
  }
  async function stop(leadId, args = {}) {
    const h = byName(leadId, String(args.name || ''));
    if (!h) throw new Error(`No helper called ${args.name}.`);
    if (TERMINAL.has(h.state) && h.state !== 'done') return { name: h.name, state: h.state };
    const s = sessions.find?.(h.id);
    if (h.state === 'queued' || h.state === 'done' || !s?.isBusy?.()) {
      end(h, 'stopped', 'stopped by the lead');
      return { name: h.name, state: h.state };
    }
    h.stopping = 'stopped by the lead';
    persist(h);
    await s.interrupt();
    return { name: h.name, state: 'stopped' };
  }
  function known(leadId) {
    const out = new Map();
    const add = (p, more) => {
      const key = slash(p).replace(/\/+$/, '');
      if (!key || out.has(key)) return;
      out.set(key, { path: key, name: path.basename(key), ...more });
    };
    for (const e of registry?.list?.() || []) if (!e.missing) add(e.path, { name: e.name, registered: true, policy: e.policy, about: e.about || '', allowed: e.policy === 'allowed' });
    const lp = leadProject(leadId);
    if (lp) add(lp, { registered: out.has(lp), allowed: true, about: out.get(lp)?.about || 'this session’s project' });
    for (const p of store.getConfig().recentProjects || []) if (exists(p)) add(p, { registered: false, allowed: startPolicy(leadId, slash(p)) === 'allow' });
    if (lp && exists(lp)) for (const d of findRepos(lp, 1)) add(d, { registered: false, allowed: startPolicy(leadId, slash(d)) === 'allow' });
    return [...out.values()];
  }
  function list(leadId) {
    const mine = helpersOf(leadId);
    return { helpers: mine.map(publicState), caps: caps(), cost: Math.round(mine.reduce((s, h) => s + (h.cost || 0), 0) * 10000) / 10000, known: known(leadId) };
  }

  // ---- what the clients ask for
  function allow(leadId, requestId) {
    let key = null;
    let label = '';
    for (const h of helpersOf(leadId)) {
      const s = sessions.find?.(h.id);
      const p = s?.pending?.get?.(requestId);
      if (p) {
        key = ruleKey(p.req, h.worktree?.path || h.cwd);
        label = ruleLabel(p.req, h.worktree?.path || h.cwd);
        break;
      }
    }
    if (!key) throw new Error('That request is gone.');
    const cur = leadMeta(leadId).lead || {};
    const rules = [...(cur.rules || [])];
    if (!rules.some((r) => r.key === key)) rules.push({ key, label, at: now() });
    persistLead(leadId, { rules });
    let answered = 0;
    for (const h of helpersOf(leadId)) {
      const s = sessions.find?.(h.id);
      for (const [rid, p] of s?.pending || []) {
        if (ruleKey(p.req, h.worktree?.path || h.cwd) === key) {
          s.respond(rid, { behavior: 'allow' });
          answered++;
        }
      }
    }
    emitLead(leadId);
    return { key, label, answered };
  }
  function stateOf(session) {
    const id = session.id;
    const m = store.getMeta(id) || {};
    const out = {};
    if (m.lead || helpersOf(id).length) {
      const mine = helpersOf(id);
      out.helpers = mine.map(publicState);
      out.lead = { since: m.lead?.since || null, cost: Math.round(mine.reduce((s, h) => s + (h.cost || 0), 0) * 10000) / 10000, caps: caps(), rules: (m.lead?.rules || []).map((r) => r.label) };
    }
    if (m.helper) out.helper = { leadId: m.helper.leadId, leadTitle: store.getMeta(m.helper.leadId)?.title || m.helper.leadTitle || '', name: m.helper.name, state: helpers.get(id)?.state || m.helper.state || null };
    return out;
  }
  async function review(leadId) {
    const diffs = [];
    for (const h of helpersOf(leadId)) {
      const dir = h.worktree?.path;
      if (!dir || !exists(dir) || h.finished === 'discard' || h.finished === 'commit') continue;
      try {
        const d = await git.diff(dir, { base: h.worktree.base });
        diffs.push({ runId: h.id, name: h.name, files: d.files || [] });
      } catch {
        /* skip */
      }
    }
    return { id: leadId, ...groupHunks(diffs) };
  }
  async function finish(leadId, { name, action, message, pr } = {}) {
    if (!['commit', 'discard', 'keep', 'pr'].includes(action)) throw new Error('Finish with commit, pr, discard or keep.');
    const mine = helpersOf(leadId).filter((h) => (!name || h.name === name) && TERMINAL.has(h.state) && !h.finished);
    const results = [];
    for (const h of mine) {
      const res = { name: h.name, ok: true, output: '' };
      try {
        if (action === 'keep') h.finished = 'keep';
        else if (!h.worktree?.path || !exists(h.worktree.path)) {
          h.finished = action;
          res.output = 'No worktree.';
        } else if (action === 'discard') {
          const r = await git.removeWorktree({ repoRoot: h.worktree.repoRoot, dir: h.worktree.path, branch: h.worktree.branch, deleteBranch: true });
          res.ok = r.ok !== false;
          res.output = r.output || '';
          h.finished = 'discard';
        } else {
          const msg = (message || '').trim() || `${leadMeta(leadId).title || 'Helper'}: ${h.name}`;
          const c = await git.commit(h.worktree.path, { message: msg });
          if (!c.ok && !/nothing to commit|nothing added/i.test(c.output || '')) throw new Error(c.output || 'commit failed');
          const head = await git.git(h.worktree.path, ['rev-parse', '--short', 'HEAD']);
          h.commit = (head.stdout || '').trim() || null;
          if (action === 'pr' || pr) {
            const url = await openPr(leadId, h).catch((e) => {
              h.prError = e?.message || 'Could not open a pull request';
              return null;
            });
            if (url) h.pr = url;
          }
          const r = await git.removeWorktree({ repoRoot: h.worktree.repoRoot, dir: h.worktree.path, branch: h.worktree.branch, deleteBranch: false });
          res.output = r.output || '';
          h.finished = 'commit';
        }
        if (action !== 'keep') sessions.find?.(h.id)?.close?.();
      } catch (e) {
        res.ok = false;
        res.output = e?.message || String(e);
        h.finishError = res.output;
      }
      persist(h);
      results.push(res);
    }
    emitLead(leadId);
    maybeReport(leadId, true);
    return { results };
  }
  async function openPr(leadId, h) {
    const gh = which('gh');
    if (!gh || !exec) throw new Error('gh is not on the path.');
    const wt = h.worktree;
    const push = await git.git(wt.path, ['push', '-u', 'origin', wt.branch]);
    if (push.code) throw new Error((push.stderr || push.stdout || 'push failed').trim());
    const siblings = helpersOf(leadId).filter((x) => x.id !== h.id && x.pr);
    const body = `${h.brief}\n\n---\n${h.handoff || ''}${siblings.length ? `\n\nRelated pull requests from the same lead:\n${siblings.map((s) => `- ${s.name}: ${s.pr}`).join('\n')}` : ''}\n\nOpened by Pilcrow (helper “${h.name}” of “${leadMeta(leadId).title || 'a lead session'}”).`;
    const r = await exec(gh, ['pr', 'create', '--head', wt.branch, '--title', `${leadMeta(leadId).title || 'Helper'}: ${h.name}`, '--body', body], { cwd: wt.repoRoot, timeout: 60_000 });
    if (r.code) throw new Error((r.stderr || r.stdout || 'gh pr create failed').trim());
    return (r.stdout || '').trim().split('\n').pop() || null;
  }
  /** One inbox item per lead once every helper has reached a terminal state and one changed files; updated, not duplicated. */
  function maybeReport(leadId, force = false) {
    const mine = helpersOf(leadId);
    if (!mine.length || !mine.every((h) => TERMINAL.has(h.state))) return;
    if (!force && !mine.some((h) => (h.files || 0) > 0)) return;
    const lm = leadMeta(leadId);
    const title = lm.title || lm.firstPrompt || 'A lead session';
    const done = mine.filter((h) => h.state === 'done').length;
    const lines = mine.map((h) => `- ${h.name}: ${h.state}${h.reason ? ` (${h.reason})` : ''}; ${h.files || 0} files (+${h.add || 0} −${h.del || 0})${h.handoff ? ` — ${h.handoff.replace(/\s+/g, ' ').slice(0, 160)}` : ''}`);
    const item = {
      kind: 'helpers',
      leadId,
      sessionId: leadId,
      scheduleName: title,
      cwd: leadProject(leadId),
      startedAt: Math.min(...mine.map((h) => h.startedAt)),
      endedAt: Math.max(...mine.map((h) => h.endedAt || now())),
      durationMs: Math.max(...mine.map((h) => h.endedAt || now())) - Math.min(...mine.map((h) => h.launchedAt || h.startedAt)),
      status: done === mine.length ? 'completed' : done ? 'completed' : 'failed',
      reason: done === mine.length ? null : `${mine.length - done} of ${mine.length} did not finish`,
      summary: `${done} of ${mine.length} helper${mine.length === 1 ? '' : 's'} done.\n${lines.join('\n')}`.slice(0, 1200),
      cost: Math.round(mine.reduce((s, h) => s + (h.cost || 0), 0) * 10000) / 10000,
      helpers: mine.map((h) => ({ ...publicState(h), handoff: h.handoff || '' })),
      title,
    };
    const existing = lm.lead?.inboxId;
    if (existing && updateInbox(existing, item)) return;
    const id = pushInbox({ ...item, id: uuid() });
    persistLead(leadId, { inboxId: id });
  }
  function exportMarkdown(leadId) {
    const lm = leadMeta(leadId);
    const mine = helpersOf(leadId);
    const lines = [`# ${lm.title || 'Lead session'}: helpers`, ''];
    for (const h of mine) {
      lines.push(`## ${h.name} — ${h.state}${h.reason ? ` (${h.reason})` : ''}`, '', `Folder: ${h.cwd}${h.worktree ? ` · branch ${h.worktree.branch}` : ''} · ${h.files || 0} files (+${h.add || 0} −${h.del || 0}) · ${fmtCost(h.cost)} · ${fmtDuration((h.endedAt || now()) - (h.launchedAt || h.startedAt))}${h.commit ? ` · commit ${h.commit}` : ''}${h.pr ? ` · ${h.pr}` : ''}`, '');
      if (h.handoff) lines.push(h.handoff, '');
    }
    const rules = lm.lead?.rules || [];
    if (rules.length) lines.push('## Allowed for all helpers', '', ...rules.map((r) => `- ${r.label}`), '');
    return lines.join('\n').trim() + '\n';
  }
  function detach(helperId) {
    const h = helpers.get(helperId);
    if (!h) return false;
    const leadId = h.leadId;
    helpers.delete(helperId);
    clearTimeout(timers.get(helperId));
    store.setMeta(helperId, { helper: null, title: store.getMeta(helperId)?.title || h.name });
    persistLead(leadId);
    queueNote(leadId, `<helper-note name="${h.name}" state="detached">The person detached ${h.name}; it is an ordinary session now.</helper-note>`);
    emitLead(leadId);
    return true;
  }
  function helperIds(leadId) {
    return helpersOf(leadId).map((h) => h.id);
  }

  // ---- the extension points sessions.js offers
  /** Pilcrow's own decisions on the helpers tools: read-only ones never ask; a start in an allowed folder does not either. */
  function permission(session, toolName, input) {
    if (!toolName.startsWith(TOOL_PREFIX)) return null;
    if (toolName !== `${TOOL_PREFIX}start`) return { behavior: 'allow', updatedInput: input };
    if (session.unattended) return null;
    try {
      const folder = resolveFolder(input || {});
      const p = startPolicy(session.id, folder);
      if (p === 'allow') return { behavior: 'allow', updatedInput: input };
      if (p === 'never') return { behavior: 'deny', message: `${folder} is registered with policy Never; nothing starts there from a lead.` };
    } catch {
      /* the handler says why */
    }
    return null;
  }
  /** *Always allow* on a start card registers the folder as Allowed (REPOSITORIES.md) instead of allowing every start for ever. */
  function respond(session, req, decision) {
    if (req?.toolName !== `${TOOL_PREFIX}start` || decision?.behavior !== 'allow' || !decision.updatedPermissions?.length) return decision;
    try {
      const folder = resolveFolder(req.input || {});
      registry?.register?.(folder, { policy: 'allowed' })?.catch?.((e) => log('register from Always allow failed', e?.message));
    } catch {
      /* nothing to register */
    }
    const { updatedPermissions: _u, ...rest } = decision;
    return rest;
  }
  /** The MCP server a session gets (null when helpers are off). */
  function serverFor(session, mcp) {
    if (!config().enabled || !mcp) return null;
    const { createSdkMcpServer, tool, z } = mcp;
    const leadId = session.id;
    const wrap = (fn) => async (args) => {
      try {
        const r = await fn(leadId, args || {});
        const text = typeof r === 'string' ? r : JSON.stringify(r, null, 2);
        return { content: [{ type: 'text', text }], ...(r && typeof r === 'object' ? { structuredContent: r } : {}) };
      } catch (e) {
        return { content: [{ type: 'text', text: e?.message || String(e) }], isError: true };
      }
    };
    const ro = { annotations: { readOnlyHint: true } };
    return createSdkMcpServer({
      name: 'helpers',
      version: '1.0.0',
      instructions: instructions(caps()),
      timeout: (WAIT_MAX_S + 30) * 1000,
      tools: [
        tool(
          'start',
          'Start a helper: a separate Pilcrow session in a folder or registered repository, with its own brief and worktree. Returns its name, id and state (running, or queued behind helpers named in after or behind the parallel cap).',
          {
            name: z.string().describe('1–32 characters of a-z, 0-9, - or _; unique among your helpers'),
            brief: z.string().describe('What to do, what done looks like, how to verify, what not to touch'),
            repo: z.string().optional().describe('A registered repository by name or alias, or a path; or give cwd'),
            cwd: z.string().optional().describe('The folder to work in'),
            worktree: z.boolean().optional().describe('Work on a branch in a worktree (default: yes in a git repository)'),
            base: z.string().optional().describe('The branch the worktree starts from (default: the checkout’s)'),
            after: z.array(z.string()).optional().describe('Names of your helpers this one waits for; their handoffs are appended to its brief'),
            mode: z.enum(['default', 'acceptEdits', 'plan']).optional().describe('Never wider than your own mode'),
            model: z.string().optional(),
            effort: z.string().optional(),
            maxMinutes: z.number().optional().describe('Up to the configured cap'),
          },
          wrap(start),
        ),
        tool(
          'wait',
          'Wait, spending no tokens, until helpers are done (or until the first change with until: any). Returns a check-in at the timeout; call again then. A helper waiting on a permission is waiting on the person, not on you.',
          { names: z.array(z.string()).optional().describe('Default: all your helpers'), until: z.enum(['done', 'any']).optional(), timeoutSec: z.number().optional().describe(`Up to ${WAIT_MAX_S}; default ${WAIT_DEFAULT_S}`) },
          wrap(wait),
          ro,
        ),
        tool(
          'read',
          'Read what a helper did: summary (its Handoff), last (its last reply), files (changed files with +/−), diff (unified, cut per file at the budget; path for one file), steps (a digest of its turn).',
          { name: z.string(), what: z.enum(['summary', 'last', 'files', 'diff', 'steps']).optional(), path: z.string().optional(), maxChars: z.number().optional().describe(`Default ${READ_DEFAULT}, up to ${READ_MAX}`) },
          wrap(read),
          ro,
        ),
        tool('send', 'Send a helper more instructions. now: interrupt its step and deliver when the turn ends.', { name: z.string(), text: z.string(), now: z.boolean().optional() }, wrap(send)),
        tool('stop', 'Interrupt a helper. Its session and worktree stay; the person can continue it by hand.', { name: z.string() }, wrap(stop)),
        tool('list', 'Your helpers with their states, the caps, the cost so far, and the folders known here (which start without asking).', {}, wrap(list), ro),
      ],
    });
  }

  // ---- startup: what the registry knows from before
  function begin() {
    if (started) return;
    started = true;
    for (const [id, meta] of Object.entries(store.meta || {})) {
      const m = meta?.helper;
      if (!m || !m.leadId) continue;
      const h = { ...m, id, timer: null, removed: false };
      if (!TERMINAL.has(h.state)) {
        h.state = 'stopped';
        h.reason = 'server restarted';
        h.endedAt = h.endedAt || now();
        h.restartNote = true;
      }
      helpers.set(id, h);
    }
    for (const h of helpers.values()) {
      if (h.restartNote) {
        delete h.restartNote;
        persist(h);
        note(h.leadId, h, 'stopped');
      }
    }
    bus.on('state', onState);
    bus.on('permission', onPermission);
    bus.on('removed', onRemoved);
  }
  function endAll() {
    bus.off('state', onState);
    bus.off('permission', onPermission);
    bus.off('removed', onRemoved);
    for (const t of timers.values()) clearTimeout(t);
    timers.clear();
    for (const l of leads.values()) {
      clearTimeout(l.noteTimer);
      for (const w of l.waits) clearTimeout(w.timer);
    }
    started = false;
  }

  return { events, begin, end: endAll, start, wait, read, send, stop, list, known, allow, review, finish, exportMarkdown, detach, helperIds, stateOf, permission, respond, serverFor, caps, flushNotes, _helpers: helpers };
}

// ---------------------------------------------------------------- the server's instance
import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import { store } from './config.js';
import { sessions, bus, extensions, textOfContent } from './sessions.js';
import * as git from './git.js';
import { pushInbox, updateInbox } from './schedules.js';
import { repos } from './repos.js';
import { expandHome, log, slash, run, which } from './util.js';

export const helpers = createHelpers({ sessions, bus, store, git, registry: repos, pushInbox, updateInbox, which, run, log, slash, expandHome, textOfContent });
export const events = helpers.events;

/** Wire the instance into sessions.js: the MCP server, the permission decisions, the state fields. */
export function start() {
  helpers.begin();
  extensions.mcpServers.push((session) => helpers.serverFor(session, { createSdkMcpServer, tool, z }));
  extensions.permission.push(helpers.permission);
  extensions.respond.push(helpers.respond);
  extensions.state.push(helpers.stateOf);
}
