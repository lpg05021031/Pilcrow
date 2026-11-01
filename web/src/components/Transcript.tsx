import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ArrowDown, ChevronRight, Layers, Telescope } from 'lucide-react';
import { useStore, models, memo as sessionMemo } from '../lib/store';
import type { Item, ToolItem } from '../lib/transcript';
import { isBusy, type PermissionRequest } from '../lib/types';
import { duration, truncate } from '../lib/format';
import { ToolCard, type ToolCtx } from './tools/ToolCard';
import { PermissionCard } from './tools/Permission';
import { useNow } from './ui/hooks';
import { WorkingMark } from './ui/brand';
import { useExitGhost } from '../lib/motion';
import { Collapse } from './ui/Collapse';
import { ErrorBoundary } from './ui/ErrorBoundary';
import { extendTurns, groupItems, liveTurnFolds, type Turns, type Unit } from '../lib/units';
import { exploreLabel, isQuiet, liveLabel, workSummary } from '../lib/labels';
import { useFollow, REVEAL_EVENT } from './useFollow';
import { ItemView, WorkingLine } from './TranscriptRows';

// ---------------------------------------------------------------- list with step grouping
// The grouping and the words on folds are pure and live in lib/units.ts and lib/labels.ts.

/**
 * The items of one slice of the conversation, with runs of tool calls drawn as steps. A unit's slice
 * is a fresh array whenever it changes (lib/units), but an agent's children are its tool's own array,
 * which the model grows in place: `rev` (the tool's) is what tells the memo they changed.
 */
const Flat = memo(function Flat({ items, ctx }: { items: Item[]; ctx: ToolCtx; rev?: number }) {
  const groups = groupItems(items);
  return (
    <>
      {groups.map((g) =>
        g.kind === 'steps' ? (
          <Steps key={g.key} items={g.items} ctx={ctx} />
        ) : (
          <ItemView key={g.key} item={g.item} rev={g.item.rev} ctx={ctx} />
        ),
      )}
    </>
  );
});

// ---------------------------------------------------------------- turns: the work folds, what Claude says stays

const WorkFold = memo(function WorkFold({ unit, ctx }: { unit: Extract<Unit, { kind: 'work' }>; ctx: ToolCtx }) {
  const { work, live, key } = unit;
  const waiting = work.some((it) => it.kind === 'tool' && ctx.pending.has(it.id));
  // Normal view keeps every stretch of work to its one line, running or not; the other views open it.
  const defaultOpen = ctx.view === 'thinking' || ctx.view === 'verbose';
  // A fold opened or closed by hand stays so while the conversation is in memory (its memo), like
  // where it was scrolled; a jump to a step inside it (revealItem) opens it through `openers`.
  const [open, setOpen] = useState<boolean>(() => sessionMemo(ctx.sessionId).folds.get(key) ?? defaultOpen);
  useEffect(() => {
    const openers = sessionMemo(ctx.sessionId).openers;
    openers.set(key, () => setOpen(true));
    return () => void openers.delete(key);
  }, [key, ctx.sessionId]);
  const summary = useMemo(() => workSummary(work), [work]);
  const doing = live ? liveLabel(work) : null;
  const now = useNow(1000, live);
  const started = useStore((s) => (live ? s.rt[ctx.sessionId]?.turnStartedAt || 0 : 0));
  const first = work[0] as Item & { startTs?: number };
  const t0 = live ? started || first?.ts || first?.startTs || 0 : 0;
  const elapsed = t0 ? now - t0 : 0;
  const toggle = () => {
    sessionMemo(ctx.sessionId).folds.set(key, !open);
    setOpen(!open);
  };
  // A pause to think, with no steps, is its own one-line row already; a fold around it would be ceremony.
  if (!work.some((it) => it.kind === 'tool')) return <Flat items={work} ctx={ctx} />;
  const isOpen = open || waiting;
  return (
    <div className={`work${isOpen ? ' open' : ''}${summary.failed ? ' has-failed' : ''}${live ? ' live' : ''}`}>
      <button className="work-head" onClick={toggle} aria-expanded={isOpen}>
        <span className={`work-mark${live ? '' : ' gone'}`} aria-hidden="true">
          <WorkingMark />
        </span>
        <span className={`work-sum${live ? ' shimmer' : ''}`}>{doing ? `${doing}…` : summary.text}</span>
        {summary.failed > 0 && <span className="work-fail">· {summary.failed} failed</span>}
        <ChevronRight size={14} className={`tool-chev${isOpen ? ' open' : ''}`} />
        {(!isOpen || live) && (summary.steps > 0 || elapsed > 1000) && (
          <span className="work-n tnum">
            {summary.steps > 0 ? `${summary.steps} ${summary.steps === 1 ? 'step' : 'steps'}` : ''}
            {elapsed > 1000 ? `${summary.steps > 0 ? ' · ' : ''}${duration(elapsed)}` : ''}
          </span>
        )}
      </button>
      <Collapse open={isOpen}>
        {() => (
          <div className="work-body">
            <Flat items={work} ctx={ctx} />
          </div>
        )}
      </Collapse>
    </div>
  );
});

