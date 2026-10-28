import { useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import type { PermissionRequest } from '../../lib/types';
import { respond } from '../../lib/store';
import { Markdown } from '../Markdown';
import type { ToolCtx } from './ToolCard';

interface Q {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: { label: string; description?: string; preview?: string }[];
}

export function AskBody({ item, perm, ctx }: { item: ToolItem; perm?: PermissionRequest; ctx: ToolCtx }) {
  const rawQuestions = perm?.input?.questions || item.input?.questions;
  const questions = useMemo(() => (Array.isArray(rawQuestions) ? (rawQuestions as Q[]) : []), [rawQuestions]);
  const answered: Record<string, string> = item.structured?.answers || {};
  const [sel, setSel] = useState<Record<number, string[]>>({});
  const [other, setOther] = useState<Record<number, string>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [hover, setHover] = useState<{ q: number; o: number } | null>(null);
  const [noteOpen, setNoteOpen] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState(false);

  const complete = useMemo(() => questions.every((_, qi) => (sel[qi]?.length || 0) > 0 || (other[qi] || '').trim()), [questions, sel, other]);

  if (!perm) {
    const resultText = item.result?.text || '';
    return (
      <div className="askcard done">
        <div className="ask-head">
          <span>{questions.length > 1 ? 'Claude asked a few questions' : 'Claude asked a question'}</span>
          {item.status === 'denied' && <span className="badge">Skipped</span>}
        </div>
        {questions.map((q, qi) => (
          <div key={qi} className="ask-q">
            <div className="ask-qtext">{q.question}</div>
            <div className="ask-answer">
              {answered[q.question] ? (
                <>
                  <Check size={13} /> {answered[q.question]}
                </>
              ) : (
                <span className="faint">{item.status === 'running' || item.status === 'streaming' ? 'Waiting…' : resultText.includes(q.question) ? extractAnswer(resultText, q.question) : 'No answer'}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const toggle = (qi: number, label: string, multi: boolean) => {
    setSel((s) => {
      const cur = s[qi] || [];
      const next = multi ? (cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label]) : [label];
      return { ...s, [qi]: next };
    });
    if (!multi) setOther((o) => ({ ...o, [qi]: '' }));
  };

  const submit = async () => {
    if (!complete || busy) return;
    setBusy(true);
    const answers: Record<string, string> = {};
    const annotations: Record<string, { notes?: string }> = {};
    questions.forEach((q, qi) => {
      const parts = [...(sel[qi] || [])];
      if ((other[qi] || '').trim()) parts.push(other[qi].trim());
      answers[q.question] = parts.join(', ');
      if ((notes[qi] || '').trim()) annotations[q.question] = { notes: notes[qi].trim() };
    });
    try {
      await respond(ctx.sessionId, perm.requestId, { behavior: 'allow', updatedInput: { ...perm.input, answers, ...(Object.keys(annotations).length ? { annotations } : {}) } });
    } catch {
      setBusy(false);
    }
  };

  const skip = () => respond(ctx.sessionId, perm.requestId, { behavior: 'deny', message: 'The user chose not to answer these questions. Continue with your best judgment.' });

  const preview = hover ? questions[hover.q]?.options[hover.o]?.preview : null;

  return (
    <div className="askcard live">
      <div className="ask-head">
        <span>{questions.length > 1 ? `Claude has ${questions.length} questions` : 'Claude has a question'}</span>
      </div>
      <div className={`ask-body${preview ? ' with-preview' : ''}`}>
        <div className="ask-qs">
          {questions.map((q, qi) => (
            <div key={qi} className="ask-q">
              <div className="ask-qline">
                {q.header && <span className="ask-tag">{q.header}</span>}
                <span className="ask-qtext">{q.question}</span>
                {q.multiSelect && <span className="ask-multi">Choose any</span>}
              </div>
              <div className="ask-options" role={q.multiSelect ? 'group' : 'radiogroup'}>
                {q.options.map((o, oi) => {
                  const on = (sel[qi] || []).includes(o.label);
                  return (
                    <button key={oi} className={`ask-opt${on ? ' on' : ''}`} role={q.multiSelect ? 'checkbox' : 'radio'} aria-checked={on} onClick={() => toggle(qi, o.label, Boolean(q.multiSelect))} onMouseEnter={() => setHover({ q: qi, o: oi })} onFocus={() => setHover({ q: qi, o: oi })}>
                      <span className={`ask-mark${q.multiSelect ? ' box' : ''}`}>{on && <Check size={11} strokeWidth={3} />}</span>
                      <span className="ask-opt-text">
                        <span className="ask-opt-label">{o.label}</span>
                        {o.description && <span className="ask-opt-desc">{o.description}</span>}
                      </span>
                    </button>
                  );
                })}
                <label className={`ask-opt ask-other${(other[qi] || '').trim() ? ' on' : ''}`}>
                  <span className={`ask-mark${q.multiSelect ? ' box' : ''}`}>{(other[qi] || '').trim() && <Check size={11} strokeWidth={3} />}</span>
                  <input
                    placeholder="Something else…"
                    value={other[qi] || ''}
                    onChange={(e) => {
                      const v = e.target.value;
                      setOther((s) => ({ ...s, [qi]: v }));
                      if (v && !q.multiSelect) setSel((s) => ({ ...s, [qi]: [] }));
                    }}
                    onKeyDown={(e) => e.key === 'Enter' && submit()}
                  />
                </label>
              </div>
              {noteOpen[qi] ? (
                <input className="input ask-notes" autoFocus placeholder="A note for Claude about this answer" value={notes[qi] || ''} onChange={(e) => setNotes((s) => ({ ...s, [qi]: e.target.value }))} />
              ) : (
                <button className="link ask-note-link" onClick={() => setNoteOpen((s) => ({ ...s, [qi]: true }))}>
                  Add a note
                </button>
              )}
            </div>
          ))}
        </div>
        {preview && (
          <div className="ask-preview">
            <Markdown text={/^\s*(```|#|[-*] )/.test(preview) ? preview : '```\n' + preview + '\n```'} cwd={ctx.cwd} className="md-compact" />
          </div>
        )}
      </div>
      <div className="ask-actions">
        <button className="btn primary" onClick={submit} disabled={!complete || busy}>
          Send {questions.length > 1 ? 'answers' : 'answer'}
        </button>
        <button className="btn ghost" onClick={skip} disabled={busy}>
          Skip
        </button>
      </div>
    </div>
  );
}

function extractAnswer(text: string, q: string) {
  const i = text.indexOf(`"${q}"="`);
  if (i < 0) return 'Answered';
  const rest = text.slice(i + q.length + 4);
  return rest.slice(0, rest.indexOf('"'));
}
