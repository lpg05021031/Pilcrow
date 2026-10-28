import { useEffect, useMemo, useState } from 'react';
import { BookMarked, Pin, PinOff, Trash, Plus, Sparkles, Check, X, Folder } from 'lucide-react';
import { useStore, call, setConfig, toast, confirmAction, activeSession } from '../../lib/store';
import type { MemoryNote } from '../../lib/types';
import { basename, timeAgo, tildify } from '../../lib/format';
import { Select } from '../ui/Select';
import { Switch, Spinner, useNow } from '../ui/bits';
import { ModelSelect } from '../ModelSelect';

/**
 * Project memory: the notes earlier sessions left about a folder, which every new session there
 * gets to read. Notes come from a small run at the end of a turn, or are written here by hand.
 */
export function MemoryPage() {
  const config = useStore((s) => s.config)!;
  const sessions = useStore((s) => s.sessions);
  const info = useStore((s) => s.info);
  const rev = useStore((s) => s.schedRev);
  const active = activeSession();
  const projects = useMemo(() => {
    const set = new Set<string>();
    if (active) set.add(active.project || active.cwd);
    for (const p of config.recentProjects || []) set.add(p);
    for (const s of sessions) if (!s.archived) set.add(s.project || s.cwd);
    return [...set].filter(Boolean);
  }, [sessions, config.recentProjects, active]);
  const [cwd, setCwd] = useState(projects[0] || '');
  const [notes, setNotes] = useState<MemoryNote[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const now = useNow(30_000, true);
  useEffect(() => {
    if (!cwd && projects[0]) setCwd(projects[0]);
  }, [projects, cwd]);

  useEffect(() => {
    if (!cwd) return;
    let alive = true;
    call<{ notes: MemoryNote[] }>('memory.list', { cwd }, { quiet: true })
      .then((r) => alive && setNotes(r.notes))
      .catch(() => alive && setNotes([]));
    return () => {
      alive = false;
    };
  }, [cwd, rev]);

  const add = async () => {
    if (!draft.trim()) return;
    const n = await call<MemoryNote>('memory.add', { cwd, text: draft.trim() });
    setNotes((xs) => [n, ...(xs || [])]);
    setDraft('');
    setAdding(false);
  };
  const patch = async (id: string, p: Partial<MemoryNote>) => {
    const n = await call<MemoryNote>('memory.update', { cwd, id, ...p });
    setNotes((xs) => (xs || []).map((x) => (x.id === id ? n : x)));
  };
  const remove = async (id: string) => {
    await call('memory.remove', { cwd, id }, { quiet: true });
    setNotes((xs) => (xs || []).filter((x) => x.id !== id));
  };
  const distill = async () => {
    const s = sessions.find((x) => x.id === useStore.getState().activeId);
    if (!s) return toast('Open a session first.', 'info');
    setBusy(true);
    try {
      const r = await call<{ added: MemoryNote[] }>('memory.distill', { id: s.id, force: true });
      toast(r.added.length ? `Saved ${r.added.length} note${r.added.length > 1 ? 's' : ''} from this session` : 'Nothing new worth keeping from this session', r.added.length ? 'success' : 'info');
      if ((s.project || s.cwd) !== cwd) setCwd(s.project || s.cwd);
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };
  const pinned = (notes || []).filter((n) => n.pinned);
  const rest = (notes || []).filter((n) => !n.pinned);

  return (
    <div className="cust mem">
      <div className="cust-intro">
        <BookMarked size={16} />
        <div>
          <div className="cust-intro-title">Project memory</div>
          <div className="hint">What earlier sessions learned about a folder — decisions, dead ends, conventions — handed to every new session there. Pin what must never be forgotten; edit anything that drifts.</div>
        </div>
      </div>

      <div className="mem-settings">
        <label className="mem-setting">
          <span>
            <span className="mem-setting-title">Take notes automatically</span>
            <span className="hint">A short run at the end of each turn writes down anything worth keeping. It costs a few cents per turn.</span>
          </span>
          <Switch on={config.memoryAuto !== false} onChange={(v) => setConfig({ memoryAuto: v })} />
        </label>
        <label className="mem-setting">
          <span>
            <span className="mem-setting-title">Give notes to new sessions</span>
            <span className="hint">The notes ride along in the system prompt of every new session in that folder.</span>
          </span>
          <Switch on={config.memoryInject !== false} onChange={(v) => setConfig({ memoryInject: v })} />
        </label>
        <div className="mem-setting">
          <span>
            <span className="mem-setting-title">Model for note-taking</span>
            <span className="hint">A smaller model is plenty here.</span>
          </span>
          <div style={{ width: 240 }}>
            <ModelSelect value={config.memoryModel || ''} onChange={(v, typing) => !typing && setConfig({ memoryModel: v })} onCommit={() => {}} />
          </div>
        </div>
      </div>

      <div className="cust-bar">
        <div className="mem-proj">
          <Folder size={14} className="faint" />
          <Select value={cwd} onChange={setCwd} options={projects.map((p) => ({ value: p, label: basename(p), hint: tildify(p, info?.home) }))} width={360} />
        </div>
        <div className="grow" />
        <button className="btn sm ghost" onClick={distill} disabled={busy} title="Write notes from the session that is open now">
          {busy ? <Spinner size={12} /> : <Sparkles size={13} />} Save what we learned
        </button>
        <button className="btn sm primary" onClick={() => setAdding(true)}>
          <Plus size={13} /> Add note
        </button>
      </div>

      {adding && (
        <div className="mem-add">
          <textarea className="textarea" rows={2} autoFocus placeholder="One durable fact about this project — e.g. “Tests must run from the repo root; the fixtures use relative paths.”" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => (e.key === 'Enter' && (e.metaKey || e.ctrlKey) ? add() : e.key === 'Escape' ? setAdding(false) : null)} />
          <div className="row">
            <span className="hint">Ctrl+Enter saves</span>
            <div className="grow" />
            <button className="btn sm" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button className="btn sm primary" disabled={!draft.trim()} onClick={add}>
              Save note
            </button>
          </div>
        </div>
      )}

      <div className="mem-list">
        {notes === null && <Spinner size={14} />}
        {notes && !notes.length && (
          <div className="empty">
            <BookMarked size={22} />
            <div>No notes for {basename(cwd) || 'this folder'} yet.</div>
            <div className="hint">Notes appear after a session that decided something, hit a dead end, or found a quirk. You can also add one by hand.</div>
          </div>
        )}
        {pinned.length > 0 && (
          <div className="mem-group">
            <div className="tool-label">Pinned</div>
            {pinned.map((n) => (
              <NoteRow key={n.id} n={n} now={now} onPatch={(p) => patch(n.id, p)} onRemove={() => remove(n.id)} />
            ))}
          </div>
        )}
        {rest.length > 0 && (
          <div className="mem-group">
            {pinned.length > 0 && <div className="tool-label">Notes</div>}
            {rest.map((n) => (
              <NoteRow key={n.id} n={n} now={now} onPatch={(p) => patch(n.id, p)} onRemove={() => remove(n.id)} />
            ))}
          </div>
        )}
        {rest.length > 3 && (
          <button
            className="link mem-clear"
            onClick={() =>
              confirmAction({
                title: `Forget the unpinned notes for ${basename(cwd)}?`,
                body: 'Pinned notes stay. New notes keep being taken.',
                confirm: 'Forget notes',
                danger: true,
                onConfirm: () => call('memory.clear', { cwd }).then(() => setNotes((xs) => (xs || []).filter((x) => x.pinned))),
              })
            }
          >
            Forget all unpinned notes
          </button>
        )}
      </div>
    </div>
  );
}

function NoteRow({ n, now, onPatch, onRemove }: { n: MemoryNote; now: number; onPatch: (p: Partial<MemoryNote>) => Promise<void>; onRemove: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(n.text);
  const commit = async () => {
    if (text.trim() && text.trim() !== n.text) await onPatch({ text: text.trim() });
    setEditing(false);
  };
  return (
    <div className={`mem-note${n.pinned ? ' pinned' : ''}`}>
      {editing ? (
        <div className="mem-edit">
          <textarea className="textarea" rows={2} autoFocus value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => (e.key === 'Enter' && (e.metaKey || e.ctrlKey) ? commit() : e.key === 'Escape' ? (setText(n.text), setEditing(false)) : null)} />
          <div className="row">
            <button className="icon-btn sm" onClick={() => (setText(n.text), setEditing(false))} title="Cancel">
              <X size={13} />
            </button>
            <button className="icon-btn sm" onClick={commit} title="Save (Ctrl+Enter)">
              <Check size={13} />
            </button>
          </div>
        </div>
      ) : (
        <button className="mem-text" onClick={() => setEditing(true)} title="Click to edit">
          {n.text}
        </button>
      )}
      <div className="mem-meta">
        <span className="faint ellipsis">
          {n.kind === 'manual' ? 'You' : n.sessionTitle || 'A session'} · {timeAgo(n.at, now)}
        </span>
        <span className="grow" />
        <button className="icon-btn sm" onClick={() => onPatch({ pinned: !n.pinned })} title={n.pinned ? 'Unpin' : 'Pin — never forgotten'}>
          {n.pinned ? <PinOff size={12} /> : <Pin size={12} />}
        </button>
        <button className="icon-btn sm" onClick={onRemove} title="Forget this note">
          <Trash size={12} />
        </button>
      </div>
    </div>
  );
}

