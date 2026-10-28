import { describe, expect, it } from 'vitest';
import { TranscriptModel, type ToolItem, type TextItem, type UserItem } from '../../web/src/lib/transcript';
import type { RawMsg } from '../../web/src/lib/types';

let seq = 0;
const uid = () => `uuid-${++seq}`;
const userMsg = (text: string, extra: Partial<RawMsg> = {}): RawMsg => ({ type: 'user', uuid: uid(), message: { role: 'user', content: [{ type: 'text', text }] }, parent_tool_use_id: null, ...extra });
const stream = (event: RawMsg['event'], parent: string | null = null): RawMsg => ({ type: 'stream_event', uuid: uid(), event, parent_tool_use_id: parent });
const assistant = (id: string, content: NonNullable<Extract<RawMsg['message'], object>['content']>, extra: Partial<RawMsg> = {}): RawMsg => ({ type: 'assistant', uuid: uid(), message: { id, role: 'assistant', content }, parent_tool_use_id: null, ...extra });
const toolResult = (toolUseId: string, content: string, isError = false, structured?: unknown): RawMsg => ({ type: 'user', uuid: uid(), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: toolUseId, content, is_error: isError }] }, parent_tool_use_id: null, tool_use_result: structured });
const result = (extra: Partial<RawMsg> = {}): RawMsg => ({ type: 'result', uuid: uid(), subtype: 'success', duration_ms: 1200, total_cost_usd: 0.01, num_turns: 1, ...extra });

/** A whole streamed text block: start, deltas, stop. */
function streamText(m: TranscriptModel, msgId: string, index: number, words: string[]) {
  m.push(stream({ type: 'content_block_start', index, content_block: { type: 'text', text: '' } }));
  for (const w of words) m.push(stream({ type: 'content_block_delta', index, delta: { type: 'text_delta', text: w } }));
  m.push(stream({ type: 'content_block_stop', index }));
}

describe('TranscriptModel: a plain turn', () => {
  it('user text, streamed reply, final assistant message and result', () => {
    const m = new TranscriptModel();
    m.push(userMsg('hello'));
    expect(m.items).toHaveLength(1);
    expect((m.items[0] as UserItem).text).toBe('hello');

    m.push(stream({ type: 'message_start', message: { id: 'msg_1' } }));
    streamText(m, 'msg_1', 0, ['Hel', 'lo ', 'there']);
    const t = m.items[1] as TextItem;
    expect(t.kind).toBe('text');
    expect(t.text).toBe('Hello there');
    expect(t.streaming).toBe(false);

    // The final message replaces the draft rather than adding a twin.
    m.push(assistant('msg_1', [{ type: 'text', text: 'Hello there!' }]));
    expect(m.items).toHaveLength(2);
    expect((m.items[1] as TextItem).text).toBe('Hello there!');

    m.push(result());
    expect(m.items[2]).toMatchObject({ kind: 'result', ok: true, durationMs: 1200, costUsd: 0.01 });
    expect(m.items[2]).toMatchObject({ chainUuid: 'uuid-' + (seq - 1) });
  });

  it('the same uuid is never applied twice (history replayed over live)', () => {
    const m = new TranscriptModel();
    const u = userMsg('once');
    m.push(u);
    m.push(u);
    expect(m.items).toHaveLength(1);
  });

  it('an empty text block from the API is not shown', () => {
    const m = new TranscriptModel();
    m.push(assistant('m', [{ type: 'text', text: '   ' }]));
    expect(m.items).toHaveLength(0);
  });
});

