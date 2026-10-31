/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// Dispatch (server/dispatch_core.js): the pure helpers, the workspace scan and the plan on real
// temporary repositories, and the job state machine driven through fake sessions and the bus.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EventEmitter } from 'node:events';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as git from '../../server/git.js';
import { createDispatcher, forecastFrom, groupHunks, levelsOf, manifestOf, renderBrief, ruleKey } from '../../server/dispatch_core.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const g = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', '-c', 'commit.gpgsign=false', ...args], { cwd, stdio: 'pipe' }).toString();

function mkRepo(dir: string, files: Record<string, string> = { 'README.md': '# r\n' }) {
  fs.mkdirSync(dir, { recursive: true });
  execFileSync('git', ['init', '-q', '-b', 'main', dir], { stdio: 'pipe' });
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    fs.writeFileSync(path.join(dir, rel), text);
  }
  g(dir, 'add', '-A');
  g(dir, 'commit', '-q', '-m', 'init');
  return dir;
}

// ---------------------------------------------------------------- fakes

class FakeSession {
  id: string;
  cwd: string;
  q = true;
  pending = new Map<string, { req: any }>();
  sent: string[] = [];
  decisions: any[] = [];
  usage = { costUsd: 0 };
  lastReply: { text: string } | null = null;
  messages: any[] = [];
  activity: any = null;
  closed = false;
  interrupted = 0;
  dispatch: any = null;
  bus: EventEmitter;
  constructor(id: string, cwd: string, bus: EventEmitter) {
    this.id = id;
    this.cwd = cwd;
    this.bus = bus;
  }
  async send({ text }: { text: string }) {
    this.sent.push(text);
    return 'u';
  }
  respond(requestId: string, decision: any) {
    const p = this.pending.get(requestId);
    if (!p) return false;
    this.pending.delete(requestId);
    this.decisions.push({ requestId, decision });
    this.bus.emit('permission-resolved', { id: this.id, requestId, decision: decision.behavior });
    return true;
  }
  async interrupt() {
    this.interrupted++;
  }
  close() {
    this.closed = true;
  }
  // the test drives the session: it asks, it says, it ends its turn
  ask(req: any) {
    const r = { requestId: req.requestId || `req-${Math.random().toString(36).slice(2, 8)}`, interactive: false, at: Date.now(), ...req };
    this.pending.set(r.requestId, { req: r });
    this.bus.emit('permission', { id: this.id, request: r });
    return r.requestId;
  }
  say(text: string, cost = 0.01) {
    this.lastReply = { text };
    this.usage.costUsd += cost;
  }
  idle() {
    this.bus.emit('state', { id: this.id, status: 'idle', usage: this.usage, lastReply: this.lastReply, activity: null });
  }
  state(status: string, extra: any = {}) {
    this.bus.emit('state', { id: this.id, status, usage: this.usage, lastReply: this.lastReply, activity: null, ...extra });
  }
}

class FakeSessions {
  all = new Map<string, FakeSession>();
  created: any[] = [];
  n = 0;
  bus: EventEmitter;
  realWorktrees: boolean;
  constructor(bus: EventEmitter, realWorktrees = false) {
    this.bus = bus;
    this.realWorktrees = realWorktrees;
  }
  async create(p: any) {
    this.created.push(p);
    const id = `s${++this.n}`;
    let worktree: any = null;
    let cwd = p.cwd;
    if (p.worktree) {
      if (this.realWorktrees) {
        const info = await git.info(p.cwd);
        const dir = path.join(path.dirname(p.cwd), '.wt', `${path.basename(p.cwd)}-${id}`);
        worktree = await git.createWorktree({ repo: info.root, base: p.worktree.base || 'main', branch: `pilcrow/${p.worktree.name}`, dir });
        cwd = worktree.path;
      } else {
        worktree = { path: `${p.cwd}/.wt/${id}`, branch: `pilcrow/${p.worktree.name}`, base: p.worktree.base || 'main', repoRoot: p.cwd };
        cwd = worktree.path;
      }
    }
    const s = new FakeSession(id, cwd, this.bus);
    this.all.set(id, s);
    return { id, cwd, worktree };
  }
  find(id: string) {
    return this.all.get(id);
  }
  async get(id: string) {
    const s = this.all.get(id);
    if (!s) throw new Error('no session');
    return s;
  }
  last() {
    return [...this.all.values()].at(-1)!;
  }
}

