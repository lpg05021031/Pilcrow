// Git integration: repo info, unified diffs for the review panel, commits and worktrees.
// One convention: reads never throw (a folder that is not a repository reads as { isRepo: false } or
// empty), and actions answer { ok, output } with git's own words, for the person to read.
import fs from 'node:fs';
import path from 'node:path';
import { run, looksBinary } from './util.js';

const GIT = ['-c', 'core.quotePath=false', '-c', 'color.ui=false'];
const MAX_UNTRACKED = 200; // untracked files shown in a diff, or counted for the header's line totals
const COUNT_FILE_BYTES = 1024 * 1024; // larger untracked files are not counted
const COUNT_BUDGET_BYTES = 8 * 1024 * 1024; // read per info(), which the header asks for every few seconds
const MAX_FILE_BYTES = 768 * 1024;
const MAX_LINES_PER_FILE = 4000;

export function git(cwd, args, opts = {}) {
  return run('git', [...GIT, ...args], { cwd, timeout: 30_000, ...opts });
}

/** Lines in a file's bytes, the way a diff counts them (a last line without a newline counts). */
function lineCount(buf) {
  let n = 0;
  for (let i = buf.indexOf(10); i !== -1; i = buf.indexOf(10, i + 1)) n++;
  return n + (buf.length && buf[buf.length - 1] !== 10 ? 1 : 0);
}

/**
 * Where the repository is and what state it is in, from two git processes run together: one
 * `status --porcelain=v2 --branch` (branch, upstream, counts and the untracked files) and one
 * `rev-parse` for the three folders. info() and diff() both start here.
 */
async function status(cwd) {
  if (!cwd) return { info: { isRepo: false }, untracked: [] }; // (a folder that is gone fails git's spawn)
  const [st, rp] = await Promise.all([git(cwd, ['status', '--porcelain=v2', '--branch', '-z', '--untracked-files=all']), git(cwd, ['rev-parse', '--show-toplevel', '--git-common-dir', '--git-dir'])]);
  if (st.code !== 0 || rp.code !== 0) return { info: { isRepo: false }, untracked: [] };
  const [root, common, gitDir] = rp.stdout.trim().split('\n');
  const info = { isRepo: true, root, branch: null, head: null, upstream: null, ahead: 0, behind: 0, changed: 0, untracked: 0 };
  info.isWorktree = path.resolve(cwd, common) !== path.resolve(cwd, gitDir);
  info.mainRoot = info.isWorktree ? path.dirname(path.resolve(cwd, common)) : root;
  const untracked = [];
  for (const rec of st.stdout.split('\0')) {
    if (rec.startsWith('# branch.head ')) info.branch = rec.slice(14);
    else if (rec.startsWith('# branch.oid ')) info.head = rec.slice(13, 13 + 12);
    else if (rec.startsWith('# branch.upstream ')) info.upstream = rec.slice(18);
    else if (rec.startsWith('# branch.ab ')) {
      const m = rec.match(/\+(\d+) -(\d+)/);
      if (m) [info.ahead, info.behind] = [Number(m[1]), Number(m[2])];
    } else if (rec.startsWith('? ')) {
      info.untracked++;
      if (untracked.length < MAX_UNTRACKED) untracked.push(rec.slice(2)); // relative to the root
    } else if (/^[12u] /.test(rec)) info.changed++;
  }
  if (info.branch === '(detached)') info.branch = null;
  return { info, untracked };
}

