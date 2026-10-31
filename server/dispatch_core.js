// Dispatch: one job, many repositories. A job has a brief and targets; it plans before it runs, runs
// each target as a real session in a worktree of its own, lets a canary go first, answers a permission
// once for every repository that asks the same thing, reviews the result across repositories and
// finishes with commits or pull requests and a report. This module is the state machine and the pure
// helpers; it takes its collaborators (sessions, git, the store, the inbox…) as `deps`, so the tests can
// drive it with fakes, and dispatch.js wires it to the real ones.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';

export const TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);
export const ACTIVE = new Set(['preparing', 'running', 'needs_action']);
export const DEFAULTS = Object.freeze({
  mode: 'acceptEdits', // 'ask' | 'acceptEdits' | 'allowlist'
  allowedTools: [],
  model: '',
  effort: '',
  parallel: 3,
  maxMinutes: 20,
  costCap: 0, // 0 = none
  canary: 1, // targets in the first wave; 0 = everything at once
  learn: true, // the main wave's briefs carry the canary's summary
  prepare: '', // a command run in the worktree before the agent
  copy: [], // files copied from the main checkout into the worktree before the agent (.env…)
  verify: '', // a command run after the agent; its exit code is part of the result
  verifyRetry: true, // a failing verify is sent back to the agent once
  commitMessage: '',
  pr: false,
  report: 'table', // 'table' | 'agent': one more session writes the prose on top of the table
  unattended: false, // a scheduled job: nobody answers, so permissions are declined and there is no canary
});
export const REPORT_MAX_MIN = 6;
export const MAX_BRIEF = 60_000;
export const MAX_FILES = 60;
export const SIZE_UNITS = { small: 1, medium: 2, large: 4 };
const SKIP_DIRS = new Set(['node_modules', 'vendor', 'target', 'dist', 'build', '.git', '.hg', '.svn', '__pycache__', '.venv', 'venv']);
const RATE_LIMIT = /rate.?limit|429|overloaded|too many requests/i;
const COALESCE_MS = 16;

// ---------------------------------------------------------------- pure helpers

/** `{{repo.name}}`, `{{repo.path}}`, `{{repo.branch}}`, `{{job.name}}`, `{{notes}}`; unknown variables stay. */
export function renderBrief(brief, { job, target, learned = [], deps = [] } = {}) {
  const vars = {
    'repo.name': target?.name || '',
    'repo.path': target?.path || '',
    'repo.branch': target?.base || '',
    'job.name': job?.name || '',
    notes: target?.notes || '',
  };
  let out = String(brief || '').replace(/\{\{\s*([a-zA-Z_.]+)\s*\}\}/g, (m, k) => (k in vars ? vars[k] : m));
  if (deps.length) {
    out += '\n\nRepositories this one depends on were done first:\n' + deps.map((d) => `- ${d.name}: ${d.summary || 'no summary'}`).join('\n');
  }
  if (learned.length) {
    out += '\n\nThe same work was already done in ' + (learned.length === 1 ? 'one repository' : `${learned.length} repositories`) + '. Follow the same approach where it fits:\n' + learned.map((l) => `- ${l.name}: ${l.summary || 'no summary'}`).join('\n');
  }
  return out.replace(/\n{3,}/g, '\n\n').trim();
}

/** The key two requests share when one answer should serve both: the tool and its normalized input. */
export function ruleKey(req, worktreePath = '') {
  const tool = String(req?.toolName || '');
  const input = req?.input || {};
  const rel = (p) => {
    let s = String(p || '').replace(/\\/g, '/');
    const root = String(worktreePath || '').replace(/\\/g, '/').replace(/\/$/, '');
    if (root && s.startsWith(root + '/')) s = s.slice(root.length + 1);
    return s;
  };
  if (tool === 'Bash' || tool === 'PowerShell') return `${tool}:${String(input.command || '').replace(/\s+/g, ' ').trim()}`;
  if (['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Read'].includes(tool)) return `${tool}:${rel(input.file_path || input.notebook_path)}`;
  if (tool === 'WebFetch') {
    try {
      return `${tool}:${new URL(String(input.url)).host}`;
    } catch {
      return `${tool}:${String(input.url || '')}`;
    }
  }
  const norm = (v) => {
    if (typeof v === 'string') return rel(v);
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, norm(v[k])]));
    return v;
  };
  return `${tool}:${JSON.stringify(norm(input))}`;
}

export function ruleLabel(req, worktreePath = '') {
  const key = ruleKey(req, worktreePath);
  const [tool, ...rest] = key.split(':');
  const what = rest.join(':');
  const short = what.length > 72 ? what.slice(0, 69) + '…' : what;
  return `${req?.displayName || tool}${short ? ': ' + short : ''}`;
}

/** Dependency levels among targets (Kahn): a target waits for the targets it depends on. Edges to
 *  targets outside the set are dropped; a cycle leaves its members at the level after everything else. */
export function levelsOf(targets) {
  const paths = new Set(targets.map((t) => t.path));
  const indeg = new Map();
  const out = new Map();
  for (const t of targets) {
    const deps = (t.deps || []).filter((d) => paths.has(d) && d !== t.path);
    indeg.set(t.path, deps.length);
    for (const d of deps) out.set(d, [...(out.get(d) || []), t.path]);
  }
  const level = new Map();
  let frontier = targets.filter((t) => indeg.get(t.path) === 0).map((t) => t.path);
  let n = 0;
  while (frontier.length) {
    const next = [];
    for (const p of frontier) {
      level.set(p, n);
      for (const q of out.get(p) || []) {
        indeg.set(q, indeg.get(q) - 1);
        if (indeg.get(q) === 0) next.push(q);
      }
    }
    frontier = next;
    n++;
  }
  const cyclic = targets.filter((t) => !level.has(t.path)).map((t) => t.path);
  for (const p of cyclic) level.set(p, n);
  return { level, levels: cyclic.length ? n + 1 : n, cyclic };
}

/** Changes across runs: the same hunk in several repositories is one item with a count. */
export function groupHunks(diffs) {
  // diffs: [{ runId, name, files: [{ path, add, del, hunks: [{ lines: [{t, s}] }] }] }]
  const groups = new Map();
  const runs = [];
  for (const d of diffs) {
    let add = 0;
    let del = 0;
    const files = [];
    for (const f of d.files || []) {
      const fa = Number(f.additions ?? f.add ?? 0);
      const fd = Number(f.deletions ?? f.del ?? 0);
      add += fa;
      del += fd;
      files.push({ path: f.path, add: fa, del: fd });
      const body = (f.hunks || [])
        .flatMap((h) => h.lines || [])
        .filter((l) => l.t === '+' || l.t === '-')
        .map((l) => l.t + l.s)
        .join('\n');
      const sig = crypto.createHash('sha1').update(f.path + '\n' + body).digest('hex').slice(0, 16);
      const g = groups.get(sig) || { sig, path: f.path, runs: [], add: fa, del: fd, preview: body.split('\n').slice(0, 8) };
      g.runs.push(d.name);
      groups.set(sig, g);
    }
    runs.push({ runId: d.runId, name: d.name, files, add, del });
  }
  const all = [...groups.values()];
  const same = all.filter((g) => g.runs.length > 1).sort((a, b) => b.runs.length - a.runs.length || a.path.localeCompare(b.path));
  const onlyOne = all.filter((g) => g.runs.length === 1);
  const differ = [];
  for (const g of onlyOne) {
    let e = differ.find((x) => x.name === g.runs[0]);
    if (!e) differ.push((e = { name: g.runs[0], files: [] }));
    e.files.push({ path: g.path, add: g.add, del: g.del });
  }
  return { runs, same, differ, files: runs.reduce((n, r) => n + r.files.length, 0) };
}

/** What the main wave is likely to cost and take, from the canary's runs and the targets' size classes. */
export function forecastFrom(canaryRuns, remaining, parallel) {
  const done = canaryRuns.filter((r) => r.state === 'done' || r.state === 'failed');
  if (!done.length || !remaining.length) return null;
  const units = (r) => SIZE_UNITS[r.size] || 1.5;
  const u = done.reduce((n, r) => n + units(r), 0) || 1;
  const costPer = done.reduce((n, r) => n + (r.cost || 0), 0) / u;
  const msPer = done.reduce((n, r) => n + (r.durationMs || 0), 0) / u;
  const ru = remaining.reduce((n, r) => n + units(r), 0);
  return { cost: Math.round(costPer * ru * 100) / 100, minutes: Math.max(1, Math.round((msPer * ru) / Math.max(1, parallel) / 60e3)), from: done.map((r) => r.name), runs: remaining.length };
}

