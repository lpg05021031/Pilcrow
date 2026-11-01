// WebSocket JSON-RPC client with automatic reconnect.
import type { DispatchDelta, PermissionRequest, RawMsg, RepoEntry, SessionState, TimelineStep } from './types';

/** The events the client subscribes to and their payloads, as server/main.js broadcasts them; `connection` is the client's own. */
interface Events {
  connection: { connected: boolean };
  'session.msgs': { id: string; msgs: RawMsg[] };
  'session.state': SessionState;
  'session.permission': { id: string; request: PermissionRequest };
  'sessions.changed': Record<string, never>;
  'schedules.changed': Record<string, never>;
  'memory.changed': { cwd: string };
  'repos.changed': { repos: RepoEntry[] };
  'inbox.changed': number;
  'dispatch.changed': DispatchDelta;
  'dispatch.playbooks': Record<string, never>;
  'timeline.step': { id: string; step: TimelineStep };
  'term.data': { id: string; data: string };
  'term.exit': { id: string; code: number | null; signal: number | string | null };
}
/** A message from the server: a response, or an event with the payload its name carries. */
type Wire = { t: 'res'; id: number; ok: boolean; r?: unknown; e?: { message?: string } } | { [K in keyof Events]: { t: 'ev'; e: K; d: Events[K] } }[keyof Events];

class Rpc {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void; method: string }>();
  // no prototype: an event name from the wire finds only the handlers registered for it
  private handlers: { [K in keyof Events]?: Set<(data: Events[K]) => void> } = Object.create(null);
  private queue: string[] = [];
  private retry = 0;
  connected = false;

  connect() {
    if (this.ws) return;
    const ws = new WebSocket(`ws://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.retry = 0;
      for (const raw of this.queue.splice(0)) ws.send(raw);
      this.emit('connection', { connected: true });
    };
    ws.onclose = () => {
      this.ws = null;
      const was = this.connected;
      this.connected = false;
      for (const [, p] of this.pending) p.reject(new Error('Connection to Pilcrow was lost'));
      this.pending.clear();
      if (was) this.emit('connection', { connected: false });
      const delay = Math.min(5000, 300 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    };
    ws.onmessage = (ev) => {
      // A response: { t: 'res', id, ok, r | e }. An event: { t: 'ev', e: name, d: payload }.
      let msg: Wire;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.t === 'res' && typeof msg.id === 'number') {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.ok) p.resolve(msg.r);
        else p.reject(new Error(msg.e?.message || `${p.method} failed`));
      } else if (msg.t === 'ev' && typeof msg.e === 'string') {
        this.emit(msg.e, msg.d);
      }
    };
  }

  call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = this.nextId++;
    const raw = JSON.stringify({ t: 'req', id, m: method, p: params });
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, method });
      if (this.ws && this.connected) this.ws.send(raw);
      else this.queue.push(raw);
    });
  }

  on<K extends keyof Events>(event: K, fn: (data: Events[K]) => void): () => void {
    // the map seen as holding this one event's handlers, which TypeScript lets a generic key write to
    const handlers: { [P in K]?: Set<(data: Events[P]) => void> } = this.handlers;
    const set = (handlers[event] ||= new Set());
    set.add(fn);
    return () => set.delete(fn);
  }

  private emit<K extends keyof Events>(event: K, data: Events[K]) {
    for (const fn of this.handlers[event] || []) {
      try {
        fn(data);
      } catch (e) {
        console.error('handler failed', event, e);
      }
    }
  }

  rawUrl(path: string) {
    return `/api/raw?path=${encodeURIComponent(path)}`;
  }
}

export const rpc = new Rpc();
