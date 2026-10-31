// Pilcrow server: serves the UI, speaks JSON-RPC over a WebSocket and owns the Claude Code sessions.
// Usage: node server/main.js [--shell=chrome|browser|none] [--port=N]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { WebSocketServer } from 'ws';
import { resolveSettings } from '@anthropic-ai/claude-agent-sdk';
import { store, CONFIG_DIR } from './config.js';
import { resolveClaude } from './claude.js';
import { gatewayModels } from './models.js';
import { sessions, bus, costLog } from './sessions.js';
import * as history from './history.js';
import * as git from './git.js';
import * as files from './files.js';
import * as term from './terminal.js';
import * as customize from './customize.js';
import * as snapshots from './snapshots.js';
import * as schedules from './schedules.js';
import * as dispatch from './dispatch.js';
import * as memory from './memory.js';
import { which, log, xdg, HOME, IS_ROOT, IS_WIN, slash, readJson, writeJson, debounce, expandHome, run, privateDir } from './util.js';

// Old environment variable names keep working after the rename.
for (const k of Object.keys(process.env)) if (k.startsWith('RETICLE_') && !('PILCROW_' + k.slice(8) in process.env)) process.env['PILCROW_' + k.slice(8)] = process.env[k];

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, 'web', 'dist');
const VERSION = readJson(path.join(ROOT, 'package.json'), {}).version || '1.0.0';
const PREFERRED_PORT = 47117;
const LOCK = path.join(xdg('state'), 'instance.json');

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon', '.wasm': 'application/wasm', '.map': 'application/json' };

function parseArgs(argv) {
  const out = {};
  for (const a of argv) {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    if (m) out[m[1]] = m[2] ?? true;
  }
  return out;
}

async function gatewayInfo(cwd) {
  try {
    const r = await resolveSettings({ cwd: cwd || '/' });
    const env = r.effective?.env || {};
    const src = r.provenance?.env;
    return {
      baseUrl: env.ANTHROPIC_BASE_URL || process.env.ANTHROPIC_BASE_URL || null,
      hasToken: Boolean(env.ANTHROPIC_AUTH_TOKEN || env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN || process.env.ANTHROPIC_API_KEY || r.effective?.apiKeyHelper),
      provider: env.CLAUDE_CODE_USE_BEDROCK ? 'bedrock' : env.CLAUDE_CODE_USE_VERTEX ? 'vertex' : env.CLAUDE_CODE_USE_FOUNDRY ? 'foundry' : 'anthropic',
      source: src?.source || null,
      model: r.effective?.model || null,
      // Signed in with `claude login` (no key in settings, credentials stored by the CLI itself).
      loggedIn: fs.existsSync(path.join(HOME, '.claude', '.credentials.json')),
    };
  } catch (e) {
    return { error: e.message };
  }
}

let cachedName;
/** The name people go by: git's user.name when set, otherwise the login name. */
async function displayName() {
  if (cachedName !== undefined) return cachedName;
  const r = await run('git', ['config', '--global', 'user.name'], { timeout: 3000 });
  cachedName = r.stdout.trim() || os.userInfo().username;
  return cachedName;
}

