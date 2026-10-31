/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// Helpers (server/helpers.js): the pure helpers, and the lead's six tools driven through fake sessions
// and the bus, with real temporary repositories where worktrees and diffs matter.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EventEmitter } from 'node:events';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as git from '../../server/git.js';
import { createHelpers, extractHandoff, renderDiff, fmtDuration, instructions, HEADER, TOOL_PREFIX } from '../../server/helpers.js';
import { createRegistry } from '../../server/repos.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function until(cond: () => boolean, ms = 3000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('timed out waiting');
    await sleep(15);
  }
}
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
  q: any = true;
  status = 'idle';
  mode = 'acceptEdits';
  model = '';
  effort = '';
  unattended = false;
  pending = new Map<string, { req: any; resolve?: any }>();
  sent: any[] = [];
  decisions: any[] = [];
  usage = { costUsd: 0 };
  lastReply: { text: string } | null = null;
  messages: any[] = [];
  activity: any = null;
  interrupted = 0;
  closed = false;
  emitted = 0;
  bus: EventEmitter;
  constructor(id: string, cwd: string, bus: EventEmitter) {
    this.id = id;
    this.cwd = cwd;
    this.bus = bus;
  }
  isBusy() {
    return this.status === 'running' || this.status === 'requires_action';
  }
  async send(m: any) {
    this.sent.push(m);
    this.messages.push({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: m.text }] }, _local: true, ...(m.note ? { _note: m.note } : {}), ...(m.from ? { _from: m.from } : {}) });
    return 'u';
  }
  respond(requestId: string, decision: any) {
    const p = this.pending.get(requestId);
    if (!p) return false;
    this.pending.delete(requestId);
    this.decisions.push({ requestId, decision });
    this.bus.emit('permission-resolved', { id: this.id, requestId, decision: decision.behavior });
    if (!this.pending.size) this.status = 'running';
    return true;
  }
  async interrupt() {
    this.interrupted++;
    this.status = 'idle';
    this.state('idle');
  }
  close() {
    this.closed = true;
  }
  emitState() {
    this.emitted++;
  }
  // the test drives the session: it asks, it says, it ends its turn
  ask(req: any) {
    const r = { requestId: req.requestId || `req-${Math.random().toString(36).slice(2, 8)}`, interactive: false, at: Date.now(), ...req };
    this.pending.set(r.requestId, { req: r });
    this.status = 'requires_action';
    this.bus.emit('permission', { id: this.id, request: r });
    this.state('requires_action');
    return r.requestId;
  }
  say(text: string, cost = 0.01) {
    this.messages.push({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text }] } });
    this.lastReply = { text: text.slice(0, 240) };
    this.usage.costUsd += cost;
  }
  tool(name: string, input: any) {
    this.messages.push({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', name, input, id: 't' }] } });
    this.messages.push({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't', content: 'ok' }] } });
  }
  run() {
    this.status = 'running';
    this.state('running');
  }
  idle() {
    this.status = 'idle';
    this.state('idle');
  }
  state(status: string, extra: any = {}) {
    this.bus.emit('state', { id: this.id, status, live: Boolean(this.q), usage: this.usage, lastReply: this.lastReply, activity: this.activity, pending: [...this.pending.values()].map((p) => p.req), ...extra });
  }
}

class FakeSessions {
  all = new Map<string, FakeSession>();
  created: any[] = [];
  n = 0;
  bus: EventEmitter;
  realWorktrees: boolean;
  store: any;
  constructor(bus: EventEmitter, store: any, realWorktrees = false) {
    this.bus = bus;
    this.store = store;
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
      }
    }
    const s = new FakeSession(id, cwd, this.bus);
    s.mode = p.mode || 'default';
    this.all.set(id, s);
    this.store.setMeta(id, { cwd, project: p.cwd, title: p.title, worktree });
    return { id, cwd, worktree };
  }
  find(id: string) {
    return this.all.get(id) || null;
  }
  async get(id: string) {
    const s = this.all.get(id);
    if (!s) throw new Error('Unknown session: ' + id);
    return s;
  }
  async remove(id: string) {
    const s = this.all.get(id);
    const meta = this.store.getMeta(id);
    this.all.delete(id);
    this.store.deleteMeta(id);
    this.bus.emit('removed', { id, meta });
    void s;
  }
  lead(id: string, cwd: string, extra: any = {}) {
    const s = new FakeSession(id, cwd, this.bus);
    this.all.set(id, s);
    this.store.setMeta(id, { cwd, project: cwd, title: `Lead ${id}`, ...extra });
    return s;
  }
}

