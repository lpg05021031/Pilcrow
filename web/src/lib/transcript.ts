// Incremental transcript model: turns raw SDK messages (live or from history) into render items.
// Items are mutable objects with a `rev` counter so React can re-render only what changed.
import { parsePartialJson } from './partialJson';
import type { RawMsg, ContentBlock, ToolInput, StructuredResult } from './types';

export type ToolStatus = 'streaming' | 'running' | 'done' | 'error' | 'denied' | 'interrupted' | 'background';

interface Base {
  id: string;
  rev: number;
  ts?: number;
  /** The subagent tool this item sits under, or null at the top level (set by the model, read by bump). */
  _parent?: string | null;
}
export interface UserItem extends Base {
  kind: 'user';
  uuid: string;
  text: string;
  images: string[];
  files: string[];
  local?: boolean;
  command?: { name: string; args: string };
  prevUuid?: string;
}
export interface TextItem extends Base {
  kind: 'text';
  text: string;
  streaming: boolean;
}
export interface ThinkingItem extends Base {
  kind: 'thinking';
  text: string;
  streaming: boolean;
  startTs: number;
  endTs?: number;
  redacted?: boolean;
}
export interface ToolResult {
  text: string;
  images: string[];
  isError: boolean;
}
export interface ToolItem extends Base {
  kind: 'tool';
  name: string;
  input: ToolInput;
  inputJson: string;
  inputDone: boolean;
  status: ToolStatus;
  result?: ToolResult;
  structured?: StructuredResult;
  children: Item[];
  startTs: number;
  endTs?: number;
  elapsed?: number;
  parentId: string | null;
  taskId?: string;
  task?: TaskInfoPatch & { status: string };
  summary?: string;
}
export interface NoticeItem extends Base {
  kind: 'notice';
  level: 'info' | 'warning' | 'error' | 'success';
  text: string;
  detail?: string;
  tag?: string;
  count?: number;
}
export interface ResultItem extends Base {
  kind: 'result';
  ok: boolean;
  subtype: string;
  durationMs: number;
  costUsd: number;
  turns: number;
  inputTokens?: number;
  outputTokens?: number;
  text?: string;
  errors?: string[];
  chainUuid?: string;
}
export interface CompactItem extends Base {
  kind: 'compact';
  trigger: string;
  preTokens?: number;
  postTokens?: number;
}
export interface CommandOutputItem extends Base {
  kind: 'command-output';
  text: string;
}
export type Item = UserItem | TextItem | ThinkingItem | ToolItem | NoticeItem | ResultItem | CompactItem | CommandOutputItem;

let seq = 0;
const nid = (p: string) => `${p}-${++seq}`;

/** What a task_started / task_progress / task_notification message says about a background task. */
export interface TaskInfoPatch {
  taskId: string;
  status?: string;
  summary?: string;
  outputFile?: string;
  usage?: unknown;
}

function contentToResult(content: string | ContentBlock[] | undefined, isError: boolean): ToolResult {
  if (typeof content === 'string') return { text: content, images: [], isError };
  const texts: string[] = [];
  const images: string[] = [];
  for (const b of content || []) {
    if (b.type === 'text') texts.push(b.text || '');
    else if (b.type === 'image' && b.source?.type === 'base64') images.push(`data:${b.source.media_type};base64,${b.source.data}`);
    else if (b.type === 'tool_reference') texts.push(`Loaded ${b.tool_name}`);
  }
  return { text: texts.join('\n'), images, isError };
}

function stripTags(s: string, tag: string) {
  const m = s.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
  return m ? m[1] : null;
}

export class TranscriptModel {
  items: Item[] = [];
  tools = new Map<string, ToolItem>();
  private seen = new Set<string>();
  private drafts = new Map<string, Map<number, Item>>();
  private ordinals = new Map<string, number>();
  private current = new Map<string | null, string>();
  private pendingTasks = new Map<string, TaskInfoPatch>();
  rev = 0;
  lastTurnTools: string[] = [];
  private lastApiError: string | null = null;
  private activeRetry: NoticeItem | null = null;
  private retryStatus: number | null = null;

