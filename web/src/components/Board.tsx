import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PanelLeftOpen, CircleAlert, Plus, CalendarClock } from 'lucide-react';
import { useStore, call, openSession, setUi, setDialog, decideCard, startCard, setWait, toast } from '../lib/store';
import type { BoardState, Schedule, SessionSummary, WaitCondition } from '../lib/types';
import { LANES, laneOf, cardState, step, actionsOf, dropAction, scheduleCard, conflictCard, isSchedule, isConflict, SCHEDULE_PREFIX, type Lane } from '../lib/board';
import { cost, timeAgo } from '../lib/format';
import { Num } from './ui/bits';
import { useNow } from './ui/hooks';
import { MenuButton, type MenuItem } from './ui/Popover';
import { typingElsewhere } from './tools/Permission';
import { Card } from './board/Card';
import { CardPanel, type PanelMode } from './board/CardPanel';
import type { CardState as CardStateT } from '../lib/board';
import { NewCard } from './board/NewCard';
import { ScheduleDialog } from './ScheduleDialog';

/** The card chosen last time the board was open, so coming back from a session lands on it. */
let remembered: string | null = null;

/**
 * The workboard: one lane for each pair of hands the work can be in — backlog, Claude, a condition,
 * you, done — and one card per session that is work (it has an intent) or is doing something.
 * Schedules that will fire sit in the Waiting lane until they do. Only the "Needs you" lane carries
 * the accent. The panel on the right shows the chosen card's intent, ledger and the actions its
 * state allows; a single key does each of them.
 */
