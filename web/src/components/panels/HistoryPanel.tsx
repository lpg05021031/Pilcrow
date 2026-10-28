import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { History, ChevronRight, Undo2, RotateCcw, ArrowUpRight, ArrowDownToLine } from 'lucide-react';
import { useStore, call, setTimeline, confirmAction, toast, bumpFs, openFile } from '../../lib/store';
import type { DiffFile, SessionSummary, TimelineStep } from '../../lib/types';
import { basename, dirname, plural, duration } from '../../lib/format';
import { DiffView } from '../Code';
import { Collapse } from '../ui/Collapse';
import { revealItem } from '../Transcript';
import { Spinner, useNow, Mark } from '../ui/bits';

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
const clockS = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

function stepTitle(s: TimelineStep) {
  switch (s.kind) {
    case 'message':
      return s.label || 'Message sent';
    case 'start':
      return s.label || 'Session started';
    case 'external':
      return 'Changed outside the conversation';
    case 'restore':
      return s.label || 'Restored';
    default:
      return s.label || s.tool || 'Step';
  }
}

/**
 * The time machine: every step that could have changed files is a point on the scrubber at the top;
 * the list under it is the same timeline in words. Pick a point to see what that step changed and
 * what has changed since, and put files back the way they were there.
 */
export function HistoryPanel({ session, cwd }: { session: SessionSummary; cwd: string }) {
  const id = session.id;
  const steps = useStore((s) => s.timeline[id]);
  const rt = useStore((s) => s.rt[id]);
  const [meta, setMeta] = useState<{ enabled: boolean; reason: string | null } | null>(null);
  const [sel, setSel] = useState<number | null>(null); // step n; null = now
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const now = useNow(30_000, true);

  useEffect(() => {
    let alive = true;
    call<{ enabled: boolean; reason: string | null; steps: TimelineStep[] }>('timeline.steps', { id }, { quiet: true })
      .then((r) => {
        if (!alive) return;
        setMeta({ enabled: r.enabled, reason: r.reason });
        setTimeline(id, r.steps);
      })
      .catch((e) => alive && setMeta({ enabled: false, reason: e.message }));
    return () => {
      alive = false;
    };
  }, [id]);

  const list = steps || [];
  const selected = sel === null ? null : list.find((s) => s.n === sel) || null;
  const changing = list.filter((s) => s.changed);
  // "Now" follows the newest step until the person picks one.
  useEffect(() => {
    if (follow) setSel(null);
  }, [list.length, follow]);

  const pick = useCallback((n: number | null) => {
    setSel(n);
    setFollow(n === null);
  }, []);

  const move = (d: number) => {
    if (!list.length) return;
    const idx = sel === null ? list.length : list.findIndex((s) => s.n === sel);
    const next = Math.max(0, Math.min(list.length, idx + d));
    pick(next >= list.length ? null : list[next].n);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'End') {
      e.preventDefault();
      pick(null);
    } else if (e.key === 'Home' && list.length) {
      e.preventDefault();
      pick(list[0].n);
    }
  };

  // Scroll the chosen step into view in the list.
  useEffect(() => {
    const el = box.current?.querySelector<HTMLElement>(sel === null ? '.tl-now' : `[data-step="${sel}"]`);
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [sel]);

  const first = list[0]?.at;
  const span = first ? (list[list.length - 1]?.at || first) - first : 0;

  if (meta && !meta.enabled) {
    return (
      <div className="history">
        <div className="empty">
          <History size={22} />
          <div>The timeline is off for this folder.</div>
          <div className="hint">{meta.reason === 'home folder' || meta.reason === 'root of a drive' || meta.reason === 'too close to the root' ? `Snapshots are not taken for ${meta.reason === 'home folder' ? 'your home folder' : 'a folder this close to the root'} — it would be too large. Start the session in a project folder.` : meta.reason || 'Turn it on in Settings → General.'}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="history" ref={box} tabIndex={0} onKeyDown={onKey}>
      <div className="panel-toolbar">
        <span className="changes-scope">Timeline</span>
        {list.length > 0 && (
          <span className="faint tnum">
            {plural(changing.length, 'change')} · {span > 60_000 ? duration(span) : 'just started'}
          </span>
        )}
        <div className="grow" />
        {sel !== null && (
          <button className="btn sm ghost" onClick={() => pick(null)} title="Back to the present (End)">
            <ArrowDownToLine size={13} /> Now
          </button>
        )}
      </div>

      {!steps && (
        <div className="changes-loading">
          {[0, 1, 2].map((k) => (
            <div key={k} className="sk-file">
              <div className="sk" style={{ width: `${46 - k * 9}%` }} />
              <div className="sk short" />
            </div>
          ))}
        </div>
      )}

      {steps && !list.length && (
        <div className="empty">
          <History size={22} />
          <div>Nothing on the timeline yet.</div>
          <div className="hint">Each step that can change files — an edit, a command, a message — becomes a point here. Pick any point to see what changed and put files back the way they were.</div>
        </div>
      )}

      {list.length > 0 && (
        <>
          <Scrubber steps={list} sel={sel} onPick={pick} now={now} />
          <div className="tl-list">
            {list.map((s, i) => (
              <StepRow key={s.n} step={s} sessionId={id} cwd={cwd} selected={sel === s.n} isLast={i === list.length - 1} onPick={() => pick(sel === s.n ? null : s.n)} />
            ))}
            <div className={`tl-row tl-now${sel === null ? ' on' : ''}`} onClick={() => pick(null)} role="button">
              <span className="tl-rail" aria-hidden="true">
                <Mark size={14} className="tl-now-mark" />
              </span>
              <span className="tl-main">
                <span className="tl-title">Now</span>
                <span className="tl-sub">{rt?.status === 'running' || rt?.status === 'requires_action' ? 'Claude is working — new steps appear as they happen' : 'The folder as it is right now'}</span>
              </span>
            </div>
          </div>
          {selected && <SinceBar step={selected} sessionId={id} />}
        </>
      )}
    </div>
  );
}

/** The scrubber: one tick per step, taller for messages; drag or click to pick a point. */
function Scrubber({ steps, sel, onPick, now }: { steps: TimelineStep[]; sel: number | null; onPick: (n: number | null) => void; now: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const n = steps.length; // positions 0..n-1 are steps, n is "now"
  // Ticks sit where the step happened in time; "now" is the right edge. Steps closer together than
  // a few pixels are nudged apart so each one can still be picked.
  const t0 = steps[0]?.at || now;
  const span = Math.max(1, now - t0);
  // Long pauses (Claude waiting on you, or you away) are squeezed so the busy stretches keep their
  // shape: each gap counts at most a few times the typical gap. The ends stay exact.
  const pos = useMemo(() => {
    const times = [...steps.map((s) => s.at), now];
    const gaps = times.slice(1).map((t, i) => Math.max(0, t - times[i]));
    const sorted = [...gaps].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)] || 1000;
    const cap = Math.max(median * 4, 20_000);
    const w = gaps.map((g) => Math.min(g, cap));
    const total = Math.max(1, w.reduce((a, b) => a + b, 0));
    const raw: number[] = [];
    let acc = 0;
    for (let i = 0; i < steps.length; i++) {
      raw.push((acc / total) * 96);
      acc += w[i];
    }
    const minGap = ref.current ? (6 / Math.max(1, ref.current.clientWidth - 40)) * 100 : 1.2;
    for (let i = 1; i < raw.length; i++) if (raw[i] < raw[i - 1] + minGap) raw[i] = raw[i - 1] + minGap;
    const over = raw[raw.length - 1] - 96;
    if (over > 0) for (let i = 0; i < raw.length; i++) raw[i] = Math.max(0, raw[i] - over * (i / Math.max(1, raw.length - 1)));
    return raw;
  }, [steps, Math.floor(now / 15_000)]); // eslint-disable-line react-hooks/exhaustive-deps
  const xAt = (i: number) => (i >= n ? 100 : pos[i] ?? 0);
  const nearest = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    const pad = 20;
    const x = Math.max(0, Math.min(100, ((clientX - r.left - pad) / (r.width - pad * 2)) * 100));
    let best = n;
    let dist = Math.abs(100 - x);
    for (let i = 0; i < n; i++) {
      const d = Math.abs(xAt(i) - x);
      if (d < dist) {
        dist = d;
        best = i;
      }
    }
    return best;
  };
  const [drag, setDrag] = useState(false);
  const onDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    setDrag(true);
    const i = nearest(e.clientX);
    onPick(i >= n ? null : steps[i].n);
    const moveH = (ev: PointerEvent) => {
      const j = nearest(ev.clientX);
      onPick(j >= n ? null : steps[j].n);
    };
    const up = () => {
      setDrag(false);
      window.removeEventListener('pointermove', moveH);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', moveH);
    window.addEventListener('pointerup', up);
  };
  const selIdx = sel === null ? n : steps.findIndex((s) => s.n === sel);
  const hi = hover === null ? selIdx : hover;
  const hiStep = hi >= n ? null : steps[hi];
  const label = hiStep ? `${clockS(hiStep.at)} · ${stepTitle(hiStep)}` : 'Now';
  const first = steps[0].at;
  return (
    <div className={`tl-scrub${drag ? ' dragging' : ''}`} ref={ref} onPointerDown={onDown} onPointerMove={(e) => setHover(nearest(e.clientX))} onPointerLeave={() => setHover(null)} role="slider" aria-valuemin={0} aria-valuemax={n} aria-valuenow={selIdx} aria-label="Timeline">
      <div className={`tl-scrub-label ellipsis${xAt(hi) > 80 ? ' edge-r' : xAt(hi) < 20 ? ' edge-l' : ''}`} style={{ left: `calc(20px + (100% - 40px) * ${xAt(hi) / 100})` }}>
        {label}
      </div>
      <div className="tl-scrub-track">
        <div className="tl-scrub-done" style={{ width: `${xAt(selIdx)}%` }} />
        {steps.map((s, i) => (
          <span key={s.n} className={`tl-tick ${s.kind}${s.changed ? ' changed' : ''}${i === selIdx ? ' sel' : ''}${i <= selIdx ? ' past' : ''}`} style={{ left: `${xAt(i)}%` }} />
        ))}
        <span className={`tl-tick now${selIdx >= n ? ' sel' : ''}`} style={{ left: '100%' }} />
        <span className="tl-cursor" style={{ left: `${xAt(selIdx)}%` }} />
      </div>
      <div className="tl-scrub-axis tnum">
        <span>{clock(first)}</span>
        {span > 4 * 60e3 && <span className="tl-axis-note">pauses shortened</span>}
        <span>now · {clock(now)}</span>
      </div>
    </div>
  );
}

