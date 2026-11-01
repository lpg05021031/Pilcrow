/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// sessions.js against a fake Agent SDK: the queue of messages typed while Claude works, the permission
// flow, how a process ends (on purpose, by failing, past a bad message), the idle reaper letting go of
// transcripts, the cap on finished tasks, and one timeline step per settled turn.
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const sdk = vi.hoisted(() => {
  /** A Claude Code process as the SDK's query() returns it, driven by the test. */
  class FakeQuery {
    sent: any[] = [];
    interrupts = 0;
    private buf: any[] = [];
    private wake: (() => void) | null = null;
    private done = false;
    private error: Error | null = null;
    constructor(
      public prompt: AsyncIterable<any>,
      public options: any,
    ) {
      void (async () => {
        for await (const m of prompt) this.sent.push(m);
      })();
    }
    emit(...msgs: any[]) {
      this.buf.push(...msgs);
      this.wake?.();
    }
    end(error?: Error) {
      this.done = true;
      this.error = error ?? null;
      this.wake?.();
    }
    async *[Symbol.asyncIterator]() {
      for (;;) {
        if (this.buf.length) yield this.buf.shift();
        else if (this.done) {
          if (this.error) throw this.error;
          return;
        } else await new Promise<void>((r) => (this.wake = r));
      }
    }
    async interrupt() {
      this.interrupts++;
    }
    close() {
      this.end();
    }
    async supportedCommands() {
      return [];
    }
    async supportedModels() {
      return [];
    }
    async mcpServerStatus() {
      return [];
    }
  }
  return { FakeQuery, queries: [] as FakeQuery[], transcript: [] as any[] };
});

vi.mock('@anthropic-ai/claude-agent-sdk', () => ({
  query: ({ prompt, options }: any) => {
    const q = new sdk.FakeQuery(prompt, options);
    sdk.queries.push(q);
    return q;
  },
  getSessionMessages: async () => sdk.transcript.map((m) => ({ ...m })),
  listSessions: async () => [],
  getSubagentMessages: async () => [],
  listSubagents: async () => [],
  renameSession: async () => {},
  deleteSession: async () => {},
  forkSession: async () => ({}),
}));
vi.mock('../../server/claude.js', () => ({ resolveClaude: async () => ({ path: '/bin/true' }) }));
vi.mock('../../server/snapshots.js', async (orig) => ({ ...(await orig<any>()), record: vi.fn(async () => null) }));

const { sessions, bus } = await import('../../server/sessions.js');
const { store } = await import('../../server/config.js');
const snapshots = await import('../../server/snapshots.js');

const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-sessions-'));
store.setConfig({ memoryAuto: false, memoryInject: false });

/** Wait until `ok()` holds (the session reacts on timers and promises). */
async function until(ok: () => boolean, ms = 2000) {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 5));
  }
}

const init = (sid: string) => ({ type: 'system', subtype: 'init', session_id: sid, model: 'claude-test' });
const said = (sid: string, text: string) => ({ type: 'assistant', uuid: `a-${text}`, session_id: sid, parent_tool_use_id: null, message: { role: 'assistant', content: [{ type: 'text', text }] } });
const result = (sid: string) => ({ type: 'result', subtype: 'success', session_id: sid, result: 'done', total_cost_usd: 0.01, modelUsage: {} });

/** A new session with a running process that has said hello. */
async function live() {
  const { id } = await sessions.create({ cwd });
  const s: any = sessions.find(id);
  await s.send({ text: 'first' });
  const q = sdk.queries.at(-1)!;
  q.emit(init(id));
  await until(() => q.sent.length === 1 && s.status === 'running');
  return { id, s, q };
}

beforeEach(() => vi.mocked(snapshots.record).mockClear());
afterAll(() => sessions.stop());

describe('the held queue', () => {
  it('keeps a message typed while Claude works and hands it over when the turn ends', async () => {
    const { id, s, q } = await live();
    await s.send({ text: 'second' });
    expect(s.held.map((h: any) => h.text)).toEqual(['second']);
    expect(q.sent).toHaveLength(1);
    q.emit(said(id, 'ok'), result(id));
    await until(() => q.sent.length === 2);
    expect(q.sent[1].message.content).toEqual([{ type: 'text', text: 'second' }]);
    expect(s.held).toEqual([]);
  });

  it('sends a held message now by interrupting the turn', async () => {
    const { s, q } = await live();
    const u = await s.send({ text: 'later' });
    expect(await s.sendHeldNow(u)).toBe(true);
    expect(q.interrupts).toBe(1);
    expect(s.removeHeld(u)).toBe(true);
    expect(s.held).toEqual([]);
  });
});