function openExternal(target) {
  if (IS_WIN) {
    // URLs go to the default browser; folders and files open in Explorer.
    const isUrl = /^[a-z][a-z0-9+.-]*:\/\//i.test(target) || target.startsWith('mailto:');
    const [cmd, args] = isUrl ? ['rundll32.exe', ['url.dll,FileProtocolHandler', target]] : ['explorer.exe', [target.replace(/\//g, '\\')]];
    spawn(cmd, args, { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    return true;
  }
  const opener = which('xdg-open') || which('gio') || which('sensible-browser');
  if (!opener) return false;
  const args = opener.endsWith('gio') ? ['open', target] : [target];
  spawn(opener, args, { detached: true, stdio: 'ignore' }).unref();
  return true;
}

export async function start({ shell = 'none', port, quiet = false, onQuit } = {}) {
  privateDir(xdg('config'));
  privateDir(xdg('state'));
  const clients = new Set();
  let totalConnections = 0;

  const send = (ws, obj) => {
    if (ws.readyState === 1) ws.send(JSON.stringify(obj));
  };
  const broadcast = (e, d) => {
    const raw = JSON.stringify({ t: 'ev', e, d });
    for (const ws of clients) if (ws.readyState === 1) ws.send(raw);
  };

  const listChanged = debounce(() => broadcast('sessions.changed', {}), 150);
  bus.on('msgs', (d) => broadcast('session.msgs', d));
  bus.on('state', (d) => broadcast('session.state', d));
  bus.on('permission', (d) => broadcast('session.permission', d));
  bus.on('permission-resolved', (d) => broadcast('session.permissionResolved', d));
  bus.on('list-changed', listChanged);
  snapshots.events.on('step', (d) => broadcast('timeline.step', d));
  schedules.events.on('schedules', () => broadcast('schedules.changed', {}));
  schedules.events.on('inbox', () => broadcast('inbox.changed', schedules.inboxList().unread));
  schedules.start();
  dispatch.events.on('changed', (d) => broadcast('dispatch.changed', d));
  dispatch.events.on('playbooks', () => broadcast('dispatch.playbooks', {}));
  dispatch.events.on('finished', (d) => d.scheduleId && schedules.noteDispatchFinished(d.scheduleId, d.status));
  schedules.setDispatchRunner((playbookId, opts) => dispatch.dispatch.startFromPlaybook(playbookId, opts));
  dispatch.start();
  memory.events.on('changed', (d) => broadcast('memory.changed', d));
  term.terminals.on('data', (d) => broadcast('term.data', d));
  term.terminals.on('exit', (d) => broadcast('term.exit', d));

  const S = (id) => sessions.get(id);
  const methods = {
    'app.info': async () => {
      const cfg = store.getConfig();
      return {
        version: VERSION,
        home: HOME,
        user: os.userInfo().username,
        displayName: await displayName(),
        hostname: os.hostname(),
        platform: process.platform,
        node: process.version,
        shell,
        isRoot: IS_ROOT,
        configDir: slash(CONFIG_DIR),
        claude: await resolveClaude(cfg.claudeBinary),
        terminal: term.capabilities(),
        git: Boolean(which('git')),
        gateway: await gatewayInfo(),
      };
    },
    'app.quit': () => {
      setTimeout(() => (onQuit ? onQuit() : (stop(), process.exit(0))), 50);
      return true;
    },
    'config.get': () => store.getConfig(),
    'config.set': (p) => store.setConfig(p),

    'sessions.list': () => sessions.list(),
    'sessions.refresh': async () => {
      history.invalidateList();
      return sessions.list();
    },
    'session.open': async ({ id }) => ({ ...(await (await S(id)).snapshot()), meta: store.getMeta(id) }),
    'session.create': (p) => sessions.create(p),
    'session.send': async ({ id, ...rest }) => (await S(id)).send(rest),
    'session.interrupt': async ({ id }) => (await S(id)).interrupt(),
    'session.setCwd': async ({ id, cwd }) => (await S(id)).setCwd(cwd),
    'session.queue.remove': async ({ id, uuid: u }) => (await S(id)).removeHeld(u),
    'session.queue.update': async ({ id, uuid: u, text }) => (await S(id)).updateHeld(u, text),
    'session.queue.sendNow': async ({ id, uuid: u }) => (await S(id)).sendHeldNow(u),
    'session.setMode': async ({ id, mode }) => (await S(id)).setMode(mode),
    'session.setModel': async ({ id, model }) => (await S(id)).setModel(model),
    'session.setEffort': async ({ id, effort }) => (await S(id)).setEffort(effort),
    'session.permission': async ({ id, requestId, decision }) => (await S(id)).respond(requestId, decision),
    'session.rename': ({ id, title }) => sessions.rename(id, title),
    'session.delete': ({ id, removeWorktree }) => sessions.remove(id, { removeWorktree }),
    'session.archive': ({ id, archived }) => {
      store.setMeta(id, { archived: archived || null });
      listChanged();
    },
    'session.pin': ({ id, pinned }) => {
      store.setMeta(id, { pinned: pinned || null });
      listChanged();
    },
    'session.fork': ({ id, upToMessageId, title }) => sessions.fork(id, { upToMessageId, title }),
    'session.close': async ({ id }) => sessions.find(id)?.close(),
    'session.contextUsage': async ({ id }) => (await S(id)).contextUsage(),
    'session.rewind': async ({ id, messageId, dryRun }) => (await S(id)).rewind(messageId, dryRun !== false),
    'session.mcpStatus': async ({ id }) => (await S(id)).mcpStatus(),
    'session.mcp.reconnect': async ({ id, name }) => (await S(id)).mcpReconnect(name),
    'session.mcp.toggle': async ({ id, name, enabled }) => (await S(id)).mcpToggle(name, Boolean(enabled)),
    'session.mcp.auth': async ({ id, name }) => (await S(id)).mcpAuth(name),
    'session.mcp.authCallback': async ({ id, name, url }) => (await S(id)).mcpAuthCallback(name, url),
    'session.mcp.clearAuth': async ({ id, name }) => (await S(id)).mcpClearAuth(name),
    'session.reloadSkills': async ({ id }) => (await S(id)).reloadSkills(),
    // ---- skills & MCP configuration (Claude Code's own files)
    'skills.list': ({ cwd }) => customize.listSkills({ cwd: cwd ? expandHome(cwd) : undefined }),
    'skills.create': ({ scope, cwd, name, description }) => customize.createSkill({ scope, cwd: cwd ? expandHome(cwd) : undefined, name, description }),
    'skills.remove': ({ dir, cwd }) => customize.removeSkill({ dir: expandHome(dir), cwd: cwd ? expandHome(cwd) : undefined }),
    'skills.import': ({ scope, cwd, from, name }) => customize.importSkill({ scope, cwd: cwd ? expandHome(cwd) : undefined, from, name }),
    'skills.reloadAll': async () => {
      let n = 0;
      for (const s of sessions.live()) {
        await s.reloadSkills().catch(() => null);
        n++;
      }
      return n;
    },
    'mcp.list': ({ cwd }) => customize.listMcp({ cwd: cwd ? expandHome(cwd) : undefined }),
    'mcp.add': async ({ scope, cwd, name, form }) => {
      const claude = await resolveClaude(store.getConfig().claudeBinary);
      return customize.addMcp({ bin: claude.path, scope, cwd: cwd ? expandHome(cwd) : undefined, name, config: customize.mcpConfigFromForm(form) });
    },
    'mcp.remove': async ({ scope, cwd, name }) => {
      const claude = await resolveClaude(store.getConfig().claudeBinary);
      return customize.removeMcp({ bin: claude.path, scope, cwd: cwd ? expandHome(cwd) : undefined, name });
    },
    'mcp.approve': ({ cwd, name, approved }) => customize.approveProjectMcp({ cwd: expandHome(cwd), name, approved: Boolean(approved) }),
    // ---- project memory and who-touched-what
    'memory.list': ({ cwd }) => memory.list(expandHome(cwd)),
    'memory.add': ({ cwd, text }) => memory.add(expandHome(cwd), { text }),
    'memory.update': ({ cwd, id, text, pinned }) => memory.update(expandHome(cwd), id, { text, pinned }),
    'memory.remove': ({ cwd, id }) => memory.remove(expandHome(cwd), id),
    'memory.clear': ({ cwd }) => memory.clear(expandHome(cwd)),
    'memory.distill': async ({ id, force }) => {
      const s = await S(id);
      await s.loadHistory();
      return memory.distill(s, { force: Boolean(force) });
    },
    'project.fileHistory': async ({ cwd }) => {
      const list = await sessions.list();
      const titles = Object.fromEntries(list.map((s) => [s.id, { title: s.title, firstPrompt: s.firstPrompt || null, lastModified: s.lastModified }]));
      const byPath = {};
      for (const st of snapshots.folderSteps(expandHome(cwd))) {
        for (const f of st.files) {
          (byPath[f.path] ||= []).push({ sessionId: st.sessionId, title: titles[st.sessionId]?.title || 'Session', about: titles[st.sessionId]?.firstPrompt || null, at: st.at, n: st.n, toolUseId: st.toolUseId, kind: st.kind, add: f.add, del: f.del });
        }
      }
      for (const k of Object.keys(byPath)) byPath[k].sort((a, b) => b.at - a.at);
      return byPath;
    },
    // ---- scheduled sessions and their inbox
    'schedules.list': () => schedules.list(),
    'schedules.save': (p) => schedules.save({ ...p, cwd: p.cwd ? expandHome(p.cwd) : p.cwd }),
    'schedules.remove': ({ id }) => schedules.remove(id),
    'schedules.toggle': ({ id, enabled }) => schedules.toggle(id, enabled),
    'schedules.runNow': ({ id }) => schedules.runNow(id, { manual: true }),
    'schedules.preview': ({ cadence }) => {
      try {
        return { nextRunAt: schedules.nextRun(cadence), label: schedules.describeCadence(cadence) };
      } catch (e) {
        return { nextRunAt: null, label: '', error: e.message };
      }
    },
    'inbox.list': () => schedules.inboxList(),
    'inbox.markRead': ({ ids }) => schedules.markRead(ids || null),
    'inbox.remove': ({ id }) => schedules.removeItem(id),
    // ---- dispatch: one job across many repositories
    'workspace.scan': ({ root, depth, force }) => dispatch.dispatch.scan({ root: expandHome(root), depth, force: Boolean(force) }),
    'dispatch.list': () => dispatch.dispatch.list(),
    'dispatch.get': ({ id }) => dispatch.dispatch.get(id),
    'dispatch.save': (p) => dispatch.dispatch.save({ ...p, root: p.root ? expandHome(p.root) : p.root, targets: (p.targets || []).map((t) => ({ ...t, path: expandHome(t.path) })) }),
    'dispatch.remove': ({ id, removeWorktrees }) => dispatch.dispatch.remove(id, { removeWorktrees: Boolean(removeWorktrees) }),
    'dispatch.plan': (p) => dispatch.dispatch.plan(p.id && !p.targets ? p.id : { ...p, root: p.root ? expandHome(p.root) : p.root, targets: (p.targets || []).map((t) => ({ ...t, path: expandHome(t.path) })) }),
    'dispatch.start': ({ id, wave }) => dispatch.dispatch.start(id, { wave }),
    'dispatch.continue': ({ id, brief, learn }) => dispatch.dispatch.continue(id, { brief, learn }),
    'dispatch.pause': ({ id }) => dispatch.dispatch.pause(id),
    'dispatch.resume': ({ id }) => dispatch.dispatch.resume(id),
    'dispatch.stop': ({ id, runId }) => dispatch.dispatch.stop(id, { runId }),
    'dispatch.retry': ({ id, runId, brief }) => dispatch.dispatch.retry(id, runId, { brief }),
    'dispatch.skip': ({ id, runId }) => dispatch.dispatch.skip(id, runId),
    'dispatch.allow': ({ id, key, requestId }) => dispatch.dispatch.allow(id, key, requestId),
    'dispatch.review': ({ id }) => dispatch.dispatch.review(id),
    'dispatch.finish': ({ id, runId, action, message, pr }) => dispatch.dispatch.finish(id, { runId, action, message, pr }),
    'dispatch.export': ({ id }) => dispatch.dispatch.exportMarkdown(id),
    'dispatch.playbooks': () => dispatch.dispatch.playbooks(),
    'dispatch.savePlaybook': (p) => dispatch.dispatch.savePlaybook({ ...p, root: p.root ? expandHome(p.root) : p.root, targets: p.targets ? p.targets.map((t) => ({ ...t, path: expandHome(t.path) })) : p.targets }),
    'dispatch.removePlaybook': ({ id }) => dispatch.dispatch.removePlaybook(id),
    'dispatch.startFromPlaybook': ({ id, name, wave, targets }) => dispatch.dispatch.startFromPlaybook(id, { name, wave, targets }),
    // ---- control room: every live session at a glance, plus spend
    'fleet.state': async () => {
      const list = await sessions.list();
      const live = Object.fromEntries(sessions.live().map((s) => [s.id, s.state()]));
      const dayStart = new Date();
      dayStart.setHours(0, 0, 0, 0);
      let todayCost = 0;
      let todaySessions = 0;
      const perSession = [];
      for (const s of list) {
        const m = store.getMeta(s.id) || {};
        if ((s.lastModified || 0) >= dayStart.getTime() && m.costUsd) {
          todayCost += m.costUsd;
          todaySessions++;
          perSession.push({ id: s.id, title: s.title, cost: m.costUsd });
        }
      }
      return { live, today: { cost: todayCost, sessions: todaySessions, perSession: perSession.sort((a, b) => b.cost - a.cost).slice(0, 8) }, spend: costLog.since(Date.now() - 3600e3) };
    },
    // ---- time machine
    'timeline.steps': async ({ id }) => snapshots.steps(await S(id)),
    'timeline.diff': async ({ id, from, to }) => snapshots.diff(await S(id), { from, to }),
    'timeline.stepDiff': async ({ id, n }) => snapshots.stepDiff(await S(id), n),
    'timeline.file': async ({ id, n, path: p }) => snapshots.fileAt(await S(id), { n, path: p }),
    'timeline.restore': async ({ id, n, paths }) => {
      const r = await snapshots.restore(await S(id), { n, paths });
      return r;
    },
    'session.stopTask': async ({ id, taskId }) => (await S(id)).stopTask(taskId),
    'session.subagent': async ({ id, agentId }) => history.loadSubagent(id, agentId, (await S(id)).cwd),
    'session.gateway': async ({ cwd }) => gatewayInfo(cwd),
    'models.gateway': async ({ cwd, force }) => gatewayModels(cwd, { force: Boolean(force), version: VERSION }),

    'git.info': ({ cwd }) => git.info(cwd),
    'git.diff': ({ cwd, base }) => git.diff(cwd, { base }),
    'git.branches': ({ cwd }) => git.branches(cwd),
    'git.commit': ({ cwd, message, paths }) => git.commit(cwd, { message, paths }),
    'git.discard': ({ cwd, path: p, untracked }) => git.discard(cwd, { path: p, untracked }),
    'git.log': ({ cwd, n }) => git.log(cwd, n),
    'git.worktrees': ({ cwd }) => git.listWorktrees(cwd),
    'git.removeWorktree': (p) => git.removeWorktree(p),

    'fs.list': ({ dir }) => files.listDir(expandHome(dir)),
    'fs.read': ({ path: p }) => files.readFile(expandHome(p)),
    'fs.write': ({ path: p, text, expectedMtime, force }) => files.writeFile(expandHome(p), text, { expectedMtime, force }),
    'fs.browse': ({ dir }) => files.browse(expandHome(dir)),
    'fs.index': ({ cwd, force }) => files.fileIndex(cwd, { force }),
    'fs.mkdir': ({ path: p }) => {
      fs.mkdirSync(expandHome(p), { recursive: true });
      return true;
    },
    'fs.exists': ({ path: p }) => fs.existsSync(expandHome(p)),

    'term.open': (p) => term.open(p),
    'term.input': ({ id, data }) => term.write(id, data),
    'term.resize': ({ id, cols, rows }) => term.resize(id, cols, rows),
    'term.close': ({ id }) => term.close(id),
    'term.list': () => term.list(),
    'term.snapshot': ({ id }) => term.snapshot(id),

    'shell.openExternal': ({ url }) => /^(https?|file|mailto):/i.test(url) && openExternal(url),
    'shell.openPath': ({ path: p }) => openExternal(expandHome(p)),
    'shell.which': async ({ cmd }) => which(cmd),
    'shell.run': async ({ cmd, args, cwd }) => run(cmd, args, { cwd }),
  };

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    // Only this app's own pages may talk to the server: no other website open in your browser,
    // no other localhost dev server, and no DNS-rebinding tricks.
    if (!hostOk(req) || (url.pathname.startsWith('/api/') && !(fetchSiteOk(req) && originOk(req)))) {
      res.writeHead(403).end('Forbidden');
      return;
    }
    if (url.pathname === '/api/hello') {
      res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ app: 'pilcrow', version: VERSION, pid: process.pid, busy: sessions.busy().length }));
      return;
    }
    // Files served at their own path, so an HTML document's relative stylesheets, scripts and images
    // resolve: /api/fs/home/me/site/index.html loads ./style.css from /api/fs/home/me/site/style.css.
    if (url.pathname.startsWith('/api/fs/')) {
      let p = decodeURIComponent(url.pathname.slice('/api/fs'.length));
      if (/^\/[a-zA-Z]:\//.test(p)) p = p.slice(1); // Windows drive paths arrive as /C:/…
      fs.stat(p, (err, st) => {
        if (err || !st.isFile()) {
          res.writeHead(404).end('Not found');
          return;
        }
        const ext = path.extname(p).toLowerCase();
        const type = MIME[ext] || files.mimeFor(p) || (ext === '.txt' || ext === '.md' ? 'text/plain; charset=utf-8' : ext === '.xml' ? 'application/xml' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : ext === '.gif' ? 'image/gif' : ext === '.webp' ? 'image/webp' : ext === '.mp4' ? 'video/mp4' : ext === '.pdf' ? 'application/pdf' : 'application/octet-stream');
        res.writeHead(200, { 'content-type': type, 'content-length': st.size, 'cache-control': 'no-store', 'cross-origin-resource-policy': 'same-origin', 'x-content-type-options': 'nosniff' });
        fs.createReadStream(p).pipe(res);
      });
      return;
    }
    if (url.pathname === '/api/raw') {
      const p = expandHome(url.searchParams.get('path') || '');
      fs.stat(p, (err, st) => {
        if (err || !st.isFile()) {
          res.writeHead(404).end('Not found');
          return;
        }
        res.writeHead(200, { 'content-type': files.mimeFor(p) || 'application/octet-stream', 'content-length': st.size, 'cache-control': 'no-store', 'cross-origin-resource-policy': 'same-origin' });
        fs.createReadStream(p).pipe(res);
      });
      return;
    }
    serveStatic(url.pathname, res);
  });

  function originOk(req) {
    const origin = req.headers.origin || '';
    const p = server.address()?.port;
    return !origin || origin === `http://127.0.0.1:${p}` || origin === `http://localhost:${p}`;
  }

  /** Browsers label every request with where it came from; anything not from our own page is refused. */
  function fetchSiteOk(req) {
    const site = req.headers['sec-fetch-site'];
    return !site || site === 'same-origin' || site === 'none';
  }

  function hostOk(req) {
    const host = (req.headers.host || '').toLowerCase();
    const p = server.address()?.port;
    return host === `127.0.0.1:${p}` || host === `localhost:${p}`;
  }

  function serveStatic(pathname, res) {
    let rel = decodeURIComponent(pathname).replace(/^\/+/, '');
    if (!rel) rel = 'index.html';
    let file = path.join(DIST, rel);
    if (!file.startsWith(DIST)) {
      res.writeHead(403).end();
      return;
    }
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html');
    if (!fs.existsSync(file)) {
      res.writeHead(500, { 'content-type': 'text/plain' }).end('The interface files are missing (web/dist). Run the Pilcrow installer again to restore them.');
      return;
    }
    const ext = path.extname(file);
    const headers = {
      'content-type': MIME[ext] || 'application/octet-stream',
      'cache-control': file.includes(`${path.sep}assets${path.sep}`) ? 'public, max-age=31536000, immutable' : 'no-cache',
      'x-content-type-options': 'nosniff',
    };
    if (ext === '.html') headers['x-frame-options'] = 'DENY';
    if (ext === '.html') headers['content-security-policy'] = "frame-ancestors 'none'; default-src 'self'; img-src 'self' data: blob: http://127.0.0.1:* http://localhost:*; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-src http: https:; worker-src 'self' blob:";
    res.writeHead(200, headers);
    fs.createReadStream(file).pipe(res);
  }

  const wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 * 1024 });
  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname !== '/ws' || !originOk(req) || !hostOk(req) || !fetchSiteOk(req)) {
      socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
  });

  wss.on('connection', (ws) => {
    totalConnections++;
    clients.add(ws);
    ws.on('close', () => clients.delete(ws));
    ws.on('message', async (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return;
      }
      if (msg.t !== 'req') return;
      const fn = methods[msg.m];
      if (!fn) {
        send(ws, { t: 'res', id: msg.id, ok: false, e: { message: `Unknown method ${msg.m}` } });
        return;
      }
      try {
        const r = await fn(msg.p || {});
        send(ws, { t: 'res', id: msg.id, ok: true, r: r === undefined ? null : r });
      } catch (e) {
        log('rpc error', msg.m, e);
        send(ws, { t: 'res', id: msg.id, ok: false, e: { message: e?.message || String(e) } });
      }
    });
    send(ws, { t: 'ev', e: 'hello', d: { version: VERSION } });
  });

  const listenOn = (p) =>
    new Promise((resolve, reject) => {
      const onErr = (e) => {
        server.off('listening', onOk);
        reject(e);
      };
      const onOk = () => {
        server.off('error', onErr);
        resolve();
      };
      server.once('error', onErr);
      server.once('listening', onOk);
      server.listen(p, '127.0.0.1');
    });
  const explicit = port !== undefined && port !== true && port !== '';
  try {
    await listenOn(explicit ? Number(port) : PREFERRED_PORT);
  } catch (e) {
    if (e.code !== 'EADDRINUSE') throw e;
    // A port you asked for (e.g. for an SSH tunnel) must not silently move somewhere else.
    if (explicit) throw new Error(`Port ${port} is already in use. Pick another one with --port=N.`);
    await listenOn(0);
  }
  const actualPort = server.address().port;
  const url = `http://127.0.0.1:${actualPort}/`;
  writeJson(LOCK, { pid: process.pid, port: actualPort, shell, startedAt: Date.now() });
  log(`Pilcrow ${VERSION} listening on 127.0.0.1:${actualPort} (shell=${shell})`);
  if (!quiet) console.log(`Pilcrow is running on port ${actualPort}.`);

  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    sessions.closeAll();
    term.closeAll();
    try {
      store.flush();
    } catch {
      /* ignore */
    }
    try {
      const cur = readJson(LOCK, {});
      if (cur.pid === process.pid) fs.rmSync(LOCK, { force: true });
    } catch {
      /* ignore */
    }
    for (const ws of clients) ws.close();
    server.close();
  };
  return { url, port: actualPort, stop, busy: () => sessions.busy(), connections: () => totalConnections };
}

