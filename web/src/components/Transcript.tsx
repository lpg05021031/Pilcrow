import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ArrowDown, PencilLine, GitFork, ChevronRight, Info, TriangleAlert, CircleAlert, Layers, Paperclip, Terminal as TermIcon, History, Telescope, CircleCheck, Sparkles } from 'lucide-react';
import { useStore, models, forkSession, insertIntoComposer, toast, call, confirmAction, bumpFs, openLightbox, openSession } from '../lib/store';
import type { Item, ToolItem, UserItem, ThinkingItem, TextItem, NoticeItem, ResultItem, CompactItem, CommandOutputItem } from '../lib/transcript';
import type { PermissionRequest } from '../lib/types';
import { duration, tokens, cost, truncate, relPath, basename } from '../lib/format';
import { Markdown } from './Markdown';
import { ToolCard, type ToolCtx } from './tools/ToolCard';
import { PermissionCard } from './tools/Permission';
import { useNow, CopyButton, WorkingMark } from './ui/bits';
import { useSmoothText, useThrottled, useExitGhost, spring, EASE, takeLaunch } from '../lib/motion';
import { Collapse } from './ui/Collapse';
import { str } from '../lib/tools';
import { groupTurns, liveTurnFolds, type Unit } from '../lib/units';

// ---------------------------------------------------------------- list with step grouping

type Group = { kind: 'steps'; items: ToolItem[]; key: string } | { kind: 'item'; item: Item; key: string };

function groupItems(items: Item[]): Group[] {
  const out: Group[] = [];
  let run: ToolItem[] = [];
  const flush = () => {
    if (run.length) out.push({ kind: 'steps', items: run, key: 's-' + run[0].id });
    run = [];
  };
  for (const it of items) {
    if (it.kind === 'tool' && it.name !== 'AskUserQuestion' && it.name !== 'ExitPlanMode') run.push(it);
    else {
      flush();
      out.push({ kind: 'item', item: it, key: it.id });
    }
  }
  flush();
  return out;
}

/** The items of one slice of the conversation, with runs of tool calls drawn as steps. */
const Flat = memo(function Flat({ items, ctx }: { items: Item[]; ctx: ToolCtx }) {
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
// The grouping itself is pure and lives in lib/units.ts so it can be tested on its own.

// Which folds you opened in the conversation on screen. Opening a conversation starts clean.
const turnOpen = new Map<string, boolean>();
const chapterOpen = new Map<string, boolean>();
export function forgetFolds() {
  turnOpen.clear();
  chapterOpen.clear();
}

/** What Claude is doing right now, for the fold line of a turn that is still running. */
function liveLabel(work: Item[]): string | null {
  for (let i = work.length - 1; i >= 0; i--) {
    const it = work[i];
    if (it.kind === 'tool' && (it.status === 'running' || it.status === 'streaming')) {
      const file = str(it.input, 'file_path') || str(it.input, 'notebook_path');
      switch (toolWord(it)) {
        case 'edit':
          return file ? `Editing ${basename(file)}` : 'Editing a file';
        case 'read':
          return file ? `Reading ${basename(file)}` : 'Reading a file';
        case 'search':
          return 'Searching';
        case 'command':
          return it.input?.description ? truncate(String(it.input.description), 64) : 'Running a command';
        case 'agent':
          return 'Running an agent';
        case 'mcp':
          return 'Using a connector';
        default:
          return `Using ${it.name}`;
      }
    }
    if (it.kind === 'thinking' && it.streaming) return 'Thinking';
  }
  return null;
}

function toolWord(t: ToolItem) {
  const n = t.name;
  if (['Edit', 'MultiEdit', 'Write', 'NotebookEdit'].includes(n)) return 'edit';
  if (n === 'Read' || n === 'NotebookRead') return 'read';
  if (['Grep', 'Glob', 'LS', 'WebSearch', 'WebFetch', 'ToolSearch'].includes(n)) return 'search';
  if (n === 'Bash' || n === 'PowerShell' || n === 'BashOutput') return 'command';
  if (n === 'Task' || n === 'Agent' || n === 'Workflow' || n === 'RunWorkflow') return 'agent';
  if (n.startsWith('mcp__')) return 'mcp';
  return 'other';
}

function workSummary(work: Item[]) {
  const tools = work.filter((it): it is ToolItem => it.kind === 'tool');
  const files = new Set<string>();
  let reads = 0;
  let searches = 0;
  let commands = 0;
  let agents = 0;
  let mcp = 0;
  let other = 0;
  let failed = 0;
  for (const t of tools) {
    if (t.status === 'error' || t.status === 'denied') failed++;
    switch (toolWord(t)) {
      case 'edit':
        files.add(str(t.input, 'file_path') || str(t.input, 'notebook_path') || t.id);
        break;
      case 'read':
        reads++;
        break;
      case 'search':
        searches++;
        break;
      case 'command':
        commands++;
        break;
      case 'agent':
        agents++;
        break;
      case 'mcp':
        mcp++;
        break;
      default:
        other++;
    }
  }
  for (const it of work) if (it.kind === 'notice' && it.level === 'error') failed++;
  const parts: string[] = [];
  if (files.size) parts.push(`edited ${files.size === 1 ? 'a file' : `${files.size} files`}`);
  if (commands) parts.push(`ran ${commands === 1 ? 'a command' : `${commands} commands`}`);
  if (reads) parts.push(`read ${reads === 1 ? 'a file' : `${reads} files`}`);
  if (searches) parts.push(`searched ${searches === 1 ? 'once' : `${searches} times`}`);
  if (agents) parts.push(`${agents === 1 ? 'ran an agent' : `ran ${agents} agents`}`);
  if (mcp) parts.push(`used ${mcp === 1 ? 'a connector' : `${mcp} connector tools`}`);
  if (other) parts.push(`${other} other ${other === 1 ? 'step' : 'steps'}`);
  if (!parts.length) parts.push(work.some((it) => it.kind === 'thinking') ? 'thought it through' : 'worked');
  const text = parts.join(', ');
  return { text: text.charAt(0).toUpperCase() + text.slice(1), failed, steps: tools.length };
}

const WorkFold = memo(function WorkFold({ unit, ctx }: { unit: Extract<Unit, { kind: 'work' }>; ctx: ToolCtx }) {
  const { work, live, key } = unit;
  const waiting = work.some((it) => it.kind === 'tool' && ctx.pending.has(it.id));
  // Normal view keeps every stretch of work to its one line, running or not; the other views open it.
  const defaultOpen = ctx.view === 'thinking' || ctx.view === 'verbose';
  const [open, setOpen] = useState<boolean>(() => turnOpen.get(key) ?? defaultOpen);
  useEffect(() => {
    const on = (e: Event) => (e as CustomEvent).detail === key && setOpen(true);
    window.addEventListener('pilcrow:open-fold', on);
    return () => window.removeEventListener('pilcrow:open-fold', on);
  }, [key]);
  const summary = useMemo(() => workSummary(work), [work]);
  const doing = live ? liveLabel(work) : null;
  const now = useNow(1000, live);
  const started = useStore((s) => (live ? s.rt[ctx.sessionId]?.turnStartedAt || 0 : 0));
  const first = work[0] as Item & { startTs?: number };
  const t0 = live ? started || first?.ts || first?.startTs || 0 : 0;
  const elapsed = t0 ? now - t0 : 0;
  const toggle = () => {
    turnOpen.set(key, !open);
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
            <Chapters items={work} ctx={ctx} />
          </div>
        )}
      </Collapse>
    </div>
  );
});