function StepRow({ step, sessionId, cwd, selected, isLast, onPick }: { step: TimelineStep; sessionId: string; cwd: string; selected: boolean; isLast: boolean; onPick: () => void }) {
  const sub = step.kind === 'tool' ? step.tool : step.kind === 'external' ? plural(step.files.length, 'file') + ' changed between turns' : step.kind === 'message' ? 'You' : step.kind === 'start' ? 'Claude Code' : 'Timeline';
  const jump = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const el = step.toolUseId ? await revealItem(sessionId, `tool-${step.toolUseId}`, step.toolUseId) : step.uuid ? await revealItem(sessionId, `msg-${step.uuid}`, step.uuid) : null;
    if (!el) return toast('That step is not in the conversation view.', 'info');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1400);
  };
  return (
    <div className={`tl-row ${step.kind}${selected ? ' on' : ''}${step.changed ? ' changed' : ''}`} data-step={step.n}>
      <button className="tl-row-btn" onClick={onPick} aria-expanded={selected}>
        <span className="tl-rail" aria-hidden="true">
          <span className={`tl-dot${step.changed ? ' changed' : ''}`} />
          {!isLast && <span className="tl-line" />}
        </span>
        <span className="tl-main">
          <span className="tl-title ellipsis">{stepTitle(step)}</span>
          <span className="tl-sub">
            <span>{sub}</span>
            <span className="tnum">{clockS(step.at)}</span>
          </span>
        </span>
        {step.changed ? (
          <span className="tl-stat tnum">
            <span className={`num-add${step.add ? '' : ' zero'}`}>+{step.add}</span>
            <span className={`num-del${step.del ? '' : ' zero'}`}>−{step.del}</span>
          </span>
        ) : (
          <span className="tl-stat faint">—</span>
        )}
        {(step.toolUseId || step.uuid) && (
          <span className="icon-btn sm tl-jump" title="Show in the conversation" onClick={jump} role="button">
            <ArrowUpRight size={13} />
          </span>
        )}
      </button>
      <Collapse open={selected}>{() => <StepDetail step={step} sessionId={sessionId} cwd={cwd} />}</Collapse>
    </div>
  );
}

