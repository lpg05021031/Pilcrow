import { useEffect, useMemo, useState } from 'react';
import { FolderGit2, Plus, Search, Trash, ChevronRight, ChevronDown, RefreshCw, X } from 'lucide-react';
import { useStore, call, toast, confirmAction } from '../../lib/store';
import type { RepoEntry, RepoPolicy, RepoScanHit } from '../../lib/types';
import { basename, errText, tildify } from '../../lib/format';
import { Select } from '../ui/Select';
import { Seg, Switch } from '../ui/bits';
import { FolderPicker } from '../FolderPicker';

/**
 * Settings → Repositories (REPOSITORIES.md): the list the person maintains of the repositories Pilcrow
 * may work in — a name, their own aliases, one line about it, the policy for starting helpers and
 * Dispatch runs there, defaults, standing notes — one row each, edited in place. Facts are scanned.
 */
const POLICIES: { value: RepoPolicy; label: string; title: string }[] = [
  { value: 'allowed', label: 'Allowed', title: 'Helpers and Dispatch runs start here without asking' },
  { value: 'ask', label: 'Ask', title: 'Each start shows a permission card' },
  { value: 'never', label: 'Never', title: 'Nothing starts here from a lead or a job' },
];
const MODES = [
  { value: '', label: 'Session default' },
  { value: 'default', label: 'Default' },
  { value: 'acceptEdits', label: 'Accept edits' },
  { value: 'plan', label: 'Plan' },
];
const FILTER_FROM = 10;

function save(id: string, patch: Partial<RepoEntry> & { path?: string }) {
  return call<RepoEntry>('repos.save', { id, ...patch }).catch((e) => {
    toast(errText(e), 'error');
    throw e;
  });
}

function facts(e: RepoEntry) {
  const f = e.facts || {};
  const bits = [f.manifest, f.packageName, f.branch, f.dirty ? 'uncommitted changes' : '', f.hasClaudeMd ? 'CLAUDE.md' : ''].filter(Boolean);
  return bits.length ? bits.join(' · ') : f.isRepo === false ? 'not a git repository' : 'not scanned yet';
}

