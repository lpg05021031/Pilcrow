#!/usr/bin/env node
// A stand-in for server/main.js for the browser tests: the same JSON-RPC over WebSocket, the built
// interface served from web/dist, and a scripted Claude that streams text, uses tools, asks for
// permission and ends its turns — without Claude Code or an API key.
//
//   node test/mock/mock-server.mjs [port]        default 5300
//   MOCK_LONG=400                                add a conversation with that many turns
//
// The tests drive it through the same socket with a few extra methods (mock.*), see below.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DIST = path.join(ROOT, 'web', 'dist');
const VERSION = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version;
const PORT = Number(process.argv[2] || process.env.MOCK_PORT || 5300);
const LONG = Number(process.env.MOCK_LONG || 0);
const HOME = '/home/tester';
const PROJ = `${HOME}/code/pilcrow`;
const OTHER = `${HOME}/code/website`;

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ico': 'image/x-icon', '.map': 'application/json' };

let seq = 0;
const uuid = () => `m-${(++seq).toString(36).padStart(5, '0')}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** How long the scripted Claude lingers between streamed pieces (ms); tests may turn it down. */
let pace = 24;

// ---------------------------------------------------------------- sessions and their transcripts

/** Raw messages in the shape Claude Code's SDK produces, as seen in sessions.js. */
const msg = {
  user: (sid, text, at) => ({ type: 'user', uuid: uuid(), message: { role: 'user', content: [{ type: 'text', text }] }, parent_tool_use_id: null, session_id: sid, timestamp: new Date(at).toISOString(), _local: true }),
  assistant: (sid, content, at, id = `msg_${uuid()}`) => ({ type: 'assistant', uuid: uuid(), message: { id, role: 'assistant', model: 'claude-mock-1', content, usage: { input_tokens: 1200, output_tokens: 80, cache_read_input_tokens: 20000 } }, parent_tool_use_id: null, session_id: sid, timestamp: new Date(at).toISOString() }),
  toolResult: (sid, toolUseId, content, at, structured, isError = false) => ({ type: 'user', uuid: uuid(), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: toolUseId, content, is_error: isError }] }, parent_tool_use_id: null, session_id: sid, timestamp: new Date(at).toISOString(), tool_use_result: structured }),
  result: (sid, text, at, ms = 4200, cost = 0.012) => ({ type: 'result', subtype: 'success', uuid: uuid(), session_id: sid, duration_ms: ms, duration_api_ms: ms - 200, is_error: false, num_turns: 1, result: text, total_cost_usd: cost, usage: { input_tokens: 1200, output_tokens: 80, cache_read_input_tokens: 20000 }, modelUsage: { 'claude-mock-1': { inputTokens: 1200, outputTokens: 80, contextWindow: 200000 } } }),
  stream: (sid, event) => ({ type: 'stream_event', uuid: uuid(), session_id: sid, parent_tool_use_id: null, event }),
};

/** A finished turn for the history: the question, Claude's words and the work in between. */
function turn(sid, at, { prompt, reply, tools = [], narration }) {
  const out = [msg.user(sid, prompt, at)];
  let t = at + 800;
  if (narration) {
    out.push(msg.assistant(sid, [{ type: 'text', text: narration }], t));
    t += 400;
  }
  for (const tool of tools) {
    const id = `toolu_${uuid()}`;
    out.push(msg.assistant(sid, [{ type: 'tool_use', id, name: tool.name, input: tool.input }], t));
    t += tool.ms || 600;
    out.push(msg.toolResult(sid, id, tool.result || 'ok', t, tool.structured, tool.error));
    t += 200;
  }
  out.push(msg.assistant(sid, [{ type: 'text', text: reply }], t));
  out.push(msg.result(sid, reply, t + 100, t + 100 - at));
  return out;
}

const READ = (file, lines = 40) => ({ name: 'Read', input: { file_path: `${PROJ}/${file}` }, result: Array.from({ length: Math.min(lines, 12) }, (_, i) => `${i + 1}\tline ${i + 1} of ${file}`).join('\n'), structured: { type: 'text', file: { filePath: `${PROJ}/${file}`, numLines: lines, totalLines: lines } } });
const GREP = (pattern) => ({ name: 'Grep', input: { pattern, path: PROJ, output_mode: 'files_with_matches' }, result: 'server/sessions.js\nweb/src/lib/store.ts' });
const BASH = (command, description, result = 'done') => ({ name: 'Bash', input: { command, description }, result, structured: { stdout: result, stderr: '', interrupted: false } });
const EDIT = (file, oldS, newS) => ({ name: 'Edit', input: { file_path: `${PROJ}/${file}`, old_string: oldS, new_string: newS }, result: `The file ${PROJ}/${file} has been updated.`, structured: { filePath: `${PROJ}/${file}`, oldString: oldS, newString: newS, structuredPatch: [{ oldStart: 10, oldLines: 1, newStart: 10, newLines: 1, lines: ['-' + oldS, '+' + newS] }] } });

const DAY = 86400e3;
const now = Date.now();

/** @type {Map<string, Sess>} */
const sessions = new Map();
class Sess {
  constructor({ id, title, cwd, touched, archived = false, pinned = false, messages = [] }) {
    this.id = id;
    this.title = title;
    this.cwd = cwd;
    this.touched = touched;
    this.createdAt = touched - 3600e3;
    this.archived = archived;
    this.pinned = pinned;
    this.messages = messages;
    this.inflight = [];
    this.status = 'idle';
    this.live = false;
    this.pending = new Map(); // requestId -> { req, resolve }
    this.held = [];
    this.mode = 'default';
    this.model = '';
    this.effort = '';
    this.usage = { contextTokens: 21280, contextWindow: 200000, costUsd: messages.filter((m) => m.type === 'result').reduce((s, m) => s + (m.total_cost_usd || 0), 0), outputTokens: 80, model: 'claude-mock-1' };
    this.activity = null;
    this.turnStartedAt = null;
    this.lastActivity = touched;
    this.lastReply = null;
    const lastResult = [...messages].reverse().find((m) => m.type === 'result');
    if (lastResult) this.lastReply = { text: String(lastResult.result).slice(0, 240), at: touched };
    this.init = null;
    this.outbox = [];
    this.flushTimer = null;
    this.turnSeq = 0;
  }
  summary() {
    return { id: this.id, title: this.title, firstPrompt: this.messages[0]?.message?.content?.[0]?.text, cwd: this.cwd, project: this.cwd, gitBranch: 'main', lastModified: this.touched, createdAt: this.createdAt, archived: this.archived, pinned: this.pinned, worktree: null, scheduled: null, status: this.status, live: this.live, pending: this.pending.size };
  }
  state() {
    return {
      id: this.id, status: this.status, live: this.live, cwd: this.cwd, mode: this.mode, model: this.model, effort: this.effort, activeModel: 'claude-mock-1',
      usage: this.usage, error: null, suggestion: null, compacting: false, mcp: [], activity: this.activity, lastActivity: this.lastActivity, turnStartedAt: this.turnStartedAt, processStartedAt: this.live ? this.createdAt : null, lastReply: this.lastReply,
      unattended: false, unattendedDenials: 0, pending: [...this.pending.values()].map((p) => p.req), tasks: [], bgTasks: [], queued: [], held: this.held.map((h) => ({ uuid: h.uuid, text: h.text, images: 0, files: [], at: h.at })),
      init: this.init, commands: null, models: null,
    };
  }
  emitState() {
    broadcast('session.state', this.state());
  }
  out(m) {
    this.outbox.push(m);
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        const msgs = this.outbox.splice(0);
        if (msgs.length) broadcast('session.msgs', { id: this.id, msgs });
      }, 16);
    }
  }
  record(m) {
    this.messages.push(m);
    this.out(m);
  }
  isBusy() {
    return this.status === 'running' || this.status === 'requires_action' || this.status === 'starting';
  }
}

function seed() {
  sessions.clear();
  seq = 0;
  const t0 = now - 2 * 3600e3;
  const s1 = new Sess({
    id: 'sess-login', title: 'Fix the login redirect bug', cwd: PROJ, touched: now - 25 * 60e3,
    messages: [
      ...turn('sess-login', t0, { prompt: 'The login page redirects to /undefined after signing in. Can you find out why?', narration: "I'll look at how the redirect target is read after sign-in.", tools: [GREP('redirect'), READ('web/src/lib/auth.ts', 120), READ('server/routes/login.js', 88)], reply: 'The redirect target comes from `?next=` but the login form never forwards that parameter, so `next` is undefined by the time the server redirects. The fix is to carry it through the form as a hidden field.' }),
      ...turn('sess-login', t0 + 15 * 60e3, { prompt: 'Please fix it and run the tests.', narration: 'Adding the hidden field and a guard on the server side.', tools: [EDIT('web/src/lib/auth.ts', "const next = params.get('next')", "const next = params.get('next') || '/'"), BASH('npm test', 'Run the test suite', 'Test Suites: 12 passed\nTests: 148 passed')], reply: 'Done. The form now forwards `next`, the server falls back to `/` when it is missing, and all 148 tests pass.' }),
      ...turn('sess-login', t0 + 40 * 60e3, { prompt: 'Thanks — summarise what changed in two sentences for the PR description.', reply: 'The login form now carries the `next` query parameter through to the server, so sign-in returns people to the page they came from. When the parameter is missing the server redirects to `/` instead of `/undefined`.' }),
    ],
  });
  const s2 = new Sess({
    id: 'sess-tests', title: 'Write tests for the parser', cwd: PROJ, touched: now - 3 * 3600e3,
    messages: [...turn('sess-tests', now - 3.5 * 3600e3, { prompt: 'Write unit tests for lib/partialJson.ts', tools: [READ('web/src/lib/partialJson.ts', 40), { ...EDIT('test/unit/partialJson.test.ts', '', ''), name: 'Write', input: { file_path: `${PROJ}/test/unit/partialJson.test.ts`, content: 'import { describe } from "vitest";' } }, BASH('npx vitest run', 'Run the unit tests', 'Tests 8 passed')], reply: 'Eight tests cover complete JSON, open strings, nested containers, dangling keys and escapes. They all pass.' })],
  });
  const s3 = new Sess({ id: 'sess-site', title: 'Deploy the marketing site', cwd: OTHER, touched: now - 1 * DAY, messages: [...turn('sess-site', now - DAY - 600e3, { prompt: 'Deploy to staging', tools: [BASH('npm run deploy:staging', 'Deploy', 'Deployed https://staging.example.com')], reply: 'Deployed to staging.' })] });
  const s4 = new Sess({ id: 'sess-old', title: 'Old archived exploration', cwd: OTHER, touched: now - 9 * DAY, archived: true, messages: [...turn('sess-old', now - 9 * DAY, { prompt: 'Explain the build', reply: 'It uses Vite.' })] });
  const s5 = new Sess({ id: 'sess-empty', title: 'New session', cwd: PROJ, touched: now - 40 * 60e3 });
  for (const s of [s1, s2, s3, s4, s5]) sessions.set(s.id, s);
  if (LONG > 0) {
    const msgs = [];
    const start = now - LONG * 90e3 - 3600e3;
    for (let i = 0; i < LONG; i++) {
      const at = start + i * 90e3;
      const kind = i % 4;
      msgs.push(...turn('sess-long', at, kind === 0 ? { prompt: `Turn ${i + 1}: what does module ${i} do?`, reply: `Module ${i} parses the configuration for stage ${i % 7} and hands it to the scheduler. It is small and has no side effects.` } : kind === 1 ? { prompt: `Turn ${i + 1}: check file ${i}`, narration: `Reading file ${i} and its tests.`, tools: [READ(`src/mod${i}.ts`, 30 + i), GREP(`mod${i}`)], reply: `File ${i} is fine: it exports two functions and both are covered by tests.` } : kind === 2 ? { prompt: `Turn ${i + 1}: fix the typo in module ${i}`, tools: [EDIT(`src/mod${i}.ts`, 'recieve', 'receive'), BASH('npm run check', 'Type-check', 'ok')], reply: `Fixed the typo in module ${i}; the type check passes.` } : { prompt: `Turn ${i + 1}: anything else?`, reply: `Nothing else stands out in module ${i}. Moving on.` }));
    }
    sessions.set('sess-long', new Sess({ id: 'sess-long', title: `Long conversation (${LONG} turns)`, cwd: PROJ, touched: now - 2 * DAY, messages: msgs }));
  }
}
seed();

// ---------------------------------------------------------------- the scripted Claude

/** What Claude does with a message: keywords in the prompt pick the script. */
function scriptFor(text) {
  const t = text.toLowerCase();
  if (/\bperm|permission|run (a |the )?command|dangerous/.test(t)) return 'permission';
  if (/\btool|read|look|check|edit|fix/.test(t)) return 'tools';
  if (/\bask|question/.test(t)) return 'question';
  if (/\bslow\b/.test(t)) return 'slow';
  return 'plain';
}

async function streamText(s, msgId, index, text, { delay = pace } = {}) {
  s.out(msg.stream(s.id, { type: 'content_block_start', index, content_block: { type: 'text', text: '' } }));
  const pieces = text.match(/\S+\s*|\s+/g) || [];
  for (const piece of pieces) {
    if (s.aborted) return;
    s.out(msg.stream(s.id, { type: 'content_block_delta', index, delta: { type: 'text_delta', text: piece } }));
    s.activity = { kind: 'text', label: text.slice(0, 140), at: Date.now() };
    if (delay) await sleep(delay);
  }
  s.out(msg.stream(s.id, { type: 'content_block_stop', index }));
}

async function streamTool(s, msgId, index, tool) {
  const id = `toolu_${uuid()}`;
  const json = JSON.stringify(tool.input);
  s.out(msg.stream(s.id, { type: 'content_block_start', index, content_block: { type: 'tool_use', id, name: tool.name, input: {} } }));
  for (let i = 0; i < json.length; i += 24) {
    if (s.aborted) return id;
    s.out(msg.stream(s.id, { type: 'content_block_delta', index, delta: { type: 'input_json_delta', partial_json: json.slice(i, i + 24) } }));
    if (pace) await sleep(pace / 2);
  }
  s.out(msg.stream(s.id, { type: 'content_block_stop', index }));
  return id;
}

/** One assistant message: streamed, then recorded whole, as the SDK does. */
async function say(s, parts) {
  const msgId = `msg_${uuid()}`;
  s.inflight = [];
  s.out(msg.stream(s.id, { type: 'message_start', message: { id: msgId } }));
  const content = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.text !== undefined) {
      await streamText(s, msgId, i, p.text);
      content.push({ type: 'text', text: p.text });
    } else {
      const id = await streamTool(s, msgId, i, p.tool);
      s.activity = { kind: 'tool', tool: p.tool.name, label: p.tool.input.file_path || p.tool.input.command || p.tool.name, at: Date.now() };
      content.push({ type: 'tool_use', id, name: p.tool.name, input: p.tool.input });
      p.id = id;
    }
    if (s.aborted) break;
  }
  s.out(msg.stream(s.id, { type: 'message_stop' }));
  s.record(msg.assistant(s.id, content, Date.now(), msgId));
  s.emitState();
  return content;
}

async function useTool(s, tool, { ask = false } = {}) {
  const [{ id }] = await say(s, [{ tool }]);
  if (s.aborted) return;
  if (ask) {
    const requestId = `req_${uuid()}`;
    const decision = await new Promise((resolve) => {
      const req = { requestId, toolName: tool.name, input: tool.input, toolUseId: id, title: tool.name === 'Bash' ? 'Allow Claude to run this command?' : undefined, description: tool.input.description, suggestions: tool.name === 'Bash' ? [{ type: 'addRules', behavior: 'allow', destination: 'localSettings', rules: [{ toolName: 'Bash', ruleContent: `${tool.input.command.split(' ')[0]}:*` }] }] : [], interactive: false, at: Date.now() };
      s.pending.set(requestId, { req, resolve });
      s.status = 'requires_action';
      broadcast('session.permission', { id: s.id, request: req });
      s.emitState();
      listChanged();
    });
    s.pending.delete(requestId);
    if (s.status === 'requires_action') s.status = 'running';
    broadcast('session.permissionResolved', { id: s.id, requestId, decision: decision.behavior });
    s.emitState();
    listChanged();
    if (decision.behavior === 'deny') {
      s.record(msg.toolResult(s.id, id, decision.message || 'The user declined this action.', Date.now(), undefined, true));
      return 'denied';
    }
  }
  await sleep(tool.ms || 350);
  s.record(msg.toolResult(s.id, id, tool.result || 'ok', Date.now(), tool.structured, tool.error));
  return 'done';
}

async function runTurn(s, text) {
  const started = Date.now();
  s.turnSeq++;
  const mine = s.turnSeq;
  s.aborted = false;
  const script = scriptFor(text);
  try {
    if (script === 'plain') {
      await say(s, [{ text: `You said “${text.trim().slice(0, 60)}”. Here is a short reply with a list:\n\n- one thing\n- another thing\n\nAnd a line of \`code\` to round it off.` }]);
    } else if (script === 'slow') {
      await say(s, [{ text: 'This reply arrives slowly, word by word, so a scroll test can watch it come in. '.repeat(6) }]);
    } else if (script === 'tools') {
      await say(s, [{ text: "I'll look at the parser first, then its tests." }]);
      await useTool(s, READ('web/src/lib/partialJson.ts', 40));
      if (s.aborted) throw new Error('aborted');
      await useTool(s, GREP('parsePartialJson'));
      if (s.aborted) throw new Error('aborted');
      await say(s, [{ text: 'The parser is only used by the transcript model. Now the edit.' }]);
      await useTool(s, EDIT('web/src/lib/partialJson.ts', 'return null;', 'return null; // unrecoverable'));
      if (s.aborted) throw new Error('aborted');
      await say(s, [{ text: 'Done: one comment added, nothing else changed. The parser is used in one place, so this is safe.' }]);
    } else if (script === 'permission') {
      await say(s, [{ text: "I need to run a command for this; I'll ask first." }]);
      const r = await useTool(s, BASH('rm -rf build && npm run build', 'Rebuild from scratch', 'built in 4.2s'), { ask: true });
      if (s.aborted) throw new Error('aborted');
      await say(s, [{ text: r === 'denied' ? 'Understood, I did not run it. Tell me how you would like to proceed.' : 'The build finished cleanly in about four seconds.' }]);
    } else if (script === 'question') {
      await say(s, [{ text: 'Before I go on, one question.' }]);
      await useTool(s, { name: 'AskUserQuestion', input: { questions: [{ question: 'Which style?', header: 'Style', options: [{ label: 'Compact', description: 'Short lines' }, { label: 'Verbose', description: 'Everything' }], multiSelect: false }] }, result: 'Compact' }, { ask: true });
      await say(s, [{ text: 'Compact it is.' }]);
    }
    if (s.aborted) throw new Error('aborted');
    const last = s.messages.filter((m) => m.type === 'assistant').at(-1);
    const replyText = last?.message.content.filter((c) => c.type === 'text').map((c) => c.text).join('\n') || '';
    const cost = 0.004 + Math.random() * 0.01;
    s.usage.costUsd += cost;
    s.usage.contextTokens += 900;
    s.record(msg.result(s.id, replyText, Date.now(), Date.now() - started, s.usage.costUsd));
    s.lastReply = { text: replyText.replace(/\s+/g, ' ').slice(0, 240), at: Date.now() };
  } catch (e) {
    if (e.message !== 'aborted') throw e;
    s.record({ type: 'result', subtype: 'error_during_execution', uuid: uuid(), session_id: s.id, is_error: false, terminal_reason: 'aborted_by_user', duration_ms: Date.now() - started, num_turns: 1, result: '', total_cost_usd: s.usage.costUsd });
  }
  if (s.turnSeq !== mine) return;
  s.status = 'idle';
  s.activity = null;
  s.turnStartedAt = null;
  s.inflight = [];
  s.lastActivity = Date.now();
  s.emitState();
  listChanged();
  if (s.held.length) setTimeout(() => deliver(s, s.held.shift()), 60);
}