function StepDetail({ step, sessionId, cwd }: { step: TimelineStep; sessionId: string; cwd: string }) {
  const [files, setFiles] = useState<DiffFile[] | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    let alive = true;
    if (!step.changed) {
      setFiles([]);
      return;
    }
    call<{ files: DiffFile[] }>('timeline.stepDiff', { id: sessionId, n: step.n }, { quiet: true })
      .then((r) => alive && setFiles(r.files))
      .catch(() => alive && setFiles([]));
    return () => {
      alive = false;
    };
  }, [sessionId, step.n, step.changed]);

  const restoreFile = (path: string) =>
    confirmAction({
      title: `Put ${basename(path)} back to this point?`,
      body: `The file goes back to how it was right after this step. A safety point is recorded first, so you can undo this from the timeline.`,
      confirm: 'Restore file',
      onConfirm: () => doRestore(sessionId, step, [path]),
    });

  if (!step.changed)
    return (
      <div className="tl-detail">
        <div className="tl-note faint">{step.kind === 'message' ? 'Nothing had changed on disk when this message went out.' : step.kind === 'start' ? 'This is where the timeline begins.' : 'No files changed in this step.'}</div>
        <RestoreAll step={step} sessionId={sessionId} />
      </div>
    );
  return (
    <div className="tl-detail">
      <div className="tl-detail-head">
        <span className="tool-label">Changed in this step</span>
        <RestoreAll step={step} sessionId={sessionId} />
      </div>
      {!files && (
        <div className="tl-note faint">
          <Spinner size={12} /> Loading the diff…
        </div>
      )}
      {files?.map((f) => {
        const isOpen = open[f.path] ?? files.length <= 3;
        return (
          <div key={f.path} className={`cfile tl-file${isOpen ? ' open' : ''}`}>
            <div className="cfile-head">
              <button className="cfile-toggle" onClick={() => setOpen({ ...open, [f.path]: !isOpen })}>
                <ChevronRight size={13} className={isOpen ? 'rot' : ''} />
                <span className={`fstat s-${f.status}`}>{f.status}</span>
                <span className="cfile-name">{basename(f.path)}</span>
                <span className="cfile-dir faint">{f.path.includes('/') ? dirname(f.path) : ''}</span>
              </button>
              <span className={`num-add${f.additions ? '' : ' zero'}`}>+{f.additions}</span>
              <span className={`num-del${f.deletions ? '' : ' zero'}`}>−{f.deletions}</span>
              <button className="icon-btn sm" title="Open the file as it is now" onClick={() => openFile(`${cwd}/${f.path}`)}>
                <ArrowUpRight size={13} />
              </button>
              <button className="icon-btn sm" title="Put this file back to how it was after this step" onClick={() => restoreFile(f.path)}>
                <Undo2 size={13} />
              </button>
            </div>
            <Collapse open={isOpen}>
              {() => (
                <div className="cfile-body">
                  {f.binary ? <div className="faint cfile-note">Binary file</div> : f.truncated && !f.hunks.length ? <div className="faint cfile-note">This diff is too large to show here.</div> : <DiffView hunks={f.hunks} path={f.path} mode="unified" wrap dual maxRows={400} />}
                </div>
              )}
            </Collapse>
          </div>
        );
      })}
    </div>
  );
}

