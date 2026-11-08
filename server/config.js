// Persistent app settings and per-session metadata (titles, worktrees, archive flags).
import path from 'node:path';
import { xdg, readJson, jsonFile, AppError } from './util.js';

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
  maxConcurrent: 3, // sessions Claude works on at once from the workboard; 0 = no limit
  handoffReport: true, // ask Claude to end finished work with a "## Handoff" section the workboard reads
  memoryModel: '', // '' = the session default
  recentProjects: [],
  repos: [], // the repositories registry (REPOSITORIES.md): names, aliases, policy, defaults, notes
  helpers: { enabled: true, total: 8, parallel: 0, maxMinutes: 30, costUsd: 0, wakeLead: true }, // HELPERS.md §5.7; parallel 0 = min(3, cores ÷ 2)
  ui: {}, // free-form UI prefs (panel sizes, last tabs) owned by the client
  window: null,
  configVersion: 2,
};

/** What a setting must hold, from its default: a list, an object (`window` is null until it is set) or
 *  the default's primitive type. */
const kindOf = (d) => (Array.isArray(d) ? 'a list' : d === null || typeof d === 'object' ? 'an object' : `a ${typeof d}`);
const isKind = (v, d) => (Array.isArray(d) ? Array.isArray(v) : d === null || typeof d === 'object' ? (v === null ? d === null : typeof v === 'object' && !Array.isArray(v)) : typeof v === typeof d);

/** The config as saved, brought up to the current version. */
function loadConfig() {
  const stored = readJson(CONFIG_FILE, {});
  // 1.1 retuned the type scale; a size saved as the old default moves to the new default.
  if ((stored.configVersion || 1) < 2) {
    if (stored.fontSize === 14) delete stored.fontSize;
    stored.configVersion = 2;
  }
  // A value of the wrong kind (a file edited by hand) reads as the default, as setConfig would refuse it.
  for (const k of Object.keys(stored)) if (Object.hasOwn(DEFAULT_CONFIG, k) && !isKind(stored[k], DEFAULT_CONFIG[k])) delete stored[k];
  return { ...DEFAULT_CONFIG, ...stored };
}

/**
 * Both files are read on first use, not at import, so importing a module (a test, `--doctor`) never
 * reads or writes the person's files before it means to. Session ids come from the clients, so the
 * metadata has no prototype: no id ('__proto__', 'toString') names anything but a session. Both are
 * written off the event loop (jsonFile); flushSaves() writes them at once when the server quits.
 */
class Store {
  #config = null;
  #meta = null;
  #configFile = jsonFile(CONFIG_FILE, () => this.config, SAVE_MS);
  #metaFile = jsonFile(META_FILE, () => this.meta, SAVE_MS);

  /** @type {Record<string, any>} */
  get config() {
    return (this.#config ??= loadConfig());
  }

  /** @type {Record<string, Record<string, any>>} id -> that session's metadata */
  get meta() {
    return (this.#meta ??= Object.assign(Object.create(null), readJson(META_FILE, {})));
  }

  getConfig() {
    return this.config;
  }

  /** Merge known keys into the settings (`ui` merges one level down). A value of another kind than the
   *  default's (a string for fontSize, an object for recentProjects) is refused before anything changes:
   *  saved, it would break whatever reads that key next (session.create reads recentProjects). */
  setConfig(patch) {
    const entries = Object.entries(patch || {}).filter(([k]) => Object.hasOwn(DEFAULT_CONFIG, k));
    for (const [k, v] of entries) if (!isKind(v, DEFAULT_CONFIG[k])) throw new AppError('invalid_params', `${k} must be ${kindOf(DEFAULT_CONFIG[k])}`);
    for (const [k, v] of entries) this.config[k] = k === 'ui' ? { ...this.config.ui, ...v } : v;
    this.#configFile.save();
    return this.config;
  }

  addRecentProject(dir) {
    if (!dir) return;
    const list = [dir, ...this.config.recentProjects.filter((d) => d !== dir)].slice(0, 12);
    this.config.recentProjects = list;
    this.#configFile.save();
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
    if (persist) this.#metaFile.save();
    return next;
  }

  deleteMeta(id) {
    delete this.meta[id];
    this.#metaFile.save();
  }

}

export const store = new Store();
export const CONFIG_DIR = DIR;
