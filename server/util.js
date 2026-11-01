// Small shared helpers for the Pilcrow server.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

export const IS_WIN = process.platform === 'win32';
/**
 * Paths the UI sees always use forward slashes ("C:/Users/me/proj" on Windows): Node accepts them
 * everywhere, and one separator keeps display, comparison and grouping simple.
 */
export const slash = (p) => (IS_WIN && typeof p === 'string' ? p.replace(/\\/g, '/') : p);
export const HOME = slash(os.homedir());
export const IS_ROOT = typeof process.getuid === 'function' && process.getuid() === 0;
/** The app's version, from its own package.json (the one copy every module uses). */
export const VERSION = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version || '1.0.0';

/** An error that says what kind of failure it is ('invalid_params', 'not_found'), for the client to act on;
 *  the message is for the person. */
export class AppError extends Error {
  /** @param {string} code @param {string} message */
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export const uuid = () => crypto.randomUUID();
/** A short, stable file name for a folder (the timeline's shadow repository, the project's notes). */
export const folderKey = (cwd) => crypto.createHash('sha1').update(slash(cwd)).digest('hex').slice(0, 16);
export const fmtCost = (usd) => `$${(usd || 0).toFixed(2)}`;

/** The text of a message's content: a string, or its text blocks joined. */
export function textOfContent(content) {
  if (typeof content === 'string') return content;
  return Array.isArray(content) ? content.filter((b) => b?.type === 'text').map((b) => b.text).join('\n') : '';
}

/** What Claude said last in a transcript (its newest message of its own with text), on one line. */
export function lastAssistantText(messages) {
  for (let i = (messages?.length || 0) - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.type === 'assistant' && !m.parent_tool_use_id) {
      const t = textOfContent(m.message?.content);
      if (t) return t.replace(/\s+/g, ' ').trim();
    }
  }
  return '';
}
export const token = () => crypto.randomBytes(24).toString('base64url');

/**
 * Where Pilcrow keeps its config, data and state, and where the "Reticle" name kept them before the
 * rename. Only computes paths: importing a module must never touch the person's files.
 */
function locate(kind) {
  if (IS_WIN) {
    // Settings roam with the profile (%APPDATA%); state and data stay on this machine (%LOCALAPPDATA%).
    const roaming = process.env.APPDATA || path.join(HOME, 'AppData', 'Roaming');
    const local = process.env.LOCALAPPDATA || path.join(HOME, 'AppData', 'Local');
    return kind === 'config' ? { dir: path.join(roaming, 'Pilcrow'), legacy: path.join(roaming, 'Reticle') } : { dir: path.join(local, 'Pilcrow', kind), legacy: path.join(local, 'Reticle', kind) };
  }
  const env = { config: 'XDG_CONFIG_HOME', data: 'XDG_DATA_HOME', state: 'XDG_STATE_HOME' }[kind];
  const fallback = { config: '.config', data: '.local/share', state: '.local/state' }[kind];
  const base = process.env[env] || path.join(HOME, fallback);
  return { dir: path.join(base, 'pilcrow'), legacy: path.join(base, 'reticle') };
}

/** @param {'config' | 'data' | 'state'} kind */
export function xdg(kind) {
  return locate(kind).dir;
}

/** First run under the new name: move what the app kept as "Reticle" to its "Pilcrow" folders (main.start). */
export function migrateLegacyDirs() {
  for (const kind of /** @type {const} */ (['config', 'data', 'state'])) {
    const { dir, legacy } = locate(kind);
    try {
      if (fs.existsSync(dir) || !fs.existsSync(legacy)) continue;
      fs.mkdirSync(path.dirname(dir), { recursive: true });
      try {
        fs.renameSync(legacy, dir);
      } catch {
        fs.cpSync(legacy, dir, { recursive: true });
      }
    } catch {
      /* keep going with an empty folder */
    }
  }
}