function harness({ realWorktrees = false, file = null as string | null, shell = null as any } = {}) {
  const bus = new EventEmitter();
  const sessions = new FakeSessions(bus, realWorktrees);
  const inbox: any[] = [];
  const shellCalls: any[] = [];
  const meta: Record<string, any> = {};
  const store = { meta, setMeta: (id: string, patch: any) => (meta[id] = { ...(meta[id] || {}), ...patch }), getMeta: (id: string) => meta[id] || null, getConfig: () => ({}) };
  const d = createDispatcher({
    file,
    sessions,
    bus,
    store,
    git,
    pushInbox: (item: any) => inbox.push(item),
    shell: async (cmd: string, cwd: string, timeout: number) => {
      shellCalls.push({ cmd, cwd, timeout });
      return shell ? shell(cmd, cwd) : { code: 0, output: 'ok' };
    },
    run: async () => ({ code: 1, stdout: '', stderr: 'no gh in tests' }),
    which: () => null,
    textOfContent: (c: any) => (Array.isArray(c) ? c.filter((x) => x.type === 'text').map((x) => x.text).join('\n') : ''),
  });
  d.begin();
  return { d, bus, sessions, inbox, shellCalls, store };
}

let root: string;
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-dispatch-'));
});
afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

// ---------------------------------------------------------------- pure helpers

describe('renderBrief', () => {
  it('substitutes the variables and appends what was learned and what dependencies did', () => {
    const out = renderBrief('Do it in {{repo.name}} ({{repo.path}}) on {{repo.branch}} for {{job.name}}. {{notes}} {{unknown}}', {
      job: { name: 'Bump' },
      target: { name: 'web', path: '/w', base: 'main', notes: 'Careful.' },
      learned: [{ name: 'cli', summary: 'Changed eslint.config.js' }],
      deps: [{ name: 'ds', summary: 'Published 2.0' }],
    });
    expect(out).toContain('Do it in web (/w) on main for Bump. Careful. {{unknown}}');
    expect(out).toContain('depends on were done first:\n- ds: Published 2.0');
    expect(out).toContain('already done in one repository');
    expect(out).toContain('- cli: Changed eslint.config.js');
  });
});

describe('ruleKey', () => {
  it('one key for the same request from two repositories', () => {
    expect(ruleKey({ toolName: 'Bash', input: { command: 'npm   install\n' } })).toBe('Bash:npm install');
    expect(ruleKey({ toolName: 'Edit', input: { file_path: '/wt/a/src/x.ts' } }, '/wt/a')).toBe('Edit:src/x.ts');
    expect(ruleKey({ toolName: 'Edit', input: { file_path: '/wt/b/src/x.ts' } }, '/wt/b')).toBe('Edit:src/x.ts');
    expect(ruleKey({ toolName: 'WebFetch', input: { url: 'https://docs.example.com/a?b=1' } })).toBe('WebFetch:docs.example.com');
    expect(ruleKey({ toolName: 'Glob', input: { pattern: '**/*.ts', path: '/wt/a/src' } }, '/wt/a')).toBe('Glob:{"path":"src","pattern":"**/*.ts"}');
    expect(ruleKey({ toolName: 'Bash', input: { command: 'rm -rf x' } })).not.toBe(ruleKey({ toolName: 'Bash', input: { command: 'rm -rf y' } }));
  });
});