export async function info(cwd) {
  const { info: out, untracked } = await status(cwd);
  if (!out.isRepo) return out;
  // Line totals for the header's "+18 −2" (tracked files against HEAD; new files count as added lines).
  out.insertions = 0;
  out.deletions = 0;
  if (out.changed) {
    const ns = await git(cwd, ['diff', '--numstat', 'HEAD']);
    for (const line of ns.stdout.split('\n')) {
      const m = line.match(/^(\d+)\t(\d+)\t/);
      if (m) {
        out.insertions += Number(m[1]);
        out.deletions += Number(m[2]);
      }
    }
  }
  let budget = COUNT_BUDGET_BYTES;
  for (const rel of untracked) {
    if (budget <= 0) break;
    try {
      const file = path.join(out.root, rel);
      if ((await fs.promises.stat(file)).size >= COUNT_FILE_BYTES) continue;
      const buf = await fs.promises.readFile(file);
      budget -= buf.length;
      out.insertions += lineCount(buf);
    } catch {
      /* unreadable or gone: skip */
    }
  }
  return out;
}

export async function branches(cwd) {
  const r = await git(cwd, ['for-each-ref', '--sort=-committerdate', '--format=%(refname:short)\t%(committerdate:relative)', 'refs/heads']);
  const cur = (await git(cwd, ['rev-parse', '--abbrev-ref', 'HEAD'])).stdout.trim();
  return {
    current: cur === 'HEAD' ? null : cur,
    list: r.stdout
      .split('\n')
      .filter(Boolean)
      .map((l) => {
        const [name, when] = l.split('\t');
        return { name, when };
      }),
  };
}

/**
 * @typedef {{ t: '+' | '-' | ' ', s: string, o?: number, n?: number, eof?: boolean }} DiffLine
 * @typedef {{ oldStart: number, oldLines: number, newStart: number, newLines: number, header: string, lines: DiffLine[] }} Hunk
 * @typedef {{ path: string | null, oldPath: string | null, status: string, binary: boolean, additions: number, deletions: number, hunks: Hunk[], truncated: boolean, untracked?: boolean }} DiffFile
 */

/**
 * Parse `git diff` unified output into files and hunks with line numbers.
 * @param {string} text
 * @returns {DiffFile[]}
 */
