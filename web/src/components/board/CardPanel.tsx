import { useEffect, useRef, useState } from 'react';
import { X, CircleCheck, ArrowUpRight, Pencil } from 'lucide-react';
import type { PermissionDecision, SessionSummary } from '../../lib/types';
import { respond, setIntent, decideCard, type Runtime } from '../../lib/store';
import { actionsFor, ledgerOf, permSummary, type CardState } from '../../lib/board';
import { basename, cost, timeAgo } from '../../lib/format';
import { suggestionLabel } from '../tools/Permission';

const WHERE: Record<CardState, string> = { review: 'With you · to review', prompt: 'With you · Claude is asking', running: 'With Claude', stuck: 'With Claude · stopped, needs something from you', wait: 'Waiting on a condition', backlog: 'Not started', done: 'Done' };

/**
 * The chosen card in full: its intent, what Claude is asking or why it stopped, the handoff report
 * when there is one, the ledger of everything that happened to it, and at the bottom the actions
 * its state allows. Approving and sending back happen here without opening the conversation.
 */
export function CardPanel({ s, rt, state, mode, setMode, onAct, onClose, now }: { s: SessionSummary; rt?: Runtime; state: CardState; mode: 'reject' | 'send' | null; setMode: (m: 'reject' | 'send' | null) => void; onAct: (a: string) => void; onClose: () => void; now: number }) {
  const actions = actionsFor(state, rt);
  const perm = state === 'prompt' ? rt?.pending?.[0] : undefined;
  const ledger = ledgerOf(s, rt);
  const [editing, setEditing] = useState(false);
  useEffect(() => setEditing(false), [s.id]);
  return (
    <aside className="bd-panel">
      <div className="bd-panel-h">
        <div className="bd-panel-meta">
          <span className="ellipsis">{basename(s.project || s.cwd)}{s.worktree ? ` · ${s.worktree.branch}` : s.gitBranch ? ` · ${s.gitBranch}` : ''}</span>
          <button className="icon-btn sm" title="Open the conversation (Enter)" aria-label="Open the conversation" onClick={() => onAct('open')}>
            <ArrowUpRight size={14} />
          </button>
          <button className="icon-btn sm" title="Close (Esc)" aria-label="Close" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <h3 className="bd-panel-title">{s.title}</h3>
        <div className="bd-panel-where">{WHERE[state]}</div>
      </div>

      <div className="bd-panel-body">
        <section className="bd-sec">
          <h4>
            Intent
            {!editing && (
              <button className="icon-btn sm" title="Edit the intent" aria-label="Edit the intent" onClick={() => setEditing(true)}>
                <Pencil size={12} />
              </button>
            )}
          </h4>
          {editing ? <IntentEditor s={s} done={() => setEditing(false)} /> : s.intent ? <p className="bd-intent">{s.intent}</p> : <p className="bd-hint">No intent yet. Two or three sentences on the goal and when it is done; a card with one can be handed to Claude from here.</p>}
        </section>

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
      </div>

      <div className="bd-actions">
        {mode ? (
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
          <button className={`btn sm ${perm.defaultToNo ? 'primary' : 'ghost'}`} onClick={deny} disabled={busy}>
            Deny<span className="key">{allowAlways ? 3 : 2}</span>
          </button>
        </div>
      )}
    </div>
  );
}
