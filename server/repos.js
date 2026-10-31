// The repositories registry (REPOSITORIES.md): one list the person maintains of the repositories
// Pilcrow may work in. An entry has a name, the person's own aliases ("order FE", "前台"), one line
// about it, a policy for starting helpers and Dispatch runs there (allowed, ask, never), defaults,
// standing notes appended to every brief for it, and facts that are scanned, never typed. Helpers,
// Dispatch, the composer's @ and every session's system prompt read the same entries.
//
// `createRegistry` takes its collaborators so the tests can hand it a fake store; `repos` at the bottom
// is the instance the server uses.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { findRepos, manifestOf } from './dispatch_core.js';

export const POLICIES = ['allowed', 'ask', 'never'];
export const MODES = ['default', 'acceptEdits', 'plan'];
export const DEFAULT_KEYS = ['mode', 'worktree', 'base', 'prepare', 'verify', 'model'];
export const LIMITS = { name: 40, alias: 40, aliases: 12, about: 200, notes: 2000 };
export const DIGEST_MAX = 2000; // characters of system prompt the digest may take
export const DIGEST_FULL = 40; // entries shown with their about line; the rest as names and aliases
const REFRESH_MS = 60_000;
// folder names that say nothing about the repository; the manifest's name is better then
const GENERIC = new Set(['src', 'app', 'repo', 'repository', 'code', 'project', 'main', 'master', 'trunk', 'workspace', 'packages', 'service', 'server', 'client']);

/** The person's words, made comparable: case, spaces, hyphens, underscores and a leading @ do not count. */
export function normalizeWord(w) {
  return String(w || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/^@/, '')
    .replace(/[\s_\-]+/g, '');
}

function distance(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[b.length];
}

const str = (v, max) => String(v ?? '').trim().slice(0, max);
const under = (dir, p) => p === dir || p.startsWith(dir.endsWith('/') ? dir : dir + '/');

