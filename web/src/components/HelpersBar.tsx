import { Eye } from 'lucide-react';
import { useStore, openSession, setUi } from '../lib/store';
import { cost, duration } from '../lib/format';
import type { HelperState } from '../lib/types';

/**
 * Under a lead's header (HELPERS.md §6.1): one chip per helper — a dot in its state's colour, its name,
 * a short state — the rolled-up cost, and Review once something is done. Drawn from `session.state`
 * alone; it is the whole picture while the cards scroll away.
 */
export function chipText(h: HelperState) {
  const word = h.state === 'needs_action' ? 'needs you' : h.state.replace('_', ' ');
  let extra = '';
  if (h.state === 'running') extra = duration(h.elapsedMs || 0);
  else if (h.state === 'done' && h.files > 0) extra = `+${h.add} −${h.del}`;
  else if (['queued', 'skipped', 'stopped', 'failed'].includes(h.state) && h.reason) extra = h.reason;
  return `${h.name} · ${word}${extra ? ` · ${extra}` : ''}`;
}

export function HelpersBar({ sessionId }: { sessionId: string }) {
  const helpers = useStore((s) => s.rt[sessionId]?.helpers);
  const lead = useStore((s) => s.rt[sessionId]?.lead);
  if (!helpers?.length) return null;
  const review = helpers.some((h) => (h.state === 'done' || h.state === 'failed' || h.state === 'stopped') && h.files > 0);
  return (
    <div className="helpers-bar" role="toolbar" aria-label="Helpers">
      <div className="helpers-chips">
        {helpers.map((h) => (
          <button key={h.id} className={`helper-chip${h.state === 'needs_action' ? ' warm' : ''}`} title={[h.lastLine, h.cwd].filter(Boolean).join(' · ')} onClick={() => openSession(h.id)}>
            <span className={`dp-glyph ${h.state}`} aria-hidden="true" />
            <span className="ellipsis">{chipText(h)}</span>
          </button>
        ))}
      </div>
      <span className="grow" />
      {lead?.cost ? <span className="faint small tnum">{cost(lead.cost)}</span> : null}
      {review && (
        <button className="btn xs ghost" onClick={() => setUi({ panel: true, panelTab: 'changes' })} title="The helpers' changes together, in the Changes panel">
          <Eye size={12} /> Review
        </button>
      )}
    </div>
  );
}
