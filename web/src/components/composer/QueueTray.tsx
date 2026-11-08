import { useState } from 'react';
import { Clock3, File as FileIcon, Image as ImageIcon, X, Zap } from 'lucide-react';
import { removeHeld, updateHeld, sendHeldNow, toasted } from '../../lib/store';
import type { HeldMsg } from '../../lib/types';
import { Leave } from '../ui/bits';

/** Messages waiting for the current step to finish. Each can be edited, sent right away, or dropped. */
export function QueueTray({ sessionId, held }: { sessionId: string; held: HeldMsg[] }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const save = (h: HeldMsg) => {
    setEditing(null);
    const t = draft.trim();
    if (!t && !h.images && !(h.files || []).length) removeHeld(sessionId, h.uuid).catch(toasted);
    else if (t !== h.text) updateHeld(sessionId, h.uuid, t).catch(toasted);
  };
  return (
    <Leave className="queue" role="list" aria-label="Queued messages">
      <div className="queue-head">
        <Clock3 size={12} />
        <span>{held.length === 1 ? 'Queued — Claude reads it after this step' : `${held.length} queued — Claude reads them in order after this step`}</span>
      </div>
      {held.map((h, i) => (
        <div key={h.uuid} className={`queue-item${editing === h.uuid ? ' editing' : ''}`} role="listitem">
          <span className="queue-n tnum">{i + 1}</span>
          {editing === h.uuid ? (
            <textarea
              className="queue-edit"
              aria-label={`Queued message ${i + 1}`}
              autoFocus
              rows={Math.min(6, Math.max(1, draft.split('\n').length))}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => save(h)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  save(h);
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  e.stopPropagation();
                  setDraft(h.text);
                  setEditing(null);
                }
              }}
            />
          ) : (
            <button
              className="queue-text"
              title="Click to edit"
              onClick={() => {
                setEditing(h.uuid);
                setDraft(h.text);
              }}
            >
              {h.text || <span className="faint">(no text)</span>}
              {h.images > 0 && (
                <span className="queue-attach">
                  <ImageIcon size={11} /> {h.images}
                </span>
              )}
              {(h.files || []).map((f) => (
                <span key={f} className="queue-attach">
                  <FileIcon size={11} /> {f}
                </span>
              ))}
            </button>
          )}
          <span className="queue-actions">
            <button className="queue-btn" title="Send now — interrupts the current step" onClick={() => sendHeldNow(sessionId, h.uuid).catch(toasted)}>
              <Zap size={12} />
              <span>Now</span>
            </button>
            <button className="queue-btn icon" title="Remove" aria-label="Remove queued message" onClick={() => removeHeld(sessionId, h.uuid).catch(toasted)}>
              <X size={13} />
            </button>
          </span>
        </div>
      ))}
    </Leave>
  );
}