function deliver(s, { uuid: u, text }) {
  if (!s.live) {
    s.live = true;
    s.init = { version: '2.0.0-mock', tools: ['Bash', 'Read', 'Edit', 'Write', 'Grep', 'Glob', 'Agent', 'AskUserQuestion'], mcpServers: [], slashCommands: ['compact', 'clear', 'context', 'usage'], model: 'claude-mock-1', capabilities: [] };
  }
  s.record({ ...msg.user(s.id, text, Date.now()), uuid: u || uuid() });
  s.status = 'running';
  s.turnStartedAt = Date.now();
  s.touched = Date.now();
  s.activity = { kind: 'thinking', label: 'Thinking', at: Date.now() };
  s.emitState();
  listChanged();
  runTurn(s, text).catch((e) => console.error('turn failed', e));
}

// ---------------------------------------------------------------- RPC

const clients = new Set();
const broadcast = (e, d) => {
  const raw = JSON.stringify({ t: 'ev', e, d });
  for (const ws of clients) if (ws.readyState === 1) ws.send(raw);
};
let listTimer = null;
const listChanged = () => {
  clearTimeout(listTimer);
  listTimer = setTimeout(() => broadcast('sessions.changed', {}), 50);
};
const config = { theme: 'dark', fontSize: 14.5, sendKey: 'enter', claudeBinary: 'auto', defaultModel: '', defaultMode: 'default', defaultEffort: '', worktreeRoot: '', idleTimeoutMin: 30, notifications: false, promptSuggestions: false, showThinking: false, recentProjects: [PROJ, OTHER], ui: { sidebar: true, panel: false, view: 'normal' } };
const S = (id) => {
  const s = sessions.get(id);
  if (!s) throw new Error('Unknown session: ' + id);
  return s;
};
const listSessions = () => [...sessions.values()].map((s) => s.summary()).sort((a, b) => (b.lastModified || 0) - (a.lastModified || 0) || (a.id < b.id ? -1 : 1));

