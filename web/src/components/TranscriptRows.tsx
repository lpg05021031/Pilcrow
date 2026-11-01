// The rows of the conversation: what the person said, what Claude said and thought, notices, a turn's
// footer, and the line that says Claude is at work. Transcript groups them and pages them.
import { memo, useState } from 'react';
import { PencilLine, GitFork, ChevronRight, Info, TriangleAlert, CircleAlert, Paperclip, Terminal as TermIcon, History, CircleCheck, Sparkles } from 'lucide-react';
import { useStore, forkSession, insertIntoComposer, toast, call, confirmAction, bumpFs, openLightbox } from '../lib/store';
import type { Item, UserItem, ThinkingItem, TextItem, NoticeItem, ResultItem, CompactItem, CommandOutputItem } from '../lib/transcript';
import { isBusy, isWorking } from '../lib/types';
import { duration, tokens, cost, relPath, plural } from '../lib/format';
import { Markdown } from './Markdown';
import { ToolCard, type ToolCtx } from './tools/ToolCard';
import { HelperNote, FromLead } from './tools/HelpersBody';
import { useNow } from './ui/hooks';
import { CopyButton } from './ui/clipboard';
import { onActivate } from './ui/keys';
import { WorkingMark } from './ui/brand';
import { useSmoothText, useThrottled } from '../lib/motion';
import { Collapse } from './ui/Collapse';
import { langOf } from '../lib/labels';

// `rev` is passed (not read) so the memo sees an item that changed in place.
export const ItemView = memo(function ItemView({ item, ctx }: { item: Item; rev: number; ctx: ToolCtx }) {
  switch (item.kind) {
    case 'user':
      return <UserMessage item={item} ctx={ctx} />;
    case 'text':
      return <AssistantText item={item} ctx={ctx} />;
    case 'thinking':
      return <Thinking item={item} view={ctx.view} />;
    case 'tool':
      return <ToolCard item={item} rev={item.rev} ctx={ctx} perm={ctx.pending.get(item.id)} />;
    case 'notice':
      return <Notice item={item} />;
    case 'compact':
      return <Compact item={item} />;
    case 'command-output':
      return <CommandOutput item={item} ctx={ctx} />;
    case 'result':
      return <ResultFooter item={item} ctx={ctx} />;
    default:
      return null;
  }
});

// ---------------------------------------------------------------- items