function RestoreAll({ step, sessionId }: { step: TimelineStep; sessionId: string }) {
  const [busy, setBusy] = useState(false);
  const onClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await call<{ files: DiffFile[] }>('timeline.diff', { id: sessionId, from: step.n }, { quiet: true });
      const files = r.files.map((f) => f.path);
      if (!files.length) return toast('The folder already matches this point.', 'info');
      confirmAction({
        title: 'Restore everything to this point?',
        body: `${files.length === 1 ? 'One file goes' : `${files.length} files go`} back to how ${files.length === 1 ? 'it was' : 'they were'} right after this step. A safety point is recorded first, so you can undo this from the timeline. The conversation stays as it is.`,
        list: files,
        confirm: 'Restore everything',
        danger: true,
        onConfirm: () => doRestore(sessionId, step),
      });
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className="btn xs" onClick={onClick} disabled={busy} title="Put every file back to how it was right after this step">
      {busy ? <Spinner size={11} /> : <RotateCcw size={11} />} Restore to here
    </button>
  );
}

async function doRestore(sessionId: string, step: TimelineStep, paths?: string[]) {
  try {
    const r = await call<{ ok: boolean; safety: TimelineStep | null; step: TimelineStep | null }>('timeline.restore', { id: sessionId, n: step.n, paths });
    bumpFs();
    const undo = r.safety
      ? {
          label: 'Undo',
          run: () =>
            call('timeline.restore', { id: sessionId, n: r.safety!.n })
              .then(() => (bumpFs(), toast('Put the files back', 'success')))
              .catch(() => {}),
        }
      : undefined;
    toast(paths?.length ? `Restored ${paths.length === 1 ? basename(paths[0]) : plural(paths.length, 'file')}` : `Restored the folder to step ${step.n}`, 'success', undo);
  } catch {
    /* the call already showed an error */
  }
}

