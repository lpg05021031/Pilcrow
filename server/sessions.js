// Live Claude Code sessions, driven through the Agent SDK in streaming-input mode.
// Each open session owns one Claude Code process; idle ones are closed and resumed on demand.
import fs from 'node:fs';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { store } from './config.js';
import { resolveClaude } from './claude.js';
import * as history from './history.js';
import * as snapshots from './snapshots.js';
import * as memory from './memory.js';
import * as git from './git.js';
import { repos } from './repos.js';
import { AsyncQueue, uuid, IS_ROOT, log, xdg, slugify, cleanEnv, slash } from './util.js';

export const bus = new EventEmitter();
/** Where other modules (helpers.js) extend a session: in-process MCP servers to attach, permission
 *  decisions Pilcrow takes itself, fields added to `state()`, and a say on a decision as it is given. */
export const extensions = {
  mcpServers: [], // (session) => McpSdkServerConfigWithInstance | null
  permission: [], // (session, toolName, input) => PermissionResult | null
  state: [], // (session) => object merged into state()
  respond: [], // (session, request, decision) => decision
};
function mcpOption(session) {
  const servers = {};
  for (const fn of extensions.mcpServers) {
    const s = fn(session);
    if (s) servers[s.name] = s;
  }
  return Object.keys(servers).length ? { mcpServers: servers } : {};
}

/** Spend over the last few hours, per session, for the control room's burn chart. */
export const costLog = {
  entries: [],
  add(id, delta) {
    this.entries.push({ at: Date.now(), id, delta });
    const cutoff = Date.now() - 6 * 3600e3;
    if (this.entries.length > 5000 || this.entries[0]?.at < cutoff) this.entries = this.entries.filter((e) => e.at >= cutoff);
  },
  since(ms) {
    return this.entries.filter((e) => e.at >= ms);
  },
};
bus.setMaxListeners(50);

// Messages that only matter while they happen; they are forwarded but not kept in the transcript.
const TRANSIENT_SYSTEM = new Set(['thinking_tokens', 'hook_started', 'hook_progress', 'hook_response', 'status', 'session_state_changed', 'background_tasks_changed', 'task_progress', 'commands_changed', 'files_persisted']);
const APP_VERSION = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version || '1.0.0';

function childEnv() {
  return cleanEnv({ CLAUDE_AGENT_SDK_CLIENT_APP: `pilcrow/${APP_VERSION}` });
}

/** A short, human line for a tool call: the file it touched or the command it ran. */
function toolLabel(name, input, cwd) {
  const rel = (p) => (typeof p === 'string' ? slash(p).replace(slash(cwd).replace(/\/$/, '') + '/', '') : '');
  if (!input || typeof input !== 'object') return name;
  if (input.file_path) return rel(input.file_path);
  if (input.notebook_path) return rel(input.notebook_path);
  if (typeof input.command === 'string') return input.command.replace(/\s+/g, ' ').trim().slice(0, 90);
  if (typeof input.pattern === 'string') return input.pattern.slice(0, 80);
  if (typeof input.description === 'string') return input.description.slice(0, 90);
  if (typeof input.prompt === 'string') return input.prompt.replace(/\s+/g, ' ').slice(0, 90);
  if (typeof input.url === 'string') return input.url.slice(0, 90);
  if (typeof input.query === 'string') return input.query.slice(0, 90);
  return name;
}

function titleFromPrompt(p) {
  const line = String(p).replace(/\s+/g, ' ').trim();
  return line.length > 72 ? line.slice(0, 70).replace(/\s+\S*$/, '') + '…' : line;
}

function textOfContent(content) {
  if (typeof content === 'string') return content;
  return (content || []).filter((b) => b.type === 'text').map((b) => b.text).join('\n');
}

/**
 * A workflow (the Workflow tool) reports its agents, phases and log lines through task_progress. The
 * list is usually cumulative, one entry per agent/phase index; keep the latest per index, and the
 * log in order, so the UI can draw the whole run from one object.
 */
const WF_PREVIEW = 600;
function trimWf(a) {
  const o = { ...a };
  for (const k of ['promptPreview', 'resultPreview', 'error', 'lastToolSummary']) if (typeof o[k] === 'string' && o[k].length > WF_PREVIEW) o[k] = o[k].slice(0, WF_PREVIEW) + '…';
  delete o.promptFramed;
  return o;
}
function mergeWorkflowProgress(t, list) {
  const wf = t.workflow || (t.workflow = { phases: [], agents: [], logs: [] });
  const logs = [];
  for (const it of list) {
    if (!it || typeof it !== 'object') continue;
    if (it.type === 'workflow_agent' && Number.isInteger(it.index) && it.index >= 0 && it.index < 500) {
      const prev = wf.agents[it.index];
      wf.agents[it.index] = trimWf({ ...(prev || {}), ...it, firstSeenAt: prev?.firstSeenAt || Date.now(), updatedAt: Date.now() });
    } else if (it.type === 'workflow_phase' && Number.isInteger(it.index) && it.index >= 0 && it.index < 200) {
      wf.phases[it.index] = { index: it.index, title: typeof it.title === 'string' ? it.title.slice(0, 200) : `Phase ${it.index + 1}`, kind: it.kind };
    } else if (it.type === 'workflow_log' && typeof it.message === 'string') {
      logs.push(it.message.slice(0, 1000));
    }
  }
  if (logs.length) {
    const same = logs.length >= wf.logs.length && wf.logs.every((m, i) => m.message === logs[i]);
    if (same) wf.logs = logs.map((message, i) => ({ at: wf.logs[i]?.at || Date.now(), message }));
    else for (const message of logs) if (wf.logs.length < 300) wf.logs.push({ at: Date.now(), message });
  }
  // Agents may arrive without their phase ever being announced; give them one so the graph has a column.
  for (const a of wf.agents) {
    if (!a) continue;
    const pi = Number.isInteger(a.phaseIndex) ? a.phaseIndex : 0;
    if (!wf.phases[pi]) wf.phases[pi] = { index: pi, title: a.phaseTitle || (wf.agents.length > 1 ? `Phase ${pi + 1}` : 'Agents') };
  }
  wf.updatedAt = Date.now();
}