export function ReposPage() {
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const repos = useMemo(() => config?.repos || [], [config?.repos]);
  const [query, setQuery] = useState('');
  const [box, setBox] = useState<'add' | 'scan' | null>(null);
  const [found, setFound] = useState<RepoScanHit[] | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [scanning, setScanning] = useState('');
  useEffect(() => {
    call('repos.refresh', {}, { quiet: true }).catch(() => {});
  }, []);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return repos.filter((e) => !q || `${e.name} ${(e.aliases || []).join(' ')} ${e.path} ${e.about || ''}`.toLowerCase().includes(q));
  }, [repos, query]);

  const addPath = (path: string) =>
    call<RepoEntry>('repos.save', { path })
      .then((e) => {
        setBox(null);
        toast(`Registered ${e.name}`, 'success');
      })
      .catch((e) => toast(errText(e), 'error'));
  const scanRoot = (root: string) => {
    setScanning(root);
    call<{ found: RepoScanHit[] }>('repos.scan', { root })
      .then((r) => {
        setFound(r.found);
        setChecked(Object.fromEntries(r.found.filter((f) => !f.registered).map((f) => [f.path, true])));
      })
      .catch((e) => toast(errText(e), 'error'));
  };
  const toRegister = (found || []).filter((f) => !f.registered && checked[f.path]);
  const registerChecked = async () => {
    let ok = 0;
    for (const f of toRegister) {
      try {
        await call('repos.save', { path: f.path, name: f.name });
        ok++;
      } catch (e) {
        toast(errText(e), 'error');
      }
    }
    setBox(null);
    toast(`Registered ${ok} repositor${ok === 1 ? 'y' : 'ies'}`, ok ? 'success' : 'error');
  };

  return (
    <div className="rp">
      <div className="intro">
        <FolderGit2 size={16} />
        <div>
          <div className="intro-title">Repositories</div>
          <div className="hint">The repositories Pilcrow may work in, by the names you use for them. A lead or a job sent to “the order FE” lands in the right folder; the policy says whether it asks first; the notes travel with every brief for that repository.</div>
        </div>
      </div>
      <div className="rp-bar">
        {(repos.length >= FILTER_FROM || query) && <input className="input rp-filter" placeholder="Filter by name, alias or folder" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter repositories" />}
        <span className="faint small">{query ? `${shown.length} of ${repos.length}` : repos.length ? `${repos.length} registered` : ''}</span>
        <span className="grow" />
        <button className="btn ghost sm" onClick={() => (setFound(null), setScanning(''), setBox('scan'))}>
          <Search size={13} /> Scan a folder…
        </button>
        <button className="btn primary sm" onClick={() => setBox('add')}>
          <Plus size={13} /> Add…
        </button>
      </div>
      {box === 'add' && (
        <div className="rp-box">
          <div className="rp-box-title">Pick the repository's folder</div>
          <FolderPicker value={info?.home || ''} onPick={addPath} />
          <div className="row end">
            <button className="btn sm" onClick={() => setBox(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {box === 'scan' && (
        <div className="rp-box">
          <div className="rp-box-title">Pick a folder to scan for repositories</div>
          <FolderPicker value={info?.home || ''} onPick={scanRoot} />
          {found && found.length > 0 && (
            <div className="rp-found">
              {found.map((f) => (
                <label key={f.path} className={`rp-found-row${f.registered ? ' done' : ''}`}>
                  <input type="checkbox" disabled={f.registered} checked={f.registered || Boolean(checked[f.path])} onChange={(e) => setChecked((c) => ({ ...c, [f.path]: e.target.checked }))} />
                  <span className="rp-found-name">{f.name}</span>
                  <span className="mono faint ellipsis">{[f.packageName || f.kind, f.branch || '—', tildify(f.path, info?.home)].filter(Boolean).join(' · ')}</span>
                  {f.registered && <span className="chip tiny">registered</span>}
                </label>
              ))}
            </div>
          )}
          <div className="row">
            <span className="hint grow">
              {found ? (found.length ? `${found.filter((f) => !f.registered).length} new, ${found.filter((f) => f.registered).length} already registered` : 'No repositories under this folder.') : scanning ? `Scanning ${tildify(scanning, info?.home)}…` : ''}
            </span>
            <button className="btn sm" onClick={() => setBox(null)}>
              Cancel
            </button>
            <button className="btn primary sm" disabled={!toRegister.length} onClick={registerChecked}>
              {toRegister.length ? `Register ${toRegister.length}` : 'Register'}
            </button>
          </div>
        </div>
      )}
      {repos.length === 0 ? (
        <div className="rp-empty">
          <FolderGit2 size={22} />
          <div className="faint">No repositories registered yet.</div>
          <div className="hint">Add one, or scan a folder that holds several. A session's menu also offers “Register this repository…”.</div>
        </div>
      ) : (
        <div className="rp-list">
          {shown.map((e) => (
            <RepoRow key={e.id} e={e} all={repos} home={info?.home} />
          ))}
        </div>
      )}
    </div>
  );
}

function RepoRow({ e, all, home }: { e: RepoEntry; all: RepoEntry[]; home?: string }) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(e.name);
  const [alias, setAlias] = useState('');
  const [locating, setLocating] = useState(false);
  const [draft, setDraft] = useState({ about: e.about || '', base: e.defaults?.base || '', prepare: e.defaults?.prepare || '', verify: e.defaults?.verify || '', notes: e.notes || '' });
  useEffect(() => {
    setDraft({ about: e.about || '', base: e.defaults?.base || '', prepare: e.defaults?.prepare || '', verify: e.defaults?.verify || '', notes: e.notes || '' });
    setName(e.name);
  }, [e]);
  const d = e.defaults || {};
  const setDefault = (key: keyof RepoEntry['defaults'], value: string | boolean) => {
    const next = { ...d } as Record<string, unknown>;
    if (key === 'worktree') {
      if (value) delete next.worktree;
      else next.worktree = false;
    } else if (value) next[key] = value;
    else delete next[key];
    if (JSON.stringify(next) !== JSON.stringify(d)) save(e.id, { defaults: next as RepoEntry['defaults'] }).catch(() => {});
  };
  const commitRename = () => {
    setRenaming(false);
    const t = name.trim();
    if (t && t !== e.name) save(e.id, { name: t }).catch(() => setName(e.name));
    else setName(e.name);
  };
  const addAlias = () => {
    const a = alias.trim();
    if (!a) return;
    setAlias('');
    save(e.id, { aliases: [...(e.aliases || []), a] }).catch(() => {});
  };
  const remove = () =>
    confirmAction({
      title: `Remove ${e.name} from the registry?`,
      body: 'The folder and its files stay. Helpers and jobs will ask before working there again.',
      confirm: 'Remove',
      danger: true,
      onConfirm: () => call('repos.remove', { id: e.id }).catch((err) => toast(errText(err), 'error')),
    });
  const byId = Object.fromEntries(all.map((x) => [x.id, x]));
  const depOptions = [{ value: '', label: 'Add a dependency…' }, ...all.filter((x) => x.id !== e.id && !(e.deps || []).includes(x.id)).map((x) => ({ value: x.id, label: x.name }))];
  return (
    <div className={`rp-row${e.missing ? ' missing' : ''}`}>
      <div className="rp-head">
        <button className="icon-btn sm" title={open ? 'Hide the rest of the entry' : 'Show the rest of the entry'} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {renaming ? (
          <input className="input rp-name-edit" autoFocus value={name} aria-label="Repository name" onChange={(ev) => setName(ev.target.value)} onBlur={commitRename} onKeyDown={(ev) => (ev.key === 'Enter' ? commitRename() : ev.key === 'Escape' ? (setName(e.name), setRenaming(false)) : null)} />
        ) : (
          <button className="rp-name" title="Click to rename" onClick={() => setRenaming(true)} disabled={e.missing}>
            {e.name}
          </button>
        )}
        <span className="mono faint ellipsis rp-path" title={e.path}>
          {tildify(e.path, home)}
        </span>
        {e.missing && <span className="chip tiny warn">not found</span>}
        {e.missing && (
          <button className="btn ghost xs" onClick={() => setLocating(!locating)}>
            Locate…
          </button>
        )}
        <Seg<RepoPolicy> size="sm" value={e.policy || 'ask'} options={POLICIES} label={`Policy for ${e.name}`} onChange={(v) => save(e.id, { policy: v }).catch(() => {})} />
        <button className="icon-btn sm" title="Remove from the registry" onClick={remove}>
          <Trash size={13} />
        </button>
      </div>
      <div className="rp-aliases">
        {(e.aliases || []).map((a) => (
          <button key={a} className="chip tiny rp-alias" title={`Remove “${a}”`} onClick={() => save(e.id, { aliases: (e.aliases || []).filter((x) => x !== a) }).catch(() => {})}>
            {a} <X size={10} />
          </button>
        ))}
        <input className="input rp-alias-edit" placeholder="add a name you use for it…" value={alias} aria-label={`Add an alias for ${e.name}`} onChange={(ev) => setAlias(ev.target.value)} onKeyDown={(ev) => ev.key === 'Enter' && addAlias()} />
      </div>
      {locating && (
        <div className="rp-locate">
          <div className="rp-box-title">Where is it now?</div>
          <FolderPicker value={home || ''} onPick={(p) => save(e.id, { path: p }).then(() => setLocating(false)).catch(() => {})} />
        </div>
      )}
      {open && (
        <div className="rp-detail">
          <Field label="About" hint="Read by the lead when it decides where a task belongs.">
            <input className="input" value={draft.about} placeholder="One line: what it is, what it talks to" onChange={(ev) => setDraft({ ...draft, about: ev.target.value })} onBlur={() => draft.about.trim() !== (e.about || '') && save(e.id, { about: draft.about.trim() }).catch(() => {})} />
          </Field>
          <Field label="Mode" hint="How a helper or a run here may act, unless the lead or the job says otherwise.">
            <Select value={d.mode || ''} options={MODES} onChange={(v) => setDefault('mode', v)} />
          </Field>
          <Field label="Worktree" hint="Work on a branch in a worktree of its own, never in the checkout you use.">
            <Switch on={d.worktree !== false} onChange={(v) => setDefault('worktree', v)} label={`Worktree for ${e.name}`} />
          </Field>
          <Field label="Base branch" hint="What a worktree starts from.">
            <input className="input" value={draft.base} placeholder="the checkout's branch" onChange={(ev) => setDraft({ ...draft, base: ev.target.value })} onBlur={() => setDefault('base', draft.base.trim())} />
          </Field>
          <Field label="Prepare" hint="Runs in a fresh worktree before the agent starts.">
            <input className="input mono" value={draft.prepare} placeholder="npm ci · cp ../.env ." onChange={(ev) => setDraft({ ...draft, prepare: ev.target.value })} onBlur={() => setDefault('prepare', draft.prepare.trim())} />
          </Field>
          <Field label="Verify" hint="Runs after the agent finishes; its exit code is part of the result.">
            <input className="input mono" value={draft.verify} placeholder="npm test" onChange={(ev) => setDraft({ ...draft, verify: ev.target.value })} onBlur={() => setDefault('verify', draft.verify.trim())} />
          </Field>
          <Field label="Notes" hint="Appended to every helper brief and Dispatch brief for this repository." top>
            <textarea className="textarea" rows={3} value={draft.notes} placeholder="What every agent sent here should know — “pnpm, not npm”, “the ESLint config lives in config/eslint.cjs”." onChange={(ev) => setDraft({ ...draft, notes: ev.target.value })} onBlur={() => draft.notes.trim() !== (e.notes || '') && save(e.id, { notes: draft.notes.trim() }).catch(() => {})} />
          </Field>
          <Field label="Depends on" hint="Runs after these in a job; a helper that waits for them is told what they did.">
            <div className="rp-deps">
              {(e.deps || []).map((id) => (
                <button key={id} className="chip tiny rp-alias" title="Remove" onClick={() => save(e.id, { deps: (e.deps || []).filter((x) => x !== id) }).catch(() => {})}>
                  {byId[id]?.name || basename(id)} <X size={10} />
                </button>
              ))}
              <Select value="" options={depOptions} width={200} onChange={(v) => v && save(e.id, { deps: [...(e.deps || []), v] }).catch(() => {})} />
            </div>
          </Field>
          <Field label="Scanned" hint="">
            <div className="row">
              <span className="mono faint ellipsis grow" title={facts(e)}>
                {facts(e)}
              </span>
              <button className="icon-btn sm" title="Read the folder again" onClick={() => call('repos.refresh', { ids: [e.id], force: true }, { quiet: true }).catch(() => {})}>
                <RefreshCw size={12} />
              </button>
            </div>
          </Field>
        </div>
      )}
    </div>
  );
}

function Field({ label, hint, top, children }: { label: string; hint: string; top?: boolean; children: React.ReactNode }) {
  return (
    <div className={`rp-field${top ? ' top' : ''}`}>
      <div className="rp-field-label">
        <div>{label}</div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <div className="rp-field-control">{children}</div>
    </div>
  );
}
