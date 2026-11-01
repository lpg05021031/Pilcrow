// A window that stops reading while the server has a lot to tell it (a terminal flooding the broadcast)
// is dropped once it is too far behind, instead of being queued for without end; one that is behind only
// on a large answer it asked for (a long conversation opened) is not. Its own server (serve.ts), with the
// default heartbeat, so only the backlog can be what drops it.
import { afterAll, expect, it } from 'vitest';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Socket } from 'node:net';
import type WebSocket from 'ws';
import { logSays, serve } from './serve.js';

/** The TCP socket under a window's WebSocket, to stop reading from it. */
const socketOf = (ws: WebSocket) => (ws as unknown as { _socket: Socket })._socket;

const { app, connect, rawClient } = await serve({ maxBuffered: 256 * 1024 });
afterAll(() => app.stop());

it('drops a window that cannot keep up', { timeout: 15_000 }, async () => {
  const raw = await rawClient();
  raw.pause();
  let got = 0;
  raw.on('data', (d) => (got += d.length));
  const ended = new Promise((r) => raw.on('close', r));
  const total = 30_000_000;
  const { ws, rpc } = await connect();
  const t = await rpc('term.open', { cwd: os.tmpdir(), command: `head -c ${total} /dev/zero | tr '\\0' x` });
  expect(t.ok).toBe(true);
  ws.close(); // the silent window is the one left to hear the flood
  await logSays(/dropping a window that is \d+ bytes behind/, 10_000);
  raw.resume();
  await ended;
  expect(got).toBeLessThan(total);
  await (await connect()).rpc('term.close', { id: t.r.id }); // the flood ends here, not in the next test
});

it('keeps a window behind only on an answer it asked for, larger than the limit', { timeout: 15_000 }, async () => {
  const image = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-big-')), 'big.png');
  fs.writeFileSync(image, crypto.randomBytes(10 * 1024 * 1024)); // 13 MB of base64 in one answer
  const slow = await connect();
  const { rpc } = await connect();
  socketOf(slow.ws).pause(); // reads nothing for now
  const answer = slow.rpc('fs.read', { path: image });
  await new Promise((r) => setTimeout(r, 300)); // the answer is queued, far past 256 KB
  const t = await rpc('term.open', { cwd: os.tmpdir(), command: 'echo an event for every window' });
  expect(t.ok).toBe(true);
  await new Promise((r) => setTimeout(r, 300));
  expect(slow.ws.readyState).toBe(slow.ws.OPEN);
  socketOf(slow.ws).resume();
  expect((await answer).r.dataUrl.length).toBeGreaterThan(13_000_000);
  slow.ws.close();
});
