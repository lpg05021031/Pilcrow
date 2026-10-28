// Git integration: repo info, unified diffs for the review panel, commits and worktrees.
import fs from 'node:fs';
import path from 'node:path';
import { run } from './util.js';

const GIT = ['-c', 'core.quotePath=false', '-c', 'color.ui=false'];
const MAX_UNTRACKED = 200;
const MAX_FILE_BYTES = 768 * 1024;
const MAX_LINES_PER_FILE = 4000;

export function git(cwd, args, opts = {}) {
  return run('git', [...GIT, ...args], { cwd, timeout: 30_000, ...opts });
}

export async function info(cwd) {
  if (!cwd || !fs.existsSync(cwd)) return { isRepo: false };
  const r = await git(cwd, ['status', '--porcelain=v2', '--branch', '-z', '--untracked-files=normal']);
  if (r.code !== 0) return { isRepo: false };
  const root = (await git(cwd, ['rev-parse', '--show-toplevel'])).stdout.trim();
  const common = (await git(cwd, ['rev-parse', '--git-common-dir'])).stdout.trim();
  const gitDir = (await git(cwd, ['rev-parse', '--git-dir'])).stdout.trim();
  const out = { isRepo: true, root, branch: null, head: null, upstream: null, ahead: 0, behind: 0, changed: 0, untracked: 0 };
  out.isWorktree = path.resolve(cwd, common) !== path.resolve(cwd, gitDir);
  out.mainRoot = out.isWorktree ? path.dirname(path.resolve(cwd, common)) : root;
  for (const rec of r.stdout.split('\0')) {
    if (rec.startsWith('# branch.head ')) out.branch = rec.slice(14);
    else if (rec.startsWith('# branch.oid ')) out.head = rec.slice(13, 13 + 12);
    else if (rec.startsWith('# branch.upstream ')) out.upstream = rec.slice(18);
    else if (rec.startsWith('# branch.ab ')) {
      const m = rec.match(/\+(\d+) -(\d+)/);
      if (m) [out.ahead, out.behind] = [Number(m[1]), Number(m[2])];
    } else if (rec.startsWith('? ')) out.untracked++;
    else if (/^[12u] /.test(rec)) out.changed++;
  }
  if (out.branch === '(detached)') out.branch = null;
  // Line totals for the header's "+18 −2" (tracked files against HEAD; new files count as added lines).
  out.insertions = 0;
  out.deletions = 0;
  if (out.changed || out.untracked) {
    const ns = await git(cwd, ['diff', '--numstat', 'HEAD']);
    for (const line of ns.stdout.split('\n')) {
      const m = line.match(/^(\d+)\t(\d+)\t/);
      if (m) {
        out.insertions += Number(m[1]);
        out.deletions += Number(m[2]);
      }
    }
    if (out.untracked && out.untracked < 200) {
      const u = await git(cwd, ['ls-files', '--others', '--exclude-standard', '-z']);
      for (const f of u.stdout.split('\0').filter(Boolean).slice(0, 200)) {
        try {
          const st = fs.statSync(path.join(cwd, f));
          if (st.isFile() && st.size < 1024 * 1024) out.insertions += fs.readFileSync(path.join(cwd, f), 'utf8').split('\n').filter((l, i, a) => i < a.length - 1 || l).length;
        } catch {
          /* unreadable: skip */
        }
      }
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

/** Parse `git diff` unified output into files and hunks with line numbers. */
export function parseUnifiedDiff(text) {
  const files = [];
  let file = null;
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

function isBinaryBuffer(buf) {
  const n = Math.min(buf.length, 8000);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return true;
  return false;
}

async function untrackedAsDiff(root, rel) {
  const full = path.join(root, rel);
  const f = { path: rel, oldPath: null, status: 'A', binary: false, additions: 0, deletions: 0, hunks: [], truncated: false, untracked: true };
  let st;
  try {
    st = fs.statSync(full);
  } catch {
    return null;
  }
  if (!st.isFile()) return null;
  if (st.size > MAX_FILE_BYTES) {
    f.truncated = true;
    return f;
  }
  const buf = fs.readFileSync(full);
  if (isBinaryBuffer(buf)) {
    f.binary = true;
    return f;
  }
  const text = buf.toString('utf8');
  const lines = text.split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  f.additions = lines.length;
  if (lines.length > MAX_LINES_PER_FILE) {
    f.truncated = true;
    return f;
  }
  f.hunks.push({ oldStart: 0, oldLines: 0, newStart: 1, newLines: lines.length, header: '', lines: lines.map((s, i) => ({ t: '+', s, n: i + 1 })) });
  return f;
}

/** Working tree vs `base` (default HEAD), including untracked files. */
export async function diff(cwd, { base } = {}) {
  const i = await info(cwd);
  if (!i.isRepo) return { isRepo: false, files: [] };
  let baseRef = base || 'HEAD';
  let baseLabel = base || 'HEAD';
  if (base && base !== 'HEAD') {
    const mb = await git(cwd, ['merge-base', base, 'HEAD']);
    if (mb.code === 0) baseRef = mb.stdout.trim();
  }
  const hasHead = (await git(cwd, ['rev-parse', '--verify', '-q', 'HEAD'])).code === 0;
  const args = hasHead ? ['diff', '--no-ext-diff', '--find-renames', '-U3', baseRef, '--'] : ['diff', '--no-ext-diff', '--cached', '-U3', '--'];
  const r = await git(i.root, args);
  const files = parseUnifiedDiff(r.stdout);
  const ut = await git(i.root, ['ls-files', '--others', '--exclude-standard', '-z']);
  const untracked = ut.stdout.split('\0').filter(Boolean).slice(0, MAX_UNTRACKED);
  for (const rel of untracked) {
    const f = await untrackedAsDiff(i.root, rel);
    if (f) files.push(f);
  }
  files.sort((a, b) => a.path.localeCompare(b.path));
  return { isRepo: true, root: i.root, base: baseLabel, baseRef, branch: i.branch, files, error: r.code ? r.stderr : null };
}

export async function commit(cwd, { message, paths }) {
  const i = await info(cwd);
  if (!i.isRepo) return { ok: false, output: 'Not a git repository' };
  const add = await git(i.root, ['add', '-A', '--', ...(paths?.length ? paths : ['.'])]);
  if (add.code) return { ok: false, output: add.stderr || add.stdout };
  const c = await git(i.root, ['commit', '-m', message]);
  return { ok: c.code === 0, output: (c.stdout + c.stderr).trim() };
}

export async function discard(cwd, { path: rel, untracked }) {
  const i = await info(cwd);
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

export async function createWorktree({ repo, base, branch, dir }) {
  const i = await info(repo);
  if (!i.isRepo) throw new Error('Not a git repository: ' + repo);
  const root = i.mainRoot || i.root;
  let name = branch;
  for (let n = 2; (await git(root, ['rev-parse', '--verify', '-q', `refs/heads/${name}`])).code === 0; n++) name = `${branch}-${n}`;
  let target = dir;
  for (let n = 2; fs.existsSync(target); n++) target = `${dir}-${n}`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const args = ['worktree', 'add', '-b', name, target];
  if (base) args.push(base);
  const r = await git(root, args);
  if (r.code) throw new Error((r.stderr || r.stdout).trim() || 'git worktree add failed');
  return { path: target, branch: name, base: base || i.branch || 'HEAD', repoRoot: root };
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