class Session {
  constructor(id, { cwd, persisted }) {
    this.id = id;
    this.cwd = slash(cwd);
    this.persisted = persisted; // a transcript exists on disk, so start with `resume`
    this.q = null;
    this.input = null;
    this.status = persisted ? 'stopped' : 'idle';
    this.messages = [];
    this.inflight = [];
    this.loaded = !persisted;
    this.loading = null;
    this.pending = new Map();
    this.tasks = new Map();
    this.bgTasks = [];
    this.init = null;
    this.commands = null;
    this.models = null;
    const saved = store.getMeta(id) || {};
    this.usage = { contextTokens: 0, contextWindow: saved.contextWindow || 0, costUsd: saved.costUsd || 0, outputTokens: 0, model: null };
    this.suggestion = null;
    this.error = null;
    this.stderrTail = '';
    this.compacting = false;
    this.mcp = []; // last known MCP server statuses for this process
    this.mcpTimer = null;
    this.lastActivity = Date.now();
    this.outbox = [];
    this.flushTimer = null;
    const meta = store.getMeta(id) || {};
    this.mode = meta.mode || store.getConfig().defaultMode || 'default';
    this.model = meta.model || store.getConfig().defaultModel || '';
    this.effort = meta.effort || store.getConfig().defaultEffort || '';
    this.queuedUuids = new Set();
    /** Messages typed while Claude was busy. Pilcrow keeps them here (editable, cancellable) and
     *  hands them over one at a time once the turn ends, instead of pushing them into the SDK's queue. */
    this.held = [];
    /** tool_use id -> { name, input } for tools in flight, so a tool_result can be read back. */
    this.toolUses = new Map();
    /** What Claude is doing right now, for views that do not load the transcript. */
    this.activity = null;
    this.turnStartedAt = null;
    this.processStartedAt = null;
    /** The last thing Claude said in a finished turn, for overviews. */
    this.lastReply = null;
  }

  get meta() {
    return store.getMeta(this.id) || {};
  }

  isBusy() {
    return this.status === 'running' || this.status === 'requires_action' || this.status === 'starting';
  }

  state() {
    const init = this.init;
    return {
      id: this.id,
      status: this.status,
      live: Boolean(this.q),
      cwd: this.cwd,
      mode: this.mode,
      model: this.model,
      effort: this.effort,
      activeModel: init?.model || this.usage.model || null,
      usage: this.usage,
      error: this.error,
      suggestion: this.suggestion,
      compacting: this.compacting,
      mcp: this.mcp,
      activity: this.activity,
      lastActivity: this.lastActivity,
      turnStartedAt: this.turnStartedAt,
      processStartedAt: this.processStartedAt,
      lastReply: this.lastReply,
      unattended: Boolean(this.unattended),
      unattendedDenials: this.unattendedDenials || 0,
      pending: [...this.pending.values()].map((p) => p.req),
      tasks: [...this.tasks.values()],
      bgTasks: this.bgTasks,
      queued: [...this.queuedUuids],
      held: this.held.map((h) => ({ uuid: h.uuid, text: h.text, images: h.images.length, files: h.files.map((f) => f.name), at: h.at, note: h.note || null, from: h.from || null })),
      ...Object.assign({}, ...extensions.state.map((fn) => fn(this) || {})),
      init: init
        ? {
            version: init.claude_code_version,
            tools: init.tools,
            mcpServers: init.mcp_servers,
            slashCommands: init.slash_commands,
            agents: init.agents,
            skills: init.skills,
            outputStyle: init.output_style,
            apiKeySource: init.apiKeySource,
            model: init.model,
            capabilities: init.capabilities,
            plugins: init.plugins,
            effort: init.effort,
          }
        : null,
      commands: this.commands,
      models: this.models,
    };
  }

  emitState() {
    bus.emit('state', this.state());
  }

