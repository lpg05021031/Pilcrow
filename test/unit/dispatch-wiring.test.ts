// dispatch.js, the wiring of the state machine to the real server: the user's shell for a run's prepare
// and verify commands, the job file in Pilcrow's state folder written when Dispatch stops, its events
// as the server listens to them, and a job's session made through sessions.js.
import { afterAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as dispatch from '../../server/dispatch.js';
import { sessions } from '../../server/sessions.js';
import { store } from '../../server/config.js';

const FILE = path.join(process.env.XDG_STATE_HOME!, 'pilcrow', 'dispatch.json');
dispatch.start();
afterAll(() => {
  dispatch.stop();
  sessions.stop();
});

describe('the shell', () => {
  it('runs a command in a folder and answers its exit code and its output, stderr after stdout', async () => {
    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-shell-')));
    expect(await dispatch.shell('pwd; echo out; echo err >&2; exit 3', dir)).toEqual({ code: 3, output: `${dir}\nout\n\nerr\n` });
    expect(await dispatch.shell('true', dir)).toEqual({ code: 0, output: '' });
  });
});

describe('the dispatcher', () => {
  it('saves its jobs to the state folder when it stops, and says what changed', async () => {
    const changed: string[] = [];
    dispatch.events.on('changed', (d: { id: string }) => changed.push(d.id));
    const job = dispatch.dispatch.save({ name: 'Wired', brief: 'b', targets: [{ path: '/tmp/a', name: 'a' }] });
    await new Promise((r) => setTimeout(r, 50));
    expect(changed).toContain(job.id);
    dispatch.stop();
    expect(JSON.parse(fs.readFileSync(FILE, 'utf8')).jobs.map((j: { id: string }) => j.id)).toContain(job.id);
    dispatch.start();
  });

  it('starts a run as a real session in a worktree of its own, titled after the job', async () => {
    const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-wired-')));
    execFileSync('git', ['init', '-q', '-b', 'main', repo]);
    fs.writeFileSync(path.join(repo, 'a.txt'), 'a\n');
    execFileSync('git', ['add', '.'], { cwd: repo });
    execFileSync('git', ['commit', '-qm', 'first'], { cwd: repo });
    store.setConfig({ claudeBinary: '/nonexistent/claude' }); // the session is made; Claude Code never starts
    const job = dispatch.dispatch.save({ name: 'Wired run', brief: 'Do it in {{repo.name}}', targets: [{ path: repo, name: 'repo' }], settings: { canary: 0 } });
    await dispatch.dispatch.start(job.id);
    let run: { sessionId: string | null; state: string } | undefined;
    for (let i = 0; i < 100 && !(run = dispatch.dispatch.get(job.id).runs[0])?.sessionId; i++) await new Promise((r) => setTimeout(r, 20));
    const meta = store.getMeta(run!.sessionId!);
    expect(meta).toMatchObject({ title: 'Wired run · repo', dispatch: { jobId: job.id, target: 'repo' }, worktree: { branch: 'pilcrow/wired-run' } });
    expect(fs.existsSync(meta!.worktree.path)).toBe(true);
    await dispatch.dispatch.stop(job.id);
  });
});
