// The real server (serve.ts): the RPC dispatcher's error codes, the parameter guards, the Host / Origin /
// Sec-Fetch-Site checks on HTTP and WebSocket, a file that fails to read, a window that sends a broken
// frame or goes silent, settings keys and session ids that name nothing, and a terminal's scrollback.
import { afterAll, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import WebSocket from 'ws';
import { METHODS } from '../../server/main.js';
import { store } from '../../server/config.js';
import { flushSaves } from '../../server/util.js';
import * as term from '../../server/terminal.js';
import { logSays, serve } from './serve.js';

const HEARTBEAT_MS = 1000;
const { app, connect, rawClient } = await serve({ heartbeatMs: HEARTBEAT_MS });
const { ws, rpc } = await connect();
afterAll(async () => {
  ws.close();
  await app.stop();
});

/** A raw HTTP request with the headers a browser (or an attacker) would send. */
const get = (path: string, headers: Record<string, string> = {}): Promise<number> =>
  new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: app.port, path, headers: { host: `127.0.0.1:${app.port}`, ...headers } }, (res) => {
      res.resume();
      resolve(res.statusCode!);
    });
    req.on('error', reject);
    req.end();
  });

const upgrade = (headers: Record<string, string>): Promise<boolean> =>
  new Promise((resolve) => {
    const sock = new WebSocket(`ws://127.0.0.1:${app.port}/ws`, { headers });
    sock.on('open', () => {
      sock.close();
      resolve(true);
    });
    sock.on('error', () => resolve(false));
  });

