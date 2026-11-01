import type { Item, ToolItem } from './transcript';

export type Unit = { kind: 'items'; items: Item[]; key: string } | { kind: 'work'; key: string; work: Item[]; live: boolean };

export const isWork = (it: Item) => it.kind === 'tool' || it.kind === 'thinking';

const itemsOf = (u: Unit) => (u.kind === 'work' ? u.work : u.items);

/**
 * Whatever Claude says stays on the page, every time it says something: a turn can think, write a
 * paragraph, think and work some more, then write again, and each paragraph is part of the answer.
 * Each stretch of work between those paragraphs — reading, searching, editing, the thinking in
 * between — folds to its own one-line summary. The stretch under way is live until the turn ends.
 * `from` starts the grouping at an item where a unit starts (extendTurns).
 */
export function groupTurns(items: Item[], running: boolean, from = 0): Unit[] {
  const out: Unit[] = [];
  let run: Item[] = [];
  let plain: Item[] = [];
  const flushPlain = () => {
    if (plain.length) out.push({ kind: 'items', items: plain, key: 'P-' + plain[0].id });
    plain = [];
  };
  const flushRun = (live: boolean) => {
    if (!run.length) return;
    if (run.some(isWork)) {
      flushPlain();
      out.push({ kind: 'work', key: 'W-' + run[0].id, work: run, live });
    } else plain.push(...run); // only notices: nothing to fold
    run = [];
  };
  for (let i = from; i < items.length; i++) {
    const it = items[i];
    const boundary = it.kind === 'user' || it.kind === 'compact' || (it.kind === 'notice' && it.tag === 'divider');
    if (isWork(it) || (it.kind === 'notice' && run.length && !boundary)) run.push(it);
    else {
      flushRun(false);
      plain.push(it);
    }
  }
  flushRun(running);
  flushPlain();
  return out;
}

/** What ItemList keeps from one pass to the next (extendTurns). */
export interface Turns {
  /** The array grouped: a model's items, which only ever grow at the end. */
  items: Item[];
  units: Unit[];
  /** Per unit, the sum of its items' revs when it was last looked at. */
  sums: number[];
  /** Where the last unit starts in `items`. */
  from: number;
  /** The model's rev less the revs of the items from `from`: it moves only when an earlier item changes. */
  base: number;
  /** The first unit that is new or changed since the last pass. */
  changed: number;
}

function revSum(u: Unit) {
  let n = 0;
  for (const it of itemsOf(u)) n += it.rev;
  return n;
}

/**
 * groupTurns for a conversation as it grows, at the cost of the turn under way rather than the whole
 * conversation. Items only append and a unit before the last never gains or loses an item, so only
 * the items from the last unit's start are grouped again. An earlier item can still change (a tool
 * running in the background finishes turns later): `rev` is the model's rev, which is the sum of the
 * top-level items' revs (TranscriptModel.bump), so the earlier units are read again only when that
 * sum, less the turn under way, has moved. A unit whose items are unchanged keeps its identity, so
 * the component under it skips the render.
 */
export function extendTurns(prev: Turns | null, items: Item[], running: boolean, rev: number): Turns {
  if (!prev || prev.items !== items || prev.from > items.length) {
    const units = groupTurns(items, running);
    const sums = units.map(revSum);
    const last = units[units.length - 1];
    return { items, units, sums, from: items.length - (last ? itemsOf(last).length : 0), base: rev - (sums[sums.length - 1] || 0), changed: 0 };
  }
  const units = prev.units.slice(0, -1);
  const sums = prev.sums.slice(0, -1);
  let changed = Infinity;
  let from = prev.from;
  let tail = 0;
  for (const u of groupTurns(items, running, prev.from)) {
    const k = units.length;
    const was = prev.units[k];
    const n = revSum(u);
    const same = was && was.key === u.key && prev.sums[k] === n && itemsOf(was).length === itemsOf(u).length && (u.kind !== 'work' || (was.kind === 'work' && was.live === u.live));
    if (!same) changed = Math.min(changed, k);
    units.push(same ? was : u);
    sums.push(n);
    tail += n;
  }
  for (let k = Math.max(0, prev.units.length - 1); k < units.length - 1; k++) from += itemsOf(units[k]).length;
  if (rev - tail !== prev.base) {
    for (let k = 0; k < prev.units.length - 1; k++) {
      const n = revSum(units[k]);
      if (n === sums[k]) continue;
      const u = units[k];
      units[k] = u.kind === 'work' ? { ...u, work: u.work.slice() } : { ...u, items: u.items.slice() };
      sums[k] = n;
      changed = Math.min(changed, k);
    }
  }
  return { items, units, sums, from, base: rev - (sums[sums.length - 1] || 0), changed: Math.min(changed, units.length) };
}

/** True when the stretch under way has work of its own on the page to carry the working mark. */
export function liveTurnFolds(items: Item[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (isWork(it)) return true;
    if (it.kind !== 'notice') return false;
  }
  return false;
}

export type Group = { kind: 'steps'; items: ToolItem[]; key: string } | { kind: 'item'; item: Item; key: string };

/** A slice of the conversation with each run of tool calls gathered into one group of steps. */
export function groupItems(items: Item[]): Group[] {
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
