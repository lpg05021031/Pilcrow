/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// The repositories registry (server/repos.js): entries, the person's words, policy, the digest, the
// scan and the facts, against a fake config store and real temporary repositories.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as git from '../../server/git.js';
import { createRegistry, normalizeWord, joinNotes, DIGEST_MAX } from '../../server/repos.js';
import { createDispatcher } from '../../server/dispatch_core.js';
import { EventEmitter } from 'node:events';

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

function fakeStore() {
  const config: any = { repos: [] };
  return {
    config,
    getConfig: () => config,
    setConfig: (patch: any) => Object.assign(config, patch),
  };
}

let tmp: string;
let store: ReturnType<typeof fakeStore>;
let reg: ReturnType<typeof createRegistry>;
let web: string;
let api: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-repos-'));
  web = mkRepo(path.join(tmp, 'code', 'web'), { 'package.json': JSON.stringify({ name: '@shop/web', dependencies: { '@shop/api': 'file:../api' } }), 'README.md': '# web\n' });
  api = mkRepo(path.join(tmp, 'code', 'api'), { 'package.json': JSON.stringify({ name: '@shop/api' }), 'CLAUDE.md': 'hi\n' });
  store = fakeStore();
  reg = createRegistry({ store, git, expandHome: (p: string) => p });
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe('words', () => {
  it('normalizes case, spaces, hyphens, underscores and a leading @', () => {
    expect(normalizeWord('Order FE')).toBe('orderfe');
    expect(normalizeWord('@order-fe')).toBe('orderfe');
    expect(normalizeWord('order_FE ')).toBe('orderfe');
    expect(normalizeWord('前台')).toBe('前台');
  });
  it('joins notes with blank lines and skips empties', () => {
    expect(joinNotes('a', '', ' b ')).toBe('a\n\nb');
    expect(joinNotes('', undefined)).toBe('');
  });
});

describe('entries', () => {
  it('saves with a default name, cleans aliases, and lists with the missing flag', async () => {
    const e = await reg.save({ path: web, aliases: ['Order FE', 'storefront', 'order-fe', ''], about: 'Next.js storefront', policy: 'allowed' });
    expect(e.name).toBe('web');
    expect(e.aliases).toEqual(['Order FE', 'storefront']); // order-fe is Order FE again
    expect(e.policy).toBe('allowed');
    expect(e.missing).toBe(false);
    expect(e.facts.packageName).toBe('@shop/web');
    expect(e.facts.branch).toBe('main');
    expect(e.facts.hasClaudeMd).toBe(false);
    expect(reg.list()).toHaveLength(1);
  });
  it('takes the manifest name when the folder name says nothing', async () => {
    const src = mkRepo(path.join(tmp, 'src'), { 'package.json': JSON.stringify({ name: '@acme/billing' }) });
    const e = await reg.save({ path: src });
    expect(e.name).toBe('billing');
  });
  it('refuses a missing folder, a second entry for one folder, and words another entry owns', async () => {
    await expect(reg.save({ path: path.join(tmp, 'nope') })).rejects.toThrow(/does not exist/);
    await reg.save({ path: web, aliases: ['order FE'] });
    await expect(reg.save({ path: web })).rejects.toThrow(/already registered as web/);
    await expect(reg.save({ path: api, name: 'Order-FE' })).rejects.toThrow(/already a name of web/);
    await expect(reg.save({ path: api, aliases: ['WEB'] })).rejects.toThrow(/already a name of web/);
  });
  it('edits in place keeping the id, and a vanished folder can still be edited', async () => {
    const e = await reg.save({ path: web, aliases: ['a'] });
    const e2 = await reg.save({ id: e.id, name: 'shop-web', about: 'the shop', policy: 'never', defaults: { mode: 'acceptEdits', worktree: false, verify: 'npm test', prepare: '' }, notes: 'pnpm, not npm' });
    expect(e2.id).toBe(e.id);
    expect(e2.name).toBe('shop-web');
    expect(e2.defaults).toEqual({ mode: 'acceptEdits', worktree: false, verify: 'npm test' });
    expect(e2.notes).toBe('pnpm, not npm');
    fs.rmSync(web, { recursive: true, force: true });
    expect(reg.list()[0].missing).toBe(true);
    const e3 = await reg.save({ id: e.id, about: 'moved' });
    expect(e3.about).toBe('moved');
    expect(e3.missing).toBe(true);
  });
  it('removes an entry and drops it from others’ dependencies', async () => {
    const a = await reg.save({ path: api });
    const w = await reg.save({ path: web, deps: [a.id, 'ghost'] });
    expect(reg.get(w.id)!.deps).toEqual([a.id]);
    expect(reg.remove(a.id)).toBe(true);
    expect(reg.get(w.id)!.deps).toEqual([]);
    expect(reg.remove('ghost')).toBe(false);
  });
  it('register adds a folder or patches the entry that holds it', async () => {
    const e = await reg.register(web, { policy: 'allowed' });
    expect(e.path).toBe(web);
    expect(e.policy).toBe('allowed');
    const e2 = await reg.register(path.join(web, 'src', 'deep'), { policy: 'ask' }); // a folder inside: the same entry
    expect(e2.id).toBe(e.id);
    expect(e2.policy).toBe('ask');
    expect(reg.list()).toHaveLength(1);
    await expect(reg.register(path.join(tmp, 'nope'))).rejects.toThrow(/does not exist/);
  });
});

