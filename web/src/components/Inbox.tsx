import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Play, Ellipsis, Pencil, Trash, CircleCheck, CircleAlert, CircleStop, ArrowUpRight, RotateCw, X, PanelLeftOpen, Check, MailOpen, Pause } from 'lucide-react';
import { useStore, call, openSession, setUi, toast, confirmAction } from '../lib/store';
import type { InboxItem, Schedule } from '../lib/types';
import { basename, cost, duration, timeAgo, plural, tildify } from '../lib/format';
import { MenuButton } from './ui/Popover';
import { Switch, Spinner, useNow, Mark } from './ui/bits';
import { ScheduleDialog } from './ScheduleDialog';

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
function dayLabel(ms: number, now: number) {
  const d = new Date(ms);
  const t = new Date(now);
  const same = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, t)) return 'Today';
  const y = new Date(t);
  y.setDate(t.getDate() - 1);
  if (same(d, y)) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}
function until(ms: number, now: number) {
  const s = Math.max(0, Math.round((ms - now) / 1000));
  if (s < 60) return 'in under a minute';
  const m = Math.round(s / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `in ${h} h${m % 60 ? ` ${m % 60} min` : ''}`;
  const d = Math.round((ms - now) / 86400e3);
  return `${d === 1 ? 'tomorrow' : `in ${d} days`} at ${clock(ms)}`;
}

/**
 * The inbox: what each scheduled run produced, newest first, under day headings that stay put as
 * you scroll, with the schedules themselves in a rail on the right. Runs open as ordinary sessions,
 * so a result can be continued by hand.
 */
export function InboxPage() {
  const rev = useStore((s) => s.schedRev);
  const sidebar = useStore((s) => s.ui.sidebar);
  const dialog = useStore((s) => s.scheduleDialog);
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const now = useNow(30_000, true);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    call<{ items: InboxItem[] }>('inbox.list', {}, { quiet: true }).then((r) => alive && setItems(r.items)).catch(() => alive && setItems([]));
    call<Schedule[]>('schedules.list', {}, { quiet: true }).then((r) => alive && setSchedules(r)).catch(() => alive && setSchedules([]));
    return () => {
      alive = false;
    };
  }, [rev]);

  const shown = useMemo(() => (items || []).filter((i) => !filter || i.scheduleId === filter), [items, filter]);
  const unread = (items || []).filter((i) => !i.read).length;
  const groups = useMemo(() => {
    const out: { label: string; items: InboxItem[] }[] = [];
    for (const it of shown) {
      const label = dayLabel(it.endedAt, now);
      const g = out[out.length - 1];
      if (g && g.label === label) g.items.push(it);
      else out.push({ label, items: [it] });
    }
    return out;
  }, [shown, now]);

  const markAll = async () => {
    await call('inbox.markRead', {});
    setItems((xs) => (xs || []).map((i) => ({ ...i, read: true })));
  };
  const markOne = (id: string) => {
    const it = items?.find((i) => i.id === id);
    if (!it || it.read) return;
    call('inbox.markRead', { ids: [id] }, { quiet: true }).catch(() => {});
    setItems((xs) => (xs || []).map((i) => (i.id === id ? { ...i, read: true } : i)));
  };
  const remove = (id: string) => {
    call('inbox.remove', { id }, { quiet: true }).catch(() => {});
    setItems((xs) => (xs || []).filter((i) => i.id !== id));
  };

  return (
    <div className="inbox" ref={root}>
      <header className="ov-head">
        {!sidebar && (
          <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
            <PanelLeftOpen size={16} />
          </button>
        )}
        <h1 className="ov-title">Inbox</h1>
        <span className="ov-sum tnum">{items ? (unread ? `${unread} unread` : items.length ? 'All read' : '') : ''}</span>
        <div className="grow" />
        {unread > 0 && (
          <button className="btn sm ghost" onClick={markAll}>
            <MailOpen size={13} /> Mark all read
          </button>
        )}
      </header>
      <div className="inbox-body">
        <div className="inbox-main">
          {filter && (
            <div className="inbox-filter">
              Showing runs of <strong>{schedules?.find((s) => s.id === filter)?.name || 'one schedule'}</strong>
              <button className="icon-btn sm" onClick={() => setFilter(null)} aria-label="Show every run">
                <X size={13} />
              </button>
            </div>
          )}
          {items === null && (
            <div className="changes-loading">
              {[0, 1, 2].map((k) => (
                <div key={k} className="sk-file">
                  <div className="sk" style={{ width: `${46 - k * 9}%` }} />
                  <div className="sk" />
                  <div className="sk short" />
                </div>
              ))}
            </div>
          )}
          {items && !shown.length && (
            <div className="inbox-empty">
              <Mark size={32} className="inbox-empty-mark" />
              <div className="inbox-empty-title">{schedules?.length ? 'No runs yet.' : 'Nothing runs on its own yet.'}</div>
              <div className="hint">
                {schedules?.length ? 'The first result lands here when a schedule fires. You can also run one now from the list on the right.' : 'A schedule runs a prompt in a folder at a set time — a nightly dependency audit, a Monday test sweep — and leaves the result here with a link back to the full session.'}
              </div>
              {!schedules?.length && (
                <button className="btn primary sm" onClick={() => useStore.setState({ scheduleDialog: {} })}>
                  <Plus size={13} /> New schedule
                </button>
              )}
            </div>
          )}
          {groups.map((g, gi) => (
            <section key={g.label} className="inbox-day" style={{ '--i': gi } as React.CSSProperties}>
              <h2 className="inbox-day-h">
                {g.label}
                <span className="ov-count">{g.items.length}</span>
              </h2>
              {g.items.map((it, i) => (
                <RunCard key={it.id} it={it} index={i} onOpen={() => (markOne(it.id), it.sessionId && openSession(it.sessionId))} onRead={() => markOne(it.id)} onRemove={() => remove(it.id)} />
              ))}
            </section>
          ))}
        </div>
        <aside className="inbox-rail">
          <div className="inbox-rail-h">
            Schedules
            {schedules && schedules.length > 0 && <span className="ov-count">{schedules.length}</span>}
            <div className="grow" />
            <button className="btn xs" onClick={() => useStore.setState({ scheduleDialog: {} })}>
              <Plus size={12} /> New
            </button>
          </div>
          {schedules === null && <Spinner size={14} />}
          {schedules?.map((s, i) => (
            <ScheduleCard key={s.id} s={s} now={now} index={i} active={filter === s.id} onFilter={() => setFilter(filter === s.id ? null : s.id)} />
          ))}
          {schedules && !schedules.length && <div className="hint">No schedules yet.</div>}
        </aside>
      </div>
      {dialog && <ScheduleDialog spec={dialog} schedule={dialog.id ? schedules?.find((s) => s.id === dialog.id) : undefined} />}
    </div>
  );
}