describe('permissions', () => {
  it('waits for the person, passes the answer on, and denies what is left when the process ends', async () => {
    const { s, q } = await live();
    const asked: any[] = [];
    const onAsk = (d: any) => asked.push(d);
    bus.on('permission', onAsk);
    const signal = new AbortController().signal;
    const first = q.options.canUseTool('Bash', { command: 'ls' }, { signal, toolUseID: 't1' });
    expect(s.status).toBe('requires_action');
    expect(asked[0].request).toMatchObject({ toolName: 'Bash', toolUseId: 't1' });
    expect(s.respond(asked[0].request.requestId, { behavior: 'allow' })).toBe(true);
    expect(await first).toEqual({ behavior: 'allow', updatedInput: { command: 'ls' } });
    expect(s.respond('no-such-request', { behavior: 'allow' })).toBe(false);

    const second = q.options.canUseTool('Write', { file_path: '/x' }, { signal });
    q.end();
    expect(await second).toEqual({ behavior: 'deny', message: 'Session ended' });
    bus.off('permission', onAsk);
  });
});

describe('the process', () => {
  it('reports a failure with the end of stderr, and reads on past a message it cannot handle', async () => {
    const { id, s, q } = await live();
    q.options.stderr('something broke\n');
    q.emit({ type: 'result', modelUsage: { broken: null } }, said(id, 'still here'));
    q.end(new Error('Claude Code exited with code 1'));
    await until(() => s.status === 'error');
    expect(s.messages.some((m: any) => m.uuid === 'a-still here')).toBe(true);
    expect(s.error).toBe('Claude Code exited with code 1\nsomething broke');
    expect(s.messages.at(-1)).toMatchObject({ subtype: 'pilcrow_error' });
    expect(s.q).toBe(null);
  });

  it('is stopped, not failed, when Pilcrow closes it, and starts a fresh one on the next message', async () => {
    const { s } = await live();
    s.close();
    await until(() => s.status === 'stopped');
    expect(s.error).toBe(null);
    const before = sdk.queries.length;
    await s.send({ text: 'again' });
    expect(sdk.queries.length).toBe(before + 1);
  });
});

describe('memory over a long run', () => {
  it('closes an idle process, then lets go of its transcript, and reads it back on the next open', async () => {
    const { id, s, q } = await live();
    q.emit(said(id, 'hello'), result(id));
    await until(() => s.status === 'idle');
    s.lastActivity = Date.now() - 31 * 60e3;
    sessions.reap(); // first the process…
    await until(() => s.q === null);
    expect(s.messages.length).toBeGreaterThan(0);
    s.lastActivity = Date.now() - 31 * 60e3;
    sessions.reap(); // …then the transcript
    expect(s.messages).toEqual([]);
    expect(s.loaded).toBe(false);

    sdk.transcript = [{ type: 'user', uuid: 'u1', session_id: id, message: { role: 'user', content: 'first' } }, said(id, 'hello')];
    const snap = await s.snapshot();
    expect(snap.messages.map((m: any) => m.uuid)).toEqual(['u1', 'a-hello']);
    expect(s.loaded).toBe(true);
  });

  it('keeps the transcript and the process of a session that is still working', async () => {
    const { s, q } = await live();
    s.lastActivity = Date.now() - 31 * 60e3;
    sessions.reap();
    expect(s.q).toBe(q); // running: not idle
    expect(s.release()).toBe(false);
  });

  it('keeps every running task and only the newest finished ones', async () => {
    const { id, s, q } = await live();
    const sys = (subtype: string, extra: any) => ({ type: 'system', subtype, session_id: id, ...extra });
    q.emit(sys('task_started', { task_id: 'keep', description: 'long' }));
    for (let i = 0; i < 150; i++) q.emit(sys('task_started', { task_id: `t${i}` }), sys('task_notification', { task_id: `t${i}`, status: 'completed' }));
    q.emit(sys('task_started', { task_id: 'last' }));
    await until(() => s.tasks.has('last'));
    expect(s.tasks.size).toBeLessThanOrEqual(101);
    expect(s.tasks.get('keep').status).toBe('running');
    expect(s.tasks.has('t0')).toBe(false);
    expect(s.tasks.has('t149')).toBe(true);
  });
});

describe('the timeline', () => {
  it('takes one step for the file-touching tool calls of a turn, once it settles', async () => {
    const { id, q } = await live();
    const use = (tid: string, name: string, input: any) => ({ type: 'assistant', session_id: id, parent_tool_use_id: null, message: { role: 'assistant', content: [{ type: 'tool_use', id: tid, name, input }] } });
    const done = (tid: string) => ({ type: 'user', session_id: id, parent_tool_use_id: null, message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: tid, content: 'ok' }] } });
    q.emit(use('e1', 'Edit', { file_path: path.join(cwd, 'a.txt') }), done('e1'), use('r1', 'Read', { file_path: 'b.txt' }), done('r1'), use('b1', 'Bash', { command: 'npm test' }), done('b1'));
    await until(() => !(sessions.find(id) as any).toolUses.size);
    const toolSteps = () => vi.mocked(snapshots.record).mock.calls.filter(([, w]: any) => w.kind === 'tool');
    expect(toolSteps()).toHaveLength(0);
    q.emit(result(id));
    await until(() => toolSteps().length === 1);
    expect(toolSteps()[0][1]).toEqual({ kind: 'tool', tool: 'Edit, Bash', toolUseId: 'e1', label: 'a.txt and 1 more' });
  });
});