// ---------------------------------------------------------------- chapters: the work, told in Claude's own words

type Chapter = { key: string; title: TextItem | null; thought: ThinkingItem | null; steps: Item[] };

/**
 * Inside a turn, what Claude says along the way ("I'm reading the source; next I'll…") describes the
 * steps that follow it. So each stretch of narration becomes a chapter heading with its steps under
 * it, and a thought that led straight into the narration is tucked onto the same heading.
 */
function chaptersOf(items: Item[]): Chapter[] {
  const out: Chapter[] = [];
  let cur: Chapter = { key: 'c0', title: null, thought: null, steps: [] };
  let pendingThought: ThinkingItem | null = null;
  const push = () => {
    if (cur.title || cur.thought || cur.steps.length) out.push(cur);
  };
  for (const it of items) {
    if (it.kind === 'thinking') {
      // Hold it: if narration follows, it belongs to that heading.
      if (pendingThought) cur.steps.push(pendingThought);
      pendingThought = it;
      continue;
    }
    if (it.kind === 'text') {
      push();
      cur = { key: 'c-' + it.id, title: it, thought: pendingThought, steps: [] };
      pendingThought = null;
      continue;
    }
    if (pendingThought) {
      cur.steps.push(pendingThought);
      pendingThought = null;
    }
    cur.steps.push(it);
  }
  if (pendingThought) cur.steps.push(pendingThought);
  push();
  return out;
}

function tally(steps: Item[]) {
  const tools = steps.filter((it): it is ToolItem => it.kind === 'tool');
  let edits = 0;
  let commands = 0;
  let reads = 0;
  let searches = 0;
  let agents = 0;
  let mcp = 0;
  let other = 0;
  let failed = 0;
  for (const t of tools) {
    if (t.status === 'error' || t.status === 'denied') failed++;
    const w = toolWord(t);
    if (w === 'edit') edits++;
    else if (w === 'command') commands++;
    else if (w === 'read') reads++;
    else if (w === 'search') searches++;
    else if (w === 'agent') agents++;
    else if (w === 'mcp') mcp++;
    else other++;
  }
  const parts: string[] = [];
  if (edits) parts.push(`${edits} edit${edits > 1 ? 's' : ''}`);
  if (commands) parts.push(`${commands} command${commands > 1 ? 's' : ''}`);
  if (reads) parts.push(`${reads} read${reads > 1 ? 's' : ''}`);
  if (searches) parts.push(`${searches} search${searches > 1 ? 'es' : ''}`);
  if (agents) parts.push(`${agents} agent${agents > 1 ? 's' : ''}`);
  if (mcp) parts.push(`${mcp} connector call${mcp > 1 ? 's' : ''}`);
  if (other) parts.push(`${other} other`);
  const first = tools[0]?.startTs;
  const last = tools[tools.length - 1];
  const ms = first && last ? (last.endTs || last.startTs) - first : 0;
  return { text: parts.join(' · '), failed, ms, n: tools.length };
}

function ChapterView({ ch, ctx, last }: { ch: Chapter; ctx: ToolCtx; last: boolean }) {
  const t = useMemo(() => tally(ch.steps), [ch.steps]);
  const waiting = ch.steps.some((it) => it.kind === 'tool' && ctx.pending.has(it.id));
  const live = ch.steps.some((it) => (it.kind === 'tool' && (it.status === 'running' || it.status === 'streaming')) || (it.kind === 'thinking' && it.streaming)) || Boolean(ch.title?.streaming);
  // Normal view shows only the headings; a chapter opens itself only while it is running or waiting on you.
  const defaultOpen = ctx.view === 'verbose' || waiting || live;
  const [openState, setOpenState] = useState<boolean | null>(() => chapterOpen.get(ch.key) ?? null);
  const open = openState ?? defaultOpen;
  const toggle = () => {
    chapterOpen.set(ch.key, !open);
    setOpenState(!open);
  };
  return (
    <div className={`chap${open ? ' open' : ''}${last ? ' last' : ''}${t.failed ? ' has-failed' : ''}`}>
      <span className="chap-rail" aria-hidden="true">
        <span className={`chap-dot${live ? ' live' : ''}`} />
        {!last && <span className="chap-line" />}
      </span>
      {
        <button className="chap-head" onClick={toggle} aria-expanded={open}>
          <span className={`chap-title${ch.title ? '' : ' untitled'}`}>
            {ch.title ? <Markdown text={ch.title.text} cwd={ctx.cwd} className="md-compact" streaming={Boolean(ch.title.streaming)} /> : t.n ? 'Steps' : 'Thinking'}
          </span>
          {ch.steps.length > 0 && (
            <span className="chap-tally tnum">
              {t.text}
              {t.failed > 0 && <span className="chap-fail"> · {t.failed} failed</span>}
              {t.ms > 1500 ? ` · ${duration(t.ms)}` : ''}
              <ChevronRight size={12} className={`tool-chev${open ? ' open' : ''}`} />
            </span>
          )}
        </button>
      }
      {ch.thought && (
        <div className="chap-thought">
          <Thinking item={ch.thought} view={ctx.view} />
        </div>
      )}
      <Collapse open={open}>
        {() => (
          <div className="chap-steps">
            <Flat items={ch.steps} ctx={ctx} />
          </div>
        )}
      </Collapse>
    </div>
  );
}