describe('RPC errors', () => {
  it('answers a method it has', async () => {
    const res = await rpc('config.get');
    expect(res.ok).toBe(true);
    expect(res.r.theme).toBe('system');
  });

  it('says unknown_method, also for names an object has by inheritance', async () => {
    for (const m of ['nope.nothing', 'constructor', '__proto__', 'toString']) {
      const res = await rpc(m);
      expect(res).toMatchObject({ ok: false, e: { code: 'unknown_method' } });
      expect(res.e.message).toContain(m);
    }
  });

  it('says invalid_params, naming the param, instead of throwing a TypeError deep down', async () => {
    const cases: [string, unknown, string][] = [
      ['term.input', { id: 'x' }, 'data'],
      ['term.resize', { id: 'x', cols: '80' }, 'cols'],
      ['fs.write', { path: '/tmp/pilcrow-never-written' }, 'text'],
      ['fs.read', {}, 'path'],
      ['fs.mkdir', { path: '' }, 'path'],
      ['session.open', {}, 'id'],
      ['session.permission', { id: 'x', requestId: 'r' }, 'decision'],
      ['memory.list', { cwd: 42 }, 'cwd'],
      ['git.branches', {}, 'cwd'],
      ['git.info', { cwd: 42 }, 'cwd'],
      ['git.diff', { cwd: 42 }, 'cwd'],
      ['git.diff', { cwd: '/tmp', base: 7 }, 'base'],
      ['git.commit', { cwd: '/tmp', message: 'm', paths: 'a.txt' }, 'paths'],
      ['git.log', { cwd: '/tmp', n: '5' }, 'n'],
      ['git.removeWorktree', { dir: '/tmp/wt' }, 'repoRoot'],
      ['git.removeWorktree', { repoRoot: '/tmp/repo' }, 'dir'],
      ['git.removeWorktree', { repoRoot: '/tmp/repo', dir: '/tmp/wt', branch: ['b'] }, 'branch'],
      ['dispatch.save', { name: 'nowhere' }, 'targets'],
      ['dispatch.save', { targets: [] }, 'targets'],
      ['dispatch.save', { targets: [{ name: 'no path' }] }, 'targets'],
      ['dispatch.plan', { targets: 'a' }, 'targets'],
      ['term.open', { cwd: 42 }, 'cwd'],
      ['term.open', { cols: 0 }, 'cols'],
      ['term.close', {}, 'id'],
      ['schedules.remove', {}, 'id'],
      ['repos.remove', { id: 3 }, 'id'],
      ['inbox.remove', {}, 'id'],
      ['dispatch.remove', {}, 'id'],
      ['memory.remove', { cwd: '/tmp' }, 'id'],
      ['helpers.list', {}, 'leadId'],
      ['helpers.allow', { leadId: 'x' }, 'requestId'],
      ['helpers.stop', { leadId: 'x' }, 'name'],
      ['helpers.finish', { leadId: 'x' }, 'action'],
      ['helpers.detach', {}, 'id'],
      ['session.queue.remove', { id: 'x' }, 'uuid'],
      ['session.queue.update', { id: 'x', uuid: 'u' }, 'text'],
      ['session.close', {}, 'id'],
      ['session.rename', { id: 'x' }, 'title'],
      ['session.fork', { id: 'x', title: 7 }, 'title'],
      ...['get', 'start', 'continue', 'pause', 'resume', 'stop', 'review', 'export'].map((m): [string, unknown, string] => [`dispatch.${m}`, { id: 7 }, 'id']),
      ['dispatch.retry', { id: 'j' }, 'runId'],
      ['dispatch.skip', { id: 'j' }, 'runId'],
      ['dispatch.allow', { id: 'j' }, 'key/requestId'],
      ['dispatch.finish', { id: 'j' }, 'action'],
      ['dispatch.startFromPlaybook', { id: 'p', targets: 'a' }, 'targets'],
      ['schedules.toggle', {}, 'id'],
      ['schedules.runNow', { id: ['s'] }, 'id'],
      ['schedules.save', { name: 'n', cwd: '/tmp', prompt: 'p', cadence: 'daily' }, 'cadence'],
      ['inbox.markRead', { ids: 'all' }, 'ids'],
      ['inbox.markRead', { ids: [1] }, 'ids'],
      ['skills.create', { name: { a: 1 } }, 'name'],
    ];
    // methods of a session check its params once it is found
    const { r: made } = await rpc('session.create', { cwd: os.tmpdir() });
    cases.push(
      ['session.setMode', { id: made.id }, 'mode'],
      ['session.setModel', { id: made.id, model: 4 }, 'model'],
      ['session.send', { id: made.id, images: 'a.png' }, 'images'],
      ['session.rewind', { id: made.id }, 'messageId'],
      ['session.stopTask', { id: made.id }, 'taskId'],
      ['session.mcp.reconnect', { id: made.id }, 'name'],
    );
    for (const [m, p, name] of cases) {
      const res = await rpc(m, p);
      expect(res, m).toMatchObject({ ok: false, e: { code: 'invalid_params' } });
      expect(res.e.message, m).toContain(name);
    }
    expect(fs.existsSync(path.join(process.env.CLAUDE_CONFIG_DIR!, 'skills', 'object-object'))).toBe(false);
    await rpc('session.delete', { id: made.id });
  });

  it('has no method to run a command or probe the file system that no window uses', async () => {
    for (const m of ['shell.run', 'shell.which', 'fs.exists']) expect((await rpc(m, { cmd: 'true', path: '/' })).e.code).toBe('unknown_method');
  });

  it('keeps the targets of a job saved again without them', async () => {
    const made = await rpc('dispatch.save', { name: 'Bump', brief: 'b', targets: [{ path: '/tmp/a', name: 'a' }] });
    expect(made.ok).toBe(true);
    await rpc('dispatch.save', { id: made.r.id, brief: 'better' });
    const got = (await rpc('dispatch.get', { id: made.r.id })).r.job;
    expect([got.brief, got.targets.map((t: { name: string }) => t.name)]).toEqual(['better', ['a']]);
    await rpc('dispatch.remove', { id: made.r.id });
  });

  it('treats params that are not an object as none', async () => {
    const res = await rpc('fs.read', 'oops');
    expect(res.e.code).toBe('invalid_params');
  });

  it('says not_found for a session or a file that does not exist', async () => {
    expect((await rpc('session.open', { id: 'no-such-session' })).e).toMatchObject({ code: 'not_found', message: 'Unknown session: no-such-session' });
    expect((await rpc('fs.read', { path: '/no/such/file.txt' })).e.code).toBe('not_found');
  });

  it("says failed, with the module's own words, when a method refuses", async () => {
    const res = await rpc('memory.add', { cwd: '/tmp/some-project', text: '   ' });
    expect(res.e).toEqual({ code: 'failed', message: 'The note is empty.' });
  });

  it('lists every method by name without starting a server', () => {
    expect(METHODS).toContain('session.open');
    expect(METHODS).toContain('dispatch.startFromPlaybook');
    expect(METHODS.length).toBeGreaterThan(100);
  });
});