describe('levelsOf', () => {
  it('orders dependents after their dependencies and parks a cycle last', () => {
    const { level, levels, cyclic } = levelsOf([
      { path: 'a', deps: [] },
      { path: 'b', deps: ['a'] },
      { path: 'c', deps: ['b', 'zzz-not-a-target'] },
      { path: 'd', deps: ['e'] },
      { path: 'e', deps: ['d'] },
    ]);
    expect([level.get('a'), level.get('b'), level.get('c')]).toEqual([0, 1, 2]);
    expect(cyclic.sort()).toEqual(['d', 'e']);
    expect(level.get('d')).toBe(3);
    expect(levels).toBe(4);
  });
});

describe('groupHunks and forecastFrom', () => {
  it('reads the same hunk in several repositories once', () => {
    const hunk = { lines: [{ t: ' ', s: 'x' }, { t: '-', s: 'a' }, { t: '+', s: 'b' }] };
    const r = groupHunks([
      { runId: '1', name: 'one', files: [{ path: 'eslint.config.js', add: 1, del: 1, hunks: [hunk] }, { path: 'only-one.ts', add: 2, del: 0, hunks: [{ lines: [{ t: '+', s: 'q' }] }] }] },
      { runId: '2', name: 'two', files: [{ path: 'eslint.config.js', add: 1, del: 1, hunks: [hunk] }] },
    ]);
    expect(r.same).toHaveLength(1);
    expect(r.same[0].runs).toEqual(['one', 'two']);
    expect(r.differ).toEqual([{ name: 'one', files: [{ path: 'only-one.ts', add: 2, del: 0 }] }]);
    expect(r.files).toBe(3);
  });
  it('scales the canary by size class and divides the time by the parallel setting', () => {
    const f = forecastFrom([{ state: 'done', size: 'small', cost: 0.1, durationMs: 60e3, name: 'c' }], [{ size: 'medium' }, { size: 'large' }, { size: 'small' }], 2)!;
    expect(f.cost).toBe(0.7);
    expect(f.minutes).toBe(4); // 7 units × 1 min ÷ 2
    expect(f.from).toEqual(['c']);
  });
});

describe('manifestOf', () => {
  it('reads local dependencies from package.json, go.mod and Cargo.toml', () => {
    const dir = path.join(root, 'm');
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name: '@acme/web', dependencies: { '@acme/ds': 'file:../ds', react: '^18' } }));
    fs.writeFileSync(path.join(dir, 'go.mod'), 'module acme/web\n\nrequire acme/core v0.1.0\n\nreplace acme/core => ../core\n');
    fs.writeFileSync(path.join(dir, 'Cargo.toml'), '[package]\nname = "web"\n\n[dependencies]\ncore = { path = "../core-rs" }\nserde = "1"\n');
    const m = manifestOf(dir);
    expect(m.kind).toBe('node');
    expect(m.name).toBe('@acme/web');
    expect(m.localDeps.map((p) => path.basename(p)).sort()).toEqual(['core', 'core-rs', 'ds']);
    expect(m.depNames).toContain('@acme/ds');
    expect(m.depNames).toContain('acme/core');
    expect(m.depNames).toContain('core');
  });
});

// ---------------------------------------------------------------- the workspace

