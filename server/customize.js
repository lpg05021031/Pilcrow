// Skills and MCP servers: what Claude Code has on disk, and the few edits Pilcrow makes to it.
//
// Skills are folders with a SKILL.md (frontmatter: name, description). Claude Code reads them from
// ~/.claude/skills (personal), <project>/.claude/skills (project) and installed plugins. Pilcrow
// lists, creates, imports and removes the first two kinds; plugin skills are shown read-only.
//
// MCP servers live in Claude Code's own config: ~/.claude.json (`mcpServers` = user scope,
// `projects[cwd].mcpServers` = local scope) and <project>/.mcp.json (project scope). Pilcrow reads
// those files directly and makes changes through `claude mcp add-json` / `claude mcp remove`, so the
// file formats stay whatever the installed CLI expects.
import fs from 'node:fs';
import path from 'node:path';
import { HOME, IS_WIN, slash, expandHome, claudeConfigDir, cleanEnv, run, readJson, log } from './util.js';

const SKILL_NAME_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

// ------------------------------------------------------------------ skills

function parseFrontmatter(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const out = {};
  if (!m) return out;
  for (const line of m[1].split(/\r?\n/)) {
    const kv = line.match(/^([A-Za-z][\w-]*):\s*(.*)$/);
    if (!kv) continue;
    let v = kv[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[kv[1]] = v;
  }
  return out;
}

function readSkillDir(dir, scope, extra = {}) {
  const file = path.join(dir, 'SKILL.md');
  let st;
  try {
    st = fs.statSync(file);
  } catch {
    return null;
  }
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    /* unreadable */
  }
  const fm = parseFrontmatter(text);
  let files = 0;
  let hasScripts = false;
  try {
    const walk = (d, depth) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        if (e.name.startsWith('.')) continue;
        if (e.isDirectory()) {
          if (/^(scripts|bin)$/i.test(e.name)) hasScripts = true;
          if (depth < 3) walk(path.join(d, e.name), depth + 1);
        } else files++;
      }
    };
    walk(dir, 0);
  } catch {
    /* ignore */
  }
  return {
    name: fm.name || path.basename(dir),
    dirName: path.basename(dir),
    description: fm.description || '',
    scope,
    dir: slash(dir),
    file: slash(file),
    mtime: st.mtimeMs,
    files,
    hasScripts,
    body: text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '').trim().slice(0, 400),
    ...extra,
  };
}

function listSkillDirs(root, scope, extra) {
  const out = [];
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    if (!e.isDirectory() && !e.isSymbolicLink()) continue;
    const s = readSkillDir(path.join(root, e.name), scope, extra);
    if (s) out.push(s);
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export function skillRoots(cwd) {
  return {
    user: slash(path.join(claudeConfigDir(), 'skills')),
    project: cwd ? slash(path.join(cwd, '.claude', 'skills')) : null,
  };
}

/** Plugin skills: best effort over the plugin cache; shown read-only. */
function pluginSkills() {
  const out = [];
  const pluginsDir = path.join(claudeConfigDir(), 'plugins');
  const seen = new Set();
  const visit = (dir, depth) => {
    if (depth > 5) return;
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (!e.isDirectory() || e.name.startsWith('.') || e.name === 'node_modules') continue;
      const full = path.join(dir, e.name);
      if (e.name === 'skills') {
        for (const s of listSkillDirs(full, 'plugin', { plugin: path.basename(dir) })) {
          if (seen.has(s.dir)) continue;
          seen.add(s.dir);
          out.push(s);
        }
      } else visit(full, depth + 1);
    }
  };
  visit(pluginsDir, 0);
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export function listSkills({ cwd } = {}) {
  const roots = skillRoots(cwd);
  return {
    roots,
    user: listSkillDirs(roots.user, 'user'),
    project: roots.project ? listSkillDirs(roots.project, 'project') : [],
    plugin: pluginSkills(),
  };
}

function assertManagedSkillDir(dir, cwd) {
  const roots = skillRoots(cwd);
  const d = slash(path.resolve(dir));
  const under = (root) => root && d.startsWith(root + '/') && d.length > root.length + 1 && !d.slice(root.length + 1).includes('/');
  if (!under(roots.user) && !under(roots.project)) throw new Error('Only skills in ~/.claude/skills or the project’s .claude/skills can be changed here');
  return d;
}

export function createSkill({ scope, cwd, name, description }) {
  const roots = skillRoots(cwd);
  const root = scope === 'project' ? roots.project : roots.user;
  if (!root) throw new Error('Open a session in a project folder to add a project skill');
  const slug = String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!SKILL_NAME_RE.test(slug)) throw new Error('Use letters, numbers and hyphens for the name');
  const dir = path.join(root, slug);
  if (fs.existsSync(dir)) throw new Error(`A skill named ${slug} already exists`);
  fs.mkdirSync(dir, { recursive: true });
  const desc = String(description || '').trim() || `Use this skill when the task involves ${slug.replace(/-/g, ' ')}.`;
  const file = path.join(dir, 'SKILL.md');
  fs.writeFileSync(
    file,
    `---\nname: ${slug}\ndescription: ${desc.replace(/\n/g, ' ')}\n---\n\n# ${slug.replace(/-/g, ' ')}\n\nWrite the instructions Claude should follow when this skill applies.\n\n## Steps\n\n1. \n2. \n\n## Notes\n\n- Keep the description above precise: Claude uses it to decide when to load this skill.\n- Put helper scripts in a \`scripts/\` folder next to this file and refer to them by relative path.\n`,
  );
  return readSkillDir(dir, scope === 'project' ? 'project' : 'user');
}

