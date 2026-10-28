// File system access for the Files panel, the folder picker and @-mentions.
import fs from 'node:fs';
import path from 'node:path';
import { HOME, tildify, slash, IS_WIN } from './util.js';
import { git } from './git.js';

const HEAVY = new Set(['node_modules', '.git', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build', '.next', 'target', '.gradle', '.idea', '.tox']);
const MAX_READ = 4 * 1024 * 1024;
const IMAGE_EXT = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.bmp': 'image/bmp', '.ico': 'image/x-icon' };

export function mimeFor(p) {
  return IMAGE_EXT[path.extname(p).toLowerCase()] || null;
}

export function listDir(dir, { showHidden = true } = {}) {
  const out = [];
  let ents = [];
  try {
    ents = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return { dir: slash(dir), entries: [], error: e.code === 'EACCES' || e.code === 'EPERM' ? 'Permission denied' : e.message };
  }
  for (const e of ents) {
    if (e.name === '.git') continue;
    if (!showHidden && e.name.startsWith('.')) continue;
    const full = slash(path.join(dir, e.name));
    let type = e.isDirectory() ? 'dir' : e.isSymbolicLink() ? 'link' : 'file';
    let size = 0;
    if (type === 'link') {
      try {
        const st = fs.statSync(full);
        type = st.isDirectory() ? 'dir' : 'file';
        size = st.size;
      } catch {
        type = 'file';
      }
    } else if (type === 'file') {
      try {
        size = fs.statSync(full).size;
      } catch {
        /* vanished */
      }
    }
    out.push({ name: e.name, path: full, type, size, heavy: type === 'dir' && HEAVY.has(e.name) });
  }
  out.sort((a, b) => (a.type === 'dir') !== (b.type === 'dir') ? (a.type === 'dir' ? -1 : 1) : a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
  return { dir: slash(dir), entries: out };
}

function looksBinary(buf) {
  const n = Math.min(buf.length, 8000);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return true;
  return false;
}

export function readFile(p) {
  p = slash(p);
  const st = fs.statSync(p);
  if (!st.isFile()) throw new Error('Not a file');
  const mime = mimeFor(p);
  if (mime) {
    if (st.size > 12 * 1024 * 1024) return { path: p, size: st.size, mtime: st.mtimeMs, binary: true, tooLarge: true, mime };
    return { path: p, size: st.size, mtime: st.mtimeMs, binary: true, mime, dataUrl: `data:${mime};base64,${fs.readFileSync(p).toString('base64')}` };
  }
  if (st.size > MAX_READ) return { path: p, size: st.size, mtime: st.mtimeMs, tooLarge: true };
  const buf = fs.readFileSync(p);
  if (looksBinary(buf)) return { path: p, size: st.size, mtime: st.mtimeMs, binary: true };
  return { path: p, size: st.size, mtime: st.mtimeMs, text: buf.toString('utf8') };
}

export function writeFile(p, text, { expectedMtime, force } = {}) {
  if (!force && expectedMtime && fs.existsSync(p)) {
    const cur = fs.statSync(p).mtimeMs;
    if (Math.abs(cur - expectedMtime) > 1) return { ok: false, conflict: true, mtime: cur };
  }
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, text);
  return { ok: true, mtime: fs.statSync(p).mtimeMs };
}

/** Windows drive roots that exist (C:/, D:/ …), shown above the drives' own folders. */
function drives() {
  const out = [];
  for (let c = 67; c <= 90; c++) {
    const root = `${String.fromCharCode(c)}:/`;
    try {
      fs.accessSync(root);
      out.push({ name: root.slice(0, 2), path: root, repo: false });
    } catch {
      /* no such drive */
    }
  }
  return out;
}

/** Directory browser for the "new session" folder picker. */
export function browse(dir) {
  // On Windows "/" is the list of drives, and a drive root's parent is that list.
  if (IS_WIN && (dir === '/' || dir === '\\')) return { dir: '/', display: 'This PC', parent: null, repo: false, dirs: drives(), home: HOME };
  const d = slash(dir ? path.resolve(dir) : HOME);
  const isRoot = IS_WIN ? /^[A-Za-z]:\/?$/.test(d) : d === '/';
  const res = listDir(d, { showHidden: false });
  const dirs = res.entries.filter((e) => e.type === 'dir').map((e) => ({ name: e.name, path: e.path, repo: fs.existsSync(path.join(e.path, '.git')) }));
  const parent = isRoot ? (IS_WIN ? '/' : null) : slash(path.dirname(d));
  return { dir: d, display: tildify(d), parent, repo: fs.existsSync(path.join(d, '.git')), dirs, error: res.error, home: HOME };
}

// ---- file index for @-mentions (cached per cwd)
const indexCache = new Map();

async function walk(root, limit) {
  const out = [];
  const stack = [''];
  while (stack.length && out.length < limit) {
    const rel = stack.pop();
    let ents = [];
    try {
      ents = fs.readdirSync(path.join(root, rel), { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of ents) {
      if (HEAVY.has(e.name) || e.name === '.git') continue;
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) stack.push(r);
      else out.push(r);
      if (out.length >= limit) break;
    }
  }
  return out;
}

export async function fileIndex(cwd, { force = false } = {}) {
  const hit = indexCache.get(cwd);
  if (!force && hit && Date.now() - hit.at < 20_000) return hit.files;
  let files = [];
  const r = await git(cwd, ['ls-files', '-co', '--exclude-standard', '-z']);
  if (r.code === 0) files = r.stdout.split('\0').filter(Boolean).slice(0, 60_000);
  else files = await walk(cwd, 30_000);
  // Include directories too, so "@src/" style mentions work.
  const dirs = new Set();
  for (const f of files) {
    let i = f.lastIndexOf('/');
    while (i > 0) {
      const d = f.slice(0, i);
      if (dirs.has(d)) break;
      dirs.add(d);
      i = d.lastIndexOf('/');
    }
  }
  const all = [...[...dirs].map((d) => d + '/'), ...files];
  indexCache.set(cwd, { at: Date.now(), files: all });
  return all;
}