describe('scan and plan on real repositories', () => {
  it('finds the repositories, their state and the dependencies between them', async () => {
    const ds = mkRepo(path.join(root, 'ds'), { 'package.json': JSON.stringify({ name: '@acme/ds' }), 'CLAUDE.md': 'hi' });
    const web = mkRepo(path.join(root, 'web'), { 'package.json': JSON.stringify({ name: '@acme/web', dependencies: { '@acme/ds': 'file:../ds' } }) });
    const api = mkRepo(path.join(root, 'api'), { 'go.mod': 'module acme/api\n' });
    fs.writeFileSync(path.join(api, 'dirty.txt'), 'x'); // uncommitted
    fs.mkdirSync(path.join(root, 'not-a-repo', 'node_modules', 'deep'), { recursive: true });
    mkRepo(path.join(root, 'group', 'nested'));
    const { d } = harness();
    const r = await d.scan({ root, depth: 3 });
    expect(r.repos.map((x: any) => x.name)).toEqual(['api', 'ds', 'nested', 'web']);
    const byName = Object.fromEntries(r.repos.map((x: any) => [x.name, x]));
    expect(byName.ds.branch).toBe('main');
    expect(byName.ds.hasClaudeMd).toBe(true);
    expect(byName.ds.kind).toBe('node');
    expect(byName.api.dirty).toBe(true);
    expect(byName.api.kind).toBe('go');
    expect(byName.web.deps).toEqual([ds]);
    expect(byName.web.size).toBe('small');
    expect(byName.ds.dirty).toBe(false);
    // cached: the second call does not walk again within a minute
    const again = await d.scan({ root, depth: 3 });
    expect(again).toBe(r);
    const forced = await d.scan({ root, depth: 3, force: true });
    expect(forced).not.toBe(r);
    void web;
  });

  it('plans: blocked, warnings, notes, levels and the worst case', async () => {
    const ds = mkRepo(path.join(root, 'ds'));
    const web = mkRepo(path.join(root, 'web'));
    fs.writeFileSync(path.join(web, 'dirty.txt'), 'x');
    const { d } = harness();
    const job = d.save({
      name: 'Bump eslint',
      brief: 'Upgrade {{repo.name}}.',
      targets: [
        { path: ds, name: 'ds', base: 'main' },
        { path: web, name: 'web', base: 'main', deps: [ds] },
        { path: path.join(root, 'nope'), name: 'nope', base: 'main' },
        { path: ds, name: 'ds-release', base: 'release/4' },
      ],
      settings: { parallel: 2, maxMinutes: 10, costCap: 5 },
    });
    const p = await d.plan(job.id);
    const by = Object.fromEntries(p.targets.map((t: any) => [t.name, t]));
    expect(by.ds.verdict).toBe('ok');
    expect(by.ds.brief).toBe('Upgrade ds.');
    expect(by.web.verdict).toBe('warning');
    expect(by.web.notes.map((n: any) => n.kind)).toContain('warning');
    expect(by.web.level).toBe(1);
    expect(by.web.notes.some((n: any) => /Runs after ds/.test(n.text))).toBe(true);
    expect(by.nope.verdict).toBe('blocked');
    expect(by.nope.notes[0].text).toBe('The folder does not exist.');
    expect(by['ds-release'].verdict).toBe('blocked');
    expect(by['ds-release'].notes[0].text).toMatch(/Branch release\/4 not found/);
    expect(p.worst).toEqual({ minutes: 10, cost: 5, runs: 2, levels: 2 });
    expect(p.blocked).toBe(2);
    expect(d.get(job.id).job.state).toBe('planned');
    await expect(d.start(job.id)).rejects.toThrow(/2 targets are blocked/);
  });
});

// ---------------------------------------------------------------- the job

async function jobWithRepos(h: ReturnType<typeof harness>, settings: any = {}, brief = 'Do the thing in {{repo.name}}.', deps = true) {
  const a = mkRepo(path.join(root, 'a'));
  const b = mkRepo(path.join(root, 'b'));
  const c = mkRepo(path.join(root, 'c'));
  const job = h.d.save({
    name: 'Bump eslint',
    brief,
    targets: [
      { path: a, name: 'a', base: 'main', size: 'small' },
      { path: b, name: 'b', base: 'main', size: 'medium', deps: deps ? [a] : [] },
      { path: c, name: 'c', base: 'main', size: 'small' },
    ],
    settings: { parallel: 3, maxMinutes: 10, canary: 1, ...settings },
  });
  return { job, a, b, c };
}

