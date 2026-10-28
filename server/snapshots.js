// Time machine: a shadow git repository per folder records the working tree after every step that can
// change files (a tool call, a message from the person), so the conversation has a timeline you can
// scrub through and restore from. The project's own .git is never touched: the shadow repository
// lives in Pilcrow's state folder and uses the folder only as its work tree.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';
import { run, xdg, slash, log, readJson, writeJson, privateDir, HOME } from './util.js';
import { parseUnifiedDiff } from './git.js';

export const events = new EventEmitter();

const ROOT = path.join(xdg('data'), 'timeline');
const EXCLUDES = ['.git/', 'node_modules/', '.venv/', 'venv/', '__pycache__/', '*.pyc', '.pytest_cache/', '.mypy_cache/', '.ruff_cache/', '.cache/', '.gradle/', '.idea/', '.vs/', 'target/debug/', 'target/release/', '.terraform/', '.pilcrow/'];
const MAX_STEPS = 600;
const SLOW_MS = 12_000;

/** Tools whose completion may have changed files. MCP tools are opaque, so they count too. */
export const FILE_TOOLS = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'PowerShell']);

const keyOf = (cwd) => crypto.createHash('sha1').update(slash(cwd)).digest('hex').slice(0, 16);
const dirOf = (cwd) => path.join(ROOT, keyOf(cwd));
const stepsFile = (cwd, sid) => path.join(dirOf(cwd), `steps-${sid}.json`);

const ready = new Map(); // cwd -> Promise<boolean>
const queues = new Map(); // cwd -> Promise chain, so two sessions in one folder never race
const disabled = new Map(); // cwd -> reason
const cache = new Map(); // sid -> steps[]

function sg(cwd, args, opts = {}) {
  return run('git', ['-c', 'core.quotePath=false', '-c', 'color.ui=false', '-c', 'core.autocrlf=false', '-c', 'user.name=Pilcrow', '-c', 'user.email=pilcrow@localhost', '--git-dir', dirOf(cwd), '--work-tree', cwd, ...args], {
    cwd,
    timeout: 90_000,
    env: { ...process.env, GIT_INDEX_FILE: path.join(dirOf(cwd), 'index'), GIT_TERMINAL_PROMPT: '0' },
    ...opts,
  });
}

function unsuitable(cwd) {
  if (!cwd) return 'no folder';
  const c = slash(path.resolve(cwd));
  if (c === slash(HOME)) return 'home folder';
  if (/^([a-z]:)?\/?$/i.test(c)) return 'root of a drive';
  if (c.split('/').filter(Boolean).length < 2) return 'too close to the root';
  return null;
}

async function ensure(cwd) {
  if (disabled.has(cwd)) return false;
  if (!ready.has(cwd)) {
    ready.set(
      cwd,
      (async () => {
        const why = unsuitable(cwd);
        if (why) {
          disabled.set(cwd, why);
          return false;
        }
        const dir = dirOf(cwd);
        if (!fs.existsSync(path.join(dir, 'HEAD'))) {
          privateDir(ROOT);
          const r = await run('git', ['init', '-q', '--bare', dir], { timeout: 20_000 });
          if (r.code !== 0) {
            disabled.set(cwd, 'git init failed: ' + r.stderr.trim());
            return false;
          }
          fs.writeFileSync(path.join(dir, 'config'), `[core]\n\tbare = false\n\trepositoryformatversion = 0\n\tfilemode = false\n\tlogallrefupdates = false\n[gc]\n\tauto = 0\n`);
          fs.mkdirSync(path.join(dir, 'info'), { recursive: true });
          fs.writeFileSync(path.join(dir, 'info', 'exclude'), EXCLUDES.join('\n') + '\n');
          writeJson(path.join(dir, 'folder.json'), { cwd: slash(cwd), createdAt: Date.now() });
        }
        return true;
      })(),
    );
  }
  return ready.get(cwd);
}

function loadSteps(cwd, sid) {
  if (!cache.has(sid)) cache.set(sid, readJson(stepsFile(cwd, sid), []));
  return cache.get(sid);
}

function saveSteps(cwd, sid, steps) {
  cache.set(sid, steps);
  writeJson(stepsFile(cwd, sid), steps);
}