  /** A response arrived after retries: turn the warning into a quiet note. */
  private settleRetry(failed = false) {
    const r = this.activeRetry;
    if (!r) return;
    this.activeRetry = null;
    const n = r.count || 1;
    r.level = 'info';
    r.tag = 'retry-ok';
    r.text = `${failed ? 'Gave up' : 'Connected'} after ${n} ${n > 1 ? 'retries' : 'retry'}${this.retryStatus ? ` (HTTP ${this.retryStatus})` : ''}`;
    this.bump(r);
  }
  lastChain: string | undefined;
  private infoSeen = new Set<string>();

  private bump(item: Item) {
    item.rev++;
    this.rev++;
    // Propagate to enclosing subagent tool items so memoised parents re-render.
    let p = (item as ToolItem).parentId ?? item._parent ?? null;
    let guard = 0;
    while (p && guard++ < 10) {
      const t = this.tools.get(p);
      if (!t) break;
      t.rev++;
      p = t.parentId;
    }
  }

  private container(parent: string | null): Item[] {
    if (!parent) return this.items;
    const t = this.tools.get(parent);
    return t ? t.children : this.items;
  }

  private add(item: Item, parent: string | null) {
    item._parent = parent;
    this.container(parent).push(item);
    this.bump(item);
  }

  pushAll(msgs: RawMsg[]) {
    for (const m of msgs) this.push(m);
  }

