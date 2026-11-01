// File system access for the Files panel, the folder picker and @-mentions.
import fs from 'node:fs';
import path from 'node:path';
import { HOME, tildify, slash, IS_WIN, looksBinary, tempPath } from './util.js';
import { git } from './git.js';

const HEAVY = new Set(['node_modules', '.git', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build', '.next', 'target', '.gradle', '.idea', '.tox']);
const MAX_READ = 4 * 1024 * 1024; // text shown in the editor
const MAX_IMAGE = 12 * 1024 * 1024; // images go to the viewer as a data URL
/** Content types by extension: the one table for the interface's files, /api/fs, /api/raw and the image viewer. */
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.map': 'application/json',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/plain; charset=utf-8',
  '.xml': 'application/xml',
  '.wasm': 'application/wasm',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.bmp': 'image/bmp',
  '.ico': 'image/x-icon',
  '.mp4': 'video/mp4',
  '.pdf': 'application/pdf',
};

/** @param {string} p */
export const mimeOf = (p) => MIME[path.extname(p).toLowerCase()] || null;

/** An image's content type, or null: what the viewer shows as a picture, and all /api/raw types. */
export function imageMime(p) {
  const t = mimeOf(p);
  return t?.startsWith('image/') ? t : null;
}

/** A folder's entries for the Files panel: folders first, then by name as people sort (2 before 10). */
export async function listDir(dir, { showHidden = true } = {}) {
  let ents = [];
  try {
    ents = await fs.promises.readdir(dir, { withFileTypes: true });
  } catch (e) {
    return { dir: slash(dir), entries: [], error: e.code === 'EACCES' || e.code === 'EPERM' ? 'Permission denied' : e.message };
  }
  const shown = ents.filter((e) => e.name !== '.git' && (showHidden || !e.name.startsWith('.')));
  const out = await Promise.all(
    shown.map(async (e) => {
      const full = slash(path.join(dir, e.name));
      if (e.isDirectory()) return { name: e.name, path: full, type: 'dir', size: 0, heavy: HEAVY.has(e.name) };
      // A link is what it points to; a file needs its size. Either may have vanished meanwhile.
      const st = e.isSymbolicLink() || e.isFile() ? await fs.promises.stat(full).catch(() => null) : null;
      const type = st?.isDirectory() ? 'dir' : 'file';
      return { name: e.name, path: full, type, size: st && type === 'file' ? st.size : 0, heavy: type === 'dir' && HEAVY.has(e.name) };
    }),
  );
  out.sort((a, b) => (a.type === 'dir') !== (b.type === 'dir') ? (a.type === 'dir' ? -1 : 1) : a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
  return { dir: slash(dir), entries: out };
}

/** A file for the editor or the image viewer: text, an image as a data URL, or why it is not shown. */
export async function readFile(p) {
  p = slash(p);
  const st = await fs.promises.stat(p);
  if (!st.isFile()) throw new Error('Not a file');
  const head = { path: p, size: st.size, mtime: st.mtimeMs };
  const mime = imageMime(p);
  if (mime) {
    if (st.size > MAX_IMAGE) return { ...head, binary: true, tooLarge: true, mime };
    return { ...head, binary: true, mime, dataUrl: `data:${mime};base64,${(await fs.promises.readFile(p)).toString('base64')}` };
  }
  if (st.size > MAX_READ) return { ...head, tooLarge: true };
  const buf = await fs.promises.readFile(p);
  if (looksBinary(buf)) return { ...head, binary: true };
  return { ...head, text: buf.toString('utf8') };
}

/**
 * Save a file the way editors do: into a temp file beside it, then a rename, so a crash or a full disk
 * never leaves it half written. A link is written through to its target, and the file keeps its mode.
 * @param {string} p @param {string} text @param {{ expectedMtime?: number, force?: boolean }} [opts]
 */
export async function writeFile(p, text, { expectedMtime, force } = {}) {
  const target = await fs.promises.realpath(p).catch(() => p);
  const cur = await fs.promises.stat(target).catch(() => null);
  if (!force && expectedMtime && cur && Math.abs(cur.mtimeMs - expectedMtime) > 1) return { ok: false, conflict: true, mtime: cur.mtimeMs };
  await fs.promises.mkdir(path.dirname(target), { recursive: true });
  const tmp = tempPath(target);
  try {
    await fs.promises.writeFile(tmp, text);
    if (cur) await fs.promises.chmod(tmp, cur.mode & 0o7777);
    await fs.promises.rename(tmp, target);
  } catch (e) {
    await fs.promises.rm(tmp, { force: true });
    throw e;
  }
  return { ok: true, mtime: (await fs.promises.stat(target)).mtimeMs };
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
export async function browse(dir) {
  // On Windows "/" is the list of drives, and a drive root's parent is that list.
  if (IS_WIN && (dir === '/' || dir === '\\')) return { dir: '/', display: 'This PC', parent: null, repo: false, dirs: drives(), home: HOME };
  const d = slash(dir ? path.resolve(dir) : HOME);
  const isRoot = IS_WIN ? /^[A-Za-z]:\/?$/.test(d) : d === '/';
  const isRepo = (p) => fs.promises.access(path.join(p, '.git')).then(() => true, () => false);
  const res = await listDir(d, { showHidden: false });
  const dirs = await Promise.all(res.entries.filter((e) => e.type === 'dir').map(async (e) => ({ name: e.name, path: e.path, repo: await isRepo(e.path) })));
  const parent = isRoot ? (IS_WIN ? '/' : null) : slash(path.dirname(d));
  return { dir: d, display: tildify(d), parent, repo: await isRepo(d), dirs, error: res.error, home: HOME };
}

// ---- file index for @-mentions (cached per cwd)
const INDEX_TTL_MS = 20_000;
const MAX_INDEXED = 8; // folders whose index stays in memory; each can hold 60K paths
const MAX_INDEX_FILES = 60_000;
const MAX_WALK_FILES = 30_000; // a folder without git is walked, which costs more per file
const indexCache = new Map(); // cwd -> { at, files }, oldest first

async function walk(root, limit) {
  const out = [];
  const stack = [''];
  while (stack.length && out.length < limit) {
    const rel = stack.pop();
    let ents = [];
    try {
      ents = await fs.promises.readdir(path.join(root, rel), { withFileTypes: true });
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
  if (!force && hit && Date.now() - hit.at < INDEX_TTL_MS) return hit.files;
  let files = [];
  const r = await git(cwd, ['ls-files', '-co', '--exclude-standard', '-z']);
  if (r.code === 0) files = r.stdout.split('\0').filter(Boolean).slice(0, MAX_INDEX_FILES);
  else files = await walk(cwd, MAX_WALK_FILES);
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
  indexCache.delete(cwd);
  indexCache.set(cwd, { at: Date.now(), files: all });
  // An expired index is never served again; past the cap the oldest goes.
  for (const [k, v] of indexCache) if (indexCache.size > MAX_INDEXED || Date.now() - v.at >= INDEX_TTL_MS) indexCache.delete(k);
  return all;
}