export function createRegistry(deps) {
  const {
    store, // { getConfig(), setConfig(patch) }
    git, // { info(cwd) }
    log = () => null,
    now = () => Date.now(),
    uuid = () => crypto.randomUUID(),
    slash = (p) => p,
    expandHome = (p) => p,
    exists = (p) => fs.existsSync(p),
  } = deps;
  const events = new EventEmitter();
  const refreshedAt = new Map(); // id -> when its facts were last read

  const all = () => {
    const list = store.getConfig().repos;
    return Array.isArray(list) ? list : [];
  };
  const write = (list) => {
    store.setConfig({ repos: list });
    events.emit('changed', { repos: list.map(withMissing) });
  };
  const withMissing = (e) => ({ ...e, missing: !exists(e.path) });
  const absolute = (p) => slash(path.resolve(expandHome(String(p || '').trim()))).replace(/(?<=.)\/+$/, '');
  const words = (e) => [e.name, ...(e.aliases || [])];

  /** The name a folder gets when the person types none: the folder's, or the manifest's when the folder's says nothing. */
  function nameFor(dir) {
    const base = path.basename(dir);
    if (!GENERIC.has(base.toLowerCase())) return base.slice(0, LIMITS.name);
    const m = exists(dir) ? manifestOf(dir) : { name: null };
    const pkg = m.name ? m.name.split('/').pop() : '';
    return (pkg || base).slice(0, LIMITS.name);
  }

  /** Validate and normalize what came from a client; throws with the reason. */
  function clean(input, current) {
    const e = current ? { ...current } : { id: uuid(), addedAt: now(), machine: 'local', facts: {} };
    if (input.path !== undefined || !current) {
      if (!input.path) throw new Error('A repository needs a folder.');
      e.path = absolute(input.path);
    }
    if (input.name !== undefined) e.name = str(input.name, LIMITS.name);
    if (!e.name) e.name = nameFor(e.path);
    if (input.aliases !== undefined) {
      const seen = new Set([normalizeWord(e.name)]);
      e.aliases = [];
      for (const a of Array.isArray(input.aliases) ? input.aliases : String(input.aliases).split(/[,\n]/)) {
        const t = str(a, LIMITS.alias);
        const n = normalizeWord(t);
        if (!t || !n || seen.has(n)) continue;
        seen.add(n);
        e.aliases.push(t);
        if (e.aliases.length >= LIMITS.aliases) break;
      }
    } else e.aliases = (e.aliases || []).filter((a) => normalizeWord(a) !== normalizeWord(e.name));
    if (input.about !== undefined) e.about = str(input.about, LIMITS.about);
    if (input.notes !== undefined) e.notes = str(input.notes, LIMITS.notes);
    if (input.policy !== undefined) e.policy = POLICIES.includes(input.policy) ? input.policy : 'ask';
    if (!e.policy) e.policy = 'ask';
    if (input.defaults !== undefined) {
      const d = {};
      for (const k of DEFAULT_KEYS) {
        const v = (input.defaults || {})[k];
        if (v === undefined || v === null || v === '') continue;
        if (k === 'worktree') d.worktree = Boolean(v);
        else if (k === 'mode') {
          if (MODES.includes(v)) d.mode = v;
        } else d[k] = str(v, 400);
      }
      e.defaults = d;
    }
    if (!e.defaults) e.defaults = {};
    if (input.deps !== undefined) e.deps = (Array.isArray(input.deps) ? input.deps : []).filter((d) => d !== e.id && all().some((x) => x.id === d));
    if (!e.deps) e.deps = [];
    e.about = e.about || '';
    e.notes = e.notes || '';
    return e;
  }

  function checkUnique(e) {
    const others = all().filter((x) => x.id !== e.id);
    const taken = others.find((x) => x.path === e.path);
    if (taken) throw new Error(`That folder is already registered as ${taken.name}.`);
    for (const w of words(e)) {
      const n = normalizeWord(w);
      const hit = others.find((x) => words(x).some((y) => normalizeWord(y) === n));
      if (hit) throw new Error(`“${w}” is already a name of ${hit.name}.`);
    }
  }

  // ---- reading
  function list() {
    return all().map(withMissing);
  }
  function get(id) {
    const e = all().find((x) => x.id === id);
    return e ? withMissing(e) : null;
  }
  /** The entry whose folder holds `dir` (the deepest one), or null. */
  function containing(dir) {
    if (!dir) return null;
    const d = absolute(dir);
    let best = null;
    for (const e of all()) if (under(e.path, d) && (!best || e.path.length > best.path.length)) best = e;
    return best;
  }
  const byIdOrPath = (x) => (x && all().find((e) => e.id === x)) || containing(x);
  /** 'allowed' | 'ask' | 'never' for a registered folder; null when the folder is not registered. */
  function policyFor(dir) {
    const e = containing(dir);
    return e ? e.policy : null;
  }
  function defaultsFor(x) {
    return byIdOrPath(x)?.defaults || {};
  }
  function notesFor(x) {
    return byIdOrPath(x)?.notes || '';
  }

  /** A word the person or the model used → the entry, by path, name, alias, then the folder's name. */
  function resolve(word) {
    const w = String(word || '').trim();
    if (!w) return null;
    const list_ = all();
    if (w.includes('/') || w.startsWith('~') || w.startsWith('.')) {
      const p = absolute(w);
      const hit = list_.find((e) => e.path === p);
      if (hit) return withMissing(hit);
    }
    const n = normalizeWord(w);
    if (!n) return null;
    const byName = list_.find((e) => normalizeWord(e.name) === n);
    if (byName) return withMissing(byName);
    const byAlias = list_.find((e) => (e.aliases || []).some((a) => normalizeWord(a) === n));
    if (byAlias) return withMissing(byAlias);
    const byBase = list_.find((e) => normalizeWord(path.basename(e.path)) === n);
    return byBase ? withMissing(byBase) : null;
  }
  /** The closest names to a word that matched nothing. */
  function nearest(word, n = 3) {
    const w = normalizeWord(word);
    const scored = [];
    for (const e of all()) {
      const best = Math.min(...words(e).map((x) => distance(w, normalizeWord(x))));
      scored.push({ name: e.name, d: best });
    }
    return scored
      .sort((a, b) => a.d - b.d || a.name.localeCompare(b.name))
      .slice(0, n)
      .map((s) => s.name);
  }

  /** The digest every session gets in its system prompt; '' when nothing is registered. */
  function promptFor() {
    const list_ = all();
    if (!list_.length) return '';
    const head = '\n\n# Repositories\nThe person registered these repositories (Pilcrow keeps the list; @name mentions them). Use these names when you talk about them and when you start helpers. A path the person gives wins over the list.\n';
    const lines = [];
    let used = head.length;
    let i = 0;
    for (const e of list_) {
      const also = e.aliases?.length ? ` (also: ${e.aliases.join(', ')})` : '';
      const full = i < DIGEST_FULL;
      const line = full ? `- ${e.name}${also}: ${e.about || 'no description'} · ${tildifyPath(e.path)} · helpers ${e.policy}` : `- ${e.name}${also}`;
      if (used + line.length + 1 > DIGEST_MAX) {
        lines.push(`- and ${list_.length - i} more; ask with the helpers list tool`);
        break;
      }
      lines.push(line);
      used += line.length + 1;
      i++;
    }
    return head + lines.join('\n') + '\n';
  }
  function tildifyPath(p) {
    const home = slash(process.env.HOME || process.env.USERPROFILE || '');
    return home && under(home, p) ? '~' + p.slice(home.length) : p;
  }

  // ---- writing
  async function save(input) {
    const current = input?.id ? all().find((x) => x.id === input.id) : null;
    if (input?.id && !current) throw new Error('No such repository.');
    const e = clean(input || {}, current);
    if (!exists(e.path) && (!current || current.path !== e.path)) throw new Error(`The folder does not exist: ${e.path}`);
    checkUnique(e);
    const list_ = current ? all().map((x) => (x.id === e.id ? e : x)) : [...all(), e];
    write(list_);
    await refresh([e.id], { force: true }).catch((err) => log('repos refresh failed', err?.message));
    return get(e.id);
  }
  function remove(id) {
    if (!all().some((x) => x.id === id)) return false;
    write(all().filter((x) => x.id !== id).map((x) => ({ ...x, deps: (x.deps || []).filter((d) => d !== id) })));
    refreshedAt.delete(id);
    return true;
  }
  /** Register a folder, or set fields on the entry that holds it (what *Always allow* on a start card does). */
  async function register(dir, patch = {}) {
    const cur = containing(dir);
    return cur ? save({ id: cur.id, ...patch }) : save({ path: dir, ...patch });
  }

  // ---- facts
  async function factsOf(dir) {
    if (!exists(dir)) return { seenAt: now(), missing: true };
    const m = manifestOf(dir);
    const info = await git.info(dir).catch(() => ({ isRepo: false }));
    return {
      manifest: m.kind,
      packageName: m.name,
      isRepo: Boolean(info.isRepo),
      branch: info.isRepo ? info.branch || null : null,
      dirty: Boolean(info.isRepo && ((info.changed || 0) + (info.untracked || 0) > 0)),
      hasClaudeMd: exists(path.join(dir, 'CLAUDE.md')) || exists(path.join(dir, '.claude', 'CLAUDE.md')),
      seenAt: now(),
    };
  }
  /** Re-read the scanned facts of some or all entries (throttled to once a minute each unless forced). */
  async function refresh(ids = null, { force = false } = {}) {
    const todo = all().filter((e) => (!ids || ids.includes(e.id)) && (force || now() - (refreshedAt.get(e.id) || 0) > REFRESH_MS));
    if (!todo.length) return list();
    const facts = new Map();
    let i = 0;
    const worker = async () => {
      while (i < todo.length) {
        const e = todo[i++];
        facts.set(e.id, await factsOf(e.path));
        refreshedAt.set(e.id, now());
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, todo.length) }, worker));
    const next = all().map((e) => (facts.has(e.id) ? { ...e, facts: facts.get(e.id) } : e));
    if (JSON.stringify(next) !== JSON.stringify(all())) write(next);
    return list();
  }

  /** Repositories under a folder that are not registered yet, for the Scan sheet. */
  async function scan(root, depth = 3) {
    const r = absolute(root);
    if (!exists(r)) throw new Error('Pick a folder that exists.');
    const dirs = findRepos(r, depth).map(slash);
    const known = new Set(all().map((e) => e.path));
    const found = [];
    let i = 0;
    const worker = async () => {
      while (i < dirs.length) {
        const dir = dirs[i++];
        const m = manifestOf(dir);
        const info = await git.info(dir).catch(() => ({ isRepo: false }));
        found.push({ path: dir, name: nameFor(dir), packageName: m.name, kind: m.kind, branch: info.branch || null, registered: known.has(dir) });
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, dirs.length || 1) }, worker));
    found.sort((a, b) => a.name.localeCompare(b.name));
    return { root: r, found };
  }

  /** Dispatch's scan results, with what the registry knows about each: the registered mark, the about
   *  line, the policy, and the dependencies the person set, by path. */
  function annotate(reposList) {
    const list_ = all();
    const pathOf = new Map(list_.map((e) => [e.id, e.path]));
    for (const r of reposList) {
      const e = list_.find((x) => x.path === r.path);
      if (!e) {
        r.registered = false;
        continue;
      }
      r.registered = true;
      r.registryId = e.id;
      r.registryName = e.name;
      r.about = e.about || '';
      r.policy = e.policy;
      if (e.notes) r.notes = e.notes;
      const extra = (e.deps || []).map((d) => pathOf.get(d)).filter((p) => p && p !== r.path);
      if (extra.length) r.deps = [...new Set([...(r.deps || []), ...extra])].sort();
    }
    return reposList;
  }

  return { events, list, get, containing, policyFor, defaultsFor, notesFor, resolve, nearest, promptFor, save, remove, register, refresh, scan, annotate };
}

export { joinNotes } from './dispatch_core.js';

// ---------------------------------------------------------------- the server's instance
import { store } from './config.js';
import * as git from './git.js';
import { expandHome, log, slash } from './util.js';

export const repos = createRegistry({ store, git, log, slash, expandHome });
export const events = repos.events;