export function removeSkill({ dir, cwd }) {
  const d = assertManagedSkillDir(dir, cwd);
  if (!fs.existsSync(path.join(d, 'SKILL.md'))) throw new Error('Not a skill folder');
  fs.rmSync(d, { recursive: true, force: true });
  return true;
}

export function importSkill({ scope, cwd, from, name }) {
  const src = expandHome(from);
  if (!src || !fs.existsSync(path.join(src, 'SKILL.md'))) throw new Error('That folder has no SKILL.md');
  const roots = skillRoots(cwd);
  const root = scope === 'project' ? roots.project : roots.user;
  if (!root) throw new Error('Open a session in a project folder to add a project skill');
  const fm = parseFrontmatter(fs.readFileSync(path.join(src, 'SKILL.md'), 'utf8'));
  const slug = String(name || fm.name || path.basename(src))
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!SKILL_NAME_RE.test(slug)) throw new Error('Could not derive a valid skill name; pass one');
  const dest = path.join(root, slug);
  if (fs.existsSync(dest)) throw new Error(`A skill named ${slug} already exists`);
  fs.mkdirSync(root, { recursive: true });
  fs.cpSync(src, dest, { recursive: true, dereference: true, filter: (p) => !/(^|[\\/])(\.git|node_modules)([\\/]|$)/.test(p) });
  return readSkillDir(dest, scope === 'project' ? 'project' : 'user');
}

// ------------------------------------------------------------------ MCP servers

const CLAUDE_JSON = () => (process.env.CLAUDE_CONFIG_DIR ? path.join(process.env.CLAUDE_CONFIG_DIR, '.claude.json') : path.join(HOME, '.claude.json'));