function readJsonFile(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/** Local dependencies a manifest declares: package.json file:/link:/workspace:, go.mod replace, Cargo path. */
export function manifestOf(dir) {
  const out = { name: null, kind: null, localDeps: [], depNames: [] };
  const pkg = readJsonFile(path.join(dir, 'package.json'));
  if (pkg && typeof pkg === 'object') {
    out.kind = 'node';
    out.name = typeof pkg.name === 'string' ? pkg.name : null;
    for (const field of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
      for (const [name, spec] of Object.entries(pkg[field] || {})) {
        out.depNames.push(name);
        const m = /^(?:file|link):(.+)$/.exec(String(spec));
        if (m) out.localDeps.push(path.resolve(dir, m[1]));
      }
    }
  }
  const gomod = path.join(dir, 'go.mod');
  if (fs.existsSync(gomod)) {
    out.kind = out.kind || 'go';
    const text = fs.readFileSync(gomod, 'utf8');
    const mod = /^module\s+(\S+)/m.exec(text);
    if (mod && !out.name) out.name = mod[1];
    for (const m of text.matchAll(/^\s*(?:replace\s+)?(\S+)(?:\s+\S+)?\s*=>\s*(\.{1,2}\/\S*|\/\S+)/gm)) {
      out.depNames.push(m[1]);
      out.localDeps.push(path.resolve(dir, m[2]));
    }
    for (const m of text.matchAll(/^\s+(\S+)\s+v[\d.]+/gm)) out.depNames.push(m[1]);
  }
  const cargo = path.join(dir, 'Cargo.toml');
  if (fs.existsSync(cargo)) {
    out.kind = out.kind || 'rust';
    const text = fs.readFileSync(cargo, 'utf8');
    const nm = /^\s*name\s*=\s*"([^"]+)"/m.exec(text);
    if (nm && !out.name) out.name = nm[1];
    for (const m of text.matchAll(/^\s*([A-Za-z0-9_-]+)\s*=\s*\{[^}\n]*path\s*=\s*"([^"]+)"/gm)) {
      out.depNames.push(m[1]);
      out.localDeps.push(path.resolve(dir, m[2]));
    }
  }
  if (!out.kind && fs.existsSync(path.join(dir, 'pyproject.toml'))) out.kind = 'python';
  return out;
}

function parseRemote(url) {
  const m = /github\.com[:/]([^/]+)\/([^/.]+)(?:\.git)?/.exec(String(url || ''));
  return m ? { host: 'github', owner: m[1], repo: m[2], url } : url ? { host: 'other', url } : null;
}

/** Walk a folder for repositories: stop at the first `.git` on a path; skip build and dependency dirs. */
export function findRepos(root, depth = 3) {
  const out = [];
  const seen = new Set();
  const walk = (dir, d) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    if (entries.some((e) => e.name === '.git')) {
      const real = fs.realpathSync.native ? fs.realpathSync.native(dir) : dir;
      if (!seen.has(real)) {
        seen.add(real);
        out.push(dir);
      }
      return;
    }
    if (d >= depth) return;
    for (const e of entries) {
      if (!e.isDirectory() || e.name.startsWith('.') || SKIP_DIRS.has(e.name)) continue;
      walk(path.join(dir, e.name), d + 1);
    }
  };
  walk(root, 0);
  return out.sort();
}

function tail(s, n = 2000) {
  const t = String(s || '');
  return t.length > n ? t.slice(-n) : t;
}

export function jobSlug(name, slugify) {
  return slugify(name || 'job') || 'job';
}

// ---------------------------------------------------------------- the dispatcher

