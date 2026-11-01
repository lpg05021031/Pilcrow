import { useMemo, useState, type ReactNode } from 'react';
import { ArrowUpRight, ChevronRight, CornerDownRight } from 'lucide-react';
import type { ToolItem, UserItem } from '../../lib/transcript';
import type { ToolCtx } from './ToolCard';
import { TERMINAL, type HelperState } from '../../lib/types';
import { openSession, useStore } from '../../lib/store';
import { str } from '../../lib/tools';
import { parseNotes } from '../../lib/labels';
import { Collapse } from '../ui/Collapse';
import { Markdown } from '../Markdown';

/**
 * The lead's tools (HELPERS.md §6.1): a start card with its brief folded and the helper's row; a wait or
 * list card with a row per helper; a read card with the text folded; told and stopped.
 */

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
      <span className="hb-name">{h.name}</span>
      <span className={`dp-word ${h.state || ''}`}>{h.state === 'needs_action' ? 'needs you' : (h.state || '').replace('_', ' ')}</span>
      <span className="hb-last ellipsis faint">{line}</span>
      {h.id && (
        <button className="btn xs ghost" onClick={() => h.id && openSession(h.id)}>
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
    const queued = Array.isArray(data?.queuedBehind) ? (data.queuedBehind as string[]) : [];
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
    const hs = Array.isArray(data?.helpers) ? (data.helpers as Partial<HelperState>[]) : [];
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

// ---------------------------------------------------------------- a helper's note to its lead (HELPERS.md §5.4)

const HELPER_VERB: Record<string, string> = { done: 'Helper finished', failed: 'Helper failed', stopped: 'Helper stopped', needs_action: 'Helper needs you', skipped: 'Helper skipped' };
const HELPER_HEADER_PREFIX = 'You are a helper started by another Pilcrow session';

/** Words another session wrote, drawn as a tool card: a glyph in the status column, a verb, a target, figures
 *  at the right, Open on hover, and a body that folds under the head (HELPERS.md §6.1). */
function SessionCard({ id, glyph, verb, target, meta, openTip, onOpen, children }: { id?: string; glyph: ReactNode; verb: string; target: string; meta?: ReactNode; openTip: string; onOpen?: () => void; children?: ReactNode }) {
  const [open, setOpen] = useState(true);
  const foldable = Boolean(children);
  return (
    <div className={`tool session-card${open && foldable ? ' open' : ''}`} id={id}>
      {/* The head folds the body wherever it is clicked; its button is what the keyboard reaches (ToolCard). */}
      <div className="tool-head" onClick={() => foldable && setOpen(!open)}>
        <span className="tool-ico">{glyph}</span>
        <span className="tool-line">
          <button className="tool-toggle" aria-expanded={foldable ? open : undefined} disabled={!foldable}>
            <span className="tool-verb">{verb}</span>
            <span className="tool-target">{target}</span>
            {foldable && <ChevronRight size={13} className="tool-chev" />}
          </button>
          {onOpen && (
            <button
              className="icon-btn sm tool-open"
              title={openTip} aria-label={openTip}
              onClick={(e) => {
                e.stopPropagation();
                onOpen();
              }}
            >
              <ArrowUpRight size={13} />
            </button>
          )}
        </span>
        <span className="tool-meta">{meta}</span>
      </div>
      {foldable && <Collapse open={open}>{() => <div className="tool-body">{children}</div>}</Collapse>}
    </div>
  );
}

/** A note Pilcrow wrote to a lead: one card per helper it speaks of; the text around the cards is for the model. */
export function HelperNote({ item, ctx }: { item: UserItem; ctx: ToolCtx }) {
  const helpers = useStore((s) => s.rt[ctx.sessionId]?.helpers);
  const { parts } = useMemo(() => parseNotes(item.text), [item.text]);
  const list = parts.length ? parts : [{ name: 'note', state: '', body: item.text }];
  return (
    <div className="msg-notes" id={`msg-${item.uuid}`}>
      {list.map((p, k) => {
        const h = helpers?.find((x) => x.name === p.name);
        const files = p.files && p.files !== '0';
        return (
          <SessionCard
            key={k}
            glyph={<span className={`dp-glyph ${p.state || 'stopped'}`} aria-hidden="true" />}
            verb={HELPER_VERB[p.state] || 'Helper'}
            target={p.name}
            openTip="Open the helper's session"
            onOpen={h ? () => openSession(h.id) : undefined}
            meta={
              <>
                {p.elapsed && <span className="tnum">{p.elapsed}</span>}
                {p.cost && <span className="tnum">{p.cost}</span>}
                {files && (
                  <span className="tool-stats tnum">
                    {p.add && p.add !== '0' && <span className="num-add">+{p.add}</span>}
                    {p.del && p.del !== '0' && <span className="num-del">−{p.del}</span>}
                  </span>
                )}
              </>
            }
          >
            {p.body ? (
              <div className="tool-well">
                <Markdown text={p.body} cwd={ctx.cwd} className="md-compact" />
              </div>
            ) : null}
          </SessionCard>
        );
      })}
    </div>
  );
}

/** A brief or a message the lead sent to this helper (`_from: lead`): not the person speaking, so a card
 *  rather than a bubble; the standing instructions a brief opens with fold under it. */
export function FromLead({ item, ctx }: { item: UserItem; ctx: ToolCtx }) {
  const lead = useStore((s) => s.rt[ctx.sessionId]?.helper);
  const [showHeader, setShowHeader] = useState(false);
  let header = '';
  let brief = item.text;
  if (item.text.startsWith(HELPER_HEADER_PREFIX)) {
    const i = item.text.indexOf('\n\n');
    header = i < 0 ? item.text : item.text.slice(0, i);
    brief = i < 0 ? '' : item.text.slice(i + 2).trim();
  }
  return (
    <SessionCard id={`msg-${item.uuid}`} glyph={<CornerDownRight size={14} />} verb="From the lead" target={lead?.leadTitle || 'the lead session'} openTip="Open the lead session" onOpen={lead ? () => openSession(lead.leadId) : undefined}>
      {brief && (
        <div className="tool-well">
          <Markdown text={brief} cwd={ctx.cwd} className="md-compact" />
        </div>
      )}
      {header && (
        <div className="hb-fold">
          <button className="tool-toggle" onClick={() => setShowHeader(!showHeader)}>
            <ChevronRight size={12} className={showHeader ? 'rot' : ''} /> Standing instructions for a helper
          </button>
          <Collapse open={showHeader}>{() => <div className="tool-note">{header}</div>}</Collapse>
        </div>
      )}
    </SessionCard>
  );
}
