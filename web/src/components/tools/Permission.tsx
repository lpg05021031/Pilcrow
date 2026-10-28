import { useEffect, useState } from 'react';
import { CornerDownLeft } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import type { PermissionRequest, PermissionSuggestion } from '../../lib/types';
import { respond, toast } from '../../lib/store';
import { relPath } from '../../lib/format';
import type { ToolCtx } from './ToolCard';
import { str } from '../../lib/tools';
import type { PermissionDecision } from '../../lib/types';

function destLabel(d?: string) {
  switch (d) {
    case 'session':
      return 'for this session';
    case 'localSettings':
      return 'in this project (just you)';
    case 'projectSettings':
      return 'in this project';
    case 'userSettings':
      return 'everywhere';
    default:
      return '';
  }
}

/** Number keys answer a waiting prompt unless the person is typing somewhere. An empty message box
 *  counts as not typing: the box has focus by default, and a lone "1" there is never a message. */
export function typingElsewhere(t: EventTarget | null) {
  const el = t as HTMLElement | null;
  if (!el || !(el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable)) return false;
  return !(el.classList.contains('composer-input') && !(el as HTMLTextAreaElement).value);
}

/** A short button label for a suggestion; the exact rules go on a quiet line under the buttons. */
export function suggestionLabel(s: PermissionSuggestion, cwd?: string): string {
  if (s.type === 'addRules' && s.behavior === 'allow') {
    const rules = s.rules || [];
    const tools = new Set(rules.map((r) => r.toolName));
    const scope = destLabel(s.destination);
    if (tools.size === 1 && (tools.has('Bash') || tools.has('PowerShell'))) return `${rules.length > 1 ? `Always allow these ${rules.length} commands` : 'Always allow this command'} ${scope}`;
    if (tools.size === 1) return `Always allow ${[...tools][0]} ${scope}`;
    return `Always allow ${rules.length} rules ${scope}`;
  }
  return describeSuggestion(s, cwd);
}

export function suggestionRules(s: PermissionSuggestion): string {
  if (s.type !== 'addRules') return '';
  return (s.rules || []).map((r) => (r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName)).join('  ·  ');
}

export function describeSuggestion(s: PermissionSuggestion, cwd?: string): string {
  if (s.type === 'setMode') return s.mode === 'acceptEdits' ? `Allow all edits ${destLabel(s.destination)}` : `Switch to ${s.mode} ${destLabel(s.destination)}`;
  if (s.type === 'addDirectories') return `Allow access to ${(s.directories || []).map((d) => relPath(d, cwd)).join(', ')} ${destLabel(s.destination)}`;
  if (s.type === 'addRules' && s.behavior === 'allow') {
    const rules = (s.rules || []).map((r) => (r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName));
    return `Always allow ${rules.join(', ')} ${destLabel(s.destination)}`;
  }
  return `${s.type} ${destLabel(s.destination)}`;
}

function defaultTitle(perm: PermissionRequest, cwd: string) {
  const i = perm.input || {};
  switch (perm.toolName) {
    case 'Bash':
    case 'PowerShell':
      return 'Allow Claude to run this command?';
    case 'Edit':
    case 'MultiEdit':
      return `Allow this edit to ${relPath(str(i, 'file_path'), cwd)}?`;
    case 'Write':
      return `Allow Claude to write ${relPath(str(i, 'file_path'), cwd)}?`;
    case 'WebFetch':
      return `Allow Claude to fetch ${i.url}?`;
    case 'NotebookEdit':
      return `Allow this notebook edit?`;
    default:
      return perm.title || `Allow Claude to use ${perm.displayName || perm.toolName}?`;
  }
}

/** Scope words for the "won't ask again" line. */
function ruleScope(s: PermissionSuggestion): string {
  const scope = destLabel(s.destination);
  return scope ? ` ${scope}` : '';
}

