import { describe, expect, it } from 'vitest';
import { groupTurns, liveTurnFolds } from '../../web/src/lib/units';
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
