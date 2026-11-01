/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// snapshots.js on a real folder and a real shadow repository: a step per change, an index.lock that a
// dead git left behind taken away, a folder switched off (a lock still held, indexing too slow) tried
// again after the cool-down instead of for ever, quitting waiting for the step under way, and a removed
// session's steps, ref and objects reclaimed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// git's own processes, with a way to make `git add` look slow (the clock moves on while it runs) or hang.
const hooks = vi.hoisted(() => ({ addTakesMs: 0, gate: null as Promise<void> | null }));
vi.mock('../../server/util.js', async (orig) => {
  const util = await orig<any>();
  return {
    ...util,
    run: async (cmd: string, args: string[], opts: any) => {
      if (args.includes('add')) await hooks.gate;
      const r = await util.run(cmd, args, opts);
      if (hooks.addTakesMs && args.includes('add')) vi.setSystemTime(Date.now() + hooks.addTakesMs);
      return r;
    },
  };
});

const snapshots = await import('../../server/snapshots.js');
const { xdg, folderKey } = await import('../../server/util.js');

const RETRY_AFTER_MS = 10 * 60e3; // as snapshots.js
let session: { id: string; cwd: string };
const shadowOf = (cwd: string) => path.join(xdg('data'), 'timeline', folderKey(cwd));
const lockOf = (cwd: string) => path.join(shadowOf(cwd), 'index.lock');
const edit = (text: string) => fs.writeFileSync(path.join(session.cwd, 'a.txt'), text);
const tool = () => snapshots.record(session, { kind: 'tool', tool: 'Edit', label: 'a.txt' });

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] }); // timers stay real: git runs for real
  const cwd = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-timeline-')), 'project');
  fs.mkdirSync(cwd);
  session = { id: `s-${path.basename(path.dirname(cwd))}`, cwd };
  edit('one\n');
  expect(await snapshots.record(session, { kind: 'start', label: 'Session started' })).toMatchObject({ n: 1 });
});
afterEach(() => {
  hooks.addTakesMs = 0;
  vi.useRealTimers();
});

describe('the timeline of a folder', () => {
  it('takes a step for a change, with what it changed', async () => {
    edit('one\ntwo\n');
    expect(await tool()).toMatchObject({ n: 2, kind: 'tool', changed: true, add: 1, del: 0, files: [{ path: 'a.txt', status: 'M' }] });
    expect(snapshots.steps(session)).toMatchObject({ enabled: true, reason: null });
  });

  it('takes away an index.lock a dead git left behind, and takes the step', async () => {
    fs.writeFileSync(lockOf(session.cwd), '');
    const old = new Date(Date.now() - 60_000);
    fs.utimesSync(lockOf(session.cwd), old, old);
    edit('one\ntwo\n');
    expect(await tool()).toMatchObject({ n: 2, changed: true });
    expect(fs.existsSync(lockOf(session.cwd))).toBe(false);
    expect(snapshots.steps(session).enabled).toBe(true);
  });

  it('is off while a fresh lock is held, and on again after the cool-down', async () => {
    fs.writeFileSync(lockOf(session.cwd), '');
    edit('one\ntwo\n');
    expect(await tool()).toBe(null);
    expect(snapshots.steps(session)).toMatchObject({ enabled: false, reason: expect.stringMatching(/index\.lock/) });
    fs.rmSync(lockOf(session.cwd)); // whoever held it is done
    expect(await tool()).toBe(null); // still cooling down: nothing is tried
    vi.setSystemTime(Date.now() + RETRY_AFTER_MS);
    expect(snapshots.steps(session).enabled).toBe(true);
    expect(await tool()).toMatchObject({ n: 2, changed: true });
  });

  it('is off for a folder that takes too long to index, and tried again after the cool-down', async () => {
    hooks.addTakesMs = 13_000;
    edit('one\ntwo\n');
    expect(await tool()).toBe(null);
    expect(snapshots.steps(session)).toMatchObject({ enabled: false, reason: expect.stringMatching(/takes too long \(13s\)/) });
    hooks.addTakesMs = 0;
    vi.setSystemTime(Date.now() + RETRY_AFTER_MS);
    expect(await tool()).toMatchObject({ n: 2, changed: true });
  });
});

describe('quitting', () => {
  it('waits for the step under way', async () => {
    edit('one\ntwo\n');
    let taken: any = undefined;
    void tool().then((s) => (taken = s));
    await snapshots.drain();
    expect(taken).toMatchObject({ n: 2 });
  });

  it('waits no longer than its bound for a step that hangs', async () => {
    let release = () => {};
    hooks.gate = new Promise((r) => (release = r));
    edit('one\ntwo\n');
    const step = tool();
    const t0 = performance.now();
    await snapshots.drain(50);
    expect(performance.now() - t0).toBeLessThan(1000);
    hooks.gate = null;
    release();
    expect(await step).toMatchObject({ n: 2 });
  });
});

describe('forgetting a session', () => {
  const git = (...args: string[]) => execFileSync('git', ['--git-dir', shadowOf(session.cwd), ...args], { encoding: 'utf8' }).trim();
  const blob = (text: string) => execFileSync('git', ['hash-object', '--stdin'], { input: text, encoding: 'utf8' }).trim();
  const has = (sha: string) => git('cat-file', '--batch-check', '--batch-all-objects').includes(sha);

  it('takes its steps, its ref and the objects only it reached; the last session leaves the shadow repository empty', async () => {
    edit('only this session\n');
    await tool();
    const other = { id: 'other', cwd: session.cwd };
    edit('the other session\n');
    await snapshots.record(other, { kind: 'start', label: 'Session started' });
    const mine = blob('only this session\n');
    expect(has(mine)).toBe(true);
    expect((await snapshots.folderSteps(session.cwd)).map((s: any) => s.sessionId)).toContain(session.id);
    expect(git('for-each-ref', '--format=%(refname)')).toBe(`refs/sessions/other\nrefs/sessions/${session.id}`);

    await snapshots.forget(session.cwd, session.id);
    expect(fs.existsSync(path.join(shadowOf(session.cwd), `steps-${session.id}.json`))).toBe(false);
    expect(git('for-each-ref', '--format=%(refname)')).toBe('refs/sessions/other');
    expect(has(mine)).toBe(false);
    expect(git('count-objects', '-v')).toMatch(/^count: 0$/m);
    expect((await snapshots.folderSteps(session.cwd)).map((s: any) => s.sessionId)).not.toContain(session.id);
    expect(has(blob('the other session\n'))).toBe(true);

    await snapshots.forget(session.cwd, 'other');
    expect(git('for-each-ref', '--format=%(refname)')).toBe('');
    expect(git('count-objects', '-v')).toMatch(/^count: 0$/m);
    expect(fs.readdirSync(shadowOf(session.cwd)).filter((f) => f.startsWith('steps-'))).toEqual([]);
    edit('again\n');
    expect(await snapshots.record(other, { kind: 'start', label: 'Session started' })).toMatchObject({ n: 1 });
  });
});
