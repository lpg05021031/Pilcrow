import { useEffect, useMemo, useRef, useState } from 'react';
import { FolderOpen, SlidersHorizontal } from 'lucide-react';
import { useStore, call, createSession, startCard, setDialog } from '../../lib/store';
import type { GitInfo } from '../../lib/types';
import { basename, errText, tildify } from '../../lib/format';
import { Switch, Spinner } from '../ui/bits';
import { ModelSelect } from '../ModelSelect';

/**
 * A card being written, at the top of the backlog: a title, two or three sentences of intent, the
 * folder it is about, and whether Claude works in its own worktree. Enter keeps it in the backlog;
 * Ctrl+Enter hands it to Claude straight away. The full New session dialog is one click away for
 * the rarer knobs (permission mode, effort, a base branch).
 */
export function NewCard({ onDone }: { onDone: (id: string | null) => void }) {
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const sessions = useStore((s) => s.sessions);
  const [title, setTitle] = useState('');
  const [intent, setIntent] = useState('');
  const [cwd, setCwd] = useState(config?.recentProjects?.[0] || info?.home || '');
  const [git, setGit] = useState<GitInfo | null>(null);
  const [worktree, setWorktree] = useState(true);
  const [model, setModel] = useState(config?.defaultModel || '');
  const [busy, setBusy] = useState<'' | 'create' | 'start'>('');
  const [error, setError] = useState('');
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => first.current?.focus(), []);

  const recents = useMemo(() => {
    const set = new Set<string>(config?.recentProjects || []);
    for (const s of sessions) if (set.size < 6) set.add(s.project || s.cwd);
    return [...set].filter(Boolean).slice(0, 5);
  }, [config?.recentProjects, sessions]);

  // A worktree only makes sense in a repository; the switch follows the folder.
  useEffect(() => {
    let alive = true;
    if (!cwd) return;
    call<GitInfo>('git.info', { cwd }, { quiet: true })
      .then((g) => {
        if (!alive) return;
        setGit(g);
        setWorktree(Boolean(g.isRepo));
      })
      .catch(() => alive && setGit(null));
    return () => {
      alive = false;
    };
  }, [cwd]);

  const pick = async () => {
    const p = await window.pilcrowNative?.pickFolder?.(cwd);
    if (p) setCwd(p);
    else setDialog('newSession', { cwd, prompt: intent });
  };

  const submit = async (start: boolean) => {
    if (busy) return;
    const t = title.trim();
    if (!t) {
      setError('Give the card a title.');
      first.current?.focus();
      return;
    }
    setBusy(start ? 'start' : 'create');
    setError('');
    try {
      // A card is on the board because it has an intent; a title alone is a one-line intent.
      const id = await createSession({ cwd, worktree: worktree && git?.isRepo ? { name: t.slice(0, 40) } : null, model, title: t, intent: intent.trim() || t, open: false });
      if (start) await startCard(id);
      onDone(id);
    } catch (e) {
      setError(errText(e));
      setBusy('');
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onDone(null);
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(true);
    } else if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLElement).tagName === 'INPUT') {
      e.preventDefault();
      submit(false);
    }
  };

  return (
    <div className="bd-new" onKeyDown={onKey} data-testid="new-card">
      <input ref={first} className="bd-new-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What is the work?" spellCheck={false} />
      <textarea className="textarea bd-new-intent" rows={3} value={intent} onChange={(e) => setIntent(e.target.value)} placeholder="Two or three sentences: the goal, and when it is done. Claude gets this as its first message." />
      <div className="bd-new-row">
        {recents.map((r) => (
          <button key={r} className={`bd-new-chip${r === cwd ? ' on' : ''}`} onClick={() => setCwd(r)} title={tildify(r, info?.home)}>
            {basename(r)}
          </button>
        ))}
        <button className="bd-new-chip" onClick={pick} title={tildify(cwd, info?.home)}>
          <FolderOpen size={12} /> {recents.includes(cwd) ? 'Other…' : basename(cwd) || 'Folder…'}
        </button>
      </div>
      <div className="bd-new-row opts">
        <label className="bd-new-opt" title={git?.isRepo ? 'Claude works on its own branch in a separate checkout' : 'Not a git repository'}>
          <Switch on={worktree && Boolean(git?.isRepo)} onChange={setWorktree} label="Worktree" />
          <span>Worktree</span>
        </label>
        <span className="bd-new-model">
          <ModelSelect value={model} onChange={(v) => setModel(v)} />
        </span>
        <button className="icon-btn sm" title="More options (permission mode, effort, base branch)" aria-label="More options" onClick={() => setDialog('newSession', { cwd, prompt: intent })}>
          <SlidersHorizontal size={13} />
        </button>
      </div>
      {error && <div className="bd-new-err">{error}</div>}
      <div className="bd-new-row acts">
        <button className="btn sm primary" onClick={() => submit(true)} disabled={Boolean(busy)}>
          {busy === 'start' && <Spinner size={12} />} Hand to Claude<span className="key">Ctrl ↵</span>
        </button>
        <button className="btn sm" onClick={() => submit(false)} disabled={Boolean(busy)}>
          {busy === 'create' && <Spinner size={12} />} Keep in backlog<span className="key">↵</span>
        </button>
        <button className="btn sm ghost" onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
