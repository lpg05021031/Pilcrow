/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// git.js: the unified-diff parser, and info()/diff()/discard() on a real repository in a temp folder.
import { beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as git from '../../server/git.js';

const DIFF = `diff --git a/src/a.ts b/src/a.ts
index 1111111..2222222 100644
--- a/src/a.ts
+++ b/src/a.ts
@@ -1,3 +1,4 @@ export function a() {
 one
-two
+two!
+two and a half
 three
@@ -10 +11 @@
-last
\\ No newline at end of file
+last
diff --git a/old name.txt b/new name.txt
similarity 90%
rename from old name.txt
rename to new name.txt
--- a/old name.txt
+++ b/new name.txt
@@ -1 +1 @@
-x
+y
diff --git a/img.png b/img.png
new file mode 100644
index 0000000..3333333
Binary files /dev/null and b/img.png differ
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
--- a/gone.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
`;

describe('parseUnifiedDiff', () => {
  const files = git.parseUnifiedDiff(DIFF);

  it('reads every file with its status', () => {
    expect(files.map((f) => [f.path, f.status, f.oldPath])).toEqual([
      ['src/a.ts', 'M', 'src/a.ts'],
      ['new name.txt', 'R', 'old name.txt'],
      ['img.png', 'A', 'img.png'],
      ['gone.txt', 'D', 'gone.txt'],
    ]);
    expect(files[2].binary).toBe(true);
  });

  it('numbers the lines of each hunk on both sides and marks a missing newline', () => {
    const [a] = files;
    expect([a.additions, a.deletions]).toEqual([3, 2]);
    expect(a.hunks[0]).toMatchObject({ oldStart: 1, oldLines: 3, newStart: 1, newLines: 4, header: 'export function a() {' });
    expect(a.hunks[0].lines).toEqual([
      { t: ' ', s: 'one', o: 1, n: 1 },
      { t: '-', s: 'two', o: 2 },
      { t: '+', s: 'two!', n: 2 },
      { t: '+', s: 'two and a half', n: 3 },
      { t: ' ', s: 'three', o: 3, n: 4 },
    ]);
    expect(a.hunks[1].lines).toEqual([
      { t: '-', s: 'last', o: 10, eof: true },
      { t: '+', s: 'last', n: 11 },
    ]);
  });

  it('counts a deleted file and keeps a line that looks like a header inside a hunk', () => {
    expect(files[3]).toMatchObject({ additions: 0, deletions: 2 });
    const tricky = git.parseUnifiedDiff('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-diff --git a/y b/y\n+same\n ctx\n');
    expect(tricky).toHaveLength(1);
    expect(tricky[0].hunks[0].lines.map((l) => l.s)).toEqual(['diff --git a/y b/y', 'same', 'ctx']);
  });
});

describe('on a real repository', () => {
  let repo: string;
  const g = (...args: string[]) => execFileSync('git', args, { cwd: repo, stdio: 'pipe' }).toString();

  beforeAll(() => {
    repo = path.join(fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-git-'))), 'repo');
    fs.mkdirSync(repo);
    g('init', '-q', '-b', 'main');
    fs.mkdirSync(path.join(repo, 'src'));
    fs.writeFileSync(path.join(repo, 'src', 'a.txt'), 'one\ntwo\n');
    g('add', '.');
    g('commit', '-qm', 'first');
    fs.writeFileSync(path.join(repo, 'src', 'a.txt'), 'one\n2\nthree\n');
    fs.mkdirSync(path.join(repo, 'new'));
    fs.writeFileSync(path.join(repo, 'new', 'b.txt'), 'b1\nb2\nb3'); // three lines, no newline at the end
    fs.writeFileSync(path.join(repo, 'new', 'c.bin'), Buffer.from([1, 0, 2]));
  });

  it('info: branch, counts and line totals, untracked files counted from the root even in a subfolder', async () => {
    const i: any = await git.info(path.join(repo, 'src'));
    expect(i).toMatchObject({ isRepo: true, root: repo, branch: 'main', changed: 1, untracked: 2, isWorktree: false, mainRoot: repo });
    expect([i.insertions, i.deletions]).toEqual([2 + 3 + 1, 1]);
  });

  it('diff: tracked hunks and untracked files, binary ones marked', async () => {
    const d: any = await git.diff(repo);
    expect(d.files.map((f: any) => [f.path, f.status, f.binary, f.additions])).toEqual([
      ['new/b.txt', 'A', false, 3],
      ['new/c.bin', 'A', true, 0],
      ['src/a.txt', 'M', false, 2],
    ]);
    expect(d.files[0].untracked).toBe(true);
  });

  it('reads that are not a repository answer isRepo: false, and actions answer { ok, output }', async () => {
    const plain = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-plain-'));
    expect(await git.info(plain)).toEqual({ isRepo: false });
    expect(await git.info(path.join(plain, 'gone'))).toEqual({ isRepo: false });
    expect(await git.diff(plain)).toEqual({ isRepo: false, files: [] });
    expect(await git.createWorktree({ repo: plain, branch: 'x', dir: path.join(plain, 'wt') })).toEqual({ ok: false, output: `Not a git repository: ${plain}` });
  });

  it('discard refuses a path outside the repository', async () => {
    const outside = path.join(path.dirname(repo), 'keep-me.txt');
    fs.writeFileSync(outside, 'x');
    expect(await git.discard(repo, { path: '../keep-me.txt', untracked: true })).toEqual({ ok: false, output: 'Path is outside the repository' });
    expect(fs.existsSync(outside)).toBe(true);
    expect(await git.discard(repo, { path: 'new/c.bin', untracked: true })).toEqual({ ok: true, output: '' });
    expect(fs.existsSync(path.join(repo, 'new', 'c.bin'))).toBe(false);
  });
});
