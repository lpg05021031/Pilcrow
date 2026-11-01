/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// The real server, started on a free port in this file's temporary HOME (setup.ts): the RPC dispatcher's
// error codes, the parameter guards, and the Host / Origin / Sec-Fetch-Site checks on HTTP and WebSocket.
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import http from 'node:http';
import WebSocket from 'ws';
import { start, METHODS } from '../../server/main.js';

let app: Awaited<ReturnType<typeof start>>;
let ws: WebSocket;
let nextId = 1;
const waiting = new Map<number, (res: any) => void>();

/** One request; resolves with the whole response ({ ok, r } or { ok: false, e }). */
const rpc = (m: string, p?: unknown): Promise<any> =>
  new Promise((resolve) => {
    const id = nextId++;
    waiting.set(id, resolve);
    ws.send(JSON.stringify({ t: 'req', id, m, p }));
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

beforeAll(async () => {
  app = await start({ port: 0, quiet: true });
  ws = new WebSocket(`ws://127.0.0.1:${app.port}/ws`);
  ws.on('message', (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.t === 'res') waiting.get(msg.id)?.(msg);
  });
  await new Promise((r) => ws.once('open', r));
});

afterAll(async () => {
  ws.close();
  await app.stop();
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
      ['shell.run', { cmd: 'true', args: 'not-a-list' }, 'args'],
    ];
    for (const [m, p, name] of cases) {
      const res = await rpc(m, p);
      expect(res, m).toMatchObject({ ok: false, e: { code: 'invalid_params' } });
      expect(res.e.message, m).toContain(name);
    }
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
