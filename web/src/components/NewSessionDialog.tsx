import { useEffect, useMemo, useState } from 'react';
import { Folder, FolderGit2, ArrowUp, Home, ChevronRight, GitBranch, CornerLeftUp, FolderOpen, Check, ChevronDown } from 'lucide-react';
import { useStore, setDialog, createSession, call } from '../lib/store';
import type { GitInfo, PermissionMode, Effort } from '../lib/types';
import { basename, tildify } from '../lib/format';
import { Dialog } from './ui/Dialog';
import { Switch, Spinner } from './ui/bits';
import { Collapse } from './ui/Collapse';
import { Select } from './ui/Select';
import { MODES } from './Composer';
import { ModelSelect } from './ModelSelect';
import { errText } from '../lib/format';

interface Browse {
  dir: string;
  display: string;
  parent: string | null;
  repo: boolean;
  dirs: { name: string; path: string; repo: boolean }[];
  error?: string;
}

export function NewSessionDialog() {
  const spec = useStore((s) => s.dialogs.newSession)!;
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const sessions = useStore((s) => s.sessions);
  const [cwd, setCwd] = useState(spec.cwd || config?.recentProjects?.[0] || info?.home || '');
  const [browse, setBrowse] = useState<Browse | null>(null);
  const [showBrowser, setShowBrowser] = useState(!spec.cwd && !config?.recentProjects?.length);
  const [git, setGit] = useState<GitInfo | null>(null);
  const [branches, setBranches] = useState<{ name: string }[]>([]);
  const [useWorktree, setUseWorktree] = useState(false);
  const [base, setBase] = useState('');
  const [wtName, setWtName] = useState('');
  const [mode, setMode] = useState<PermissionMode>((config?.defaultMode as PermissionMode) || 'default');
  const [model, setModel] = useState(config?.defaultModel || '');
  const [effort, setEffort] = useState<Effort>((config?.defaultEffort as Effort) || '');
  const [prompt, setPrompt] = useState(spec.prompt || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Defaults cover the usual case; the knobs stay folded until someone wants them.
  const [showOptions, setShowOptions] = useState(false);

  const recents = useMemo(() => {
    const set = new Set<string>(config?.recentProjects || []);
    for (const s of sessions) if (set.size < 10) set.add(s.project || s.cwd);
    return [...set].filter(Boolean).slice(0, 8);
  }, [config?.recentProjects, sessions]);

  useEffect(() => {
    if (!showBrowser) return;
    call<Browse>('fs.browse', { dir: browse?.dir || cwd || info?.home }, { quiet: true })
      .then(setBrowse)
      .catch(() => {});
  }, [showBrowser]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setError('');
    if (!cwd) return;
    const t = setTimeout(async () => {
      try {
        const g = await call<GitInfo>('git.info', { cwd }, { quiet: true });
        setGit(g);
        if (g.isRepo) {
          const b = await call<{ current: string | null; list: { name: string }[] }>('git.branches', { cwd }, { quiet: true });
          setBranches(b.list);
          setBase(b.current || b.list[0]?.name || 'HEAD');
        } else setUseWorktree(false);
      } catch {
        setGit(null);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [cwd]);

  const nav = async (dir: string) => {
    const r = await call<Browse>('fs.browse', { dir }, { quiet: true }).catch(() => null);
    if (r) {
      setBrowse(r);
      setCwd(r.dir);
    }
  };

  const pickNative = async () => {
    const native = window.pilcrowNative;
    const p = await native?.pickFolder?.(cwd);
    if (p) setCwd(p);
  };

  const start = async () => {
    if (!cwd || busy) return;
    setBusy(true);
    setError('');
    try {
      await createSession({ cwd, worktree: useWorktree ? { base, name: wtName || prompt.slice(0, 40) || undefined } : null, mode, model, effort, prompt });
      setDialog('newSession', null);
    } catch (e) {
      setError(errText(e));
      setBusy(false);
    }
  };

  const native = window.pilcrowNative?.pickFolder;

  return (
    <Dialog
      title="New session"
      onClose={() => setDialog('newSession', null)}
      width={580}
      onSubmit={() => cwd && !busy && start()}
      className="newsession"
      footer={
        <>
          {error ? <span className="err-text grow">{error}</span> : <span className="hint grow">
              <kbd>Ctrl</kbd> <kbd>Enter</kbd> to start
            </span>}
          <button className="btn ghost" onClick={() => setDialog('newSession', null)}>
            Cancel
          </button>
          <button className="btn primary" onClick={start} disabled={!cwd || busy}>
            {busy && <Spinner size={13} />} {prompt.trim() ? 'Start' : 'Create session'}
          </button>
        </>
      }
    >
      <section className="ns-section">
        <div className="ns-label">Folder</div>
        <div className="ns-folder">
          <FolderOpen size={16} className="ns-folder-ico" />
          <input className="ns-folder-input mono" value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="/path/to/project" spellCheck={false} />
          <button className={`btn sm ghost${showBrowser ? ' on' : ''}`} onClick={() => setShowBrowser(!showBrowser)}>
            Browse
          </button>
          {native && (
            <button className="btn sm ghost" onClick={pickNative} title="Use the system folder picker">
              System…
            </button>
          )}
        </div>
        {recents.some((r) => r !== cwd) && !showBrowser && (
          <div className="chips">
            <span className="chips-label">Recent</span>
            {recents
              .filter((r) => r !== cwd)
              .map((r) => (
                <button key={r} className="folder-chip" onClick={() => setCwd(r)} title={tildify(r, info?.home)}>
                  {basename(r)}
                </button>
              ))}
          </div>
        )}
        {showBrowser && browse && (
          <div className="browser">
            <div className="browser-head">
              <button className="icon-btn sm" title="Home" onClick={() => nav(info?.home || '/')}>
                <Home size={13} />
              </button>
              <button className="icon-btn sm" title="Up one level" disabled={!browse.parent} onClick={() => browse.parent && nav(browse.parent)}>
                <CornerLeftUp size={13} />
              </button>
              <span className="mono ellipsis grow">{browse.display}</span>
              {browse.repo && <span className="badge">git</span>}
            </div>
            <div className="browser-list">
              {browse.error && <div className="faint browser-empty">{browse.error}</div>}
              {browse.dirs.map((d) => (
                <button key={d.path} className="browser-item" onClick={() => nav(d.path)} onDoubleClick={() => (setCwd(d.path), setShowBrowser(false))}>
                  {d.repo ? <FolderGit2 size={14} /> : <Folder size={14} />}
                  <span className="ellipsis grow">{d.name}</span>
                  <ChevronRight size={13} className="faint" />
                </button>
              ))}
              {!browse.dirs.length && !browse.error && <div className="faint browser-empty">No subfolders</div>}
            </div>
          </div>
        )}
      </section>

      <button className={`ns-disclose${showOptions ? ' open' : ''}`} onClick={() => setShowOptions(!showOptions)} aria-expanded={showOptions}>
        <ChevronDown size={14} className="ns-disclose-chev" />
        <span className="ns-disclose-title">Options</span>
        {!showOptions && (
          <span className="ns-disclose-summary ellipsis">
            {MODES.find((m) => m.id === mode)?.label || 'Ask before edits'} · {model ? model.replace(/^claude-/, '') : 'default model'}
            {effort ? ` · ${effort === 'xhigh' ? 'extra high' : effort} effort` : ''}
            {useWorktree ? ' · isolated worktree' : ''}
          </span>
        )}
      </button>
      <Collapse open={showOptions} className="ns-options">
      {git?.isRepo && (
        <section className="ns-section">
          <div className="ns-row">
            <div className="grow">
              <div className="ns-row-title">Isolated worktree</div>
              <div className="hint">Claude works on its own branch and checkout, so parallel sessions never collide.</div>
            </div>
            <Switch on={useWorktree} onChange={setUseWorktree} label="Use a worktree" />
          </div>
          {useWorktree && (
            <div className="ns-two">
              <div className="field">
                <label>
                  <GitBranch size={11} /> Start from
                </label>
                <Select value={base} onChange={setBase} options={branches.map((b) => ({ value: b.name, label: b.name }))} />
              </div>
              <div className="field">
                <label>Branch name</label>
                <input className="input" value={wtName} onChange={(e) => setWtName(e.target.value)} placeholder="murphy-yield" />
              </div>
            </div>
          )}
          {!useWorktree && git.changed ? <div className="hint">{git.changed} uncommitted change{git.changed > 1 ? 's' : ''} here; Claude works on top of them.</div> : null}
        </section>
      )}

      <section className="ns-section">
        <div className="ns-label">Permissions</div>
        <div className="radio-list" role="radiogroup">
          {MODES.filter((m) => !(m.id === 'bypassPermissions' && info?.isRoot)).map((m) => (
            <button key={m.id} role="radio" aria-checked={mode === m.id} className={`radio-row${mode === m.id ? ' on' : ''}${m.id === 'bypassPermissions' ? ' danger' : ''}`} onClick={() => setMode(m.id)}>
              <m.icon size={15} className="radio-ico" />
              <span className="radio-label">{m.label}</span>
              <span className="radio-hint">{m.hint}</span>
              <span className="radio-check">{mode === m.id && <Check size={14} />}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="ns-section ns-two">
        <div className="field">
          <label>Model</label>
          <ModelSelect value={model} onChange={(v) => setModel(v)} />
        </div>
        <div className="field">
          <label>Effort</label>
          <Select
            value={effort || ''}
            onChange={(v) => setEffort(v as Effort)}
            options={[
              { value: '', label: 'Default' },
              { value: 'low', label: 'Low' },
              { value: 'medium', label: 'Medium' },
              { value: 'high', label: 'High' },
              { value: 'xhigh', label: 'Extra high' },
              { value: 'max', label: 'Max' },
            ]}
          />
        </div>
      </section>
      </Collapse>

      <section className="ns-section">
        <div className="ns-label">First message</div>
        <textarea className="textarea ns-prompt" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={`What should Claude do in ${basename(cwd) || 'this folder'}? (optional)`} />
      </section>
    </Dialog>
  );
}

export { ArrowUp };
