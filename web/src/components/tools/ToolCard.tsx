import { memo, useMemo, useRef, useState, type ReactNode } from 'react';
import { Collapse } from '../ui/Collapse';
import { ChevronRight, Check, X, Ban, CircleStop, FileText, CircleDot, ArrowUpRight } from 'lucide-react';
import { structuredPatch } from 'diff';
import type { ToolItem } from '../../lib/transcript';
import type { PermissionRequest } from '../../lib/types';
import { toolMeta, isFileTool, filePathOf } from '../../lib/tools';
import { duration, relPath, plural, truncate } from '../../lib/format';
import { openFile, setDialog, call, toast, useStore, openLightbox } from '../../lib/store';
import { Spinner, CopyButton } from '../ui/bits';
import { CodeView, DiffView, AnsiText, hunksFromPatch } from '../Code';
import { Markdown } from '../Markdown';
import { PermissionCard } from './Permission';
import { AgentBody } from './AgentBody';
import { HelpersBody } from './HelpersBody';
import { AskBody } from './AskBody';
import { PlanBody } from './PlanBody';
import { WorkflowInline, WorkflowMeta } from './WorkflowBody';
import { str } from '../../lib/tools';
import type { ToolInput, PatchHunk } from '../../lib/types';

export interface ToolCtx {
  sessionId: string;
  cwd: string;
  pending: Map<string, PermissionRequest>;
  depth: number;
  view?: 'normal' | 'thinking' | 'verbose';
}

function diffStats(item: ToolItem) {
  const s = item.structured;
  let add = 0;
  let del = 0;
  const patch = s?.structuredPatch;
  if (Array.isArray(patch) && patch.length) {
    for (const h of patch)
      for (const l of h.lines || []) {
        if (l[0] === '+') add++;
        else if (l[0] === '-') del++;
      }
    return { add, del };
  }
  if (item.name === 'Edit' && item.input?.old_string !== undefined && item.inputDone) {
    for (const h of computeHunks(String(item.input.old_string || ''), String(item.input.new_string || '')))
      for (const l of h.lines) {
        if (l.t === '+') add++;
        else if (l.t === '-') del++;
      }
    return { add, del };
  }
  if (item.name === 'Write' && typeof item.input?.content === 'string' && (!s || s.type === 'create')) return { add: item.input.content.split('\n').length, del: 0 };
  return null;
}

function StatusIcon({ item, waiting }: { item: ToolItem; waiting?: boolean }) {
  const Icon = toolMeta(item.name, item.input).icon;
  if (waiting) return <span className="wait-dot" aria-label="Waiting for you" />;
  switch (item.status) {
    case 'streaming':
    case 'running':
      return <Spinner size={14} />;
    case 'error':
      return <X size={14} className="st-err" />;
    case 'denied':
      return <Ban size={14} className="st-err" />;
    case 'interrupted':
      return <CircleStop size={14} className="st-muted" />;
    case 'background':
      return <CircleDot size={14} className="st-bg" />;
    default:
      return <Icon size={14} />;
  }
}

/** Finished file edits fold to one line; they open while being proposed, while waiting for approval, or on error. */
function openByDefault(item: ToolItem, waiting: boolean) {
  if (waiting || item.status === 'error') return true;
  if (['Edit', 'MultiEdit', 'Write', 'NotebookEdit'].includes(item.name)) return item.status === 'streaming' || item.status === 'running';
  return false;
}