  out(msg) {
    this.outbox.push(msg);
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        const msgs = this.outbox.splice(0);
        if (msgs.length) bus.emit('msgs', { id: this.id, msgs });
      }, 16);
    }
  }

  async loadHistory() {
    if (this.loaded) return;
    if (!this.loading) {
      this.loading = (async () => {
        const msgs = await history.loadTranscript(this.id, this.cwd);
        const seen = new Set(this.messages.map((m) => m.uuid));
        this.messages = [...msgs.filter((m) => !seen.has(m.uuid)), ...this.messages];
        this.recomputeFromHistory(msgs);
        this.loaded = true;
      })();
    }
    await this.loading;
  }

  recomputeFromHistory(msgs) {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.type === 'assistant' && !m.parent_tool_use_id && m.message?.usage) {
        this.applyUsage(m.message);
        break;
      }
    }
  }

  applyUsage(message) {
    const u = message.usage || {};
    const total = (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.output_tokens || 0);
    if (total > 0) this.usage.contextTokens = total;
    if (message.model) this.usage.model = message.model;
  }

  async snapshot() {
    await this.loadHistory();
    return { state: this.state(), messages: this.messages, inflight: this.inflight };
  }

  /** Start Claude Code for this session once, even when several requests arrive together. */
  ensureStarted() {
    if (this.q && !this.q.pilcrowClosing) return Promise.resolve();
    if (!this.starting) {
      this.starting = (async () => {
        // A process that is shutting down (idle timeout, settings change) cannot take new messages:
        // let it finish, then start a fresh one.
        if (this.q?.pilcrowClosing) {
          await Promise.race([this.consumed, new Promise((r) => setTimeout(r, 5000))]);
          if (this.q?.pilcrowClosing) this.q = null; // gave up waiting; its consume() will see it was replaced
        }
        if (!this.q) await this.startProcess();
      })().finally(() => (this.starting = null));
    }
    return this.starting;
  }

  async startProcess() {
    await this.loadHistory();
    const cfg = store.getConfig();
    const claude = await resolveClaude(cfg.claudeBinary);
    if (!claude.path) {
      const pref = cfg.claudeBinary || 'auto';
      const msg =
        pref === 'system'
          ? 'Claude Code was not found on PATH. Install it, or switch to the bundled copy in Settings → Claude Code.'
          : pref === 'bundled'
            ? 'The bundled Claude Code is missing. Reinstall Pilcrow, or switch to the system copy in Settings → Claude Code.'
            : pref !== 'auto'
              ? `No Claude Code program at ${pref}. Fix the path in Settings → Claude Code.`
              : 'Claude Code was not found. Reinstall Pilcrow (it bundles Claude Code) or install `claude` on PATH.';
      this.status = 'error';
      this.error = msg;
      this.emitState();
      throw new Error(msg);
    }
    this.status = 'starting';
    this.error = null;
    this.stderrTail = '';
    this.emitState();
    const meta = this.meta;
    const input = new AsyncQueue();
    // What earlier sessions in this project learned rides along in the system prompt.
    const memoryText = cfg.memoryInject === false ? '' : memory.promptFor(memory.projectOf(this));
    // …and the repositories the person registered, so their own names for them land in the right folders.
    const appendText = memoryText + repos.promptFor();
    const options = {
      cwd: this.cwd,
      pathToClaudeCodeExecutable: claude.path,
      permissionMode: this.mode,
      includePartialMessages: true,
      forwardSubagentText: true,
      settingSources: ['user', 'project', 'local'],
      systemPrompt: { type: 'preset', preset: 'claude_code', ...(appendText ? { append: appendText } : {}) },
      env: childEnv(),
      canUseTool: (name, toolInput, opts) => this.askPermission(name, toolInput, opts),
      stderr: (d) => {
        this.stderrTail = (this.stderrTail + d).slice(-8000);
      },
      promptSuggestions: cfg.promptSuggestions !== false,
      perTaskStopAffordance: true,
      // Lets the UI restore files to how they were before any earlier message (like /rewind).
      enableFileCheckpointing: true,
      ...mcpOption(this),
    };
    if (!IS_ROOT) options.allowDangerouslySkipPermissions = true;
    if (this.model) options.model = this.model;
    if (this.effort) options.effort = this.effort;
    if (meta.worktree?.repoRoot) options.projectConfigRoot = meta.worktree.repoRoot;
    if (this.persisted) options.resume = this.id;
    else options.sessionId = this.id;
    log('starting session', this.id, { cwd: this.cwd, resume: this.persisted, bin: claude.path, mode: this.mode });
    let q;
    try {
      q = query({ prompt: input, options });
    } catch (e) {
      this.status = 'error';
      this.error = e.message;
      this.emitState();
      throw e;
    }
    this.q = q;
    this.input = input;
    this.processStartedAt = Date.now();
    this.consumed = this.consume(q, input);
    if (store.getConfig().timeline !== false) snapshots.record(this, { kind: 'start', label: this.persisted ? 'Session resumed' : 'Session started' });
  }

  async consume(q, input) {
    let err = null;
    try {
      for await (const msg of q) this.onMessage(msg);
    } catch (e) {
      err = e;
    }
    input.close();
    if (this.q !== q) return; // replaced by a newer process
    this.q = null;
    this.input = null;
    for (const [rid, p] of this.pending) {
      p.resolve({ behavior: 'deny', message: 'Session ended' });
      bus.emit('permission-resolved', { id: this.id, requestId: rid });
    }
    this.pending.clear();
    this.compacting = false;
    this.queuedUuids.clear();
    this.turnStartedAt = null;
    this.activity = null;
    // Nothing is streaming any more, and background work died with the process.
    this.inflight = [];
    this.bgTasks = [];
    for (const t of this.tasks.values()) {
      if (t.status === 'running' || t.status === 'pending') Object.assign(t, { status: 'stopped', endedAt: Date.now() });
    }
    if (err && !q.pilcrowClosing) {
      const detail = (this.stderrTail || '').trim().split('\n').slice(-12).join('\n');
      this.error = [err.message, detail].filter(Boolean).join('\n');
      this.status = 'error';
      const rec = { type: 'system', subtype: 'pilcrow_error', uuid: uuid(), session_id: this.id, content: this.error, timestamp: new Date().toISOString() };
      this.messages.push(rec);
      this.out(rec);
      log('session error', this.id, this.error);
    } else {
      this.status = 'stopped';
    }
    this.emitState();
    bus.emit('list-changed');
  }

  onMessage(msg) {
    this.lastActivity = Date.now();
    if (!this.persisted && msg.session_id) this.persisted = true;
    switch (msg.type) {
      case 'stream_event': {
        if (!msg.parent_tool_use_id) {
          const t = msg.event?.type;
          if (t === 'message_start') this.inflight = [msg];
          else if (t === 'message_stop') this.inflight = [];
          else this.inflight.push(msg);
        }
        this.out(msg);
        return;
      }
      case 'assistant':
        if (!msg.parent_tool_use_id && msg.message) this.applyUsage(msg.message);
        this.noteAssistant(msg);
        this.markDelivered(msg.user_message_uuids || (msg.user_message_uuid ? [msg.user_message_uuid] : null));
        if (this.status === 'requires_action' && !this.pending.size) this.status = 'running';
        break;
      case 'result': {
        const prevCost = this.usage.costUsd || 0;
        this.usage.costUsd = msg.total_cost_usd ?? this.usage.costUsd;
        if (this.usage.costUsd > prevCost) costLog.add(this.id, this.usage.costUsd - prevCost);
        if (typeof msg.result === 'string' && msg.result.trim()) this.lastReply = { text: msg.result.replace(/\s+/g, ' ').trim().slice(0, 240), at: Date.now() };
        const mu = msg.modelUsage || {};
        const model = this.usage.model || Object.keys(mu)[0];
        const entry = (model && mu[model]) || Object.values(mu)[0];
        if (entry?.contextWindow) this.usage.contextWindow = entry.contextWindow;
        this.usage.outputTokens = Object.values(mu).reduce((s, x) => s + (x.outputTokens || 0), 0);
        store.setMeta(this.id, { contextWindow: this.usage.contextWindow || null, costUsd: this.usage.costUsd || null, turnEnd: Date.now() });
        this.markDelivered(msg.user_message_uuids || (msg.user_message_uuid ? [msg.user_message_uuid] : null));
        if (!(msg.queued_turn_count > 0)) {
          this.status = 'idle';
          this.queuedUuids.clear();
          this.activity = null;
          this.turnStartedAt = null;
        }
        this.compacting = false;
        if (msg.is_error && msg.subtype !== 'success') this.error = (msg.errors || []).join('\n') || null;
        this.emitState();
        bus.emit('list-changed');
        if (this.status === 'idle') setTimeout(() => this.drainHeld(), 50);
        if (this.mcp.length) this.scheduleMcpRefresh(300);
        if (this.status === 'idle' && memory.worthIt(this)) memory.distill(this).catch((e) => log('memory distill failed', this.id, e?.message));
        break;
      }
      case 'system':
        this.onSystem(msg);
        if (TRANSIENT_SYSTEM.has(msg.subtype)) {
          this.out(msg);
          return;
        }
        break;
      case 'user': {
        const content = Array.isArray(msg.message?.content) ? msg.message.content : [];
        for (const c of content) {
          if (c?.type === 'tool_result') this.noteToolResult(c);
          if (c?.type !== 'tool_result' || !c.is_error) continue;
          const text = typeof c.content === 'string' ? c.content : Array.isArray(c.content) ? c.content.map((x) => x?.text || '').join('\n') : '';
          if (/mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|MCP server .* (is not|not) connected|connection closed/i.test(text)) this.scheduleMcpRefresh(200);
        }
        break;
      }
      case 'tool_progress':
      case 'auth_status':
        this.out(msg);
        return;
      case 'prompt_suggestion':
        this.suggestion = msg.suggestion;
        this.emitState();
        return;
      default:
        break;
    }
    this.messages.push(msg);
    this.out(msg);
  }

  noteAssistant(msg) {
    const content = Array.isArray(msg.message?.content) ? msg.message.content : [];
    for (const c of content) {
      if (c?.type === 'tool_use') {
        this.toolUses.set(c.id, { name: c.name, input: c.input, at: Date.now(), sub: Boolean(msg.parent_tool_use_id) });
        if (this.toolUses.size > 400) this.toolUses.delete(this.toolUses.keys().next().value);
        if (!msg.parent_tool_use_id) this.setActivity({ kind: 'tool', tool: c.name, label: toolLabel(c.name, c.input, this.cwd) });
      } else if (c?.type === 'text' && c.text && !msg.parent_tool_use_id) this.setActivity({ kind: 'text', label: c.text.replace(/\s+/g, ' ').trim().slice(0, 140) });
      else if (c?.type === 'thinking' && !msg.parent_tool_use_id) this.setActivity({ kind: 'thinking', label: 'Thinking' });
    }
  }

  setActivity(a) {
    this.activity = { ...a, at: Date.now() };
  }

  noteToolResult(c) {
    const use = this.toolUses.get(c.tool_use_id);
    if (!use) return;
    this.toolUses.delete(c.tool_use_id);
    if (!snapshots.FILE_TOOLS.has(use.name) || store.getConfig().timeline === false) return;
    snapshots.record(this, { kind: 'tool', tool: use.name, toolUseId: c.tool_use_id, label: toolLabel(use.name, use.input, this.cwd) });
  }

  markDelivered(uuids) {
    if (!uuids || !this.queuedUuids.size) return;
    let changed = false;
    for (const u of uuids) if (this.queuedUuids.delete(u)) changed = true;
    if (changed) this.emitState();
  }

  onSystem(msg) {
    switch (msg.subtype) {
      case 'init':
        this.init = msg;
        if (msg.permissionMode) this.mode = msg.permissionMode;
        if (this.status === 'starting') this.status = 'running';
        this.emitState();
        if (!this.commands) this.refreshCapabilities();
        this.scheduleMcpRefresh(1500);
        break;
      case 'status':
        if (msg.permissionMode && msg.permissionMode !== this.mode) {
          this.mode = msg.permissionMode;
          store.setMeta(this.id, { mode: this.mode });
        }
        this.compacting = msg.status === 'compacting';
        this.emitState();
        break;
      case 'session_state_changed':
        if (msg.state === 'running' && this.status !== 'requires_action') this.status = 'running';
        else if (msg.state === 'requires_action') this.status = 'requires_action';
        else if (msg.state === 'idle' && !this.pending.size) this.status = 'idle';
        this.emitState();
        bus.emit('list-changed');
        if (this.status === 'idle') setTimeout(() => this.drainHeld(), 50);
        break;
      case 'task_started':
        this.tasks.set(msg.task_id, { id: msg.task_id, runId: msg.run_id, toolUseId: msg.tool_use_id, parentTaskId: msg.parent_task_id, description: msg.description, type: msg.task_type, subagentType: msg.subagent_type, workflowName: msg.workflow_name, ownedBySubagent: Boolean(msg.owned_by_subagent), background: msg.is_backgrounded, ambient: msg.ambient, status: 'running', startedAt: Date.now(), prompt: typeof msg.prompt === 'string' ? msg.prompt.slice(0, 4000) : undefined });
        this.emitState();
        break;
      case 'task_updated': {
        const t = this.tasks.get(msg.task_id);
        if (t) Object.assign(t, msg.patch?.status ? { status: msg.patch.status } : {}, msg.patch?.description ? { description: msg.patch.description } : {}, msg.patch?.is_backgrounded !== undefined ? { background: msg.patch.is_backgrounded } : {});
        this.emitState();
        break;
      }
      case 'task_progress': {
        const t = this.tasks.get(msg.task_id);
        if (t) {
          Object.assign(t, { usage: msg.usage, lastTool: msg.last_tool_name, summary: msg.summary || t.summary, progress: msg.description });
          if (Array.isArray(msg.workflow_progress)) mergeWorkflowProgress(t, msg.workflow_progress);
        }
        this.emitState();
        break;
      }
      case 'task_notification': {
        const t = this.tasks.get(msg.task_id) || { id: msg.task_id };
        Object.assign(t, { status: msg.status, summary: msg.summary, outputFile: msg.output_file, usage: msg.usage || t.usage, toolUseId: msg.tool_use_id || t.toolUseId, endedAt: Date.now(), reason: msg.reason });
        if (t.workflow) for (const a of t.workflow.agents) if (a && (a.state === 'start' || a.state === 'progress')) a.state = msg.status === 'completed' ? 'done' : 'error';
        this.tasks.set(msg.task_id, t);
        this.emitState();
        break;
      }
      case 'background_tasks_changed':
        this.bgTasks = msg.tasks || [];
        this.emitState();
        break;
      case 'commands_changed':
        this.commands = msg.commands;
        this.emitState();
        break;
      default:
        break;
    }
  }

  async refreshCapabilities() {
    const q = this.q;
    if (!q) return;
    try {
      const [commands, models] = await Promise.all([q.supportedCommands().catch(() => null), q.supportedModels().catch(() => null)]);
      if (commands) this.commands = commands;
      if (models) this.models = models;
      this.emitState();
    } catch (e) {
      log('capabilities failed', e?.message);
    }
  }

  askPermission(toolName, input, opts) {
    // Decisions Pilcrow takes itself (a helper's read-only tools, a start in an allowed repository).
    for (const fn of extensions.permission) {
      const d = fn(this, toolName, input);
      if (d) return Promise.resolve(d);
    }
    if (this.unattended) {
      // A scheduled run has nobody to ask. Say so, so Claude works around it instead of waiting.
      const why = toolName === 'AskUserQuestion' ? 'This session runs on a schedule with nobody watching, so questions cannot be answered. Decide yourself and say what you assumed.' : 'This session runs on a schedule with nobody watching, so this action cannot be approved. Do what you can without it and report what you skipped.';
      this.unattendedDenials = (this.unattendedDenials || 0) + 1;
      return Promise.resolve({ behavior: 'deny', message: why });
    }
    return new Promise((resolve) => {
      const requestId = opts.requestId || uuid();
      const req = {
        requestId,
        toolName,
        input,
        toolUseId: opts.toolUseID,
        title: opts.title,
        displayName: opts.displayName,
        description: opts.description,
        decisionReason: opts.decisionReason,
        blockedPath: opts.blockedPath,
        suggestions: opts.suggestions || [],
        agentId: opts.agentID,
        defaultToNo: opts.defaultToNo,
        suppressAlwaysAllowRule: opts.suppressAlwaysAllowRule,
        interactive: Boolean(opts.requiresUserInteraction) || toolName === 'AskUserQuestion' || toolName === 'ExitPlanMode',
        at: Date.now(),
      };
      const done = (decision) => {
        if (!this.pending.has(requestId)) return;
        this.pending.delete(requestId);
        resolve(decision);
        bus.emit('permission-resolved', { id: this.id, requestId, decision: decision.behavior });
        if (!this.pending.size && this.status === 'requires_action') this.status = 'running';
        this.emitState();
      };
      opts.signal?.addEventListener('abort', () => done({ behavior: 'deny', message: 'Cancelled' }));
      this.pending.set(requestId, { req, resolve: done });
      this.status = 'requires_action';
      bus.emit('permission', { id: this.id, request: req });
      this.emitState();
      bus.emit('list-changed');
    });
  }

  respond(requestId, decision) {
    const p = this.pending.get(requestId);
    if (!p) return false;
    let d = { ...decision };
    for (const fn of extensions.respond) d = fn(this, p.req, d) || d;
    if (d.behavior === 'allow' && !d.updatedInput) d.updatedInput = p.req.input;
    if (d.behavior === 'deny' && !d.message) d.message = 'The user declined this action.';
    p.resolve(d);
    return true;
  }

  /** `note` marks a message Pilcrow wrote (a helper's news to its lead); `from` says who sent it
   *  when it was not the person ('lead'). Both ride on the record for the clients to draw it apart. */
  async send({ text = '', images = [], files = [], clientUuid, shouldQuery, now = false, note = null, from = null }) {
    await this.ensureStarted();
    if (!text && !images.length && !files.length) return null;
    const u = clientUuid || uuid();
    const busy = this.isBusy() && this.status !== 'starting';
    if (busy && now) {
      // Steer: stop the current step, then deliver as soon as the turn is over.
      this.held.unshift({ uuid: u, text, images, files, at: Date.now(), shouldQuery, note, from });
      this.emitState();
      await this.interrupt();
      return u;
    }
    if (busy) {
      this.held.push({ uuid: u, text, images, files, at: Date.now(), shouldQuery, note, from });
      this.emitState();
      return u;
    }
    return this.deliver({ uuid: u, text, images, files, shouldQuery, note, from });
  }

  /** Push one message into the running Claude Code process. */
  deliver({ uuid: u, text = '', images = [], files = [], shouldQuery, note = null, from = null }) {
    const content = [];
    for (const img of images) content.push({ type: 'image', source: { type: 'base64', media_type: img.mediaType, data: img.data } });
    for (const f of files) content.push({ type: 'text', text: `<attached_file name="${f.name}">\n${f.text}\n</attached_file>` });
    if (text) content.push({ type: 'text', text });
    if (!content.length) return null;
    const rec = { type: 'user', uuid: u, message: { role: 'user', content }, parent_tool_use_id: null, session_id: this.id, timestamp: new Date().toISOString(), _local: true, ...(note ? { _note: note } : {}), ...(from ? { _from: from } : {}) };
    this.messages.push(rec);
    this.out(rec);
    const sdkMsg = { type: 'user', message: { role: 'user', content }, parent_tool_use_id: null, uuid: u, session_id: this.id };
    if (shouldQuery === false) sdkMsg.shouldQuery = false;
    this.input.push(sdkMsg);
    if (!this.turnStartedAt) this.turnStartedAt = Date.now();
    if (store.getConfig().timeline !== false) snapshots.record(this, { kind: 'message', uuid: u, label: text.replace(/\s+/g, ' ').trim().slice(0, 120) });
    this.setActivity({ kind: 'thinking', label: 'Thinking' });
    this.suggestion = null;
    this.error = null;
    this.lastActivity = Date.now();
    if (this.status !== 'requires_action') this.status = 'running';
    this.emitState();
    store.setMeta(this.id, { touched: Date.now() });
    if (!this.meta.title && !this.meta.firstPrompt && text) store.setMeta(this.id, { firstPrompt: text.slice(0, 200) });
    bus.emit('list-changed');
    return u;
  }

  /** The turn is over: hand the next held message to Claude, if any. */
  async drainHeld() {
    if (!this.held.length || this.isBusy() || this.draining) return;
    this.draining = true;
    try {
      const next = this.held.shift();
      this.emitState();
      await this.ensureStarted();
      this.deliver(next);
    } catch (e) {
      log('drainHeld failed', this.id, e?.message);
    } finally {
      this.draining = false;
    }
  }

  removeHeld(u) {
    const n = this.held.length;
    this.held = this.held.filter((h) => h.uuid !== u);
    if (this.held.length !== n) this.emitState();
    return this.held.length !== n;
  }

  updateHeld(u, text) {
    const h = this.held.find((h) => h.uuid === u);
    if (!h) return false;
    h.text = text;
    this.emitState();
    return true;
  }

  /** Send a held message right away: it moves to the front and the current step is interrupted. */
  async sendHeldNow(u) {
    const i = this.held.findIndex((h) => h.uuid === u);
    if (i < 0) return false;
    const [h] = this.held.splice(i, 1);
    this.held.unshift(h);
    this.emitState();
    if (this.isBusy()) await this.interrupt();
    else await this.drainHeld();
    return true;
  }

  async interrupt() {
    if (!this.q) return;
    try {
      await this.q.interrupt();
    } catch (e) {
      log('interrupt failed', e?.message);
    }
  }

  /** Change the folder of a session that has not talked to Claude yet. */
  setCwd(cwd) {
    cwd = slash(cwd);
    if (this.q || this.messages.length || this.persisted) throw new Error('The folder can only be changed before the first message.');
    if (!cwd || !fs.existsSync(cwd)) throw new Error('Folder does not exist: ' + cwd);
    this.cwd = cwd;
    store.setMeta(this.id, { cwd, project: cwd });
    store.addRecentProject(cwd);
    this.emitState();
    bus.emit('list-changed');
    return true;
  }

  async setMode(mode) {
    this.mode = mode;
    store.setMeta(this.id, { mode });
    if (this.q) {
      try {
        await this.q.setPermissionMode(mode);
      } catch (e) {
        this.emitState();
        throw new Error(`Could not switch to ${mode}: ${e.message}`);
      }
    }
    this.emitState();
  }

  async setModel(model) {
    this.model = model || '';
    store.setMeta(this.id, { model: this.model || null });
    if (this.q) await this.q.setModel(model || undefined);
    this.emitState();
  }

  async setEffort(effort) {
    this.effort = effort || '';
    store.setMeta(this.id, { effort: this.effort || null });
    if (this.q) {
      try {
        await this.q.applyFlagSettings({ effortLevel: effort || null });
      } catch (e) {
        log('effort change failed', e?.message);
      }
    }
    this.emitState();
  }

  async stopTask(taskId) {
    if (this.q) await this.q.stopTask(taskId);
  }

  /** Restore files to their state before the given user message. `dryRun` only reports what would change. */
  async rewind(messageId, dryRun = true) {
    await this.ensureStarted();
    const r = await this.q.rewindFiles(messageId, { dryRun });
    if (!dryRun && r.canRewind) log('rewound files', this.id, messageId, r.filesChanged?.length);
    return r;
  }

  async contextUsage() {
    if (!this.q) return null;
    return this.q.getContextUsage({ detail: 'summary' });
  }

  async mcpStatus() {
    if (!this.q) return null;
    const list = await this.q.mcpServerStatus();
    this.setMcp(list);
    return list;
  }

  scheduleMcpRefresh(ms) {
    clearTimeout(this.mcpTimer);
    this.mcpTimer = setTimeout(() => {
      this.mcpTimer = null;
      this.mcpStatus().catch((e) => log('mcp status failed', this.id, e?.message));
    }, ms);
    this.mcpTimer.unref?.();
  }

  /** Keep a compact copy in the state so the UI can warn about servers that need attention. */
  setMcp(list) {
    const next = (list || []).map((s) => ({ name: s.name, status: s.status, error: s.error ? String(s.error).slice(0, 400) : undefined, scope: s.scope || s.source, tools: s.tools?.length || 0, serverInfo: s.serverInfo }));
    const changed = JSON.stringify(next) !== JSON.stringify(this.mcp);
    this.mcp = next;
    if (changed) this.emitState();
  }

  async mcpReconnect(name) {
    if (!this.q) throw new Error('Claude Code is not running for this session');
    await this.q.reconnectMcpServer(name);
    return this.mcpStatus();
  }

  async mcpToggle(name, enabled) {
    if (!this.q) throw new Error('Claude Code is not running for this session');
    await this.q.toggleMcpServer(name, enabled);
    return this.mcpStatus();
  }

  /** Start the OAuth flow. Claude Code opens a local callback listener; we hand the sign-in URL to the UI. */
  async mcpAuth(name) {
    if (!this.q) throw new Error('Claude Code is not running for this session');
    if (typeof this.q.mcpAuthenticate !== 'function') throw new Error('This Claude Code build cannot start MCP sign-in from here; run `claude mcp` in a terminal');
    const r = await this.q.mcpAuthenticate(name);
    log('mcp auth started', this.id, name, r?.redirectScheme, r?.callbackPort);
    // Poll for the reconnect that follows a completed sign-in.
    let tries = 0;
    const tick = () => {
      tries++;
      this.mcpStatus()
        .then((list) => {
          const s = (list || []).find((x) => x.name === name);
          if (s && s.status !== 'needs-auth' && s.status !== 'pending') return;
          if (tries < 90) setTimeout(tick, 2000).unref?.();
        })
        .catch(() => {});
    };
    setTimeout(tick, 2500).unref?.();
    return r || null;
  }

  async mcpAuthCallback(name, url) {
    if (!this.q) throw new Error('Claude Code is not running for this session');
    if (typeof this.q.mcpSubmitOAuthCallbackUrl !== 'function') throw new Error('Not supported by this Claude Code build');
    const r = await this.q.mcpSubmitOAuthCallbackUrl(name, url);
    this.scheduleMcpRefresh(500);
    return r || true;
  }

  async mcpClearAuth(name) {
    if (!this.q) throw new Error('Claude Code is not running for this session');
    if (typeof this.q.mcpClearAuth !== 'function') throw new Error('Not supported by this Claude Code build');
    const r = await this.q.mcpClearAuth(name);
    this.scheduleMcpRefresh(500);
    return r || true;
  }

  async reloadSkills() {
    if (!this.q) return null;
    const r = await this.q.reloadSkills();
    await this.refreshCapabilities();
    return r?.skills?.length ?? null;
  }

  close() {
    if (!this.q) return;
    this.q.pilcrowClosing = true; // per process, so a later crash of a new process is still reported
    try {
      this.input?.close();
      this.q.close();
    } catch (e) {
      log('close failed', e?.message);
    }
  }
}