// ---------------------------------------------------------------- launcher (non-Electron shells)

const BROWSERS = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'microsoft-edge-stable', 'brave-browser', 'vivaldi'];

/** Chrome first, then Edge (always present on Windows 10/11), then other Chromium browsers. */
function findWindowsBrowser() {
  const bases = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA].filter(Boolean);
  const rel = ['Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe', 'Chromium/Application/chrome.exe', 'BraveSoftware/Brave-Browser/Application/brave.exe'];
  for (const r of rel) {
    for (const b of bases) {
      const p = path.join(b, r);
      if (fs.existsSync(p)) return slash(p);
    }
  }
  return which('chrome') || which('msedge');
}

function findChrome() {
  if (process.env.PILCROW_CHROME) return which(process.env.PILCROW_CHROME);
  if (IS_WIN) return findWindowsBrowser();
  for (const b of BROWSERS) {
    const p = which(b);
    if (p) return p;
  }
  for (const p of ['/opt/google/chrome/chrome', '/snap/bin/chromium', '/usr/lib/chromium/chromium']) if (fs.existsSync(p)) return p;
  return null;
}

/** The Pilcrow server already running for this user, if any. */
async function existingInstance() {
  const lock = readJson(LOCK, null);
  if (!lock?.pid || !lock.port) return null;
  try {
    process.kill(lock.pid, 0);
  } catch {
    return null;
  }
  try {
    const r = await fetch(`http://127.0.0.1:${lock.port}/api/hello`, { signal: AbortSignal.timeout(1500) });
    const j = await r.json();
    if (j.app === 'pilcrow' && j.pid === lock.pid) return { ...lock, version: j.version || '?', busy: typeof j.busy === 'number' ? j.busy : null, url: `http://127.0.0.1:${lock.port}/` };
  } catch {
    /* stale lock */
  }
  return null;
}