export const ToolCard = memo(
  function ToolCard({ item, ctx }: { item: ToolItem; rev: number; ctx: ToolCtx; perm?: PermissionRequest }) {
    const perm = ctx.pending.get(item.id);
    const interactive = perm && (item.name === 'AskUserQuestion' || item.name === 'ExitPlanMode');
    const [open, setOpen] = useState<boolean | null>(null);
    const planFile = item.name === 'Write' && /\/\.claude\/plans\//.test(str(item.input, 'file_path'));
    const isOpen = open ?? ((openByDefault(item, Boolean(perm)) || (ctx.view === 'verbose' && item.status !== 'running' && item.status !== 'streaming')) && !planFile);
    const meta = toolMeta(item.name, item.input, ctx.cwd);
    // A failed edit changed nothing, so it gets no +/− counts.
    const stats = item.status === 'error' ? null : diffStats(item);
    const elapsed = item.endTs && item.startTs ? item.endTs - item.startTs : item.elapsed ? item.elapsed * 1000 : 0;
    const file = isFileTool(item.name) ? filePathOf(item.name, item.input) : null;
    // Keep showing the answered request while it folds away, instead of having it vanish mid-layout.
    const lastPerm = useRef(perm);
    if (perm) lastPerm.current = perm;

    if (interactive || (item.name === 'AskUserQuestion' && item.status !== 'streaming') || (item.name === 'ExitPlanMode' && item.status !== 'streaming')) {
      return (
        <div className={`tool special ${item.status}`}>
          {item.name === 'AskUserQuestion' ? <AskBody item={item} perm={perm} ctx={ctx} /> : <PlanBody item={item} perm={perm} ctx={ctx} />}
        </div>
      );
    }

    return (
      <div className={`tool ${item.status}${isOpen ? ' open' : ''}${perm ? ' awaiting' : ''}`} data-tool={item.name} id={`tool-${item.id}`}>
        <div className="tool-head" role="button" tabIndex={0} onClick={() => setOpen(!isOpen)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpen(!isOpen))} aria-expanded={isOpen}>
          <span className="tool-ico">
            <span key={perm ? 'wait' : item.status} className="tool-ico-swap">
              <StatusIcon item={item} waiting={!!perm} />
            </span>
          </span>
          <span className="tool-line">
            {meta.verb && <span className="tool-verb">{meta.verb}</span>}
            <span className={`tool-target${meta.mono ? ' mono' : ''}`}>{meta.target}</span>
            <ChevronRight size={13} className="tool-chev" />
            {file && (
              <button
                className="icon-btn sm tool-open"
                title="Open in Files"
                onClick={(e) => {
                  e.stopPropagation();
                  openFile(file);
                }}
              >
                <ArrowUpRight size={13} />
              </button>
            )}
          </span>
          <span className="tool-meta" key={item.status === 'streaming' || item.status === 'running' ? 'live' : 'done'}>
            {item.status === 'background' && <span className="badge info">background</span>}
            {item.status === 'denied' && <span className="badge err">declined</span>}
            {item.status === 'error' && <span className="tool-flag err">Failed</span>}
            {item.status === 'interrupted' && <span className="badge">stopped</span>}
            {(item.name === 'Workflow' || item.name === 'RunWorkflow') && <WorkflowMeta sessionId={ctx.sessionId} toolUseId={item.id} />}
            {stats && (stats.add > 0 || stats.del > 0) && (
              <span className="tool-stats tnum">
                {stats.add > 0 && <span className="num-add">+{stats.add}</span>}
                {stats.del > 0 && <span className="num-del">−{stats.del}</span>}
              </span>
            )}
            {elapsed > 1500 && !(stats && (stats.add > 0 || stats.del > 0)) && <span className="tool-time tnum">{duration(elapsed)}</span>}
          </span>
        </div>
        <Collapse open={isOpen}>{() => <div className="tool-body">{renderBody(item, ctx)}</div>}</Collapse>
        <Collapse open={Boolean(perm)}>{() => lastPerm.current && <PermissionCard perm={lastPerm.current} item={item} ctx={ctx} stale={!perm} />}</Collapse>
      </div>
    );
  },
  (a, b) => a.item === b.item && a.rev === b.rev && a.ctx.pending === b.ctx.pending && a.ctx.cwd === b.ctx.cwd && a.ctx.view === b.ctx.view,
);

