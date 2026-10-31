import { useState } from 'react';
import type { ToolItem } from '../../lib/transcript';
import type { ToolCtx } from './ToolCard';
import type { HelperState } from '../../lib/types';
import { openSession } from '../../lib/store';
import { str } from '../../lib/tools';
import { Collapse } from '../ui/Collapse';
import { ChevronRight } from 'lucide-react';

/**
 * The lead's tools (HELPERS.md §6.1): a start card with its brief folded and the helper's row; a wait or
 * list card with a row per helper; a read card with the text folded; told and stopped.
 */
const TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);

function resultOf(item: ToolItem): Record<string, unknown> | null {
  const s = item.structured;
  if (s && typeof s === 'object' && !Array.isArray(s)) return s as Record<string, unknown>;
  const r = item.result;
  if (r && r.text && !r.isError) {
    try {
      const v = JSON.parse(r.text);
      return v && typeof v === 'object' ? (v as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
}

function HelperRow({ h }: { h: Partial<HelperState> }) {
  const line = h.lastLine || h.reason || '';
  return (
    <div className="hb-row">
      <span className={`dp-glyph ${h.state || ''}`} aria-hidden="true" />
      <span className={`dp-word ${h.state || ''}`}>{(h.state || '').replace('_', ' ')}</span>
      <span className="hb-name">{h.name}</span>
      <span className="hb-last ellipsis faint">{line}</span>
      {h.id && (
        <button className="btn xs ghost" onClick={() => openSession(h.id!)}>
          Open
        </button>
      )}
    </div>
  );
}

function Fold({ label, open: initial, children }: { label: string; open: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="hb-fold">
      <button className="tool-toggle" onClick={() => setOpen(!open)}>
        <ChevronRight size={12} className={open ? 'rot' : ''} /> {label}
      </button>
      <Collapse open={open}>{() => <div className="hb-fold-body">{children}</div>}</Collapse>
    </div>
  );
}

export function HelpersBody({ item }: { item: ToolItem; ctx: ToolCtx }) {
  const tool = item.name.slice('mcp__helpers__'.length);
  const i = item.input || {};
  const r = item.result;
  if (r?.isError) return <div className="tool-section tool-error-text">{r.text}</div>;
  const data = resultOf(item);
  if (tool === 'start') {
    const wt = data?.worktree && typeof data.worktree === 'object' ? (data.worktree as { branch?: string }) : null;
    const queued = Array.isArray(data?.queuedBehind) ? (data!.queuedBehind as string[]) : [];
    return (
      <div className="tool-section hb">
        {str(i, 'brief') && (
          <Fold label="Brief" open={false}>
            <div className="tool-note">{str(i, 'brief')}</div>
          </Fold>
        )}
        {data ? (
          <>
            {(wt?.branch || queued.length > 0) && (
              <div className="hb-meta">
                {wt?.branch && <span className="dp-chip plan">{wt.branch}</span>}
                {queued.length > 0 && <span className="faint small">waits for {queued.join(', ')}</span>}
              </div>
            )}
            <HelperRow h={data as Partial<HelperState>} />
          </>
        ) : !r ? (
          <div className="faint">Starting…</div>
        ) : null}
      </div>
    );
  }
  if (tool === 'wait' || tool === 'list') {
    const hs = Array.isArray(data?.helpers) ? (data!.helpers as Partial<HelperState>[]) : [];
    return (
      <div className="tool-section hb">
        {data?.timedOut ? <div className="faint small">Timed out; still going: {hs.filter((h) => !TERMINAL.has(h.state || '')).map((h) => h.name).join(', ') || 'none'}</div> : null}
        {hs.map((h, k) => (
          <HelperRow key={h.id || k} h={h} />
        ))}
        {!data && !r ? <div className="faint">{tool === 'wait' ? 'Waiting…' : 'Running…'}</div> : data && !hs.length ? <div className="faint small">No helpers.</div> : null}
      </div>
    );
  }
  if (tool === 'read') {
    const text = String(data?.text || r?.text || '');
    return (
      <div className="tool-section hb">
        {text ? (
          <Fold label={`${str(i, 'what') || 'summary'} of ${str(i, 'name')}`} open={text.length <= 1200}>
            <pre className="tool-pre">{text}</pre>
          </Fold>
        ) : !r ? (
          <div className="faint">Reading…</div>
        ) : null}
        {data?.truncated ? <div className="faint small">Cut at the budget{data.more ? ` · ${String(data.more)}` : ''}</div> : null}
      </div>
    );
  }
  if (tool === 'send') {
    return (
      <div className="tool-section hb">
        {str(i, 'text') && <div className="tool-note">{str(i, 'text')}</div>}
        {data ? <div className="faint small">delivered {String(data.delivered || 'now')}</div> : null}
      </div>
    );
  }
  if (tool === 'stop') {
    return <div className="tool-section hb">{data ? <HelperRow h={data as Partial<HelperState>} /> : !r ? <div className="faint">Stopping…</div> : null}</div>;
  }
  return <div className="tool-section hb">{r ? <pre className="tool-pre">{r.text}</pre> : <div className="faint">Running…</div>}</div>;
}