export function createDispatcher(deps) {
  const {
    file = null,
    sessions,
    bus,
    store,
    git,
    snapshots = { steps: () => ({ steps: [] }) },
    pushInbox = () => null,
    updateInbox = () => null,
    shell, // (cmd, cwd, timeoutMs) -> { code, output }
    run: exec, // execFile as a promise: (cmd, args, opts) -> { code, stdout, stderr }
    which = () => null,
    log = () => null,
    now = () => Date.now(),
    uuid = () => crypto.randomUUID(),
    slugify = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40),
    slash = (p) => p,
    textOfContent = () => '',
    readJson = (p, fb) => readJsonFile(p) ?? fb,
    writeJson = (p, d) => {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, JSON.stringify(d, null, 2));
    },
  } = deps;

  const events = new EventEmitter();
  const data = file ? readJson(file, { jobs: [], playbooks: [] }) : { jobs: [], playbooks: [] };
  if (!Array.isArray(data.jobs)) data.jobs = [];
  if (!Array.isArray(data.playbooks)) data.playbooks = [];
  const timers = new Map(); // runId -> timeout
  const backoffTimers = new Map(); // jobId -> timeout
  const scanCache = new Map(); // root -> { at, result }
  let saveTimer = null;
  const dirty = new Map(); // jobId -> Set(runId) | 'all'
  let flushTimer = null;
  let started = false;

  // ---- persistence and events
  function persist() {
    if (!file) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => writeJson(file, data), 150);
  }
  function flushNow() {
    if (file) writeJson(file, data);
  }
  function summary(job) {
    const counts = { queued: 0, preparing: 0, running: 0, needs_action: 0, done: 0, failed: 0, stopped: 0, skipped: 0 };
    let cost = 0;
    let needs = 0;
    let files = 0;
    for (const r of job.runs) {
      counts[r.state] = (counts[r.state] || 0) + 1;
      cost += r.cost || 0;
      needs += (r.pending || []).length;
      files += (r.files || []).length;
    }
    const { runs, ...rest } = job;
    return { ...rest, counts, total: runs.length, cost: Math.round(cost * 10000) / 10000, needsAction: needs, files, canaryWaiting: job.state === 'reviewing' && job.wave === 'canary' };
  }
  function touch(job, runOrRuns) {
    job.updatedAt = now();
    const set = dirty.get(job.id);
    if (set === 'all') {
      /* already everything */
    } else if (runOrRuns === undefined) dirty.set(job.id, 'all');
    else {
      const s = set || new Set();
      for (const r of Array.isArray(runOrRuns) ? runOrRuns : [runOrRuns]) s.add(typeof r === 'string' ? r : r.id);
      dirty.set(job.id, s);
    }
    persist();
    if (!flushTimer) flushTimer = setTimeout(flush, COALESCE_MS);
  }
  function flush() {
    flushTimer = null;
    for (const [id, set] of dirty) {
      const job = data.jobs.find((j) => j.id === id);
      if (!job) {
        events.emit('changed', { id, removed: true });
        continue;
      }
      const runs = set === 'all' ? job.runs : job.runs.filter((r) => set.has(r.id));
      events.emit('changed', { id, job: summary(job), runs, all: set === 'all' });
    }
    dirty.clear();
  }

  // ---- lookups
  const jobOf = (id) => {
    const j = data.jobs.find((x) => x.id === id);
    if (!j) throw new Error('No such job.');
    return j;
  };
  const runOf = (job, runId) => {
    const r = job.runs.find((x) => x.id === runId);
    if (!r) throw new Error('No such run.');
    return r;
  };
  function reportBySession(sid) {
    return data.jobs.find((j) => j.reportSessionId === sid && j.report?.proseState === 'writing') || null;
  }
  function runBySession(sid) {
    for (const j of data.jobs) {
      if (j.state !== 'running' && j.state !== 'reviewing') continue;
      const r = j.runs.find((x) => x.sessionId === sid && ACTIVE.has(x.state));
      if (r) return { job: j, run: r };
    }
    return null;
  }

  // ---- the workspace
  async function scan({ root, depth = 3, force = false } = {}) {
    if (!root || !fs.existsSync(root)) throw new Error('Pick a folder that exists.');
    const hit = scanCache.get(root);
    if (hit && !force && now() - hit.at < 60_000) return hit.result;
    const dirs = findRepos(root, depth);
    const repos = [];
    const metaAll = store.meta || {};
    let i = 0;
    const worker = async () => {
      while (i < dirs.length) {
        const dir = dirs[i++];
        repos.push(await describeRepo(dir, metaAll));
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, dirs.length || 1) }, worker));
    // local dependencies: by path, then by package name
    const byPath = new Map(repos.map((r) => [r.path, r]));
    const byName = new Map(repos.filter((r) => r.packageName).map((r) => [r.packageName, r]));
    for (const r of repos) {
      const deps = new Set();
      for (const p of r._localDeps || []) {
        const hit2 = byPath.get(slash(p));
        if (hit2 && hit2 !== r) deps.add(hit2.path);
      }
      for (const n of r._depNames || []) {
        const hit2 = byName.get(n);
        if (hit2 && hit2 !== r) deps.add(hit2.path);
      }
      r.deps = [...deps].sort();
      delete r._localDeps;
      delete r._depNames;
    }
    repos.sort((a, b) => a.name.localeCompare(b.name));
    const result = { root: slash(root), depth, at: now(), repos };
    scanCache.set(root, { at: now(), result });
    return result;
  }

  async function describeRepo(dir, metaAll) {
    const info = await git.info(dir).catch(() => ({ isRepo: false }));
    const name = path.basename(dir);
    const out = {
      path: slash(dir),
      name,
      branch: info.branch || null,
      dirty: Boolean(info.isRepo && (info.changed || info.untracked)),
      changed: info.changed || 0,
      untracked: info.untracked || 0,
      ahead: info.ahead || 0,
      behind: info.behind || 0,
      remote: null,
      hasClaudeMd: fs.existsSync(path.join(dir, 'CLAUDE.md')) || fs.existsSync(path.join(dir, '.claude', 'CLAUDE.md')),
      size: 'small',
      files: 0,
      kind: null,
      packageName: null,
      lastSession: null,
      deps: [],
    };
    if (info.isRepo) {
      const remote = await git.git(dir, ['config', '--get', 'remote.origin.url']).catch(() => ({ stdout: '' }));
      out.remote = parseRemote((remote.stdout || '').trim());
      const ls = await git.git(dir, ['ls-files']).catch(() => ({ stdout: '' }));
      out.files = (ls.stdout || '').split('\n').filter(Boolean).length;
      out.size = out.files < 300 ? 'small' : out.files < 3000 ? 'medium' : 'large';
    }
    const man = manifestOf(dir);
    out.kind = man.kind;
    out.packageName = man.name;
    out._localDeps = man.localDeps;
    out._depNames = man.depNames;
    let last = 0;
    for (const m of Object.values(metaAll)) {
      if ((m?.project === out.path || m?.cwd === out.path) && (m.touched || m.createdAt) > last) last = m.touched || m.createdAt;
    }
    out.lastSession = last || null;
    return out;
  }

  // ---- jobs
  function normalizeJob(input, cur) {
    const settings = { ...DEFAULTS, ...(cur?.settings || {}), ...(input.settings || {}) };
    settings.parallel = Math.max(1, Math.min(16, Number(settings.parallel) || DEFAULTS.parallel));
    settings.maxMinutes = Math.max(1, Math.min(240, Number(settings.maxMinutes) || DEFAULTS.maxMinutes));
    settings.costCap = Math.max(0, Number(settings.costCap) || 0);
    settings.canary = Math.max(0, Math.min(8, Number.isFinite(Number(settings.canary)) ? Number(settings.canary) : DEFAULTS.canary));
    if (!['ask', 'acceptEdits', 'allowlist'].includes(settings.mode)) settings.mode = DEFAULTS.mode;
    settings.allowedTools = Array.isArray(settings.allowedTools) ? settings.allowedTools.map(String) : [];
    settings.copy = Array.isArray(settings.copy) ? settings.copy.map(String).filter(Boolean) : [];
    settings.unattended = Boolean(settings.unattended);
    settings.report = settings.report === 'agent' ? 'agent' : 'table';
    const name = String(input.name ?? cur?.name ?? '').trim() || 'Untitled job';
    const targets = (input.targets ?? cur?.targets ?? []).map((t) => ({
      path: slash(String(t.path || '')),
      name: String(t.name || path.basename(String(t.path || ''))),
      base: String(t.base || ''),
      notes: String(t.notes || ''),
      size: t.size || null,
      deps: Array.isArray(t.deps) ? t.deps.map((d) => slash(String(d))) : [],
    }));
    return {
      id: cur?.id || uuid(),
      name,
      slug: settings.branch ? slugify(settings.branch) : jobSlug(name, slugify),
      brief: String(input.brief ?? cur?.brief ?? ''),
      root: slash(String(input.root ?? cur?.root ?? '')),
      targets,
      settings,
      state: cur?.state || 'draft',
      wave: cur?.wave || null,
      paused: cur?.paused || false,
      createdAt: cur?.createdAt || now(),
      updatedAt: now(),
      startedAt: cur?.startedAt || null,
      finishedAt: cur?.finishedAt || null,
      plan: cur?.plan || null,
      rules: cur?.rules || [],
      runs: cur?.runs || [],
      learned: cur?.learned || [],
      forecast: cur?.forecast || null,
      backoffMs: cur?.backoffMs || 0,
      backoffUntil: cur?.backoffUntil || 0,
      capReached: cur?.capReached || false,
      report: cur?.report || null,
      stoppedReason: cur?.stoppedReason || null,
      playbookId: input.playbookId ?? cur?.playbookId ?? null,
      scheduleId: input.scheduleId ?? cur?.scheduleId ?? null,
      inboxId: cur?.inboxId || null,
      reportSessionId: cur?.reportSessionId || null,
    };
  }

  // ---- playbooks: a brief with its settings and a set of targets, kept to run again
  function normalizePlaybook(input, cur) {
    const settings = { ...DEFAULTS, ...(cur?.settings || {}), ...(input.settings || {}) };
    delete settings.unattended;
    const targets = (input.targets ?? cur?.targets ?? []).map((t) => ({ path: slash(String(t.path || '')), name: String(t.name || path.basename(String(t.path || ''))), base: String(t.base || ''), notes: String(t.notes || '') })).filter((t) => t.path);
    return {
      id: cur?.id || uuid(),
      name: String(input.name ?? cur?.name ?? '').trim() || 'Untitled playbook',
      brief: String(input.brief ?? cur?.brief ?? ''),
      root: slash(String(input.root ?? cur?.root ?? '')),
      settings,
      targets,
      createdAt: cur?.createdAt || now(),
      updatedAt: now(),
      uses: cur?.uses || 0,
      lastUsedAt: cur?.lastUsedAt || null,
    };
  }
  function playbooks() {
    return [...data.playbooks].sort((a, b) => (b.lastUsedAt || b.updatedAt || 0) - (a.lastUsedAt || a.updatedAt || 0));
  }
  function savePlaybook(input) {
    let src = input;
    if (input.fromJob) {
      const job = jobOf(input.fromJob);
      src = { name: input.name || job.name, brief: job.brief, root: job.root, settings: job.settings, targets: job.targets, id: input.id };
      job.playbookId = job.playbookId || null;
    }
    const cur = src.id ? data.playbooks.find((p) => p.id === src.id) : null;
    const pb = normalizePlaybook(src, cur);
    if (!pb.brief.trim()) throw new Error('A playbook needs a brief.');
    if (cur) Object.assign(cur, pb);
    else data.playbooks.push(pb);
    if (input.fromJob) jobOf(input.fromJob).playbookId = pb.id;
    persist();
    events.emit('playbooks');
    return cur || pb;
  }
  function removePlaybook(id) {
    const n = data.playbooks.length;
    data.playbooks = data.playbooks.filter((p) => p.id !== id);
    if (data.playbooks.length === n) throw new Error('No such playbook.');
    persist();
    events.emit('playbooks');
    return true;
  }
  /** A job from a playbook, started at once: what a schedule fires, and what "Run again" does. */
  async function startFromPlaybook(id, { name, wave, unattended = false, scheduleId = null, targets } = {}) {
    const pb = data.playbooks.find((p) => p.id === id);
    if (!pb) throw new Error('No such playbook.');
    const settings = { ...pb.settings, unattended: Boolean(unattended) };
    const job = save({ name: name || pb.name, brief: pb.brief, root: pb.root, targets: targets || pb.targets, settings, playbookId: pb.id, scheduleId });
    pb.uses = (pb.uses || 0) + 1;
    pb.lastUsedAt = now();
    persist();
    events.emit('playbooks');
    return start(job.id, { wave });
  }

  function list() {
    return data.jobs.map(summary).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  }
  function get(id) {
    const job = jobOf(id);
    return { job: summary(job), runs: job.runs };
  }
  function save(input) {
    const cur = input.id ? data.jobs.find((j) => j.id === input.id) : null;
    if (cur && cur.state !== 'draft' && cur.state !== 'planned') {
      // a running job takes only what can still change: its name, caps and finishing settings
      const next = normalizeJob({ name: input.name, settings: input.settings }, cur);
      cur.name = next.name;
      cur.settings = { ...cur.settings, maxMinutes: next.settings.maxMinutes, costCap: next.settings.costCap, parallel: next.settings.parallel, commitMessage: next.settings.commitMessage, pr: next.settings.pr, report: next.settings.report };
      touch(cur);
      return summary(cur);
    }
    const job = normalizeJob(input, cur);
    if (cur) Object.assign(cur, job, { plan: null, state: 'draft' });
    else data.jobs.push(job);
    const j = cur || job;
    touch(j);
    return summary(j);
  }
  function remove(id, { removeWorktrees = false } = {}) {
    const job = jobOf(id);
    const work = [];
    if (job.state === 'running') stopJob(job, 'Removed');
    if (removeWorktrees) {
      for (const r of job.runs) {
        if (r.worktree && r.finished !== 'commit' && r.finished !== 'discard') work.push(git.removeWorktree({ repoRoot: r.worktree.repoRoot, dir: r.worktree.path, branch: r.worktree.branch, deleteBranch: true }).catch(() => null));
      }
    }
    data.jobs = data.jobs.filter((j) => j.id !== id);
    dirty.set(id, 'all');
    persist();
    if (!flushTimer) flushTimer = setTimeout(flush, COALESCE_MS);
    return Promise.all(work).then(() => true);
  }

  // ---- the plan
  async function plan(input) {
    const job = typeof input === 'string' ? jobOf(input) : input.id && data.jobs.find((j) => j.id === input.id) ? Object.assign(jobOf(input.id), normalizeJob(input, jobOf(input.id)), { runs: jobOf(input.id).runs }) : normalizeJob(input, null);
    const settings = job.settings;
    const openElsewhere = new Set();
    for (const j of data.jobs) {
      if (j.id === job.id || !['running', 'reviewing'].includes(j.state)) continue;
      for (const r of j.runs) if (!TERMINAL.has(r.state) || (r.worktree && !r.finished)) openElsewhere.add(r.path);
    }
    const { level, levels, cyclic } = levelsOf(job.targets);
    const targets = [];
    for (const t of job.targets) {
      const notes = [];
      let verdict = 'ok';
      const blocked = (why) => {
        verdict = 'blocked';
        notes.push({ kind: 'blocked', text: why });
      };
      const warn = (why) => {
        if (verdict === 'ok') verdict = 'warning';
        notes.push({ kind: 'warning', text: why });
      };
      const note = (why) => notes.push({ kind: 'note', text: why });
      const info = fs.existsSync(t.path) ? await git.info(t.path).catch(() => ({ isRepo: false })) : { isRepo: false };
      let base = t.base || '';
      if (!info.isRepo) blocked(fs.existsSync(t.path) ? 'Not a git repository.' : 'The folder does not exist.');
      else {
        if (!base) base = info.branch || 'HEAD';
        if (base !== 'HEAD') {
          const local = await git.git(t.path, ['rev-parse', '--verify', '-q', `refs/heads/${base}`]);
          if (local.code !== 0) {
            const remote = await git.git(t.path, ['rev-parse', '--verify', '-q', `refs/remotes/origin/${base}`]);
            if (remote.code === 0) {
              base = `origin/${base}`;
              note(`Starts from origin/${t.base} (no local branch of that name).`);
            } else blocked(`Branch ${base} not found. Pick another branch or deselect this repository.`);
          }
        }
        if (info.changed || info.untracked) warn(`${(info.changed || 0) + (info.untracked || 0)} uncommitted change${(info.changed || 0) + (info.untracked || 0) === 1 ? '' : 's'} in the main checkout. The run starts from the branch's last commit; they are not in it.`);
        if (openElsewhere.has(t.path)) warn('Another job still has a run open on this repository (the branches differ, so both can run).');
        if (!fs.existsSync(path.join(t.path, 'CLAUDE.md')) && !fs.existsSync(path.join(t.path, '.claude', 'CLAUDE.md'))) note('No CLAUDE.md: the brief may want to say more about this repository.');
      }
      const brief = renderBrief(job.brief, { job, target: { ...t, base } });
      if (brief.length > MAX_BRIEF) blocked(`The brief is ${brief.length.toLocaleString()} characters after substitution; the limit is ${MAX_BRIEF.toLocaleString()}.`);
      if (!job.brief.trim()) blocked('The brief is empty.');
      const lv = level.get(t.path) ?? 0;
      const deps = (t.deps || []).filter((d) => job.targets.some((x) => x.path === d));
      if (deps.length) note(`Runs after ${deps.map((d) => job.targets.find((x) => x.path === d)?.name || d).join(', ')} and receives ${deps.length === 1 ? 'its' : 'their'} summary.`);
      if (cyclic.includes(t.path)) warn('Part of a dependency cycle: it runs in the last level.');
      targets.push({ path: t.path, name: t.name, base, verdict, notes, brief, level: lv, deps, size: t.size || null });
    }
    const n = targets.filter((t) => t.verdict !== 'blocked').length;
    const worst = { minutes: Math.ceil((n * settings.maxMinutes) / Math.max(1, settings.parallel)), cost: settings.costCap || null, runs: n, levels };
    const out = { at: now(), targets, levels, worst, ok: targets.filter((t) => t.verdict === 'ok').length, warnings: targets.filter((t) => t.verdict === 'warning').length, blocked: targets.filter((t) => t.verdict === 'blocked').length };
    if (data.jobs.includes(job)) {
      job.plan = out;
      if (job.state === 'draft') job.state = 'planned';
      touch(job);
    }
    return out;
  }

  // ---- running
  function modeFor(job) {
    const m = job.settings.mode;
    return m === 'acceptEdits' ? 'acceptEdits' : 'default';
  }

  async function start(id, { wave } = {}) {
    const job = jobOf(id);
    if (job.state === 'running') throw new Error('This job is already running.');
    if (!['draft', 'planned', 'stopped'].includes(job.state)) throw new Error('This job has run; make a new one or retry a run.');
    const p = await plan(job.id);
    if (p.blocked) throw new Error(`${p.blocked} target${p.blocked === 1 ? ' is' : 's are'} blocked. Fix or deselect ${p.blocked === 1 ? 'it' : 'them'} first.`);
    if (!p.targets.length) throw new Error('Pick at least one repository.');
    const canaryN = wave === 'all' || job.settings.unattended ? 0 : job.settings.canary;
    const order = [...p.targets].sort((a, b) => a.level - b.level || (SIZE_UNITS[a.size] || 1.5) - (SIZE_UNITS[b.size] || 1.5) || a.name.localeCompare(b.name));
    const canaryPaths = new Set(order.filter((t) => t.level === 0).slice(0, canaryN).map((t) => t.path));
    job.runs = p.targets.map((t) => ({
      id: uuid(),
      jobId: job.id,
      path: t.path,
      name: t.name,
      base: t.base,
      size: t.size,
      level: t.level,
      dependsOn: t.deps,
      wave: canaryPaths.has(t.path) ? 'canary' : 'main',
      state: 'queued',
      attempt: 1,
      sessionId: null,
      worktree: null,
      startedAt: null,
      endedAt: null,
      durationMs: 0,
      cost: 0,
      summary: '',
      files: [],
      add: 0,
      del: 0,
      reason: null,
      verify: null,
      now: null,
      pending: [],
      retried: false,
      commit: null,
      pr: null,
      finished: null,
      brief: t.brief,
    }));
    job.state = 'running';
    job.wave = canaryPaths.size ? 'canary' : 'main';
    job.paused = false;
    job.startedAt = now();
    job.finishedAt = null;
    job.learned = [];
    job.forecast = null;
    job.capReached = false;
    job.stoppedReason = null;
    job.report = null;
    touch(job);
    pump();
    return summary(job);
  }

  function pump() {
    for (const job of data.jobs) {
      if (job.state !== 'running' || job.paused) continue;
      if (job.backoffUntil && job.backoffUntil > now()) {
        armBackoff(job);
        continue;
      }
      const active = job.runs.filter((r) => ACTIVE.has(r.state));
      let slots = job.settings.parallel - active.length;
      const cost = job.runs.reduce((n, r) => n + (r.cost || 0), 0);
      if (job.settings.costCap && cost >= job.settings.costCap) {
        if (!job.capReached) {
          job.capReached = true;
          touch(job);
        }
        slots = 0;
      }
      const eligible = (r) => r.state === 'queued' && (job.wave === 'main' || r.wave === 'canary');
      const doneOf = (p) => job.runs.filter((r) => r.path === p);
      for (const r of job.runs.filter(eligible)) {
        // a dependency that ended badly skips its dependents
        const bad = r.dependsOn.find((d) => doneOf(d).length && doneOf(d).every((x) => TERMINAL.has(x.state) && x.state !== 'done'));
        if (bad) {
          r.state = 'skipped';
          r.reason = `Depends on ${job.runs.find((x) => x.path === bad)?.name || bad}, which did not finish.`;
          r.endedAt = now();
          touch(job, r);
        }
      }
      const ready = job.runs.filter(eligible).filter((r) => r.dependsOn.every((d) => !doneOf(d).length || doneOf(d).some((x) => x.state === 'done')));
      ready.sort((a, b) => a.level - b.level || (SIZE_UNITS[a.size] || 1.5) - (SIZE_UNITS[b.size] || 1.5) || a.name.localeCompare(b.name));
      for (const r of ready.slice(0, Math.max(0, slots))) startRun(job, r).catch((e) => log('dispatch run failed to start', e?.message));
      settle(job);
    }
  }

  /** When nothing is active: the canary review, or the final review. */
  function settle(job) {
    if (job.state !== 'running') return;
    const active = job.runs.some((r) => ACTIVE.has(r.state));
    if (active) return;
    const queued = job.runs.filter((r) => r.state === 'queued');
    if (job.wave === 'canary') {
      if (queued.some((r) => r.wave === 'canary')) return; // waiting for a slot (a cap, a backoff)
      if (queued.length) {
        job.state = 'reviewing';
        job.learned = job.runs.filter((r) => r.wave === 'canary' && r.state === 'done').map((r) => ({ name: r.name, summary: r.summary }));
        job.forecast = forecastFrom(
          job.runs.filter((r) => r.wave === 'canary'),
          queued.filter((r) => r.wave === 'main'),
          job.settings.parallel,
        );
        touch(job);
        return;
      }
    }
    if (queued.length && !job.capReached) return; // dependents still waiting
    if (queued.length) return; // cap reached: the queued ones wait for a raise or a stop
    job.state = 'reviewing';
    job.wave = 'done';
    touch(job);
    maybeFinish(job);
  }

  function armBackoff(job) {
    if (backoffTimers.has(job.id)) return;
    const t = setTimeout(
      () => {
        backoffTimers.delete(job.id);
        pump();
      },
      Math.max(50, job.backoffUntil - now()),
    );
    backoffTimers.set(job.id, t);
  }

  async function startRun(job, run) {
    run.state = 'preparing';
    run.now = 'creating the worktree';
    run.startedAt = now();
    touch(job, run);
    let session = null;
    try {
      const made = await sessions.create({ cwd: run.path, worktree: { name: job.slug, base: run.base }, mode: modeFor(job), model: job.settings.model || undefined, effort: job.settings.effort || undefined, title: `${job.name} · ${run.name}` });
      run.sessionId = made.id;
      run.worktree = made.worktree;
      store.setMeta(made.id, { dispatch: { jobId: job.id, runId: run.id, name: job.name, target: run.name } });
      session = sessions.find(made.id);
      if (session) {
        session.dispatch = { jobId: job.id, runId: run.id };
        if (job.settings.unattended) session.unattended = true; // nobody will answer: Claude is told so
      }
      touch(job, run);
      // the worktree isolates files only: copies and the prepare command bring what the agent needs
      for (const rel of job.settings.copy || []) {
        const from = path.join(made.worktree?.repoRoot || run.path, rel);
        const to = path.join(made.cwd, rel);
        if (fs.existsSync(from) && !fs.existsSync(to)) {
          fs.mkdirSync(path.dirname(to), { recursive: true });
          fs.copyFileSync(from, to);
        }
      }
      if (job.settings.prepare) {
        run.now = `prepare: ${job.settings.prepare}`;
        touch(job, run);
        const r = await shell(job.settings.prepare, made.cwd, Math.min(job.settings.maxMinutes, 15) * 60e3);
        if (r.code !== 0) {
          run.prepare = { code: r.code, tail: tail(r.output) };
          endRun(job, run, 'failed', `Prepare failed (exit ${r.code}): ${tail(r.output, 200).trim() || 'no output'}`, session);
          return;
        }
        run.prepare = { code: 0, tail: tail(r.output, 400) };
      }
      if (run.state !== 'preparing') return; // stopped meanwhile
      run.state = 'running';
      run.now = 'starting';
      touch(job, run);
      timers.set(
        run.id,
        setTimeout(() => {
          const s = sessions.find(run.sessionId);
          s?.interrupt?.().catch?.(() => null);
          setTimeout(() => {
            if (ACTIVE.has(run.state)) endRun(job, run, 'stopped', `Stopped after ${job.settings.maxMinutes} minutes`, s);
          }, 3000);
        }, job.settings.maxMinutes * 60e3),
      );
      const extras = { learned: job.wave === 'main' && job.settings.learn ? job.learned : [], deps: run.dependsOn.map((d) => job.runs.find((x) => x.path === d && x.state === 'done')).filter(Boolean).map((d) => ({ name: d.name, summary: d.summary })) };
      run.brief = renderBrief(job.brief, { job, target: { ...run }, ...extras });
      await (session || (await sessions.get(made.id))).send({ text: run.brief });
    } catch (e) {
      if (ACTIVE.has(run.state)) endRun(job, run, 'failed', e?.message || 'Could not start', session);
    }
  }

  function clearRunTimer(run) {
    clearTimeout(timers.get(run.id));
    timers.delete(run.id);
  }

  async function onIdle(job, run, session) {
    if (!ACTIVE.has(run.state)) return;
    const v = job.settings.verify;
    if (v && !(run.verify && run.verify.ran)) {
      run.now = `verify: ${v}`;
      run.state = 'running';
      touch(job, run);
      const r = await shell(v, run.worktree?.path || session?.cwd || run.path, 10 * 60e3);
      if (!ACTIVE.has(run.state)) return;
      run.verify = { ran: true, code: r.code, tail: tail(r.output) };
      if (r.code !== 0) {
        if (job.settings.verifyRetry && !run.retried) {
          run.retried = true;
          run.verify.ran = false; // runs again after the fix
          run.now = 'fixing what verify found';
          touch(job, run);
          try {
            await session.send({ text: `The verify command \`${v}\` failed with exit code ${r.code}:\n\n\`\`\`\n${tail(r.output, 3000)}\n\`\`\`\n\nPlease fix this and stop when it passes.` });
            return;
          } catch (e) {
            endRun(job, run, 'failed', e?.message || 'Could not send', session);
            return;
          }
        }
        endRun(job, run, 'failed', `Verify failed (exit ${r.code})`, session);
        return;
      }
    }
    endRun(job, run, 'done', null, session);
  }

  async function collect(job, run, session) {
    const files = new Map();
    if (session) {
      try {
        for (const st of snapshots.steps(session).steps || []) {
          if (st.at < (run.startedAt || 0)) continue;
          for (const f of st.files || []) {
            const cur = files.get(f.path) || { path: f.path, add: 0, del: 0 };
            cur.add += f.add || 0;
            cur.del += f.del || 0;
            files.set(f.path, cur);
          }
        }
      } catch {
        /* no snapshots */
      }
    }
    let add = 0;
    let del = 0;
    if (run.worktree?.path) {
      try {
        const d = await git.diff(run.worktree.path, { base: run.worktree.base });
        for (const f of d.files || []) {
          const fa = Number(f.additions ?? f.add ?? 0);
          const fd = Number(f.deletions ?? f.del ?? 0);
          add += fa;
          del += fd;
          if (!files.has(f.path)) files.set(f.path, { path: f.path, add: fa, del: fd });
        }
      } catch {
        /* the worktree may be gone */
      }
    }
    if (!add && !del) {
      for (const f of files.values()) {
        add += f.add;
        del += f.del;
      }
    }
    run.files = [...files.values()].slice(0, MAX_FILES);
    run.add = add;
    run.del = del;
    if (session) {
      run.cost = session.usage?.costUsd || run.cost || 0;
      const text = session.lastReply?.text || lastAssistantText(session) || '';
      if (text) run.summary = text.slice(0, 1200);
    }
  }

  function lastAssistantText(session) {
    const msgs = session?.messages || [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.type === 'assistant' && !m.parent_tool_use_id) {
        const t = textOfContent(m.message?.content);
        if (t) return t.replace(/\s+/g, ' ').trim();
      }
    }
    return '';
  }

  function endRun(job, run, state, reason, session) {
    if (TERMINAL.has(run.state)) return;
    clearRunTimer(run);
    run.state = state;
    run.reason = reason || null;
    run.now = null;
    run.pending = [];
    run.endedAt = now();
    run.durationMs = run.endedAt - (run.startedAt || run.endedAt);
    touch(job, run);
    const s = session || (run.sessionId ? sessions.find(run.sessionId) : null);
    collect(job, run, s)
      .catch(() => null)
      .then(() => {
        touch(job, run);
        if (s && state !== 'failed') setTimeout(() => s.close?.(), 1500);
        else if (s) setTimeout(() => s.close?.(), 1500);
        log('dispatch run', job.name, run.name, state, reason || '');
        pump();
      });
  }

  // ---- what the sessions say
  function onState(st) {
    const rj = reportBySession(st.id);
    if (rj) {
      if (st.status === 'idle') finishProse(rj, sessions.find(st.id), null);
      else if (st.status === 'error' || st.status === 'stopped') finishProse(rj, sessions.find(st.id), st.error || 'The report session ended');
      return;
    }
    const hit = runBySession(st.id);
    if (!hit) return;
    const { job, run } = hit;
    const session = sessions.find(st.id);
    let changed = false;
    const label = st.activity?.label ? (st.activity.kind === 'tool' && st.activity.tool ? `${st.activity.tool}: ${st.activity.label}` : st.activity.label) : null;
    if (run.state === 'running' && label && label !== run.now && !String(run.now || '').startsWith('verify:') && !String(run.now || '').startsWith('fixing')) {
      run.now = label.slice(0, 140);
      changed = true;
    }
    const cost = st.usage?.costUsd;
    if (typeof cost === 'number' && cost !== run.cost) {
      run.cost = cost;
      changed = true;
    }
    if (st.status === 'idle' && (run.state === 'running' || run.state === 'needs_action')) {
      onIdle(job, run, session).catch((e) => log('dispatch idle failed', e?.message));
      return;
    }
    if (st.status === 'error') {
      if (RATE_LIMIT.test(String(st.error || '')) && run.attempt < 4) {
        clearRunTimer(run);
        job.backoffMs = Math.min(60_000, (job.backoffMs || 2000) * 2);
        job.backoffUntil = now() + job.backoffMs;
        run.state = 'queued';
        run.attempt += 1;
        run.now = null;
        run.reason = `Rate limited; retrying in ${Math.round(job.backoffMs / 1000)} s`;
        session?.close?.();
        touch(job, run);
        armBackoff(job);
        return;
      }
      endRun(job, run, 'failed', st.error || 'Claude Code reported an error', session);
      return;
    }
    if (st.status === 'stopped' && ACTIVE.has(run.state)) {
      if (session?.lastReply) onIdle(job, run, session).catch(() => null);
      else endRun(job, run, 'failed', 'Claude Code exited', session);
      return;
    }
    if (changed) touch(job, run);
  }

  /** Streaming does not change the session's status; its message batches carry what it is doing now. */
  function onMsgs({ id }) {
    const hit = runBySession(id);
    if (!hit || hit.run.state !== 'running') return;
    const session = sessions.find(id);
    const a = session?.activity;
    if (!a?.label) return;
    const label = (a.kind === 'tool' && a.tool ? `${a.tool}: ${a.label}` : a.label).slice(0, 140);
    const cur = String(hit.run.now || '');
    if (label !== cur && !cur.startsWith('verify:') && !cur.startsWith('fixing')) {
      hit.run.now = label;
      if (typeof session?.usage?.costUsd === 'number') hit.run.cost = session.usage.costUsd;
      touch(hit.job, hit.run);
    }
  }

  function onPermission({ id, request }) {
    const hit = runBySession(id);
    if (!hit) return;
    const { job, run } = hit;
    const session = sessions.find(id);
    const key = ruleKey(request, run.worktree?.path);
    if (job.rules.some((r) => r.key === key) && !request.interactive) {
      session?.respond?.(request.requestId, { behavior: 'allow', updatedInput: request.input, decisionClassification: 'user_temporary' });
      log('dispatch rule answered', job.name, run.name, key);
      return;
    }
    run.pending = [...(run.pending || []).filter((p) => p.requestId !== request.requestId), { requestId: request.requestId, toolName: request.toolName, displayName: request.displayName || null, key, label: ruleLabel(request, run.worktree?.path), interactive: Boolean(request.interactive), at: request.at || now() }];
    run.state = 'needs_action';
    run.now = run.pending[run.pending.length - 1].label;
    touch(job, run);
  }

  function onPermissionResolved({ id, requestId }) {
    const hit = runBySession(id);
    if (!hit) return;
    const { job, run } = hit;
    const before = (run.pending || []).length;
    run.pending = (run.pending || []).filter((p) => p.requestId !== requestId);
    if (run.pending.length !== before) {
      if (!run.pending.length && run.state === 'needs_action') {
        run.state = 'running';
        run.now = 'continuing';
      }
      touch(job, run);
    }
  }

  // ---- steering
  function continueJob(id, { brief, learn } = {}) {
    const job = jobOf(id);
    if (!(job.state === 'reviewing' && job.wave === 'canary')) throw new Error('The job is not waiting after a canary.');
    if (typeof brief === 'string' && brief.trim()) job.brief = brief;
    if (typeof learn === 'boolean') job.settings.learn = learn;
    job.wave = 'main';
    job.state = 'running';
    job.paused = false;
    touch(job);
    pump();
    return summary(job);
  }
  function pause(id) {
    const job = jobOf(id);
    job.paused = true;
    touch(job);
    return summary(job);
  }
  function resume(id) {
    const job = jobOf(id);
    job.paused = false;
    if (job.capReached && job.settings.costCap && job.runs.reduce((n, r) => n + (r.cost || 0), 0) < job.settings.costCap) job.capReached = false;
    touch(job);
    pump();
    return summary(job);
  }
  function stopJob(job, reason) {
    for (const r of job.runs) {
      if (ACTIVE.has(r.state)) {
        const s = r.sessionId ? sessions.find(r.sessionId) : null;
        s?.interrupt?.().catch?.(() => null);
        endRun(job, r, 'stopped', reason, s);
      } else if (r.state === 'queued') {
        r.state = 'skipped';
        r.reason = reason;
        r.endedAt = now();
      }
    }
    job.state = 'stopped';
    job.stoppedReason = reason;
    touch(job);
  }
  function stop(id, { runId } = {}) {
    const job = jobOf(id);
    if (runId) {
      const run = runOf(job, runId);
      if (ACTIVE.has(run.state)) {
        const s = run.sessionId ? sessions.find(run.sessionId) : null;
        s?.interrupt?.().catch?.(() => null);
        endRun(job, run, 'stopped', 'Stopped by you', s);
      } else if (run.state === 'queued') {
        run.state = 'skipped';
        run.reason = 'Skipped by you';
        run.endedAt = now();
        touch(job, run);
        pump();
      }
      return summary(job);
    }
    stopJob(job, 'Stopped by you');
    return summary(job);
  }
  function retry(id, runId, { brief } = {}) {
    const job = jobOf(id);
    const old = runOf(job, runId);
    if (!TERMINAL.has(old.state)) throw new Error('That run is still going.');
    if (typeof brief === 'string' && brief.trim()) job.brief = brief;
    const t = job.plan?.targets.find((x) => x.path === old.path);
    const run = { ...old, id: uuid(), state: 'queued', attempt: (old.attempt || 1) + 1, sessionId: null, worktree: null, startedAt: null, endedAt: null, durationMs: 0, cost: 0, summary: '', files: [], add: 0, del: 0, reason: null, verify: null, prepare: null, now: null, pending: [], retried: false, commit: null, pr: null, finished: null, wave: 'main', brief: t?.brief || old.brief };
    old.superseded = run.id;
    job.runs.push(run);
    if (job.state !== 'running') {
      job.state = 'running';
      job.wave = 'main';
      job.finishedAt = null;
    }
    touch(job);
    pump();
    return run;
  }
  function skip(id, runId) {
    return stop(id, { runId });
  }
  function allow(id, key, requestId) {
    const job = jobOf(id);
    if (!key && requestId) key = job.runs.flatMap((r) => r.pending || []).find((p) => p.requestId === requestId)?.key || null;
    if (!key) throw new Error('No request to allow.');
    if (!job.rules.some((r) => r.key === key)) job.rules.push({ key, label: null, at: now(), count: 0 });
    const rule = job.rules.find((r) => r.key === key);
    for (const run of job.runs) {
      const hits = (run.pending || []).filter((p) => p.key === key && !p.interactive);
      if (!hits.length) continue;
      rule.label = rule.label || hits[0].label;
      const session = sessions.find(run.sessionId);
      for (const p of hits) {
        rule.count += 1;
        const req = session?.pending?.get?.(p.requestId)?.req;
        session?.respond?.(p.requestId, { behavior: 'allow', updatedInput: req?.input, decisionClassification: 'user_temporary' });
      }
      run.pending = run.pending.filter((p) => !(p.key === key && !p.interactive));
      if (!run.pending.length && run.state === 'needs_action') {
        run.state = 'running';
        run.now = 'continuing';
      }
      touch(job, run);
    }
    touch(job);
    return summary(job);
  }

  // ---- review and finish
  async function review(id) {
    const job = jobOf(id);
    const diffs = [];
    for (const r of job.runs) {
      if (!r.worktree?.path || r.superseded) continue;
      if (!fs.existsSync(r.worktree.path)) continue;
      try {
        const d = await git.diff(r.worktree.path, { base: r.worktree.base });
        diffs.push({ runId: r.id, name: r.name, files: d.files || [] });
      } catch {
        /* skip */
      }
    }
    return { id, ...groupHunks(diffs) };
  }

  async function finish(id, { runId, action, message, pr } = {}) {
    const job = jobOf(id);
    if (!['commit', 'discard', 'keep'].includes(action)) throw new Error('Finish with commit, discard or keep.');
    const runs = runId ? [runOf(job, runId)] : job.runs.filter((r) => !r.superseded && TERMINAL.has(r.state) && !r.finished && (action === 'commit' ? r.state === 'done' : true));
    const results = [];
    for (const run of runs) {
      if (!TERMINAL.has(run.state)) throw new Error(`${run.name} is still going.`);
      const res = { runId: run.id, name: run.name, ok: true, output: '' };
      try {
        if (action === 'keep') run.finished = 'keep';
        else if (!run.worktree?.path || !fs.existsSync(run.worktree.path)) {
          run.finished = action;
          res.output = 'No worktree.';
        } else if (action === 'discard') {
          const r = await git.removeWorktree({ repoRoot: run.worktree.repoRoot, dir: run.worktree.path, branch: run.worktree.branch, deleteBranch: true });
          res.ok = r.ok !== false;
          res.output = r.output || '';
          run.finished = 'discard';
        } else {
          const msg = renderBrief(message || job.settings.commitMessage || `${job.name}`, { job, target: run });
          const c = await git.commit(run.worktree.path, { message: msg });
          if (!c.ok && !/nothing to commit|nothing added/i.test(c.output || '')) throw new Error(c.output || 'commit failed');
          const head = await git.git(run.worktree.path, ['rev-parse', '--short', 'HEAD']);
          run.commit = (head.stdout || '').trim() || null;
          if (pr ?? job.settings.pr) {
            const url = await openPr(job, run).catch((e) => {
              run.prError = e?.message || 'Could not open a pull request';
              return null;
            });
            if (url) run.pr = url;
          }
          const r = await git.removeWorktree({ repoRoot: run.worktree.repoRoot, dir: run.worktree.path, branch: run.worktree.branch, deleteBranch: false });
          res.output = r.output || '';
          run.finished = 'commit';
        }
        const s = run.sessionId ? sessions.find(run.sessionId) : null;
        if (action !== 'keep') s?.close?.();
      } catch (e) {
        res.ok = false;
        res.output = e?.message || String(e);
        run.finishError = res.output;
      }
      results.push(res);
      touch(job, run);
    }
    maybeFinish(job);
    return { job: summary(job), results };
  }

  async function openPr(job, run) {
    const gh = which('gh');
    if (!gh) throw new Error('gh is not on the path.');
    const wt = run.worktree;
    const push = await run_(['push', '-u', 'origin', wt.branch], wt.path);
    if (push.code) throw new Error((push.stderr || push.stdout || 'push failed').trim());
    const body = `${job.brief}\n\n---\n${run.summary || ''}\n\nOpened by Pilcrow Dispatch (job “${job.name}”).`;
    const r = await exec(gh, ['pr', 'create', '--head', wt.branch, '--title', `${job.name}`, '--body', body], { cwd: wt.repoRoot, timeout: 60_000 });
    if (r.code) throw new Error((r.stderr || r.stdout || 'gh pr create failed').trim());
    const url = (r.stdout || '').trim().split('\n').pop();
    return url || null;
  }
  const run_ = (args, cwd) => git.git(cwd, args);

  function maybeFinish(job) {
    if (job.state !== 'reviewing' || job.wave === 'canary') return;
    const live = job.runs.filter((r) => !r.superseded);
    if (!live.every((r) => TERMINAL.has(r.state))) return;
    if (!live.every((r) => r.finished || r.state !== 'done' || !r.worktree)) return;
    job.state = 'finished';
    job.finishedAt = now();
    job.report = buildReport(job);
    const item = inboxItem(job);
    job.inboxId = item.id;
    try {
      pushInbox(item);
    } catch (e) {
      log('dispatch inbox failed', e?.message);
    }
    touch(job);
    events.emit('finished', { id: job.id, scheduleId: job.scheduleId || null, playbookId: job.playbookId || null, status: item.status, cost: job.report.cost, name: job.name });
    if (job.settings.report === 'agent') writeProse(job).catch((e) => log('dispatch report failed', e?.message));
  }

  /** One more session reads every run's summary and writes the prose on top of the table. */
  async function writeProse(job) {
    const rep = job.report;
    const cwd = job.root && fs.existsSync(job.root) ? job.root : job.targets.find((t) => fs.existsSync(t.path))?.path;
    if (!cwd) {
      rep.proseState = 'failed';
      rep.proseError = 'No folder to run the report session in.';
      touch(job);
      return;
    }
    rep.proseState = 'writing';
    touch(job);
    const made = await sessions.create({ cwd, mode: 'default', model: job.settings.model || undefined, title: `${job.name} · report` });
    store.setMeta(made.id, { dispatch: { jobId: job.id, runId: null, name: job.name, target: 'report' } });
    const session = sessions.find(made.id) || (await sessions.get(made.id));
    session.unattended = true; // the report needs no tools and nobody answers
    job.reportSessionId = made.id;
    touch(job);
    const lines = rep.targets.map((t) => `- ${t.name}: ${t.state}${t.reason ? ` (${t.reason})` : ''}; ${t.files || 0} files changed (+${t.add || 0} −${t.del || 0})${t.verify ? `; verify exit ${t.verify.code}` : ''}${t.summary ? `\n  Summary: ${t.summary.slice(0, 600)}` : ''}`);
    const prompt = [
      `You are writing the report of a job that ran the same brief across ${rep.targets.length} repositories. Use only what is below; do not use tools or read files.`,
      '',
      `Job: ${job.name}`,
      'Brief:',
      job.brief,
      '',
      'Repositories:',
      ...lines,
      '',
      'Write the report in plain prose, under 250 words, in three short paragraphs: what was done the same way everywhere; where a repository differed or did not finish, and why; what still needs a hand. No headings, no lists, no preamble.',
    ].join('\n');
    timers.set(
      `report:${job.id}`,
      setTimeout(() => finishProse(job, sessions.find(made.id), `The report took more than ${REPORT_MAX_MIN} minutes`), REPORT_MAX_MIN * 60e3),
    );
    try {
      await session.send({ text: prompt });
    } catch (e) {
      finishProse(job, session, e?.message || 'Could not send');
    }
  }

  function finishProse(job, session, error) {
    const rep = job.report;
    if (!rep || rep.proseState !== 'writing') return;
    clearTimeout(timers.get(`report:${job.id}`));
    timers.delete(`report:${job.id}`);
    const text = error ? '' : session?.lastReply?.text || lastAssistantText(session) || '';
    rep.proseState = text ? 'done' : 'failed';
    rep.prose = text || null;
    rep.proseError = text ? null : error || 'The report session wrote nothing';
    rep.proseCost = session?.usage?.costUsd || 0;
    rep.cost = (rep.cost || 0) + rep.proseCost;
    if (job.inboxId) {
      try {
        updateInbox(job.inboxId, { dispatch: rep, summary: text ? text.slice(0, 1200) : undefined });
      } catch (e) {
        log('dispatch inbox update failed', e?.message);
      }
    }
    touch(job);
    setTimeout(() => session?.close?.(), 1500);
  }

  function buildReport(job) {
    const live = job.runs.filter((r) => !r.superseded);
    return {
      at: now(),
      targets: live.map((r) => ({ name: r.name, path: r.path, state: r.state, reason: r.reason, summary: r.summary, files: (r.files || []).length, add: r.add, del: r.del, cost: r.cost, durationMs: r.durationMs, branch: r.worktree?.branch || null, commit: r.commit, pr: r.pr, finished: r.finished, verify: r.verify ? { code: r.verify.code } : null })),
      rules: job.rules,
      plan: job.plan ? { ok: job.plan.ok, warnings: job.plan.warnings, blocked: job.plan.blocked, levels: job.plan.levels } : null,
      cost: live.reduce((n, r) => n + (r.cost || 0), 0),
      durationMs: (job.finishedAt || now()) - (job.startedAt || now()),
    };
  }

  function inboxItem(job) {
    const rep = job.report || buildReport(job);
    const done = rep.targets.filter((t) => t.state === 'done').length;
    const failed = rep.targets.filter((t) => t.state === 'failed' || t.state === 'stopped').length;
    const prs = rep.targets.filter((t) => t.pr).length;
    const files = [];
    for (const r of job.runs) for (const f of r.files || []) if (files.length < MAX_FILES) files.push({ ...f, repo: r.name });
    const lines = rep.targets.map((t) => `- ${t.name}: ${t.state}${t.reason ? ` (${t.reason})` : ''}${t.summary ? ` — ${t.summary.slice(0, 160)}` : ''}`);
    return {
      id: uuid(),
      kind: 'dispatch',
      jobId: job.id,
      scheduleId: job.scheduleId || null,
      scheduleName: job.name,
      sessionId: null,
      cwd: job.root || job.targets[0]?.path || '',
      startedAt: job.startedAt,
      endedAt: job.finishedAt || now(),
      durationMs: rep.durationMs,
      status: failed && !done ? 'failed' : 'completed',
      reason: failed ? `${failed} of ${rep.targets.length} did not finish` : null,
      summary: `${done} of ${rep.targets.length} repositories done${prs ? `, ${prs} pull request${prs === 1 ? '' : 's'}` : ''}.\n${lines.join('\n')}`.slice(0, 1200),
      cost: rep.cost,
      files,
      read: false,
      manual: true,
      dispatch: rep,
    };
  }

  function exportMarkdown(id) {
    const job = jobOf(id);
    const rep = job.report || buildReport(job);
    const fmtCost = (c) => `$${(c || 0).toFixed(2)}`;
    const fmtMin = (ms) => `${Math.round((ms || 0) / 60e3)} min`;
    const rows = rep.targets.map((t) => `| ${t.name} | ${t.state}${t.reason ? ` · ${t.reason}` : ''} | ${t.files || 0} (+${t.add || 0} −${t.del || 0}) | ${fmtCost(t.cost)} | ${fmtMin(t.durationMs)} | ${t.commit ? `\`${t.commit}\`` : t.finished || ''}${t.pr ? ` [PR](${t.pr})` : ''} |`);
    const rules = rep.rules?.length ? `\n\n## Allowed for every repository\n\n${rep.rules.map((r) => `- ${r.label || r.key}`).join('\n')}` : '';
    const prose = rep.prose ? ['', rep.prose, ''] : [];
    return [`# ${job.name}`, '', `${rep.targets.length} repositories · ${fmtCost(rep.cost)} · ${fmtMin(rep.durationMs)}${job.state === 'finished' ? '' : ` · ${job.state}`}`, ...prose, '', '## Brief', '', job.brief, '', '## Repositories', '', '| Repository | State | Files | Cost | Time | Landed |', '|---|---|---|---|---|---|', ...rows, ...rep.targets.filter((t) => t.summary).map((t) => `\n### ${t.name}\n\n${t.summary}`), rules].join('\n');
  }

  // ---- lifecycle
  function start_() {
    if (started) return;
    started = true;
    bus.on('state', onState);
    bus.on('msgs', onMsgs);
    bus.on('permission', onPermission);
    bus.on('permission-resolved', onPermissionResolved);
    // what the restart left: live sessions re-attach, the rest are stopped with a reason
    for (const job of data.jobs) {
      if (job.state !== 'running' && job.state !== 'reviewing') continue;
      for (const r of job.runs) {
        if (!ACTIVE.has(r.state)) continue;
        const s = r.sessionId ? sessions.find(r.sessionId) : null;
        if (s && s.q) {
          timers.set(
            r.id,
            setTimeout(() => endRun(job, r, 'stopped', `Stopped after ${job.settings.maxMinutes} minutes`, s), Math.max(1000, job.settings.maxMinutes * 60e3 - (now() - (r.startedAt || now())))),
          );
        } else {
          r.state = 'stopped';
          r.reason = 'The server restarted';
          r.endedAt = now();
          r.durationMs = r.endedAt - (r.startedAt || r.endedAt);
          r.now = null;
          r.pending = [];
        }
      }
      touch(job);
    }
    pump();
  }
  function stop_() {
    if (!started) return;
    started = false;
    bus.off('state', onState);
    bus.off('msgs', onMsgs);
    bus.off('permission', onPermission);
    bus.off('permission-resolved', onPermissionResolved);
    for (const t of timers.values()) clearTimeout(t);
    timers.clear();
    for (const t of backoffTimers.values()) clearTimeout(t);
    backoffTimers.clear();
    clearTimeout(flushTimer);
    flushTimer = null;
    clearTimeout(saveTimer);
    flushNow();
  }

  return {
    events,
    scan,
    list,
    get,
    save,
    remove,
    plan,
    start,
    continue: continueJob,
    pause,
    resume,
    stop,
    retry,
    skip,
    allow,
    review,
    finish,
    exportMarkdown,
    inboxItem,
    playbooks,
    savePlaybook,
    removePlaybook,
    startFromPlaybook,
    pump,
    flush,
    summary,
    begin: start_,
    end: stop_,
    _data: data,
  };
}
