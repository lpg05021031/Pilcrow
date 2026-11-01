import { describe, expect, it } from 'vitest';
import { chaptersOf, extendTurns, groupItems, groupTurns, liveTurnFolds, type Turns, type Unit } from '../../web/src/lib/units';
import type { Item, ToolItem } from '../../web/src/lib/transcript';

let n = 0;
const user = (text = 'hi'): Item => ({ kind: 'user', id: 'u' + ++n, uuid: 'uu' + n, rev: 0, text, images: [], files: [] });
const text = (t = 'answer'): Item => ({ kind: 'text', id: 't' + ++n, rev: 0, text: t, streaming: false });
const tool = (name = 'Read', status: ToolItem['status'] = 'done'): ToolItem => ({ kind: 'tool', id: 'tool' + ++n, rev: 0, name, input: {}, inputJson: '', inputDone: true, status, children: [], startTs: 0, parentId: null });
const thinking = (): Item => ({ kind: 'thinking', id: 'th' + ++n, rev: 0, text: 'hmm', streaming: false, startTs: 0 });
const notice = (tag?: string): Item => ({ kind: 'notice', id: 'n' + ++n, rev: 0, level: 'info', text: 'note', tag });
const result = (): Item => ({ kind: 'result', id: 'r' + ++n, rev: 0, ok: true, subtype: 'success', durationMs: 1, costUsd: 0, turns: 1 });

describe('groupTurns', () => {
  it('a plain exchange is one items unit', () => {
    const units = groupTurns([user(), text(), result()], false);
    expect(units.map((u) => u.kind)).toEqual(['items']);
  });
  it('work between what Claude says folds on its own; the words stay out', () => {
    const items = [user(), text('first I will look'), tool(), tool(), text('done'), result()];
    const units = groupTurns(items, false);
    expect(units.map((u) => u.kind)).toEqual(['items', 'work', 'items']);
    expect(units[1].kind === 'work' && units[1].work.length).toBe(2);
    expect(units[1].kind === 'work' && units[1].live).toBe(false);
  });
  it('the stretch under way is live while the turn runs', () => {
    const units = groupTurns([user(), tool('Read', 'running')], true);
    expect(units[1]).toMatchObject({ kind: 'work', live: true });
    const done = groupTurns([user(), tool('Read', 'done')], false);
    expect(done[1]).toMatchObject({ kind: 'work', live: false });
  });
  it('thinking alone counts as work; a notice inside a run stays in it', () => {
    const units = groupTurns([user(), thinking(), notice(), tool(), text()], false);
    expect(units.map((u) => u.kind)).toEqual(['items', 'work', 'items']);
    expect(units[1].kind === 'work' && units[1].work.map((i) => i.kind)).toEqual(['thinking', 'notice', 'tool']);
  });
  it('a divider notice is a boundary', () => {
    const units = groupTurns([user(), tool(), notice('divider'), tool()], false);
    expect(units.map((u) => u.kind)).toEqual(['items', 'work', 'items', 'work']);
  });
  it('a run of only notices is not folded', () => {
    const units = groupTurns([user(), notice(), notice()], false);
    expect(units.map((u) => u.kind)).toEqual(['items']);
  });
  it('unit keys are stable: the first item names the unit', () => {
    const items = [user(), tool()];
    const a = groupTurns(items, true);
    const b = groupTurns([...items, tool()], true);
    expect(a[1].key).toBe(b[1].key);
    expect(a[0].key).toBe('P-' + items[0].id);
  });
});

describe('liveTurnFolds', () => {
  it('true when the tail is work (through notices), false when it is words', () => {
    expect(liveTurnFolds([user(), tool('Read', 'running')])).toBe(true);
    expect(liveTurnFolds([user(), tool(), notice()])).toBe(true);
    expect(liveTurnFolds([user(), tool(), text()])).toBe(false);
    expect(liveTurnFolds([user()])).toBe(false);
    expect(liveTurnFolds([])).toBe(false);
  });
});