  push(m: RawMsg) {
    try {
      switch (m.type) {
        case 'stream_event':
          return this.onStream(m);
        case 'assistant':
          return this.onAssistant(m);
        case 'user':
          return this.onUser(m);
        case 'result':
          return this.onResult(m);
        case 'system':
          return this.onSystem(m);
        case 'tool_progress':
          return this.onToolProgress(m);
        case 'rate_limit_event':
          return this.onRateLimit(m);
        case 'tool_use_summary': {
          const first = this.tools.get(m.preceding_tool_use_ids?.[0] || '');
          if (first) {
            first.summary = m.summary;
            this.bump(first);
          }
          return;
        }
        case 'conversation_reset':
          this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'info', text: 'Conversation cleared', tag: 'divider' }, null);
          return;
        default:
          return;
      }
    } catch (e) {
      console.error('transcript push failed', e, m);
    }
  }

  private onStream(m: RawMsg) {
    if (m.uuid) {
      if (this.seen.has(m.uuid)) return;
      this.seen.add(m.uuid);
    }
    const e = m.event;
    const parent: string | null = m.parent_tool_use_id || null;
    if (!e) return;
    if (e.type === 'message_start') {
      const id = e.message?.id;
      if (!id) return;
      if (!parent) this.settleRetry();
      this.current.set(parent, id);
      if (!this.drafts.has(id)) this.drafts.set(id, new Map());
      return;
    }
    const msgId = this.current.get(parent);
    if (!msgId) return;
    const drafts = this.drafts.get(msgId)!;
    const index = e.index ?? 0;
    if (e.type === 'content_block_start') {
      if ((this.ordinals.get(msgId) || 0) > index) return; // final block already shown
      const cb: ContentBlock = e.content_block || { type: '' };
      let item: Item | null = null;
      if (cb.type === 'text') {
        item = { kind: 'text', id: nid('t'), rev: 0, text: cb.text || '', streaming: true, ts: Date.now() };
        this.unsettled.add(item);
      } else if (cb.type === 'thinking' || cb.type === 'redacted_thinking') {
        item = { kind: 'thinking', id: nid('th'), rev: 0, text: cb.thinking || '', streaming: true, startTs: Date.now(), redacted: cb.type === 'redacted_thinking' };
        this.unsettled.add(item);
      } else if ((cb.type === 'tool_use' || cb.type === 'server_tool_use' || cb.type === 'mcp_tool_use') && cb.id) {
        const existing = this.tools.get(cb.id);
        if (existing) item = existing;
        else {
          const t: ToolItem = { kind: 'tool', id: cb.id, rev: 0, name: cb.name || cb.type, input: {}, inputJson: '', inputDone: false, status: 'streaming', children: [], startTs: Date.now(), parentId: parent };
          this.tools.set(cb.id, t);
          this.unsettled.add(t);
          item = t;
        }
      }
      if (!item) return;
      drafts.set(index, item);
      if (!(item.kind === 'tool' && this.tools.get(item.id) && item.rev > 0)) this.add(item, parent);
      return;
    }
    const item = drafts.get(index);
    if (!item) return;
    if (e.type === 'content_block_delta') {
      const d = e.delta || {};
      if (d.type === 'text_delta' && item.kind === 'text') item.text += d.text || '';
      else if (d.type === 'thinking_delta' && item.kind === 'thinking') item.text += d.thinking || '';
      else if (d.type === 'input_json_delta' && item.kind === 'tool') {
        item.inputJson += d.partial_json || '';
        const parsed = parsePartialJson(item.inputJson);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) item.input = parsed as ToolInput;
      } else return;
      this.bump(item);
    } else if (e.type === 'content_block_stop') {
      if (item.kind === 'thinking') {
        item.streaming = false;
        item.endTs = Date.now();
      } else if (item.kind === 'tool') {
        item.inputDone = true;
        try {
          item.input = JSON.parse(item.inputJson || '{}');
        } catch {
          /* keep partial */
        }
        if (item.status === 'streaming') item.status = 'running';
      } else if (item.kind === 'text') item.streaming = false;
      this.bump(item);
    }
  }

  private onAssistant(m: RawMsg) {
    if (m.uuid) {
      if (this.seen.has(m.uuid)) return;
      this.seen.add(m.uuid);
    }
    const parent: string | null = m.parent_tool_use_id || null;
    const msg = messageOf(m);
    const msgId: string = msg.id || m.uuid || nid('msg');
    const ts = m.timestamp ? Date.parse(m.timestamp) : Date.now();
    const content = blocksOf(msg.content);
    if (!parent) this.settleRetry(Boolean(m.error));
    for (const b of content) {
      const k = this.ordinals.get(msgId) || 0;
      this.ordinals.set(msgId, k + 1);
      const draft = this.drafts.get(msgId)?.get(k);
      if (b.type === 'text') {
        if (m.error) continue; // API failure placeholder text; shown as an error notice below
        if (draft && draft.kind === 'text') {
          draft.text = b.text || '';
          draft.streaming = false;
          this.bump(draft);
        } else if (b.text?.trim()) this.add({ kind: 'text', id: nid('t'), rev: 0, text: b.text, streaming: false, ts }, parent);
      } else if (b.type === 'thinking' || b.type === 'redacted_thinking') {
        if (draft && draft.kind === 'thinking') {
          if (b.thinking) draft.text = b.thinking;
          draft.streaming = false;
          draft.endTs = draft.endTs || Date.now();
          this.bump(draft);
        } else if (b.thinking?.trim() || b.type === 'redacted_thinking') this.add({ kind: 'thinking', id: nid('th'), rev: 0, text: b.thinking || '', streaming: false, startTs: ts, endTs: ts, redacted: b.type === 'redacted_thinking' }, parent);
      } else if ((b.type === 'tool_use' || b.type === 'server_tool_use' || b.type === 'mcp_tool_use') && b.id) {
        let t = this.tools.get(b.id);
        if (!t) {
          t = { kind: 'tool', id: b.id, rev: 0, name: b.name || b.type, input: b.input || {}, inputJson: '', inputDone: true, status: 'running', children: [], startTs: ts, parentId: parent };
          this.unsettled.add(t);
          this.tools.set(b.id, t);
          this.add(t, parent);
        } else {
          t.input = b.input || t.input;
          t.inputDone = true;
          if (t.status === 'streaming') t.status = 'running';
          this.bump(t);
        }
        if (!parent) this.lastTurnTools.push(b.id);
        const pending = this.pendingTasks.get(b.id);
        if (pending) {
          this.applyTask(t, pending);
          this.pendingTasks.delete(b.id);
        }
      } else if (b.type === 'web_search_tool_result' || b.type === 'web_fetch_tool_result' || b.type?.endsWith('_tool_result')) {
        const t = b.tool_use_id ? this.tools.get(b.tool_use_id) : undefined;
        if (t) {
          const errorCode = b.content && !Array.isArray(b.content) && typeof b.content === 'object' ? (b.content as { error_code?: string }).error_code : undefined;
          t.result = { text: summarizeServerResult(b), images: [], isError: Boolean(errorCode) };
          t.structured = { content: b.content };
          t.status = 'done';
          t.endTs = ts;
          this.bump(t);
        }
      }
    }
    if (m.uuid && !parent) this.lastChain = m.uuid;
    if (m.error) {
      const detail = content.map((c) => c.text).filter(Boolean).join('\n');
      this.lastApiError = detail;
      this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'error', text: assistantErrorText(String(m.error)), detail }, null);
    }
  }

  private onUser(m: RawMsg) {
    if (m.uuid) {
      if (this.seen.has(m.uuid)) return;
      this.seen.add(m.uuid);
    }
    const parent: string | null = m.parent_tool_use_id || null;
    const msg = messageOf(m);
    const ts = m.timestamp ? Date.parse(m.timestamp) : Date.now();
    const content = blocksOf(msg.content);
    const results = content.filter((b) => b.type === 'tool_result');
    const prevChain = this.lastChain;
    if (m.uuid && !parent) this.lastChain = m.uuid;
    for (const r of results) {
      const t = r.tool_use_id ? this.tools.get(r.tool_use_id) : undefined;
      if (!t) continue;
      t.result = contentToResult(r.content, Boolean(r.is_error));
      if (results.length === 1 && m.tool_use_result && typeof m.tool_use_result === 'object') t.structured = m.tool_use_result as StructuredResult;
      t.endTs = ts;
      t.status = classifyResult(t);
      this.bump(t);
    }
    if (results.length) return;
    if (parent) return; // a subagent's own prompt; the Agent card already shows it
    const texts: string[] = [];
    const images: string[] = [];
    const files: string[] = [];
    for (const b of content) {
      if (b.type === 'text') {
        const text = b.text || '';
        const af = text.match(/^<attached_file name="([^"]+)">/);
        if (af) files.push(af[1]);
        else texts.push(text);
      } else if (b.type === 'image' && b.source?.type === 'base64') images.push(`data:${b.source.media_type};base64,${b.source.data}`);
    }
    const text = texts.join('\n').trim();
    if (!text && !images.length && !files.length) return;
    // A background task reporting back. Claude Code feeds this to the model as a prompt; in the
    // transcript it is a status line on the task, not something the person typed.
    if (originKind(m) === 'task-notification' || /^<task-notification>/.test(text)) {
      const toolId = stripTags(text, 'tool-use-id')?.trim();
      const taskId = stripTags(text, 'task-id')?.trim() || '';
      const status = stripTags(text, 'status')?.trim();
      const summary = stripTags(text, 'summary')?.trim();
      const t = toolId ? this.tools.get(toolId) : [...this.tools.values()].find((x) => x.taskId === taskId);
      if (t) this.applyTask(t, { taskId, status, summary }, ts);
      this.addTaskLine(t, summary, status);
      return;
    }
    // Claude Code's own messages: meta notes and the summary a compaction leaves for the model.
    if (m.isMeta || m.is_meta || m.isSynthetic || m.isCompactSummary) {
      if (/^\[Request interrupted/.test(text)) this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'info', text: 'Interrupted', tag: 'interrupt' }, null);
      return;
    }
    if (/^\[Request interrupted by user/.test(text)) {
      this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'info', text: 'Interrupted', tag: 'interrupt' }, null);
      return;
    }
    if (text.includes('<local-command-caveat>')) return;
    const stdout = stripTags(text, 'local-command-stdout');
    if (stdout !== null) {
      if (stdout.trim()) this.add({ kind: 'command-output', id: nid('co'), rev: 0, text: stdout.trim(), ts }, null);
      return;
    }
    const cmdName = stripTags(text, 'command-name');
    if (cmdName !== null) {
      this.add({ kind: 'user', id: m.uuid || nid('u'), uuid: m.uuid || '', rev: 0, text: '', images, files, command: { name: cmdName.replace(/^\//, ''), args: (stripTags(text, 'command-args') || '').trim() }, ts }, null);
      return;
    }
    this.add({ kind: 'user', id: m.uuid || nid('u'), uuid: m.uuid || '', rev: 0, text, images, files, local: Boolean(m._local), ts, prevUuid: prevChain }, null);
    this.lastTurnTools = [];
  }

  /** The turn is over: nothing may keep spinning. Subagent tools are included when the process died. */
  /**
   * Items that may still need settling when a turn ends: every tool, text and thought is entered
   * here when it starts and leaves once settled, so the end of a turn touches a turn's worth of
   * items rather than the whole conversation (a history of thousands of turns is replayed on open,
   * and a scan of everything per turn made that quadratic).
   */
  private unsettled = new Set<Item>();

  private settleTurn(status: 'done' | 'interrupted', all = false) {
    for (const it of this.unsettled) {
      if (it.kind === 'tool') {
        const live = it.status === 'running' || it.status === 'streaming' || (all && it.status === 'background');
        if (live && ((!it.parentId && !it.taskId) || all)) {
          it.status = status;
          this.bump(it);
        }
        if (!(it.status === 'running' || it.status === 'streaming' || it.status === 'background')) this.unsettled.delete(it);
      } else if (it.kind === 'text' || it.kind === 'thinking') {
        if (it.streaming) {
          it.streaming = false;
          this.bump(it);
        }
        this.unsettled.delete(it);
      } else this.unsettled.delete(it);
    }
    if (all) this.drafts.clear();
  }

  private onResult(m: RawMsg) {
    if (m.uuid) {
      if (this.seen.has(m.uuid)) return;
      this.seen.add(m.uuid);
    }
    this.settleTurn(m.subtype === 'success' && !m.is_error ? 'done' : 'interrupted');
    const aborted = typeof m.terminal_reason === 'string' && m.terminal_reason.startsWith('aborted');
    if (aborted) {
      const last = this.items[this.items.length - 1];
      if (!(last && last.kind === 'notice' && last.tag === 'interrupt')) this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'info', text: 'Interrupted', tag: 'interrupt' }, null);
      this.lastTurnTools = [];
      return;
    }
    const ok = m.subtype === 'success' && !m.is_error;
    const apiError = this.lastApiError;
    this.lastApiError = null;
    // The API error was already shown as a notice; do not repeat it as the turn's result.
    if (!ok && apiError && String(m.result || '').trim() === apiError.trim()) {
      this.lastTurnTools = [];
      return;
    }
    this.add({ kind: 'result', id: m.uuid || nid('r'), rev: 0, ok, subtype: m.subtype || '', durationMs: m.duration_ms || 0, costUsd: m.total_cost_usd || 0, turns: m.num_turns || 0, inputTokens: (m.usage?.input_tokens || 0) + (m.usage?.cache_read_input_tokens || 0) + (m.usage?.cache_creation_input_tokens || 0), outputTokens: m.usage?.output_tokens || 0, text: ok ? undefined : m.result, errors: m.errors, chainUuid: this.lastChain }, null);
    this.lastTurnTools = [];
  }

  private onSystem(m: RawMsg) {
    if (m.uuid) {
      if (this.seen.has(m.uuid)) return;
      this.seen.add(m.uuid);
    }
    switch (m.subtype) {
      case 'compact_boundary':
        this.add({ kind: 'compact', id: nid('c'), rev: 0, trigger: m.compact_metadata?.trigger || 'auto', preTokens: m.compact_metadata?.pre_tokens, postTokens: m.compact_metadata?.post_tokens }, null);
        return;
      case 'informational': {
        if (m.level === 'info' || !m.content) return;
        if (this.infoSeen.has(m.content)) return;
        this.infoSeen.add(m.content);
        this.add({ kind: 'notice', id: nid('n'), rev: 0, level: m.level === 'warning' ? 'warning' : 'info', text: m.content, tag: 'cli-info' }, null);
        return;
      }
      case 'local_command_output':
        if (m.content?.trim()) this.add({ kind: 'command-output', id: nid('co'), rev: 0, text: m.content }, null);
        return;
      case 'api_retry': {
        const last = this.items[this.items.length - 1];
        const text = `Retrying request (attempt ${m.attempt} of ${m.max_retries})${m.error_status ? ` after HTTP ${m.error_status}` : ''}`;
        this.retryStatus = m.error_status || null;
        if (last && last.kind === 'notice' && last.tag === 'retry' && last === this.activeRetry) {
          last.text = text;
          last.count = (last.count || 1) + 1;
          this.bump(last);
        } else {
          const n: NoticeItem = { kind: 'notice', id: nid('n'), rev: 0, level: 'warning', text, tag: 'retry', detail: m.error == null ? undefined : String(m.error) };
          this.activeRetry = n;
          this.add(n, null);
        }
        return;
      }
      case 'permission_denied': {
        const t = m.tool_use_id ? this.tools.get(m.tool_use_id) : undefined;
        if (t) {
          t.status = 'denied';
          this.bump(t);
        }
        this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'warning', text: `${m.tool_name} was not allowed`, detail: m.decision_reason || (typeof m.message === 'string' ? m.message : undefined) }, null);
        return;
      }
      case 'model_refusal_fallback':
      case 'model_refusal_no_fallback':
        this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'warning', text: m.content || 'The model declined this request.' }, null);
        return;
      case 'pilcrow_error':
        this.settleTurn('interrupted', true);
        this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'error', text: 'Claude Code stopped unexpectedly', detail: m.content }, null);
        return;
      case 'task_started':
      case 'task_notification':
      case 'task_updated': {
        const toolId = m.tool_use_id;
        const info = { taskId: m.task_id || '', status: m.subtype === 'task_started' ? 'running' : m.status || m.patch?.status, summary: m.summary, outputFile: m.output_file, usage: m.usage };
        const t = toolId ? this.tools.get(toolId) : [...this.tools.values()].find((x) => x.taskId === m.task_id);
        // Work that was already running in the background gets a line when it reports back, because
        // Claude usually picks the conversation up again right after it.
        const wasBackground = Boolean(t && (t.status === 'background' || t.result));
        if (t) this.applyTask(t, info);
        else if (toolId) this.pendingTasks.set(toolId, info);
        if (m.subtype === 'task_notification' && wasBackground) this.addTaskLine(t, m.summary, info.status);
        return;
      }
      default:
        return;
    }
  }

  private addTaskLine(t: ToolItem | undefined, summary: string | undefined, status?: string) {
    const outcome = status === 'failed' ? 'failed' : status === 'killed' || status === 'stopped' ? 'was stopped' : 'finished';
    const desc = (t && typeof t.input.description === 'string' ? t.input.description : '').trim();
    let text = summary || '';
    if (t && (t.name === 'Agent' || t.name === 'Task')) text = desc ? `Agent “${desc}” ${outcome}` : `Agent ${outcome}`;
    else if (t && (t.name === 'Bash' || t.name === 'PowerShell')) text = `Background command “${desc || String(t.input?.command || '').split('\n')[0].slice(0, 60)}” ${outcome}`;
    if (!text) return;
    this.add({ kind: 'notice', id: nid('n'), rev: 0, level: status === 'failed' ? 'warning' : 'info', text, tag: 'task' }, null);
  }

  private applyTask(t: ToolItem, info: TaskInfoPatch, at = Date.now()) {
    t.taskId = info.taskId;
    const given = Object.fromEntries(Object.entries(info).filter(([, v]) => v !== undefined)) as Partial<TaskInfoPatch>;
    t.task = { ...(t.task || { status: 'running' }), ...given, taskId: info.taskId, status: given.status || t.task?.status || 'running' };
    if (info.status && ['completed', 'failed', 'stopped', 'killed'].includes(info.status)) {
      t.status = info.status === 'completed' ? 'done' : info.status === 'failed' ? 'error' : 'interrupted';
      t.endTs = at;
    } else if (info.status === 'running' && t.status !== 'done') t.status = t.result ? 'background' : 'running';
    this.bump(t);
  }

  private onToolProgress(m: RawMsg) {
    const t = m.tool_use_id ? this.tools.get(m.tool_use_id) : undefined;
    if (!t) return;
    t.elapsed = m.elapsed_time_seconds;
    if (t.status === 'streaming') t.status = 'running';
    this.bump(t);
  }

  private onRateLimit(m: RawMsg) {
    const info = m.rate_limit_info || {};
    if (info.status === 'rejected') this.add({ kind: 'notice', id: nid('n'), rev: 0, level: 'error', text: 'Usage limit reached', detail: info.resetsAt ? `Resets ${new Date(info.resetsAt * 1000).toLocaleString()}` : undefined }, null);
  }

  /** Find the most recent tool item with this name (used for plans and todos). */
  latestTool(name: string | string[]): ToolItem | null {
    const names = Array.isArray(name) ? name : [name];
    let found: ToolItem | null = null;
    for (const t of this.tools.values()) if (names.includes(t.name) && (!found || t.startTs >= found.startTs)) found = t;
    return found;
  }
}