/**
 * After an update, the previous version can still be running in the background; a new window would then
 * talk to old code. Stop it when that is safe: it reports no busy sessions, or it predates busy reporting
 * (only possible right after upgrading from 1.2.2 or older). Returns true when the old process is gone.
 */
export async function retireOutdated(existing, { force = false } = {}) {
  if (!existing || existing.version === VERSION) return false;
  if (!force && existing.busy) return false;
  try {
    process.kill(existing.pid, 'SIGTERM');
  } catch {
    return true;
  }
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 100));
    try {
      process.kill(existing.pid, 0);
    } catch {
      return true;
    }
  }
  try {
    process.kill(existing.pid, 'SIGKILL');
  } catch {
    /* already gone */
  }
  return true;
}

export { existingInstance };

/** The snap a browser binary belongs to (Ubuntu ships Firefox and Chromium as snaps), or null. */
function snapOf(bin) {
  if (!bin) return null;
  const name = path.basename(bin).split('.')[0];
  if (bin.startsWith('/snap/bin/')) return name;
  try {
    if (fs.realpathSync(bin) === '/usr/bin/snap') return name;
    // Ubuntu's /usr/bin/chromium-browser is a small script that runs /snap/bin/chromium.
    if (fs.statSync(bin).size < 16384) {
      const m = fs.readFileSync(bin, 'utf8').match(/\/snap\/bin\/([\w-]+)/);
      if (m) return m[1];
    }
  } catch {
    /* not readable: assume a normal package */
  }
  return null;
}

