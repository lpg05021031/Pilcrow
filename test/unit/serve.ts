/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// The real server for a test file: started on a free port in the file's temporary HOME (setup.ts),
// windows that speak its JSON-RPC, a WebSocket handshake made by hand, and its log.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import WebSocket from 'ws';
import { start } from '../../server/main.js';

export type Rpc = (m: string, p?: unknown) => Promise<any>;

export async function serve(opts: Parameters<typeof start>[0] = {}) {
  const app = await start({ port: 0, quiet: true, ...opts });

  /** A window: each request resolves with its whole response ({ ok, r } or { ok: false, e }). */
  async function connect() {
    const ws = new WebSocket(`ws://127.0.0.1:${app.port}/ws`);
    const waiting = new Map<number, (res: any) => void>();
    let nextId = 1;
    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.t === 'res') waiting.get(msg.id)?.(msg);
    });
    await new Promise((r) => ws.once('open', r));
    const rpc: Rpc = (m, p) =>
      new Promise((resolve) => {
        const id = nextId++;
        waiting.set(id, resolve);
        ws.send(JSON.stringify({ t: 'req', id, m, p }));
      });
    return { ws, rpc };
  }

  /** A WebSocket handshake by hand, and then nothing: no pongs, no reading unless asked. */
  const rawClient = (): Promise<net.Socket> =>
    new Promise((resolve, reject) => {
      const sock = net.connect(app.port, '127.0.0.1', () => sock.write(`GET /ws HTTP/1.1\r\nHost: 127.0.0.1:${app.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n`));
      sock.once('data', (d) => (d.toString().startsWith('HTTP/1.1 101') ? resolve(sock) : reject(new Error(d.toString()))));
      sock.on('error', reject);
    });

  return { app, connect, rawClient };
}

/** The server's log once it says what `re` looks for (it is written through a stream, a moment later). */
export async function logSays(re: RegExp, ms = 5000) {
  const file = path.join(process.env.XDG_STATE_HOME!, 'pilcrow', 'pilcrow.log');
  for (const end = Date.now() + ms; Date.now() < end; await new Promise((r) => setTimeout(r, 20))) {
    const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
    if (re.test(text)) return text;
  }
  throw new Error(`the log never said ${re}`);
}
