// What a step returned, as its card shows it: the output, or the error read in two steps.
import { useState, type ReactNode } from 'react';
import { Collapse } from '../ui/Collapse';
import { ChevronRight } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import { truncate } from '../../lib/format';
import { setDialog, call, toast, useStore, openLightbox } from '../../lib/store';
import { Spinner } from '../ui/bits';
import type { ToolInput } from '../../lib/types';
import type { ToolCtx } from './ToolCard';

/** Internal failures that have a plain explanation; the raw text stays in the tooltip. */
function plainError(text: string) {
  if (/Tool permission (request failed|stream closed)/i.test(text)) return 'The approval request was cut off when Claude Code restarted, so this step did not run. Ask Claude to try again.';
  return null;
}

/** Claude Code wraps tool failures in <tool_use_error> tags meant for the model; people only need the message. */
export function unwrapError(text: string) {
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

export function ResultText({ item, label, input, ctx }: { item: ToolItem; label?: boolean; input?: ToolInput; ctx?: ToolCtx }) {
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
