import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Play, Folder, ChevronDown, RefreshCw, Pencil, BookMarked } from 'lucide-react';
import { useStore, call, showDispatch, openJob, loadPlaybooks, toast, defaultCwd } from '../../lib/store';
import type { DispatchPlan, DispatchSettings, DispatchTarget, Playbook, WorkspaceRepo } from '../../lib/types';
import { basename, dirname, plural, tildify } from '../../lib/format';
import { Switch } from '../ui/bits';
import { Select } from '../ui/Select';
import { Popover } from '../ui/Popover';
import { FolderPicker } from '../FolderPicker';

const VARIABLES = '{{repo.name}} {{repo.path}} {{repo.branch}} {{job.name}} {{notes}}';
const DEFAULTS: DispatchSettings = { mode: 'acceptEdits', allowedTools: [], model: '', effort: '', parallel: 3, maxMinutes: 20, costCap: 0, canary: 1, learn: true, prepare: '', copy: [], verify: '', verifyRetry: true, commitMessage: '', pr: false, report: 'table' };

/**
 * A new job: name, brief, targets, how to run, how to finish. The plan runs as you edit and paints its
 * verdicts on the targets; Start is enabled when nothing is blocked.
 */
export function Composer() {
  const info = useStore((s) => s.info);
  const playbooks = useStore((s) => s.playbooks);
  const [name, setName] = useState('');
  const [brief, setBrief] = useState('');
  const [root, setRoot] = useState(() => dirname(defaultCwd()) || info?.home || '');
  const [repos, setRepos] = useState<WorkspaceRepo[] | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState('');
  const [picking, setPicking] = useState(false);
  const [settings, setSettings] = useState<DispatchSettings>(DEFAULTS);
  const [branch, setBranch] = useState('');
  const [branchAuto, setBranchAuto] = useState(true);
  const [plan, setPlan] = useState<DispatchPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [playbookId, setPlaybookId] = useState('');
  const [starting, setStarting] = useState(false);
  const folderBtn = useRef<HTMLButtonElement>(null);
  const planSeq = useRef(0);

  // the workspace
  useEffect(() => {
    if (!root) return;
    let alive = true;
    setRepos(null);
    call<{ repos: WorkspaceRepo[] }>('workspace.scan', { root }, { quiet: true })
      .then((r) => alive && setRepos(r.repos))
      .catch(() => alive && setRepos([]));
    return () => {
      alive = false;
    };
  }, [root]);
  const rescan = () => call<{ repos: WorkspaceRepo[] }>('workspace.scan', { root, force: true }, { quiet: true }).then((r) => setRepos(r.repos)).catch(() => {});
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (repos || []).filter((r) => !q || `${r.name} ${r.path} ${r.kind || ''} ${r.packageName || ''}`.toLowerCase().includes(q));
  }, [repos, filter]);
  const targets: DispatchTarget[] = useMemo(() => {
    const byPath = Object.fromEntries((repos || []).map((r) => [r.path, r]));
    return Object.entries(checked)
      .filter(([p, on]) => on && byPath[p])
      .map(([p]) => ({ path: p, name: byPath[p].name, base: byPath[p].branch || '', notes: notes[p] || '', size: byPath[p].size, deps: byPath[p].deps }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [checked, repos, notes]);
  const selected = Object.values(checked).filter(Boolean).length;

  // the plan, a moment after the last change
  const values = () => ({ playbookId: playbookId || null, name: name.trim(), brief, root, targets, settings: { ...settings, branch: branch.startsWith('pilcrow/') ? branch.slice('pilcrow/'.length) : branch } });
  useEffect(() => {
    if (!targets.length) {
      setPlan(null);
      return;
    }
    setPlanning(true);
    const seq = ++planSeq.current;
    const t = setTimeout(() => {
      call<DispatchPlan>('dispatch.plan', values(), { quiet: true })
        .then((p) => {
          if (seq !== planSeq.current) return;
          setPlan(p);
          setPlanning(false);
        })
        .catch(() => seq === planSeq.current && setPlanning(false));
    }, 600);
    return () => clearTimeout(t);
  }, [name, brief, root, targets, settings, branch]); // eslint-disable-line react-hooks/exhaustive-deps -- values() reads the same state
  const verdicts = useMemo(() => Object.fromEntries((plan?.targets || []).map((t) => [t.path, t])), [plan]);
  const canStart = Boolean(plan && plan.targets.length && !plan.blocked && brief.trim()) && !starting;

  const onName = (v: string) => {
    setName(v);
    if (branchAuto) {
      const slug = v
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '')
        .slice(0, 40);
      setBranch(slug ? `pilcrow/${slug}` : '');
    }
  };
  const loadPlaybook = (pb: Playbook) => {
    setPlaybookId(pb.id);
    if (!name.trim()) onName(pb.name);
    setBrief(pb.brief);
    setSettings({ ...DEFAULTS, ...pb.settings });
    setNotes(Object.fromEntries(pb.targets.filter((t) => t.notes).map((t) => [t.path, t.notes])));
    setChecked(Object.fromEntries(pb.targets.map((t) => [t.path, true])));
    if (pb.root && pb.root !== root) setRoot(pb.root);
  };
  const savePlaybook = async () => {
    if (!brief.trim()) return toast('Write the brief first.', 'warning');
    const v = values();
    const pb = await call<Playbook>('dispatch.savePlaybook', { ...(playbookId ? { id: playbookId } : {}), name: v.name || brief.trim().split('\n')[0].slice(0, 60) || 'Untitled playbook', brief, root, targets, settings: v.settings }).catch(() => null);
    if (pb) {
      setPlaybookId(pb.id);
      loadPlaybooks();
      toast(`Saved as playbook: ${pb.name}`, 'success');
    }
  };
  const start = async () => {
    if (!canStart) return;
    setStarting(true);
    try {
      const v = values();
      const job = await call<{ id: string }>('dispatch.save', { ...v, name: v.name || brief.trim().split('\n')[0].slice(0, 60) || 'Untitled job' });
      try {
        await call('dispatch.start', { id: job.id });
      } finally {
        openJob(job.id);
      }
    } catch {
      setStarting(false);
    }
  };
  const set = <K extends keyof DispatchSettings>(k: K, v: DispatchSettings[K]) => setSettings((s) => ({ ...s, [k]: v }));
  const planRows = (plan?.targets || [])
    .flatMap((t) => (t.notes || []).filter((n) => n.kind !== 'note' || /Runs after/.test(n.text)).map((n) => ({ t, n })))
    .sort((a, b) => ({ blocked: 0, warning: 1, note: 2 })[a.n.kind] - ({ blocked: 0, warning: 1, note: 2 })[b.n.kind]);

  return (
    <div className="dispatch">
      <header className="ov-head">
        <button className="icon-btn" title="Back to the board" aria-label="Back" onClick={() => showDispatch(null)}>
          <ArrowLeft size={16} />
        </button>
        <h1 className="ov-title">New job</h1>
        <div className="grow" />
        <Select
          className="ghost-select"
          value={playbookId}
          options={[{ value: '', label: 'No playbook', hint: 'Start from a blank brief' }, ...playbooks.map((p) => ({ value: p.id, label: p.name, hint: `${plural(p.targets.length, 'repository', 'repositories')} · used ${p.uses}×` }))]}
          onChange={(v) => {
            const pb = playbooks.find((p) => p.id === v);
            if (pb) loadPlaybook(pb);
            else setPlaybookId('');
          }}
          width={300}
        />
        <button className="btn sm ghost" onClick={savePlaybook}>
          <BookMarked size={13} /> Save as playbook
        </button>
        <button className="btn sm primary" onClick={start} disabled={!canStart}>
          <Play size={13} /> Start
        </button>
      </header>
      <div className="ov-body dp-body dp-form">
        <div className="field">
          <label>Name</label>
          <input className="input" value={name} placeholder="Bump eslint to v9" onChange={(e) => onName(e.target.value)} />
        </div>
        <div className="field">
          <label>Brief</label>
          <textarea className="textarea" rows={6} value={brief} placeholder="Upgrade eslint to v9 with the flat config. Keep the rules we have; drop the deprecated ones. Run the linter and fix what it reports. Repository: {{repo.name}} on {{repo.branch}}. {{notes}}" onChange={(e) => setBrief(e.target.value)} />
          <div className="hint">Variables: {VARIABLES}. Each repository gets its own copy; a target’s notes land in {'{{notes}}'}.</div>
        </div>
        <div className="field">
          <label>Targets</label>
          <div className="dp-targets-top">
            <button ref={folderBtn} className={`ns-folder sd-folder dp-folder${picking ? ' on' : ''}`} onClick={() => setPicking(!picking)} title={tildify(root, info?.home)}>
              <Folder size={14} className="ns-folder-ico" />
              <span className="ellipsis grow" style={{ textAlign: 'left' }}>
                {basename(root) || 'Pick a folder'}
              </span>
              <ChevronDown size={13} className="faint" />
            </button>
            <Popover anchor={folderBtn.current} open={picking} onClose={() => setPicking(false)} placement="bottom-start" width={380}>
              <FolderPicker
                value={root}
                onPick={(d) => {
                  setRoot(d);
                  setPicking(false);
                }}
              />
            </Popover>
            <button className="icon-btn" title="Scan the folder again" onClick={rescan}>
              <RefreshCw size={14} />
            </button>
            <input className="input grow" value={filter} placeholder="Filter by name, path or kind" onChange={(e) => setFilter(e.target.value)} />
            <span className="faint tnum">{repos ? `${selected} of ${repos.length} selected` : ''}</span>
            <button className="btn xs ghost" onClick={() => setChecked((c) => ({ ...c, ...Object.fromEntries(shown.map((r) => [r.path, true])) }))}>
              All
            </button>
            <button className="btn xs ghost" onClick={() => setChecked((c) => ({ ...c, ...Object.fromEntries(shown.map((r) => [r.path, false])) }))}>
              None
            </button>
          </div>
          <div className="dp-targets">
            {repos === null && <div className="hint">Scanning…</div>}
            {repos && !repos.length && <div className="hint">No repositories under this folder. Pick another.</div>}
            {shown.map((r) => (
              <TargetRow key={r.path} repo={r} checked={Boolean(checked[r.path])} notes={notes[r.path] || ''} verdict={verdicts[r.path]} onToggle={() => setChecked((c) => ({ ...c, [r.path]: !c[r.path] }))} onNotes={(v) => setNotes((n) => ({ ...n, [r.path]: v }))} />
            ))}
          </div>
        </div>
        <div className="field">
          <label>Run</label>
          <div className="dp-inline">
            <Select value={settings.mode} options={[{ value: 'acceptEdits', label: 'Allow edits in the worktree' }, { value: 'ask', label: 'Ask for everything' }]} onChange={(v) => set('mode', v)} width={260} />
            <input className="input dp-model" value={settings.model} placeholder="default model" onChange={(e) => set('model', e.target.value)} />
          </div>
          <div className="dp-inline">
            <span className="sd-inline">parallel</span>
            <input className="input sd-num" type="number" min={1} max={16} value={settings.parallel} onChange={(e) => set('parallel', Math.max(1, Math.min(16, Number(e.target.value) || 3)))} />
            <span className="sd-inline">minutes each</span>
            <input className="input sd-num" type="number" min={1} max={240} value={settings.maxMinutes} onChange={(e) => set('maxMinutes', Math.max(1, Math.min(240, Number(e.target.value) || 20)))} />
            <span className="sd-inline">cost cap $</span>
            <input className="input sd-num" type="number" min={0} value={settings.costCap} onChange={(e) => set('costCap', Math.max(0, Number(e.target.value) || 0))} />
            <span className="sd-inline">canary</span>
            <input className="input sd-num" type="number" min={0} max={8} value={settings.canary} onChange={(e) => set('canary', Math.max(0, Math.min(8, Number(e.target.value) || 0)))} />
          </div>
          <div className="dp-inline">
            <Switch on={settings.learn} onChange={(v) => set('learn', v)} label="the rest learn from the canary" />
            <span className="faint">the rest learn from the canary</span>
          </div>
        </div>
        <div className="ns-two">
          <div className="field">
            <label>Prepare (before the agent, in the worktree)</label>
            <input className="input mono" value={settings.prepare} placeholder="npm ci" onChange={(e) => set('prepare', e.target.value)} />
          </div>
          <div className="field">
            <label>Verify (after the agent)</label>
            <input className="input mono" value={settings.verify} placeholder="npm test" onChange={(e) => set('verify', e.target.value)} />
          </div>
        </div>
        <div className="dp-inline">
          <Switch on={settings.verifyRetry} onChange={(v) => set('verifyRetry', v)} label="send a failing verify back to the agent once" />
          <span className="faint">send a failing verify back to the agent once</span>
        </div>
        <div className="field">
          <label>Finish</label>
          <div className="ns-two">
            <div className="field">
              <label>Branch in every repository</label>
              <input
                className="input mono"
                value={branch}
                placeholder="pilcrow/<job>"
                onChange={(e) => {
                  setBranch(e.target.value);
                  setBranchAuto(false);
                }}
              />
            </div>
            <div className="field">
              <label>Commit message</label>
              <input className="input" value={settings.commitMessage} placeholder="chore: {{job.name}}" onChange={(e) => set('commitMessage', e.target.value)} />
            </div>
          </div>
          <div className="dp-inline">
            <Select value={settings.report} options={[{ value: 'table', label: 'The table of repositories', hint: 'What each run did, landed and cost' }, { value: 'agent', label: 'The table, with prose written by an agent', hint: 'One more session reads the summaries and writes three paragraphs' }]} onChange={(v) => set('report', v)} width={360} />
            <Switch on={settings.pr} onChange={(v) => set('pr', v)} label="open pull requests with gh after committing" />
            <span className="faint">open pull requests with gh after committing</span>
          </div>
        </div>
        <div className="dp-box dp-plan">
          <div className="dp-box-h">
            <b>Plan</b>
            <span className="faint">{plan ? `${plan.ok} ok${plan.warnings ? ` · ${plural(plan.warnings, 'warning')}` : ''}${plan.blocked ? ` · ${plan.blocked} blocked` : ''}` : ''}</span>
            {planning && <span className="faint">planning…</span>}
            <div className="grow" />
            {plan && (
              <span className="faint tnum">
                worst case {plural(plan.worst.runs, 'run')} · {plan.worst.minutes} min{plan.worst.cost ? ` · $${Math.round(plan.worst.cost)} cap` : ''}
                {plan.worst.levels > 1 ? ` · ${plan.worst.levels} levels` : ''}
              </span>
            )}
          </div>
          {!plan && !planning && <div className="hint">Pick repositories and write the brief; the plan appears here as you type.</div>}
          {planRows.map(({ t, n }, i) => (
            <div key={i} className="dp-plan-row">
              <span className={`dp-plan-tag ${n.kind}`}>{n.kind === 'note' ? 'order' : n.kind}</span>
              <span>
                {t.name} · {n.text}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TargetRow({ repo, checked, notes, verdict, onToggle, onNotes }: { repo: WorkspaceRepo; checked: boolean; notes: string; verdict?: DispatchPlan['targets'][number]; onToggle: () => void; onNotes: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const deps = repo.deps.map((d) => basename(d));
  const chips: { label: string; tone: string }[] = [];
  if (verdict && checked) {
    const first = verdict.notes.find((n) => n.kind === verdict.verdict);
    chips.push({ label: verdict.verdict === 'ok' || !first ? verdict.verdict : `${verdict.verdict} · ${first.text.split('.')[0].slice(0, 40)}`, tone: verdict.verdict });
    if (verdict.level > 0) chips.push({ label: `after ${deps.join(', ')}`, tone: '' });
  } else {
    if (repo.dirty) chips.push({ label: `${repo.changed + repo.untracked} uncommitted`, tone: 'warning' });
    if (deps.length) chips.push({ label: `depends on ${deps.join(', ')}`, tone: '' });
    if (repo.kind) chips.push({ label: repo.kind, tone: '' });
    if (notes) chips.push({ label: 'notes', tone: 'info' });
  }
  return (
    <div className={`dp-target${checked ? ' on' : ''}`}>
      <label className="dp-target-main">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span className="dp-target-name">{repo.name}</span>
        <span className="mono faint">{repo.branch || '—'}</span>
        <span className="grow" />
        {chips.map((c, i) => (
          <span key={i} className={`dp-chip ${c.tone}`}>
            {c.label}
          </span>
        ))}
      </label>
      <button className="icon-btn sm" title="Notes for this repository (reach the brief as {{notes}})" onClick={() => setOpen(!open)}>
        <Pencil size={12} />
      </button>
      {open && <input className="input dp-notes" autoFocus value={notes} placeholder="Notes for this repository, appended to its brief" onChange={(e) => onNotes(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && setOpen(false)} />}
    </div>
  );
}