describe('who may talk to the server', () => {
  it('serves its own pages and refuses other hosts, origins and sites', async () => {
    expect(await get('/api/hello')).toBe(200);
    expect(await get('/api/hello', { host: `evil.example:${app.port}` })).toBe(403);
    expect(await get('/api/hello', { origin: 'http://evil.example' })).toBe(403);
    expect(await get('/api/hello', { origin: `http://localhost:${app.port}` })).toBe(200);
    expect(await get('/api/hello', { 'sec-fetch-site': 'cross-site' })).toBe(403);
  });

  it('accepts a WebSocket only from its own origin, and refuses the rest with 403 as HTTP does', async () => {
    expect(await upgrade({})).toBe(true);
    expect(await upgrade({ origin: `http://127.0.0.1:${app.port}` })).toBe(true);
    expect(await upgrade({ origin: 'http://evil.example' })).toBe(false);
    expect(await upgrade({ 'sec-fetch-site': 'cross-site' })).toBe(false);
    const status = await new Promise((resolve) => {
      const req = http.request({ host: '127.0.0.1', port: app.port, path: '/ws', headers: { host: `127.0.0.1:${app.port}`, origin: 'http://evil.example', connection: 'Upgrade', upgrade: 'websocket', 'sec-websocket-version': '13', 'sec-websocket-key': 'dGhlIHNhbXBsZSBub25jZQ==' } });
      req.on('response', (res) => resolve(res.statusCode));
      req.on('error', () => resolve('error'));
      req.end();
    });
    expect(status).toBe(403);
  });

  it('answers a file that fails to read with 500 and keeps serving', async () => {
    const file = path.join(os.tmpdir(), `pilcrow-unreadable-${process.pid}.txt`);
    fs.writeFileSync(file, 'x');
    const read = vi.spyOn(fs, 'createReadStream').mockImplementationOnce(() => {
      const r = new Readable({ read() {} });
      process.nextTick(() => r.destroy(Object.assign(new Error('EIO: i/o error, read'), { code: 'EIO' })));
      return r as unknown as fs.ReadStream;
    });
    try {
      expect(await get(`/api/fs${file}`)).toBe(500);
      expect(await logSays(/file read failed .*EIO/)).not.toMatch(/uncaughtException/);
      expect(await get(`/api/fs${file}`)).toBe(200);
    } finally {
      read.mockRestore();
    }
  });

  it('serves a file as a sandboxed page that cannot act as the app, and nothing it may be sniffed into', async () => {
    const file = path.join(os.tmpdir(), `pilcrow-page-${process.pid}.html`);
    fs.writeFileSync(file, '<script>fetch("/api/hello")</script>');
    const headersOf = (p: string): Promise<http.IncomingHttpHeaders> =>
      new Promise((resolve, reject) => {
        http.get({ host: '127.0.0.1', port: app.port, path: p, headers: { host: `127.0.0.1:${app.port}` } }, (res) => (res.resume(), resolve(res.headers))).on('error', reject);
      });
    const page = await headersOf(`/api/fs${file}`);
    expect(page['content-type']).toMatch(/text\/html/);
    expect(page['content-security-policy']).toMatch(/^sandbox /);
    expect(page['content-security-policy']).not.toMatch(/allow-same-origin/);
    expect((await headersOf(`/api/raw?path=${encodeURIComponent(file)}`))['x-content-type-options']).toBe('nosniff');
  });

  it('answers a malformed path with 400 instead of hanging', async () => {
    expect(await get('/api/fs/%zz')).toBe(400);
    expect(await get('/%zz')).toBe(400);
  });

  it('keeps static paths inside the interface folder', async () => {
    // A sibling folder whose name starts like web/dist must not count as inside it.
    expect(await get('/..%2fdist-other/x')).toBe(403);
  });
});