function Chapters({ items, ctx }: { items: Item[]; ctx: ToolCtx }) {
  const chapters = useMemo(() => chaptersOf(items), [items]);
  // One chapter with no narration is just a list of steps; the rail would be ceremony.
  if (chapters.length === 1 && !chapters[0].title) return <Flat items={items} ctx={ctx} />;
  return (
    <div className="chapters">
      {chapters.map((ch, i) => (
        <ChapterView key={ch.key} ch={ch} ctx={ctx} last={i === chapters.length - 1} />
      ))}
    </div>
  );
}

/**
 * Long conversations. Units are gathered into pages of a few units each, and a page far from the
 * viewport is an empty box of about the right height until the reader comes near (IntersectionObserver,
 * well ahead of them): no Markdown, no rows, no icons for the thousand turns nobody is looking at,
 * which is what makes a long conversation open at once and stay light. A page keeps its stand-in
 * height until it is drawn (transcript.css), so the scrollbar is the same either way; once drawn, a
 * page stays drawn, and which pages are drawn is remembered per conversation. A unit's page never
 * changes (units only ever append), so nothing is ever moved between boxes and re-mounted.
 */
const PAGE = 8;
const EST = { items: 260, work: 44 }; // stand-in heights, matching transcript.css

/** Where a conversation on screen can be asked to show one of its items (a tool, a message). */
const revealers = new Map<string, (id: string) => boolean>();
const drawnPages = new Map<string, NearSet>();

/** Which pages have been drawn, per conversation; each page listens for its own key only. */
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
    let set = this.listeners.get(k);
    if (!set) this.listeners.set(k, (set = new Set()));
    set.add(fn);
    return () => {
      set!.delete(fn);
      if (!set!.size) this.listeners.delete(k);
    };
  }
}

/**
 * Bring an item of the conversation on screen into view — drawing its page and opening the fold
 * around it first if need be — and return the element, or null when it is not in this conversation.
 */
export async function revealItem(sessionId: string, domId: string, itemId: string): Promise<HTMLElement | null> {
  let el = document.getElementById(domId);
  if (!el && revealers.get(sessionId)?.(itemId)) {
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
      {far
        ? null
        : page.units.map((u) => (
            <UnitBox key={u.key} unit={u} ctx={ctx} />
          ))}
    </div>
  );
});