export function parseUnifiedDiff(text) {
  /** @type {DiffFile[]} */
  const files = [];
  /** @type {DiffFile | null} */
  let file = null;
  /** @type {Hunk | null} */
  let hunk = null;
  let inHeader = false;
  let remOld = 0;
  let remNew = 0;
  let oldNo = 0;
  let newNo = 0;
  const stripPrefix = (p) => (p === '/dev/null' ? null : p.replace(/^[ab]\//, ''));
  const startHunk = (line) => {
    const m = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/);
    if (!m) return false;
    hunk = { oldStart: +m[1], oldLines: m[2] === undefined ? 1 : +m[2], newStart: +m[3], newLines: m[4] === undefined ? 1 : +m[4], header: m[5] || '', lines: [] };
    [oldNo, newNo, remOld, remNew] = [hunk.oldStart, hunk.newStart, hunk.oldLines, hunk.newLines];
    if (file.additions + file.deletions > MAX_LINES_PER_FILE) file.truncated = true;
    else file.hunks.push(hunk);
    return true;
  };
  for (const line of text.split('\n')) {
    if (remOld <= 0 && remNew <= 0 && line.startsWith('diff --git ')) {
      file = { path: null, oldPath: null, status: 'M', binary: false, additions: 0, deletions: 0, hunks: [], truncated: false };
      files.push(file);
      hunk = null;
      inHeader = true;
      const m = line.match(/^diff --git a\/(.*) b\/(.*)$/);
      if (m) [file.oldPath, file.path] = [m[1], m[2]];
      continue;
    }
    if (!file) continue;
    if (inHeader) {
      if (line.startsWith('@@')) {
        inHeader = false;
        startHunk(line);
      } else if (line.startsWith('new file mode')) file.status = 'A';
      else if (line.startsWith('deleted file mode')) file.status = 'D';
      else if (line.startsWith('rename from ')) [file.status, file.oldPath] = ['R', line.slice(12)];
      else if (line.startsWith('rename to ')) file.path = line.slice(10);
      else if (line.startsWith('Binary files ') || line.startsWith('GIT binary patch')) file.binary = true;
      else if (line.startsWith('--- ')) file.oldPath = stripPrefix(line.slice(4)) ?? file.oldPath;
      else if (line.startsWith('+++ ')) {
        const p = stripPrefix(line.slice(4));
        if (p) file.path = p;
      }
      continue;
    }
    if (remOld > 0 || remNew > 0) {
      const c = line[0];
      const rec = file.truncated ? null : hunk;
      if (c === '+') {
        rec?.lines.push({ t: '+', s: line.slice(1), n: newNo });
        newNo++;
        remNew--;
        file.additions++;
      } else if (c === '-') {
        rec?.lines.push({ t: '-', s: line.slice(1), o: oldNo });
        oldNo++;
        remOld--;
        file.deletions++;
      } else if (c === '\\') {
        const last = rec?.lines[rec.lines.length - 1];
        if (last) last.eof = true;
      } else {
        rec?.lines.push({ t: ' ', s: line.slice(1), o: oldNo, n: newNo });
        oldNo++;
        newNo++;
        remOld--;
        remNew--;
      }
      continue;
    }
    if (line.startsWith('@@')) startHunk(line);
    else if (line.startsWith('\\') && hunk) {
      const last = hunk.lines[hunk.lines.length - 1];
      if (last) last.eof = true;
    }
  }
  for (const f of files) {
    if (!f.path) f.path = f.oldPath;
    if (f.status === 'R' && f.oldPath === f.path) f.status = 'M';
  }
  return files;
}

async function untrackedAsDiff(root, rel) {
  const full = path.join(root, rel);
  const f = { path: rel, oldPath: null, status: 'A', binary: false, additions: 0, deletions: 0, hunks: [], truncated: false, untracked: true };
  const st = await fs.promises.stat(full).catch(() => null);
  if (!st?.isFile()) return null;
  if (st.size > MAX_FILE_BYTES) {
    f.truncated = true;
    return f;
  }
  const buf = await fs.promises.readFile(full).catch(() => null);
  if (!buf) return null;
  if (looksBinary(buf)) {
    f.binary = true;
    return f;
  }
  const lines = buf.toString('utf8').split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  f.additions = lines.length;
  if (lines.length > MAX_LINES_PER_FILE) {
    f.truncated = true;
    return f;
  }
  f.hunks.push({ oldStart: 0, oldLines: 0, newStart: 1, newLines: lines.length, header: '', lines: lines.map((s, i) => ({ t: '+', s, n: i + 1 })) });
  return f;
}

/**
 * Working tree vs `base` (default HEAD), including untracked files. Two or three git processes besides
 * status(): the merge base when `base` is a branch, and the diff itself.
 * @param {string} cwd @param {{ base?: string }} [opts]
 */
export async function diff(cwd, { base } = {}) {
  const { info: i, untracked } = await status(cwd);
  if (!i.isRepo) return { isRepo: false, files: [] };
  let baseRef = base || 'HEAD';
  const baseLabel = base || 'HEAD';
  if (base && base !== 'HEAD') {
    const mb = await git(cwd, ['merge-base', base, 'HEAD']);
    if (mb.code === 0) baseRef = mb.stdout.trim();
  }
  const hasHead = i.head !== '(initial)'; // a repository without a commit yet
  const args = hasHead ? ['diff', '--no-ext-diff', '--find-renames', '-U3', baseRef, '--'] : ['diff', '--no-ext-diff', '--cached', '-U3', '--'];
  const r = await git(i.root, args);
  const files = parseUnifiedDiff(r.stdout);
  for (const f of await Promise.all(untracked.map((rel) => untrackedAsDiff(i.root, rel)))) if (f) files.push(f);
  files.sort((a, b) => a.path.localeCompare(b.path));
  return { isRepo: true, root: i.root, base: baseLabel, baseRef, branch: i.branch, files, error: r.code ? r.stderr : null };
}

/** Commit everything (or `paths`) in the repository `cwd` is in. @param {string} cwd @param {{ message: string, paths?: string[] }} opts */
export async function commit(cwd, { message, paths }) {
  const { info: i } = await status(cwd);
  if (!i.isRepo) return { ok: false, output: 'Not a git repository' };
  const add = await git(i.root, ['add', '-A', '--', ...(paths?.length ? paths : ['.'])]);
  if (add.code) return { ok: false, output: add.stderr || add.stdout };
  const c = await git(i.root, ['commit', '-m', message]);
  return { ok: c.code === 0, output: (c.stdout + c.stderr).trim() };
}

export async function discard(cwd, { path: rel, untracked }) {
  const { info: i } = await status(cwd);
  if (!i.isRepo) return { ok: false, output: 'Not a git repository' };
  const full = path.resolve(i.root, rel);
  if (!full.startsWith(i.root + path.sep)) return { ok: false, output: 'Path is outside the repository' };
  if (untracked) {
    fs.rmSync(full, { force: true, recursive: true });
    return { ok: true, output: '' };
  }
  let r = await git(i.root, ['restore', '--source=HEAD', '--staged', '--worktree', '--', rel]);
  if (r.code) r = await git(i.root, ['checkout', 'HEAD', '--', rel]);
  return { ok: r.code === 0, output: (r.stdout + r.stderr).trim() };
}

export async function listWorktrees(cwd) {
  const r = await git(cwd, ['worktree', 'list', '--porcelain']);
  const out = [];
  let cur = null;
  for (const line of r.stdout.split('\n')) {
    if (line.startsWith('worktree ')) {
      cur = { path: line.slice(9), branch: null, head: null, detached: false };
      out.push(cur);
    } else if (cur && line.startsWith('branch ')) cur.branch = line.slice(7).replace('refs/heads/', '');
    else if (cur && line.startsWith('HEAD ')) cur.head = line.slice(5, 17);
    else if (cur && line === 'detached') cur.detached = true;
  }
  return out;
}

/** A new worktree on a new branch; `worktree` is where it went (the names get -2, -3… when taken). */
export async function createWorktree({ repo, base, branch, dir }) {
  const { info: i } = await status(repo);
  if (!i.isRepo) return { ok: false, output: 'Not a git repository: ' + repo };
  const root = i.mainRoot || i.root;
  let name = branch;
  for (let n = 2; (await git(root, ['rev-parse', '--verify', '-q', `refs/heads/${name}`])).code === 0; n++) name = `${branch}-${n}`;
  let target = dir;
  for (let n = 2; fs.existsSync(target); n++) target = `${dir}-${n}`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const args = ['worktree', 'add', '-b', name, target];
  if (base) args.push(base);
  const r = await git(root, args);
  if (r.code) return { ok: false, output: (r.stderr || r.stdout).trim() || 'git worktree add failed' };
  return { ok: true, output: '', worktree: { path: target, branch: name, base: base || i.branch || 'HEAD', repoRoot: root } };
}

export async function removeWorktree({ repoRoot, dir, branch, deleteBranch = false }) {
  const r = await git(repoRoot, ['worktree', 'remove', '--force', dir]);
  if (r.code && fs.existsSync(dir)) return { ok: false, output: (r.stderr || r.stdout).trim() };
  await git(repoRoot, ['worktree', 'prune']);
  if (deleteBranch && branch) await git(repoRoot, ['branch', '-D', branch]);
  return { ok: true, output: '' };
}

export async function log(cwd, n = 20) {
  const r = await git(cwd, ['log', `-${n}`, '--format=%h%x09%an%x09%ar%x09%s']);
  return r.stdout
    .split('\n')
    .filter(Boolean)
    .map((l) => {
      const [hash, author, when, subject] = l.split('\t');
      return { hash, author, when, subject };
    });
}