class SessionManager {
  constructor() {
    this.sessions = new Map();
    this.reaper = setInterval(() => this.reap(), 60_000);
    this.reaper.unref?.();
  }

  find(id) {
    return this.sessions.get(id) || null;
  }

  /** Sessions with a running Claude Code process. */
  live() {
    return [...this.sessions.values()].filter((s) => s.q);
  }

  async get(id) {
    let s = this.sessions.get(id);
    if (s) return s;
    const meta = store.getMeta(id) || {};
    let cwd = meta.cwd;
    let persisted = Boolean(history.findTranscriptFile(id));
    if (!cwd) {
      const info = (await history.listHistory()).find((x) => x.sessionId === id);
      cwd = info?.cwd;
      if (info) persisted = true;
    }
    if (!cwd) throw new Error('Unknown session: ' + id);
    s = new Session(id, { cwd, persisted });
    this.sessions.set(id, s);
    return s;
  }

  /**
   * When a session last moved, for ordering the list. Claude Code keeps writing to a transcript on
   * its own after a turn (summaries, snapshots, the process's own exit), and a row that creeps up
   * the list while nobody touched it reads as a glitch. So a session is placed by the last message
   * you sent it (`touched`, set on create, send and fork), never by the transcript file's time.
   */
  orderOf(id, meta, fileTime) {
    if (meta.touched) return meta.touched;
    // First sight of a session (one from before this rule, or one made in the terminal): it takes
    // the place its file time gives it today, and keeps it. Claude Code and the process's own exit
    // keep writing to transcripts long after a turn, and none of those writes is a reason to move.
    const touched = fileTime || meta.createdAt || Date.now();
    store.setMeta(id, { touched });
    return touched;
  }