function renderBody(item: ToolItem, ctx: ToolCtx): ReactNode {
  const i = item.input || {};
  const s = item.structured;
  const r = item.result;
  if (item.name.startsWith('mcp__helpers__')) return <HelpersBody item={item} ctx={ctx} />;
  switch (item.name) {
    case 'Bash':
    case 'PowerShell':
      return <BashBody item={item} ctx={ctx} />;
    case 'Read':
      return <ReadBody item={item} ctx={ctx} />;
    case 'Edit':
    case 'MultiEdit':
      return <EditBody item={item} ctx={ctx} />;
    case 'Write':
      return <WriteBody item={item} ctx={ctx} />;
    case 'Grep':
    case 'Glob':
      return <SearchBody item={item} ctx={ctx} />;
    case 'TodoWrite':
      return <TodoBody todos={Array.isArray(i.todos) ? (i.todos as Todo[]) : []} />;
    case 'Agent':
    case 'Task':
      return <AgentBody item={item} ctx={ctx} />;
    case 'WebFetch':
    case 'WebSearch':
      return (
        <div className="tool-section">
          {str(i, 'prompt') && <div className="tool-note">{str(i, 'prompt')}</div>}
          {r ? <Markdown text={truncate(r.text, 20000)} cwd={ctx.cwd} className="md-compact" /> : <div className="faint">Waiting for the result…</div>}
        </div>
      );
    case 'ToolSearch':
      return <ResultText item={item} />;
    case 'Workflow':
    case 'RunWorkflow':
      return <WorkflowInline item={item} ctx={ctx} />;
    default:
      return (
        <>
          {r?.isError && <ResultText item={item} input={i} ctx={ctx} />}
          {!r?.isError && Object.keys(i).length > 0 && (
            <div className="tool-section">
              <div className="tool-label">Input</div>
              <pre className="tool-pre">{JSON.stringify(i, null, 2)}</pre>
            </div>
          )}
          {s && typeof s === 'object' && !r?.text && (
            <div className="tool-section">
              <div className="tool-label">Result</div>
              <pre className="tool-pre">{JSON.stringify(s, null, 2)}</pre>
            </div>
          )}
          {!r?.isError && <ResultText item={item} label />}
        </>
      );
  }
}

/** Internal failures that have a plain explanation; the raw text stays in the tooltip. */
function plainError(text: string) {
  if (/Tool permission (request failed|stream closed)/i.test(text)) return 'The approval request was cut off when Claude Code restarted, so this step did not run. Ask Claude to try again.';
  return null;
}

/** Claude Code wraps tool failures in <tool_use_error> tags meant for the model; people only need the message. */
function unwrapError(text: string) {
  return text.replace(/^\s*<tool_use_error>([\s\S]*?)<\/tool_use_error>\s*$/, '$1').trim();
}

/**
 * An error is read in two steps: what went wrong (one calm line), then the raw text only if wanted.
 * Tool runners prefix their messages ("Error executing tool x: …"); that prefix says nothing new here.
 */
function splitError(text: string) {
  const clean = unwrapError(text).replace(/^Error executing tool [\w-]+:\s*/i, '').replace(/^Error:\s*/i, '');
  const lines = clean.split('\n');
  const title = (lines[0] || 'Failed').trim();
  const rest = lines.slice(1).join('\n').trim();
  return { title: title.length > 180 ? title.slice(0, 177) + '…' : title, rest, full: clean };
}

