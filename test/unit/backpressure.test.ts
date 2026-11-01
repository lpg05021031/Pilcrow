// A window that stops reading while the server has a lot to tell it (a terminal flooding the broadcast)
// is dropped once it is too far behind, instead of being queued for without end. Its own server
// (serve.ts), with the default heartbeat, so only the backlog can be what drops it.
import { afterAll, expect, it } from 'vitest';
import os from 'node:os';
import { logSays, serve } from './serve.js';

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
});
