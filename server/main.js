// Pilcrow server: serves the UI, speaks JSON-RPC over a WebSocket and owns the Claude Code sessions.
// Usage: node server/main.js [--shell=chrome|browser|none] [--port=N]; run that way, the command line
// in cli.js (the launcher, --doctor) takes over. Imported (Electron, the tests), this only defines start().
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { WebSocket, WebSocketServer } from 'ws';
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
import * as inbox from './inbox.js';
import * as dispatch from './dispatch.js';
import * as memory from './memory.js';
import { repos } from './repos.js';
import * as helpersMod from './helpers.js';
import { which, log, xdg, HOME, IS_ROOT, IS_WIN, slash, readJson, writeJson, debounce, expandHome, run, privateDir, migrateLegacyDirs, VERSION, AppError, under } from './util.js';

// Old environment variable names keep working after the rename.
for (const k of Object.keys(process.env)) if (k.startsWith('RETICLE_') && !('PILCROW_' + k.slice(8) in process.env)) process.env['PILCROW_' + k.slice(8)] = process.env[k];

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DIST = path.join(ROOT, 'web', 'dist');
const PREFERRED_PORT = 47117;
const HEARTBEAT_MS = 30_000; // a window that misses a ping for this long is gone (the laptop slept, a tunnel dropped)
const MAX_BUFFERED = 32 * 1024 * 1024; // bytes queued for one window; past this it cannot keep up (see deliver)
const MAX_PAYLOAD = 64 * 1024 * 1024; // a message from a window: attachments travel inline
const LIST_CHANGED_MS = 150; // a burst of session changes is one "the list changed" to the windows
/** The running instance's pid and port, for the launcher to find it (cli.js). */
export const LOCK = path.join(xdg('state'), 'instance.json');

const OCTET = 'application/octet-stream';
/** The interface's pages may run only their own scripts and talk only to this server. */
const CSP = "frame-ancestors 'none'; default-src 'self'; img-src 'self' data: blob: http://127.0.0.1:* http://localhost:*; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-src http: https:; worker-src 'self' blob:";