function ErrorBlock({ text, input, fix }: { text: string; input?: ToolInput; fix?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { title, rest, full } = splitError(text);
  const hasInput = input && typeof input === 'object' && Object.keys(input).length > 0;
  const hasMore = rest.length > 0 || title.endsWith('…') || hasInput;
  return (
    <div className="tool-error">
      <div className="tool-error-title">{title}</div>
      {fix}
      {hasMore && (
        <button className="tool-error-more" onClick={() => setOpen(!open)} aria-expanded={open}>
          <ChevronRight size={12} className={`tool-chev${open ? ' open' : ''}`} />
          {open ? 'Hide details' : 'Details'}
        </button>
      )}
      <Collapse open={open}>
        {() => (
          <div className="tool-error-detail">
            {(rest || title.endsWith('…')) && <pre className="tool-pre err">{truncate(full, 60000)}</pre>}
            {hasInput && (
              <>
                <div className="tool-label">Input</div>
                <pre className="tool-pre">{JSON.stringify(input, null, 2)}</pre>
              </>
            )}
          </div>
        )}
      </Collapse>
    </div>
  );
}

/** For a failed MCP tool: the server's live status decides whether a sign-in or a reconnect fixes it. */
function McpFix({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const serverKey = item.name.split('__')[1] || '';
  const live = useStore((s) => s.rt[ctx.sessionId]?.mcp);
  const text = item.result?.text || '';
  const match = (live || []).find((m) => m.name === serverKey) || (live || []).find((m) => text.includes(m.name));
  const name = match?.name || serverKey;
  const authy = /mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|token (has )?expired/i.test(text) || match?.status === 'needs-auth';
  const down = /not connected|connection (closed|refused|reset)|ECONNREFUSED|MCP server .* (failed|unavailable)/i.test(text) || match?.status === 'failed';
  const [busy, setBusy] = useState(false);
  // Only an error that talks about auth or connectivity gets a fix; a validation error is Claude's to sort out.
  const textual = /mcp_authenticate|needs? (re-?)?auth|not authenticated|unauthori[sz]ed|\b401\b|invalid[_ ]token|token (has )?expired|not connected|connection (closed|refused|reset)|ECONNREFUSED|MCP server .* (failed|unavailable)/i.test(text);
  if (!name || !textual || (!authy && !down)) return null;
  if (match?.status === 'connected' && !authy) return null;
  const retry = async () => {
    setBusy(true);
    try {
      await call('session.mcp.reconnect', { id: ctx.sessionId, name });
      toast(`Reconnected ${name} — ask Claude to try again`, 'success');
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="tool-fix">
      {authy ? (
        <button className="btn xs primary" onClick={() => setDialog('mcpAuth', { sessionId: ctx.sessionId, name })}>
          Sign in to {name}
        </button>
      ) : (
        <button className="btn xs" disabled={busy} onClick={retry}>
          {busy ? <Spinner size={11} /> : null} Reconnect {name}
        </button>
      )}
      <span className="faint">then ask Claude to try again</span>
    </div>
  );
}

function ResultText({ item, label, input, ctx }: { item: ToolItem; label?: boolean; input?: ToolInput; ctx?: ToolCtx }) {
  const r = item.result;
  if (!r) return item.status === 'running' ? <div className="faint tool-wait">Running…</div> : null;
  return (
    <div className="tool-section">
      {label && !r.isError && <div className="tool-label">Output</div>}
      {r.text &&
        (r.isError ? (
          plainError(r.text) ? (
            <div className="tool-note err" title={r.text}>
              {plainError(r.text)}
            </div>
          ) : (
            <ErrorBlock text={r.text} input={input} fix={ctx && item.name.startsWith('mcp__') ? <McpFix item={item} ctx={ctx} /> : null} />
          )
        ) : (
          <pre className="tool-pre">{truncate(r.text, 60000)}</pre>
        ))}
      {r.images.map((src, k) => (
        <img key={k} className="tool-img" src={src} alt="Tool output" onClick={() => openLightbox(src)} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Bash

function BashBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const waiting = ctx.pending.has(item.id);
  const i = item.input || {};
  const s = item.structured && typeof item.structured === 'object' ? item.structured : null;
  const r = item.result;
  const [full, setFull] = useState(false);
  let out = '';
  if (s) out = [s.stdout, s.stderr].filter(Boolean).join(s.stdout && s.stderr ? '\n' : '');
  if (!out && r) out = r.isError ? unwrapError(r.text) : r.text;
  const exit = r?.isError ? (r.text.match(/^(?:Error: )?Exit code (\d+)/) || [])[1] : null;
  if (exit && out.startsWith('Exit code')) out = out.replace(/^Exit code \d+\n?/, '');
  if (exit && out.startsWith('Error: Exit code')) out = out.replace(/^Error: Exit code \d+\n?/, '');
  const lines = out.split('\n');
  const long = lines.length > 18 && !full;
  return (
    <div className="bash">
      <div className="bash-cmd">
        <span className="bash-prompt">$</span>
        <code className="grow">{str(i, 'command')}</code>
        <CopyButton text={str(i, 'command')} title="Copy command" size={12} />
      </div>
      {item.status === 'running' && !r && !waiting && <div className="bash-running faint">{`Running${item.elapsed ? ` · ${Math.round(item.elapsed)}s` : '…'}`}</div>}
      {out && (
        <pre className={`bash-out${r?.isError ? ' err' : ''}`}>
          <AnsiText text={long ? lines.slice(-18).join('\n') : out} />
        </pre>
      )}
      {long && (
        <button className="code-more" onClick={() => setFull(true)}>
          Show all {lines.length} lines
        </button>
      )}
      {(exit || s?.backgroundTaskId || s?.interrupted || s?.returnCodeInterpretation) && (
        <div className="bash-foot">
          {exit && <span className="badge err">exit {exit}</span>}
          {s?.interrupted && <span className="badge">interrupted</span>}
          {s?.returnCodeInterpretation && <span className="faint">{s.returnCodeInterpretation}</span>}
          {s?.backgroundTaskId && (
            <span className="faint">
              Background task <code>{s.backgroundTaskId}</code>
              {item.task?.status ? ` · ${item.task.status}` : ''}
            </span>
          )}
          {item.task?.outputFile && (
            <button className="btn sm ghost" onClick={() => openFile(item.task!.outputFile!)}>
              <FileText size={12} /> Output file
            </button>
          )}
        </div>
      )}
      {s?.gitOperation?.commit && <div className="bash-foot faint">Committed {String(s.gitOperation.commit).slice(0, 8)}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- Read

function stripLineNumbers(text: string) {
  const lines = text.split('\n');
  if (lines.filter((l) => /^\s*\d+[\t→]/.test(l)).length < lines.length * 0.6) return { text, start: 1 };
  const m = lines[0].match(/^\s*(\d+)/);
  return { text: lines.map((l) => l.replace(/^\s*\d+[\t→]/, '')).join('\n'), start: m ? Number(m[1]) : 1 };
}

function ReadBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const s = item.structured;
  const r = item.result;
  const path = str(item.input, 'file_path');
  let content = '';
  let start = 1;
  if (s?.file?.content !== undefined) {
    content = s.file.content || '';
    start = s.file.startLine || 1;
  } else if (r?.text) ({ text: content, start } = stripLineNumbers(r.text.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '').trimEnd()));
  if (r?.isError) return <ResultText item={item} />;
  if (s?.type === 'image' || r?.images.length) return <ResultText item={item} />;
  return (
    <div className="tool-section">
      <div className="tool-file-bar">
        <button className="link mono" onClick={() => openFile(path, start)}>
          {relPath(path, ctx.cwd)}
        </button>
        {/* Only worth saying when Claude read part of the file. */}
        {s?.file?.totalLines && s.file.numLines !== undefined && s.file.numLines < s.file.totalLines - 1 ? (
          <span className="faint tnum">
            Lines {start}–{start + s.file.numLines - 1} of {s.file.totalLines}
          </span>
        ) : null}
      </div>
      {content ? <CodeView text={content} path={path} startLine={start} maxLines={60} /> : <div className="faint">Waiting…</div>}
    </div>
  );
}

// ---------------------------------------------------------------- Edit / Write

const hunkCache = new Map<string, ReturnType<typeof hunksFromPatch>>();
function computeHunks(oldS: string, newS: string) {
  const key = oldS + '\u0000' + newS;
  const hit = hunkCache.get(key);
  if (hit) return hit;
  const p = structuredPatch('a', 'b', oldS, newS, '', '', { context: 3 });
  const h = hunksFromPatch(p.hunks as PatchHunk[]);
  if (hunkCache.size > 300) hunkCache.delete(hunkCache.keys().next().value!);
  hunkCache.set(key, h);
  return h;
}

function EditBody({ item }: { item: ToolItem; ctx: ToolCtx }) {
  const i = item.input || {};
  const s = item.structured;
  const path = str(i, 'file_path');
  const hunks = useMemo(() => {
    if (Array.isArray(s?.structuredPatch) && s.structuredPatch.length) return { hunks: hunksFromPatch(s.structuredPatch), numbered: true };
    if (Array.isArray(i.edits)) return { hunks: (i.edits as { old_string?: string; new_string?: string }[]).flatMap((e) => computeHunks(String(e.old_string ?? ''), String(e.new_string ?? ''))), numbered: false };
    if (i.old_string !== undefined || i.new_string !== undefined) return { hunks: computeHunks(String(i.old_string ?? ''), String(i.new_string ?? '')), numbered: false };
    return { hunks: [], numbered: false };
  }, [s, i.old_string, i.new_string, i.edits]);
  // While the edit is still arriving, a diff against half a replacement would show phantom deletions.
  // Show the new text as it is written instead, and the real diff once the edit is complete.
  if (item.status === 'streaming') {
    const edits = Array.isArray(i.edits) ? (i.edits as { new_string?: string }[]) : null;
    const draft = typeof i.new_string === 'string' ? i.new_string : edits ? String(edits[edits.length - 1]?.new_string ?? '') : '';
    return (
      <div className="tool-section">
        <div className="tool-note shimmer-soft">Writing the change…</div>
        {draft ? <CodeView text={draft} path={path} maxLines={16} /> : null}
      </div>
    );
  }
  return (
    <div className="tool-section">
      {Boolean(i.replace_all) && <div className="tool-note">Replaced every occurrence</div>}
      {hunks.hunks.length ? <DiffView hunks={hunks.hunks} path={path} numbers={hunks.numbered} maxRows={16} /> : null}
      {item.result?.isError && <ResultText item={item} />}
    </div>
  );
}

function WriteBody({ item }: { item: ToolItem; ctx: ToolCtx }) {
  const i = item.input || {};
  const s = item.structured;
  const path = str(i, 'file_path');
  const patch = s?.type === 'update' && Array.isArray(s.structuredPatch) && s.structuredPatch.length ? s.structuredPatch : null;
  const hunks = useMemo(() => (patch ? hunksFromPatch(patch) : null), [patch]);
  return (
    <div className="tool-section">
      {hunks ? <DiffView hunks={hunks} path={path} maxRows={16} /> : typeof i.content === 'string' ? <CodeView text={i.content} path={path} maxLines={16} /> : null}
      {item.result?.isError && <ResultText item={item} />}
    </div>
  );
}

// ---------------------------------------------------------------- Grep / Glob

function SearchBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const s = item.structured;
  const r = item.result;
  if (r?.isError) return <ResultText item={item} />;
  const files: string[] = s?.filenames || [];
  if (typeof s?.content === 'string' && s.content) return <pre className="tool-pre">{truncate(s.content, 40000)}</pre>;
  if (!files.length) return r?.text ? <pre className="tool-pre">{truncate(r.text, 40000)}</pre> : <div className="faint tool-wait">{r ? 'No matches' : 'Searching…'}</div>;
  return (
    <div className="file-list">
      <div className="faint">{plural(s?.numFiles ?? files.length, 'file')}{s?.truncated ? ' (truncated)' : ''}</div>
      {files.slice(0, 200).map((f) => (
        <button key={f} className="file-list-item mono" onClick={() => openFile(f.startsWith('/') ? f : `${ctx.cwd}/${f}`)}>
          {relPath(f, ctx.cwd)}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Todos

export interface Todo {
  content?: string;
  status?: string;
  activeForm?: string;
}

export function TodoBody({ todos }: { todos: Todo[] }) {
  if (!Array.isArray(todos)) return null;
  return (
    <ul className="todos">
      {todos.map((t, k) => (
        <li key={k} className={`todo ${t.status}`}>
          <span className="todo-box">{t.status === 'completed' ? <Check size={11} /> : t.status === 'in_progress' ? <span className="todo-dot" /> : null}</span>
          <span>{t.status === 'in_progress' ? t.activeForm || t.content : t.content}</span>
        </li>
      ))}
    </ul>
  );
}