describe('a job through its life', () => {
  it('canary first, one answer for every repository, dependents after dependencies, a report at the end', async () => {
    const h = harness();
    const { job } = await jobWithRepos(h);
    const changes: any[] = [];
    h.d.events.on('changed', (e) => changes.push(e));
    await h.d.start(job.id);
    await sleep(40);
    let { job: j, runs } = h.d.get(job.id);
    expect(j.state).toBe('running');
    expect(j.wave).toBe('canary');
    const canary = runs.find((r: any) => r.wave === 'canary')!;
    expect(canary.name).toBe('a'); // level 0, smallest, first by name
    expect(canary.state).toBe('running');
    expect(runs.filter((r: any) => r.state === 'queued')).toHaveLength(2);
    const s1 = h.sessions.last();
    expect(h.sessions.created[0]).toMatchObject({ worktree: { name: 'bump-eslint', base: 'main' }, mode: 'acceptEdits', title: 'Bump eslint · a' });
    expect(h.store.getMeta('s1').dispatch).toMatchObject({ jobId: job.id, runId: canary.id, target: 'a' });
    expect(s1.sent[0]).toBe('Do the thing in a.');

    // the canary asks: the run waits, with the request's key on it
    const rid = s1.ask({ toolName: 'Bash', input: { command: 'npm install' }, displayName: 'Bash' });
    await sleep(5);
    ({ runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('needs_action');
    expect(runs[0].pending[0]).toMatchObject({ requestId: rid, key: 'Bash:npm install', label: 'Bash: npm install' });
    expect(h.d.get(job.id).job.needsAction).toBe(1);
    // ...and is answered once for every repository
    h.d.allow(job.id, 'Bash:npm install');
    expect(s1.decisions[0].decision.behavior).toBe('allow');
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('running');
    expect(j.rules).toEqual([expect.objectContaining({ key: 'Bash:npm install', label: 'Bash: npm install', count: 1 })]);

    s1.say('Upgraded eslint in a.', 0.2);
    s1.idle();
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('done');
    expect(runs[0].summary).toBe('Upgraded eslint in a.');
    expect(runs[0].cost).toBe(0.2);
    expect(j.state).toBe('reviewing');
    expect(j.wave).toBe('canary');
    expect(j.canaryWaiting).toBe(true);
    expect(j.learned).toEqual([{ name: 'a', summary: 'Upgraded eslint in a.' }]);
    expect(j.forecast).toMatchObject({ runs: 2, from: ['a'] });
    expect(j.forecast.cost).toBeCloseTo(0.6, 5); // medium (2) + small (1) at 0.2 per unit

    // continue: the main wave learns from the canary; b waits for nothing (a is done) so both start
    h.d.continue(job.id, { learn: true });
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(j.state).toBe('running');
    expect(j.wave).toBe('main');
    expect(runs.filter((r: any) => r.state === 'running').map((r: any) => r.name).sort()).toEqual(['b', 'c']);
    const sb = h.sessions.find('s2')!;
    const sc = h.sessions.find('s3')!;
    const briefB = [sb, sc].find((s) => s.sent[0].startsWith('Do the thing in b.'))!.sent[0];
    expect(briefB).toContain('Repositories this one depends on were done first:\n- a: Upgraded eslint in a.');
    expect(briefB).toContain('already done in one repository');
    // the rule answers the same request from the next repository without asking
    sb.ask({ toolName: 'Bash', input: { command: 'npm  install' } });
    await sleep(5);
    expect(sb.decisions).toHaveLength(1);
    expect(h.d.get(job.id).runs.find((r: any) => r.sessionId === sb.id).state).toBe('running');
    // a different request still asks
    sc.ask({ toolName: 'Bash', input: { command: 'rm -rf node_modules' } });
    await sleep(5);
    expect(h.d.get(job.id).runs.find((r: any) => r.sessionId === sc.id).state).toBe('needs_action');
    sc.respond([...sc.pending.keys()][0], { behavior: 'deny' });
    await sleep(5);
    expect(h.d.get(job.id).runs.find((r: any) => r.sessionId === sc.id).state).toBe('running');

    sb.say('b done', 0.3);
    sb.idle();
    sc.say('c done', 0.1);
    sc.idle();
    await sleep(60);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs.map((r: any) => r.state)).toEqual(['done', 'done', 'done']);
    expect(j.state).toBe('reviewing');
    expect(j.wave).toBe('done');
    expect(j.cost).toBeCloseTo(0.6, 5);
    expect(h.inbox).toHaveLength(0);

    // finishing: keep everything → the job is finished and the report is in the inbox
    const f = await h.d.finish(job.id, { action: 'keep' });
    expect(f.results.map((r: any) => r.ok)).toEqual([true, true, true]);
    ({ job: j } = h.d.get(job.id));
    expect(j.state).toBe('finished');
    expect(h.inbox).toHaveLength(1);
    expect(h.inbox[0]).toMatchObject({ kind: 'dispatch', scheduleName: 'Bump eslint', status: 'completed', jobId: job.id });
    expect(h.inbox[0].dispatch.targets.map((t: any) => t.name)).toEqual(['a', 'b', 'c']);
    expect(h.inbox[0].dispatch.rules[0].key).toBe('Bash:npm install');
    const md = h.d.exportMarkdown(job.id);
    expect(md).toContain('# Bump eslint');
    expect(md).toContain('| a | done |');
    expect(md).toContain('Allowed for every repository');
    // the events named the job and the runs that changed
    expect(changes.some((e) => e.id === job.id && e.runs.length)).toBe(true);
    h.d.end();
  });

  it('prepare failing fails the run before anything is sent; verify failing goes back to the agent once', async () => {
    let prepareCode = 1;
    const verifyCodes = [1, 0];
    const h = harness({
      shell: (cmd: string) => (cmd === 'npm ci' ? { code: prepareCode, output: 'E404 nope' } : { code: verifyCodes.shift() ?? 0, output: cmd === 'npm test' ? '1 failing' : '' }),
    });
    const { job } = await jobWithRepos(h, { canary: 0, prepare: 'npm ci', verify: 'npm test', parallel: 1 });
    await h.d.start(job.id);
    await sleep(40);
    let { runs } = h.d.get(job.id);
    const first = runs.find((r: any) => r.name === 'a')!;
    expect(first.state).toBe('failed');
    expect(first.reason).toMatch(/Prepare failed \(exit 1\): E404 nope/);
    expect(h.sessions.find('s1')!.sent).toEqual([]);
    expect(h.shellCalls[0]).toMatchObject({ cmd: 'npm ci' });
    // b depends on a, which failed: skipped; c goes next
    await sleep(40);
    ({ runs } = h.d.get(job.id));
    expect(runs.find((r: any) => r.name === 'b')!.state).toBe('skipped');
    expect(runs.find((r: any) => r.name === 'b')!.reason).toMatch(/Depends on a/);
    prepareCode = 0;
    // prepare passed this time? no: c already started with the failing code... let c retry
    const c = runs.find((r: any) => r.name === 'c')!;
    expect(c.state).toBe('failed');
    const retried = h.d.retry(job.id, c.id);
    await sleep(40);
    ({ runs } = h.d.get(job.id));
    const cr = runs.find((r: any) => r.id === retried.id)!;
    expect(cr.state).toBe('running');
    expect(cr.attempt).toBe(2);
    const sc = h.sessions.last();
    expect(sc.sent).toHaveLength(1);
    // the turn ends: verify fails once → the output goes back; the second time it passes → done
    sc.say('c done', 0.05);
    sc.idle();
    await sleep(30);
    ({ runs } = h.d.get(job.id));
    expect(runs.find((r: any) => r.id === retried.id)!.state).toBe('running');
    expect(sc.sent).toHaveLength(2);
    expect(sc.sent[1]).toMatch(/verify command `npm test` failed with exit code 1/);
    expect(sc.sent[1]).toContain('1 failing');
    sc.idle();
    await sleep(40);
    ({ runs } = h.d.get(job.id));
    const done = runs.find((r: any) => r.id === retried.id)!;
    expect(done.state).toBe('done');
    expect(done.verify).toMatchObject({ ran: true, code: 0 });
    expect(h.d.get(job.id).job.state).toBe('reviewing');
    h.d.end();
  });

  it('a cost cap stops new runs, stop ends the active ones and skips the queued, a rate limit backs off', async () => {
    const h = harness();
    const { job } = await jobWithRepos(h, { canary: 0, parallel: 1, costCap: 0.15 });
    await h.d.start(job.id);
    await sleep(40);
    let { job: j, runs } = h.d.get(job.id);
    expect(runs.filter((r: any) => r.state === 'running')).toHaveLength(1);
    const s1 = h.sessions.last();
    // a rate limit: the run goes back to the queue and the job waits
    s1.state('error', { error: 'API rate limit reached (429)' });
    await sleep(5);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('queued');
    expect(runs[0].attempt).toBe(2);
    expect(j.backoffUntil).toBeGreaterThan(Date.now());
    expect(s1.closed).toBe(true);
    // let the backoff pass quickly
    j = h.d._data.jobs[0];
    j.backoffUntil = Date.now() - 1;
    h.d.pump();
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('running');
    const s2 = h.sessions.last();
    s2.say('a done', 0.2); // over the cap
    s2.idle();
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs[0].state).toBe('done');
    expect(j.capReached).toBe(true);
    expect(runs.filter((r: any) => r.state === 'queued')).toHaveLength(2);
    expect(j.state).toBe('running');
    // a raise of the cap resumes
    h.d.save({ id: job.id, settings: { costCap: 10 } });
    h.d.resume(job.id);
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(runs.filter((r: any) => r.state === 'running')).toHaveLength(1);
    expect(j.capReached).toBe(false);
    // stop: the active run is interrupted and stopped, the queued one skipped
    h.d.stop(job.id);
    await sleep(40);
    ({ job: j, runs } = h.d.get(job.id));
    expect(h.sessions.last().interrupted).toBe(1);
    expect(runs.map((r: any) => r.state).sort()).toEqual(['done', 'skipped', 'stopped']);
    expect(j.state).toBe('stopped');
    h.d.end();
  });

  it('after a restart a run whose session is gone reads stopped, and the job can be finished', async () => {
    const file = path.join(root, 'dispatch.json');
    const h = harness({ file });
    const { job } = await jobWithRepos(h, { canary: 0 });
    await h.d.start(job.id);
    await sleep(40);
    h.d.end(); // flushes to disk
    const bus2 = new EventEmitter();
    const sessions2 = new FakeSessions(bus2);
    const inbox2: any[] = [];
    const d2 = createDispatcher({ file, sessions: sessions2, bus: bus2, store: { meta: {}, setMeta: () => null, getMeta: () => null, getConfig: () => ({}) }, git, pushInbox: (i: any) => inbox2.push(i), shell: async () => ({ code: 0, output: '' }), run: async () => ({ code: 1, stdout: '', stderr: '' }) });
    d2.begin();
    await sleep(40);
    const { job: j, runs } = d2.get(job.id);
    // a and c were running; b waited for a, which did not finish, so it is skipped
    expect(runs.filter((r: any) => r.state === 'stopped' && r.reason === 'The server restarted').map((r: any) => r.name)).toEqual(['a', 'c']);
    expect(runs.find((r: any) => r.name === 'b')).toMatchObject({ state: 'skipped', reason: expect.stringMatching(/Depends on a/) });
    // nothing is left to land, so the job is finished, with a failed report in the inbox
    expect(j.state).toBe('finished');
    expect(inbox2).toHaveLength(1);
    expect(inbox2[0]).toMatchObject({ kind: 'dispatch', status: 'failed', reason: '2 of 3 did not finish' });
    d2.end();
  });
});