/**
 * Long conversations. Units are gathered into pages of a few units each, and a page far from the
 * viewport is an empty box of about the right height until the reader comes near (IntersectionObserver,
 * well ahead of them): no Markdown, no rows, no icons for the thousand turns nobody is looking at,
 * which is what makes a long conversation open at once and stay light. A page keeps its stand-in
 * height until it is drawn (transcript.css), so the scrollbar is the same either way; once drawn, a
 * page stays drawn while the conversation is on screen. Shown again, it starts from the pages around
 * where the reader was (the scroll memory, useFollow) and the rest come back as they come near, so
 * switching back costs a few pages, however much of the history was read before. A unit's page never
 * changes (units only ever append), so nothing is ever moved between boxes and re-mounted.
 */
const PAGE = 8;
const EST = { items: 260, work: 44 }; // stand-in heights, matching transcript.css

/** Which pages have been drawn; each page listens for its own key only. */
class NearSet {
  keys = new Set<string>();
  private listeners = new Map<string, Set<() => void>>();
  has(k: string) {
    return this.keys.has(k);
  }
  add(k: string) {
    if (this.keys.has(k)) return false;
    this.keys.add(k);
    this.listeners.get(k)?.forEach((fn) => fn());
    return true;
  }
  subscribe(k: string, fn: () => void) {
    const set = this.listeners.get(k) || new Set();
    this.listeners.set(k, set);
    set.add(fn);
    return () => {
      set.delete(fn);
      if (!set.size) this.listeners.delete(k);
    };
  }
}

/**
 * Bring an item of the conversation on screen into view — drawing its page and opening the fold
 * around it first if need be — and return the element, or null when it is not in this conversation.
 */
export async function revealItem(sessionId: string, domId: string, itemId: string): Promise<HTMLElement | null> {
  // A jump the reader asked for: the conversation stops following its live end first (useFollow), so
  // the page drawn here does not pull the view back down before the caller scrolls to it.
  document.querySelector('.session .transcript')?.dispatchEvent(new Event(REVEAL_EVENT));
  let el = document.getElementById(domId);
  if (!el && sessionMemo(sessionId).reveal?.(itemId)) {
    for (let i = 0; i < 12 && !el; i++) {
      await new Promise((r) => requestAnimationFrame(r));
      el = document.getElementById(domId);
    }
  }
  return el;
}

/** One box of the conversation. Memoised, so a page is reconciled only when one of its units changes. */
const UnitBox = memo(function UnitBox({ unit, ctx }: { unit: Unit; ctx: ToolCtx }) {
  return <div className={`unit ${unit.kind}`}>{unit.kind === 'work' ? <WorkFold unit={unit} ctx={ctx} /> : <Flat items={unit.items} ctx={ctx} />}</div>;
});

type Page = { key: string; units: Unit[]; est: number };

const PageBox = memo(function PageBox({ page, ctx, live, near, observe }: { page: Page; ctx: ToolCtx; live: boolean; near: NearSet | null; observe: (el: HTMLDivElement | null, key: string) => void }) {
  const drawn = useSyncExternalStore(
    (fn) => (near ? near.subscribe(page.key, fn) : () => {}),
    () => !near || near.has(page.key),
  );
  const far = !live && !drawn;
  return (
    <div className={`page${far ? ' far' : ''}${live ? ' live' : ''}`} style={far ? { minHeight: page.est, containIntrinsicSize: `auto ${page.est}px` } : undefined} ref={(el) => observe(el, page.key)}>
      {far ? null : (
        <ErrorBoundary label="This part of the conversation">
          {page.units.map((u) => (
            <UnitBox key={u.key} unit={u} ctx={ctx} />
          ))}
        </ErrorBoundary>
      )}
    </div>
  );
});