function classifyResult(t: ToolItem): ToolStatus {
  const r = t.result!;
  const s = t.structured;
  if (s && typeof s === 'object') {
    if (s.isAsync || s.status === 'async_launched' || (s.backgroundTaskId && !r.isError)) return t.task && t.task.status !== 'running' ? (t.task.status === 'completed' ? 'done' : 'interrupted') : 'background';
  }
  if (!r.isError) return 'done';
  const txt = r.text || '';
  if (/interrupted by user/i.test(txt)) return 'interrupted';
  if (/doesn't want to proceed|user declined|was rejected|permission to use .* (has been|was) denied|not allowed/i.test(txt)) return 'denied';
  return 'error';
}

function assistantErrorText(code: string) {
  const map: Record<string, string> = {
    authentication_failed: 'Authentication failed — check the API key or gateway token Claude Code uses.',
    billing_error: 'The API account has a billing problem.',
    rate_limit: 'Rate limited by the API. Claude Code will retry.',
    overloaded: 'The API is overloaded right now.',
    invalid_request: 'The API rejected the request.',
    model_not_found: 'The selected model is not available through this endpoint.',
    server_error: 'The API returned a server error.',
    max_output_tokens: 'The response hit the output token limit.',
  };
  return map[code] || `Request failed (${code})`;
}

function summarizeServerResult(b: ContentBlock) {
  const c = b.content as string | ContentBlock[] | { error_code?: string } | undefined;
  if (Array.isArray(c)) return c.map((r) => (r.title ? `${r.title} — ${r.url}` : r.url || '')).filter(Boolean).join('\n');
  if (c && typeof c === 'object' && 'error_code' in c && c.error_code) return `Error: ${c.error_code}`;
  return typeof c === 'string' ? c : JSON.stringify(c, null, 2);
}

// ---------------------------------------------------------------- raw message shapes

/** The message body of a raw assistant/user message; the SDK sometimes sends a bare string. */
function messageOf(m: RawMsg): { id?: string; content?: string | ContentBlock[] } {
  if (!m.message) return {};
  if (typeof m.message === 'string') return { content: m.message };
  return m.message;
}

/** Content as blocks: a plain string is one text block. */
function blocksOf(content: string | ContentBlock[] | undefined): ContentBlock[] {
  if (Array.isArray(content)) return content;
  if (typeof content === 'string') return [{ type: 'text', text: content }];
  return [];
}

/** `origin` is an object on task notifications and a string elsewhere. */
function originKind(m: RawMsg): string | undefined {
  const o = m.origin as unknown;
  return o && typeof o === 'object' ? (o as { kind?: string }).kind : typeof o === 'string' ? o : undefined;
}