describe('windows that break or go silent', () => {
  it('survives a broken frame from a window', async () => {
    const raw = await rawClient();
    const closed = new Promise((r) => raw.on('close', r));
    raw.write(Buffer.from([0x81, 0x02, 0x68, 0x69])); // a text frame without the mask a client must set
    await closed;
    expect((await rpc('config.get')).ok).toBe(true);
    // Handled on the socket, not left to the process-wide last resort.
    expect(await logSays(/websocket error .*MASK/)).not.toMatch(/uncaughtException/);
  });

  it('drops a window that stops answering pings, and keeps the one that answers', async () => {
    const raw = await rawClient();
    raw.resume();
    const t0 = Date.now();
    await new Promise((r) => raw.on('close', r));
    expect(Date.now() - t0).toBeLessThan(3 * HEARTBEAT_MS);
    expect((await rpc('config.get')).ok).toBe(true);
  });
});

describe('terminals', () => {
  it('keeps the last 400 KB of output for a window that opens one later', async () => {
    const t = term.open({ cwd: os.tmpdir(), command: "head -c 600000 /dev/zero | tr '\\0' a; echo; echo END" });
    for (const end = Date.now() + 10_000; !term.list().find((x) => x.id === t.id)?.exited && Date.now() < end; ) await new Promise((r) => setTimeout(r, 20));
    const snap = term.snapshot(t.id)!;
    expect(snap.buffer.length).toBe(400 * 1024);
    expect(snap.buffer).toMatch(/a+\r?\nEND/);
    await term.close(t.id);
  });

  it('closing one waits until its process is gone', async () => {
    const t = await rpc('term.open', { cwd: os.tmpdir(), command: "trap '' HUP; sleep 30" });
    expect(t.ok).toBe(true);
    const t0 = Date.now();
    expect((await rpc('term.close', { id: t.r.id })).ok).toBe(true);
    expect(Date.now() - t0).toBeLessThan(2500); // SIGHUP, and SIGKILL after 1.5 s at the latest
    expect((await rpc('term.list')).r).toEqual([]);
  });
});

describe('names that are not settings or sessions', () => {
  it('ignores a settings key the defaults do not have, inherited ones included', async () => {
    const r = (await rpc('config.set', { toString: 'x', constructor: 'y', theme: 'dark' })).r;
    expect(r.theme).toBe('dark');
    expect(Object.hasOwn(store.getConfig(), 'toString') || Object.hasOwn(store.getConfig(), 'constructor')).toBe(false);
  });

  it('refuses a setting of the wrong kind, changes nothing, and a session can still be made', async () => {
    const before = JSON.stringify(store.getConfig());
    for (const p of [{ fontSize: 'huge' }, { recentProjects: 'oops' }, { helpers: null }, { theme: { x: 1 } }, { ui: [] }, { theme: 'dark', fontSize: 'huge' }]) {
      const res = await rpc('config.set', p);
      expect(res, JSON.stringify(p)).toMatchObject({ ok: false, e: { code: 'invalid_params' } });
    }
    expect((await rpc('config.set', { fontSize: 'huge' })).e.message).toBe('fontSize must be a number');
    expect(JSON.stringify(store.getConfig())).toBe(before);
    expect((await rpc('config.set', { window: { width: 900, height: 700 } })).ok).toBe(true);
    expect((await rpc('config.set', { window: null })).ok).toBe(true);
    const made = await rpc('session.create', { cwd: os.tmpdir() });
    expect(made.ok).toBe(true);
    expect(store.getConfig().recentProjects[0]).toBe(os.tmpdir());
    await rpc('session.delete', { id: made.r.id });
  });

  it('keeps session ids such as __proto__ as plain entries', async () => {
    expect((await rpc('session.archive', { id: '__proto__', archived: true })).ok).toBe(true);
    expect(Object.getPrototypeOf(store.meta)).toBe(null);
    expect(store.getMeta('__proto__')).toEqual({ archived: true });
    expect(store.getMeta('toString')).toBe(null);
    store.deleteMeta('__proto__');
  });

  it('writes the metadata on quit even with a save still waiting', () => {
    store.setMeta('sess-flush', { title: 'kept' });
    flushSaves();
    const file = path.join(process.env.XDG_CONFIG_HOME!, 'pilcrow', 'sessions.json');
    expect(JSON.parse(fs.readFileSync(file, 'utf8'))['sess-flush']).toEqual({ title: 'kept' });
    store.deleteMeta('sess-flush');
  });
});