export function PermissionCard({ perm, item, ctx, stale = false }: { perm: PermissionRequest; item?: ToolItem; ctx: ToolCtx; stale?: boolean }) {
  const [feedback, setFeedback] = useState('');
  const [showNote, setShowNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const allowAlways = !perm.suppressAlwaysAllowRule && perm.suggestions.length > 0;
  const first = [...ctx.pending.values()][0]?.requestId === perm.requestId;
  // Inside a tool card the row above already says what Claude wants to do; only an orphan needs a title.
  const titleText = item ? '' : perm.title && perm.toolName !== 'Bash' && perm.toolName !== 'PowerShell' ? perm.title : defaultTitle(perm, ctx.cwd);
  const sub = [
    perm.description && !item && !relPath(str(perm.input, 'file_path'), ctx.cwd).includes(perm.description) ? perm.description : '',
    perm.decisionReason || '',
  ].filter(Boolean);

  const decide = async (decision: PermissionDecision) => {
    if (busy) return;
    setBusy(true);
    try {
      await respond(ctx.sessionId, perm.requestId, decision);
    } catch {
      setBusy(false);
    }
  };
  const allow = () => decide({ behavior: 'allow', decisionClassification: 'user_temporary' });
  const always = () => decide({ behavior: 'allow', updatedPermissions: perm.suggestions, decisionClassification: 'user_permanent' });
  const deny = () => {
    decide({ behavior: 'deny', message: feedback.trim() ? `The user declined and said: ${feedback.trim()}` : 'The user declined this action.', decisionClassification: 'user_reject' });
    if (feedback.trim()) toast('Sent your note to Claude', 'info');
  };

  useEffect(() => {
    if (!first) return;
    const onKey = (e: KeyboardEvent) => {
      if (typingElsewhere(e.target)) return;
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

  const rule = allowAlways ? suggestionRules(perm.suggestions[0]) : '';
  const extra = allowAlways && perm.suggestions.length > 1 ? ` and ${perm.suggestions.length - 1} more` : '';

  return (
    <div className={`perm${stale ? ' stale' : ''}${item ? '' : ' orphan'}`} role="alertdialog" aria-label="Permission request">
      {titleText && <div className="perm-title">{titleText}</div>}
      {(sub.length > 0 || perm.blockedPath) && (
        <div className="perm-sub">
          {sub.map((t, i) => (
            <span key={i}>{t}</span>
          ))}
          {perm.blockedPath && (
            <span>
              Outside the working folder: <code>{perm.blockedPath}</code>
            </span>
          )}
        </div>
      )}
      <div className="perm-actions">
        <span className="perm-choice">
          <button className={`btn ${perm.defaultToNo ? '' : 'primary'}`} onClick={allow} disabled={busy}>
            Allow
          </button>
          {first && !perm.defaultToNo && <kbd className="perm-key">1</kbd>}
        </span>
        {allowAlways && (
          <span className="perm-choice">
            <button className="btn" onClick={always} disabled={busy} title={perm.suggestions.map((s) => describeSuggestion(s, ctx.cwd)).join('\n')}>
              Always allow
            </button>
            {first && <kbd className="perm-key">2</kbd>}
          </span>
        )}
        <span className="perm-choice">
          <button className={`btn ${perm.defaultToNo ? 'primary' : 'ghost'}`} onClick={deny} disabled={busy}>
            {feedback.trim() ? 'Deny with note' : 'Deny'}
          </button>
          {first && <kbd className="perm-key">{allowAlways ? 3 : 2}</kbd>}
        </span>
        <div className="grow" />
        {!showNote && (
          <button className="perm-note-link" onClick={() => setShowNote(true)}>
            Tell Claude what to do instead
          </button>
        )}
      </div>
      {rule && (
        <div className="perm-rules">
          Always allow covers <code>{rule}</code>
          {extra}
          {ruleScope(perm.suggestions[0])}.
        </div>
      )}
      {showNote && (
        <div className="perm-feedback">
          <input
            className="input"
            autoFocus
            value={feedback}
            placeholder="What should Claude do instead?"
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && feedback.trim()) deny();
              if (e.key === 'Escape') setShowNote(false);
            }}
          />
          <span className="perm-enter">
            <CornerDownLeft size={12} /> sends it and declines this step
          </span>
        </div>
      )}
    </div>
  );
}
