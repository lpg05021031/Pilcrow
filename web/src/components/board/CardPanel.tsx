import { useEffect, useRef, useState } from 'react';
import { X, CircleCheck, ArrowUpRight, Pencil, CalendarClock } from 'lucide-react';
import type { PermissionDecision, SessionSummary, WaitCondition } from '../../lib/types';
import { respond, setIntent, decideCard, call, type Runtime } from '../../lib/store';
import { actionsOf, ledgerOf, permSummary, describeWait, isSchedule, quickWaits, type CardState, type Lane } from '../../lib/board';
import { basename, cost, timeAgo } from '../../lib/format';
import { suggestionLabel } from '../tools/Permission';

export type PanelMode = 'reject' | 'send' | 'wait' | null;

const WHERE: Record<CardState, string> = { review: 'With you · to review', prompt: 'With you · Claude is asking', running: 'With Claude', queued: 'With Claude · queued for a slot', stuck: 'With Claude · stopped, needs something from you', wait: 'Waiting on a condition', backlog: 'Not started', done: 'Done' };

/**
 * The chosen card in full: its intent, what Claude is asking or why it stopped, the handoff report
 * when there is one, the ledger of everything that happened to it, and at the bottom the actions
 * its state allows. Approving, sending back and setting a wait happen here without opening the
 * conversation. A schedule's card shows what it will run and when; its actions open the schedule.
 */