  async list() {
    const hist = await history.listHistory();
    const out = new Map();
    const metaAll = store.meta;
    for (const h of hist) {
      const meta = metaAll[h.sessionId] || {};
      out.set(h.sessionId, {
        id: h.sessionId,
        title: meta.title || h.customTitle || (meta.firstPrompt ? titleFromPrompt(meta.firstPrompt) : null) || h.summary || h.firstPrompt || 'Untitled session',
        firstPrompt: h.firstPrompt,
        cwd: slash(h.cwd || meta.cwd),
        project: slash(meta.worktree?.repoRoot || meta.project || h.cwd),
        gitBranch: meta.worktree?.branch || h.gitBranch,
        lastModified: this.orderOf(h.sessionId, meta, h.lastModified),
        createdAt: h.createdAt,
        archived: Boolean(meta.archived),
        pinned: Boolean(meta.pinned),
        worktree: meta.worktree || null,
        scheduled: meta.scheduled || null,
        dispatch: meta.dispatch || null,
        helper: meta.helper ? { leadId: meta.helper.leadId, name: meta.helper.name, state: meta.helper.state || null } : null,
        lead: meta.lead ? { since: meta.lead.since, count: (meta.lead.helpers || []).length } : null,
        status: 'stopped',
        live: false,
      });
    }
    // Sessions created here that have not reached disk yet.
    for (const [id, meta] of Object.entries(metaAll)) {
      if (out.has(id) || !meta.cwd || !this.sessions.has(id)) continue;
      out.set(id, {
        id,
        title: meta.title || (meta.firstPrompt ? titleFromPrompt(meta.firstPrompt) : null) || 'New session',
        cwd: meta.cwd,
        project: meta.worktree?.repoRoot || meta.project || meta.cwd,
        gitBranch: meta.worktree?.branch || null,
        lastModified: meta.touched || meta.createdAt,
        createdAt: meta.createdAt,
        archived: Boolean(meta.archived),
        pinned: Boolean(meta.pinned),
        worktree: meta.worktree || null,
        scheduled: meta.scheduled || null,
        dispatch: meta.dispatch || null,
        helper: meta.helper ? { leadId: meta.helper.leadId, name: meta.helper.name, state: meta.helper.state || null } : null,
        lead: meta.lead ? { since: meta.lead.since, count: (meta.lead.helpers || []).length } : null,
        status: 'idle',
        live: false,
      });
    }
    for (const s of this.sessions.values()) {
      const e = out.get(s.id);
      if (!e) continue;
      e.status = s.status;
      e.live = Boolean(s.q);
      e.pending = s.pending.size;
    }
    return [...out.values()].sort((a, b) => (b.lastModified || 0) - (a.lastModified || 0) || (a.id < b.id ? -1 : 1));
  }

