/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// Performance budgets for the server's own paths, on the real server (serve.ts) and real git: how many
// git processes each action starts (counted by a `git` on PATH that logs and hands over to the real
// one), and how long sessions.list and session.open take over generated Claude Code transcripts. The
// budgets are counts and generous latencies: a regression doubles them, noise does not.
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as snapshots from '../../server/snapshots.js';
import { serve } from './serve.js';

// git processes per action (git.js, snapshots.js say why each one runs)
const SPAWNS = {
  'git.info, clean': 2, // status + rev-parse, together
  'git.info, with changes': 3, // + diff --numstat for the line totals
  'git.diff': 3, // status + rev-parse + diff
  'git.diff, against a branch': 4, // + merge-base
  'timeline step': 6, // add, write-tree, commit-tree, update-ref, diff-tree ×2
};
const TRANSCRIPT_MB = 27; // the reviewer's long conversation
const SESSIONS = 60; // other conversations in the list
const LIST_MS = 1000; // sessions.list, cold (the SDK reads the head of every transcript)
const OPEN_MS = 3000; // session.open of the long one, from the request to the whole answer parsed
const OPEN_MB = 60; // …and the size of that answer

const { app, connect } = await serve();
const { ws, rpc } = await connect();
afterAll(async () => {
  ws.close();
  await app.stop();
});

// ---------------------------------------------------------------- git, counted

const realGit = execFileSync('sh', ['-c', 'command -v git']).toString().trim();
const shimDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-gitshim-'));
const gitLog = path.join(shimDir, 'calls.log');
fs.writeFileSync(path.join(shimDir, 'git'), `#!/bin/sh\necho "$1 $2 $3 $4 $5 $6 $7" >> "${gitLog}"\nexec "${realGit}" "$@"\n`, { mode: 0o755 });
const PATH = process.env.PATH;

/** The git processes `fn` starts, one line each. */
async function gitCalls(fn: () => Promise<unknown>) {
  fs.writeFileSync(gitLog, '');
  process.env.PATH = `${shimDir}${path.delimiter}${PATH}`;
  try {
    await fn();
  } finally {
    process.env.PATH = PATH;
  }
  return fs.readFileSync(gitLog, 'utf8').split('\n').filter(Boolean);
}

