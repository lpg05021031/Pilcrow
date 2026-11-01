/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// sessions.js against a fake Agent SDK: the queue of messages typed while Claude works, the permission
// flow, how a process ends (on purpose, by failing, past a bad message), the idle reaper letting go of
// transcripts, the cap on finished tasks, one timeline step per settled turn, and what one window is
// sent during a streamed turn and a workflow's progress storm (bytes per second, the longest stall).
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { monitorEventLoopDelay } from 'node:perf_hooks';

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

describe('what one window receives', () => {
  // Budgets for one person's machine: a window on a slow tunnel keeps up, and the server stays responsive.
  // The workflow is the worst case: sixty agents with every preview at its cap, re-sent with each state.
  const TURN_BYTES_PER_SEC = 96 * 1024;
  const WORKFLOW_BYTES_PER_SEC = 512 * 1024;
  const STALL_MS = 50;
  const TICK_MS = 4;

  /** Count what the server would broadcast (main.js sends every bus event to every window as JSON). */
  function measure() {
    const out = { bytes: 0, states: 0, stateBytes: 0 };
    const add = (d: any, isState = false) => {
      const n = JSON.stringify(d).length;
      out.bytes += n;
      if (isState) {
        out.states++;
        out.stateBytes += n;
      }
    };
    const onState = (d: any) => add(d, true);
    const onOther = (d: any) => add(d);
    bus.on('state', onState);
    bus.on('msgs', onOther);
    const loop = monitorEventLoopDelay({ resolution: 1 });
    loop.enable();
    const t0 = performance.now();
    return {
      out,
      stop() {
        bus.off('state', onState);
        bus.off('msgs', onOther);
        loop.disable();
        return { ...out, seconds: (performance.now() - t0) / 1000, stallMs: loop.max / 1e6 };
      },
    };
  }

  /** A live session whose Claude Code reports what a real one with plugins and skills does. */
  async function heavy() {
    const { id } = await sessions.create({ cwd });
    const s: any = sessions.find(id);
    await s.send({ text: 'go' });
    const q: any = sdk.queries.at(-1)!;
    const many = (n: number, f: (i: number) => any) => Array.from({ length: n }, (_, i) => f(i));
    q.supportedCommands = async () => many(80, (i) => ({ name: `command-${i}`, description: 'A command from a plugin that does something useful with a long description '.repeat(2), argumentHint: '[args]' }));
    q.supportedModels = async () => many(12, (i) => ({ value: `model-${i}`, displayName: `Model ${i}`, description: 'A model with a description of what it is good at' }));
    q.emit({ ...init(id), tools: many(80, (i) => `mcp__server__tool_number_${i}`), slash_commands: many(80, (i) => `command-${i}`), agents: many(20, (i) => `agent-${i}`), skills: many(40, (i) => `skill-${i}`), plugins: many(10, (i) => ({ name: `plugin-${i}`, path: `/home/me/.claude/plugins/cache/plugin-${i}` })), mcp_servers: many(6, (i) => ({ name: `server-${i}`, status: 'connected' })) });
    await until(() => s.commands?.length === 80);
    return { id, s, q };
  }

  const pace = async (n: number, each: (i: number) => void) => {
    for (let i = 0; i < n; i++) {
      each(i);
      await new Promise((r) => setTimeout(r, TICK_MS));
    }
  };

  it('sends what stays put for a process (init, commands, models) once, not with every state', async () => {
    const seen: any[] = [];
    const on = (d: any) => seen.push(d);
    bus.on('state', on);
    const { id, s } = await heavy();
    await s.setEffort('high'); // a routine update
    bus.off('state', on);
    const mine = seen.filter((d) => d.id === id);
    const full = mine.filter((d) => 'init' in d);
    expect(full.length).toBeGreaterThanOrEqual(1); // when init arrives, and when the commands and models do
    expect(full.at(-1).commands).toHaveLength(80);
    expect(mine.at(-1)).toMatchObject({ effort: 'high' });
    expect(mine.at(-1)).not.toHaveProperty('init');
    expect(mine.at(-1)).not.toHaveProperty('commands');
    const snap = await s.snapshot();
    expect(snap.state.init.tools).toHaveLength(80);
    expect(snap.state.models).toHaveLength(12);
  });

  it('a streamed turn stays under its byte rate and never stalls the loop', async () => {
    const { id, s, q } = await heavy();
    const m = measure();
    const ev = (event: any) => ({ type: 'stream_event', session_id: id, parent_tool_use_id: null, event });
    q.emit(ev({ type: 'message_start', message: { id: 'm1' } }));
    await pace(250, (i) => {
      q.emit(ev({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: `word ${i} of a long streamed answer, ` } }));
      if (i % 50 === 49) q.emit({ type: 'system', subtype: 'status', session_id: id, status: null });
    });
    q.emit(said(id, 'the whole answer'), result(id));
    await until(() => s.status === 'idle');
    const r = m.stop();
    console.log(`turn: ${Math.round(r.bytes / r.seconds / 1024)} KB/s to one window, ${r.states} states averaging ${Math.round(r.stateBytes / r.states)} bytes, longest stall ${r.stallMs.toFixed(1)} ms`);
    expect(r.bytes / r.seconds).toBeLessThan(TURN_BYTES_PER_SEC);
    expect(r.stallMs).toBeLessThan(STALL_MS);
  });

  it('a workflow of sixty agents reporting at once stays under its byte rate and never stalls the loop', async () => {
    const { id, s, q } = await heavy();
    q.emit({ type: 'system', subtype: 'task_started', session_id: id, task_id: 'wf', task_type: 'local_workflow', description: 'review', prompt: 'p'.repeat(4000) });
    const text = (n: number, i: number) => `agent ${i}: `.padEnd(n, 'the agent is looking at a file and says what it found. ');
    const logs: any[] = [];
    const m = measure();
    await pace(250, (t) => {
      if (t % 3 === 0) logs.push({ type: 'workflow_log', message: text(900, t) });
      const agents = Array.from({ length: 60 }, (_, i) => ({ type: 'workflow_agent', index: i, label: `reviewer ${i}`, phaseIndex: i % 3, state: 'progress', promptPreview: text(600, i), resultPreview: t > 200 ? text(600, i) : undefined, lastToolSummary: text(300, t), lastToolName: 'Read', model: 'claude-test' }));
      q.emit({ type: 'system', subtype: 'task_progress', session_id: id, task_id: 'wf', description: 'review', summary: `tick ${t}`, workflow_progress: [...agents, ...logs] });
    });
    await new Promise((r) => setTimeout(r, 200)); // the last coalesced state
    const r = m.stop();
    console.log(`workflow: ${Math.round(r.bytes / r.seconds / 1024)} KB/s to one window, ${r.states} states averaging ${Math.round(r.stateBytes / r.states)} bytes, longest stall ${r.stallMs.toFixed(1)} ms`);
    expect(s.tasks.get('wf').workflow.agents).toHaveLength(60);
    expect(r.bytes / r.seconds).toBeLessThan(WORKFLOW_BYTES_PER_SEC);
    expect(r.stallMs).toBeLessThan(STALL_MS);
  });
});