  async create({ cwd, worktree, mode, model, effort, title }) {
    cwd = slash(cwd);
    if (!cwd || !fs.existsSync(cwd)) throw new Error('Folder does not exist: ' + cwd);
    const id = uuid();
    const cfg = store.getConfig();
    const meta = { cwd, createdAt: Date.now(), touched: Date.now(), project: cwd };
    if (title) meta.title = title;
    if (worktree) {
      const info = await git.info(cwd);
      if (!info.isRepo) throw new Error('Worktrees need a git repository.');
      const slug = slugify(worktree.name || title || 'session') || 'session';
      const repoName = path.basename(info.mainRoot || info.root);
      const root = cfg.worktreeRoot || path.join(xdg('data'), 'worktrees');
      const dir = path.join(root, repoName, `${slug}-${id.slice(0, 6)}`);
      const wt = await git.createWorktree({ repo: info.mainRoot || info.root, base: worktree.base || info.branch || 'HEAD', branch: `pilcrow/${slug}`, dir });
      meta.cwd = slash(wt.path);
      meta.project = slash(wt.repoRoot);
      meta.worktree = wt;
    }
    if (mode) meta.mode = mode;
    if (model) meta.model = model;
    if (effort) meta.effort = effort;
    store.setMeta(id, meta);
    store.addRecentProject(meta.project);
    const s = new Session(id, { cwd: meta.cwd, persisted: false });
    this.sessions.set(id, s);
    bus.emit('list-changed');
    return { id, cwd: meta.cwd, worktree: meta.worktree || null };
  }