/**
 * The conversation, or (`flat`) an agent's own steps. `items` is a model's array, mutated in place as
 * the conversation grows, so `rev` (the model's rev) is what marks a change.
 */
export const ItemList = memo(function ItemList({ items, ctx, rev, running, flat = false }: { items: Item[]; ctx: ToolCtx; rev: number; running: boolean; flat?: boolean }) {
  // Grouped again per frame only from the turn under way; earlier units keep their identity, so the
  // components under them skip the render (lib/units extendTurns).
  const turns = useRef<Turns | null>(null);
  const grouped = useMemo(() => (flat ? null : (turns.current = extendTurns(turns.current, items, running, rev))), [items, rev, running, flat]);
  const units = useMemo(() => grouped?.units || [], [grouped]);

  // A page keeps its identity until a unit in it changes, so a streaming reply reconciles one page;
  // the pages before the first changed unit are not even looked at.
  const pagesRef = useRef<Page[]>([]);
  const pages = useMemo(() => {
    const old = pagesRef.current;
    const out = old.slice(0, Math.min(old.length, Math.floor((grouped?.changed || 0) / PAGE)));
    for (let p = out.length; p * PAGE < units.length; p++) {
      const slice = units.slice(p * PAGE, (p + 1) * PAGE);
      const was = old[p];
      const same = was && was.units.length === slice.length && was.units.every((u, i) => u === slice[i]);
      out.push(same ? was : { key: 'pg' + p, units: slice, est: slice.reduce((n, u) => n + EST[u.kind], 0) + 24 * slice.length });
    }
    pagesRef.current = out;
    return out;
  }, [units, grouped]);
  // The turn under way (and the page the reader lands on) is always drawn.
  let tailStart = units.length;
  for (let i = units.length - 1; i >= 0; i--) {
    const u = units[i];
    if (u.kind === 'items' && u.items.some((it) => it.kind === 'user')) {
      tailStart = i;
      break;
    }
  }
  const liveFrom = Math.min(Math.floor(tailStart / PAGE), pages.length - 1);

  // Which far-off pages have been drawn (sticky while on screen). Coming back to a conversation, the
  // pages around the one the reader was at are drawn in the first frame, so the place they return to
  // is there at once; the others are drawn again only as they come near.
  const [near] = useState(() => {
    const set = new NearSet();
    const at = flat ? null : sessionMemo(ctx.sessionId).scroll;
    if (at && !at.bottom) for (let p = Math.max(0, at.index - 1); p <= at.index + 2; p++) set.add('pg' + p);
    return set;
  });
  const io = useRef<IntersectionObserver | null>(null);
  const drawing = useRef<number | null>(null);
  const keys = useRef(new WeakMap<Element, string>());
  const unitsRef = useRef(units);
  unitsRef.current = units;
  const observe = useMemo(
    () => (el: HTMLDivElement | null, key: string) => {
      if (!el) return;
      if (near.has(key)) return;
      if (!io.current && typeof IntersectionObserver === 'function') {
        // Pages are drawn one per frame, nearest the viewport first, and not at all while the
        // reader is flinging past them: a fast scroll through the history then costs short frames
        // and the pages fill in as soon as it slows, the way a native list does. A page that leaves
        // the margin before its turn comes is dropped from the queue, so a fling (or a jump) draws
        // where it ends, not everything it passed.
        const queue: { key: string; dist: number }[] = [];
        let lastTop = 0;
        let lastAt = 0;
        const sc = el.closest('.transcript');
        const draw = () => {
          drawing.current = null;
          const top = sc ? sc.scrollTop : 0;
          const now = performance.now();
          const flinging = lastAt && now - lastAt < 80 && Math.abs(top - lastTop) > (sc?.clientHeight || 900) * 1.5;
          lastTop = top;
          lastAt = now;
          if (!flinging) {
            queue.sort((a, b) => a.dist - b.dist);
            for (let next = queue.shift(); next; next = queue.shift()) if (near.add(next.key)) break;
          }
          if (queue.length) drawing.current = requestAnimationFrame(draw);
        };
        io.current = new IntersectionObserver(
          (entries) => {
            const vh = sc?.clientHeight || window.innerHeight || 900;
            for (const e of entries) {
              const k = keys.current.get(e.target);
              if (!k || near.has(k)) {
                io.current?.unobserve(e.target);
                continue;
              }
              const queued = queue.findIndex((q) => q.key === k);
              if (queued >= 0) queue.splice(queued, 1);
              if (!e.isIntersecting) continue;
              const r = e.boundingClientRect;
              const dist = r.bottom < 0 ? -r.bottom : r.top > vh ? r.top - vh : 0;
              queue.push({ key: k, dist });
            }
            if (queue.length && drawing.current == null) drawing.current = requestAnimationFrame(draw);
          },
          // The conversation scrolls inside its own box, so that box is the root: measured against the
          // viewport, a row scrolled out of it would never count as near, margin or no margin.
          { root: sc, rootMargin: '1600px 0px' },
        );
      }
      keys.current.set(el, key);
      io.current?.observe(el);
    },
    [near],
  );
  useEffect(
    () => () => {
      io.current?.disconnect();
      if (drawing.current != null) cancelAnimationFrame(drawing.current);
    },
    [],
  );
  const shownNear = typeof IntersectionObserver === 'function' ? near : null;
  // Jumps from the timeline, a file's history or Find land on items that may sit in a far page or a closed fold.
  useEffect(() => {
    if (flat) return;
    const m = sessionMemo(ctx.sessionId);
    const reveal = (id: string) => {
      const i = unitsRef.current.findIndex((x) => (x.kind === 'work' ? x.work : x.items).some((it) => it.id === id || (it as { uuid?: string }).uuid === id));
      if (i < 0) return false;
      const u = unitsRef.current[i];
      near.add('pg' + Math.floor(i / PAGE));
      if (u.kind === 'work') {
        m.folds.set(u.key, true);
        m.openers.get(u.key)?.();
      }
      return true;
    };
    m.reveal = reveal;
    return () => {
      if (m.reveal === reveal) m.reveal = null;
    };
  }, [ctx.sessionId, flat, near]);

  if (flat) return <Flat items={items} ctx={ctx} rev={rev} />;
  return (
    <div className="units">
      {pages.map((pg, i) => (
        <PageBox key={pg.key} page={pg} ctx={ctx} live={i >= liveFrom} near={shownNear} observe={observe} />
      ))}
    </div>
  );
});