describe('TranscriptModel: tools', () => {
  it('tool input streams as partial JSON, then the result settles the status', () => {
    const m = new TranscriptModel();
    m.push(userMsg('read it'));
    m.push(stream({ type: 'message_start', message: { id: 'msg_2' } }));
    m.push(stream({ type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'toolu_1', name: 'Read', input: {} } }));
    m.push(stream({ type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: '{"file_pa' } }));
    m.push(stream({ type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: 'th": "/a/b.ts"' } }));
    const t = m.tools.get('toolu_1')!;
    expect(t.status).toBe('streaming');
    expect(t.input).toEqual({ file_path: '/a/b.ts' });
    m.push(stream({ type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: '}' } }));
    m.push(stream({ type: 'content_block_stop', index: 0 }));
    expect(t.status).toBe('running');
    expect(t.inputDone).toBe(true);

    m.push(assistant('msg_2', [{ type: 'tool_use', id: 'toolu_1', name: 'Read', input: { file_path: '/a/b.ts' } }]));
    expect(m.items.filter((i) => i.kind === 'tool')).toHaveLength(1);
    expect(m.lastTurnTools).toEqual(['toolu_1']);

    m.push(toolResult('toolu_1', 'line 1\nline 2', false, { type: 'text', file: { filePath: '/a/b.ts', numLines: 2 } }));
    expect(t.status).toBe('done');
    expect(t.result?.text).toBe('line 1\nline 2');
    expect(t.structured?.file?.numLines).toBe(2);
  });

  it('error results are classified: denied, interrupted, error', () => {
    const m = new TranscriptModel();
    for (const [id, text] of [
      ['a', "The user doesn't want to proceed with this tool use."],
      ['b', 'Request interrupted by user for tool use'],
      ['c', 'ENOENT: no such file'],
    ] as const) {
      m.push(assistant('m-' + id, [{ type: 'tool_use', id, name: 'Bash', input: { command: 'x' } }]));
      m.push(toolResult(id, text, true));
    }
    expect(m.tools.get('a')!.status).toBe('denied');
    expect(m.tools.get('b')!.status).toBe('interrupted');
    expect(m.tools.get('c')!.status).toBe('error');
  });

  it('a subagent’s steps nest under the Agent card', () => {
    const m = new TranscriptModel();
    m.push(assistant('m1', [{ type: 'tool_use', id: 'agent_1', name: 'Agent', input: { description: 'look around' } }]));
    m.push({ ...assistant('m2', [{ type: 'tool_use', id: 'sub_1', name: 'Grep', input: { pattern: 'x' } }]), parent_tool_use_id: 'agent_1' });
    m.push({ ...toolResult('sub_1', 'found'), parent_tool_use_id: 'agent_1' });
    const agent = m.tools.get('agent_1')!;
    expect(m.items.filter((i) => i.kind === 'tool')).toHaveLength(1);
    expect(agent.children).toHaveLength(1);
    expect((agent.children[0] as ToolItem).status).toBe('done');
    expect(agent.rev).toBeGreaterThan(0); // the parent re-renders when a child changes
  });

  it('the turn ending settles whatever was still running', () => {
    const m = new TranscriptModel();
    m.push(assistant('m1', [{ type: 'tool_use', id: 't1', name: 'Bash', input: {} }]));
    m.push(stream({ type: 'message_start', message: { id: 'msg_x' } }));
    m.push(stream({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }));
    m.push(stream({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'part' } }));
    m.push(result({ subtype: 'error_during_execution', is_error: true, result: 'stopped' }));
    expect(m.tools.get('t1')!.status).toBe('interrupted');
    const text = m.items.find((i) => i.kind === 'text') as TextItem;
    expect(text.streaming).toBe(false);
    expect(m.items.at(-1)).toMatchObject({ kind: 'result', ok: false, text: 'stopped' });
  });

  it('background tasks: a task notification becomes a status line and finishes the tool', () => {
    const m = new TranscriptModel();
    m.push(assistant('m1', [{ type: 'tool_use', id: 'bg_1', name: 'Bash', input: { command: 'npm test', description: 'Run the tests', run_in_background: true } }]));
    m.push(toolResult('bg_1', 'started', false, { isAsync: true, backgroundTaskId: 'task_9' }));
    const t = m.tools.get('bg_1')!;
    expect(t.status).toBe('background');
    m.push({ type: 'system', subtype: 'task_started', uuid: uid(), task_id: 'task_9', tool_use_id: 'bg_1' });
    expect(t.taskId).toBe('task_9');
    m.push({ type: 'system', subtype: 'task_notification', uuid: uid(), task_id: 'task_9', tool_use_id: 'bg_1', status: 'completed', summary: 'All green' });
    expect(t.status).toBe('done');
    expect(m.items.at(-1)).toMatchObject({ kind: 'notice', tag: 'task', text: 'Background command “Run the tests” finished' });
  });
});