export function Board() {
  const sessions = useStore((s) => s.sessions);
  const rt = useStore((s) => s.rt);
  const sidebar = useStore((s) => s.ui.sidebar);
  const schedRev = useStore((s) => s.schedRev);
  const scheduleDialog = useStore((s) => s.dialogs.schedule);
  const [state, setState] = useState<BoardState | null>(null);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [sel, setSel] = useState<string | null>(remembered);
  const [mode, setMode] = useState<PanelMode>(null);
  const [creating, setCreating] = useState(false);
  /** The card being dragged and the lane under it, for the lanes' highlight. */
  const [drag, setDrag] = useState<{ id: string; state: CardStateT; over: Lane | null } | null>(null);
  // The same, readable before React has re-rendered: dragover can arrive before the state lands.
  const dragRef = useRef<{ id: string; state: CardStateT; over: Lane | null } | null>(null);
  const now = useNow(1000, true);
  const box = useRef<HTMLDivElement>(null);

  // Live state for every running session, then the push stream keeps it fresh.
  useEffect(() => {
    let alive = true;
    const load = () =>
      call<BoardState>('board.state', {}, { quiet: true })
        .then((b) => {
          if (!alive) return;
          setState(b);
          const cur = useStore.getState().rt;
          const next = { ...cur };
          for (const [id, st] of Object.entries(b.live)) next[id] = { ...(cur[id] || { rev: 0, loaded: false, loading: false }), ...st };
          useStore.setState({ rt: next });
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 20_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [sessions.length, schedRev]);
  useEffect(() => {
    let alive = true;
    call<Schedule[]>('schedules.list', {}, { quiet: true })
      .then((r) => alive && setSchedules(r || []))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [schedRev]);

  // Schedules and contradicted notes are cards too, under their own ids; the panel treats them by their actions, not a transcript.
  const all = useMemo(() => [...sessions, ...(state?.scheduled || []).map(scheduleCard), ...(state?.conflicts || []).map(conflictCard)], [sessions, state?.scheduled, state?.conflicts]);
  const titles = useMemo(() => Object.fromEntries(sessions.map((s) => [s.id, s.title])), [sessions]);

  const lanes = useMemo(() => {
    const out: Record<Lane, SessionSummary[]> = { backlog: [], agent: [], wait: [], me: [], done: [] };
    for (const s of all) {
      const lane = laneOf(s, rt[s.id], now);
      if (lane) out[lane].push(s);
    }
    // Prompts first in "Needs you" and contradicted notes last; the soonest condition first in "Waiting"; otherwise the most recently touched on top.
    out.me.sort((a, b) => Number(Boolean(a.conflict)) - Number(Boolean(b.conflict)) || Number(cardState(b, rt[b.id], now) === 'prompt') - Number(cardState(a, rt[a.id], now) === 'prompt') || (b.lastModified || 0) - (a.lastModified || 0));
    out.wait.sort((a, b) => (a.wait?.kind === 'time' ? a.wait.at : Infinity) - (b.wait?.kind === 'time' ? b.wait.at : Infinity) || (b.lastModified || 0) - (a.lastModified || 0));
    for (const k of ['backlog', 'agent', 'done'] as Lane[]) out[k].sort((a, b) => (b.lastModified || 0) - (a.lastModified || 0));
    return out;
  }, [all, rt, Math.floor(now / 60e3)]); // eslint-disable-line react-hooks/exhaustive-deps -- lanes only move on the minute

  const selected = sel ? all.find((s) => s.id === sel) || null : null;
  const selState = selected ? cardState(selected, rt[selected.id], now) : null;
  const select = useCallback((id: string | null) => {
    remembered = id;
    setSel(id);
    setMode(null);
  }, []);
  useEffect(() => {
    if (sel && !selected) select(null);
  }, [sel, selected, select]);
  useEffect(() => {
    if (!sel) return;
    box.current?.querySelector<HTMLElement>(`[data-card="${sel}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [sel]);

  const stuck = lanes.agent.filter((s) => cardState(s, rt[s.id], now) === 'stuck');
  const running = state?.limit.running ?? lanes.agent.filter((s) => s.status === 'running' || s.status === 'starting').length;
  const max = state?.limit.max ?? 0;
  const openSchedule = (id?: string) => setDialog('schedule', id ? { id } : {});

  const act = useCallback(
    async (id: string, action: string, payload?: WaitCondition | null) => {
      if (isSchedule(id)) {
        const sid = id.slice(SCHEDULE_PREFIX.length);
        if (action === 'schedule-edit' || action === 'open') openSchedule(sid);
        else if (action === 'schedule-run') {
          await call('schedules.runNow', { id: sid });
          toast('Running now', 'info');
        }
        return;
      }
      if (isConflict(id)) {
        const c = all.find((x) => x.id === id)?.conflict;
        if (!c || (action !== 'note-keep' && action !== 'note-forget')) return;
        await call('memory.resolve', { cwd: c.cwd, id: c.id, keep: action === 'note-keep' });
        toast(action === 'note-keep' ? 'The note stays' : 'The note is forgotten', 'info');
        const me = lanes.me.filter((x) => x.id !== id);
        select(me[0]?.id || null);
        return;
      }
      const s = useStore.getState().sessions.find((x) => x.id === id);
      if (!s) return;
      const nextNeeds = () => {
        const me = lanes.me.filter((x) => x.id !== id);
        select(me[0]?.id || null);
      };
      if (action === 'open') {
        setUi({ returnTo: 'board', ...(cardState(s, useStore.getState().rt[id]) === 'review' ? { panel: true, panelTab: 'changes' as const } : {}) });
        openSession(id);
      } else if (action === 'approve') {
        await decideCard(id, 'approve');
        nextNeeds();
      } else if (action === 'park') {
        await decideCard(id, 'park');
        nextNeeds();
      } else if (action === 'reject') setMode('reject');
      else if (action === 'send') setMode('send');
      else if (action === 'wait') setMode('wait');
      else if (action === 'set-wait') {
        await setWait(id, payload || null);
        setMode(null);
        if (payload) {
          toast('Waiting; Claude is woken when the condition is met', 'info');
          nextNeeds();
        }
      } else if (action === 'unwait') await setWait(id, null);
      else if (action === 'wake') await decideCard(id, 'wake');
      else if (action === 'stop') await decideCard(id, 'stop');
      else if (action === 'start') await startCard(id);
    },
    [lanes.me, select, all],
  );

  // j/k and arrows move within a lane, h/l across lanes, 1–5 jump to a lane; single letters act on the card.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typingElsewhere(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (scheduleDialog) return;
      const k = e.key;
      const dir = k === 'j' || k === 'ArrowDown' ? 'down' : k === 'k' || k === 'ArrowUp' ? 'up' : k === 'l' || k === 'ArrowRight' ? 'right' : k === 'h' || k === 'ArrowLeft' ? 'left' : null;
      if (mode === 'wait' && '123'.includes(k) && k !== '') {
        document.querySelector<HTMLButtonElement>(`.bd-waitpick [data-k="${k}"]`)?.click();
      } else if (dir) {
        select(step(lanes, sel, dir));
      } else if ('12345'.includes(k) && k !== '' && !(selState === 'prompt')) {
        const lane = LANES[Number(k) - 1];
        if (lanes[lane.key][0]) select(lanes[lane.key][0].id);
        else return;
      } else if (k === 'n') {
        setCreating(true);
      } else if (k === 'Escape') {
        if (mode) setMode(null);
        else if (sel) select(null);
        else return;
      } else if (selected && selState) {
        const actions = actionsOf(selected, selState, rt[selected.id]);
        const hit = k === 'Enter' ? actions[0] : actions.find((a) => a.key === k);
        if (!hit) return;
        act(selected.id, hit.id);
      } else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lanes, sel, selected, selState, mode, rt, act, select, scheduleDialog]);

  const scheduleMenu = (): MenuItem[] => [
    { heading: schedules.length ? 'Schedules' : 'No schedules yet' },
    ...schedules.map((s) => ({ label: s.name, hint: s.enabled ? `${s.cadenceLabel}${s.nextRunAt ? ` · next ${timeAgo(s.nextRunAt, now)}` : ''}` : `${s.cadenceLabel} · paused`, checked: s.enabled, onSelect: () => openSchedule(s.id) })),
    { separator: true },
    { label: 'New schedule…', icon: CalendarClock, onSelect: () => openSchedule() },
  ];

  const today = new Date(now).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  return (
    <div className="board-page" ref={box}>
      <header className="bd-strip">
        {!sidebar && (
          <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
            <PanelLeftOpen size={16} />
          </button>
        )}
        <h1 className="bd-title">{today}</h1>
        <span className={`bd-fact${lanes.me.length ? ' hot' : ''}`}>
          <b className="tnum">{lanes.me.length}</b> {lanes.me.length === 1 ? 'needs' : 'need'} you
        </span>
        <span className="bd-fact">
          <b className="tnum">{lanes.agent.length}</b> with Claude
        </span>
        <span className="bd-fact">
          <b className="tnum">{lanes.wait.length}</b> waiting
        </span>
        <span className="bd-fact">
          <b className="tnum">{state?.today.done ?? lanes.done.length}</b> done today
        </span>
        <span className="bd-fact">
          <b className="tnum">
            <Num value={state?.today.cost || 0} format={cost} />
          </b>{' '}
          spent today
        </span>
        <span className="grow" />
        {max > 0 && (
          <span className={`bd-fact bd-limit${running >= max ? ' full' : ''}`} title="Sessions Claude works on at once from the board (maxConcurrent in the config; 0 for no limit)">
            running <b className="tnum">{running}</b> / {max}
          </span>
        )}
      </header>

      {stuck.length > 0 && (
        <button className="bd-andon" onClick={() => select(stuck[0].id)}>
          <CircleAlert size={14} />
          <b>Stopped</b>
          <span className="ellipsis">
            {stuck[0].title}
            {stuck.length > 1 ? ` and ${stuck.length - 1} more` : ''} — Claude could not go on by itself
          </span>
          <span className="bd-andon-go">Look →</span>
        </button>
      )}

      <div className="bd-body">
        <div
          className="bd-lanes"
          onDragStart={(e) => {
            const el = (e.target as HTMLElement).closest<HTMLElement>('.bd-card[draggable="true"]');
            if (!el) return;
            const id = el.dataset.card;
            if (!id) return;
            const st = el.dataset.state as CardStateT;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', id);
            // Not selected here: opening the panel would shift the lanes under the pointer mid-drag.
            dragRef.current = { id, state: st, over: null };
            setDrag(dragRef.current);
          }}
          onDragEnd={() => {
            dragRef.current = null;
            setDrag(null);
          }}
          onDragOver={(e) => {
            const d = dragRef.current;
            if (!d) return;
            const lane = (e.target as HTMLElement).closest<HTMLElement>('.bd-lane')?.dataset.lane as Lane | undefined;
            const ok = lane ? dropAction(d.state, lane) : null;
            if (ok) {
              e.preventDefault();
              e.dataTransfer.dropEffect = 'move';
            }
            const over = ok && lane ? lane : null;
            if (over !== d.over) {
              dragRef.current = { ...d, over };
              setDrag(dragRef.current);
            }
          }}
          onDrop={(e) => {
            const d = dragRef.current;
            if (!d) return;
            const lane = (e.target as HTMLElement).closest<HTMLElement>('.bd-lane')?.dataset.lane as Lane | undefined;
            const action = lane ? dropAction(d.state, lane) : null;
            e.preventDefault();
            dragRef.current = null;
            setDrag(null);
            if (action) act(d.id, action);
          }}
        >
          {LANES.map((lane, i) => (
            <section key={lane.key} className={`bd-lane ${lane.key}${drag ? (drag.over === lane.key ? ' over' : dropAction(drag.state, lane.key) ? '' : ' no') : ''}`} data-lane={lane.key} aria-label={lane.title}>
              <div className="bd-lane-h">
                <span className="bd-lane-mark" aria-hidden="true" />
                <span className="bd-lane-title">{lane.title}</span>
                <span className="bd-lane-key">{i + 1}</span>
                <span className="grow" />
                {lane.key === 'backlog' && (
                  <button className="icon-btn sm" title="New card (n)" aria-label="New card" onClick={() => setCreating(true)}>
                    <Plus size={13} />
                  </button>
                )}
                {lane.key === 'wait' && (
                  <MenuButton items={scheduleMenu} className="icon-btn sm bd-lane-menu" title="Schedules: recurring work that lands here before each run" width={300}>
                    <CalendarClock size={13} />
                  </MenuButton>
                )}
                <span className={`bd-lane-n tnum${lane.key === 'agent' && max > 0 && running >= max ? ' full' : ''}`}>{lane.key === 'agent' && max > 0 ? `${running}/${max}` : lanes[lane.key].length}</span>
              </div>
              <div className="bd-cards">
                {lane.key === 'backlog' && creating && (
                  <NewCard
                    onDone={(id) => {
                      setCreating(false);
                      if (id) select(id);
                    }}
                  />
                )}
                {lanes[lane.key].length ? lanes[lane.key].map((s) => <Card key={s.id} s={s} rt={rt[s.id]} selected={s.id === sel} now={now} titles={titles} onSelect={() => select(s.id)} onOpen={() => act(s.id, 'open')} />) : lane.key === 'backlog' && creating ? null : <div className="bd-empty">{lane.empty}</div>}
              </div>
            </section>
          ))}
        </div>
        {selected && selState && <CardPanel s={selected} rt={rt[selected.id]} state={selState} mode={mode} setMode={setMode} caps={state?.caps || { ci: false }} cards={lanes} titles={titles} onAct={(a, p) => act(selected.id, a, p)} onClose={() => select(null)} now={now} />}
      </div>

      {scheduleDialog && <ScheduleDialog spec={scheduleDialog} schedule={scheduleDialog.id ? schedules.find((s) => s.id === scheduleDialog.id) : undefined} />}

      <footer className="bd-keys">
        <span>
          <kbd>h</kbd>
          <kbd>l</kbd> lanes
        </span>
        <span>
          <kbd>j</kbd>
          <kbd>k</kbd> cards
        </span>
        <span>
          <kbd>1</kbd>–<kbd>5</kbd> jump
        </span>
        <span>
          <kbd>Enter</kbd> main action
        </span>
        <span>
          <kbd>e</kbd> approve
        </span>
        <span>
          <kbd>r</kbd> send back
        </span>
        <span>
          <kbd>w</kbd> wait / set aside
        </span>
        <span>
          <kbd>s</kbd> stop
        </span>
        <span>
          <kbd>n</kbd> new
        </span>
        <span>drag a card onto a lane to do that lane's action</span>
      </footer>
    </div>
  );
}
