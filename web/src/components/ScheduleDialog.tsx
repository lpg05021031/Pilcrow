import { useEffect, useRef, useState } from 'react';
import { CalendarClock, Folder, ChevronDown, FilePen, ShieldOff, Map as MapIcon, ShieldCheck, Check } from 'lucide-react';
import { useStore, call, toast } from '../lib/store';
import type { Cadence, Schedule, PermissionMode } from '../lib/types';
import { basename, tildify } from '../lib/format';
import { Dialog } from './ui/Dialog';
import { Popover } from './ui/Popover';
import { Select } from './ui/Select';
import { Switch } from './ui/bits';
import { FolderPicker } from './FolderPicker';
import { ModelSelect } from './ModelSelect';
import type { LucideIcon } from 'lucide-react';

const KINDS: { value: Cadence['kind']; label: string; hint: string }[] = [
  { value: 'daily', label: 'Every day', hint: 'At a time you pick' },
  { value: 'weekdays', label: 'Weekdays', hint: 'Monday to Friday' },
  { value: 'weekly', label: 'Every week', hint: 'One day of the week' },
  { value: 'hourly', label: 'Every hour', hint: 'At a minute past the hour' },
  { value: 'interval', label: 'Every few hours', hint: 'Anchored to a time of day' },
  { value: 'cron', label: 'Custom (cron)', hint: 'Five-field cron expression' },
];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** How an unattended run may act. Prompts cannot be answered, so the choice is what it may do without asking. */
const RUN_MODES: { id: PermissionMode; label: string; hint: string; icon: LucideIcon }[] = [
  { id: 'acceptEdits', label: 'Edit files', hint: 'Edits are applied; commands that would ask are skipped and reported', icon: FilePen },
  { id: 'plan', label: 'Read only', hint: 'Explores and reports; changes nothing', icon: MapIcon },
  { id: 'default', label: 'Ask-level only', hint: 'Only what your settings already allow; everything else is skipped', icon: ShieldCheck },
  { id: 'bypassPermissions', label: 'Everything', hint: 'Runs any command without asking — only for folders you trust', icon: ShieldOff },
];