async function statsBetween(cwd, from, to) {
  const [ns, st] = await Promise.all([sg(cwd, ['diff-tree', '-r', '--numstat', from, to]), sg(cwd, ['diff-tree', '-r', '--name-status', from, to])]);
  const status = new Map();
  for (const line of st.stdout.split('\n')) {
    const [s, p] = line.split('\t');
    if (s && p) status.set(p, s[0]);
  }
  const files = [];
  for (const line of ns.stdout.split('\n')) {
    const [a, d, p] = line.split('\t');
    if (p === undefined) continue;
    files.push({ path: p, status: status.get(p) || 'M', add: a === '-' ? 0 : +a, del: d === '-' ? 0 : +d, binary: a === '-' });
  }
  files.sort((x, y) => x.path.localeCompare(y.path));
  return files;
}

/**
 * Record the working tree as it is now. Returns the new step, or null when nothing changed (for a
 * message or the start of a session a marker step is still recorded, pointing at the same tree).
 * kind: 'start' | 'message' | 'tool' | 'restore'
 */
export function record(session, { kind, tool, toolUseId, label, uuid: msgUuid, onlyIfChanged }) {
  const cwd = session.cwd;
  const prev = queues.get(cwd) || Promise.resolve();
  const p = prev
    .then(() => take(session, { kind, tool, toolUseId, label, uuid: msgUuid, onlyIfChanged }))
    .catch((e) => {
      log('timeline failed', session.id, e?.message);
      return null;
    });
  queues.set(cwd, p);
  return p;
}

async function take(session, { kind, tool, toolUseId, label, uuid: msgUuid, onlyIfChanged }) {
  const cwd = session.cwd;
  const sid = session.id;
  if (!(await ensure(cwd))) return null;
  const t0 = Date.now();
  const add = await sg(cwd, ['add', '-A', '--ignore-errors', '--', '.']);
  if (add.code !== 0 && !/warning/i.test(add.stderr)) {
    log('timeline add failed', sid, add.stderr.trim().slice(0, 300));
    if (/fatal: (not a git repository|Unable to create)/.test(add.stderr)) disabled.set(cwd, add.stderr.trim().slice(0, 120));
    return null;
  }
  if (Date.now() - t0 > SLOW_MS) {
    disabled.set(cwd, `indexing this folder takes too long (${Math.round((Date.now() - t0) / 1000)}s)`);
    log('timeline disabled for', cwd, disabled.get(cwd));
    return null;
  }
  const wt = await sg(cwd, ['write-tree']);
  if (wt.code !== 0) return null;
  const tree = wt.stdout.trim();
  const steps = loadSteps(cwd, sid);
  const last = steps[steps.length - 1];
  let commit = last?.commit || null;
  let files = [];
  if (!last || last.tree !== tree) {
    const args = ['commit-tree', tree, '-m', `${kind}: ${label || ''}`.slice(0, 200)];
    if (last?.commit) args.push('-p', last.commit);
    const ct = await sg(cwd, args);
    if (ct.code !== 0) return null;
    commit = ct.stdout.trim();
    await sg(cwd, ['update-ref', `refs/sessions/${sid}`, commit]);
    if (last?.commit) files = await statsBetween(cwd, last.commit, commit);
  }
  const changed = Boolean(last) && last.tree !== tree;
  if (onlyIfChanged && !changed) return null;
  // A message or session start that follows changes nobody recorded means the person (or another
  // program) edited files between turns: say so instead of attributing it to the message.
  const external = changed && (kind === 'message' || kind === 'start');
  const step = {
    n: (last?.n || 0) + 1,
    at: Date.now(),
    kind: external ? 'external' : kind,
    tool: tool || null,
    toolUseId: toolUseId || null,
    uuid: msgUuid || null,
    label: label || '',
    commit,
    tree,
    changed,
    files,
    add: files.reduce((s, f) => s + f.add, 0),
    del: files.reduce((s, f) => s + f.del, 0),
  };
  // An external-edit marker is followed by the message marker itself, so the timeline still shows
  // where the message went out.
  const out = [step];
  if (external) out.push({ ...step, n: step.n + 1, kind, changed: false, files: [], add: 0, del: 0 });
  steps.push(...out);
  if (steps.length > MAX_STEPS) steps.splice(0, steps.length - MAX_STEPS);
  saveSteps(cwd, sid, steps);
  for (const s of out) events.emit('step', { id: sid, step: publicStep(s) });
  return out[out.length - 1];
}

function publicStep(s) {
  const { tree, ...rest } = s;
  return rest;
}

export function steps(session) {
  const cwd = session.cwd;
  return { cwd: slash(cwd), enabled: !disabled.has(cwd), reason: disabled.get(cwd) || null, steps: loadSteps(cwd, session.id).map(publicStep) };
}