export function CardPanel({ s, rt, state, mode, setMode, caps, cards, titles, onAct, onClose, now }: { s: SessionSummary; rt?: Runtime; state: CardState; mode: PanelMode; setMode: (m: PanelMode) => void; caps: { ci: boolean }; cards: Record<Lane, SessionSummary[]>; titles: Record<string, string>; onAct: (a: string, payload?: WaitCondition | null) => void; onClose: () => void; now: number }) {
  const schedule = isSchedule(s.id);
  const conflict = s.conflict;
  const virtual = schedule || Boolean(conflict);
  const actions = actionsOf(s, state, rt);
  const perm = state === 'prompt' && !conflict ? rt?.pending?.[0] : undefined;
  const ledger = virtual ? [] : ledgerOf(s, rt);
  const [editing, setEditing] = useState(false);
  useEffect(() => setEditing(false), [s.id]);
  return (
    <aside className="bd-panel">
      <div className="bd-panel-h">
        <div className="bd-panel-meta">
          <span className="ellipsis">{basename(s.project || s.cwd)}{s.worktree ? ` · ${s.worktree.branch}` : s.gitBranch ? ` · ${s.gitBranch}` : ''}</span>
          {!virtual && (
            <button className="icon-btn sm" title="Open the conversation" aria-label="Open the conversation" onClick={() => onAct('open')}>
              <ArrowUpRight size={14} />
            </button>
          )}
          <button className="icon-btn sm" title="Close (Esc)" aria-label="Close" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <h3 className="bd-panel-title">{s.title}</h3>
        <div className="bd-panel-where">{schedule ? 'A schedule · runs on its own' : conflict ? 'With you · project memory' : WHERE[state]}</div>
      </div>

      <div className="bd-panel-body">
        <section className="bd-sec">
          <h4>
            {schedule ? 'What it runs' : conflict ? 'The note' : 'Intent'}
            {!virtual && !editing && (
              <button className="icon-btn sm" title="Edit the intent" aria-label="Edit the intent" onClick={() => setEditing(true)}>
                <Pencil size={12} />
              </button>
            )}
          </h4>
          {editing ? <IntentEditor s={s} done={() => setEditing(false)} /> : s.intent ? <p className="bd-intent">{s.intent}</p> : <p className="bd-hint">No intent yet. Two or three sentences on the goal and when it is done; a card with one can be handed to Claude from here.</p>}
        </section>

        {conflict && (
          <>
            <section className="bd-sec">
              <h4>Your correction{conflict.sessionTitle ? ` · on ${conflict.sessionTitle}` : ''}</h4>
              <p className="bd-intent">{conflict.correction || 'You sent the work back.'}</p>
            </section>
            {conflict.replacement && (
              <section className="bd-sec">
                <h4>Written instead</h4>
                <p className="bd-intent">{conflict.replacement.text}</p>
              </section>
            )}
            <section className="bd-sec">
              <p className="bd-hint">Forgetting removes the note from this folder's memory. Keeping it clears the flag; new sessions get it again, with the correction counted against it.</p>
            </section>
          </>
        )}
        {(state === 'wait' || schedule) && s.wait && (
          <section className="bd-sec">
            <h4>{schedule ? 'Next run' : 'Waiting for'}</h4>
            <p className="bd-intent">
              <CalendarClock size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} />
              {describeWait(s.wait, titles)}
              {s.wait.kind === 'time' && <span className="faint"> · {timeAgo(s.wait.at, now)}</span>}
            </p>
            {s.wait.prompt && <p className="bd-hint">Then: {s.wait.prompt}</p>}
          </section>
        )}
        {state === 'queued' && s.queued && (
          <section className="bd-sec">
            <h4>Queued</h4>
            <p className="bd-intent">Claude is working on as many cards as the limit allows. This one starts when a slot frees, with:</p>
            <p className="bd-hint">{s.queued.text}</p>
          </section>
        )}

        {perm && (
          <section className="bd-sec">
            <h4>Claude is asking</h4>
            <Permission s={s} perm={perm} now={now} onOpen={() => onAct('open')} />
          </section>
        )}
        {state === 'stuck' && rt?.error && (
          <section className="bd-sec">
            <h4>Why it stopped</h4>
            <p className="bd-intent err">{rt.error}</p>
          </section>
        )}
        {state === 'running' && rt?.activity && (
          <section className="bd-sec">
            <h4>Right now</h4>
            <p className="bd-intent">{rt.activity.kind === 'tool' ? `${rt.activity.tool} · ${rt.activity.label}` : rt.activity.kind === 'text' ? rt.activity.label : 'Thinking…'}</p>
          </section>
        )}
        {s.report && (
          <section className="bd-sec">
            <h4>Handoff</h4>
            <div className="bd-report">
              <dl>
                <dt>Asked</dt>
                <dd>{s.report.asked}</dd>
                <dt>Did</dt>
                <dd>{s.report.did}</dd>
                {s.report.files.length > 0 && (
                  <>
                    <dt>Changed</dt>
                    <dd className="mono">
                      {s.report.files.map((f) => (
                        <div key={f.path}>
                          {f.path} <span className="add">+{f.add}</span> <span className="del">−{f.del}</span>
                        </div>
                      ))}
                    </dd>
                  </>
                )}
                <dt>Unverified</dt>
                <dd className="warn">{s.report.unverified || 'nothing'}</dd>
                <dt>Decide</dt>
                <dd className="ask">{s.report.ask || 'none'}</dd>
              </dl>
            </div>
          </section>
        )}
        {!s.report && state === 'review' && rt?.lastReply?.text && (
          <section className="bd-sec">
            <h4>Last reply</h4>
            <p className="bd-intent">{rt.lastReply.text}</p>
          </section>
        )}

        {ledger.length > 0 && (
          <section className="bd-sec">
            <h4>Ledger</h4>
            <div className="bd-ledger">
              {ledger.map((e, i) => (
                <div key={i} className={`bd-ev${e.tone ? ` tone-${e.tone}` : ''}`}>
                  <b>{e.label}</b>
                  <span className="bd-ev-when tnum">{timeAgo(e.at, now)}</span>
                  {e.note && <span className="bd-ev-note">{e.note}</span>}
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      <div className="bd-actions">
        {mode === 'wait' ? (
          <WaitPicker s={s} caps={caps} cards={cards} now={now} onPick={(w) => onAct('set-wait', w)} onCancel={() => setMode(null)} />
        ) : mode ? (
          <NoteBox s={s} kind={mode === 'reject' ? 'reject' : 'answer'} placeholder={mode === 'reject' ? 'What should change? It goes to Claude as the next message, in this same conversation.' : 'What does it need? Your message continues the conversation.'} onDone={() => setMode(null)} />
        ) : (
          actions.map((a) => (
            <button key={a.id} className={`btn sm${a.primary ? ' primary' : a.danger ? ' danger' : ''}`} onClick={() => onAct(a.id)}>
              {a.label}
              <span className="key">{a.key}</span>
            </button>
          ))
        )}
        {state === 'review' && !mode && rt?.usage && rt.usage.costUsd > 0 && <span className="bd-actions-cost tnum faint">{cost(rt.usage.costUsd)} so far</span>}
      </div>
    </aside>
  );
}

/** The conditions a card can wait on: CI on its branch, a time, or another card being approved. */
function WaitPicker({ s, caps, cards, now, onPick, onCancel }: { s: SessionSummary; caps: { ci: boolean }; cards: Record<Lane, SessionSummary[]>; now: number; onPick: (w: WaitCondition) => void; onCancel: () => void }) {
  const quick = quickWaits(now);
  const [at, setAt] = useState('');
  const [other, setOther] = useState('');
  const others = [...cards.backlog, ...cards.agent, ...cards.wait, ...cards.me].filter((c) => c.id !== s.id && !isSchedule(c.id));
  const branch = s.worktree?.branch || s.gitBranch || '';
  const local = (ms: number) => {
    const d = new Date(ms);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  return (
    <div className="bd-waitpick">
      <div className="bd-hint">Claude is woken with the intent when the condition is met; until then the card sits in Waiting.</div>
      {quick.map((q) => {
        const ci = q.wait === 'ci';
        const disabled = ci && !caps.ci;
        return (
          <button key={q.key} data-k={q.key} className="bd-waitopt" disabled={disabled} title={disabled ? 'Needs the GitHub CLI (gh) on this machine' : undefined} onClick={() => onPick(ci ? { kind: 'ci', cwd: s.cwd, branch, since: now } : (q.wait as WaitCondition))}>
            <span className="key">{q.key}</span>
            <span>
              {q.label}
              {ci && branch ? <small> · {branch}</small> : null}
            </span>
            <small>{disabled ? 'no gh' : q.hint}</small>
          </button>
        );
      })}
      <div className="bd-waitrow">
        <input id="bd-wait-at" type="datetime-local" className="input" value={at} min={local(now + 60e3)} onChange={(e) => setAt(e.target.value)} />
        <button className="btn sm" disabled={!at || new Date(at).getTime() <= now} onClick={() => onPick({ kind: 'time', at: new Date(at).getTime() })}>
          Until then
        </button>
      </div>
      {others.length > 0 && (
        <div className="bd-waitrow">
          <select id="bd-wait-card" className="input" value={other} onChange={(e) => setOther(e.target.value)}>
            <option value="">Until another card is approved…</option>
            {others.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
          <button className="btn sm" disabled={!other} onClick={() => onPick({ kind: 'session', id: other })}>
            Wait for it
          </button>
        </div>
      )}
      <div className="bd-note-row">
        <button className="btn sm ghost" onClick={onCancel}>
          Cancel<span className="key">Esc</span>
        </button>
      </div>
    </div>
  );
}

function IntentEditor({ s, done }: { s: SessionSummary; done: () => void }) {
  const [text, setText] = useState(s.intent || '');
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const save = async () => {
    setBusy(true);
    try {
      await setIntent(s.id, text);
      done();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bd-note">
      <textarea ref={ref} className="textarea" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="What is the work, and when is it done?" onKeyDown={(e) => ((e.ctrlKey || e.metaKey) && e.key === 'Enter' ? save() : e.key === 'Escape' ? done() : null)} />
      <div className="bd-note-row">
        <button className="btn sm primary" onClick={save} disabled={busy}>
          Save
        </button>
        <button className="btn sm ghost" onClick={done}>
          Cancel
        </button>
        <span className="bd-hint">Ctrl+Enter</span>
      </div>
    </div>
  );
}

function NoteBox({ s, kind, placeholder, onDone }: { s: SessionSummary; kind: 'reject' | 'answer'; placeholder: string; onDone: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await decideCard(s.id, kind, text.trim());
      onDone();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bd-note">
      <textarea ref={ref} className="textarea" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} onKeyDown={(e) => ((e.ctrlKey || e.metaKey) && e.key === 'Enter' ? send() : e.key === 'Escape' ? onDone() : null)} />
      <div className="bd-note-row">
        <button className="btn sm primary" onClick={send} disabled={busy || !text.trim()}>
          {kind === 'reject' ? 'Send back' : 'Send'}
        </button>
        <button className="btn sm ghost" onClick={onDone}>
          Cancel
        </button>
        <span className="bd-hint">Ctrl+Enter</span>
      </div>
    </div>
  );
}

/** The permission waiting on this card, answerable in place; questions and plans open the conversation. */
function Permission({ s, perm, now, onOpen }: { s: SessionSummary; perm: NonNullable<Runtime['pending']>[number]; now: number; onOpen: () => void }) {
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState<'allow' | 'deny' | null>(null);
  const allowAlways = !perm.suppressAlwaysAllowRule && perm.suggestions.length > 0;
  const decide = async (decision: PermissionDecision) => {
    if (busy) return;
    setBusy(true);
    try {
      await respond(s.id, perm.requestId, decision);
      setDecided(decision.behavior === 'allow' ? 'allow' : 'deny');
      setTimeout(() => (setDecided(null), setBusy(false)), 900);
    } catch {
      setBusy(false);
    }
  };
  const allow = () => decide({ behavior: 'allow', decisionClassification: 'user_temporary' });
  const always = () => decide({ behavior: 'allow', updatedPermissions: perm.suggestions, decisionClassification: 'user_permanent' });
  const deny = () => decide({ behavior: 'deny', message: 'The user declined this action.', decisionClassification: 'user_reject' });
  // a helper's request can be answered for every helper of its lead at once (HELPERS.md §5.6)
  const helperOf = s.helper || null;
  const allowAllHelpers = async () => {
    if (busy || !helperOf) return;
    setBusy(true);
    try {
      await call('helpers.allow', { leadId: helperOf.leadId, requestId: perm.requestId });
      setDecided('allow');
      setTimeout(() => (setDecided(null), setBusy(false)), 900);
    } catch {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (perm.interactive) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '1' && !perm.defaultToNo) allow();
      else if (e.key === '2' && allowAlways) always();
      else if (e.key === (allowAlways ? '3' : '2')) deny();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  const what = permSummary(perm, s.cwd);
  return (
    <div className="bd-perm">
      <div className="bd-perm-well">
        <span className="bd-perm-tool">{perm.displayName || perm.toolName}</span>
        <span className={`bd-perm-what${what.mono ? ' mono' : ''}`}>{what.text}</span>
      </div>
      {what.note && <div className="bd-hint">{what.note}</div>}
      <div className="bd-perm-wait faint tnum">waiting {Math.max(0, Math.round((now - perm.at) / 1000))}s</div>
      {decided ? (
        <div className="bd-perm-row ok">
          <CircleCheck size={14} /> {decided === 'allow' ? 'Allowed — Claude is continuing' : 'Declined'}
        </div>
      ) : perm.interactive ? (
        <div className="bd-perm-row">
          <button className="btn sm primary" onClick={onOpen}>
            {perm.toolName === 'AskUserQuestion' ? 'Answer the question' : perm.toolName === 'ExitPlanMode' ? 'Review the plan' : 'Open to respond'}
          </button>
        </div>
      ) : (
        <div className="bd-perm-row">
          <button className={`btn sm ${perm.defaultToNo ? '' : 'primary'}`} onClick={allow} disabled={busy}>
            Allow{!perm.defaultToNo && <span className="key">1</span>}
          </button>
          {allowAlways && (
            <button className="btn sm" onClick={always} disabled={busy} title={suggestionLabel(perm.suggestions[0], s.cwd)}>
              Always allow<span className="key">2</span>
            </button>
          )}
          {helperOf && (
            <button className="btn sm" onClick={allowAllHelpers} disabled={busy} title="Answer every helper of this lead that asks the same thing, now and later">
              Allow for all helpers
            </button>
          )}
          <button className={`btn sm ${perm.defaultToNo ? 'primary' : 'ghost'}`} onClick={deny} disabled={busy}>
            Deny<span className="key">{allowAlways ? 3 : 2}</span>
          </button>
        </div>
      )}
    </div>
  );
}