export function ScheduleDialog({ spec, schedule }: { spec: { id?: string; cwd?: string }; schedule?: Schedule }) {
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const sessions = useStore((s) => s.sessions);
  const close = () => useStore.setState({ scheduleDialog: null });
  const [name, setName] = useState(schedule?.name || '');
  const [cwd, setCwd] = useState(schedule?.cwd || spec.cwd || sessions.find((s) => !s.archived)?.project || config?.recentProjects?.[0] || info?.home || '');
  const [prompt, setPrompt] = useState(schedule?.prompt || '');
  const [cadence, setCadence] = useState<Cadence>(schedule?.cadence || { kind: 'daily', time: '09:00' });
  const [mode, setMode] = useState<PermissionMode>(schedule?.mode || 'acceptEdits');
  const [model, setModel] = useState(schedule?.model || '');
  const [worktree, setWorktree] = useState(schedule?.worktree || false);
  const [maxMinutes, setMaxMinutes] = useState(schedule?.maxMinutes || 45);
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [picking, setPicking] = useState(false);
  const [more, setMore] = useState(false);
  const [preview, setPreview] = useState<{ nextRunAt: number | null; label: string; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const folderBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => call<{ nextRunAt: number | null; label: string; error?: string }>('schedules.preview', { cadence }, { quiet: true }).then((r) => alive && setPreview(r)).catch(() => {}), 150);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [cadence]);

  const save = async () => {
    if (busy) return;
    if (!name.trim()) return toast('Give the schedule a name.', 'warning');
    if (!prompt.trim()) return toast('Write what Claude should do each time.', 'warning');
    setBusy(true);
    try {
      await call('schedules.save', { id: schedule?.id, name: name.trim(), cwd, prompt, cadence, mode, model, worktree, maxMinutes, enabled });
      toast(schedule ? 'Schedule updated' : `Scheduled: ${name.trim()}`, 'success');
      close();
    } catch {
      setBusy(false);
    }
  };

  const time = cadence.time || '09:00';
  return (
    <Dialog
      title={
        <span className="row">
          <CalendarClock size={15} /> {schedule ? 'Edit schedule' : 'New schedule'}
        </span>
      }
      onClose={close}
      onSubmit={save}
      width={580}
      footer={
        <>
          <span className="hint sd-preview">{preview?.error ? <span className="st-err">{preview.error}</span> : preview?.nextRunAt ? `${preview.label} · first run ${new Date(preview.nextRunAt).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}` : ''}</span>
          <div className="grow" />
          <button className="btn" onClick={close}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={busy}>
            {schedule ? 'Save' : 'Create schedule'}
          </button>
        </>
      }
    >
      <div className="sd">
        <div className="ns-two">
          <div className="field">
            <label>Name</label>
            <input className="input" autoFocus value={name} placeholder="Nightly dependency audit" onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label>Folder</label>
            <button ref={folderBtn} className={`ns-folder sd-folder${picking ? ' on' : ''}`} onClick={() => setPicking(!picking)} title={tildify(cwd, info?.home)}>
              <Folder size={14} className="ns-folder-ico" />
              <span className="ellipsis grow" style={{ textAlign: 'left' }}>
                {basename(cwd) || 'Pick a folder'}
              </span>
              <ChevronDown size={13} className="faint" />
            </button>
            <Popover anchor={folderBtn.current} open={picking} onClose={() => setPicking(false)} placement="bottom-start" width={380}>
              <FolderPicker
                value={cwd}
                onPick={(d) => {
                  setCwd(d);
                  setPicking(false);
                }}
              />
            </Popover>
          </div>
        </div>

        <div className="field">
          <label>What Claude does each time</label>
          <textarea className="textarea ns-prompt" rows={4} value={prompt} placeholder="Run the test suite, fix any failing tests you can, and summarise what changed. Do not push." onChange={(e) => setPrompt(e.target.value)} />
          <div className="hint">Nobody is there to answer questions during a run, so say what to assume and what to leave alone.</div>
        </div>

        <div className="field">
          <label>When</label>
          <div className="sd-when">
            <Select value={cadence.kind} onChange={(kind) => setCadence({ ...cadence, kind })} options={KINDS} width={240} />
            {cadence.kind === 'weekly' && <Select value={String(cadence.weekday ?? 1)} onChange={(d) => setCadence({ ...cadence, weekday: Number(d) })} options={DAYS.map((d, i) => ({ value: String(i), label: d }))} width={170} />}
            {cadence.kind === 'interval' && (
              <label className="sd-inline">
                every
                <input className="input sd-num" type="number" min={2} max={48} value={cadence.everyHours || 6} onChange={(e) => setCadence({ ...cadence, everyHours: Math.max(2, Math.min(48, Number(e.target.value) || 6)) })} />
                hours, from
              </label>
            )}
            {cadence.kind === 'hourly' && <span className="sd-inline">at minute</span>}
            {cadence.kind !== 'cron' && cadence.kind !== 'hourly' && <span className="sd-inline">at</span>}
            {cadence.kind === 'hourly' ? (
              <input className="input sd-num" type="number" min={0} max={59} value={Number(time.split(':')[1]) || 0} onChange={(e) => setCadence({ ...cadence, time: `00:${String(Math.max(0, Math.min(59, Number(e.target.value) || 0))).padStart(2, '0')}` })} />
            ) : cadence.kind === 'cron' ? (
              <input className="input mono sd-cron" value={cadence.cron || ''} placeholder="0 9 * * 1-5" onChange={(e) => setCadence({ ...cadence, cron: e.target.value })} spellCheck={false} />
            ) : (
              <input className="input sd-time" type="time" value={time} onChange={(e) => setCadence({ ...cadence, time: e.target.value || '09:00' })} />
            )}
          </div>
        </div>

        <div className="field">
          <label>While it runs, Claude may</label>
          <div className="radio-list">
            {RUN_MODES.filter((m) => !(m.id === 'bypassPermissions' && info?.isRoot)).map((m) => (
              <button key={m.id} className={`radio-row${mode === m.id ? ' on' : ''}${m.id === 'bypassPermissions' ? ' danger' : ''}`} onClick={() => setMode(m.id)} role="radio" aria-checked={mode === m.id}>
                <m.icon size={14} className="radio-ico" />
                <span className="radio-label">{m.label}</span>
                <span className="radio-hint">{m.hint}</span>
                <span className="radio-check">{mode === m.id && <Check size={14} />}</span>
              </button>
            ))}
          </div>
        </div>

        <button className={`ns-disclose${more ? ' open' : ''}`} onClick={() => setMore(!more)} aria-expanded={more}>
          <ChevronDown size={14} className="ns-disclose-chev" />
          <span className="ns-disclose-title">More</span>
          {!more && (
            <span className="ns-disclose-summary ellipsis">
              {model ? model.replace(/^claude-/, '') : 'default model'} · stops after {maxMinutes} min{worktree ? ' · isolated worktree' : ''}
              {!enabled ? ' · paused' : ''}
            </span>
          )}
        </button>
        {more && (
          <div className="sd-more">
            <div className="ns-two">
              <div className="field">
                <label>Model</label>
                <ModelSelect value={model} onChange={(v) => setModel(v)} />
              </div>
              <div className="field">
                <label>Stop after</label>
                <label className="sd-inline">
                  <input className="input sd-num" type="number" min={1} max={240} value={maxMinutes} onChange={(e) => setMaxMinutes(Math.max(1, Math.min(240, Number(e.target.value) || 45)))} /> minutes
                </label>
              </div>
            </div>
            <div className="ns-row">
              <div className="grow">
                <div className="ns-row-title">Isolated worktree</div>
                <div className="hint">Each run gets its own branch and checkout, so it never collides with your work.</div>
              </div>
              <Switch on={worktree} onChange={setWorktree} label="Use a worktree" />
            </div>
            <div className="ns-row">
              <div className="grow">
                <div className="ns-row-title">Enabled</div>
                <div className="hint">Paused schedules keep their settings and can still be run by hand.</div>
              </div>
              <Switch on={enabled} onChange={setEnabled} label="Enabled" />
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}
