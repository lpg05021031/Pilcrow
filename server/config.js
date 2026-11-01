// Persistent app settings and per-session metadata (titles, worktrees, archive flags).
import path from 'node:path';
import { xdg, readJson, writeJson, coalesce } from './util.js';

const DIR = xdg('config');
const CONFIG_FILE = path.join(DIR, 'config.json');
const META_FILE = path.join(DIR, 'sessions.json');
const SAVE_MS = 1000; // sessions.json changes with every turn; once a second is plenty

export const DEFAULT_CONFIG = {
  theme: 'system', // 'system' | 'dark' | 'light'
  fontSize: 14.5,
  sendKey: 'enter', // 'enter' | 'mod-enter'
  claudeBinary: 'auto', // 'auto' | 'bundled' | 'system' | absolute path
  defaultModel: '', // '' = Claude Code default
  defaultMode: 'default',
  defaultEffort: '',
  worktreeRoot: '', // '' = ~/.local/share/pilcrow/worktrees
  idleTimeoutMin: 30,
  notifications: true,
  promptSuggestions: true,
  showThinking: true,
  timeline: true, // record a snapshot of the folder after every step so you can scrub back
  memoryAuto: true, // distil notes about a project when a turn ends
  memoryInject: true, // hand those notes to new sessions
  memoryModel: '', // '' = the session default
  recentProjects: [],
  repos: [], // the repositories registry (REPOSITORIES.md): names, aliases, policy, defaults, notes
  helpers: { enabled: true, total: 8, parallel: 0, maxMinutes: 30, costUsd: 0, wakeLead: true }, // HELPERS.md §5.7; parallel 0 = min(3, cores ÷ 2)
  ui: {}, // free-form UI prefs (panel sizes, last tabs) owned by the client
  window: null,
  configVersion: 2,
};

/** The config as saved, brought up to the current version. */
function loadConfig() {
  const stored = readJson(CONFIG_FILE, {});
  // 1.1 retuned the type scale; a size saved as the old default moves to the new default.
  if ((stored.configVersion || 1) < 2) {
    if (stored.fontSize === 14) delete stored.fontSize;
    stored.configVersion = 2;
  }
  return { ...DEFAULT_CONFIG, ...stored };
}

/**
 * Both files are read on first use, not at import, so importing a module (a test, `--doctor`) never
 * reads or writes the person's files before it means to.
 */
class Store {
  #config = null;
  #meta = null;
  saveConfig = coalesce(() => writeJson(CONFIG_FILE, this.config), SAVE_MS);
  saveMeta = coalesce(() => writeJson(META_FILE, this.meta), SAVE_MS);

  /** @type {Record<string, any>} */
  get config() {
    return (this.#config ??= loadConfig());
  }

  /** @type {Record<string, Record<string, any>>} id -> that session's metadata */
  get meta() {
    return (this.#meta ??= readJson(META_FILE, {}));
  }

  getConfig() {
    return this.config;
  }

  setConfig(patch) {
    for (const [k, v] of Object.entries(patch || {})) {
      if (!(k in DEFAULT_CONFIG)) continue;
      if (k === 'ui' && v && typeof v === 'object') this.config.ui = { ...this.config.ui, ...v };
      else this.config[k] = v;
    }
    this.saveConfig();
    return this.config;
  }

  addRecentProject(dir) {
    if (!dir) return;
    const list = [dir, ...this.config.recentProjects.filter((d) => d !== dir)].slice(0, 12);
    this.config.recentProjects = list;
    this.saveConfig();
  }

  getMeta(id) {
    return this.meta[id] || null;
  }

  /** Merge `patch` into a session's metadata (null or undefined deletes a key). `persist: false`
   *  keeps the change in memory until the next save, for callers on a read path. */
  setMeta(id, patch, { persist = true } = {}) {
    const cur = this.meta[id] || {};
    const next = { ...cur, ...patch };
    for (const [k, v] of Object.entries(next)) if (v === undefined || v === null) delete next[k];
    this.meta[id] = next;
    if (persist) this.saveMeta();
    return next;
  }

  deleteMeta(id) {
    delete this.meta[id];
    this.saveMeta();
  }

  /** Write now what is waiting to be saved. */
  flush() {
    this.saveConfig.flush();
    this.saveMeta.flush();
  }
}

export const store = new Store();
export const CONFIG_DIR = DIR;