const methods = {
  'app.info': () => ({ version: VERSION, home: HOME, user: 'tester', displayName: 'Test Person', hostname: 'mock', platform: 'linux', node: process.version, shell: 'none', isRoot: false, configDir: `${HOME}/.config/pilcrow`, claude: { path: '/usr/bin/claude', source: 'bundled', version: '2.0.0', bundled: { path: '/usr/bin/claude', version: '2.0.0' }, system: null }, terminal: { python: null, script: null, pty: false }, git: true, gateway: { baseUrl: 'https://gateway.example.com', hasToken: true, loggedIn: false, provider: 'anthropic', source: 'user', model: null } }),
  'app.quit': () => true,
  'config.get': () => config,
  'config.set': (p) => {
    for (const [k, v] of Object.entries(p)) config[k] = k === 'ui' ? { ...config.ui, ...v } : v;
    return config;
  },
  'sessions.list': () => listSessions(),
  'sessions.refresh': () => listSessions(),
  'session.open': ({ id }) => {
    const s = S(id);
    return { state: s.state(), messages: s.messages, inflight: s.inflight, meta: { title: s.title } };
  },
  'session.create': ({ cwd, title }) => {
    const id = `sess-${uuid()}`;
    const s = new Sess({ id, title: title || 'New session', cwd: cwd || PROJ, touched: Date.now() });
    s.createdAt = Date.now();
    sessions.set(id, s);
    listChanged();
    return { id, cwd: s.cwd, worktree: null };
  },
  'session.send': ({ id, text = '', now: steer }) => {
    const s = S(id);
    if (!text) return null;
    const u = uuid();
    if (s.isBusy()) {
      if (steer) {
        s.held.unshift({ uuid: u, text, at: Date.now() });
        s.aborted = true;
      } else s.held.push({ uuid: u, text, at: Date.now() });
      s.emitState();
      return u;
    }
    deliver(s, { uuid: u, text });
    return u;
  },
  'session.interrupt': ({ id }) => {
    S(id).aborted = true;
  },
  'session.permission': ({ id, requestId, decision }) => {
    const p = S(id).pending.get(requestId);
    if (!p) return false;
    p.resolve(decision);
    return true;
  },
  'session.queue.remove': ({ id, uuid: u }) => {
    const s = S(id);
    s.held = s.held.filter((h) => h.uuid !== u);
    s.emitState();
    return true;
  },
  'session.queue.update': ({ id, uuid: u, text }) => {
    const h = S(id).held.find((h) => h.uuid === u);
    if (h) h.text = text;
    S(id).emitState();
    return Boolean(h);
  },
  'session.queue.sendNow': ({ id, uuid: u }) => {
    const s = S(id);
    const i = s.held.findIndex((h) => h.uuid === u);
    if (i < 0) return false;
    const [h] = s.held.splice(i, 1);
    s.held.unshift(h);
    s.aborted = true;
    s.emitState();
    return true;
  },
  'session.setMode': ({ id, mode }) => {
    S(id).mode = mode;
    S(id).emitState();
  },
  'session.setModel': ({ id, model }) => {
    S(id).model = model || '';
    S(id).emitState();
  },
  'session.setEffort': ({ id, effort }) => {
    S(id).effort = effort || '';
    S(id).emitState();
  },
  'session.setCwd': ({ id, cwd }) => {
    S(id).cwd = cwd;
    S(id).emitState();
    listChanged();
    return true;
  },
  'session.rename': ({ id, title }) => {
    S(id).title = title;
    listChanged();
  },
  'session.delete': ({ id }) => {
    sessions.delete(id);
    listChanged();
  },
  'session.archive': ({ id, archived }) => {
    S(id).archived = Boolean(archived);
    listChanged();
  },
  'session.pin': ({ id, pinned }) => {
    S(id).pinned = Boolean(pinned);
    listChanged();
  },
  'session.fork': ({ id, title }) => {
    const src = S(id);
    const nid = `sess-${uuid()}`;
    const s = new Sess({ id: nid, title: title || `${src.title} (fork)`, cwd: src.cwd, touched: Date.now(), messages: [...src.messages] });
    sessions.set(nid, s);
    listChanged();
    return { id: nid };
  },
  'session.close': ({ id }) => {
    const s = sessions.get(id);
    if (s) {
      s.live = false;
      s.status = 'stopped';
      s.emitState();
    }
  },
  'session.contextUsage': () => null,
  'session.rewind': () => ({ canRewind: false, error: 'No checkpoints in the mock' }),
  'session.mcpStatus': () => [],
  'session.reloadSkills': () => 0,
  'session.gateway': () => ({ baseUrl: 'https://gateway.example.com', hasToken: true }),
  'session.subagent': () => [],
  'session.stopTask': () => null,
  'skills.list': () => ({ roots: { user: `${HOME}/.claude/skills`, project: null }, user: [], project: [], plugin: [] }),
  'mcp.list': () => ({ file: `${HOME}/.claude.json`, servers: [] }),
  'memory.list': () => ({ notes: [] }),
  'project.fileHistory': () => ({}),
  'schedules.list': () => [],
  'inbox.list': () => ({ unread: 0, items: [] }),
  'fleet.state': () => {
    const live = Object.fromEntries([...sessions.values()].filter((s) => s.live).map((s) => [s.id, s.state()]));
    const dayStart = new Date();
    dayStart.setHours(0, 0, 0, 0);
    const perSession = [...sessions.values()].filter((s) => s.touched >= dayStart.getTime() && s.usage.costUsd > 0).map((s) => ({ id: s.id, title: s.title, cost: s.usage.costUsd })).sort((a, b) => b.cost - a.cost);
    return { live, today: { cost: perSession.reduce((a, b) => a + b.cost, 0), sessions: perSession.length, perSession: perSession.slice(0, 8) }, spend: perSession.map((p) => ({ at: Date.now() - 600e3, id: p.id, delta: p.cost })) };
  },
  'timeline.steps': ({ id }) => ({ cwd: S(id).cwd, enabled: false, reason: 'Timeline is off in the mock', steps: [] }),
  'models.gateway': () => ({ ok: true, base: 'https://gateway.example.com', url: 'https://gateway.example.com/v1/models', models: [{ id: 'claude-mock-1', displayName: 'Claude Mock 1', created: null }, { id: 'claude-mock-fast', displayName: 'Claude Mock Fast', created: null }], fetchedAt: Date.now() }),
  'git.info': ({ cwd }) => ({ isRepo: true, root: cwd, branch: 'main', head: 'abc1234', changed: 2, untracked: 1, insertions: 14, deletions: 3 }),
  'git.diff': () => [],
  'git.branches': () => ({ current: 'main', branches: ['main'] }),
  'git.log': () => [],
  'fs.list': ({ dir }) => ({ dir, entries: [] }),
  'fs.browse': ({ dir }) => ({ dir, parent: null, dirs: [], files: [] }),
  'fs.index': () => ['web/src/lib/store.ts', 'web/src/lib/transcript.ts', 'server/main.js', 'README.md'],
  'fs.read': ({ path: p }) => ({ path: p, size: 10, mtime: Date.now(), text: '// mock file\n' }),
  'fs.exists': () => true,
  'term.list': () => [],
  'shell.which': () => null,
  // ---- test controls
  'mock.reset': () => {
    seed();
    listChanged();
    return true;
  },
  'mock.pace': ({ ms }) => {
    pace = Math.max(0, Number(ms) || 0);
    return pace;
  },
  'mock.sessions': () => listSessions().map((s) => s.id),
  'mock.touch': ({ id }) => {
    S(id).touched = Date.now();
    listChanged();
    return true;
  },
  'mock.state': ({ id }) => S(id).state(),
};

