import { useState } from 'react';
import { ChevronRight, Download } from 'lucide-react';
import type { ToolItem, Item, TextItem } from '../../lib/transcript';
import { TranscriptModel } from '../../lib/transcript';
import { call } from '../../lib/store';
import { duration, tokens, plural } from '../../lib/format';
import { Markdown } from '../Markdown';
import { ItemList } from '../Transcript';
import { Spinner } from '../ui/bits';
import type { ToolCtx } from './ToolCard';
import { str } from '../../lib/tools';
import type { RawMsg, ContentBlock } from '../../lib/types';

export function AgentBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const i = item.input || {};
  const s = item.structured && typeof item.structured === 'object' ? item.structured : null;
  const [showPrompt, setShowPrompt] = useState(false);
  const [loaded, setLoaded] = useState<TranscriptModel | null>(null);
  const [loading, setLoading] = useState(false);
  const agentId: string | undefined = s?.agentId || item.taskId;
  // Usage comes in two spellings depending on where it was read from (live task events vs the transcript).
  const usage = (item.task?.usage || s?.usage) as { tool_uses?: number; toolUses?: number; total_tokens?: number; totalTokens?: number; duration_ms?: number } | undefined;
  const running = item.status === 'running' || item.status === 'background' || item.status === 'streaming';
  const report: string = item.task?.summary && item.status !== 'running' ? '' : s && !s.isAsync ? textOf(s.content) || item.result?.text || '' : !s?.isAsync ? item.result?.text || '' : '';
  const children = item.children.length ? item.children : loaded?.items || [];

  const loadTranscript = async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const msgs = await call<RawMsg[]>('session.subagent', { id: ctx.sessionId, agentId });
      const m = new TranscriptModel();
      m.pushAll(msgs);
      setLoaded(m);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="agent">
      <div className="agent-meta">
        {str(i, 'subagent_type') && <span className="badge">{str(i, 'subagent_type')}</span>}
        {str(i, 'model') && <span className="badge">{str(i, 'model')}</span>}
        {str(i, 'isolation') === 'worktree' && <span className="badge">worktree</span>}
        {usage && (
          <span className="faint">
            {plural(usage.tool_uses ?? usage.toolUses ?? 0, 'tool use')} · {tokens(usage.total_tokens ?? usage.totalTokens ?? 0)} tokens{usage.duration_ms ? ` · ${duration(usage.duration_ms)}` : ''}
          </span>
        )}
        {running && (
          <span className="faint row">
            <Spinner size={12} /> {item.status === 'background' ? 'Working in the background' : 'Working'}
          </span>
        )}
      </div>
      <button className="agent-prompt-toggle" onClick={() => setShowPrompt(!showPrompt)}>
        <ChevronRight size={13} className={showPrompt ? 'rot' : ''} /> Instructions
      </button>
      {showPrompt && <div className="agent-prompt">{str(i, 'prompt')}</div>}
      {children.length > 0 && (
        <div className="agent-children">
          <ItemList items={children} ctx={{ ...ctx, depth: ctx.depth + 1 }} rev={item.rev} running={running} flat />
        </div>
      )}
      {!children.length && !running && agentId && !loaded && (
        <button className="btn sm ghost" onClick={loadTranscript} disabled={loading}>
          {loading ? <Spinner size={12} /> : <Download size={12} />} Show the agent's steps
        </button>
      )}
      {item.task?.summary && !childrenEndWith(children, item.task.summary) && (
        <div className="agent-report">
          <Markdown text={item.task.summary} cwd={ctx.cwd} className="md-compact" />
        </div>
      )}
      {report && !childrenEndWith(children, report) && (
        <div className="agent-report">
          <Markdown text={report} cwd={ctx.cwd} className="md-compact" />
        </div>
      )}
    </div>
  );
}

function childrenEndWith(children: Item[], text: string) {
  const last = [...children].reverse().find((c): c is TextItem => c.kind === 'text');
  return Boolean(last && last.text.trim() === text.trim());
}

function textOf(content: unknown): string {
  if (!content) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return (content as ContentBlock[]).filter((b) => b.type === 'text').map((b) => b.text || '').join('\n');
  return '';
}