/** Where Claude Code sends its requests and whether it has credentials, from its own settings. */
export async function gatewayInfo(cwd) {
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

/** Open a URL in the default browser, or a file or folder in the file manager. */
export function openExternal(target) {
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

// ---------------------------------------------------------------- RPC

/** A required, non-empty string param; anything else is the client's mistake (invalid_params). */
function str(v, name) {
  if (typeof v !== 'string' || !v) throw new AppError('invalid_params', `${name} is required`);
  return v;
}

/** A condition on the params; the error names the param that broke it. */
function need(ok, name) {
  if (!ok) throw new AppError('invalid_params', `${name} is missing or has the wrong type`);
}

/** An optional string param: absent (undefined, null or '') reads as undefined. */
function optStr(v, name) {
  need(v === undefined || v === null || typeof v === 'string', name);
  return v || undefined;
}

/** A path param, with ~ expanded. */
const pathParam = (v, name = 'path') => expandHome(str(v, name));
/** An optional path param (a folder to work in), with ~ expanded. */
const optPath = (v, name = 'cwd') => expandHome(optStr(v, name));

/** Dispatch's targets: at least one, each with a folder. */
const targetsOk = (t) => Array.isArray(t) && t.length > 0 && t.every((x) => typeof x?.path === 'string' && x.path);
/** A job's or playbook's folders with ~ expanded; a field that is not given stays not given. */
const withPaths = (p) => ({ ...p, root: optPath(p.root, 'root'), ...(p.targets === undefined ? {} : { targets: p.targets.map((t) => ({ ...t, path: expandHome(t.path) })) }) });

/**
 * The JSON-RPC methods by name. A function of the few things they need from the running server, so
 * the names can be listed without starting one (METHODS, which the mock server checks itself against).
 * Params are checked where a missing one would otherwise surface as a TypeError deep in a module.
 * @param {{ shell?: string, quit?: () => void, listChanged?: () => void }} server
 */
function rpcMethods({ shell, quit, listChanged }) {
  const S = (id) => sessions.get(str(id, 'id'));
  return {
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
      setTimeout(() => quit?.(), 50); // after the reply is out
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
    'session.queue.remove': async ({ id, uuid: u }) => {
      str(u, 'uuid');
      return (await S(id)).removeHeld(u);
    },
    'session.queue.update': async ({ id, uuid: u, text }) => {
      str(u, 'uuid');
      need(typeof text === 'string', 'text');
      return (await S(id)).updateHeld(u, text);
    },
    'session.queue.sendNow': async ({ id, uuid: u }) => {
      str(u, 'uuid');
      return (await S(id)).sendHeldNow(u);
    },
    'session.setMode': async ({ id, mode }) => (await S(id)).setMode(mode),
    'session.setModel': async ({ id, model }) => (await S(id)).setModel(model),
    'session.setEffort': async ({ id, effort }) => (await S(id)).setEffort(effort),
    'session.permission': async ({ id, requestId, decision }) => {
      need(typeof decision?.behavior === 'string', 'decision');
      return (await S(id)).respond(str(requestId, 'requestId'), decision);
    },
    'session.rename': ({ id, title }) => sessions.rename(str(id, 'id'), title),
    'session.delete': async ({ id, removeWorktree, removeHelpers }) => {
      str(id, 'id');
      // a lead's helpers go with it when asked (HELPERS.md §5.10); otherwise they stay as sessions
      if (removeHelpers) for (const hid of helpersMod.helpers.helperIds(id)) await sessions.remove(hid, { removeWorktree: Boolean(removeWorktree) });
      return sessions.remove(id, { removeWorktree });
    },
    'session.archive': ({ id, archived }) => {
      store.setMeta(str(id, 'id'), { archived: archived || null });
      listChanged?.();
    },
    'session.pin': ({ id, pinned }) => {
      store.setMeta(str(id, 'id'), { pinned: pinned || null });
      listChanged?.();
    },
    'session.fork': ({ id, upToMessageId, title }) => sessions.fork(str(id, 'id'), { upToMessageId, title }),
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
    'skills.list': ({ cwd }) => customize.listSkills({ cwd: optPath(cwd) }),
    'skills.create': ({ scope, cwd, name, description }) => customize.createSkill({ scope, cwd: optPath(cwd), name, description }),
    'skills.remove': ({ dir, cwd }) => customize.removeSkill({ dir: pathParam(dir, 'dir'), cwd: optPath(cwd) }),
    'skills.import': ({ scope, cwd, from, name }) => customize.importSkill({ scope, cwd: optPath(cwd), from, name }),
    'skills.reloadAll': async () => {
      let n = 0;
      for (const s of sessions.live()) {
        await s.reloadSkills().catch(() => null);
        n++;
      }
      return n;
    },
    'mcp.list': ({ cwd }) => customize.listMcp({ cwd: optPath(cwd) }),
    'mcp.add': async ({ scope, cwd, name, form }) => {
      const claude = await resolveClaude(store.getConfig().claudeBinary);
      return customize.addMcp({ bin: claude.path, scope, cwd: optPath(cwd), name, config: customize.mcpConfigFromForm(form) });
    },
    'mcp.remove': async ({ scope, cwd, name }) => {
      str(name, 'name');
      const claude = await resolveClaude(store.getConfig().claudeBinary);
      return customize.removeMcp({ bin: claude.path, scope, cwd: optPath(cwd), name });
    },
    'mcp.approve': ({ cwd, name, approved }) => customize.approveProjectMcp({ cwd: pathParam(cwd, 'cwd'), name, approved: Boolean(approved) }),
    // ---- project memory and who-touched-what
    'memory.list': ({ cwd }) => memory.list(pathParam(cwd, 'cwd')),
    'memory.add': ({ cwd, text }) => memory.add(pathParam(cwd, 'cwd'), { text }),
    'memory.update': ({ cwd, id, text, pinned }) => memory.update(pathParam(cwd, 'cwd'), id, { text, pinned }),
    'memory.remove': ({ cwd, id }) => memory.remove(pathParam(cwd, 'cwd'), str(id, 'id')),
    'memory.clear': ({ cwd }) => memory.clear(pathParam(cwd, 'cwd')),
    'memory.distill': async ({ id, force }) => {
      const s = await S(id);
      await s.loadHistory();
      return memory.distill(s, { force: Boolean(force) });
    },
    'project.fileHistory': async ({ cwd }) => {
      const list = await sessions.list();
      const titles = Object.fromEntries(list.map((s) => [s.id, { title: s.title, firstPrompt: s.firstPrompt || null, lastModified: s.lastModified }]));
      const byPath = {};
      for (const st of await snapshots.folderSteps(pathParam(cwd, 'cwd'))) {
        for (const f of st.files) {
          (byPath[f.path] ||= []).push({ sessionId: st.sessionId, title: titles[st.sessionId]?.title || 'Session', about: titles[st.sessionId]?.firstPrompt || null, at: st.at, n: st.n, toolUseId: st.toolUseId, kind: st.kind, add: f.add, del: f.del });
        }
      }
      for (const k of Object.keys(byPath)) byPath[k].sort((a, b) => b.at - a.at);
      return byPath;
    },
    // ---- scheduled sessions and their inbox
    'schedules.list': () => schedules.list(),
    'schedules.save': (p) => schedules.save({ ...p, cwd: optPath(p.cwd) }),
    'schedules.remove': ({ id }) => schedules.remove(str(id, 'id')),
    'schedules.toggle': ({ id, enabled }) => schedules.toggle(id, enabled),
    'schedules.runNow': ({ id }) => schedules.runNow(id, { manual: true }),
    'schedules.preview': ({ cadence }) => {
      try {
        return { nextRunAt: schedules.nextRun(cadence), label: schedules.describeCadence(cadence) };
      } catch (e) {
        return { nextRunAt: null, label: '', error: e.message };
      }
    },
    'inbox.list': () => inbox.list(),
    'inbox.markRead': ({ ids }) => inbox.markRead(ids || null),
    'inbox.remove': ({ id }) => inbox.remove(str(id, 'id')),
    // ---- dispatch: one job across many repositories
    'helpers.list': ({ leadId }) => helpersMod.helpers.list(str(leadId, 'leadId')),
    'helpers.allow': ({ leadId, requestId }) => helpersMod.helpers.allow(str(leadId, 'leadId'), str(requestId, 'requestId')),
    'helpers.stop': ({ leadId, name }) => helpersMod.helpers.stop(str(leadId, 'leadId'), { name: str(name, 'name') }),
    'helpers.review': ({ leadId }) => helpersMod.helpers.review(str(leadId, 'leadId')),
    'helpers.finish': ({ leadId, name, action, message, pr }) => helpersMod.helpers.finish(str(leadId, 'leadId'), { name: optStr(name, 'name'), action: str(action, 'action'), message: optStr(message, 'message'), pr }),
    'helpers.export': ({ leadId }) => helpersMod.helpers.exportMarkdown(str(leadId, 'leadId')),
    'helpers.detach': ({ id }) => helpersMod.helpers.detach(str(id, 'id')),
    'repos.list': () => repos.list(),
    'repos.save': (p) => repos.save({ ...p, path: optPath(p.path, 'path') }),
    'repos.remove': ({ id }) => repos.remove(str(id, 'id')),
    'repos.register': ({ path: dir, ...patch }) => repos.register(pathParam(dir), patch),
    'repos.scan': ({ root, depth }) => repos.scan(pathParam(root, 'root'), depth),
    'repos.refresh': ({ ids, force }) => repos.refresh(ids || null, { force: Boolean(force) }),
    'repos.resolve': ({ word }) => repos.resolve(word) || { nearest: repos.nearest(word) },
    'workspace.scan': ({ root, depth, force }) => dispatch.dispatch.scan({ root: optPath(root, 'root'), depth, force: Boolean(force) }),
    'dispatch.list': () => dispatch.dispatch.list(),
    'dispatch.get': ({ id }) => dispatch.dispatch.get(id),
    'dispatch.save': (p) => {
      // a new job needs somewhere to run; an update that names no targets keeps the job's
      need(p.targets === undefined ? Boolean(p.id) : targetsOk(p.targets), 'targets');
      return dispatch.dispatch.save(withPaths(p));
    },
    'dispatch.remove': ({ id, removeWorktrees }) => dispatch.dispatch.remove(str(id, 'id'), { removeWorktrees: Boolean(removeWorktrees) }),
    'dispatch.plan': (p) => {
      need(p.targets === undefined || Array.isArray(p.targets), 'targets');
      return dispatch.dispatch.plan(p.id && !p.targets ? p.id : withPaths({ ...p, targets: p.targets || [] }));
    },
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
    'dispatch.savePlaybook': (p) => {
      need(p.targets === undefined || Array.isArray(p.targets), 'targets');
      return dispatch.dispatch.savePlaybook(withPaths(p));
    },
    'dispatch.removePlaybook': ({ id }) => dispatch.dispatch.removePlaybook(str(id, 'id')),
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
    'timeline.restore': async ({ id, n, paths }) => snapshots.restore(await S(id), { n, paths }),
    'session.stopTask': async ({ id, taskId }) => (await S(id)).stopTask(taskId),
    'session.subagent': async ({ id, agentId }) => history.loadSubagent(id, str(agentId, 'agentId'), (await S(id)).cwd),
    'session.gateway': async ({ cwd }) => gatewayInfo(cwd),
    'models.gateway': async ({ cwd, force }) => gatewayModels(cwd, { force: Boolean(force), version: VERSION }),

    // Without a folder, git would answer for whatever folder the server runs in (info and diff say isRepo: false).
    'git.info': ({ cwd }) => git.info(optPath(cwd)),
    'git.diff': ({ cwd, base }) => git.diff(optPath(cwd), { base: optStr(base, 'base') }),
    'git.branches': ({ cwd }) => git.branches(pathParam(cwd, 'cwd')),
    'git.commit': ({ cwd, message, paths }) => {
      need(paths === undefined || paths === null || (Array.isArray(paths) && paths.every((x) => typeof x === 'string')), 'paths');
      return git.commit(pathParam(cwd, 'cwd'), { message: str(message, 'message'), paths: paths || undefined });
    },
    'git.discard': ({ cwd, path: p, untracked }) => git.discard(pathParam(cwd, 'cwd'), { path: str(p, 'path'), untracked: Boolean(untracked) }),
    'git.log': ({ cwd, n }) => {
      need(n === undefined || (Number.isInteger(n) && n > 0), 'n');
      return git.log(pathParam(cwd, 'cwd'), n);
    },
    'git.worktrees': ({ cwd }) => git.listWorktrees(pathParam(cwd, 'cwd')),
    'git.removeWorktree': ({ repoRoot, dir, branch, deleteBranch }) => git.removeWorktree({ repoRoot: pathParam(repoRoot, 'repoRoot'), dir: pathParam(dir, 'dir'), branch: optStr(branch, 'branch'), deleteBranch: Boolean(deleteBranch) }),

    'fs.list': ({ dir }) => files.listDir(pathParam(dir, 'dir')),
    'fs.read': ({ path: p }) => files.readFile(pathParam(p)),
    'fs.write': ({ path: p, text, expectedMtime, force }) => {
      need(typeof text === 'string', 'text');
      return files.writeFile(pathParam(p), text, { expectedMtime, force });
    },
    'fs.browse': ({ dir }) => files.browse(optPath(dir, 'dir')),
    'fs.index': ({ cwd, force }) => files.fileIndex(pathParam(cwd, 'cwd'), { force }),
    'fs.mkdir': async ({ path: p }) => {
      await fs.promises.mkdir(pathParam(p), { recursive: true });
      return true;
    },

    'term.open': ({ cwd, cols, rows, command }) => {
      need((cols === undefined || (Number.isInteger(cols) && cols > 0)) && (rows === undefined || (Number.isInteger(rows) && rows > 0)), 'cols/rows');
      return term.open({ cwd: optPath(cwd), cols, rows, command: optStr(command, 'command') });
    },
    'term.input': ({ id, data }) => {
      need(typeof data === 'string', 'data');
      return term.write(str(id, 'id'), data);
    },
    'term.resize': ({ id, cols, rows }) => {
      need(Number.isInteger(cols) && Number.isInteger(rows) && cols > 0 && rows > 0, 'cols/rows');
      return term.resize(str(id, 'id'), cols, rows);
    },
    'term.close': ({ id }) => term.close(str(id, 'id')),
    'term.list': () => term.list(),
    'term.snapshot': ({ id }) => term.snapshot(str(id, 'id')),

    'shell.openExternal': ({ url }) => /^(https?|file|mailto):/i.test(str(url, 'url')) && openExternal(url),
    'shell.openPath': ({ path: p }) => openExternal(pathParam(p)),
  };
}

/** Every method name the server answers. */
export const METHODS = Object.freeze(Object.keys(rpcMethods({})));

/**
 * What the client is told when a method fails: the message it shows, and a code it can act on.
 * A bug (TypeError and its kind) is 'internal'; an error a module threw on purpose to say no, with
 * a message for the person, is 'failed'. The mock server answers through it too.
 */
export function errorOf(e) {
  const message = e?.message || String(e);
  if (e instanceof AppError) return { code: e.code, message };
  if (e?.code === 'ENOENT') return { code: 'not_found', message };
  if (e instanceof TypeError || e instanceof RangeError || e instanceof ReferenceError || e instanceof SyntaxError) return { code: 'internal', message };
  return { code: 'failed', message };
}

/**
 * Start the server (the Electron shell calls this in-process; `cli()` for the other shells).
 * `heartbeatMs` and `maxBuffered` are there for the tests to turn down.
 * @param {{ shell?: string, port?: number | string | boolean, quiet?: boolean, onQuit?: () => void, heartbeatMs?: number, maxBuffered?: number }} [opts]
 */
export async function start({ shell = 'none', port, quiet = false, onQuit, heartbeatMs = HEARTBEAT_MS, maxBuffered = MAX_BUFFERED } = {}) {
  process.on('unhandledRejection', (e) => log('unhandledRejection', e));
  process.on('uncaughtException', (e) => log('uncaughtException', e));
  migrateLegacyDirs();
  privateDir(xdg('config'));
  privateDir(xdg('state'));
  const clients = new Set();
  let totalConnections = 0;

  /**
   * Every event goes to every window. One that cannot keep up (asleep, on a slow tunnel) would hold an
   * ever-growing queue here; past `maxBuffered` it is dropped instead. It reconnects on its own and
   * reopens what it shows from a fresh snapshot, which is the clean way to catch up.
   */
  const deliver = (ws, raw) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > maxBuffered) {
      log(`dropping a window that is ${ws.bufferedAmount} bytes behind; it will reconnect`);
      ws.terminate();
      return;
    }
    ws.send(raw);
  };
  const send = (ws, obj) => deliver(ws, JSON.stringify(obj));
  const broadcast = (e, d) => {
    const raw = JSON.stringify({ t: 'ev', e, d });
    for (const ws of clients) deliver(ws, raw);
  };

  const listChanged = debounce(() => broadcast('sessions.changed', {}), LIST_CHANGED_MS);
  bus.on('msgs', (d) => broadcast('session.msgs', d));
  bus.on('state', (d) => broadcast('session.state', d));
  bus.on('permission', (d) => broadcast('session.permission', { id: d.id, request: d.shown })); // the input cut as in the state
  bus.on('permission-resolved', (d) => broadcast('session.permissionResolved', d));
  bus.on('list-changed', listChanged);
  snapshots.events.on('step', (d) => broadcast('timeline.step', d));
  schedules.events.on('schedules', () => broadcast('schedules.changed', {}));
  inbox.events.on('changed', () => broadcast('inbox.changed', inbox.list().unread));
  schedules.start();
  dispatch.events.on('changed', (d) => broadcast('dispatch.changed', d));
  dispatch.events.on('playbooks', () => broadcast('dispatch.playbooks', {}));
  dispatch.events.on('finished', (d) => d.scheduleId && schedules.noteDispatchFinished(d.scheduleId, d.status));
  schedules.setDispatchRunner((playbookId, opts) => dispatch.dispatch.startFromPlaybook(playbookId, opts));
  dispatch.start();
  memory.events.on('changed', (d) => broadcast('memory.changed', d));
  repos.events.on('changed', (d) => broadcast('repos.changed', d));
  helpersMod.start();
  helpersMod.events.on('changed', (d) => broadcast('helpers.changed', d));
  sessions.start();
  term.terminals.on('data', (d) => broadcast('term.data', d));
  term.terminals.on('exit', (d) => broadcast('term.exit', d));

  const methods = rpcMethods({ shell, quit: () => (onQuit ? onQuit() : stop().then(() => process.exit(0))), listChanged });

  const server = http.createServer((req, res) => {
    try {
      serveHttp(req, res);
    } catch (e) {
      // A malformed request (a bad %-escape in the path) must get an answer, not hang.
      log('http request failed', req.url, e);
      if (!res.headersSent) res.writeHead(e instanceof URIError ? 400 : 500).end();
    }
  });

  function serveHttp(req, res) {
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
      sendFile(res, p, { 'content-type': files.mimeOf(p) || OCTET, 'x-content-type-options': 'nosniff' });
      return;
    }
    if (url.pathname === '/api/raw') {
      const p = expandHome(url.searchParams.get('path') || '');
      sendFile(res, p, { 'content-type': files.imageMime(p) || OCTET });
      return;
    }
    serveStatic(url.pathname, res);
  }

  /** A file from disk for the page, or 404; a read that fails (unreadable, gone midway) ends the answer. */
  function sendFile(res, p, headers) {
    fs.stat(p, (err, st) => {
      if (err || !st.isFile()) {
        res.writeHead(404).end('Not found');
        return;
      }
      const stream = fs.createReadStream(p);
      stream.once('open', () => stream.pipe(res.writeHead(200, { ...headers, 'content-length': st.size, 'cache-control': 'no-store', 'cross-origin-resource-policy': 'same-origin' })));
      stream.once('error', (e) => {
        log('file read failed', p, e.message);
        if (res.headersSent) res.destroy();
        else res.writeHead(500).end();
      });
    });
  }

  /** The port we listen on (set once listening; nothing is served before). */
  let boundPort = 0;
  function originOk(req) {
    const origin = req.headers.origin || '';
    const p = boundPort;
    return !origin || origin === `http://127.0.0.1:${p}` || origin === `http://localhost:${p}`;
  }

  /** Browsers label every request with where it came from; anything not from our own page is refused. */
  function fetchSiteOk(req) {
    const site = req.headers['sec-fetch-site'];
    return !site || site === 'same-origin' || site === 'none';
  }

  function hostOk(req) {
    const host = (req.headers.host || '').toLowerCase();
    const p = boundPort;
    return host === `127.0.0.1:${p}` || host === `localhost:${p}`;
  }

  /** The interface (web/dist): a path that is not a file is the app's own route, so index.html. */
  function serveStatic(pathname, res) {
    const file = path.join(DIST, decodeURIComponent(pathname).replace(/^\/+/, '') || 'index.html');
    if (!under(DIST, file)) {
      res.writeHead(403).end();
      return;
    }
    fs.stat(file, (err, st) => {
      const target = err || st.isDirectory() ? path.join(DIST, 'index.html') : file;
      const html = target.endsWith('.html');
      const headers = {
        'content-type': files.mimeOf(target) || OCTET,
        'cache-control': target.includes(`${path.sep}assets${path.sep}`) ? 'public, max-age=31536000, immutable' : 'no-cache',
        'x-content-type-options': 'nosniff',
        ...(html ? { 'x-frame-options': 'DENY', 'content-security-policy': CSP } : {}),
      };
      const stream = fs.createReadStream(target);
      stream.once('open', () => stream.pipe(res.writeHead(200, headers)));
      stream.once('error', () => (res.headersSent ? res.destroy() : res.writeHead(500, { 'content-type': 'text/plain' }).end('The interface files are missing (web/dist). Run the Pilcrow installer again to restore them.')));
    });
  }

  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_PAYLOAD });
  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url || '/', 'http://x');
    if (url.pathname !== '/ws' || !originOk(req) || !hostOk(req) || !fetchSiteOk(req)) {
      socket.write('HTTP/1.1 403 Forbidden\r\n\r\n'); // as the HTTP refusals
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
  });

  // Ping every window; one that has not answered the previous ping is gone, though its socket never said so.
  const answered = new WeakSet();
  const heartbeat = setInterval(() => {
    for (const ws of clients) {
      if (!answered.delete(ws)) ws.terminate();
      else ws.ping();
    }
  }, heartbeatMs);

  wss.on('connection', (ws) => {
    totalConnections++;
    clients.add(ws);
    answered.add(ws);
    ws.on('pong', () => answered.add(ws));
    ws.on('close', () => clients.delete(ws));
    // A broken connection (a bad frame, a reset) is logged; 'close' follows and cleans up.
    ws.on('error', (e) => log('websocket error', e.message));
    ws.on('message', async (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return;
      }
      if (msg?.t !== 'req') return;
      const fn = Object.hasOwn(methods, msg.m) ? methods[msg.m] : null;
      if (!fn) {
        send(ws, { t: 'res', id: msg.id, ok: false, e: { code: 'unknown_method', message: `Unknown method ${msg.m}` } });
        return;
      }
      try {
        const r = await fn(msg.p && typeof msg.p === 'object' ? msg.p : {});
        send(ws, { t: 'res', id: msg.id, ok: true, r: r === undefined ? null : r });
      } catch (e) {
        const err = errorOf(e);
        log('rpc error', msg.m, err.code === 'internal' ? e : err.message);
        send(ws, { t: 'res', id: msg.id, ok: false, e: err });
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
        resolve(undefined);
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
  const actualPort = (boundPort = /** @type {import('node:net').AddressInfo} */ (server.address()).port);
  const url = `http://127.0.0.1:${actualPort}/`;
  writeJson(LOCK, { pid: process.pid, port: actualPort, shell, startedAt: Date.now() });
  log(`Pilcrow ${VERSION} listening on 127.0.0.1:${actualPort} (shell=${shell})`);
  if (!quiet) console.log(`Pilcrow is running on port ${actualPort}.`);

  let stopping = null;
  /**
   * Stop everything, once. What is pending is written before the first await, so even a caller that
   * cannot wait has it on disk; the promise settles when the terminals' processes are gone, and the
   * quit paths wait for it before they exit. Dispatch and helpers let go of the sessions first, so the
   * processes closing here read as a restart to them (their runs resume as stopped), not as failures.
   */
  const stop = () =>
    (stopping ??= (async () => {
      schedules.stop();
      dispatch.stop();
      helpersMod.stop();
      sessions.stop();
      try {
        store.flush();
      } catch (e) {
        log('could not save settings', e);
      }
      try {
        const cur = readJson(LOCK, {});
        if (cur.pid === process.pid) fs.rmSync(LOCK, { force: true });
      } catch {
        /* someone else's lock now */
      }
      clearInterval(heartbeat);
      for (const ws of clients) ws.close();
      server.close();
      await term.closeAll();
    })());
  return { url, port: actualPort, stop, busy: () => sessions.busy(), connections: () => totalConnections };
}

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  import('./cli.js')
    .then((m) => m.cli())
    .catch((e) => {
      console.error(e);
      process.exit(1);
    });
}
