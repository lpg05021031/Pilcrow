/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// history.js over readTranscript's one pass: withEarlierMessages, so a reopened session reads the same
// as it did live across compactions, and loadTranscript handing the SDK's reader the parsed records
// instead of the file, which must read the same as the SDK reading the file itself.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { getSessionMessages } from '@anthropic-ai/claude-agent-sdk';
import { loadTranscript, readTranscript, withEarlierMessages } from '../../server/history.js';

const SID = 'sess-1';
const at = (n: number) => new Date(Date.UTC(2026, 0, 1, 0, n)).toISOString();
const user = (uuid: string, parentUuid: string | null, text: string, n: number, extra = {}) => ({ type: 'user', uuid, parentUuid, sessionId: SID, timestamp: at(n), message: { role: 'user', content: text }, ...extra });
const said = (uuid: string, parentUuid: string, text: string, n: number) => ({ type: 'assistant', uuid, parentUuid, sessionId: SID, timestamp: at(n), message: { role: 'assistant', content: [{ type: 'text', text }] } });
const boundary = (uuid: string, logicalParentUuid: string, n: number, pre: number) => ({ type: 'system', subtype: 'compact_boundary', uuid, parentUuid: null, logicalParentUuid, sessionId: SID, timestamp: at(n), compactMetadata: { trigger: 'manual', preTokens: pre, postTokens: 900 } });

/** The raw records by uuid, as loadTranscript reads them after a compaction. */
async function transcript(lines: any[]) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-history-')), `${SID}.jsonl`);
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n') + '\nnot json\n');
  return (await readTranscript(file))!.byUuid;
}

describe('withEarlierMessages', () => {
  // Two compactions: u1 a1 | b1 | u2 a2 | b2 | summary u3 a3. The SDK's reader returns only what follows b2.
  const lines = [
    user('u1', null, 'first question', 1),
    said('a1', 'u1', 'first answer', 2),
    boundary('b1', 'a1', 3, 50_000),
    user('s1', 'b1', 'summary one', 4, { isCompactSummary: true }),
    user('u2', 's1', 'second question', 5),
    said('a2', 'u2', 'second answer', 6),
    boundary('b2', 'a2', 7, 80_000),
    user('s2', 'b2', 'summary two', 8, { isCompactSummary: true }),
    user('u3', 's2', 'third question', 9),
    said('a3', 'u3', 'third answer', 10),
  ];
  const fromSdk = () => [
    { type: 'system', uuid: 'b2', session_id: SID, timestamp: at(7) },
    { type: 'user', uuid: 's2', session_id: SID, timestamp: at(8), isCompactSummary: true, message: { role: 'user', content: 'summary two' } },
    { type: 'user', uuid: 'u3', session_id: SID, timestamp: at(9), message: { role: 'user', content: 'third question' } },
    { type: 'assistant', uuid: 'a3', session_id: SID, timestamp: at(10), message: { role: 'assistant', content: [] } },
  ];

  it('puts back the conversation before every compaction, in the order it happened', async () => {
    const out = withEarlierMessages(fromSdk(), await transcript(lines), SID);
    expect(out.map((m: any) => m.uuid)).toEqual(['u1', 'a1', 'b1', 's1', 'u2', 'a2', 'b2', 's2', 'u3', 'a3']);
  });

  it('gives each boundary its trigger and token counts', async () => {
    const out = withEarlierMessages(fromSdk(), await transcript(lines), SID);
    const b = out.filter((m: any) => m.subtype === 'compact_boundary');
    expect(b.map((m: any) => m.compact_metadata)).toEqual([
      { trigger: 'manual', pre_tokens: 50_000, post_tokens: 900 },
      { trigger: 'manual', pre_tokens: 80_000, post_tokens: 900 },
    ]);
    expect(out.find((m: any) => m.uuid === 's1').isCompactSummary).toBe(true);
  });

  it('reads nothing from a transcript that cannot be read; every record, and the tool results, from one that can', async () => {
    expect(await readTranscript('/no/such/transcript.jsonl')).toBe(null);
    const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-history-')), `${SID}.jsonl`);
    fs.writeFileSync(file, [JSON.stringify({ ...user('u1', null, 'q', 1), toolUseResult: { stdout: 'ok' } }), '', 'not json', JSON.stringify(said('a1', 'u1', 'a', 2)), JSON.stringify({ type: 'summary', summary: 's' })].join('\r\n'));
    const raw = (await readTranscript(file))!;
    expect([...raw.results]).toEqual([['u1', { stdout: 'ok' }]]);
    expect([...raw.byUuid.keys()]).toEqual(['u1', 'a1']);
    expect(raw.entries.map((e) => e.type)).toEqual(['user', 'assistant', 'summary']);
  });
});

describe('loadTranscript', () => {
  // A real session id (the SDK's reader checks it is a UUID) in Claude Code's projects folder.
  const id = '0b7c1a52-3e0f-4c55-9d7e-6d8a2f1c9b10';
  const cwd = path.join(os.tmpdir(), 'pilcrow-history-project');
  const rec = (o: any) => ({ sessionId: id, cwd, isSidechain: false, userType: 'external', ...o });
  const lines = [
    rec(user('u1', null, 'first question', 1)),
    rec({ type: 'assistant', uuid: 'a1', parentUuid: 'u1', timestamp: at(2), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Read', input: { file_path: 'x' } }] } }),
    rec({ type: 'user', uuid: 'r1', parentUuid: 'a1', timestamp: at(3), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'text' }] }, toolUseResult: { file: { filePath: 'x', numLines: 1 } } }),
    rec(said('a2', 'r1', 'first answer', 4)),
    rec(boundary('b1', 'a2', 5, 50_000)),
    rec(user('s1', 'b1', 'summary', 6, { isCompactSummary: true })),
    rec(user('u2', 's1', 'second question', 7)),
    rec(said('a3', 'u2', 'second answer', 8)),
  ];
  const dir = path.join(process.env.CLAUDE_CONFIG_DIR!, 'projects', cwd.replace(/[^a-zA-Z0-9]/g, '-'));
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${id}.jsonl`), lines.map((l) => JSON.stringify(l)).join('\n') + '\n');

  it('reads what the SDK reads from the file, then the tool results and the conversation before the compaction', async () => {
    const fromFile = await getSessionMessages(id, { dir: cwd, includeSystemMessages: true });
    const msgs: any[] = await loadTranscript(id, cwd);
    expect(msgs.slice(-fromFile.length).map((m) => m.uuid)).toEqual(fromFile.map((m) => m.uuid));
    expect(msgs.map((m) => m.uuid)).toEqual(['u1', 'a1', 'r1', 'a2', 'b1', 's1', 'u2', 'a3']);
    expect(msgs.find((m) => m.uuid === 'r1').tool_use_result).toEqual({ file: { filePath: 'x', numLines: 1 } });
    expect(msgs.every((m) => m._history)).toBe(true);
  });
});
