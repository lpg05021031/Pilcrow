import { memo, useRef, useState, type ReactNode } from 'react';
import { Collapse } from '../ui/Collapse';
import { ChevronRight, X, Ban, CircleStop, CircleDot, ArrowUpRight } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import type { PermissionRequest } from '../../lib/types';
import { toolMeta, isFileTool, filePathOf } from '../../lib/tools';
import { duration, truncate } from '../../lib/format';
import { openFile } from '../../lib/store';
import { Spinner } from '../ui/bits';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { Markdown } from '../Markdown';
import { PermissionCard } from './Permission';
import { AgentBody } from './AgentBody';
import { HelpersBody } from './HelpersBody';
import { AskBody } from './AskBody';
import { PlanBody } from './PlanBody';
import { WorkflowInline, WorkflowMeta } from './WorkflowBody';
import { str } from '../../lib/tools';
import { ResultText } from './Result';
import { BashBody } from './BashBody';
import { ReadBody } from './ReadBody';
import { EditBody, WriteBody, computeHunks } from './EditBody';
import { SearchBody } from './SearchBody';
import { TodoBody, type Todo } from './TodoBody';

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

// `rev` is passed (not read) so the memo sees an item that changed in place; `perm` comes from
// ctx.pending, so a ctx that is the same object means the same answer there too.
export const ToolCard = memo(function ToolCard({ item, ctx }: { item: ToolItem; rev: number; ctx: ToolCtx; perm?: PermissionRequest }) {
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
      {/* The head opens the card wherever it is clicked. Its button, the step's name and the chevron,
          is what the keyboard and a screen reader reach; Open in Files is a button beside it, not in it. */}
      <div className="tool-head" onClick={() => setOpen(!isOpen)}>
        <span className="tool-ico">
          <span key={perm ? 'wait' : item.status} className="tool-ico-swap">
            <StatusIcon item={item} waiting={!!perm} />
          </span>
        </span>
        <span className="tool-line">
          <button className="tool-toggle" aria-expanded={isOpen}>
            {meta.verb && <span className="tool-verb">{meta.verb}</span>}
            <span className={`tool-target${meta.mono ? ' mono' : ''}`}>{meta.target}</span>
            <ChevronRight size={13} className="tool-chev" />
          </button>
          {file && (
            <button
              className="icon-btn sm tool-open"
              title="Open in Files" aria-label="Open in Files"
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
      <Collapse open={isOpen}>
        {() => (
          <div className="tool-body">
            <ErrorBoundary label={`The ${item.name} step`}>{renderBody(item, ctx)}</ErrorBoundary>
          </div>
        )}
      </Collapse>
      <Collapse open={Boolean(perm)}>{() => lastPerm.current && <PermissionCard perm={lastPerm.current} item={item} ctx={ctx} stale={!perm} />}</Collapse>
    </div>
  );
});

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
