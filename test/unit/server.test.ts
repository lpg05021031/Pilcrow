// The real server (serve.ts): the RPC dispatcher's error codes, the parameter guards, the Host / Origin /
// Sec-Fetch-Site checks on HTTP and WebSocket, and a window that sends a broken frame or goes silent.
import { afterAll, describe, expect, it } from 'vitest';
import http from 'node:http';
import os from 'node:os';
import WebSocket from 'ws';
import { METHODS } from '../../server/main.js';
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
    ];
    for (const [m, p, name] of cases) {
      const res = await rpc(m, p);
      expect(res, m).toMatchObject({ ok: false, e: { code: 'invalid_params' } });
      expect(res.e.message, m).toContain(name);
    }
  });

  it('has no method to run a command or probe the file system that no window uses', async () => {
    for (const m of ['shell.run', 'shell.which', 'fs.exists']) expect((await rpc(m, { cmd: 'true', path: '/' })).e.code).toBe('unknown_method');
  });

  it('keeps the targets of a job saved again without them', async () => {
    const made = await rpc('dispatch.save', { name: 'Bump', brief: 'b', targets: [{ path: '/tmp/a', name: 'a' }] });
    expect(made.ok).toBe(true);
    await rpc('dispatch.save', { id: made.r.id, brief: 'better' });
    const got = (await rpc('dispatch.get', { id: made.r.id })).r.job;
    expect([got.brief, got.targets.map((t: any) => t.name)]).toEqual(['better', ['a']]);
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

  it('accepts a WebSocket only from its own origin', async () => {
    expect(await upgrade({})).toBe(true);
    expect(await upgrade({ origin: `http://127.0.0.1:${app.port}` })).toBe(true);
    expect(await upgrade({ origin: 'http://evil.example' })).toBe(false);
    expect(await upgrade({ 'sec-fetch-site': 'cross-site' })).toBe(false);
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
  it('closing one waits until its process is gone', async () => {
    const t = await rpc('term.open', { cwd: os.tmpdir(), command: "trap '' HUP; sleep 30" });
    expect(t.ok).toBe(true);
    const t0 = Date.now();
    expect((await rpc('term.close', { id: t.r.id })).ok).toBe(true);
    expect(Date.now() - t0).toBeLessThan(2500); // SIGHUP, and SIGKILL after 1.5 s at the latest
    expect((await rpc('term.list')).r).toEqual([]);
  });
});