/** The shape of a grouping, for comparing two of them. */
const shape = (units: Unit[]) => units.map((u) => ({ key: u.key, kind: u.kind, ids: (u.kind === 'work' ? u.work : u.items).map((i) => i.id), live: u.kind === 'work' ? u.live : null }));
/** What TranscriptModel.rev is: the sum of the top-level items' revs. */
const total = (items: Item[]) => items.reduce((n, it) => n + it.rev, 0);
const bump = (it: Item) => void it.rev++;

describe('extendTurns', () => {
  const conversation = (): Item[] => [user(), text('first'), tool(), thinking(), notice(), tool(), text('then'), result(), user(), tool(), notice('divider'), tool(), text(), user(), notice(), notice(), result()];

  it('groups a growing conversation exactly as groupTurns does, item by item, running or not', () => {
    for (const running of [false, true]) {
      const all = conversation();
      const items: Item[] = [];
      let turns: Turns | null = null;
      for (const it of all) {
        items.push(it);
        bump(it);
        turns = extendTurns(turns, items, running, total(items));
        expect(shape(turns.units)).toEqual(shape(groupTurns(items, running)));
      }
    }
  });

  it('keeps every unit before the turn under way, as the same object, while the turn grows', () => {
    const items = conversation();
    const a = extendTurns(null, items, true, total(items));
    items.push(tool('Read', 'running'));
    const b = extendTurns(a, items, true, total(items));
    expect(b.units.slice(0, -1).every((u, i) => u === a.units[i])).toBe(true);
    expect(b.changed).toBe(b.units.length - 1);
    // Nothing new at all: every unit is the same, and nothing is marked changed.
    const c = extendTurns(b, items, true, total(items));
    expect(c.units.every((u, i) => u === b.units[i])).toBe(true);
    expect(c.changed).toBe(c.units.length);
  });

  it('an earlier item that changes (a background task finishing) gives its unit, alone, a new identity', () => {
    const items = conversation();
    const a = extendTurns(null, items, false, total(items));
    const early = items[2]; // the first tool, in the first turn's work
    bump(early);
    const b = extendTurns(a, items, false, total(items));
    const i = a.units.findIndex((u) => u.kind === 'work' && u.work.includes(early));
    expect(b.units[i]).not.toBe(a.units[i]);
    expect(b.units[i].kind === 'work' && b.units[i].work).not.toBe(a.units[i].kind === 'work' && a.units[i].work);
    expect(shape([b.units[i]])).toEqual(shape([a.units[i]]));
    expect(b.units.filter((u, k) => u !== a.units[k])).toHaveLength(1);
    expect(b.changed).toBe(i);
  });

  it('the turn ending turns the live fold off', () => {
    const items = [user(), tool('Read', 'running')];
    const a = extendTurns(null, items, true, total(items));
    const b = extendTurns(a, items, false, total(items));
    expect(a.units[1]).toMatchObject({ kind: 'work', live: true });
    expect(b.units[1]).toMatchObject({ kind: 'work', live: false });
  });

  it('a different items array (the conversation loaded again) is grouped from the start', () => {
    const a = extendTurns(null, conversation(), false, 0);
    const other = [user(), text()];
    expect(shape(extendTurns(a, other, false, 0).units)).toEqual(shape(groupTurns(other, false)));
  });
});

describe('groupItems', () => {
  it('runs of tool calls become steps; questions and plans stand on their own', () => {
    const items = [text(), tool(), tool(), tool('AskUserQuestion'), tool(), text()];
    expect(groupItems(items).map((g) => (g.kind === 'steps' ? `steps:${g.items.length}` : g.item.kind))).toEqual(['text', 'steps:2', 'tool', 'steps:1', 'text']);
  });
});

describe('chaptersOf', () => {
  it('narration heads the steps after it, and a thought just before it rides on the heading', () => {
    const th = thinking();
    const t1 = text('reading the source');
    const th2 = thinking();
    const items = [tool(), th, t1, tool(), tool(), th2, text('now the tests'), tool()];
    const ch = chaptersOf(items);
    expect(ch.map((c) => [c.title?.text ?? null, c.steps.length])).toEqual([
      [null, 1],
      ['reading the source', 2],
      ['now the tests', 1],
    ]);
    expect([ch[1].thought, ch[2].thought]).toEqual([th, th2]);
  });
});