function UserMessage({ item, ctx }: { item: UserItem; ctx: ToolCtx }) {
  const queued = useStore((s) => s.rt[ctx.sessionId]?.queued?.includes(item.uuid));
  const busy = useStore((s) => isBusy(s.rt[ctx.sessionId]?.status));
  if (item.note) return <HelperNote item={item} ctx={ctx} />;
  if (item.from) return <FromLead item={item} ctx={ctx} />;
  if (item.command) {
    return (
      <div className="msg-user command">
        <span className="cmd-chip mono">/{item.command.name}</span>
        {item.command.args && <span className="mono muted">{item.command.args}</span>}
      </div>
    );
  }
  const edit = async () => {
    if (!item.prevUuid) {
      insertIntoComposer(ctx.sessionId, item.text, true);
      return;
    }
    const id = await forkSession(ctx.sessionId, item.prevUuid);
    insertIntoComposer(id, item.text, true);
  };
  const rewind = async () => {
    try {
      const r = await call<{ canRewind: boolean; error?: string; filesChanged?: string[]; insertions?: number; deletions?: number }>('session.rewind', { id: ctx.sessionId, messageId: item.uuid, dryRun: true });
      if (!r.canRewind) return toast(r.error || 'There is no saved copy of the files from before this message.', 'warning');
      const files = (r.filesChanged || []).map((f) => relPath(f, ctx.cwd));
      if (!files.length) return toast('No files changed after this message.', 'info');
      confirmAction({
        title: 'Restore files?',
        body: `Put ${files.length === 1 ? 'this file' : `these ${files.length} files`} back the way ${files.length === 1 ? 'it was' : 'they were'} before this message. The conversation stays as it is.`,
        list: files,
        confirm: 'Restore files',
        danger: true,
        checkbox: item.prevUuid ? { label: 'Also start a new branch of the conversation from before this message', value: false } : undefined,
        onConfirm: async (branch) => {
          const done = await call<{ canRewind: boolean; error?: string; skippedLinks?: number }>('session.rewind', { id: ctx.sessionId, messageId: item.uuid, dryRun: false });
          if (!done.canRewind) return toast(done.error || 'Could not restore the files.', 'error');
          bumpFs();
          toast(`Restored ${plural(files.length, 'file')}${done.skippedLinks ? ` (${plural(done.skippedLinks, 'linked file')} skipped)` : ''}`, 'success');
          if (branch && item.prevUuid) {
            const id = await forkSession(ctx.sessionId, item.prevUuid);
            insertIntoComposer(id, item.text, true);
          }
        },
      });
    } catch {
      /* the call already showed an error */
    }
  };
  return (
    <div className={`msg-user${queued ? ' queued' : ''}`} lang={langOf(item.text)} id={`msg-${item.uuid}`}>
      <div className="msg-bubble">
        {(item.images.length > 0 || item.files.length > 0) && (
          <div className="msg-attachments">
            {item.images.map((src, k) => (
              <img key={k} src={src} className="msg-thumb" alt="Attached image" role="button" tabIndex={0} aria-label="Show the attached image full size" onClick={() => openLightbox(src)} onKeyDown={onActivate(() => openLightbox(src))} />
            ))}
            {item.files.map((f) => (
              <span key={f} className="msg-file">
                <Paperclip size={12} /> {f}
              </span>
            ))}
          </div>
        )}
        {item.text && <div className="msg-user-text">{renderMentions(item.text)}</div>}
      </div>
      {queued ? (
        <div className="msg-queued">Queued — Claude reads this after the current step</div>
      ) : (
        <div className="msg-actions">
          <CopyButton text={item.text} />
          <button className="icon-btn sm" title="Edit and resend in a new branch of this conversation" aria-label="Edit and resend in a new branch of this conversation" onClick={edit}>
            <PencilLine size={13} />
          </button>
          {item.uuid && !busy && (
            <button className="icon-btn sm" title="Restore files to how they were before this message" aria-label="Restore files to how they were before this message" onClick={rewind}>
              <History size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function renderMentions(text: string) {
  const parts = text.split(/(@[\w./~-]+[\w/-])/g);
  return parts.map((p, i) => (p.startsWith('@') && p.length > 1 ? <span key={i} className="mention">{p}</span> : p));
}

function AssistantText({ item, ctx }: { item: TextItem; ctx: ToolCtx }) {
  const shown = useSmoothText(item.text, Boolean(item.streaming));
  return (
    <div className="msg-assistant" lang={langOf(item.text)} id={`text-${item.id}`}>
      <Markdown text={shown} streaming={Boolean(item.streaming) || shown.length < item.text.length} cwd={ctx.cwd} />
    </div>
  );
}

function Thinking({ item, view }: { item: ThinkingItem; view?: string }) {
  const [openState, setOpen] = useState<boolean | null>(null);
  const open = openState ?? (view === 'thinking' || view === 'verbose');
  const now = useNow(1000, item.streaming);
  const peek = useThrottled(item.text.slice(-320), 160);
  const secs = Math.max(1, Math.round(((item.endTs || now) - item.startTs) / 1000));
  if (!item.text && !item.streaming && !item.redacted) return null;
  return (
    <div className={`thinking${open ? ' open' : ''}${item.streaming ? ' live' : ''}`}>
      <button className="thinking-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="thinking-ico">
          <Sparkles size={13} />
        </span>
        <span className={item.streaming ? 'shimmer' : ''}>{item.streaming ? 'Thinking' : item.redacted ? 'Reasoned privately' : `Thought for ${secs}s`}</span>
        <ChevronRight size={13} className="thinking-chev" />
      </button>
      {item.streaming && !open && item.text && <div className="thinking-text peek">{peek}</div>}
      <Collapse open={open && Boolean(item.text)}>{() => <div className="thinking-text">{item.text}</div>}</Collapse>
    </div>
  );
}

function Notice({ item }: { item: NoticeItem }) {
  const [open, setOpen] = useState(false);
  if (item.tag === 'divider' || item.tag === 'interrupt') {
    return (
      <div className={`divider ${item.tag}`}>
        <span>{item.text}</span>
      </div>
    );
  }
  if (item.tag === 'task') {
    return (
      <div className={`cli-info task ${item.level}`}>
        {item.level === 'warning' ? <CircleAlert size={12} /> : <CircleCheck size={12} />}
        <div className="grow">{item.text}</div>
      </div>
    );
  }
  if (item.tag === 'retry-ok') {
    return (
      <div className="cli-info info">
        <Info size={12} />
        <div className="grow">{item.text}</div>
      </div>
    );
  }
  if (item.tag === 'cli-info') {
    const first = item.text.split(/(?<=\.)\s/)[0];
    return (
      <div className={`cli-info ${item.level}`}>
        <Info size={12} />
        <div className="grow">
          {open ? <Markdown text={item.text} className="md-compact" /> : <span>{first}</span>}
          {item.text.length > first.length && (
            <button className="link" onClick={() => setOpen(!open)}>
              {open ? 'Less' : 'More'}
            </button>
          )}
        </div>
      </div>
    );
  }
  const Icon = item.level === 'error' ? CircleAlert : item.level === 'warning' ? TriangleAlert : Info;
  return (
    <div className={`notice ${item.level}`}>
      <Icon size={14} />
      <div className="grow">
        <div>
          {item.text}
          {item.count && item.count > 1 ? <span className="faint"> ×{item.count}</span> : null}
        </div>
        {item.detail && (
          <>
            {open ? <pre className="notice-detail">{item.detail}</pre> : null}
            <button className="link" onClick={() => setOpen(!open)}>
              {open ? 'Hide details' : 'Details'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Compact({ item }: { item: CompactItem }) {
  return (
    <div className="divider compact">
      <span>
        Conversation compacted{item.trigger === 'manual' ? '' : ' automatically'}
        {item.preTokens ? ` · ${tokens(item.preTokens)} → ${item.postTokens ? tokens(item.postTokens) : 'summary'}` : ''}
      </span>
    </div>
  );
}

function CommandOutput({ item, ctx }: { item: CommandOutputItem; ctx: ToolCtx }) {
  return (
    <div className="cmd-output">
      <div className="cmd-output-head">
        <TermIcon size={12} /> Command output
      </div>
      <Markdown text={item.text} cwd={ctx.cwd} className="md-compact" />
    </div>
  );
}

function ResultFooter({ item, ctx }: { item: ResultItem; ctx: ToolCtx }) {
  if (!item.ok) {
    return (
      <div className="notice error">
        <CircleAlert size={14} />
        <div className="grow">
          <div>{item.subtype === 'error_max_turns' ? 'Stopped: reached the turn limit' : item.subtype === 'error_max_budget_usd' ? 'Stopped: reached the budget limit' : item.text || 'The turn ended with an error'}</div>
          {item.errors?.length ? <pre className="notice-detail">{item.errors.join('\n')}</pre> : null}
        </div>
      </div>
    );
  }
  return (
    <div className="turn-foot" title={`${duration(item.durationMs)}${item.outputTokens ? ` · ${tokens(item.outputTokens)} tokens out` : ''}${item.inputTokens ? ` · ${tokens(item.inputTokens)} in` : ''}${item.costUsd ? ` · ${cost(item.costUsd)}` : ''}`}>
      <span className="turn-mark" aria-hidden="true">✱</span>
      <span className="tnum">{duration(item.durationMs)}</span>
      <span className="tnum turn-detail">
        {item.outputTokens ? `${tokens(item.outputTokens)} tokens` : ''}
        {item.costUsd ? `${item.outputTokens ? ' · ' : ''}${cost(item.costUsd)}` : ''}
      </span>
      {item.chainUuid && (
        <button className="link turn-fork" title="Start a new session that continues from this point" onClick={() => forkSession(ctx.sessionId, item.chainUuid)}>
          <GitFork size={12} /> Fork from here
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- working indicator

// Words from the composing room, since the mark is a pilcrow: what an editor does while a page takes shape.
const VERBS = ['Reading', 'Drafting', 'Composing', 'Outlining', 'Revising', 'Proofing', 'Collating', 'Annotating', 'Indexing', 'Marking up', 'Typesetting', 'Kerning'];

export function WorkingLine({ sessionId, startTs, quiet }: { sessionId: string; startTs: number; quiet: boolean }) {
  const status = useStore((s) => s.rt[sessionId]?.status);
  const compacting = useStore((s) => s.rt[sessionId]?.compacting);
  const thinkingTokens = useStore((s) => s.rt[sessionId]?.thinkingTokens);
  const now = useNow(1000);
  const [verb] = useState(() => VERBS[Math.floor(Math.random() * VERBS.length)]);
  if (!isWorking(status)) return null;
  const elapsed = startTs ? now - startTs : 0;
  if (quiet && !compacting) return null; // the turn's own fold line is breathing already
  return (
    <div className="working">
      <WorkingMark />
      <span className="working-verb shimmer">{compacting ? 'Compacting the conversation' : status === 'starting' ? 'Starting Claude Code' : `${verb}…`}</span>
      <span className="working-meta tnum">
        {elapsed > 1000 ? duration(elapsed) : ''}
        {thinkingTokens ? ` · ${tokens(thinkingTokens)} thinking tokens` : ''}
      </span>
    </div>
  );
}