  async remove(id, { removeWorktree = false } = {}) {
    const s = this.sessions.get(id);
    const meta = store.getMeta(id) || {};
    if (s) s.close();
    this.sessions.delete(id);
    await history.remove(id, meta.cwd || s?.cwd);
    if (removeWorktree && meta.worktree) await git.removeWorktree({ repoRoot: meta.worktree.repoRoot, dir: meta.worktree.path, branch: meta.worktree.branch, deleteBranch: true });
    store.deleteMeta(id);
    bus.emit('removed', { id, meta });
    bus.emit('list-changed');
  }

  async rename(id, title) {
    const s = await this.get(id).catch(() => null);
    store.setMeta(id, { title });
    if (s?.persisted) await history.rename(id, title, s.cwd);
    bus.emit('list-changed');
  }

  async fork(id, { upToMessageId, title } = {}) {
    const s = await this.get(id);
    const meta = store.getMeta(id) || {};
    const base = meta.title || (await history.listHistory()).find((x) => x.sessionId === id)?.summary || 'session';
    const r = await history.fork(id, { dir: s.cwd, upToMessageId, title: title || `${base} (fork)` });
    store.setMeta(r.sessionId, { cwd: s.cwd, project: meta.project || s.cwd, worktree: meta.worktree, mode: s.mode, model: s.model || null, effort: s.effort || null, title: title || `${base} (fork)`, createdAt: Date.now(), touched: Date.now() });
    bus.emit('list-changed');
    return { id: r.sessionId };
  }

  reap() {
    const mins = store.getConfig().idleTimeoutMin || 30;
    for (const s of this.sessions.values()) {
      const backgroundWork = s.bgTasks.length > 0 || [...s.tasks.values()].some((t) => t.status === 'running');
      if (s.q && s.status === 'idle' && !s.pending.size && !backgroundWork && Date.now() - s.lastActivity > mins * 60_000) {
        log('closing idle session', s.id);
        s.close();
      }
    }
  }

  /** Sessions where Claude is still working or waiting on the user. */
  busy() {
    return [...this.sessions.values()].filter((s) => s.q && s.isBusy()).map((s) => ({ id: s.id, cwd: s.cwd, status: s.status }));
  }

  closeAll() {
    for (const s of this.sessions.values()) s.close();
  }
}

export const sessions = new SessionManager();
export { textOfContent };