/** The footer while a past step is picked: how far the folder has moved since then. */
function SinceBar({ step, sessionId }: { step: TimelineStep; sessionId: string }) {
  const steps = useStore((s) => s.timeline[sessionId]);
  const later = useMemo(() => (steps || []).filter((s) => s.n > step.n && s.changed), [steps, step.n]);
  const files = useMemo(() => {
    const m = new Map<string, { add: number; del: number }>();
    for (const s of later) for (const f of s.files) {
      const cur = m.get(f.path) || { add: 0, del: 0 };
      m.set(f.path, { add: cur.add + f.add, del: cur.del + f.del });
    }
    return m;
  }, [later]);
  const [open, setOpen] = useState(false);
  if (!later.length) return <div className="tl-since faint">Nothing has changed since this point.</div>;
  const add = [...files.values()].reduce((s, f) => s + f.add, 0);
  const del = [...files.values()].reduce((s, f) => s + f.del, 0);
  return (
    <div className="tl-since">
      <button className="tl-since-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={12} className={`tool-chev${open ? ' open' : ''}`} />
        <span>
          Since this point: {plural(later.length, 'step')} changed {plural(files.size, 'file')}
        </span>
        <span className="tnum tl-stat">
          <span className="num-add">+{add}</span>
          <span className="num-del">−{del}</span>
        </span>
      </button>
      <Collapse open={open}>
        {() => (
          <div className="tl-since-files">
            {[...files.entries()].map(([p, s]) => (
              <div key={p} className="tl-since-file">
                <span className="ellipsis mono">{p}</span>
                <span className="tnum tl-stat">
                  <span className="num-add">+{s.add}</span>
                  <span className="num-del">−{s.del}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Collapse>
    </div>
  );
}