// ---------------------------------------------------------------- HTTP + WS

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/api/hello') {
    res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ app: 'pilcrow', version: VERSION, pid: process.pid, busy: 0, mock: true }));
    return;
  }
  let rel = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'index.html';
  let file = path.join(DIST, rel);
  if (!file.startsWith(DIST)) return void res.writeHead(403).end();
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html');
  if (!fs.existsSync(file)) return void res.writeHead(500, { 'content-type': 'text/plain' }).end('web/dist is missing: run `npm run build` first.');
  const ext = path.extname(file);
  const headers = { 'content-type': MIME[ext] || 'application/octet-stream', 'cache-control': 'no-cache' };
  // The same policy as the real server, so a test catches anything it would block.
  if (ext === '.html') headers['content-security-policy'] = "frame-ancestors 'none'; default-src 'self'; img-src 'self' data: blob: http://127.0.0.1:* http://localhost:*; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-src http: https:; worker-src 'self' blob:";
  res.writeHead(200, headers);
  fs.createReadStream(file).pipe(res);
});

const wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 * 1024 });
server.on('upgrade', (req, socket, head) => {
  if (new URL(req.url, 'http://x').pathname !== '/ws') {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
});
wss.on('connection', (ws) => {
  clients.add(ws);
  ws.on('close', () => clients.delete(ws));
  ws.on('message', async (raw) => {
    let m;
    try {
      m = JSON.parse(raw.toString());
    } catch {
      return;
    }
    if (m.t !== 'req') return;
    const fn = methods[m.m];
    const send = (obj) => ws.readyState === 1 && ws.send(JSON.stringify(obj));
    if (!fn) {
      console.log(`[mock] unknown method ${m.m}`);
      return send({ t: 'res', id: m.id, ok: false, e: { message: `Unknown method ${m.m}` } });
    }
    try {
      const r = await fn(m.p || {});
      send({ t: 'res', id: m.id, ok: true, r: r === undefined ? null : r });
    } catch (e) {
      send({ t: 'res', id: m.id, ok: false, e: { message: e?.message || String(e) } });
    }
  });
  ws.send(JSON.stringify({ t: 'ev', e: 'hello', d: { version: VERSION } }));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`Pilcrow mock server: http://127.0.0.1:${PORT}/  (${sessions.size} sessions${LONG ? `, one with ${LONG} turns` : ''})`);
});
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => process.exit(0));