describe('git processes per action', () => {
  let repo: string;
  const g = (...args: string[]) => execFileSync(realGit, args, { cwd: repo, stdio: 'pipe' });
  beforeAll(() => {
    repo = path.join(fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-spawns-'))), 'repo');
    fs.mkdirSync(repo);
    g('init', '-q', '-b', 'main');
    fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
    g('add', '.');
    g('commit', '-qm', 'first');
    g('branch', 'base');
  });

  it('stays within its count for each action', async () => {
    const counts: Record<string, number> = {};
    counts['git.info, clean'] = (await gitCalls(() => rpc('git.info', { cwd: repo }))).length;
    fs.writeFileSync(path.join(repo, 'a.txt'), 'one\ntwo\n');
    counts['git.info, with changes'] = (await gitCalls(() => rpc('git.info', { cwd: repo }))).length;
    counts['git.diff'] = (await gitCalls(() => rpc('git.diff', { cwd: repo }))).length;
    counts['git.diff, against a branch'] = (await gitCalls(() => rpc('git.diff', { cwd: repo, base: 'base' }))).length;
    const session = { id: 'spawns', cwd: repo };
    await snapshots.record(session, { kind: 'start' });
    fs.writeFileSync(path.join(repo, 'a.txt'), 'one\ntwo\nthree\n');
    counts['timeline step'] = (await gitCalls(() => snapshots.record(session, { kind: 'tool', tool: 'Edit' }))).length;
    console.log('git processes:', counts);
    expect(counts).toEqual(SPAWNS);
  });
});

// ---------------------------------------------------------------- transcripts

/** A Claude Code transcript of about `mb` megabytes in `cwd`'s projects folder: turns of a prompt, a
 *  reply that reads a file, the file coming back (with its toolUseResult) and an answer. */
function writeTranscript(cwd: string, mb: number) {
  const sid = crypto.randomUUID();
  const dir = path.join(process.env.CLAUDE_CONFIG_DIR!, 'projects', cwd.replace(/[^a-zA-Z0-9]/g, '-'));
  fs.mkdirSync(dir, { recursive: true });
  const lines: string[] = [];
  let parent: string | null = null;
  let bytes = 0;
  const t0 = Date.parse('2026-01-01T00:00:00Z');
  const rec = (o: any) => {
    const uuid = crypto.randomUUID();
    const line = JSON.stringify({ parentUuid: parent, isSidechain: false, userType: 'external', cwd, sessionId: sid, version: '2.1.0', gitBranch: 'main', uuid, timestamp: new Date(t0 + lines.length * 1000).toISOString(), ...o });
    lines.push(line);
    bytes += line.length + 1;
    parent = uuid;
  };
  const body = 'export function line(n) { return n * 2; } // a line of source code that is read back\n'.repeat(60);
  for (let t = 0; bytes < mb * 1e6; t++) {
    rec({ type: 'user', message: { role: 'user', content: `Question ${t}: what does src/file-${t}.ts do?` } });
    rec({ type: 'assistant', message: { id: `msg_${t}a`, type: 'message', role: 'assistant', model: 'claude-test', content: [{ type: 'text', text: 'Let me read it.' }, { type: 'tool_use', id: `toolu_${t}`, name: 'Read', input: { file_path: `${cwd}/src/file-${t}.ts` } }], stop_reason: 'tool_use', usage: { input_tokens: 100, output_tokens: 20 } } });
    rec({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: `toolu_${t}`, content: body }] }, toolUseResult: { type: 'text', file: { filePath: `${cwd}/src/file-${t}.ts`, content: body, numLines: 60 } } });
    rec({ type: 'assistant', message: { id: `msg_${t}b`, type: 'message', role: 'assistant', model: 'claude-test', content: [{ type: 'text', text: `It doubles a number. `.repeat(10) }], stop_reason: 'end_turn', usage: { input_tokens: 100, output_tokens: 200 } } });
  }
  fs.writeFileSync(path.join(dir, `${sid}.jsonl`), lines.join('\n') + '\n');
  return { sid, records: lines.length };
}

describe('conversations on disk', () => {
  const cwd = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-long-')));
  let long: { sid: string; records: number };
  beforeAll(() => {
    long = writeTranscript(cwd, TRANSCRIPT_MB);
    for (let i = 0; i < SESSIONS; i++) writeTranscript(cwd, 0.05);
  });

  it('sessions.list stays within its budget', async () => {
    await rpc('sessions.refresh'); // cold: the SDK reads every transcript's head again
    const t0 = performance.now();
    const res = await rpc('sessions.refresh');
    const ms = performance.now() - t0;
    console.log(`sessions.list over ${SESSIONS + 1} conversations: ${ms.toFixed(0)} ms`);
    expect(res.r.filter((s: any) => s.cwd === cwd)).toHaveLength(SESSIONS + 1);
    expect(ms).toBeLessThan(LIST_MS);
  });

  it('session.open of a long conversation stays within its budget', { timeout: 20_000 }, async () => {
    let bytes = 0;
    const onMessage = (raw: Buffer) => (bytes = Math.max(bytes, raw.length));
    ws.on('message', onMessage);
    const t0 = performance.now();
    const res = await rpc('session.open', { id: long.sid });
    const ms = performance.now() - t0;
    ws.off('message', onMessage);
    console.log(`session.open of ${TRANSCRIPT_MB} MB (${res.r.messages.length} messages): ${ms.toFixed(0)} ms, an answer of ${(bytes / 1e6).toFixed(1)} MB`);
    expect(res.r.messages).toHaveLength(long.records);
    expect(res.r.messages.filter((m: any) => m.tool_use_result)).toHaveLength(long.records / 4);
    expect(ms).toBeLessThan(OPEN_MS);
    expect(bytes / 1e6).toBeLessThan(OPEN_MB);
  });
});