// Read-only steps that finished cleanly fold into one "Explored" line (lib/labels isQuiet); edits,
// agents, failures and anything waiting on the user always stay visible as their own rows.
type Seg = { kind: 'tool'; item: ToolItem } | { kind: 'explore'; items: ToolItem[]; key: string };

function Steps({ items, ctx }: { items: ToolItem[]; ctx: ToolCtx }) {
  const [expanded, setExpanded] = useState(false);
  // While Claude is still working, the steps at the tail stay as rows: folding them into "Explored"
  // the moment each one finishes would make the end of the list jump with every tool call. They
  // fold once something follows them, or when the turn is over.
  const live = useStore((s) => isBusy(s.rt[ctx.sessionId]?.status));
  // A step that changes gives its unit, and so this list, a new array (lib/units): `items` is enough.
  const segs = useMemo(() => {
    const out: Seg[] = [];
    let run: ToolItem[] = [];
    const flush = (tail = false) => {
      if (run.length >= 2 && !(tail && live)) out.push({ kind: 'explore', items: run, key: 'x-' + run[0].id });
      else run.forEach((t) => out.push({ kind: 'tool', item: t }));
      run = [];
    };
    for (const t of items) {
      if (ctx.view !== 'verbose' && isQuiet(t, ctx.pending.has(t.id))) run.push(t);
      else {
        flush();
        out.push({ kind: 'tool', item: t });
      }
    }
    flush(true);
    return out;
  }, [items, ctx, live]);
  const COLLAPSE_AT = 10;
  const hidden = !expanded && segs.length > COLLAPSE_AT ? segs.length - 6 : 0;
  const visible = hidden ? segs.slice(hidden) : segs;
  const summary = items.find((t) => t.summary)?.summary;
  return (
    <div className="steps">
      {hidden > 0 && (
        <button className="steps-more" onClick={() => setExpanded(true)}>
          <span className="tool-ico">
            <Layers size={14} />
          </span>
          <span>
            {summary ? truncate(summary, 80) + ' · ' : ''}
            {hidden} earlier steps
          </span>
        </button>
      )}
      {visible.map((g) => (g.kind === 'explore' ? <Explore key={g.key} items={g.items} ctx={ctx} /> : <ToolCard key={g.item.id} item={g.item} rev={g.item.rev} ctx={ctx} perm={ctx.pending.get(g.item.id)} />))}
    </div>
  );
}