function openChromeWindow(chrome, url) {
  const snap = snapOf(chrome);
  const common = snap ? path.join(HOME, 'snap', snap, 'common') : null;
  // A snap browser cannot use a profile in a hidden folder; give it one inside its own snap folder.
  // On Windows the profile stays in %LOCALAPPDATA% so it never roams with the user profile.
  const profile = snap ? (fs.existsSync(common) ? privateDir(path.join(common, 'pilcrow-profile')) : null) : privateDir(path.join(xdg(IS_WIN ? 'data' : 'config'), 'chrome-profile'));
  const win = store.getConfig().window || {};
  const args = [`--app=${url}`, '--class=pilcrow', '--name=pilcrow', '--no-first-run', '--no-default-browser-check', '--disable-features=Translate,MediaRouter', `--window-size=${win.width || 1480},${win.height || 940}`];
  if (profile) args.push(`--user-data-dir=${profile}`);
  if (IS_ROOT) args.push('--no-sandbox');
  if (process.env.PILCROW_CHROME_FLAGS) args.push(...process.env.PILCROW_CHROME_FLAGS.split(' ').filter(Boolean));
  // Chromium builds without Google keys show a warning bar; these values silence it (no Google services are used).
  const env = { ...process.env, GOOGLE_API_KEY: 'no', GOOGLE_DEFAULT_CLIENT_ID: 'no', GOOGLE_DEFAULT_CLIENT_SECRET: 'no' };
  return spawn(chrome, args, { stdio: 'ignore', env });
}