function fakeStore() {
  const config: any = { repos: [], helpers: { enabled: true, total: 5, parallel: 2, maxMinutes: 30, costUsd: 0, wakeLead: true }, recentProjects: [] };
  const meta: Record<string, any> = {};
  return {
    config,
    meta,
    getConfig: () => config,
    setConfig: (patch: any) => Object.assign(config, patch),
    getMeta: (id: string) => meta[id] || null,
    setMeta(id: string, patch: any) {
      const next = { ...(meta[id] || {}), ...patch };
      for (const [k, v] of Object.entries(next)) if (v === undefined || v === null) delete (next as any)[k];
      meta[id] = next;
      return next;
    },
    deleteMeta(id: string) {
      delete meta[id];
    },
  };
}

let tmp: string;
let api: string;
let web: string;
let store: ReturnType<typeof fakeStore>;
let bus: EventEmitter;
let sessions: FakeSessions;
let reg: ReturnType<typeof createRegistry>;
let inbox: any[];
let H: ReturnType<typeof createHelpers>;

function make(realWorktrees = false) {
  bus = new EventEmitter();
  store = fakeStore();
  sessions = new FakeSessions(bus, store, realWorktrees);
  reg = createRegistry({ store, git, expandHome: (p: string) => p });
  inbox = [];
  H = createHelpers({
    sessions,
    bus,
    store,
    git,
    registry: reg,
    pushInbox: (item: any) => {
      inbox.push(item);
      return item.id;
    },
    updateInbox: (id: string, patch: any) => {
      const it = inbox.find((i) => i.id === id);
      if (!it) return false;
      Object.assign(it, patch);
      return true;
    },
    expandHome: (p: string) => p,
    homeDir: '/home/nobody',
    cpus: 4,
    noteMs: 20,
  });
  H.begin();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-helpers-'));
  api = mkRepo(path.join(tmp, 'code', 'api'), { 'package.json': JSON.stringify({ name: '@shop/api' }), 'src/routes.ts': 'export const r = 1;\n' });
  web = mkRepo(path.join(tmp, 'code', 'web'), { 'package.json': JSON.stringify({ name: '@shop/web' }), 'src/page.tsx': 'export const p = 1;\n' });
  make();
});
afterEach(() => {
  H.end();
  fs.rmSync(tmp, { recursive: true, force: true });
});

const lead = (id = 'lead', cwd = path.join(tmp, 'code', 'lead')) => {
  fs.mkdirSync(cwd, { recursive: true });
  return sessions.lead(id, cwd);
};
const helperSession = (name: string) => sessions.all.get(H._helpers.get([...H._helpers.values()].find((h: any) => h.name === name)!.id)!.id)!;

// ---------------------------------------------------------------- pure

describe('pure helpers', () => {
  it('extracts the Handoff section, else the whole reply, bounded', () => {
    expect(extractHandoff('I did things.\n\n## Handoff\nAdded GET /x.\nVerify: npm test')).toBe('## Handoff\nAdded GET /x.\nVerify: npm test');
    expect(extractHandoff('Done.\n\n**Handoff**\n- a\n- b')).toBe('**Handoff**\n- a\n- b');
    expect(extractHandoff('No section here')).toBe('No section here');
    expect(extractHandoff('x'.repeat(5000)).length).toBe(4000);
    expect(extractHandoff('')).toBe('');
  });
  it('renders a diff within a budget, one file on request', () => {
    const files = [
      { path: 'a.ts', hunks: [{ oldStart: 1, oldLines: 1, newStart: 1, newLines: 2, lines: [{ t: ' ', s: 'x' }, { t: '+', s: 'y' }] }] },
      { path: 'b.ts', hunks: [{ oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: [{ t: '-', s: 'q' }, { t: '+', s: 'r' }] }] },
    ];
    const all = renderDiff(files, { maxChars: 10000 });
    expect(all.truncated).toBe(false);
    expect(all.files).toBe(2);
    expect(all.text).toMatch(/\+\+\+ b\/a\.ts[\s\S]*\+y[\s\S]*\+\+\+ b\/b\.ts/);
    const one = renderDiff(files, { only: 'b.ts' });
    expect(one.files).toBe(1);
    expect(one.text).not.toMatch(/a\.ts/);
    const small = renderDiff(files, { maxChars: 60 });
    expect(small.truncated).toBe(true);
    expect(small.text).toMatch(/not shown|cut/);
  });
  it('formats durations and writes the instructions with the caps', () => {
    expect(fmtDuration(4000)).toBe('4s');
    expect(fmtDuration(252_000)).toBe('4m12s');
    expect(fmtDuration(3_720_000)).toBe('1h02m');
    expect(instructions({ parallel: 3, total: 8, maxMinutes: 30 })).toMatch(/3 running at once, 8 in all, 30 minutes each/);
    expect(HEADER).toMatch(/Handoff/);
  });
});

