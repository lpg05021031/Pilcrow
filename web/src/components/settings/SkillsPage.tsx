import { useEffect, useMemo, useState } from 'react';
import { Plus, Search, ScrollText, FolderOpen, Pencil, Trash, Copy, RefreshCw, Puzzle, FolderInput, Ellipsis } from 'lucide-react';
import { useStore, call, openFile, confirmAction, toast, setDialog } from '../../lib/store';
import type { SkillInfo, SkillsList } from '../../lib/types';
import { tildify, timeAgo } from '../../lib/format';
import { MenuButton } from '../ui/Popover';
import { Dialog } from '../ui/Dialog';
import { Spinner, Seg } from '../ui/bits';
import { FolderPicker } from '../FolderPicker';
import { errText } from '../../lib/format';

type Scope = 'user' | 'project';

/** The folder the active session works in: project skills live there. */
function useActiveCwd() {
  const activeId = useStore((s) => s.activeId);
  const rt = useStore((s) => (activeId ? s.rt[activeId] : null));
  const session = useStore((s) => s.sessions.find((x) => x.id === activeId));
  return rt?.cwd || session?.cwd || '';
}

export function SkillsPage() {
  const cwd = useActiveCwd();
  const info = useStore((s) => s.info);
  const [data, setData] = useState<SkillsList | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState<null | Scope>(null);
  const [importing, setImporting] = useState<null | Scope>(null);

  const load = async () => {
    setLoading(true);
    try {
      setData(await call<SkillsList>('skills.list', { cwd }, { quiet: true }));
    } catch (e) {
      toast(errText(e) || 'Could not list skills', 'error');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [cwd]); // eslint-disable-line react-hooks/exhaustive-deps

  const filter = (list: SkillInfo[]) => {
    const n = q.trim().toLowerCase();
    return n ? list.filter((s) => s.name.toLowerCase().includes(n) || s.description.toLowerCase().includes(n)) : list;
  };
  const groups = useMemo(
    () =>
      data
        ? [
            { id: 'user', title: 'Personal', hint: tildify(data.roots.user, info?.home), items: filter(data.user), editable: true },
            { id: 'project', title: 'This project', hint: data.roots.project ? tildify(data.roots.project, info?.home) : 'Open a session to see project skills', items: filter(data.project), editable: Boolean(data.roots.project) },
            { id: 'plugin', title: 'From plugins', hint: 'Installed with a plugin; edit them there', items: filter(data.plugin), editable: false },
          ]
        : [],
    [data, q, info?.home], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const total = data ? data.user.length + data.project.length + data.plugin.length : 0;

  const reload = async () => {
    await load();
    const n = await call<number>('skills.reloadAll', {}, { quiet: true }).catch(() => 0);
    if (n) toast(`Skills reloaded in ${n} running session${n > 1 ? 's' : ''}`, 'success');
  };

  const remove = (s: SkillInfo) =>
    confirmAction({
      title: `Delete the skill “${s.name}”?`,
      body: `The folder ${tildify(s.dir, info?.home)} and everything in it is removed. This cannot be undone.`,
      confirm: 'Delete skill',
      danger: true,
      onConfirm: async () => {
        await call('skills.remove', { dir: s.dir, cwd });
        toast(`Deleted ${s.name}`, 'success');
        reload();
      },
    });

  const duplicate = async (s: SkillInfo, scope: Scope) => {
    try {
      const r = await call<SkillInfo>('skills.import', { scope, cwd, from: s.dir, name: s.dirName });
      toast(`Copied ${r.name} to ${scope === 'user' ? 'your personal skills' : 'this project'}`, 'success');
      reload();
    } catch {
      /* toasted */
    }
  };

  const edit = (s: SkillInfo) => {
    setDialog('settings', false);
    openFile(s.file);
  };

  return (
    <div className="cust">
      <div className="cust-bar">
        <label className="cust-search">
          <Search size={13} />
          <input placeholder="Search skills" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <div className="grow" />
        <button className="icon-btn sm" title="Reload skills in running sessions" onClick={reload}>
          {loading ? <Spinner size={13} /> : <RefreshCw size={13} />}
        </button>
        <MenuButton
          className="btn sm primary"
          placement="bottom-end"
          items={[
            { label: 'New personal skill', hint: 'For every project, in ~/.claude/skills', icon: ScrollText, onSelect: () => setCreating('user') },
            { label: 'New project skill', hint: cwd ? `In ${tildify(cwd, info?.home)}/.claude/skills` : 'Open a session first', icon: ScrollText, disabled: !cwd, onSelect: () => setCreating('project') },
            { separator: true },
            { label: 'Import a folder…', hint: 'Copy a folder that has a SKILL.md', icon: FolderInput, onSelect: () => setImporting('user') },
          ]}
        >
          <Plus size={14} /> New
        </MenuButton>
      </div>

      {data && total === 0 && !q && (
        <div className="cust-intro">
          <div className="cust-intro-title">Teach Claude how you work</div>
          <div className="cust-intro-body">
            A skill is a folder with a <code>SKILL.md</code>: a short description of when it applies and the steps to follow. Claude reads the description on every turn and opens the skill only when the task matches.
          </div>
          <div className="row">
            <button className="btn sm primary" onClick={() => setCreating('user')}>
              <Plus size={13} /> New skill
            </button>
            <button className="btn sm ghost" onClick={() => setImporting('user')}>
              <FolderInput size={13} /> Import a folder…
            </button>
          </div>
        </div>
      )}

      {groups.map((g) => (
        <section key={g.id} className="cust-group">
          <div className="cust-group-head">
            <span className="cust-group-title">{g.title}</span>
            <span className="cust-count">{g.items.length}</span>
            <span className="cust-group-hint mono ellipsis" title={g.hint}>
              {g.hint}
            </span>
          </div>
          {g.items.length === 0 ? (
            <div className="cust-empty">{q ? 'No match.' : g.id === 'plugin' ? 'No plugin skills installed.' : g.id === 'project' && !data?.roots.project ? g.hint : 'None yet.'}</div>
          ) : (
            g.items.map((s) => (
              <div key={s.dir} className="cust-row" onDoubleClick={() => edit(s)}>
                <span className="cust-ico">{s.scope === 'plugin' ? <Puzzle size={16} /> : <ScrollText size={16} />}</span>
                <span className="cust-main">
                  <span className="cust-name">
                    {s.name}
                    {s.hasScripts && <span className="chip tiny">scripts</span>}
                  </span>
                  <span className="cust-desc ellipsis" title={s.description}>
                    {s.plugin && <span className="faint">{s.plugin} · </span>}
                    {s.description || <span className="faint">No description — Claude will rarely pick this skill</span>}
                  </span>
                </span>
                <span className="cust-time faint" title={new Date(s.mtime).toLocaleString()}>
                  {timeAgo(s.mtime)}
                </span>
                <MenuButton
                  className="icon-btn sm cust-more"
                  placement="bottom-end"
                  items={[
                    { label: g.editable ? 'Edit SKILL.md' : 'Open SKILL.md', icon: Pencil, onSelect: () => edit(s) },
                    { label: 'Show folder', icon: FolderOpen, onSelect: () => call('shell.openPath', { path: s.dir }) },
                    { separator: true },
                    ...(s.scope !== 'user' ? [{ label: 'Copy to personal skills', icon: Copy, onSelect: () => duplicate(s, 'user') }] : []),
                    ...(s.scope !== 'project' && data?.roots.project ? [{ label: 'Copy to this project', icon: Copy, onSelect: () => duplicate(s, 'project') }] : []),
                    ...(g.editable ? [{ separator: true } as const, { label: 'Delete…', icon: Trash, danger: true, onSelect: () => remove(s) }] : []),
                  ]}
                >
                  <Ellipsis size={14} />
                </MenuButton>
              </div>
            ))
          )}
        </section>
      ))}

      {creating && (
        <NewSkillDialog
          scope={creating}
          cwd={cwd}
          onClose={() => setCreating(null)}
          onCreated={(s) => {
            setCreating(null);
            reload();
            edit(s);
          }}
        />
      )}
      {importing && (
        <ImportSkillDialog
          scope={importing}
          cwd={cwd}
          hasProject={Boolean(data?.roots.project)}
          onClose={() => setImporting(null)}
          onDone={() => {
            setImporting(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function NewSkillDialog({ scope, cwd, onClose, onCreated }: { scope: Scope; cwd: string; onClose: () => void; onCreated: (s: SkillInfo) => void }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [busy, setBusy] = useState(false);
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  const submit = async () => {
    if (!slug || busy) return;
    setBusy(true);
    try {
      const s = await call<SkillInfo>('skills.create', { scope, cwd, name: slug, description: desc });
      toast(`Created ${s.name}`, 'success');
      onCreated(s);
    } catch {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={scope === 'user' ? 'New personal skill' : 'New project skill'}
      onClose={onClose}
      onSubmit={submit}
      width={480}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!slug || busy} onClick={submit}>
            {busy ? <Spinner size={13} /> : 'Create and edit'}
          </button>
        </>
      }
    >
      <div className="cust-form">
        <div className="field">
          <label>Name</label>
          <input className="input" autoFocus placeholder="release-checklist" value={name} onChange={(e) => setName(e.target.value)} />
          {slug && slug !== name.trim() && <div className="hint mono">Saved as {slug}</div>}
        </div>
        <div className="field">
          <label>When should Claude use it?</label>
          <textarea className="textarea" rows={3} placeholder="Use when preparing a release: bump the version, update the changelog and tag the commit." value={desc} onChange={(e) => setDesc(e.target.value)} />
          <div className="hint">This sentence is what Claude reads to decide whether to open the skill. Be specific.</div>
        </div>
      </div>
    </Dialog>
  );
}

function ImportSkillDialog({ scope: initial, cwd, hasProject, onClose, onDone }: { scope: Scope; cwd: string; hasProject: boolean; onClose: () => void; onDone: () => void }) {
  const [scope, setScope] = useState<Scope>(initial);
  const [from, setFrom] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!from.trim() || busy) return;
    setBusy(true);
    try {
      const s = await call<SkillInfo>('skills.import', { scope, cwd, from: from.trim() });
      toast(`Imported ${s.name}`, 'success');
      onDone();
    } catch {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title="Import a skill folder"
      onClose={onClose}
      onSubmit={submit}
      width={520}
      footer={
        <>
          <Seg
            size="sm"
            value={scope}
            onChange={(v) => setScope(v)}
            options={[
              { value: 'user', label: 'Personal' },
              { value: 'project', label: 'This project', disabled: !hasProject },
            ]}
          />
          <div className="grow" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!from.trim() || busy} onClick={submit}>
            {busy ? <Spinner size={13} /> : 'Import'}
          </button>
        </>
      }
    >
      <div className="cust-form">
        <div className="hint">
          Pick the folder that contains <code>SKILL.md</code>. It is copied, so the original stays where it is.
        </div>
        <div className="cust-picker">
          <FolderPicker value={from} onPick={setFrom} />
        </div>
        {from && <div className="hint mono">{from}</div>}
      </div>
    </Dialog>
  );
}