function RunCard({ it, index, onOpen, onRead, onRemove }: { it: InboxItem; index: number; onOpen: () => void; onRead: () => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const add = it.files.reduce((s, f) => s + f.add, 0);
  const del = it.files.reduce((s, f) => s + f.del, 0);
  const Icon = it.status === 'completed' ? CircleCheck : it.status === 'failed' ? CircleAlert : CircleStop;
  const rerun = async () => {
    try {
      await call('schedules.runNow', { id: it.scheduleId });
      toast(`Running ${it.scheduleName} now`, 'info');
    } catch {
      /* toasted */
    }
  };
  const summary = it.summary || it.reason || (it.status === 'completed' ? 'Finished without a written summary.' : 'No output.');
  const long = summary.length > 280;
  return (
    <article className={`run${it.read ? '' : ' unread'} ${it.status}`} onClick={onRead} style={{ animationDelay: `${Math.min(index, 6) * 40}ms` }}>
      <span className={`run-badge ${it.status}`} aria-hidden="true">
        <Icon size={15} />
      </span>
      <div className="run-main">
        <div className="run-head">
          {!it.read && <span className="run-dot" title="New" />}
          <span className="run-name ellipsis">{it.scheduleName}</span>
          {it.manual && <span className="chip tiny">by hand</span>}
          <span className="run-when tnum">
            {clock(it.endedAt)} · {duration(it.durationMs)}
          </span>
        </div>
        <p className={`run-sum${open ? ' open' : ''}`}>{open || !long ? summary : summary.slice(0, 280) + '…'}</p>
        {long && (
          <button className="link run-more" onClick={() => setOpen(!open)}>
            {open ? 'Less' : 'Read more'}
          </button>
        )}
        {it.status !== 'completed' && it.reason && it.summary && <div className="run-reason">{it.reason}</div>}
        <div className="run-foot">
          {it.files.length > 0 ? (
            <span className="tnum" title={it.files.map((f) => f.path).join('\n')}>
              {plural(it.files.length, 'file')} <span className="num-add">+{add}</span> <span className="num-del">−{del}</span>
            </span>
          ) : (
            <span className="faint">No files changed</span>
          )}
          {it.cost > 0 && <span className="tnum">{cost(it.cost)}</span>}
          <span className="faint ellipsis">{basename(it.cwd)}</span>
          <div className="grow" />
          <button className="btn xs ghost" onClick={(e) => (e.stopPropagation(), rerun())} title="Run this schedule again now">
            <RotateCw size={11} /> Run now
          </button>
          {it.sessionId && (
            <button className="btn xs" onClick={(e) => (e.stopPropagation(), onOpen())} title="Open the session and continue by hand">
              <ArrowUpRight size={11} /> Open session
            </button>
          )}
          <button className="icon-btn sm run-x" onClick={(e) => (e.stopPropagation(), onRemove())} title="Dismiss" aria-label="Dismiss">
            <X size={13} />
          </button>
        </div>
      </div>
    </article>
  );
}

/** A schedule: name, cadence and folder, when it next fires, and a thin bar filling toward that moment. */
function ScheduleCard({ s, now, index, active, onFilter }: { s: Schedule; now: number; index: number; active: boolean; onFilter: () => void }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await call('schedules.runNow', { id: s.id });
      toast(`Running ${s.name} now`, 'info');
    } catch {
      /* toasted */
    } finally {
      setBusy(false);
    }
  };
  const toggle = (on: boolean) => call('schedules.toggle', { id: s.id, enabled: on }).catch(() => {});
  const del = () =>
    confirmAction({
      title: `Delete the schedule “${s.name}”?`,
      body: 'Its past runs stay in the inbox.',
      confirm: 'Delete schedule',
      danger: true,
      onConfirm: () => call('schedules.remove', { id: s.id }).catch(() => {}),
    });
  const menu = [
    { label: 'Edit…', icon: Pencil, onSelect: () => useStore.setState({ scheduleDialog: { id: s.id } }) },
    { label: 'Run now', icon: Play, onSelect: run },
    { label: s.enabled ? 'Pause' : 'Resume', icon: s.enabled ? Pause : Play, onSelect: () => toggle(!s.enabled) },
    { separator: true },
    { label: 'Delete…', icon: Trash, danger: true, onSelect: del },
  ];
  // How far along the wait for the next run is, when both ends are known.
  const progress = s.enabled && !s.running && s.nextRunAt && s.lastRunAt && s.nextRunAt > s.lastRunAt ? Math.max(0, Math.min(1, (now - s.lastRunAt) / (s.nextRunAt - s.lastRunAt))) : null;
  return (
    <div className={`sched${s.enabled ? '' : ' off'}${active ? ' on' : ''}${s.running ? ' running' : ''}`} style={{ animationDelay: `${Math.min(index, 6) * 40}ms` }}>
      <button className="sched-main" onClick={onFilter} title={`${tildify(s.cwd)}\n\n${s.prompt}`}>
        <span className="sched-name ellipsis">{s.name}</span>
        <span className="sched-sub ellipsis">
          {s.cadenceLabel} · {basename(s.cwd)}
        </span>
        <span className={`sched-next${s.error ? ' err' : ''}`}>
          {s.running ? (
            <>
              <Spinner size={11} /> running {duration(now - s.running.startedAt)}
            </>
          ) : s.error ? (
            s.error
          ) : !s.enabled ? (
            'Paused'
          ) : s.nextRunAt ? (
            `Next ${until(s.nextRunAt, now)}`
          ) : (
            'Never'
          )}
          {s.lastRunAt && !s.running ? (
            <span className="faint">
              {' '}
              · last {timeAgo(s.lastRunAt, now)}
              {s.lastStatus && s.lastStatus !== 'completed' ? ` (${s.lastStatus})` : ''}
            </span>
          ) : null}
        </span>
      </button>
      <div className="sched-end">
        <Switch on={s.enabled} onChange={toggle} label={s.enabled ? 'Pause' : 'Resume'} />
        <button className="icon-btn sm" title="Run now" onClick={run} disabled={busy || Boolean(s.running)}>
          {busy ? <Spinner size={12} /> : <Play size={13} />}
        </button>
        <MenuButton items={menu} title="Schedule actions" width={200}>
          <Ellipsis size={14} />
        </MenuButton>
      </div>
      {(progress !== null || s.running) && (
        <span className="sched-bar" aria-hidden="true">
          <span style={{ width: s.running ? '40%' : `${(progress || 0) * 100}%` }} />
        </span>
      )}
      {active && <Check size={12} className="sched-check" />}
    </div>
  );
}