function find(session, n) {
  return loadSteps(session.cwd, session.id).find((s) => s.n === n) || null;
}

/** Unified diff between two steps, or between a step and the working tree now (`to` omitted). */
export async function diff(session, { from, to }) {
  const cwd = session.cwd;
  if (!(await ensure(cwd))) return { files: [] };
  const a = typeof from === 'number' ? find(session, from)?.commit : from;
  if (!a) return { files: [] };
  let r;
  if (to === undefined || to === null || to === 'now') {
    await sg(cwd, ['add', '-A', '--ignore-errors', '--', '.']);
    r = await sg(cwd, ['diff', '--no-ext-diff', '--find-renames', '-U3', '--cached', a, '--']);
  } else {
    const b = typeof to === 'number' ? find(session, to)?.commit : to;
    if (!b) return { files: [] };
    r = await sg(cwd, ['diff', '--no-ext-diff', '--find-renames', '-U3', a, b, '--']);
  }
  const files = parseUnifiedDiff(r.stdout);
  files.sort((x, y) => x.path.localeCompare(y.path));
  return { files, error: r.code ? r.stderr.trim() : null };
}

/** What one step changed: the tree before it against the tree after it. */
export async function stepDiff(session, n) {
  const all = loadSteps(session.cwd, session.id);
  const i = all.findIndex((s) => s.n === n);
  if (i < 0) return { files: [] };
  const prev = all.slice(0, i).reverse().find((s) => s.commit && s.commit !== all[i].commit);
  if (!prev || !all[i].commit) {
    // First tree of the session: everything in it counts as added, but that is rarely useful, so show nothing.
    return { files: [], first: true };
  }
  return diff(session, { from: prev.commit, to: all[i].commit });
}

export async function fileAt(session, { n, path: rel }) {
  const s = find(session, n);
  if (!s?.commit) return null;
  const r = await sg(session.cwd, ['show', `${s.commit}:${rel}`]);
  return r.code === 0 ? r.stdout : null;
}

/**
 * Put the working tree back the way it was at step `n` (all files, or just `paths`). A safety step is
 * recorded first, so a restore can itself be undone from the timeline.
 */
export async function restore(session, { n, paths }) {
  const cwd = session.cwd;
  const target = find(session, n);
  if (!target?.commit) throw new Error('That point in the timeline has no snapshot.');
  const cur = await record(session, { kind: 'restore', label: 'Before restoring', onlyIfChanged: true });
  if (paths?.length) {
    const inTarget = new Set((await sg(cwd, ['ls-tree', '-r', '--name-only', target.commit, '--', ...paths])).stdout.split('\n').filter(Boolean));
    const have = paths.filter((p) => inTarget.has(p));
    if (have.length) {
      const r = await sg(cwd, ['checkout', '-f', target.commit, '--', ...have]);
      if (r.code !== 0) throw new Error(r.stderr.trim() || 'git checkout failed');
    }
    for (const p of paths) {
      if (inTarget.has(p)) continue;
      const abs = path.join(cwd, p);
      if (abs.startsWith(path.resolve(cwd)) && fs.existsSync(abs)) fs.rmSync(abs, { force: true });
    }
  } else {
    const r = await sg(cwd, ['read-tree', '--reset', '-u', target.commit]);
    if (r.code !== 0) throw new Error(r.stderr.trim() || 'git read-tree failed');
  }
  const after = await record(session, { kind: 'restore', label: paths?.length ? `Restored ${paths.length === 1 ? paths[0] : paths.length + ' files'} from step ${n}` : `Restored everything to step ${n}` });
  return { ok: true, safety: cur ? publicStep(cur) : null, step: after ? publicStep(after) : null };
}

/** Steps recorded by every session in a folder — the raw material for "who touched this file". */
export function folderSteps(cwd) {
  const dir = dirOf(cwd);
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const f of fs.readdirSync(dir)) {
    const m = f.match(/^steps-(.+)\.json$/);
    if (!m) continue;
    const sid = m[1];
    for (const s of cache.get(sid) || readJson(path.join(dir, f), [])) if (s.files?.length) out.push({ sessionId: sid, ...publicStep(s) });
  }
  return out;
}

export function forget(cwd, sid) {
  cache.delete(sid);
  try {
    fs.rmSync(stepsFile(cwd, sid), { force: true });
  } catch {
    /* fine */
  }
}
