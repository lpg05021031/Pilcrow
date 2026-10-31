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
import { EventEmitter } from 'node:events';
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
/** Canned results for RPC methods (mock.answer): a screenshot or test can show any state without a real backend. */
const overrides = new Map();

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
function turn(sid, at, { prompt, reply, tools = [], narration, thinking }) {
  const out = [msg.user(sid, prompt, at)];
  let t = at + 800;
  if (thinking) {
    out.push(msg.assistant(sid, [{ type: 'thinking', thinking, signature: '' }], t));
    t += 300;
  }
  if (narration) {
    out.push(msg.assistant(sid, [{ type: 'text', text: narration }], t));
    t += 400;
  }
  for (const tool of tools) {
    const id = `toolu_${uuid()}`;
    out.push(msg.assistant(sid, [{ type: 'tool_use', id, name: tool.name, input: tool.input }], t));
    t += 120;
    // A sub-agent's own work arrives with parent_tool_use_id set, nested under the Agent card.
    for (const c of tool.children || []) {
      if (c.text !== undefined) {
        out.push({ ...msg.assistant(sid, [{ type: 'text', text: c.text }], t), parent_tool_use_id: id });
      } else {
        const cid = `toolu_${uuid()}`;
        out.push({ ...msg.assistant(sid, [{ type: 'tool_use', id: cid, name: c.name, input: c.input }], t), parent_tool_use_id: id });
        t += c.ms || 200;
        out.push({ ...msg.toolResult(sid, cid, c.result || 'ok', t, c.structured, c.error), parent_tool_use_id: id });
      }
      t += 100;
    }
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

// ---- the extreme shapes the benchmarks (qt/tests/bench) open: long prose, big outputs, big diffs,
// thinking, sub-agents with their own work, failures. Deterministic, so runs compare.
const WORDS = 'the parser reads each line and keeps a small stack of open containers so a truncated document still closes cleanly while the scheduler hands every stage its configuration without side effects and the tests cover both paths'.split(' ');
const sentence = (i, n = 14) => {
  const out = [];
  for (let k = 0; k < n; k++) out.push(WORDS[(i * 7 + k * 3) % WORDS.length]);
  const s = out.join(' ');
  return s[0].toUpperCase() + s.slice(1) + '.';
};
const paragraph = (i, sentences = 3) => Array.from({ length: sentences }, (_, k) => sentence(i + k, 10 + ((i + k) % 7))).join(' ');
function longReply(i) {
  return [
    `## Module ${i}: what it does`,
    paragraph(i, 3),
    `- ${sentence(i + 1, 8)}\n- ${sentence(i + 2, 9)}\n- ${sentence(i + 3, 7)}`,
    paragraph(i + 4, 2),
    '```ts\n' + Array.from({ length: 14 }, (_, k) => `export function step${k}(x: number): number {\n  return x * ${k} + ${i}; // ${WORDS[(i + k) % WORDS.length]}\n}`).join('\n') + '\n```',
    `| stage | files | notes |\n|---|---|---|\n| ${i % 7} | ${(i % 5) + 1} | ${sentence(i, 4)} |\n| ${(i + 1) % 7} | ${(i % 3) + 2} | ${sentence(i + 1, 4)} |`,
    paragraph(i + 5, 2),
    'Inline `code` and a path `web/src/lib/store.ts:42` to round it off.',
  ].join('\n\n');
}
const BIG_BASH = (n, fail = false) => {
  const lines = Array.from({ length: n }, (_, k) => `[${String(k).padStart(4, '0')}] ${k % 9 === 0 ? `warning: unused variable in src/mod${k}.ts` : `compiled src/mod${k}.ts (${((k * 37) % 900) + 100} bytes)`}`);
  const out = lines.join('\n');
  return { name: 'Bash', input: { command: `npm run build 2>&1 | tail -n ${n}`, description: 'Build and show the log' }, result: fail ? `Error: Exit code 1\n${out}` : out, structured: fail ? undefined : { stdout: out, stderr: '', interrupted: false }, error: fail, ms: 900 };
};
const BIG_EDIT = (file, n) => {
  const lines = [];
  for (let k = 0; k < n; k++) {
    const m = k % 4;
    lines.push(m === 1 ? `-  const v${k} = read(${k});` : m === 2 ? `+  const v${k} = readSafe(${k}); // checked` : `   use(v${k});`);
  }
  const ctx = Math.ceil((n * 3) / 4);
  return { name: 'Edit', input: { file_path: `${PROJ}/${file}`, old_string: 'read(', new_string: 'readSafe(' }, result: `The file ${PROJ}/${file} has been updated.`, structured: { filePath: `${PROJ}/${file}`, oldString: 'read(', newString: 'readSafe(', structuredPatch: [{ oldStart: 40, oldLines: ctx, newStart: 40, newLines: ctx, lines }] } };
};
const AGENT = (i) => ({ name: 'Agent', input: { description: `Explore module ${i}`, prompt: `Find every call site of parse() in module ${i} and report whether each is covered by a test.`, subagent_type: 'Explore' }, result: `Found 3 call sites in module ${i}; all three are covered by tests.`, ms: 2500, children: [{ text: "I'll search for the call sites first." }, GREP('parse\\('), READ(`src/mod${i}.ts`, 80), READ(`src/mod${i}.test.ts`, 60), { text: 'Three call sites, all covered by tests.' }] });

/** One turn of the heavy conversation: six shapes, cycling. */
function heavyTurn(sid, at, i) {
  const kind = i % 6;
  if (kind === 0) return turn(sid, at, { prompt: `Turn ${i + 1}: explain module ${i} in detail`, reply: longReply(i) });
  if (kind === 1) return turn(sid, at, { prompt: `Turn ${i + 1}: check file ${i} and its tests`, narration: `Reading file ${i}, its tests and the call sites.`, tools: [READ(`src/mod${i}.ts`, 60), READ(`src/mod${i}.test.ts`, 40), GREP(`mod${i}`), GREP(`import.*mod${i}`), READ(`docs/mod${i}.md`, 20)], reply: `File ${i} is fine. ${sentence(i, 12)}` });
  if (kind === 2) return turn(sid, at, { prompt: `Turn ${i + 1}: fix the unchecked read in module ${i}`, thinking: paragraph(i, 4), tools: [BIG_EDIT(`src/mod${i}.ts`, 48), BIG_BASH(300)], reply: `Fixed module ${i}. ${sentence(i, 10)}\n\n\`\`\`sh\nnpm run check\n\`\`\`` });
  if (kind === 3) return turn(sid, at, { prompt: `Turn ${i + 1}: find every call site of parse in module ${i}`, thinking: paragraph(i, 2), tools: [AGENT(i)], reply: paragraph(i, 3) });
  if (kind === 4) return turn(sid, at, { prompt: `Turn ${i + 1}: run the build for module ${i}`, narration: 'Building first.', tools: [BIG_BASH(40, true), EDIT(`src/mod${i}.ts`, 'recieve', 'receive'), BIG_BASH(20)], reply: `The build failed on an unused variable, so I fixed it. ${sentence(i, 9)}` });
  return turn(sid, at, { prompt: `Turn ${i + 1}: anything else?`, reply: `Nothing else stands out in module ${i}. Moving on.` });
}

/** The simple shapes of MOCK_LONG, as a function so mock.generate can build them too. */
function mixedTurn(sid, at, i) {
  const kind = i % 4;
  return turn(sid, at, kind === 0 ? { prompt: `Turn ${i + 1}: what does module ${i} do?`, reply: `Module ${i} parses the configuration for stage ${i % 7} and hands it to the scheduler. It is small and has no side effects.` } : kind === 1 ? { prompt: `Turn ${i + 1}: check file ${i}`, narration: `Reading file ${i} and its tests.`, tools: [READ(`src/mod${i}.ts`, 30 + i), GREP(`mod${i}`)], reply: `File ${i} is fine: it exports two functions and both are covered by tests.` } : kind === 2 ? { prompt: `Turn ${i + 1}: fix the typo in module ${i}`, tools: [EDIT(`src/mod${i}.ts`, 'recieve', 'receive'), BASH('npm run check', 'Type-check', 'ok')], reply: `Fixed the typo in module ${i}; the type check passes.` } : { prompt: `Turn ${i + 1}: anything else?`, reply: `Nothing else stands out in module ${i}. Moving on.` });
}

const DAY = 86400e3;
const now = Date.now();

/** @type {Map<string, Sess>} */
const sessions = new Map();
/** What the sessions do, for the dispatch engine below: 'idle' (s), 'permission' ({ s, req }), 'resolved' ({ s, requestId }). */
const hooks = new EventEmitter();
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
    this.extra = {}; // fields the tests lay over state() (mock.patch): tasks, mcp, …
  }
  summary() {
    return { id: this.id, title: this.title, firstPrompt: this.messages[0]?.message?.content?.[0]?.text, cwd: this.cwd, project: this.cwd, gitBranch: 'main', lastModified: this.touched, createdAt: this.createdAt, archived: this.archived, pinned: this.pinned, worktree: null, scheduled: null, dispatch: this.dispatch || null, status: this.status, live: this.live, pending: this.pending.size };
  }
  state() {
    return {
      id: this.id, status: this.status, live: this.live, cwd: this.cwd, mode: this.mode, model: this.model, effort: this.effort, activeModel: 'claude-mock-1',
      usage: this.usage, error: null, suggestion: null, compacting: false, mcp: [], activity: this.activity, lastActivity: this.lastActivity, turnStartedAt: this.turnStartedAt, processStartedAt: this.live ? this.createdAt : null, lastReply: this.lastReply,
      unattended: false, unattendedDenials: 0, pending: [...this.pending.values()].map((p) => p.req), tasks: [], bgTasks: [], queued: [], held: this.held.map((h) => ({ uuid: h.uuid, text: h.text, images: 0, files: [], at: h.at })),
      init: this.init, commands: null, models: null,
      ...this.extra,
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
  if (/\bmarathon\b/.test(t)) return 'marathon';
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

async function streamThinking(s, index, text) {
  s.out(msg.stream(s.id, { type: 'content_block_start', index, content_block: { type: 'thinking', thinking: '' } }));
  const pieces = text.match(/\S+\s*|\s+/g) || [];
  for (const piece of pieces) {
    if (s.aborted) return;
    s.out(msg.stream(s.id, { type: 'content_block_delta', index, delta: { type: 'thinking_delta', thinking: piece } }));
    s.activity = { kind: 'thinking', label: 'Thinking', at: Date.now() };
    if (pace) await sleep(pace);
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
    } else if (p.thinking !== undefined) {
      await streamThinking(s, i, p.thinking);
      content.push({ type: 'thinking', thinking: p.thinking, signature: '' });
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
      hooks.emit('permission', { s, req });
    });
    s.pending.delete(requestId);
    if (s.status === 'requires_action') s.status = 'running';
    broadcast('session.permissionResolved', { id: s.id, requestId, decision: decision.behavior });
    hooks.emit('resolved', { s, requestId });
    s.emitState();
    listChanged();
    if (decision.behavior === 'deny') {
      s.record(msg.toolResult(s.id, id, decision.message || 'The user declined this action.', Date.now(), undefined, true));
      return 'denied';
    }
  }
  for (const c of tool.children || []) {
    // the sub-agent's work, recorded whole under the Agent card as it comes in
    if (c.text !== undefined) s.record({ ...msg.assistant(s.id, [{ type: 'text', text: c.text }], Date.now()), parent_tool_use_id: id });
    else {
      const cid = `toolu_${uuid()}`;
      s.record({ ...msg.assistant(s.id, [{ type: 'tool_use', id: cid, name: c.name, input: c.input }], Date.now()), parent_tool_use_id: id });
      await sleep(Math.min(c.ms || 120, 120));
      s.record({ ...msg.toolResult(s.id, cid, c.result || 'ok', Date.now(), c.structured, c.error), parent_tool_use_id: id });
    }
  }
  await sleep(Math.min(tool.ms || 350, 350));
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
    } else if (script === 'marathon') {
      // A long turn with everything in it: thinking, prose, quiet steps, a big edit, a big command, a
      // sub-agent, and a long answer. What a streaming benchmark watches.
      for (let k = 0; k < 4; k++) {
        await say(s, [{ thinking: paragraph(k, 3) }, { text: `Step ${k + 1}. ${paragraph(k, 2)}` }]);
        if (s.aborted) throw new Error('aborted');
        await useTool(s, READ(`src/mod${k}.ts`, 60));
        await useTool(s, GREP(`mod${k}`));
        await useTool(s, BIG_BASH(200));
        await useTool(s, BIG_EDIT(`src/mod${k}.ts`, 40));
        if (s.aborted) throw new Error('aborted');
      }
      await say(s, [{ text: 'Now a sub-agent checks the call sites.' }]);
      await useTool(s, AGENT(7));
      if (s.aborted) throw new Error('aborted');
      await say(s, [{ text: `${longReply(99)}\n\n${longReply(100)}` }]);
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
  hooks.emit('idle', s);
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

// ---------------------------------------------------------------- dispatch: one job across many repositories
// A small model of server/dispatch_core.js: the same states and events, runs that are mock sessions fed
// the brief (so they stream, ask and finish like any mock turn), a canary wave, rules that answer a
// permission for every repository, review, finish and a report in the inbox.

const REPOS = [
  { name: 'design-system', branch: 'main', size: 'small', kind: 'node', deps: [], hasClaudeMd: true },
  { name: 'web-ui', branch: 'main', size: 'medium', kind: 'node', deps: ['design-system'] },
  { name: 'docs', branch: 'main', size: 'small', kind: 'node', deps: [] },
  { name: 'api-gateway', branch: 'main', size: 'medium', kind: 'node', deps: [], changed: 2 },
  { name: 'cli', branch: 'main', size: 'small', kind: 'node', deps: [] },
  { name: 'legacy-api', branch: 'release/4', size: 'large', kind: 'go', deps: [] },
  { name: 'infra', branch: 'main', size: 'small', kind: null, deps: [] },
  { name: 'mobile', branch: 'main', size: 'large', kind: 'node', deps: ['design-system'] },
].map((r) => ({
  ...r,
  path: `${HOME}/code/${r.name}`,
  deps: r.deps.map((n) => `${HOME}/code/${n}`),
  dirty: Boolean(r.changed),
  changed: r.changed || 0,
  untracked: 0,
  ahead: 0,
  behind: 0,
  remote: { host: 'github', owner: 'acme', repo: r.name, url: `git@github.com:acme/${r.name}.git` },
  files: { small: 120, medium: 900, large: 4000 }[r.size],
  packageName: r.kind === 'node' ? `@acme/${r.name}` : null,
  lastSession: null,
  hasClaudeMd: Boolean(r.hasClaudeMd),
}));
const REPO_BY_PATH = new Map(REPOS.map((r) => [r.path, r]));
const D_TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);
const D_ACTIVE = new Set(['preparing', 'running', 'needs_action']);
const D_DEFAULTS = { mode: 'acceptEdits', allowedTools: [], model: '', effort: '', parallel: 3, maxMinutes: 20, costCap: 0, canary: 1, learn: true, prepare: '', copy: [], verify: '', verifyRetry: true, commitMessage: '', pr: false, report: 'table' };
/** @type {any[]} */
let jobs = [];
/** @type {any[]} */
let inboxItems = [];

function dRender(brief, job, t, extras = {}) {
  let out = String(brief || '').replace(/\{\{\s*([a-zA-Z_.]+)\s*\}\}/g, (m, k) => ({ 'repo.name': t.name, 'repo.path': t.path, 'repo.branch': t.base || '', 'job.name': job.name, notes: t.notes || '' })[k] ?? m);
  if (extras.learned?.length) out += '\n\nThe same work was already done in ' + extras.learned.length + ' repositories. Follow the same approach where it fits:\n' + extras.learned.map((l) => `- ${l.name}: ${l.summary}`).join('\n');
  return out.trim();
}
function dKey(req) {
  const tool = req.toolName || '';
  const input = req.input || {};
  if (tool === 'Bash') return `Bash:${String(input.command || '').replace(/\s+/g, ' ').trim()}`;
  if (input.file_path) return `${tool}:${String(input.file_path).split('/').slice(-2).join('/')}`;
  return `${tool}:${JSON.stringify(input)}`;
}
function dLabel(req) {
  const [tool, ...rest] = dKey(req).split(':');
  return `${tool}: ${rest.join(':')}`;
}
function dSummary(job) {
  const counts = { queued: 0, preparing: 0, running: 0, needs_action: 0, done: 0, failed: 0, stopped: 0, skipped: 0 };
  let cost = 0;
  let needs = 0;
  let files = 0;
  for (const r of job.runs) {
    counts[r.state] = (counts[r.state] || 0) + 1;
    cost += r.cost || 0;
    needs += (r.pending || []).length;
    files += (r.files || []).length;
  }
  const { runs, ...rest } = job;
  return { ...rest, counts, total: runs.length, cost: Math.round(cost * 10000) / 10000, needsAction: needs, files, canaryWaiting: job.state === 'reviewing' && job.wave === 'canary' };
}
function dEmit(job, runs = null) {
  job.updatedAt = Date.now();
  broadcast('dispatch.changed', { id: job.id, job: dSummary(job), runs: runs || job.runs, all: !runs });
}
const dJob = (id) => {
  const j = jobs.find((x) => x.id === id);
  if (!j) throw new Error('No such job.');
  return j;
};
function dNormalize(input, cur) {
  const settings = { ...D_DEFAULTS, ...(cur?.settings || {}), ...(input.settings || {}) };
  settings.parallel = Math.max(1, Math.min(16, Number(settings.parallel) || 3));
  settings.canary = Math.max(0, Math.min(8, Number.isFinite(Number(settings.canary)) ? Number(settings.canary) : 1));
  const name = String(input.name ?? cur?.name ?? '').trim() || 'Untitled job';
  const targets = (input.targets ?? cur?.targets ?? []).map((t) => ({ path: String(t.path || ''), name: String(t.name || String(t.path || '').split('/').pop()), base: String(t.base || ''), notes: String(t.notes || ''), size: t.size || REPO_BY_PATH.get(t.path)?.size || null, deps: Array.isArray(t.deps) ? t.deps : REPO_BY_PATH.get(t.path)?.deps || [] }));
  return { id: cur?.id || `job-${uuid()}`, name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'job', brief: String(input.brief ?? cur?.brief ?? ''), root: String(input.root ?? cur?.root ?? `${HOME}/code`), targets, settings, state: cur?.state || 'draft', wave: cur?.wave || null, paused: cur?.paused || false, createdAt: cur?.createdAt || Date.now(), updatedAt: Date.now(), startedAt: cur?.startedAt || null, finishedAt: cur?.finishedAt || null, plan: cur?.plan || null, rules: cur?.rules || [], runs: cur?.runs || [], learned: cur?.learned || [], forecast: cur?.forecast || null, capReached: false, report: cur?.report || null, stoppedReason: null };
}
function dLevels(targets) {
  const paths = new Set(targets.map((t) => t.path));
  const level = new Map();
  let changed = true;
  for (const t of targets) level.set(t.path, 0);
  while (changed) {
    changed = false;
    for (const t of targets) {
      const l = Math.max(0, ...(t.deps || []).filter((d) => paths.has(d)).map((d) => level.get(d) + 1));
      if (l !== level.get(t.path) && l < 10) {
        level.set(t.path, l);
        changed = true;
      }
    }
  }
  return level;
}
function dPlan(job) {
  const level = dLevels(job.targets);
  const targets = job.targets.map((t) => {
    const repo = REPO_BY_PATH.get(t.path);
    const notes = [];
    let verdict = 'ok';
    const base = t.base || repo?.branch || 'main';
    if (!repo) {
      verdict = 'blocked';
      notes.push({ kind: 'blocked', text: 'Not a git repository.' });
    } else {
      if (base !== repo.branch) {
        verdict = 'blocked';
        notes.push({ kind: 'blocked', text: `Branch ${base} not found. Pick another branch or deselect this repository.` });
      }
      if (repo.dirty) {
        if (verdict === 'ok') verdict = 'warning';
        notes.push({ kind: 'warning', text: `${repo.changed} uncommitted changes in the main checkout. The run starts from the branch's last commit; they are not in it.` });
      }
      if (!repo.hasClaudeMd) notes.push({ kind: 'note', text: 'No CLAUDE.md: the brief may want to say more about this repository.' });
    }
    if (!job.brief.trim()) {
      verdict = 'blocked';
      notes.push({ kind: 'blocked', text: 'The brief is empty.' });
    }
    const deps = (t.deps || []).filter((d) => job.targets.some((x) => x.path === d));
    if (deps.length) notes.push({ kind: 'note', text: `Runs after ${deps.map((d) => job.targets.find((x) => x.path === d)?.name || d).join(', ')} and receives ${deps.length === 1 ? 'its' : 'their'} summary.` });
    return { path: t.path, name: t.name, base, verdict, notes, brief: dRender(job.brief, job, { ...t, base }), level: level.get(t.path) || 0, deps, size: t.size };
  });
  const n = targets.filter((t) => t.verdict !== 'blocked').length;
  const levels = targets.length ? Math.max(...targets.map((t) => t.level)) + 1 : 0;
  return { at: Date.now(), targets, levels, worst: { minutes: Math.ceil((n * job.settings.maxMinutes) / job.settings.parallel), cost: job.settings.costCap || null, runs: n, levels }, ok: targets.filter((t) => t.verdict === 'ok').length, warnings: targets.filter((t) => t.verdict === 'warning').length, blocked: targets.filter((t) => t.verdict === 'blocked').length };
}
function dRunBySession(sid) {
  for (const j of jobs) {
    const r = j.runs.find((x) => x.sessionId === sid && D_ACTIVE.has(x.state));
    if (r) return { job: j, run: r };
  }
  return null;
}
function dStartRun(job, run) {
  run.state = 'running';
  run.startedAt = Date.now();
  run.now = 'starting';
  const extras = { learned: job.wave === 'main' && job.settings.learn ? job.learned : [] };
  run.brief = dRender(job.brief, job, run, extras);
  const s = new Sess({ id: `dj-${uuid()}`, title: `${job.name} · ${run.name}`, cwd: run.path, touched: Date.now() });
  s.dispatch = { jobId: job.id, runId: run.id, name: job.name, target: run.name };
  s.usage.costUsd = 0;
  sessions.set(s.id, s);
  run.sessionId = s.id;
  run.worktree = { path: `${HOME}/.local/share/pilcrow/worktrees/${run.name}/${job.slug}-${s.id.slice(-4)}`, branch: `pilcrow/${job.slug}`, base: run.base, repoRoot: run.path };
  dEmit(job, [run]);
  listChanged();
  setTimeout(() => deliver(s, { text: run.brief }), 30);
}
function dPump(job) {
  if (job.state !== 'running' || job.paused) return;
  const active = job.runs.filter((r) => D_ACTIVE.has(r.state)).length;
  const eligible = (r) => r.state === 'queued' && (job.wave === 'main' || r.wave === 'canary');
  for (const r of job.runs.filter(eligible)) {
    const bad = r.dependsOn.find((d) => job.runs.some((x) => x.path === d && D_TERMINAL.has(x.state) && x.state !== 'done'));
    if (bad) {
      r.state = 'skipped';
      r.reason = `Depends on ${job.runs.find((x) => x.path === bad)?.name}, which did not finish.`;
      r.endedAt = Date.now();
    }
  }
  const ready = job.runs.filter(eligible).filter((r) => r.dependsOn.every((d) => !job.runs.some((x) => x.path === d) || job.runs.some((x) => x.path === d && x.state === 'done')));
  ready.sort((a, b) => a.level - b.level || a.name.localeCompare(b.name));
  for (const r of ready.slice(0, Math.max(0, job.settings.parallel - active))) dStartRun(job, r);
  dSettle(job);
}
function dSettle(job) {
  if (job.state !== 'running' || job.runs.some((r) => D_ACTIVE.has(r.state))) return;
  const queued = job.runs.filter((r) => r.state === 'queued');
  if (job.wave === 'canary' && queued.length) {
    job.state = 'reviewing';
    job.learned = job.runs.filter((r) => r.wave === 'canary' && r.state === 'done').map((r) => ({ name: r.name, summary: r.summary }));
    const canary = job.runs.filter((r) => r.wave === 'canary' && r.state === 'done');
    job.forecast = canary.length ? { cost: Math.round(queued.length * (canary[0].cost || 0.01) * 100) / 100, minutes: Math.max(1, Math.round((queued.length * Math.max(1, canary[0].durationMs || 60e3)) / job.settings.parallel / 60e3)), from: canary.map((r) => r.name), runs: queued.length } : null;
    dEmit(job);
    return;
  }
  if (queued.length) return;
  job.state = 'reviewing';
  job.wave = 'done';
  dEmit(job);
  dMaybeFinish(job);
}
function dEndRun(job, run, state, reason, s) {
  if (D_TERMINAL.has(run.state)) return;
  run.state = state;
  run.reason = reason || null;
  run.now = null;
  run.pending = [];
  run.endedAt = Date.now();
  run.durationMs = run.endedAt - (run.startedAt || run.endedAt);
  if (s) {
    run.cost = Math.round((s.usage.costUsd || 0) * 10000) / 10000;
    run.summary = s.lastReply?.text || '';
    const edits = s.messages.flatMap((m) => (m.type === 'assistant' ? m.message.content : [])).filter((c) => c.type === 'tool_use' && (c.name === 'Edit' || c.name === 'Write'));
    run.files = edits.map((c) => ({ path: String(c.input.file_path || '').replace(`${PROJ}/`, ''), add: 3, del: 1 }));
    run.add = run.files.length * 3;
    run.del = run.files.length;
  }
  dEmit(job, [run]);
  dPump(job);
}
hooks.on('idle', (s) => {
  const hit = dRunBySession(s.id);
  if (hit) dEndRun(hit.job, hit.run, 'done', null, s);
});
hooks.on('permission', ({ s, req }) => {
  const hit = dRunBySession(s.id);
  if (!hit) return;
  const { job, run } = hit;
  const key = dKey(req);
  if (job.rules.some((r) => r.key === key)) {
    const p = s.pending.get(req.requestId);
    p?.resolve({ behavior: 'allow', updatedInput: req.input });
    return;
  }
  run.pending = [...run.pending.filter((p) => p.requestId !== req.requestId), { requestId: req.requestId, toolName: req.toolName, displayName: req.toolName, key, label: dLabel(req), interactive: Boolean(req.interactive), at: req.at }];
  run.state = 'needs_action';
  run.now = dLabel(req);
  dEmit(job, [run]);
});
hooks.on('resolved', ({ s, requestId }) => {
  const hit = dRunBySession(s.id);
  if (!hit) return;
  const { job, run } = hit;
  run.pending = run.pending.filter((p) => p.requestId !== requestId);
  if (!run.pending.length && run.state === 'needs_action') {
    run.state = 'running';
    run.now = 'continuing';
  }
  dEmit(job, [run]);
});
function dReport(job) {
  return { at: Date.now(), targets: job.runs.map((r) => ({ name: r.name, path: r.path, state: r.state, reason: r.reason, summary: r.summary, files: (r.files || []).length, add: r.add, del: r.del, cost: r.cost, durationMs: r.durationMs, branch: r.worktree?.branch || null, commit: r.commit, pr: r.pr, finished: r.finished, verify: null })), rules: job.rules, plan: job.plan ? { ok: job.plan.ok, warnings: job.plan.warnings, blocked: job.plan.blocked, levels: job.plan.levels } : null, cost: job.runs.reduce((n, r) => n + (r.cost || 0), 0), durationMs: (job.finishedAt || Date.now()) - (job.startedAt || Date.now()) };
}
function dMaybeFinish(job) {
  if (job.state !== 'reviewing' || job.wave === 'canary') return;
  if (!job.runs.every((r) => D_TERMINAL.has(r.state) && (r.finished || r.state !== 'done'))) return;
  job.state = 'finished';
  job.finishedAt = Date.now();
  job.report = dReport(job);
  const done = job.runs.filter((r) => r.state === 'done').length;
  const failed = job.runs.filter((r) => r.state === 'failed' || r.state === 'stopped').length;
  inboxItems.unshift({ id: `ib-${uuid()}`, kind: 'dispatch', jobId: job.id, scheduleId: null, scheduleName: job.name, sessionId: null, cwd: job.root, startedAt: job.startedAt, endedAt: job.finishedAt, durationMs: job.report.durationMs, status: failed && !done ? 'failed' : 'completed', reason: failed ? `${failed} of ${job.runs.length} did not finish` : null, summary: `${done} of ${job.runs.length} repositories done.\n` + job.runs.map((r) => `- ${r.name}: ${r.state}${r.summary ? ' — ' + r.summary.slice(0, 120) : ''}`).join('\n'), cost: job.report.cost, files: job.runs.flatMap((r) => (r.files || []).map((f) => ({ ...f, repo: r.name }))).slice(0, 60), read: false, manual: true, dispatch: job.report });
  broadcast('inbox.changed', inboxItems.filter((i) => !i.read).length);
  dEmit(job);
}
function seedDispatch() {
  jobs = [];
  inboxItems = [];
  const t0 = now - 26 * 3600e3;
  const old = dNormalize({ name: 'Pin node 22 in CI', brief: 'Pin the Node version in CI to 22 in {{repo.name}}.', targets: ['cli', 'docs', 'infra'].map((n) => ({ path: `${HOME}/code/${n}`, name: n, base: 'main' })), settings: { canary: 0 } }, null);
  old.plan = dPlan(old);
  old.state = 'finished';
  old.wave = 'done';
  old.startedAt = t0;
  old.finishedAt = t0 + 9 * 60e3;
  old.createdAt = t0 - 60e3;
  old.updatedAt = old.finishedAt;
  old.runs = old.targets.map((t, i) => ({ id: `run-old-${i}`, jobId: old.id, path: t.path, name: t.name, base: 'main', size: t.size, level: 0, dependsOn: [], wave: 'main', state: i === 2 ? 'failed' : 'done', attempt: 1, sessionId: null, worktree: null, startedAt: t0 + i * 60e3, endedAt: t0 + (i + 3) * 60e3, durationMs: 3 * 60e3, cost: 0.37 - i * 0.1, summary: i === 2 ? '' : `Pinned node 22 in .github/workflows/ci.yml of ${t.name}.`, files: i === 2 ? [] : [{ path: '.github/workflows/ci.yml', add: 1, del: 1 }], add: i === 2 ? 0 : 1, del: i === 2 ? 0 : 1, reason: i === 2 ? 'Stopped after 20 minutes' : null, verify: null, now: null, pending: [], retried: false, commit: i === 2 ? null : `a1b2c3${i}`, pr: null, finished: i === 2 ? null : 'commit', brief: '' }));
  old.report = dReport(old);
  jobs.push(old);
}
seedDispatch();

const dispatchMethods = {
  'workspace.scan': ({ root }) => ({ root: root || `${HOME}/code`, depth: 3, at: Date.now(), repos: REPOS }),
  'dispatch.list': () => jobs.map(dSummary).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)),
  'dispatch.get': ({ id }) => ({ job: dSummary(dJob(id)), runs: dJob(id).runs }),
  'dispatch.save': (p) => {
    const cur = p.id ? jobs.find((j) => j.id === p.id) : null;
    const job = dNormalize(p, cur);
    if (cur) Object.assign(cur, job, { plan: null, state: cur.state === 'draft' || cur.state === 'planned' ? 'draft' : cur.state, runs: cur.runs });
    else jobs.push(job);
    const j = cur || job;
    dEmit(j);
    return dSummary(j);
  },
  'dispatch.remove': ({ id }) => {
    jobs = jobs.filter((j) => j.id !== id);
    broadcast('dispatch.changed', { id, removed: true });
    return true;
  },
  'dispatch.plan': (p) => {
    const saved = p.id ? jobs.find((j) => j.id === p.id) : null;
    const job = saved && !p.targets ? saved : dNormalize(p, saved);
    const plan = dPlan(job);
    if (saved) {
      saved.plan = plan;
      if (saved.state === 'draft') saved.state = 'planned';
      dEmit(saved);
    }
    return plan;
  },
  'dispatch.start': ({ id, wave }) => {
    const job = dJob(id);
    if (job.state === 'running') throw new Error('This job is already running.');
    const plan = (job.plan = dPlan(job));
    if (plan.blocked) throw new Error(`${plan.blocked} target${plan.blocked === 1 ? ' is' : 's are'} blocked. Fix or deselect ${plan.blocked === 1 ? 'it' : 'them'} first.`);
    const ok = plan.targets.filter((t) => t.verdict !== 'blocked');
    const canaryN = wave === 'all' ? 0 : job.settings.canary;
    const order = [...ok].sort((a, b) => a.level - b.level || a.name.localeCompare(b.name));
    const canary = new Set(order.filter((t) => t.level === 0).slice(0, canaryN).map((t) => t.path));
    job.runs = ok.map((t) => ({ id: `run-${uuid()}`, jobId: job.id, path: t.path, name: t.name, base: t.base, size: t.size, level: t.level, dependsOn: t.deps, wave: canary.has(t.path) ? 'canary' : 'main', state: 'queued', attempt: 1, sessionId: null, worktree: null, startedAt: null, endedAt: null, durationMs: 0, cost: 0, summary: '', files: [], add: 0, del: 0, reason: null, verify: null, now: null, pending: [], retried: false, commit: null, pr: null, finished: null, brief: t.brief }));
    job.state = 'running';
    job.wave = canary.size ? 'canary' : 'main';
    job.paused = false;
    job.startedAt = Date.now();
    job.finishedAt = null;
    job.learned = [];
    job.forecast = null;
    job.report = null;
    dEmit(job);
    dPump(job);
    return dSummary(job);
  },
  'dispatch.continue': ({ id, brief, learn }) => {
    const job = dJob(id);
    if (!(job.state === 'reviewing' && job.wave === 'canary')) throw new Error('The job is not waiting after a canary.');
    if (typeof brief === 'string' && brief.trim()) job.brief = brief;
    if (typeof learn === 'boolean') job.settings.learn = learn;
    job.wave = 'main';
    job.state = 'running';
    dEmit(job);
    dPump(job);
    return dSummary(job);
  },
  'dispatch.pause': ({ id }) => {
    const job = dJob(id);
    job.paused = true;
    dEmit(job);
    return dSummary(job);
  },
  'dispatch.resume': ({ id }) => {
    const job = dJob(id);
    job.paused = false;
    dEmit(job);
    dPump(job);
    return dSummary(job);
  },
  'dispatch.stop': ({ id, runId }) => {
    const job = dJob(id);
    const stopRun = (r) => {
      const s = r.sessionId ? sessions.get(r.sessionId) : null;
      if (s) {
        s.aborted = true;
        for (const p of s.pending.values()) p.resolve({ behavior: 'deny', message: 'Stopped' });
      }
      dEndRun(job, r, 'stopped', 'Stopped by you', s);
    };
    if (runId) {
      const r = job.runs.find((x) => x.id === runId);
      if (r && D_ACTIVE.has(r.state)) stopRun(r);
      else if (r && r.state === 'queued') {
        r.state = 'skipped';
        r.reason = 'Skipped by you';
        r.endedAt = Date.now();
        dEmit(job, [r]);
        dPump(job);
      }
      return dSummary(job);
    }
    for (const r of job.runs) {
      if (D_ACTIVE.has(r.state)) stopRun(r);
      else if (r.state === 'queued') {
        r.state = 'skipped';
        r.reason = 'Stopped by you';
        r.endedAt = Date.now();
      }
    }
    job.state = 'stopped';
    job.stoppedReason = 'Stopped by you';
    dEmit(job);
    return dSummary(job);
  },
  'dispatch.retry': ({ id, runId, brief }) => {
    const job = dJob(id);
    const old = job.runs.find((x) => x.id === runId);
    if (!old || !D_TERMINAL.has(old.state)) throw new Error('That run is still going.');
    if (typeof brief === 'string' && brief.trim()) job.brief = brief;
    const run = { ...old, id: `run-${uuid()}`, state: 'queued', attempt: (old.attempt || 1) + 1, sessionId: null, worktree: null, startedAt: null, endedAt: null, durationMs: 0, cost: 0, summary: '', files: [], add: 0, del: 0, reason: null, now: null, pending: [], commit: null, pr: null, finished: null, wave: 'main' };
    old.superseded = run.id;
    job.runs.push(run);
    if (job.state !== 'running') {
      job.state = 'running';
      job.wave = 'main';
      job.finishedAt = null;
    }
    dEmit(job);
    dPump(job);
    return run;
  },
  'dispatch.skip': ({ id, runId }) => dispatchMethods['dispatch.stop']({ id, runId }),
  'dispatch.allow': ({ id, key, requestId }) => {
    const job = dJob(id);
    if (!key && requestId) key = job.runs.flatMap((r) => r.pending || []).find((p) => p.requestId === requestId)?.key || null;
    if (!key) throw new Error('No request to allow.');
    if (!job.rules.some((r) => r.key === key)) job.rules.push({ key, label: null, at: Date.now(), count: 0 });
    const rule = job.rules.find((r) => r.key === key);
    for (const run of job.runs) {
      const hits = (run.pending || []).filter((p) => p.key === key);
      if (!hits.length) continue;
      rule.label = rule.label || hits[0].label;
      const s = sessions.get(run.sessionId);
      for (const p of hits) {
        rule.count += 1;
        s?.pending.get(p.requestId)?.resolve({ behavior: 'allow', updatedInput: s.pending.get(p.requestId).req.input });
      }
    }
    dEmit(job);
    return dSummary(job);
  },
  'dispatch.review': ({ id }) => {
    const job = dJob(id);
    const runs = job.runs.filter((r) => r.state === 'done' && !r.superseded).map((r) => ({ runId: r.id, name: r.name, files: r.files || [], add: r.add || 0, del: r.del || 0 }));
    const byPath = new Map();
    for (const r of runs) for (const f of r.files) byPath.set(f.path, [...(byPath.get(f.path) || []), r.name]);
    const same = [...byPath.entries()].filter(([, names]) => names.length > 1).map(([p, names]) => ({ sig: p, path: p, runs: names, add: 3, del: 1, preview: ['-  node: 20', '+  node: 22'] }));
    const differ = runs.map((r) => ({ name: r.name, files: r.files.filter((f) => (byPath.get(f.path) || []).length === 1) })).filter((d) => d.files.length);
    return { id, runs, same, differ, files: runs.reduce((n, r) => n + r.files.length, 0) };
  },
  'dispatch.finish': ({ id, runId, action, pr }) => {
    const job = dJob(id);
    const runs = runId ? job.runs.filter((r) => r.id === runId) : job.runs.filter((r) => !r.superseded && D_TERMINAL.has(r.state) && !r.finished && (action === 'commit' ? r.state === 'done' : true));
    const results = runs.map((r) => {
      r.finished = action;
      if (action === 'commit') {
        r.commit = `c0ffee${r.id.slice(-2)}`;
        if (pr ?? job.settings.pr) r.pr = `https://github.com/acme/${r.name}/pull/${40 + job.runs.indexOf(r)}`;
      }
      return { runId: r.id, name: r.name, ok: true, output: '' };
    });
    dEmit(job);
    dMaybeFinish(job);
    return { job: dSummary(job), results };
  },
  'dispatch.export': ({ id }) => {
    const job = dJob(id);
    const rep = job.report || dReport(job);
    return [`# ${job.name}`, '', `${rep.targets.length} repositories · $${rep.cost.toFixed(2)}`, '', '## Brief', '', job.brief, '', '## Repositories', '', '| Repository | State | Files | Cost | Time | Landed |', '|---|---|---|---|---|---|', ...rep.targets.map((t) => `| ${t.name} | ${t.state} | ${t.files} | $${(t.cost || 0).toFixed(2)} | ${Math.round((t.durationMs || 0) / 60e3)} min | ${t.commit || t.finished || ''} |`)].join('\n');
  },
};

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
const configDefaults = JSON.parse(JSON.stringify(config)); // what mock.reset puts back
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
  'inbox.list': () => ({ unread: inboxItems.filter((i) => !i.read).length, items: inboxItems }),
  'inbox.markRead': ({ ids }) => {
    for (const i of inboxItems) if (!ids || ids.includes(i.id)) i.read = true;
    return inboxItems.filter((i) => !i.read).length;
  },
  'inbox.remove': ({ id }) => {
    inboxItems = inboxItems.filter((i) => i.id !== id);
    return true;
  },
  ...dispatchMethods,
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
    seedDispatch();
    overrides.clear();
    // the config too: a saved ui (a dragged sidebar width) must not leak into the next test's window
    for (const k of Object.keys(config)) delete config[k];
    Object.assign(config, JSON.parse(JSON.stringify(configDefaults)));
    listChanged();
    return true;
  },
  /** Answer `method` with `result` from now on (null forgets it); the real handler is used for everything else. */
  'mock.answer': ({ method, result = null }) => {
    if (result === null) overrides.delete(method);
    else overrides.set(method, result);
    return overrides.size;
  },
  /** Lay fields over a session's state (tasks, mcp, usage…) and broadcast it. */
  'mock.patch': ({ id, state = {} }) => {
    const s = S(id);
    Object.assign(s.extra, state);
    s.emitState();
    return s.state();
  },
  /** Append raw transcript messages (the SDK shapes above) to a session, as if Claude had just sent them. */
  'mock.messages': ({ id, msgs = [] }) => {
    const s = S(id);
    for (const m of msgs) s.record({ uuid: uuid(), session_id: id, timestamp: new Date().toISOString(), ...m });
    return s.messages.length;
  },
  /** Build a long conversation: `shape` is 'heavy' (prose, big outputs, big diffs, thinking, sub-agents, failures) or 'mixed' (the MOCK_LONG shapes). */
  'mock.generate': ({ id = 'sess-heavy', title, turns = 500, shape = 'heavy' }) => {
    const msgs = [];
    const start = now - turns * 90e3 - 3600e3;
    for (let i = 0; i < turns; i++) msgs.push(...(shape === 'heavy' ? heavyTurn : mixedTurn)(id, start + i * 90e3, i));
    sessions.set(id, new Sess({ id, title: title || `${shape} conversation (${turns} turns)`, cwd: PROJ, touched: now - 3 * DAY, messages: msgs }));
    listChanged();
    return { id, turns, messages: msgs.length, bytes: JSON.stringify(msgs).length };
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
    const fn = overrides.has(m.m) ? () => overrides.get(m.m) : methods[m.m];
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
