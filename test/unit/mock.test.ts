/* eslint-disable @typescript-eslint/no-explicit-any -- the server and the mock are plain JavaScript */
// The browser tests run against test/mock/mock-server.mjs, so its answers must have the shapes the real
// server's have where the clients read them: the real functions on a temporary repository and the mock's
// handlers, side by side, key for key, and the same error code for the same kind of failure.
import { beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as git from '../../server/git.js';
import * as files from '../../server/files.js';
import { errorOf } from '../../server/main.js';
import { sessions } from '../../server/sessions.js';
import { methods as mock } from '../../test/mock/mock-server.mjs';

/** Method -> the keys of its answer, and of the first item of a list in it. */
const SHAPES: Record<string, { keys: string[]; each?: [string, string[]] }> = {
  'git.info': { keys: ['isRepo', 'root', 'branch', 'head', 'upstream', 'ahead', 'behind', 'changed', 'untracked', 'isWorktree', 'mainRoot', 'insertions', 'deletions'] },
  'git.branches': { keys: ['current', 'list'], each: ['list', ['name', 'when']] },
  'git.diff': { keys: ['isRepo', 'root', 'base', 'baseRef', 'branch', 'files', 'truncated', 'error'], each: ['files', ['path', 'oldPath', 'status', 'binary', 'additions', 'deletions', 'hunks', 'truncated']] },
  'fs.browse': { keys: ['dir', 'display', 'parent', 'repo', 'dirs', 'home'] },
};

/** The keys a window sees (what JSON leaves of the answer), sorted. */
const keysOf = (v: any) => Object.keys(JSON.parse(JSON.stringify(v))).sort();

let repo: string;
beforeAll(() => {
  repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-mockshape-')));
  const g = (...args: string[]) => execFileSync('git', args, { cwd: repo, stdio: 'pipe' });
  g('init', '-q', '-b', 'main');
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
  g('add', '.');
  g('commit', '-qm', 'first');
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\ntwo\n');
  fs.mkdirSync(path.join(repo, 'sub'));
});

describe('the mock answers in the real server’s shapes', () => {
  const real: Record<string, () => Promise<any>> = {
    'git.info': () => git.info(repo),
    'git.branches': () => git.branches(repo),
    'git.diff': () => git.diff(repo),
    'fs.browse': () => files.browse(repo),
  };
  for (const [m, shape] of Object.entries(SHAPES)) {
    it(m, async () => {
      const r = await real[m]();
      const k = await mock[m]({ cwd: repo, dir: repo });
      expect(keysOf(r), 'the server').toEqual([...shape.keys].sort());
      expect(keysOf(k), 'the mock').toEqual([...shape.keys].sort());
      if (shape.each) {
        const [field, keys] = shape.each;
        expect(keysOf(r[field][0]), `the server's ${field}`).toEqual(expect.arrayContaining(keys));
        expect(keysOf(k[field][0]), `the mock's ${field}`).toEqual(expect.arrayContaining(keys));
      }
    });
  }

  it('says not_found for a session that does not exist, as the server does', async () => {
    const realErr = await sessions.get('no-such-session').catch((e: any) => e);
    let mockErr: any;
    try {
      await mock['session.open']({ id: 'no-such-session' });
    } catch (e) {
      mockErr = e;
    }
    expect(errorOf(mockErr)).toEqual(errorOf(realErr));
    expect(errorOf(realErr).code).toBe('not_found');
  });
});
