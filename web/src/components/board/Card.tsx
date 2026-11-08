import { memo } from 'react';
import type { SessionSummary } from '../../lib/types';
import type { Runtime } from '../../lib/store';
import { cardState, cardLine, isSchedule } from '../../lib/board';
import { basename, cost, duration, shortModel } from '../../lib/format';
import { WorkingMark } from '../ui/brand';

const CHIP: Record<string, string> = { review: 'To review', prompt: 'Asking', running: 'Working', queued: 'Queued', stuck: 'Stopped', wait: 'Waiting', done: 'Done', backlog: '' };

/**
 * One session as a card: id line with a state chip, the title, one line on what it is waiting for
 * or doing, and a foot with who is on it and what it has cost. The stripe on the left is the only
 * colour: accent for a review, amber for a prompt, blue while working, red when stopped.
 */
export const Card = memo(function Card({ s, rt, selected, now, titles, onSelect, onOpen }: { s: SessionSummary; rt?: Runtime; selected: boolean; now: number; titles?: Record<string, string>; onSelect: () => void; onOpen: () => void }) {
  const state = cardState(s, rt, now);
  if (!state) return null;
  const schedule = isSchedule(s.id);
  const line = cardLine(s, state, rt, titles);
  // Who is on it: the model once Claude has run (the live state knows it), otherwise you.
  const model = rt?.activeModel ? shortModel(rt.activeModel) : '';
  const elapsed = state === 'running' && rt?.turnStartedAt ? now - rt.turnStartedAt : 0;
  const spent = rt?.usage?.costUsd || s.costUsd || 0;
  const files = s.report?.files;
  const add = files?.reduce((a, f) => a + f.add, 0) || 0;
  const del = files?.reduce((a, f) => a + f.del, 0) || 0;
  return (
    <article className={`bd-card ${state}${s.conflict ? ' note' : ''}${selected ? ' sel' : ''}`} data-card={s.id} data-state={state} draggable={!schedule && !s.conflict} role="button" tabIndex={0} onClick={onSelect} onDoubleClick={onOpen} onKeyDown={(e) => e.key === ' ' && (e.preventDefault(), onSelect())}>
      <span className="bd-stripe" aria-hidden="true" />
      <div className="bd-card-top">
        <span className="ellipsis">
          {rt?.helper || s.helper ? `helper of ${rt?.helper?.leadTitle || 'a lead session'} · ` : ''}
          {basename(s.project || s.cwd)}
          {s.worktree ? ` · ${s.worktree.branch.replace(/^pilcrow\//, '')}` : ''}
        </span>
        {schedule ? <span className="bd-chip wait">Scheduled</span> : s.conflict ? <span className="bd-chip prompt">Memory</span> : CHIP[state] ? <span className={`bd-chip ${state}`}>{CHIP[state]}</span> : null}
      </div>
      <div className="bd-card-title">{s.title}</div>
      {line.text && <div className={`bd-card-line${line.tone ? ` ${line.tone}` : ''}`}>{line.text}</div>}
      <div className="bd-card-foot tnum">
        {model ? (
          <span className="bd-who" title={model}>
            <span className="bd-av">{model[0]?.toUpperCase()}</span>
            <span className="ellipsis">{model}</span>
          </span>
        ) : (
          <span className="bd-who">
            <span className="bd-av me">you</span>
          </span>
        )}
        <span className="grow" />
        {state === 'running' && (
          <>
            <WorkingMark size={13} />
            {elapsed > 1000 && <span>{duration(elapsed)}</span>}
          </>
        )}
        {state === 'review' && files && files.length > 0 && (
          <span className="bd-delta">
            <span className="add">+{add}</span> <span className="del">−{del}</span>
          </span>
        )}
        {spent > 0 && <span>{cost(spent)}</span>}
      </div>
    </article>
  );
});
