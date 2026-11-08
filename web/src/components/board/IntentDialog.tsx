import { useEffect, useRef, useState } from 'react';
import { useStore, setDialog, setIntent } from '../../lib/store';
import { errText } from '../../lib/format';
import { Dialog } from '../ui/Dialog';

/**
 * Turn a conversation into a card, or change what a card is for: the intent is the only thing a
 * session needs to be work on the board.
 */
export function IntentDialog() {
  const spec = useStore((s) => s.dialogs.intent);
  const session = useStore((s) => s.sessions.find((x) => x.id === spec?.id));
  const [text, setText] = useState(session?.intent || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const close = () => setDialog('intent', null);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (spec) await setIntent(spec.id, text);
      close();
    } catch (e) {
      setError(errText(e));
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={session?.intent ? 'Edit the intent' : 'Make this a card'}
      onClose={close}
      onSubmit={save}
      width={520}
      footer={
        <>
          {error && <span className="err-text">{error}</span>}
          <span className="grow" />
          <button className="btn" onClick={close}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={busy}>
            Save
          </button>
        </>
      }
    >
      <p className="faint" style={{ margin: '0 0 8px' }}>
        {session?.title}
      </p>
      <textarea ref={ref} className="textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Two or three sentences: what the work is, and when it is done. A session with an intent shows on the workboard and asks for your review when a turn ends." onKeyDown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && save()} />
      <p className="faint" style={{ margin: '8px 0 0' }}>Clear the text to take the session off the board again.</p>
    </Dialog>
  );
}