// `pilcrow --doctor`: plain-text health check for the environment Pilcrow runs in.
/** Git for Windows' bash.exe, which Claude Code uses for its Bash tool on Windows. */
export function gitBash() {
  if (!IS_WIN) return null;
  const env = process.env.CLAUDE_CODE_GIT_BASH_PATH;
  if (env && fs.existsSync(env)) return slash(env);
  const git = which('git');
  const candidates = [
    git && path.join(path.dirname(path.dirname(git)), 'bin', 'bash.exe'),
    ...[process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs')].filter(Boolean).map((b) => path.join(b, 'Git', 'bin', 'bash.exe')),
  ].filter(Boolean);
  for (const c of candidates) if (fs.existsSync(c)) return slash(c);
  return null;
}

async function doctor() {
  const tty = process.stdout.isTTY;
  const paint = (c, s) => (tty ? `\x1b[${c}m${s}\x1b[0m` : s);
  const ok = (label, detail = '') => console.log(`  ${paint('32', '✓')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  const warn = (label, detail = '') => console.log(`  ${paint('33', '!')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  const bad = (label, detail = '') => console.log(`  ${paint('31', '✗')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  let failures = 0;
  console.log(paint('1', `Pilcrow ${VERSION} doctor`) + paint('2', `  (${ROOT})`));

  console.log(paint('1', '\nRuntime'));
  const major = Number(process.versions.node.split('.')[0]);
  (major >= 20 ? ok : (failures++, bad))(`Node ${process.version}`, process.execPath);
  fs.existsSync(path.join(DIST, 'index.html')) ? ok('Interface', path.join('web', 'dist')) : (failures++, bad('Interface missing', 'run the installer again (it ships the built interface)'));
  which('git') ? ok('git', which('git')) : warn('git not found', 'Changes, worktrees and commits are disabled');
  const caps = term.capabilities();
  if (IS_WIN) caps.conpty ? ok('Terminal', 'ConPTY (node-pty)') : warn('Terminal is basic (line mode)', 'node-pty was not installed; full-screen programs will not work');
  else caps.python ? ok('Terminal', `python3 PTY bridge (${caps.python})`) : caps.script ? warn('Terminal uses `script` fallback', 'install python3 for a full PTY') : warn('Terminal unavailable', 'needs python3 or util-linux script');

  console.log(paint('1', '\nClaude Code'));
  const cfg = store.getConfig();
  const claude = await resolveClaude(cfg.claudeBinary);
  if (claude.path) ok(`Claude Code ${claude.version || '(version unknown)'}`, `${claude.source}: ${claude.path}`);
  else (failures++, bad('Claude Code not found', 'reinstall Pilcrow, or install `claude` on PATH'));
  if (claude.system && claude.source === 'bundled') ok(`Also on PATH: Claude Code ${claude.system.version || ''}`.trim(), `${claude.system.path} (switch in Settings → Claude Code)`);
  if (IS_WIN) {
    const bash = gitBash();
    bash ? ok('Git Bash (for the Bash tool)', bash) : warn('No Git for Windows', 'Claude Code runs commands with PowerShell instead; install Git for Windows for the Bash tool');
  }
  const gw = await gatewayInfo(process.cwd());
  if (gw.error) warn('Could not read Claude Code settings', gw.error);
  else {
    if (gw.baseUrl) ok('API endpoint', `${gw.baseUrl}${gw.source ? ` (from ${gw.source} settings)` : ' (from environment)'}`);
    else ok('API endpoint', 'api.anthropic.com (default)');
    gw.hasToken ? ok('Credentials configured') : warn('No API key or token in settings/env', 'fine if you log in with `claude` instead');
    if (gw.model) ok('Default model', gw.model);
    const target = gw.baseUrl || 'https://api.anthropic.com';
    try {
      const r = await fetch(target, { method: 'GET', signal: AbortSignal.timeout(6000) });
      ok('Endpoint reachable', `HTTP ${r.status}`);
    } catch (e) {
      warn('Endpoint not reachable from here', e.cause?.code || e.message);
    }
    if (gw.hasToken) {
      const m = await gatewayModels(process.cwd(), { version: VERSION });
      if (m.ok) ok('Models on the gateway', `${m.models.length ? `${m.models.length}: ${m.models.slice(0, 4).map((x) => x.id).join(', ')}${m.models.length > 4 ? ', …' : ''}` : 'the list is empty'} (${m.url})`);
      else warn('Could not list the gateway models', m.error);
    }
  }

  console.log(paint('1', '\nDesktop window'));
  if (!IS_WIN) {
    const display = process.env.WAYLAND_DISPLAY ? `Wayland ${process.env.WAYLAND_DISPLAY}` : process.env.DISPLAY ? `X11 ${process.env.DISPLAY}` : null;
    display ? ok('Display', display) : warn('No display', 'use `pilcrow --server` and open the printed URL');
  }
  const electron = path.join(ROOT, 'node_modules', 'electron', 'dist', IS_WIN ? 'electron.exe' : 'electron');
  if (fs.existsSync(electron)) {
    let version = '';
    try {
      version = fs.readFileSync(path.join(ROOT, 'node_modules', 'electron', 'dist', 'version'), 'utf8').trim();
    } catch {
      /* ignore */
    }
    ok(`Electron ${version}`, electron);
    const noSandbox = fs.existsSync(path.join(xdg('state'), 'electron-no-sandbox'));
    if (noSandbox) warn('Electron runs with --no-sandbox', 'set automatically because the sandbox failed on this machine');
  } else {
    const chrome = findChrome();
    warn('Electron not downloaded', chrome ? `will use a ${path.basename(chrome)} app window instead` : 'will open in the default browser');
  }
  const chrome = findChrome();
  chrome ? ok(IS_WIN ? 'Browser window fallback' : 'Chrome/Chromium fallback', chrome) : warn(IS_WIN ? 'No Chrome or Edge found' : 'No Chrome/Chromium', 'fallback is the default browser');

  console.log(paint('1', '\nFiles'));
  ok('Config', CONFIG_DIR);
  ok('Logs', path.join(xdg('state'), 'pilcrow.log'));
  const lock = await existingInstance();
  if (lock) ok('Running instance', `pid ${lock.pid}, port ${lock.port}, ${lock.shell}`);
  console.log(failures ? paint('31', `\n${failures} problem(s) found.`) : paint('32', '\nAll essential checks passed.'));
  return failures;
}

/**
 * Used by the installer right after an update: if an older Pilcrow is still running, offer to stop it so
 * the next launch runs the new version. Busy sessions are never stopped without asking.
 */
async function restartOutdatedPrompt() {
  const existing = await existingInstance();
  if (!existing || existing.version === VERSION) return 0;
  const say = (s) => console.log(s);
  const busy = existing.busy;
  if (busy === 0) {
    await retireOutdated(existing);
    say(`  ✓ 已關閉還在背景執行的舊版 Pilcrow ${existing.version}；重新開啟就是 ${VERSION}`);
    return 0;
  }
  const what = busy ? `Claude 還有 ${busy} 個 session 正在工作，關閉會中斷它們（對話會保留）。` : '如果 Claude 正在工作，關閉會中斷它（對話會保留）。';
  if (!process.stdin.isTTY) {
    say(`  ! 舊版 Pilcrow ${existing.version} 還在背景執行。${what}`);
    say('    關閉所有 Pilcrow 視窗後再重新開啟，就會改用新版。');
    return 0;
  }
  const rl = (await import('node:readline/promises')).createInterface({ input: process.stdin, output: process.stdout });
  const ans = (await rl.question(`  ? 舊版 Pilcrow ${existing.version} 還在背景執行。${what}現在關閉它？[Y/n] `)).trim().toLowerCase();
  rl.close();
  if (ans === '' || ans === 'y' || ans === 'yes') {
    await retireOutdated(existing, { force: true });
    say(`  ✓ 已關閉舊版；重新開啟 Pilcrow 就是 ${VERSION}`);
  } else {
    say('  ! 沒有關閉。之後關閉所有 Pilcrow 視窗再重新開啟，就會改用新版。');
  }
  return 0;
}

async function cli() {
  const args = parseArgs(process.argv.slice(2));
  if (args.doctor) process.exit((await doctor()) ? 1 : 0);
  if (args['restart-outdated']) process.exit(await restartOutdatedPrompt());
  let shell = args.shell || (args.server || args.headless ? 'none' : 'chrome');
  let existing = await existingInstance();
  if (existing && existing.version !== VERSION && (await retireOutdated(existing))) {
    log(`stopped Pilcrow ${existing.version} (pid ${existing.pid}) so ${VERSION} can start`);
    console.log(`Restarted Pilcrow: the older ${existing.version} was still running in the background.`);
    existing = null;
  }
  if (existing) {
    if (shell === 'none') {
      console.log(`Pilcrow is already running: ${existing.url}`);
      return;
    }
    const chrome = shell === 'chrome' ? findChrome() : null;
    if (chrome) openChromeWindow(chrome, existing.url).unref();
    else openExternal(existing.url);
    console.log('Pilcrow is already running; opened another window.');
    return;
  }
  const chrome = shell === 'chrome' ? findChrome() : null;
  if (shell === 'chrome' && !chrome) {
    console.log('No Chrome/Chromium found; opening Pilcrow in your default browser instead.');
    shell = 'browser';
  }
  let app;
  try {
    app = await start({ shell, port: args.port, quiet: true });
  } catch (e) {
    console.error(`Pilcrow could not start: ${e.message}`);
    process.exit(1);
  }
  const shutdown = () => {
    app.stop();
    setTimeout(() => process.exit(0), 300);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  process.on('SIGHUP', shutdown);

  const toBrowser = (why) => {
    if (why) console.log(`${why} Opening Pilcrow in your default browser instead.`);
    if (!openExternal(app.url)) console.log(`Open ${app.url} in a browser.`);
    console.log(`Pilcrow keeps running at ${app.url} until you quit it (Ctrl+Q in the window, or Ctrl+C here).`);
  };

  if (shell === 'none') {
    console.log(`Pilcrow is running at ${app.url}`);
    console.log('Press Ctrl+C to quit.');
  } else if (shell === 'browser') {
    toBrowser();
  } else {
    const child = openChromeWindow(chrome, app.url);
    const startedAt = Date.now();
    let fellBack = false;
    const fallback = (why) => {
      if (fellBack) return;
      fellBack = true;
      log(why);
      toBrowser(why);
    };
    child.on('error', (e) => fallback(`Could not start ${path.basename(chrome)} (${e.message}).`));
    child.on('exit', (code) => {
      if (fellBack) return;
      if (Date.now() - startedAt < 3000) {
        // Exit code 0 right away means Chrome handed the window to its running instance.
        if (code === 0) return console.log('Chrome opened the window in its running instance. Quit Pilcrow with Ctrl+Q in the window.');
        return fallback(`${path.basename(chrome)} exited right away (code ${code}).`);
      }
      shutdown();
    });
    // If no window ever connects (blocked sandbox, broken GPU, policy), do not leave a server with no window.
    setTimeout(() => {
      if (!app.connections()) fallback('No Pilcrow window connected within 25 seconds.');
    }, 25_000).unref();
  }
}

process.on('unhandledRejection', (e) => log('unhandledRejection', e));
process.on('uncaughtException', (e) => log('uncaughtException', e));

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  cli().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