function projectKey(cwd) {
  // Claude Code keys projects by their absolute path as the OS writes it.
  return IS_WIN ? cwd.replace(/\//g, '\\') : cwd;
}

function summarizeConfig(cfg = {}) {
  const type = cfg.type || (cfg.url ? (cfg.url.includes('/sse') ? 'sse' : 'http') : 'stdio');
  if (type === 'stdio') return { type, command: cfg.command || '', args: cfg.args || [], env: cfg.env || {} };
  return { type, url: cfg.url || '', headers: cfg.headers || {} };
}

export function listMcp({ cwd } = {}) {
  const out = [];
  const file = CLAUDE_JSON();
  const root = readJson(file, {});
  const push = (name, cfg, scope, source) => out.push({ name, scope, source: slash(source), config: summarizeConfig(cfg), raw: cfg });
  for (const [name, cfg] of Object.entries(root.mcpServers || {})) push(name, cfg, 'user', file);
  if (cwd) {
    const proj = root.projects?.[projectKey(cwd)] || root.projects?.[cwd] || {};
    for (const [name, cfg] of Object.entries(proj.mcpServers || {})) push(name, cfg, 'local', file);
    const mcpJson = path.join(cwd, '.mcp.json');
    const pj = readJson(mcpJson, null);
    if (pj) {
      const disabled = new Set(proj.disabledMcpjsonServers || []);
      const enabled = new Set(proj.enabledMcpjsonServers || []);
      for (const [name, cfg] of Object.entries(pj.mcpServers || {})) out.push({ name, scope: 'project', source: slash(mcpJson), config: summarizeConfig(cfg), raw: cfg, approved: enabled.has(name) ? true : disabled.has(name) ? false : null });
    }
  }
  return { file: slash(file), servers: out };
}

async function claudeMcp(bin, args, cwd) {
  if (!bin) throw new Error('Claude Code executable not found');
  const r = await run(bin, ['mcp', ...args], { cwd: cwd || HOME, env: cleanEnv({ CLAUDE_CODE_ENTRYPOINT: 'pilcrow' }), timeout: 30_000 });
  if (r.code !== 0) {
    const msg = (r.stderr || r.stdout || '').trim().split('\n').filter(Boolean).pop() || `claude mcp ${args[0]} failed (${r.code})`;
    throw new Error(msg.replace(/^error:\s*/i, ''));
  }
  return (r.stdout || '').trim();
}

/** Build the JSON Claude Code expects from the form Pilcrow shows. */
export function mcpConfigFromForm(f) {
  const type = f.type || 'http';
  if (type === 'stdio') {
    const command = String(f.command || '').trim();
    if (!command) throw new Error('A command is required');
    const args = Array.isArray(f.args) ? f.args : String(f.args || '').trim() ? splitArgs(String(f.args)) : [];
    const env = parsePairs(f.env);
    return { type: 'stdio', command, ...(args.length ? { args } : {}), ...(Object.keys(env).length ? { env } : {}) };
  }
  const url = String(f.url || '').trim();
  if (!/^https?:\/\//i.test(url)) throw new Error('The URL must start with http:// or https://');
  const headers = parsePairs(f.headers);
  return { type, url, ...(Object.keys(headers).length ? { headers } : {}) };
}

function splitArgs(s) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(s))) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

function parsePairs(v) {
  if (!v) return {};
  if (typeof v === 'object' && !Array.isArray(v)) return v;
  const out = {};
  for (const line of String(v).split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    const i = t.search(/[=:]/);
    if (i <= 0) continue;
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  return out;
}

export async function addMcp({ bin, scope = 'user', cwd, name, config }) {
  const n = String(name || '').trim();
  if (!/^[A-Za-z0-9][\w.-]{0,63}$/.test(n)) throw new Error('Server names use letters, numbers, dots, dashes and underscores');
  if (scope !== 'user' && !cwd) throw new Error('Project and local servers need an open session folder');
  const json = JSON.stringify(config);
  await claudeMcp(bin, ['add-json', '-s', scope, n, json], scope === 'user' ? HOME : cwd);
  log('mcp added', n, scope);
  return listMcp({ cwd });
}

export async function removeMcp({ bin, scope = 'user', cwd, name }) {
  await claudeMcp(bin, ['remove', '-s', scope, name], scope === 'user' ? HOME : cwd);
  log('mcp removed', name, scope);
  return listMcp({ cwd });
}

/** Approve or reject a project's .mcp.json server for this user (what Claude Code asks on first use). */
export function approveProjectMcp({ cwd, name, approved }) {
  const file = CLAUDE_JSON();
  const root = readJson(file, {});
  root.projects = root.projects || {};
  const key = root.projects[cwd] ? cwd : projectKey(cwd);
  const proj = (root.projects[key] = root.projects[key] || {});
  const en = new Set(proj.enabledMcpjsonServers || []);
  const dis = new Set(proj.disabledMcpjsonServers || []);
  en.delete(name);
  dis.delete(name);
  (approved ? en : dis).add(name);
  proj.enabledMcpjsonServers = [...en];
  proj.disabledMcpjsonServers = [...dis];
  fs.writeFileSync(file, JSON.stringify(root, null, 2));
  return listMcp({ cwd });
}