export const ItemList = memo(function ItemList({ items, ctx, rev, running, flat = false }: { items: Item[]; ctx: ToolCtx; rev: number; running: boolean; flat?: boolean }) {
  // Inside an agent's own transcript there is no "answer" to set apart: every step shows.
  // The items array is mutated in place as the conversation grows, so `rev` is what marks a change.
  const prev = useRef(new Map<string, { sig: string; unit: Unit }>());
  const units = useMemo(() => {
    if (flat) return [];
    // A unit whose items (and their revisions) have not changed keeps its identity, so the component
    // under it skips the render: while a reply streams only the live end re-renders, not the history.
    const next = new Map<string, { sig: string; unit: Unit }>();
    const out = groupTurns(items, running).map((u) => {
      let sig = u.kind === 'work' ? (u.live ? 'W!' : 'W') : 'P';
      for (const it of u.kind === 'work' ? u.work : u.items) sig += `|${it.id}:${it.rev}`;
      const old = prev.current.get(u.key);
      const unit = old && old.sig === sig ? old.unit : u;
      next.set(u.key, { sig, unit });
      return unit;
    });
    prev.current = next;
    return out;
  }, [items, rev, running, flat]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pages keep their identity until a unit in them changes, so a streaming reply reconciles one page.
  const pagesRef = useRef<Page[]>([]);
  const pages = useMemo(() => {
    const out: Page[] = [];
    for (let p = 0; p * PAGE < units.length; p++) {
      const slice = units.slice(p * PAGE, (p + 1) * PAGE);
      const old = pagesRef.current[p];
      const same = old && old.units.length === slice.length && old.units.every((u, i) => u === slice[i]);
      out.push(same ? old : { key: 'pg' + p, units: slice, est: slice.reduce((n, u) => n + EST[u.kind], 0) + 24 * slice.length });
    }
    pagesRef.current = out;
    return out;
  }, [units]);
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

  // Which far-off pages have been drawn (sticky, and remembered per conversation so coming back to one
  // finds the same pages drawn, the same heights, and the same scroll position).
  const nearRef = useRef<NearSet>(null as unknown as NearSet);
  if (!nearRef.current) nearRef.current = drawnPages.get(ctx.sessionId) || new NearSet();
  useEffect(() => {
    drawnPages.set(ctx.sessionId, nearRef.current);
  }, [ctx.sessionId]);
  const io = useRef<IntersectionObserver | null>(null);
  const keys = useRef(new WeakMap<Element, string>());
  const unitsRef = useRef(units);
  unitsRef.current = units;
  const observe = useMemo(
    () => (el: HTMLDivElement | null, key: string) => {
      if (!el) return;
      if (nearRef.current.has(key)) return;
      if (!io.current && typeof IntersectionObserver === 'function') {
        // Pages are drawn one per frame, nearest the viewport first, and not at all while the
        // reader is flinging past them: a fast scroll through the history then costs short frames
        // and the pages fill in as soon as it slows, the way a native list does.
        const queue: { key: string; dist: number }[] = [];
        let drawing: number | null = null;
        let lastTop = 0;
        let lastAt = 0;
        const sc = el.closest('.transcript');
        const draw = () => {
          drawing = null;
          const top = sc ? sc.scrollTop : 0;
          const now = performance.now();
          const flinging = lastAt && now - lastAt < 80 && Math.abs(top - lastTop) > (sc?.clientHeight || 900) * 1.5;
          lastTop = top;
          lastAt = now;
          if (!flinging) {
            queue.sort((a, b) => a.dist - b.dist);
            while (queue.length) if (nearRef.current.add(queue.shift()!.key)) break;
          }
          if (queue.length) drawing = requestAnimationFrame(draw);
        };
        io.current = new IntersectionObserver(
          (entries) => {
            const vh = sc?.clientHeight || window.innerHeight || 900;
            for (const e of entries) {
              if (!e.isIntersecting) continue;
              const k = keys.current.get(e.target);
              io.current?.unobserve(e.target);
              if (!k || nearRef.current.has(k)) continue;
              const r = e.boundingClientRect;
              const dist = r.bottom < 0 ? -r.bottom : r.top > vh ? r.top - vh : 0;
              queue.push({ key: k, dist });
            }
            if (queue.length && drawing == null) drawing = requestAnimationFrame(draw);
          },
          // The conversation scrolls inside its own box, so that box is the root: measured against the
          // viewport, a row scrolled out of it would never count as near, margin or no margin.
          { root: sc, rootMargin: '1600px 0px' },
        );
      }
      keys.current.set(el, key);
      io.current?.observe(el);
    },
    [],
  );
  useEffect(() => () => io.current?.disconnect(), []);
  // Find in the conversation needs every row on the page: opening the bar draws every page, and a
  // drawn page stays drawn, so closing it never moves what the reader is looking at.
  const finding = useStore((s) => s.findOpen);
  useEffect(() => {
    if (finding) for (const pg of pagesRef.current) nearRef.current.add(pg.key);
  }, [finding]);
  const near = typeof IntersectionObserver === 'function' ? nearRef.current : null;
  // Jumps from the timeline or a file's history land on items that may sit in a far page or a closed fold.
  useEffect(() => {
    if (flat) return;
    revealers.set(ctx.sessionId, (id) => {
      const i = unitsRef.current.findIndex((x) => (x.kind === 'work' ? x.work : x.items).some((it) => it.id === id || (it as { uuid?: string }).uuid === id));
      if (i < 0) return false;
      const u = unitsRef.current[i];
      nearRef.current.add('pg' + Math.floor(i / PAGE));
      if (u.kind === 'work') {
        turnOpen.set(u.key, true);
        window.dispatchEvent(new CustomEvent('pilcrow:open-fold', { detail: u.key }));
      }
      return true;
    });
    return () => void revealers.delete(ctx.sessionId);
  }, [ctx.sessionId, flat]);

  if (flat) return <Flat items={items} ctx={ctx} />;
  return (
    <div className="units">
      {pages.map((pg, i) => (
        <PageBox key={pg.key} page={pg} ctx={ctx} live={i >= liveFrom} near={near} observe={observe} />
      ))}
    </div>
  );
});

// Read-only steps that finished cleanly fold into one "Explored" line; edits, agents, failures and
// anything waiting on the user always stay visible as their own rows.
const QUIET = new Set(['Read', 'Grep', 'Glob', 'LS', 'WebSearch', 'WebFetch', 'ToolSearch', 'BashOutput', 'TaskList', 'TaskGet', 'NotebookRead']);
function isQuiet(t: ToolItem, ctx: ToolCtx) {
  if (ctx.pending.has(t.id) || t.status !== 'done') return false;
  return QUIET.has(t.name) || ((t.name === 'Bash' || t.name === 'PowerShell') && !t.result?.isError && !t.input?.run_in_background);
}

function exploreLabel(items: ToolItem[]) {
  const n = (names: string[]) => items.filter((t) => names.includes(t.name)).length;
  const parts: string[] = [];
  const cmds = n(['Bash', 'PowerShell', 'BashOutput']);
  const reads = n(['Read', 'NotebookRead']);
  const searches = n(['Grep', 'Glob', 'LS', 'ToolSearch']);
  const web = n(['WebSearch', 'WebFetch']);
  const tasks = n(['TaskList', 'TaskGet']);
  if (cmds) parts.push(cmds === 1 ? 'ran a command' : `ran ${cmds} commands`);
  if (reads) parts.push(reads === 1 ? 'read a file' : `read ${reads} files`);
  if (searches) parts.push(searches === 1 ? 'searched once' : `searched ${searches} times`);
  if (web) parts.push(web === 1 ? 'checked the web' : `checked ${web} web pages`);
  if (tasks) parts.push('checked tasks');
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

type Seg = { kind: 'tool'; item: ToolItem } | { kind: 'explore'; items: ToolItem[]; key: string };

function Steps({ items, ctx }: { items: ToolItem[]; ctx: ToolCtx }) {
  const [expanded, setExpanded] = useState(false);
  // While Claude is still working, the steps at the tail stay as rows: folding them into "Explored"
  // the moment each one finishes would make the end of the list jump with every tool call. They
  // fold once something follows them, or when the turn is over.
  const live = useStore((s) => ['running', 'starting', 'requires_action'].includes(s.rt[ctx.sessionId]?.status || ''));
  const segs = useMemo(() => {
    const out: Seg[] = [];
    let run: ToolItem[] = [];
    const flush = (tail = false) => {
      if (run.length >= 2 && !(tail && live)) out.push({ kind: 'explore', items: run, key: 'x-' + run[0].id });
      else run.forEach((t) => out.push({ kind: 'tool', item: t }));
      run = [];
    };
    for (const t of items) {
      if (ctx.view !== 'verbose' && isQuiet(t, ctx)) run.push(t);
      else {
        flush();
        out.push({ kind: 'tool', item: t });
      }
    }
    flush(true);
    return out;
  }, [items, items.map((t) => t.status + t.rev).join(), ctx, live]); // eslint-disable-line react-hooks/exhaustive-deps
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

const ItemView = memo(
  function ItemView({ item, ctx }: { item: Item; rev: number; ctx: ToolCtx }) {
    switch (item.kind) {
      case 'user':
        return <UserMessage item={item} ctx={ctx} />;
      case 'text':
        return <AssistantText item={item} ctx={ctx} />;
      case 'thinking':
        return <Thinking item={item} view={ctx.view} />;
      case 'tool':
        return <ToolCard item={item} rev={item.rev} ctx={ctx} perm={ctx.pending.get(item.id)} />;
      case 'notice':
        return <Notice item={item} />;
      case 'compact':
        return <Compact item={item} />;
      case 'command-output':
        return <CommandOutput item={item} ctx={ctx} />;
      case 'result':
        return <ResultFooter item={item} ctx={ctx} />;
      default:
        return null;
    }
  },
  (a, b) => a.item === b.item && a.rev === b.rev && a.ctx === b.ctx,
);

// ---------------------------------------------------------------- items

function UserMessage({ item, ctx }: { item: UserItem; ctx: ToolCtx }) {
  const queued = useStore((s) => s.rt[ctx.sessionId]?.queued?.includes(item.uuid));
  const busy = useStore((s) => ['running', 'starting', 'requires_action'].includes(s.rt[ctx.sessionId]?.status || ''));
  if (item.note) return <HelperNote item={item} ctx={ctx} />;
  if (item.command) {
    return (
      <div className="msg-user command">
        <span className="cmd-chip mono">/{item.command.name}</span>
        {item.command.args && <span className="mono muted">{item.command.args}</span>}
      </div>
    );
  }
  const edit = async () => {
    if (!item.prevUuid) {
      insertIntoComposer(ctx.sessionId, item.text, true);
      return;
    }
    const id = await forkSession(ctx.sessionId, item.prevUuid);
    insertIntoComposer(id, item.text, true);
  };
  const rewind = async () => {
    try {
      const r = await call<{ canRewind: boolean; error?: string; filesChanged?: string[]; insertions?: number; deletions?: number }>('session.rewind', { id: ctx.sessionId, messageId: item.uuid, dryRun: true });
      if (!r.canRewind) return toast(r.error || 'There is no saved copy of the files from before this message.', 'warning');
      const files = (r.filesChanged || []).map((f) => relPath(f, ctx.cwd));
      if (!files.length) return toast('No files changed after this message.', 'info');
      confirmAction({
        title: 'Restore files?',
        body: `Put ${files.length === 1 ? 'this file' : `these ${files.length} files`} back the way ${files.length === 1 ? 'it was' : 'they were'} before this message. The conversation stays as it is.`,
        list: files,
        confirm: 'Restore files',
        danger: true,
        checkbox: item.prevUuid ? { label: 'Also start a new branch of the conversation from before this message', value: false } : undefined,
        onConfirm: async (branch) => {
          const done = await call<{ canRewind: boolean; error?: string; skippedLinks?: number }>('session.rewind', { id: ctx.sessionId, messageId: item.uuid, dryRun: false });
          if (!done.canRewind) return toast(done.error || 'Could not restore the files.', 'error');
          bumpFs();
          toast(`Restored ${files.length} file${files.length > 1 ? 's' : ''}${done.skippedLinks ? ` (${done.skippedLinks} linked file${done.skippedLinks > 1 ? 's' : ''} skipped)` : ''}`, 'success');
          if (branch && item.prevUuid) {
            const id = await forkSession(ctx.sessionId, item.prevUuid);
            insertIntoComposer(id, item.text, true);
          }
        },
      });
    } catch {
      /* the call already showed an error */
    }
  };
  return (
    <div className={`msg-user${queued ? ' queued' : ''}`} lang={langOf(item.text)} id={`msg-${item.uuid}`}>
      {item.from && (
        <span className="msg-from" title="Sent by another session">
          {item.from}
        </span>
      )}
      <div className="msg-bubble">
        {(item.images.length > 0 || item.files.length > 0) && (
          <div className="msg-attachments">
            {item.images.map((src, k) => (
              <img key={k} src={src} className="msg-thumb" alt="Attached image" onClick={() => openLightbox(src)} />
            ))}
            {item.files.map((f) => (
              <span key={f} className="msg-file">
                <Paperclip size={12} /> {f}
              </span>
            ))}
          </div>
        )}
        {item.text && <div className="msg-user-text">{renderMentions(item.text)}</div>}
      </div>
      {queued ? (
        <div className="msg-queued">Queued — Claude reads this after the current step</div>
      ) : (
        <div className="msg-actions">
          <CopyButton text={item.text} />
          <button className="icon-btn sm" title="Edit and resend in a new branch of this conversation" onClick={edit}>
            <PencilLine size={13} />
          </button>
          {item.uuid && !busy && (
            <button className="icon-btn sm" title="Restore files to how they were before this message" onClick={rewind}>
              <History size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Chinese text is marked as Traditional Chinese so the browser picks Taiwanese glyph forms and
// punctuation rules for it; English stays unmarked (and is read with an English voice).
const HAN = /[\u3400-\u9fff\uf900-\ufaff]/;
function langOf(text: string) {
  return HAN.test(text.slice(0, 400)) ? 'zh-Hant-TW' : undefined;
}

function renderMentions(text: string) {
  const parts = text.split(/(@[\w./~-]+[\w/-])/g);
  return parts.map((p, i) => (p.startsWith('@') && p.length > 1 ? <span key={i} className="mention">{p}</span> : p));
}

function AssistantText({ item, ctx }: { item: TextItem; ctx: ToolCtx }) {
  const shown = useSmoothText(item.text, Boolean(item.streaming));
  return (
    <div className="msg-assistant" lang={langOf(item.text)}>
      <Markdown text={shown} streaming={Boolean(item.streaming) || shown.length < item.text.length} cwd={ctx.cwd} />
    </div>
  );
}

function Thinking({ item, view }: { item: ThinkingItem; view?: string }) {
  const [openState, setOpen] = useState<boolean | null>(null);
  const open = openState ?? (view === 'thinking' || view === 'verbose');
  const now = useNow(1000, item.streaming);
  const peek = useThrottled(item.text.slice(-320), 160);
  const secs = Math.max(1, Math.round(((item.endTs || now) - item.startTs) / 1000));
  if (!item.text && !item.streaming && !item.redacted) return null;
  return (
    <div className={`thinking${open ? ' open' : ''}${item.streaming ? ' live' : ''}`}>
      <button className="thinking-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="thinking-ico">
          <Sparkles size={13} />
        </span>
        <span className={item.streaming ? 'shimmer' : ''}>{item.streaming ? 'Thinking' : item.redacted ? 'Reasoned privately' : `Thought for ${secs}s`}</span>
        <ChevronRight size={13} className="thinking-chev" />
      </button>
      {item.streaming && !open && item.text && <div className="thinking-text peek">{peek}</div>}
      <Collapse open={open && Boolean(item.text)}>{() => <div className="thinking-text">{item.text}</div>}</Collapse>
    </div>
  );
}

function Notice({ item }: { item: NoticeItem }) {
  const [open, setOpen] = useState(false);
  if (item.tag === 'divider' || item.tag === 'interrupt') {
    return (
      <div className={`divider ${item.tag}`}>
        <span>{item.text}</span>
      </div>
    );
  }
  if (item.tag === 'task') {
    return (
      <div className={`cli-info task ${item.level}`}>
        {item.level === 'warning' ? <CircleAlert size={12} /> : <CircleCheck size={12} />}
        <div className="grow">{item.text}</div>
      </div>
    );
  }
  if (item.tag === 'retry-ok') {
    return (
      <div className="cli-info info">
        <Info size={12} />
        <div className="grow">{item.text}</div>
      </div>
    );
  }
  if (item.tag === 'cli-info') {
    const first = item.text.split(/(?<=\.)\s/)[0];
    return (
      <div className={`cli-info ${item.level}`}>
        <Info size={12} />
        <div className="grow">
          {open ? <Markdown text={item.text} className="md-compact" /> : <span>{first}</span>}
          {item.text.length > first.length && (
            <button className="link" onClick={() => setOpen(!open)}>
              {open ? 'Less' : 'More'}
            </button>
          )}
        </div>
      </div>
    );
  }
  const Icon = item.level === 'error' ? CircleAlert : item.level === 'warning' ? TriangleAlert : Info;
  return (
    <div className={`notice ${item.level}`}>
      <Icon size={14} />
      <div className="grow">
        <div>
          {item.text}
          {item.count && item.count > 1 ? <span className="faint"> ×{item.count}</span> : null}
        </div>
        {item.detail && (
          <>
            {open ? <pre className="notice-detail">{item.detail}</pre> : null}
            <button className="link" onClick={() => setOpen(!open)}>
              {open ? 'Hide details' : 'Details'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Compact({ item }: { item: CompactItem }) {
  return (
    <div className="divider compact">
      <span>
        Conversation compacted{item.trigger === 'manual' ? '' : ' automatically'}
        {item.preTokens ? ` · ${tokens(item.preTokens)} → ${item.postTokens ? tokens(item.postTokens) : 'summary'}` : ''}
      </span>
    </div>
  );
}

function CommandOutput({ item, ctx }: { item: CommandOutputItem; ctx: ToolCtx }) {
  return (
    <div className="cmd-output">
      <div className="cmd-output-head">
        <TermIcon size={12} /> Command output
      </div>
      <Markdown text={item.text} cwd={ctx.cwd} className="md-compact" />
    </div>
  );
}

function ResultFooter({ item, ctx }: { item: ResultItem; ctx: ToolCtx }) {
  if (!item.ok) {
    return (
      <div className="notice error">
        <CircleAlert size={14} />
        <div className="grow">
          <div>{item.subtype === 'error_max_turns' ? 'Stopped: reached the turn limit' : item.subtype === 'error_max_budget_usd' ? 'Stopped: reached the budget limit' : item.text || 'The turn ended with an error'}</div>
          {item.errors?.length ? <pre className="notice-detail">{item.errors.join('\n')}</pre> : null}
        </div>
      </div>
    );
  }
  return (
    <div className="turn-foot" title={`${duration(item.durationMs)}${item.outputTokens ? ` · ${tokens(item.outputTokens)} tokens out` : ''}${item.inputTokens ? ` · ${tokens(item.inputTokens)} in` : ''}${item.costUsd ? ` · ${cost(item.costUsd)}` : ''}`}>
      <span className="turn-mark" aria-hidden="true">✱</span>
      <span className="tnum">{duration(item.durationMs)}</span>
      <span className="tnum turn-detail">
        {item.outputTokens ? `${tokens(item.outputTokens)} tokens` : ''}
        {item.costUsd ? `${item.outputTokens ? ' · ' : ''}${cost(item.costUsd)}` : ''}
      </span>
      {item.chainUuid && (
        <button className="link turn-fork" title="Start a new session that continues from this point" onClick={() => forkSession(ctx.sessionId, item.chainUuid)}>
          <GitFork size={12} /> Fork from here
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- working indicator

// Words from the composing room, since the mark is a pilcrow: what an editor does while a page takes shape.
const VERBS = ['Reading', 'Drafting', 'Composing', 'Outlining', 'Revising', 'Proofing', 'Collating', 'Annotating', 'Indexing', 'Marking up', 'Typesetting', 'Kerning'];

function WorkingLine({ sessionId, startTs, quiet }: { sessionId: string; startTs: number; quiet: boolean }) {
  const rt = useStore((s) => s.rt[sessionId]);
  const now = useNow(1000);
  const [verb] = useState(() => VERBS[Math.floor(Math.random() * VERBS.length)]);
  const status = rt?.status;
  if (!(status === 'running' || status === 'starting')) return null;
  const elapsed = startTs ? now - startTs : 0;
  if (quiet && !rt?.compacting) return null; // the turn's own fold line is breathing already
  return (
    <div className="working">
      <WorkingMark />
      <span className="working-verb shimmer">{rt?.compacting ? 'Compacting the conversation' : status === 'starting' ? 'Starting Claude Code' : `${verb}…`}</span>
      <span className="working-meta tnum">
        {elapsed > 1000 ? duration(elapsed) : ''}
        {rt?.thinkingTokens ? ` · ${tokens(rt.thinkingTokens)} thinking tokens` : ''}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- transcript

// Where each conversation was scrolled to, so switching away and back keeps your place: the row at
// the top of the view and how far it was from the edge (a scroll offset would drift, since rows far
// off screen only have estimated heights until they have been seen).
const scrollMemory = new Map<string, { index: number; offset: number; bottom: boolean }>();

export function Transcript({ sessionId, cwd, empty, onScrolled }: { sessionId: string; cwd: string; empty?: React.ReactNode; onScrolled?: (scrolled: boolean) => void }) {
  const rt = useStore((s) => s.rt[sessionId]);
  const model = models.get(sessionId);
  // Opening a conversation starts it clean: every fold closed, before any turn reads its memory.
  const shownSid = useRef('');
  if (shownSid.current !== sessionId) {
    shownSid.current = sessionId;
    forgetFolds();
  }
  const pendingList = rt?.pending;
  const pending = useMemo(() => {
    const m = new Map<string, PermissionRequest>();
    for (const p of pendingList || []) if (p.toolUseId) m.set(p.toolUseId, p);
    return m;
  }, [pendingList]);
  const view = useStore((s) => s.ui.view);
  const ctx = useMemo<ToolCtx>(() => ({ sessionId, cwd, pending, depth: 0, view }), [sessionId, cwd, pending, view]);
  const orphans = (pendingList || []).filter((p) => !p.toolUseId || !model?.tools.has(p.toolUseId));

  const scroller = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const lastSet = useRef(0);
  const raf = useRef<number | null>(null);
  const anchorRaf = useRef<number | null>(null);
  const landing = useRef(false);
  // When the reader last moved the page themselves (wheel, keys, the scrollbar, a finger). Never,
  // to begin with: at 0 it would read as "just now" for the first half second of the page's life,
  // and a far page taking its real height then (the position drops) counted as the reader leaving
  // the live end, so a long conversation opened at start-up landed short of its end.
  const userScrollAt = useRef(-Infinity);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const mark = () => (userScrollAt.current = performance.now());
    const onWheel = (e: WheelEvent) => e.deltaY < 0 && mark();
    const onKey = (e: KeyboardEvent) => ['ArrowUp', 'PageUp', 'Home'].includes(e.key) && mark();
    const onDown = (e: PointerEvent) => e.target === el && mark(); // the scrollbar itself
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('keydown', onKey);
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('touchmove', mark, { passive: true });
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('keydown', onKey);
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('touchmove', mark);
    };
  }, [sessionId]);
  const [showJump, setShowJump] = useState(false);
  // Items already on screen when a conversation opens must not all animate in at once.
  const [settling, setSettling] = useState(true);
  // Up in the history (not following the live end): the browser then keeps the rows in view still
  // while rows above take their real size (transcript.css). State, not a class set by hand, so a
  // re-render cannot wipe it.
  const [reading, setReading] = useState(false);
  const readingRef = useRef(false);
  const markReading = (v: boolean) => {
    if (readingRef.current === v) return;
    readingRef.current = v;
    setReading(v);
  };

  // Follow new output smoothly instead of snapping to the bottom on every streamed token.
  const follow = () => {
    if (raf.current != null) return;
    const step = () => {
      const el = scroller.current;
      if (!el || !atBottom.current) return void (raf.current = null);
      const target = el.scrollHeight - el.clientHeight;
      const d = target - el.scrollTop;
      if (d <= 0.5) return void (raf.current = null);
      // While a conversation is still landing after opening, go straight there: easing from a
      // stand-in height to the real one would read as the page scrolling itself.
      el.scrollTop = d < 1.5 || landing.current ? target : el.scrollTop + Math.max(1, d * 0.24);
      lastSet.current = el.scrollTop;
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
  };

  const settled = useRef(false);
  useLayoutEffect(() => {
    atBottom.current = true;
    settled.current = false;
    setSettling(true);
    const el = scroller.current;
    const saved = scrollMemory.get(sessionId);
    const anchor = saved && !saved.bottom && rt?.loaded ? (inner.current?.querySelector('.units')?.children[saved.index] as HTMLElement | undefined) : undefined;
    if (el && saved && anchor) {
      // Coming back to a conversation you had scrolled up in: return to that spot, not the bottom.
      // Boxes that have not been laid out yet only have stand-in heights, so the first placing is
      // approximate; once the frame has painted the boxes around the spot have their real heights,
      // and the same measure lands exactly. Two more passes cover the boxes that fill in after that.
      const place = () => {
        if (!anchor.isConnected) return;
        el.scrollTop = anchor.offsetTop - saved.offset;
        lastSet.current = el.scrollTop;
      };
      place();
      atBottom.current = false;
      markReading(true);
      setShowJump(true);
      let passes = 0;
      const again = () => {
        if (atBottom.current || passes++ >= 3) return;
        place();
        requestAnimationFrame(again);
      };
      requestAnimationFrame(again);
    } else if (el) {
      // Pages far from the viewport take their stand-in heights only once the browser has decided
      // which are off screen, which happens after this first layout; so land at the bottom now and
      // again over the next frames, until the height stops changing.
      const land = () => {
        el.scrollTop = el.scrollHeight;
        lastSet.current = el.scrollTop;
      };
      land();
      markReading(false);
      // The browser settles which pages are off screen in its own time (it can take a while on a
      // long conversation), so keep landing until the height has held still for a few frames.
      landing.current = true;
      let passes = 0;
      let still = 0;
      let lastH = el.scrollHeight;
      const again = () => {
        if (!atBottom.current || !el.isConnected || passes++ >= 120 || still >= 12) {
          landing.current = false;
          return;
        }
        const h = el.scrollHeight;
        if (h === lastH) still++;
        else {
          still = 0;
          lastH = h;
          land();
        }
        requestAnimationFrame(again);
      };
      requestAnimationFrame(again);
    }
    // History that is already there appears at once; a brand-new conversation animates from its first row.
    const fresh = (models.get(sessionId)?.items.length || 0) === 0 && rt?.loaded;
    const t = setTimeout(
      () => {
        settled.current = true;
        setSettling(false);
      },
      fresh ? 0 : 450,
    );
    return () => clearTimeout(t);
  }, [sessionId, rt?.loaded]);

  // New rows ease in where they land. Only rows added after the conversation has settled animate,
  // so opening a long session or switching between sessions never replays the whole history.
  useEffect(() => {
    const body = inner.current;
    if (!body || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const mo = new MutationObserver((records) => {
      if (!settled.current) return;
      for (const r of records) {
        const parent = r.target as HTMLElement;
        // A history box being drawn as the reader approaches is not news: only new rows in the live
        // boxes at the end, and new boxes themselves, ease in.
        if (parent !== body && !parent.matches?.('.units, .page.live, .page.live > .unit, .steps, .explore-body, .agent-children')) continue;
        r.addedNodes.forEach((added) => {
          let n = added;
          // A new unit (or page) arrives with its first row already inside; the row is what moves.
          if (n instanceof HTMLElement && n.classList.contains('page')) n = n.firstElementChild as Node;
          if (n instanceof HTMLElement && n.classList.contains('unit')) n = n.firstElementChild as Node;
          if (!(n instanceof HTMLElement) || n.classList.contains('working') || n.classList.contains('transcript-loading')) return;
          // Your message flies up out of the composer to its place in the conversation, settling on a
          // spring, the way a sent message does; Claude's rows simply rise.
          const user = n.classList.contains('msg-user');
          const { snappy, snappyT } = spring();
          const from = user ? takeLaunch() : null;
          const bubble = user ? n.querySelector<HTMLElement>('.msg-bubble') : null;
          if (from && bubble) {
            const to = bubble.getBoundingClientRect();
            const dx = Math.min(0, from.right - 16 - to.right);
            const dy = from.top - to.top;
            bubble.animate(
              [
                { opacity: 0.5, transform: `translate(${dx}px, ${dy}px) scale(0.94)`, transformOrigin: 'right bottom' },
                { opacity: 1, transform: 'none', transformOrigin: 'right bottom' },
              ],
              { duration: snappyT, easing: snappy },
            );
            return;
          }
          n.animate(
            [
              { opacity: 0, transform: `translateY(${user ? 12 : 5}px)${user ? ' scale(0.96)' : ''}`, transformOrigin: 'right bottom' },
              { opacity: 1, transform: 'none', transformOrigin: 'right bottom' },
            ],
            user ? { duration: snappyT, easing: snappy } : { duration: 240, easing: EASE },
          );
        });
      }
    });
    mo.observe(body, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [sessionId]);

  useLayoutEffect(() => {
    if (atBottom.current) return follow();
    const el = scroller.current;
    // The reader is up in the history: offer "Latest" — unless the page has in fact ended up at the
    // bottom (a fold above closed, the window grew), in which case there is nowhere to go.
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
      atBottom.current = true;
      lastSet.current = el.scrollTop;
      markReading(false);
      setShowJump(false);
    } else setShowJump(true);
  }, [rt?.rev, orphans.length]);  

  useEffect(() => {
    const body = inner.current;
    if (!body) return;
    // The content growing, or the view shrinking (the composer growing as you type, a panel opening,
    // the window resized): either leaves room under the last row, and both are followed.
    const ro = new ResizeObserver((entries) => {
      if (!atBottom.current) return;
      const el = scroller.current;
      // The view itself changing size (the composer growing as you type, a panel opening) is kept
      // in step at once, in the same frame: easing after it would make the conversation twitch with
      // every line you type. New content below is what the smooth follow is for.
      if (el && entries.some((e) => e.target === el)) {
        el.scrollTop = el.scrollHeight - el.clientHeight;
        lastSet.current = el.scrollTop;
        return;
      }
      follow();
    });
    ro.observe(body);
    if (scroller.current) ro.observe(scroller.current);
    return () => {
      ro.disconnect();
      if (raf.current != null) cancelAnimationFrame(raf.current);
      raf.current = null;
    };
  }, [sessionId]);  

  const onScroll = () => {
    const el = scroller.current!;
    // Raised by the follow itself (the position is exactly the one it set): reading scrollHeight here
    // would force a layout for every streamed frame, and all it would say is what we already know —
    // we are at the bottom. A reader's scroll lands anywhere else.
    if (atBottom.current && el.scrollTop === lastSet.current) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom) {
      atBottom.current = true;
      setShowJump(false);
    } else if (el.scrollTop < lastSet.current - 2 && performance.now() - userScrollAt.current < 500) {
      // The reader scrolled up: stop following. Only their own input counts — the position can
      // also fall when a page above takes its real, smaller height, and that is not a reader leaving.
      atBottom.current = false;
    }
    lastSet.current = Math.max(lastSet.current, el.scrollTop);
    if (el.scrollTop < lastSet.current - 2) lastSet.current = el.scrollTop;
    onScrolled?.(el.scrollTop > 4);
    markReading(!atBottom.current);
    if (!rt?.loaded) return;
    if (atBottom.current) {
      // Following the live end: nothing to anchor to, and a layout read here (once per streamed
      // frame, since the follow itself scrolls) would be the costliest thing on the page.
      scrollMemory.set(sessionId, { index: 0, offset: 0, bottom: true });
      return;
    }
    if (anchorRaf.current != null) return;
    anchorRaf.current = requestAnimationFrame(() => {
      anchorRaf.current = null;
      const kids = inner.current?.querySelector('.units')?.children;
      let index = 0;
      if (kids && kids.length) {
        // The boxes are stacked in order, so the first one reaching below the top edge is found by
        // bisection: a scan of every box on every scroll event forced a layout read per box.
        let lo = 0;
        let hi = kids.length - 1;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          const k = kids[mid] as HTMLElement;
          if (k.offsetTop + k.offsetHeight > el.scrollTop) hi = mid;
          else lo = mid + 1;
        }
        index = lo;
      }
      const k = kids?.[index] as HTMLElement | undefined;
      scrollMemory.set(sessionId, { index, offset: k ? k.offsetTop - el.scrollTop : 0, bottom: atBottom.current });
    });
  };

  const jump = () => {
    atBottom.current = true;
    setShowJump(false);
    markReading(false);
    follow();
  };
  // Your own message goes to the end of the conversation, and so do you.
  useEffect(() => {
    const on = (e: Event) => (e as CustomEvent).detail === sessionId && jump();
    window.addEventListener('pilcrow:sent', on);
    return () => window.removeEventListener('pilcrow:sent', on);
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

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
          <div className="transcript-loading">
            <div className="sk" />
            <div className="sk short" />
            <div className="sk" />
          </div>
        )}
        {rt?.loaded && items.length === 0 && empty}
        <ItemList items={items} ctx={ctx} rev={rt?.rev || 0} running={rt?.status === 'running' || rt?.status === 'starting' || rt?.status === 'requires_action'} />
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

// ---------------------------------------------------------------- a helper's note to its lead (HELPERS.md §5.4)

const NOTE_RE = /<helper-note\s+([^>]*)>\n?([\s\S]*?)\n?<\/helper-note>/g;
const ATTR_RE = /(\w+)="([^"]*)"/g;

function parseNotes(text: string) {
  const parts: Record<string, string>[] = [];
  const tail: string[] = [];
  let last = 0;
  for (const m of text.matchAll(NOTE_RE)) {
    const before = text.slice(last, m.index).trim();
    if (before) tail.push(before);
    const attrs: Record<string, string> = {};
    for (const a of m[1].matchAll(ATTR_RE)) attrs[a[1]] = a[2];
    attrs.body = m[2].trim();
    parts.push(attrs);
    last = (m.index || 0) + m[0].length;
  }
  const rest = text.slice(last).trim();
  if (rest) tail.push(rest);
  return { parts, tail: tail.join(' ') };
}

/** Drawn apart from the person's messages: left-aligned, a rule in the state's colour, the helper's name
 *  and numbers in the head, its handoff as quiet prose, Open at the right. */
function HelperNote({ item, ctx }: { item: UserItem; ctx: ToolCtx }) {
  const helpers = useStore((s) => s.rt[ctx.sessionId]?.helpers);
  const { parts, tail } = parseNotes(item.text);
  const open = (name: string) => {
    const h = helpers?.find((x) => x.name === name);
    if (h) openSession(h.id);
    else toast(`${name} is no longer a helper of this session`, 'info');
  };
  return (
    <div className="msg-note" id={`msg-${item.uuid}`}>
      {(parts.length ? parts : [{ name: 'note', state: '', body: item.text }]).map((p, k) => (
        <div key={k} className={`note ${p.state || ''}`}>
          <div className="note-head">
            <span className="note-name">{p.name}</span>
            <span className="note-meta faint tnum">
              {[p.state?.replace('_', ' '), p.elapsed, p.cost, p.files && p.files !== '0' ? `${p.files} files +${p.add || 0} −${p.del || 0}` : ''].filter(Boolean).join(' · ')}
            </span>
            <span className="grow" />
            <button className="btn xs ghost" onClick={() => open(p.name)} title="Open the helper's session">
              Open
            </button>
          </div>
          {p.body && <div className="note-body">{p.body}</div>}
        </div>
      ))}
      {tail && <div className="note-tail faint small">{tail}</div>}
    </div>
  );
}
