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

export const uuid = () => crypto.randomUUID();
export const token = () => crypto.randomBytes(24).toString('base64url');

/** First run under the new name: move what the app kept as "Reticle" to its "Pilcrow" folders. */
function migrateDir(oldDir, newDir) {
  try {
    if (fs.existsSync(newDir) || !fs.existsSync(oldDir)) return;
    fs.mkdirSync(path.dirname(newDir), { recursive: true });
    try {
      fs.renameSync(oldDir, newDir);
    } catch {
      fs.cpSync(oldDir, newDir, { recursive: true });
    }
  } catch {
    /* keep going with an empty folder */
  }
}

export function xdg(kind) {
  if (IS_WIN) {
    // Settings roam with the profile (%APPDATA%); state and data stay on this machine (%LOCALAPPDATA%).
    const roaming = process.env.APPDATA || path.join(HOME, 'AppData', 'Roaming');
    const local = process.env.LOCALAPPDATA || path.join(HOME, 'AppData', 'Local');
    const dir = kind === 'config' ? path.join(roaming, 'Pilcrow') : path.join(local, 'Pilcrow', kind);
    migrateDir(kind === 'config' ? path.join(roaming, 'Reticle') : path.join(local, 'Reticle', kind), dir);
    return dir;
  }
  const env = { config: 'XDG_CONFIG_HOME', data: 'XDG_DATA_HOME', state: 'XDG_STATE_HOME' }[kind];
  const fallback = { config: '.config', data: '.local/share', state: '.local/state' }[kind];
  const base = process.env[env] || path.join(HOME, fallback);
  const dir = path.join(base, 'pilcrow');
  migrateDir(path.join(base, 'reticle'), dir);
  return dir;
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

export function tildify(p) {
  if (!p) return p;
  p = slash(p);
  const same = IS_WIN ? (a, b) => a.toLowerCase() === b.toLowerCase() : (a, b) => a === b;
  if (same(p, HOME)) return '~';
  return same(p.slice(0, HOME.length + 1), HOME + '/') ? '~' + p.slice(HOME.length) : p;
}

/** Locate an executable on PATH (like `which`; on Windows also tries the PATHEXT extensions). */
export function which(cmd) {
  if (!cmd) return null;
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
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, file);
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

const LOG_DIR = xdg('state');
let logStream = null;
export function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.map((a) => (typeof a === 'string' ? a : a instanceof Error ? a.stack : JSON.stringify(a))).join(' ')}\n`;
  if (process.env.PILCROW_DEBUG) process.stderr.write(line);
  try {
    if (!logStream) {
      fs.mkdirSync(LOG_DIR, { recursive: true, mode: 0o700 });
      const fd = fs.openSync(path.join(LOG_DIR, 'pilcrow.log'), 'a', 0o600);
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
