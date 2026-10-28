import { useEffect, useState } from 'react';
import { Check, FileText } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import type { PermissionRequest } from '../../lib/types';
import { respond, openFile } from '../../lib/store';
import { Markdown } from '../Markdown';
import type { ToolCtx } from './ToolCard';
import { typingElsewhere } from './Permission';
import { str } from '../../lib/tools';
import type { PermissionDecision } from '../../lib/types';

export function PlanBody({ item, perm, ctx }: { item: ToolItem; perm?: PermissionRequest; ctx: ToolCtx }) {
  const plan: string = str(perm?.input, 'plan') || str(item.input, 'plan') || item.structured?.plan || '';
  const file: string | undefined = item.structured?.filePath;
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(!perm && plan.length > 1200);
  const [revising, setRevising] = useState(false);

  const decide = async (d: PermissionDecision) => {
    setBusy(true);
    try {
      await respond(ctx.sessionId, perm!.requestId, d);
    } catch {
      setBusy(false);
    }
  };
  const approve = (mode: 'acceptEdits' | 'default') => decide({ behavior: 'allow', updatedInput: perm!.input, updatedPermissions: [{ type: 'setMode', mode, destination: 'session' }] });
  const keepPlanning = () => decide({ behavior: 'deny', message: feedback.trim() ? `The user wants changes to the plan: ${feedback.trim()}` : 'The user wants to keep planning. Ask what to change.' });

  const outcome = !perm && item.result ? (item.status === 'denied' || item.result.isError ? 'kept' : 'approved') : null;

  // Same keys as a permission prompt: 1 approve and start, 2 approve but ask before edits, 3 keep planning.
  useEffect(() => {
    if (!perm || revising || busy) return;
    const onKey = (e: KeyboardEvent) => {
      if (typingElsewhere(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '1') approve('acceptEdits');
      else if (e.key === '2') approve('default');
      else if (e.key === '3') setRevising(true);
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <div className={`plancard${perm ? ' live' : ''}`}>
      <div className="plan-head">
        <span className="plan-kicker">{perm ? 'Plan ready for review' : 'Plan'}</span>
        <span className="grow" />
        {outcome === 'approved' && (
          <span className="badge ok">
            <Check size={11} /> Approved
          </span>
        )}
        {outcome === 'kept' && <span className="badge">Kept planning</span>}
        {file && (
          <button className="link" onClick={() => openFile(file)} title="Open the plan file">
            <FileText size={13} /> Plan file
          </button>
        )}
      </div>
      <div className={`plan-text${collapsed ? ' collapsed' : ''}`}>
        {plan ? <Markdown text={plan} cwd={ctx.cwd} /> : <div className="faint">Claude is ready to leave plan mode.</div>}
        {collapsed && (
          <button className="code-more fade" onClick={() => setCollapsed(false)}>
            Show the full plan
          </button>
        )}
      </div>
      {perm && (
        <div className="plan-actions">
          {revising ? (
            <div className="plan-feedback">
              <textarea className="textarea" rows={2} autoFocus placeholder="What should change in the plan?" value={feedback} onChange={(e) => setFeedback(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.metaKey || e.ctrlKey) && keepPlanning()} />
              <div className="row">
                <span className="hint grow">Ctrl+Enter sends</span>
                <button className="btn ghost" onClick={() => setRevising(false)} disabled={busy}>
                  Back
                </button>
                <button className="btn primary" onClick={keepPlanning} disabled={busy}>
                  Send feedback
                </button>
              </div>
            </div>
          ) : (
            <div className="row">
              <button className="btn primary" onClick={() => approve('acceptEdits')} disabled={busy}>
                Approve and start <span className="key">1</span>
              </button>
              <button className="btn" onClick={() => approve('default')} disabled={busy} title="Approve, but ask before each edit">
                Approve, ask before edits <span className="key">2</span>
              </button>
              <button className="btn ghost" onClick={() => setRevising(true)} disabled={busy}>
                Keep planning <span className="key">3</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