describe('the person’s words', () => {
  beforeEach(async () => {
    await reg.save({ path: web, aliases: ['order FE', '前台'], about: 'storefront', policy: 'allowed' });
    await reg.save({ path: api, aliases: ['orders service'], policy: 'ask' });
  });
  it('resolves by path, name, alias and the folder name, loosely', () => {
    expect(reg.resolve(web)!.name).toBe('web');
    expect(reg.resolve('Web')!.name).toBe('web');
    expect(reg.resolve('@web')!.name).toBe('web');
    expect(reg.resolve('ORDER-FE')!.name).toBe('web');
    expect(reg.resolve('前台')!.name).toBe('web');
    expect(reg.resolve('orders_service')!.name).toBe('api');
    expect(reg.resolve('mobile')).toBeNull();
    expect(reg.resolve('')).toBeNull();
  });
  it('names the nearest entries for a miss', () => {
    expect(reg.nearest('order-fe2')[0]).toBe('web');
    expect(reg.nearest('apu')[0]).toBe('api');
    expect(reg.nearest('zzz', 1)).toHaveLength(1);
  });
  it('knows which entry holds a folder, and its policy, defaults and notes', async () => {
    expect(reg.containing(path.join(web, 'src', 'x.ts'))!.name).toBe('web');
    expect(reg.containing(tmp)).toBeNull();
    expect(reg.policyFor(web)).toBe('allowed');
    expect(reg.policyFor(path.join(api, 'deep'))).toBe('ask');
    expect(reg.policyFor(path.join(tmp, 'elsewhere'))).toBeNull();
    const a = reg.resolve('api')!;
    await reg.save({ id: a.id, notes: 'go mod tidy first', defaults: { verify: 'go test ./...' } });
    expect(reg.notesFor(path.join(api, 'cmd'))).toBe('go mod tidy first');
    expect(reg.defaultsFor(a.id).verify).toBe('go test ./...');
    expect(reg.notesFor('nope')).toBe('');
  });
  it('writes the digest with names, aliases, about, path and policy, bounded', async () => {
    const d = reg.promptFor();
    expect(d).toMatch(/# Repositories/);
    expect(d).toMatch(/- web \(also: order FE, 前台\): storefront · .*web · helpers allowed/);
    expect(d).toMatch(/- api \(also: orders service\): no description · .*api · helpers ask/);
    for (let i = 0; i < 60; i++) {
      const d2 = mkRepo(path.join(tmp, 'many', `r${i}`));
      await reg.save({ path: d2, about: 'x'.repeat(100) });
    }
    const big = reg.promptFor();
    expect(big.length).toBeLessThanOrEqual(DIGEST_MAX + 80);
    expect(big).toMatch(/and \d+ more/);
    expect(reg.promptFor().startsWith('\n\n')).toBe(true);
    store.config.repos = [];
    expect(reg.promptFor()).toBe('');
  });
});

describe('scan and facts', () => {
  it('finds repositories under a folder, marks the registered ones, and refreshes facts', async () => {
    await reg.save({ path: api });
    const r = await reg.scan(path.join(tmp, 'code'));
    expect(r.found.map((f: any) => [f.name, f.registered, f.packageName])).toEqual([
      ['api', true, '@shop/api'],
      ['web', false, '@shop/web'],
    ]);
    fs.writeFileSync(path.join(api, 'dirty.txt'), 'x');
    const before = reg.get(reg.resolve('api')!.id)!.facts;
    expect(before.dirty).toBe(false);
    await reg.refresh(null); // throttled: nothing changes within a minute
    expect(reg.list()[0].facts.dirty).toBe(false);
    await reg.refresh(null, { force: true });
    expect(reg.list()[0].facts.dirty).toBe(true);
    expect(reg.list()[0].facts.hasClaudeMd).toBe(true);
    await expect(reg.scan(path.join(tmp, 'nope'))).rejects.toThrow(/exists/);
  });
  it('emits changed on every write', async () => {
    const seen: any[] = [];
    reg.events.on('changed', (d: any) => seen.push(d.repos.length));
    const e = await reg.save({ path: web });
    reg.remove(e.id);
    expect(seen[0]).toBe(1);
    expect(seen[seen.length - 1]).toBe(0);
  });
});

describe('with Dispatch', () => {
  it('annotates the scan, blocks a never repository, and renders registry notes into the brief', async () => {
    const a = await reg.save({ path: api, name: 'api', about: 'the orders API', policy: 'never', notes: 'Run go generate first.' });
    await reg.save({ path: web, aliases: ['order FE'], deps: [a.id], notes: 'pnpm, not npm.', defaults: { verify: 'pnpm test' } });
    const bus = new EventEmitter();
    const sessions = { find: () => null, get: async () => null, create: async () => ({ id: 'x', cwd: web, worktree: null }) };
    const d = createDispatcher({ sessions, bus, store: { meta: {}, setMeta() {}, getConfig: () => ({}) } as any, git, shell: async () => ({ code: 0, output: '' }), run: async () => ({ code: 0, stdout: '', stderr: '' }), registry: reg });
    const scan = await d.scan({ root: path.join(tmp, 'code') });
    const w = scan.repos.find((r: any) => r.name === 'web');
    const ap = scan.repos.find((r: any) => r.name === 'api');
    expect(w.registered).toBe(true);
    expect(w.registryName).toBe('web');
    expect(w.deps).toEqual([api]); // from the manifest and the registry alike
    expect(w.notes).toBe('pnpm, not npm.');
    expect(ap.policy).toBe('never');
    const job = await d.save({ name: 'Bump', brief: 'Do it in {{repo.name}}. {{notes}}', root: path.join(tmp, 'code'), targets: [{ path: web, name: 'web', notes: 'Also the docs.' }, { path: api, name: 'api' }] });
    const plan = await d.plan(job.id);
    const pw = plan.targets.find((t: any) => t.name === 'web');
    const pa = plan.targets.find((t: any) => t.name === 'api');
    expect(pw.brief).toBe('Do it in web. pnpm, not npm.\n\nAlso the docs.');
    expect(pa.verdict).toBe('blocked');
    expect(pa.notes.some((n: any) => /policy Never/.test(n.text))).toBe(true);
  });
});
