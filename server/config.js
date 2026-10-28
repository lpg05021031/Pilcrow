// Persistent app settings and per-session metadata (titles, worktrees, archive flags).
import path from 'node:path';
import { xdg, readJson, writeJson, debounce } from './util.js';

const DIR = xdg('config');
const CONFIG_FILE = path.join(DIR, 'config.json');
const META_FILE = path.join(DIR, 'sessions.json');

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
  timeline: true,
  memoryAuto: true, // distil notes about a project when a turn ends
  memoryInject: true, // hand those notes to new sessions
  memoryModel: '', // '' = the session default // record a snapshot of the folder after every step so you can scrub back
  recentProjects: [],
  ui: {}, // free-form UI prefs (panel sizes, last tabs) owned by the client
  window: null,
  configVersion: 2,
};

class Store {
  constructor() {
    const stored = readJson(CONFIG_FILE, {});
    // 1.1 retuned the type scale; a size saved as the old default moves to the new default.
    if ((stored.configVersion || 1) < 2) {
      if (stored.fontSize === 14) delete stored.fontSize;
      stored.configVersion = 2;
    }
    this.config = { ...DEFAULT_CONFIG, ...stored };
    this.meta = readJson(META_FILE, {});
    this.saveConfig = debounce(() => writeJson(CONFIG_FILE, this.config), 200);
    this.saveMeta = debounce(() => writeJson(META_FILE, this.meta), 200);
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

  setMeta(id, patch) {
    const cur = this.meta[id] || {};
    const next = { ...cur, ...patch };
    for (const [k, v] of Object.entries(next)) if (v === undefined || v === null) delete next[k];
    this.meta[id] = next;
    this.saveMeta();
    return next;
  }

  deleteMeta(id) {
    delete this.meta[id];
    this.saveMeta();
  }

  flush() {
    writeJson(CONFIG_FILE, this.config);
    writeJson(META_FILE, this.meta);
  }
}

export const store = new Store();
export const CONFIG_DIR = DIR;