export function claudeConfigDir() {
  return process.env.CLAUDE_CONFIG_DIR || path.join(HOME, '.claude');
}

/**
 * Environment for processes we spawn (Claude Code, terminals). Undoes what the desktop shell
 * changes: Electron rewrites XDG_CURRENT_DESKTOP and adds its own ELECTRON_ and CHROME_ variables.
 */
export function cleanEnv(extra = {}) {
  const env = { ...process.env };
  if (env.ORIGINAL_XDG_CURRENT_DESKTOP) {
    env.XDG_CURRENT_DESKTOP = env.ORIGINAL_XDG_CURRENT_DESKTOP;
    delete env.ORIGINAL_XDG_CURRENT_DESKTOP;
  }
  for (const k of Object.keys(env)) if (k.startsWith('ELECTRON_') || k === 'CHROME_DESKTOP') delete env[k];
  // Never let a parent Claude Code session leak into ours.
  for (const k of ['CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID']) delete env[k];
  return Object.assign(env, extra);
}

export function expandHome(p) {
  if (!p) return p;
  if (p === '~') return HOME;
  if (p.startsWith('~/') || (IS_WIN && p.startsWith('~\\'))) return slash(path.join(HOME, p.slice(2)));
  return slash(p);
}

/** A folder the person typed, as one absolute path: ~ expanded, forward slashes, no trailing slash. */
export const absolute = (p) => slash(path.resolve(expandHome(String(p || '').trim()))).replace(/(?<=.)\/+$/, '');

/** Whether `p` is `dir` or inside it, by whole path segments (/a/bc is not inside /a/b). */
export function under(dir, p) {
  if (!dir || !p) return false;
  const d = slash(dir);
  const q = slash(p);
  return q === d || q.startsWith(d.endsWith('/') ? d : d + '/');
}

export function tildify(p) {
  if (!p) return p;
  p = slash(p);
  const same = IS_WIN ? (a, b) => a.toLowerCase() === b.toLowerCase() : (a, b) => a === b;
  if (same(p, HOME)) return '~';
  return same(p.slice(0, HOME.length + 1), HOME + '/') ? '~' + p.slice(HOME.length) : p;
}

const WHICH_TTL_MS = 30_000; // long enough to spare the stats, short enough to see a fresh install
const whichCache = new Map(); // `${PATH}\0${cmd}` -> { at, found }

/** Locate an executable on PATH (like `which`; on Windows also tries the PATHEXT extensions). */
export function which(cmd) {
  if (!cmd) return null;
  const key = `${process.env.PATH || process.env.Path || ''}\0${cmd}`;
  const hit = whichCache.get(key);
  if (hit && Date.now() - hit.at < WHICH_TTL_MS) return hit.found;
  const found = search(cmd);
  whichCache.set(key, { at: Date.now(), found });
  return found;
}

function search(cmd) {
  const exts = IS_WIN ? ['', ...(process.env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean).map((e) => e.toLowerCase())] : [''];
  const hasDir = cmd.includes('/') || (IS_WIN && cmd.includes('\\'));
  if (hasDir) {
    for (const e of exts) if (isExecutable(cmd + e)) return slash(cmd + e);
    return null;
  }
  for (const dir of (process.env.PATH || process.env.Path || '').split(path.delimiter)) {
    if (!dir) continue;
    for (const e of exts) {
      const full = path.join(dir.replace(/^"|"$/g, ''), cmd + e);
      if (isExecutable(full)) return slash(full);
    }
  }
  return null;
}

export function isExecutable(p) {
  try {
    const st = fs.statSync(p);
    if (!st.isFile()) return false;
    // Windows has no execute bit; existence (with a PATHEXT extension) is what counts.
    if (IS_WIN) return /\.(exe|cmd|bat|com)$/i.test(p);
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** execFile as a promise; never rejects — returns { code, stdout, stderr }. */
export function run(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    execFile(cmd, args, { maxBuffer: 64 * 1024 * 1024, timeout: 60_000, windowsHide: true, ...opts, encoding: 'utf8' }, (err, stdout, stderr) => {
      let code = 0;
      if (err) code = typeof err.code === 'number' ? err.code : 1;
      resolve({ code, stdout: stdout || '', stderr: stderr || (err && typeof err.code !== 'number' ? String(err.message) : '') });
    });
  });
}