function Explore({ items, ctx }: { items: ToolItem[]; ctx: ToolCtx }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`explore${open ? ' open' : ''}`}>
      <button className="tool-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="tool-ico">
          <Telescope size={14} />
        </span>
        <span className="tool-line">
          <span className="tool-target">{exploreLabel(items)}</span>
          <ChevronRight size={13} className="tool-chev" />
        </span>
      </button>
      <Collapse open={open}>
        {() => (
          <div className="explore-body">
            {items.map((t) => (
              <ToolCard key={t.id} item={t} rev={t.rev} ctx={ctx} />
            ))}
          </div>
        )}
      </Collapse>
    </div>
  );
}

// ---------------------------------------------------------------- transcript

export function Transcript({ sessionId, cwd, empty, onScrolled }: { sessionId: string; cwd: string; empty?: React.ReactNode; onScrolled?: (scrolled: boolean) => void }) {
  const rt = useStore((s) => s.rt[sessionId]);
  const model = models.get(sessionId);
  const pendingList = rt?.pending;
  const pending = useMemo(() => {
    const m = new Map<string, PermissionRequest>();
    for (const p of pendingList || []) if (p.toolUseId) m.set(p.toolUseId, p);
    return m;
  }, [pendingList]);
  const view = useStore((s) => s.ui.view);
  const ctx = useMemo<ToolCtx>(() => ({ sessionId, cwd, pending, depth: 0, view }), [sessionId, cwd, pending, view]);
  const orphans = (pendingList || []).filter((p) => !p.toolUseId || !model?.tools.has(p.toolUseId));

  const { scroller, inner, onScroll, jump, showJump, settling, reading } = useFollow({ sessionId, model, loaded: Boolean(rt?.loaded), rev: rt?.rev || 0, extra: orphans.length, onScrolled });
  const hasRows = Boolean(rt?.loaded && model?.items.length);
  useLayoutEffect(() => {
    // Measured by test/e2e/perf.mjs: from the click that opened the conversation (store.ts loadSession) to its first rows.
    const mark = `pilcrow:open:${sessionId}`;
    if (!hasRows || !performance.getEntriesByName(mark).length) return;
    performance.measure('pilcrow:first-rows', { start: mark, detail: { id: sessionId } });
    performance.clearMarks(mark);
  }, [sessionId, hasRows]);

  const items = model?.items || [];
  let turnStart = 0;
  for (let k = items.length - 1; k >= 0; k--) {
    if (items[k].kind === 'user') {
      turnStart = items[k].ts || 0;
      break;
    }
  }

  return (
    <div className={`transcript${settling ? ' settling' : ''}${reading ? ' reading' : ''}`} ref={scroller} onScroll={onScroll}>
      <div className="transcript-inner" ref={inner}>
        {!rt?.loaded && rt?.loading && (
          <div className="transcript-loading" role="status" aria-label="Opening the conversation">
            <div className="sk" />
            <div className="sk short" />
            <div className="sk" />
          </div>
        )}
        {rt?.loaded && items.length === 0 && empty}
        <ItemList items={items} ctx={ctx} rev={rt?.rev || 0} running={isBusy(rt?.status)} />
        {orphans.map((p) => (
          <div key={p.requestId} className="tool awaiting orphan">
            <PermissionCard perm={p} ctx={ctx} />
          </div>
        ))}
        <WorkingLine sessionId={sessionId} startTs={turnStart} quiet={liveTurnFolds(items)} />
      </div>
      {showJump && <Jump onClick={jump} />}
    </div>
  );
}

function Jump({ onClick }: { onClick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  useExitGhost(ref, { duration: 180 });
  return (
    <button ref={ref} className="jump" onClick={onClick}>
      <ArrowDown size={14} /> Latest
    </button>
  );
}