// ---------------------------------------------------------------- start

describe('start', () => {
  it('refuses what it must, with the reason', async () => {
    lead();
    await expect(H.start('lead', { name: 'Bad Name', brief: 'x', cwd: api })).rejects.toThrow(/needs a name/);
    await expect(H.start('lead', { name: 'api', brief: '', cwd: api })).rejects.toThrow(/needs a brief/);
    await expect(H.start('lead', { name: 'api', brief: 'x' })).rejects.toThrow(/needs a folder/);
    await expect(H.start('lead', { name: 'api', brief: 'x', cwd: path.join(tmp, 'nope') })).rejects.toThrow(/does not exist/);
    await expect(H.start('lead', { name: 'api', brief: 'x', cwd: '/home/nobody' })).rejects.toThrow(/home folder/);
    await reg.save({ path: web, aliases: ['order FE'] });
    await expect(H.start('lead', { name: 'web', brief: 'x', repo: 'order-fe2' })).rejects.toThrow(/No repository called “order-fe2”.*Nearest names: web/);
    await reg.save({ id: reg.resolve('web')!.id, policy: 'never' });
    await expect(H.start('lead', { name: 'web', brief: 'x', repo: 'order FE' })).rejects.toThrow(/policy Never/);
    await expect(H.start('lead', { name: 'api', brief: 'x', cwd: api, after: ['ghost'] })).rejects.toThrow(/after names ghost/);
    const u = sessions.lead('un', api);
    u.unattended = true;
    await expect(H.start('un', { name: 'api', brief: 'x', cwd: api })).rejects.toThrow(/unattended/);
    store.setMeta('hp', { helper: { leadId: 'lead', name: 'x' } });
    sessions.lead('hp', api);
    await expect(H.start('hp', { name: 'api', brief: 'x', cwd: api })).rejects.toThrow(/cannot start helpers/);
  });
  it('starts a helper as a worktree session with the header, the registry notes and the lead’s mode, narrowed', async () => {
    const L = lead();
    L.mode = 'default';
    await reg.save({ path: api, name: 'api', aliases: ['orders service'], notes: 'Run go generate first.', policy: 'allowed' });
    const r = await H.start('lead', { name: 'api', brief: 'Add GET /invoice.', repo: 'orders service', mode: 'acceptEdits' });
    expect(r.state).toBe('running');
    expect(r.worktree!.branch).toBe('pilcrow/api');
    const created = sessions.created[0];
    expect(created.cwd).toBe(api);
    expect(created.mode).toBe('default'); // never wider than the lead
    expect(created.title).toBe('api');
    const hs = sessions.all.get(r.id)!;
    expect(hs.sent).toHaveLength(1);
    expect(hs.sent[0].from).toBe('lead');
    expect(hs.sent[0].text.startsWith(HEADER)).toBe(true);
    expect(hs.sent[0].text).toMatch(/Add GET \/invoice\./);
    expect(hs.sent[0].text).toMatch(/## About this repository\nRun go generate first\./);
    expect(store.getMeta(r.id).helper.name).toBe('api');
    expect(store.getMeta('lead').lead.helpers).toEqual([r.id]);
    await expect(H.start('lead', { name: 'api', brief: 'again', cwd: api })).rejects.toThrow(/named api exists/);
    expect(H.list('lead').helpers[0]).toMatchObject({ name: 'api', state: 'running', branch: 'pilcrow/api' });
  });
  it('queues behind the parallel cap and behind after, skips when a dependency fails, and keeps the total cap', async () => {
    lead();
    const a = await H.start('lead', { name: 'a', brief: 'x', cwd: api });
    const b = await H.start('lead', { name: 'b', brief: 'x', cwd: web });
    const c = await H.start('lead', { name: 'c', brief: 'x', cwd: api, worktree: false });
    expect([a.state, b.state, c.state]).toEqual(['running', 'running', 'queued']);
    expect(c.queuedBehind).toEqual(['a', 'b']);
    const d = await H.start('lead', { name: 'd', brief: 'needs a', cwd: web, after: ['a'] });
    expect(d.queuedBehind).toEqual(['a']);
    // a finishes with a handoff: c takes its slot, d starts with a's handoff and worktree path appended
    const A = sessions.all.get(a.id)!;
    A.say('Did it.\n\n## Handoff\nAdded the route in src/routes.ts.');
    A.idle();
    await sleep(30);
    expect(H.list('lead').helpers.map((h: any) => [h.name, h.state])).toEqual([
      ['a', 'done'],
      ['b', 'running'],
      ['c', 'running'],
      ['d', 'queued'],
    ]);
    const B = sessions.all.get(b.id)!;
    B.state('error', { error: 'boom\nmore' });
    await sleep(30);
    expect(H.list('lead').helpers.find((h: any) => h.name === 'd')!.state).toBe('running');
    const D = sessions.all.get(d.id)!;
    expect(D.sent[0].text).toMatch(/## From helpers you waited for\n- a \(done, \d+s\): worktree .*api.* on branch pilcrow\/a\. Read the change there before you start\.\n {2}## Handoff\n {2}Added the route/);
    const f = await H.start('lead', { name: 'f', brief: 'needs b', cwd: web, after: ['b'] });
    expect(f.state).toBe('skipped');
    expect(H.list('lead').helpers.find((h: any) => h.name === 'f')!.reason).toMatch(/b failed/);
    await expect(H.start('lead', { name: 'g', brief: 'x', cwd: api })).rejects.toThrow(/cap is 5 helpers/);
  });
});

// ---------------------------------------------------------------- states, notes, waits

describe('states, notes and waits', () => {
  it('follows the session: needs action, done with the handoff, a note to the lead coalesced', async () => {
    const L = lead();
    await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    await H.start('lead', { name: 'web', brief: 'x', cwd: web });
    const A = helperSession('api');
    const W = helperSession('web');
    A.ask({ toolName: 'Bash', input: { command: 'npm test' } });
    expect(H.list('lead').helpers[0]).toMatchObject({ state: 'needs_action', pending: 1 });
    expect(L.sent).toHaveLength(0); // a permission is the person's, not the lead's
    A.respond([...A.pending.keys()][0], { behavior: 'allow' });
    A.run();
    A.say('Working…');
    A.tool('Edit', { file_path: 'x' });
    A.say('All done.\n\n## Handoff\nGET /invoice added. Verify: npm test.', 0.3);
    A.idle();
    W.say('## Handoff\nButton added.', 0.2);
    W.idle();
    await sleep(60);
    const [a, w] = H.list('lead').helpers;
    expect(a).toMatchObject({ name: 'api', state: 'done', cost: 0.31 });
    expect(w.state).toBe('done');
    expect(store.getMeta(a.id).helper.handoff).toBe('## Handoff\nGET /invoice added. Verify: npm test.');
    expect(L.sent).toHaveLength(1); // two notes within the window, one message
    expect(L.sent[0].note).toBe('helper');
    expect(L.sent[0].text).toMatch(/<helper-note name="api" state="done" elapsed="\d+s" cost="\$0\.31" files="0" add="0" del="0">\n## Handoff\nGET \/invoice added/);
    expect(L.sent[0].text).toMatch(/<helper-note name="web" state="done"/);
    expect(L.sent[0].text).toMatch(/No helpers are running now/);
    expect(inbox).toHaveLength(0); // nothing changed files, so no record yet
    // the lead's state carries its helpers; a helper's carries its lead
    expect(H.stateOf({ id: 'lead' }).helpers.map((h: any) => h.name)).toEqual(['api', 'web']);
    expect(H.stateOf({ id: 'lead' }).lead.cost).toBe(0.51);
    expect(H.stateOf({ id: a.id }).helper).toMatchObject({ leadId: 'lead', name: 'api', state: 'done', leadTitle: 'Lead lead' });
    expect(L.emitted).toBeGreaterThan(0);
  });
  it('wait: done ignores a pending permission, any returns on it, the timeout checks in; a covered helper is not noted', async () => {
    const L = lead();
    await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    const A = helperSession('api');
    const t = await H.wait('lead', { timeoutSec: 1 });
    expect(t.timedOut).toBe(true);
    expect(t.helpers[0].state).toBe('running');
    const any = H.wait('lead', { until: 'any', timeoutSec: 5 });
    A.ask({ toolName: 'Bash', input: { command: 'ls' } });
    expect((await any).helpers[0].state).toBe('needs_action');
    const done = H.wait('lead', { names: ['api'], timeoutSec: 5 });
    await sleep(20);
    A.respond([...A.pending.keys()][0], { behavior: 'allow' });
    A.run();
    A.say('## Handoff\nok');
    A.idle();
    const r = await done;
    expect(r.timedOut).toBe(false);
    expect(r.helpers[0].state).toBe('done');
    await sleep(60);
    expect(L.sent).toHaveLength(0); // the wait carried the news
    expect(() => H.wait('lead', { names: ['ghost'] })).toThrow(/No helper called ghost/);
  });
  it('a note wakes a closed lead unless wakeLead is off, in which case it waits for the next time it is live', async () => {
    const L = lead();
    L.q = null; // the process is closed
    store.config.helpers.wakeLead = false;
    await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    helperSession('api').say('## Handoff\nok');
    helperSession('api').idle();
    await sleep(60);
    expect(L.sent).toHaveLength(0);
    L.q = true;
    L.state('idle');
    await sleep(20);
    expect(L.sent).toHaveLength(1);
    expect(L.sent[0].text).toMatch(/helper-note name="api"/);
  });
  it('stop interrupts a running helper and ends a done one; send is now or held, tagged from the lead', async () => {
    lead();
    await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    const A = helperSession('api');
    A.run();
    expect(await H.send('lead', { name: 'api', text: 'Also the docs.' })).toMatchObject({ delivered: 'held' });
    expect(A.sent[1]).toMatchObject({ text: 'Also the docs.', from: 'lead', now: false });
    const r = await H.stop('lead', { name: 'api' });
    expect(r.state).toBe('stopped');
    expect(A.interrupted).toBe(1);
    await sleep(30);
    expect(H.list('lead').helpers[0]).toMatchObject({ state: 'stopped', reason: 'stopped by the lead' });
    await expect(H.send('lead', { name: 'ghost', text: 'x' })).rejects.toThrow(/No helper called ghost/);
  });
});

// ---------------------------------------------------------------- reading on real worktrees, review, finish

describe('on real worktrees', () => {
  beforeEach(() => make(true));
  it('reads files, the diff and the steps; reviews and commits across helpers; the inbox gets one record', async () => {
    lead();
    const a = await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    const w = await H.start('lead', { name: 'web', brief: 'x', cwd: web });
    fs.writeFileSync(path.join(a.worktree!.path, 'src', 'routes.ts'), 'export const r = 1;\nexport const invoice = 2;\n');
    fs.writeFileSync(path.join(w.worktree!.path, 'src', 'page.tsx'), 'export const p = 1;\nexport const btn = true;\n');
    const A = helperSession('api');
    A.tool('Edit', { file_path: 'src/routes.ts' });
    A.say('## Handoff\nAdded invoice.');
    A.idle();
    helperSession('web').say('## Handoff\nAdded the button.');
    helperSession('web').idle();
    await until(() => H.list('lead').helpers.every((h: any) => h.files === 1) && inbox.length === 1);
    expect(H.list('lead').helpers.map((h: any) => [h.name, h.files, h.add])).toEqual([
      ['api', 1, 1],
      ['web', 1, 1],
    ]);
    expect((await H.read('lead', { name: 'api' })).text).toBe('## Handoff\nAdded invoice.');
    expect((await H.read('lead', { name: 'api', what: 'files' })).text).toBe('src/routes.ts +1 −0');
    const diff = await H.read('lead', { name: 'api', what: 'diff' });
    expect(diff.text).toMatch(/\+\+\+ b\/src\/routes\.ts[\s\S]*\+export const invoice = 2;/);
    expect(diff.truncated).toBe(false);
    const steps = await H.read('lead', { name: 'api', what: 'steps' });
    expect(steps.text).toMatch(/GOT You are a helper[\s\S]*TOOL Edit[\s\S]*SAID ## Handoff/);
    expect((await H.read('lead', { name: 'api', what: 'last' })).text).toBe('## Handoff\nAdded invoice.');
    await expect(H.read('lead', { name: 'api', what: 'nope' })).rejects.toThrow(/what must be/);
    // the record: one item, updated as things finish
    expect(inbox).toHaveLength(1);
    expect(inbox[0]).toMatchObject({ kind: 'helpers', leadId: 'lead', status: 'completed', title: 'Lead lead' });
    expect(inbox[0].helpers.map((h: any) => h.name)).toEqual(['api', 'web']);
    // review groups by content; finish commits each worktree on its branch and removes the directory
    const rv = await H.review('lead');
    expect(rv.runs.map((r: any) => r.name)).toEqual(['api', 'web']);
    const fin = await H.finish('lead', { action: 'commit', message: 'Invoice download' });
    expect(fin.results.every((r: any) => r.ok)).toBe(true);
    expect(fs.existsSync(a.worktree!.path)).toBe(false);
    expect(g(api, 'log', '--oneline', 'pilcrow/api')).toMatch(/Invoice download/);
    expect(H.list('lead').helpers[0]).toMatchObject({ finished: 'commit' });
    expect(H.list('lead').helpers[0].commit).toMatch(/^[0-9a-f]{7,}$/);
    expect(inbox).toHaveLength(1);
    const md = H.exportMarkdown('lead');
    expect(md).toMatch(/# Lead lead: helpers[\s\S]*## api — done[\s\S]*## Handoff\nAdded invoice\.[\s\S]*## web — done/);
  });
  it('discard removes the worktree and the branch; keep leaves both', async () => {
    lead();
    const a = await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    const w = await H.start('lead', { name: 'web', brief: 'x', cwd: web });
    helperSession('api').idle();
    helperSession('web').idle();
    await until(() => H.list('lead').helpers.every((h: any) => h.state === 'done'));
    await sleep(60);
    await H.finish('lead', { name: 'api', action: 'discard' });
    expect(fs.existsSync(a.worktree!.path)).toBe(false);
    expect(g(api, 'branch', '--list', 'pilcrow/api').trim()).toBe('');
    await H.finish('lead', { name: 'web', action: 'keep' });
    expect(fs.existsSync(w.worktree!.path)).toBe(true);
    expect(H.list('lead').helpers.map((h: any) => h.finished)).toEqual(['discard', 'keep']);
  });
});

// ---------------------------------------------------------------- permissions and rules

describe('permissions', () => {
  it('Allow for all helpers answers every helper of the lead asking the same thing, now and later', async () => {
    lead();
    await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    await H.start('lead', { name: 'web', brief: 'x', cwd: web });
    const A = helperSession('api');
    const W = helperSession('web');
    const r1 = A.ask({ toolName: 'Bash', input: { command: 'npm install' } });
    const r2 = W.ask({ toolName: 'Bash', input: { command: 'npm  install' } });
    W.ask({ toolName: 'Bash', input: { command: 'rm -rf x' } });
    const out = H.allow('lead', r1);
    expect(out).toMatchObject({ answered: 2 });
    expect(out.label).toMatch(/npm install/);
    expect(A.decisions.map((d) => d.requestId)).toEqual([r1]);
    expect(W.decisions.map((d) => d.requestId)).toEqual([r2]);
    expect(W.pending.size).toBe(1); // rm -rf is a different request
    expect(store.getMeta('lead').lead.rules).toHaveLength(1);
    expect(H.stateOf({ id: 'lead' }).lead.rules[0]).toMatch(/npm install/);
    // later, a third helper asking the same is answered by the server as it arrives
    await H.finish('lead', { action: 'keep' }).catch(() => {});
    helperSession('api').idle();
    await sleep(10);
    const c = await H.start('lead', { name: 'cli', brief: 'x', cwd: api, worktree: false });
    const C = sessions.all.get(c.id)!;
    C.ask({ toolName: 'Bash', input: { command: 'npm install' } });
    expect(C.decisions).toHaveLength(1);
    expect(() => H.allow('lead', 'gone')).toThrow(/gone/);
  });
  it('Pilcrow decides the helpers tools itself: read-only ones never ask; start asks only outside allowed folders', async () => {
    const L = lead();
    await reg.save({ path: web, name: 'web', policy: 'allowed' });
    await reg.save({ path: api, name: 'api', policy: 'never' });
    const leadDir = path.join(tmp, 'code', 'lead');
    expect(H.permission(L, `${TOOL_PREFIX}wait`, {})).toMatchObject({ behavior: 'allow' });
    expect(H.permission(L, `${TOOL_PREFIX}list`, {})).toMatchObject({ behavior: 'allow' });
    expect(H.permission(L, 'Bash', { command: 'ls' })).toBeNull();
    expect(H.permission(L, `${TOOL_PREFIX}start`, { repo: 'web' })).toMatchObject({ behavior: 'allow' });
    expect(H.permission(L, `${TOOL_PREFIX}start`, { cwd: path.join(leadDir, 'packages', 'x') })).toMatchObject({ behavior: 'allow' });
    expect(H.permission(L, `${TOOL_PREFIX}start`, { repo: 'api' })).toMatchObject({ behavior: 'deny' });
    expect(H.permission(L, `${TOOL_PREFIX}start`, { cwd: path.join(tmp, 'code', 'other') })).toBeNull();
    L.unattended = true;
    expect(H.permission(L, `${TOOL_PREFIX}start`, { repo: 'web' })).toBeNull();
    // Always allow on a start card registers the folder as Allowed instead of allowing every start
    const other = mkRepo(path.join(tmp, 'code', 'other'));
    const d = H.respond(L, { toolName: `${TOOL_PREFIX}start`, input: { cwd: other } }, { behavior: 'allow', updatedPermissions: [{ type: 'addRules' }] });
    expect(d).toEqual({ behavior: 'allow' });
    await sleep(50);
    expect(reg.policyFor(other)).toBe('allowed');
    expect(H.respond(L, { toolName: 'Bash', input: {} }, { behavior: 'allow', updatedPermissions: [1] }).updatedPermissions).toHaveLength(1);
  });
});

// ---------------------------------------------------------------- the person, and a restart

describe('the person and a restart', () => {
  it('a removed helper notes the lead and leaves; a detached one becomes an ordinary session; known lists the folders', async () => {
    const L = lead();
    await reg.save({ path: web, name: 'web', aliases: ['order FE'], about: 'storefront', policy: 'allowed' });
    store.config.recentProjects = [api];
    const a = await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    const w = await H.start('lead', { name: 'web', brief: 'x', repo: 'order FE' });
    const known = H.known('lead');
    expect(known.find((k: any) => k.path === web)).toMatchObject({ name: 'web', registered: true, allowed: true, about: 'storefront' });
    expect(known.find((k: any) => k.path === api)).toMatchObject({ registered: false, allowed: false });
    expect(known.find((k: any) => k.path === path.join(tmp, 'code', 'lead'))).toMatchObject({ allowed: true });
    await sessions.remove(a.id);
    await sleep(60);
    expect(H.list('lead').helpers.map((h: any) => h.name)).toEqual(['web']);
    expect(L.sent[0].text).toMatch(/state="removed">The person removed the helper api/);
    expect(H.detach(w.id)).toBe(true);
    expect(store.getMeta(w.id).helper).toBeUndefined();
    expect(H.list('lead').helpers).toEqual([]);
    expect(H.detach('ghost')).toBe(false);
  });
  it('after a restart, helpers that were running are stopped and the lead is told once', async () => {
    lead();
    const a = await H.start('lead', { name: 'api', brief: 'x', cwd: api });
    expect(store.getMeta(a.id).helper.state).toBe('running');
    H.end();
    const L = sessions.all.get('lead')!;
    H = createHelpers({ sessions, bus, store, git, registry: reg, expandHome: (p: string) => p, homeDir: '/home/nobody', noteMs: 20 });
    H.begin();
    expect(H.list('lead').helpers[0]).toMatchObject({ name: 'api', state: 'stopped', reason: 'server restarted' });
    await sleep(60);
    expect(L.sent).toHaveLength(1);
    expect(L.sent[0].text).toMatch(/state="stopped"[^>]*>\nserver restarted/);
  });
});
