// WebSocket JSON-RPC client with automatic reconnect.

// Handlers are registered per event with the payload type they expect; the wire carries unknown.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Handler = (data: any) => void;

class Rpc {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void; method: string }>();
  private handlers = new Map<string, Set<Handler>>();
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
      let msg: { t?: string; id?: number; ok?: boolean; r?: unknown; e?: unknown; d?: unknown };
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
        else p.reject(new Error((msg.e as { message?: string } | undefined)?.message || `${p.method} failed`));
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

  on(event: string, fn: Handler): () => void {
    let set = this.handlers.get(event);
    if (!set) this.handlers.set(event, (set = new Set()));
    set.add(fn);
    return () => set!.delete(fn);
  }

  private emit(event: string, data: unknown) {
    for (const fn of this.handlers.get(event) || []) {
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