describe('finishing on real worktrees', () => {
  it('commits on the run branch, keeps the branch and removes the directory; discard removes both; review groups the same change', async () => {
    const h = harness({ realWorktrees: true });
    const { job, a, b } = await jobWithRepos(h, { canary: 0, parallel: 3, commitMessage: 'chore: {{job.name}} in {{repo.name}}' }, undefined, false);
    await h.d.start(job.id);
    let { runs } = h.d.get(job.id);
    for (let i = 0; i < 100 && !runs.every((r: any) => r.state === 'running'); i++) {
      await sleep(30); // three real worktrees take a moment
      ({ runs } = h.d.get(job.id));
    }
    expect(runs.every((r: any) => r.state === 'running')).toBe(true);
    // the agents change files: the same change in a and b, another in c
    for (const r of runs) {
      const wt = r.worktree.path;
      expect(fs.existsSync(wt)).toBe(true);
      if (r.name !== 'c') fs.writeFileSync(path.join(wt, 'eslint.config.js'), 'export default [];\n');
      else fs.writeFileSync(path.join(wt, 'README.md'), '# r\n\nmore\n');
      const s = h.sessions.find(r.sessionId)!;
      s.say(`done in ${r.name}`, 0.01);
      s.idle();
    }
    for (let i = 0; i < 100 && !runs.every((r: any) => r.state === 'done' && r.files.length); i++) {
      await sleep(30);
      ({ runs } = h.d.get(job.id));
    }
    expect(runs.map((r: any) => r.state)).toEqual(['done', 'done', 'done']);
    expect(runs.find((r: any) => r.name === 'a')!.files).toEqual([{ path: 'eslint.config.js', add: 1, del: 0 }]);
    const rv = await h.d.review(job.id);
    expect(rv.same).toHaveLength(1);
    expect(rv.same[0]).toMatchObject({ path: 'eslint.config.js', runs: ['a', 'b'] });
    expect(rv.differ).toEqual([{ name: 'c', files: [{ path: 'README.md', add: 2, del: 0 }] }]);
    // commit a and b, discard c
    const ra = runs.find((r: any) => r.name === 'a')!;
    const rc = runs.find((r: any) => r.name === 'c')!;
    const fa = await h.d.finish(job.id, { runId: ra.id, action: 'commit' });
    expect(fa.results[0].ok).toBe(true);
    ({ runs } = h.d.get(job.id));
    const a2 = runs.find((r: any) => r.name === 'a')!;
    expect(a2.finished).toBe('commit');
    expect(a2.commit).toMatch(/^[0-9a-f]{7,}$/);
    expect(fs.existsSync(a2.worktree.path)).toBe(false);
    expect(g(a, 'log', '--oneline', 'pilcrow/bump-eslint')).toContain('chore: Bump eslint in a');
    expect(g(a, 'branch', '--list', 'pilcrow/bump-eslint').trim()).toBe('pilcrow/bump-eslint');
    const fc = await h.d.finish(job.id, { runId: rc.id, action: 'discard' });
    expect(fc.results[0].ok).toBe(true);
    ({ runs } = h.d.get(job.id));
    const c2 = runs.find((r: any) => r.name === 'c')!;
    expect(fs.existsSync(c2.worktree.path)).toBe(false);
    expect(g(path.join(root, 'c'), 'branch', '--list', 'pilcrow/bump-eslint').trim()).toBe('');
    expect(h.d.get(job.id).job.state).toBe('reviewing'); // b is still open
    const fb = await h.d.finish(job.id, { action: 'commit' }); // everything left that is done
    expect(fb.results.map((r: any) => r.name)).toEqual(['b']);
    expect(g(b, 'log', '--oneline', 'pilcrow/bump-eslint')).toContain('chore: Bump eslint in b');
    expect(h.d.get(job.id).job.state).toBe('finished');
    expect(h.inbox[0].dispatch.targets.map((t: any) => t.finished)).toEqual(['commit', 'commit', 'discard']);
    h.d.end();
  });
});
