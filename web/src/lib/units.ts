import type { Item } from './transcript';

export type Unit = { kind: 'items'; items: Item[]; key: string } | { kind: 'work'; key: string; work: Item[]; live: boolean };

export const isWork = (it: Item) => it.kind === 'tool' || it.kind === 'thinking';

/**
 * Whatever Claude says stays on the page, every time it says something: a turn can think, write a
 * paragraph, think and work some more, then write again, and each paragraph is part of the answer.
 * Each stretch of work between those paragraphs — reading, searching, editing, the thinking in
 * between — folds to its own one-line summary. The stretch under way is live until the turn ends.
 */
export function groupTurns(items: Item[], running: boolean): Unit[] {
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
  items.forEach((it, i) => {
    const last = i === items.length - 1;
    const boundary = it.kind === 'user' || it.kind === 'compact' || (it.kind === 'notice' && it.tag === 'divider');
    if (isWork(it) || (it.kind === 'notice' && run.length && !boundary)) run.push(it);
    else {
      flushRun(false);
      plain.push(it);
    }
    if (last) flushRun(running);
  });
  flushPlain();
  return out;
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