describe('TranscriptModel: system messages', () => {
  it('interrupts, compaction, retries and permission denials', () => {
    const m = new TranscriptModel();
    m.push(userMsg('[Request interrupted by user]'));
    expect(m.items.at(-1)).toMatchObject({ kind: 'notice', tag: 'interrupt' });

    m.push({ type: 'system', subtype: 'compact_boundary', uuid: uid(), compact_metadata: { trigger: 'auto', pre_tokens: 150000, post_tokens: 20000 } });
    expect(m.items.at(-1)).toMatchObject({ kind: 'compact', trigger: 'auto', preTokens: 150000 });

    m.push({ type: 'system', subtype: 'api_retry', uuid: uid(), attempt: 1, max_retries: 10, error_status: 529 });
    m.push({ type: 'system', subtype: 'api_retry', uuid: uid(), attempt: 2, max_retries: 10, error_status: 529 });
    const retries = m.items.filter((i) => i.kind === 'notice' && i.tag === 'retry');
    expect(retries).toHaveLength(1);
    expect(retries[0]).toMatchObject({ count: 2, text: 'Retrying request (attempt 2 of 10) after HTTP 529' });
    // The reply arriving settles the warning into a quiet note.
    m.push(stream({ type: 'message_start', message: { id: 'ok' } }));
    expect(retries[0]).toMatchObject({ tag: 'retry-ok', level: 'info', text: 'Connected after 2 retries (HTTP 529)' });

    m.push(assistant('m9', [{ type: 'tool_use', id: 'den', name: 'Write', input: {} }]));
    m.push({ type: 'system', subtype: 'permission_denied', uuid: uid(), tool_use_id: 'den', tool_name: 'Write', decision_reason: 'policy' });
    expect(m.tools.get('den')!.status).toBe('denied');
    expect(m.items.at(-1)).toMatchObject({ kind: 'notice', level: 'warning', text: 'Write was not allowed', detail: 'policy' });
  });

  it('slash commands and their output', () => {
    const m = new TranscriptModel();
    m.push(userMsg('<command-name>/compact</command-name><command-args>focus on tests</command-args>'));
    expect(m.items.at(-1)).toMatchObject({ kind: 'user', command: { name: 'compact', args: 'focus on tests' } });
    m.push(userMsg('<local-command-stdout>Compacted.</local-command-stdout>'));
    expect(m.items.at(-1)).toMatchObject({ kind: 'command-output', text: 'Compacted.' });
    m.push(userMsg('<local-command-caveat>ignored</local-command-caveat>'));
    expect(m.items).toHaveLength(2);
  });

  it('an API error shows once, not again as the result', () => {
    const m = new TranscriptModel();
    m.push(assistant('bad', [{ type: 'text', text: 'Invalid API key' }], { error: 'authentication_failed' }));
    expect(m.items).toHaveLength(1);
    expect(m.items[0]).toMatchObject({ kind: 'notice', level: 'error', text: expect.stringContaining('Authentication failed') });
    m.push(result({ subtype: 'error_during_execution', is_error: true, result: 'Invalid API key' }));
    expect(m.items).toHaveLength(1);
  });

  it('latestTool finds the newest by name', () => {
    const m = new TranscriptModel();
    m.push(assistant('a', [{ type: 'tool_use', id: 'p1', name: 'ExitPlanMode', input: {} }]));
    m.push(assistant('b', [{ type: 'tool_use', id: 'p2', name: 'ExitPlanMode', input: {} }]));
    expect(m.latestTool('ExitPlanMode')?.id).toBe('p2');
    expect(m.latestTool('TodoWrite')).toBeNull();
  });
});
