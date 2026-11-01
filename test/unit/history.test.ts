/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// history.withEarlierMessages: a reopened session reads the same as it did live, across compactions.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { withEarlierMessages } from '../../server/history.js';

const SID = 'sess-1';
const at = (n: number) => new Date(Date.UTC(2026, 0, 1, 0, n)).toISOString();
const user = (uuid: string, parentUuid: string | null, text: string, n: number, extra = {}) => ({ type: 'user', uuid, parentUuid, sessionId: SID, timestamp: at(n), message: { role: 'user', content: text }, ...extra });
const said = (uuid: string, parentUuid: string, text: string, n: number) => ({ type: 'assistant', uuid, parentUuid, sessionId: SID, timestamp: at(n), message: { role: 'assistant', content: [{ type: 'text', text }] } });
const boundary = (uuid: string, logicalParentUuid: string, n: number, pre: number) => ({ type: 'system', subtype: 'compact_boundary', uuid, parentUuid: null, logicalParentUuid, sessionId: SID, timestamp: at(n), compactMetadata: { trigger: 'manual', preTokens: pre, postTokens: 900 } });

function transcript(lines: any[]) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-history-')), `${SID}.jsonl`);
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n') + '\nnot json\n');
  return file;
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
    const out = await withEarlierMessages(fromSdk(), transcript(lines), SID);
    expect(out.map((m: any) => m.uuid)).toEqual(['u1', 'a1', 'b1', 's1', 'u2', 'a2', 'b2', 's2', 'u3', 'a3']);
  });

  it('gives each boundary its trigger and token counts', async () => {
    const out = await withEarlierMessages(fromSdk(), transcript(lines), SID);
    const b = out.filter((m: any) => m.subtype === 'compact_boundary');
    expect(b.map((m: any) => m.compact_metadata)).toEqual([
      { trigger: 'manual', pre_tokens: 50_000, post_tokens: 900 },
      { trigger: 'manual', pre_tokens: 80_000, post_tokens: 900 },
    ]);
    expect(out.find((m: any) => m.uuid === 's1').isCompactSummary).toBe(true);
  });

  it('leaves the messages alone when the transcript cannot be read', async () => {
    const msgs = fromSdk();
    expect(await withEarlierMessages(msgs, '/no/such/transcript.jsonl', SID)).toBe(msgs);
  });
});