export function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

export async function readJsonAsync(file, fallback) {
  try {
    return JSON.parse(await fs.promises.readFile(file, 'utf8'));
  } catch {
    return fallback;
  }
}

const BINARY_SNIFF = 8000; // bytes looked at for a NUL, as git does

/** Whether a file's bytes look binary (a NUL early on), as git decides it. */
export function looksBinary(buf) {
  const n = Math.min(buf.length, BINARY_SNIFF);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return true;
  return false;
}

/** Create a folder only this user can open (0700), tightening it if it already exists. */
export function privateDir(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try {
    fs.chmodSync(dir, 0o700);
  } catch {
    /* not ours to change */
  }
  return dir;
}

/** Atomic JSON write (temp file + rename). Pilcrow's own files are private to the user (0600). */
export function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = tempPath(file);
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, file);
}

/**
 * Run `fn` once, `ms` after the first call of a burst. Unlike a debounce, a steady stream of calls
 * cannot starve it: a file saved this way is never more than `ms` behind.
 */
export function coalesce(fn, ms) {
  let t = null;
  const run = () => {
    clearTimeout(t);
    t = null;
    fn();
  };
  const call = () => {
    t ??= setTimeout(run, ms);
  };
  /** Run now if a call is waiting (the server is quitting); nothing waiting, nothing to write. */
  call.flush = () => t !== null && run();
  return call;
}

let tmpSeq = 0;
/** A temp file beside `file` for an atomic write; unique per write, so overlapping writes stay whole. */
export const tempPath = (file) => `${file}.${process.pid}.${++tmpSeq}.tmp`;

/** writeJson without blocking the event loop. */
export async function writeJsonAsync(file, data) {
  await fs.promises.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = tempPath(file);
  await fs.promises.writeFile(tmp, JSON.stringify(data), { mode: 0o600 });
  await fs.promises.rename(tmp, file);
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

let logStream = null;
export function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.map((a) => (typeof a === 'string' ? a : a instanceof Error ? a.stack : JSON.stringify(a))).join(' ')}\n`;
  if (process.env.PILCROW_DEBUG) process.stderr.write(line);
  try {
    if (!logStream) {
      const dir = xdg('state');
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      const fd = fs.openSync(path.join(dir, 'pilcrow.log'), 'a', 0o600);
      fs.fchmodSync(fd, 0o600); // also tightens logs left by older versions
      logStream = fs.createWriteStream(null, { fd });
    }
    logStream.write(line);
  } catch {
    /* logging must never crash the app */
  }
}

/** A push-based async iterable queue (used as the SDK's streaming prompt). */
export class AsyncQueue {
  constructor() {
    this.items = [];
    this.waiters = [];
    this.closed = false;
  }
  push(item) {
    if (this.closed) return false;
    const w = this.waiters.shift();
    if (w) w({ value: item, done: false });
    else this.items.push(item);
    return true;
  }
  close() {
    this.closed = true;
    for (const w of this.waiters.splice(0)) w({ value: undefined, done: true });
  }
  [Symbol.asyncIterator]() {
    return {
      next: () => {
        if (this.items.length) return Promise.resolve({ value: this.items.shift(), done: false });
        if (this.closed) return Promise.resolve({ value: undefined, done: true });
        return new Promise((resolve) => this.waiters.push(resolve));
      },
      return: () => {
        this.close();
        return Promise.resolve({ value: undefined, done: true });
      },
    };
  }
}

export function slugify(s, max = 40) {
  return (s || '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[\s_]+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, max)
    .replace(/-$/, '');
}
