// Pure helpers over folders and changes that Dispatch, helpers and the repositories registry share:
// the repositories under a folder, what a manifest declares, the key one permission answer serves,
// the same hunk across runs, and a brief's notes. No state and no collaborators, so any module can
// use them without pulling in a state machine.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readJson } from './util.js';

const SKIP_DIRS = new Set(['node_modules', 'vendor', 'target', 'dist', 'build', '.git', '.hg', '.svn', '__pycache__', '.venv', 'venv']);

/** Walk a folder for repositories: stop at the first `.git` on a path; skip build and dependency dirs. */
export function findRepos(root, depth = 3) {
  const out = [];
  const seen = new Set();
  const walk = (dir, d) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    if (entries.some((e) => e.name === '.git')) {
      const real = fs.realpathSync.native ? fs.realpathSync.native(dir) : dir;
      if (!seen.has(real)) {
        seen.add(real);
        out.push(dir);
      }
      return;
    }
    if (d >= depth) return;
    for (const e of entries) {
      if (!e.isDirectory() || e.name.startsWith('.') || SKIP_DIRS.has(e.name)) continue;
      walk(path.join(dir, e.name), d + 1);
    }
  };
  walk(root, 0);
  return out.sort();
}

/** Registry notes and a target's own, as one `{{notes}}` for a brief. */
export function joinNotes(...parts) {
  const clean = parts.map((p) => String(p || '').trim()).filter(Boolean);
  // a target's notes that begin as the registry's (the picker prefills them) are not said twice
  const kept = clean.filter((p, i) => !clean.some((q, j) => j !== i && q !== p && q.includes(p)));
  return [...new Set(kept)].join('\n\n');
}

/** The key two requests share when one answer should serve both: the tool and its normalized input. */
export function ruleKey(req, worktreePath = '') {
  const tool = String(req?.toolName || '');
  const input = req?.input || {};
  const rel = (p) => {
    let s = String(p || '').replace(/\\/g, '/');
    const root = String(worktreePath || '').replace(/\\/g, '/').replace(/\/$/, '');
    if (root && s.startsWith(root + '/')) s = s.slice(root.length + 1);
    return s;
  };
  if (tool === 'Bash' || tool === 'PowerShell') return `${tool}:${String(input.command || '').replace(/\s+/g, ' ').trim()}`;
  if (['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Read'].includes(tool)) return `${tool}:${rel(input.file_path || input.notebook_path)}`;
  if (tool === 'WebFetch') {
    try {
      return `${tool}:${new URL(String(input.url)).host}`;
    } catch {
      return `${tool}:${String(input.url || '')}`;
    }
  }
  const norm = (v) => {
    if (typeof v === 'string') return rel(v);
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, norm(v[k])]));
    return v;
  };
  return `${tool}:${JSON.stringify(norm(input))}`;
}

export function ruleLabel(req, worktreePath = '') {
  const key = ruleKey(req, worktreePath);
  const [tool, ...rest] = key.split(':');
  const what = rest.join(':');
  const short = what.length > 72 ? what.slice(0, 69) + '…' : what;
  return `${req?.displayName || tool}${short ? ': ' + short : ''}`;
}

/** Changes across runs: the same hunk in several repositories is one item with a count. */
export function groupHunks(diffs) {
  // diffs: [{ runId, name, files: [{ path, add, del, hunks: [{ lines: [{t, s}] }] }] }]
  const groups = new Map();
  const runs = [];
  for (const d of diffs) {
    let add = 0;
    let del = 0;
    const files = [];
    for (const f of d.files || []) {
      const fa = Number(f.additions ?? f.add ?? 0);
      const fd = Number(f.deletions ?? f.del ?? 0);
      add += fa;
      del += fd;
      files.push({ path: f.path, add: fa, del: fd });
      // a file too large to show has no hunks to compare: it is never "the same change" as another
      const body = f.truncated
        ? `\0${d.runId}`
        : (f.hunks || [])
            .flatMap((h) => h.lines || [])
            .filter((l) => l.t === '+' || l.t === '-')
            .map((l) => l.t + l.s)
            .join('\n');
      const sig = crypto.createHash('sha1').update(f.path + '\n' + body).digest('hex').slice(0, 16);
      const g = groups.get(sig) || { sig, path: f.path, runs: [], add: fa, del: fd, preview: f.truncated ? [] : body.split('\n').slice(0, 8) };
      g.runs.push(d.name);
      groups.set(sig, g);
    }
    runs.push({ runId: d.runId, name: d.name, files, add, del });
  }
  const all = [...groups.values()];
  const same = all.filter((g) => g.runs.length > 1).sort((a, b) => b.runs.length - a.runs.length || a.path.localeCompare(b.path));
  const onlyOne = all.filter((g) => g.runs.length === 1);
  const differ = [];
  for (const g of onlyOne) {
    let e = differ.find((x) => x.name === g.runs[0]);
    if (!e) differ.push((e = { name: g.runs[0], files: [] }));
    e.files.push({ path: g.path, add: g.add, del: g.del });
  }
  return { runs, same, differ, files: runs.reduce((n, r) => n + r.files.length, 0) };
}

/** Local dependencies a manifest declares: package.json file:/link:/workspace:, go.mod replace, Cargo path. */
export function manifestOf(dir) {
  /** @type {{ name: string | null, kind: string | null, localDeps: string[], depNames: string[] }} */
  const out = { name: null, kind: null, localDeps: [], depNames: [] };
  const pkg = readJson(path.join(dir, 'package.json'), null);
  if (pkg && typeof pkg === 'object') {
    out.kind = 'node';
    out.name = typeof pkg.name === 'string' ? pkg.name : null;
    for (const field of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
      for (const [name, spec] of Object.entries(pkg[field] || {})) {
        out.depNames.push(name);
        const m = /^(?:file|link):(.+)$/.exec(String(spec));
        if (m) out.localDeps.push(path.resolve(dir, m[1]));
      }
    }
  }
  const gomod = path.join(dir, 'go.mod');
  if (fs.existsSync(gomod)) {
    out.kind = out.kind || 'go';
    const text = fs.readFileSync(gomod, 'utf8');
    const mod = /^module\s+(\S+)/m.exec(text);
    if (mod && !out.name) out.name = mod[1];
    for (const m of text.matchAll(/^\s*(?:replace\s+)?(\S+)(?:\s+\S+)?\s*=>\s*(\.{1,2}\/\S*|\/\S+)/gm)) {
      out.depNames.push(m[1]);
      out.localDeps.push(path.resolve(dir, m[2]));
    }
    for (const m of text.matchAll(/^\s+(\S+)\s+v[\d.]+/gm)) out.depNames.push(m[1]);
  }
  const cargo = path.join(dir, 'Cargo.toml');
  if (fs.existsSync(cargo)) {
    out.kind = out.kind || 'rust';
    const text = fs.readFileSync(cargo, 'utf8');
    const nm = /^\s*name\s*=\s*"([^"]+)"/m.exec(text);
    if (nm && !out.name) out.name = nm[1];
    for (const m of text.matchAll(/^\s*([A-Za-z0-9_-]+)\s*=\s*\{[^}\n]*path\s*=\s*"([^"]+)"/gm)) {
      out.depNames.push(m[1]);
      out.localDeps.push(path.resolve(dir, m[2]));
    }
  }
  if (!out.kind && fs.existsSync(path.join(dir, 'pyproject.toml'))) out.kind = 'python';
  return out;
}
