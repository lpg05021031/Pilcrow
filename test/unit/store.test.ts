import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyConfigUi, dismissToast, evictModels, memo, models, setDialog, toast, useStore } from '../../web/src/lib/store';
import { TranscriptModel, findInItems, type Item } from '../../web/src/lib/transcript';
import { isBusy, isWorking, type Config } from '../../web/src/lib/types';

// The store's own logic, without a window: what toasts collapse into, which conversations stay in
// memory and what each forgets, and how a saved layout is read back.
const set = useStore.setState;

describe('toasts', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    for (const t of useStore.getState().toasts) dismissToast(t.id);
    vi.useRealTimers();
  });

  it('the same message while it shows is counted on the one showing, not stacked', () => {
    toast('Saved notes.md', 'success');
    toast('Saved notes.md', 'success');
    toast('Saved notes.md', 'info'); // another level is another toast
    const ts = useStore.getState().toasts;
    expect(ts.map((t) => [t.text, t.level, t.count])).toEqual([
      ['Saved notes.md', 'success', 2],
      ['Saved notes.md', 'info', 1],
    ]);
  });

  it('a repeat keeps its twin up for a full spell again', () => {
    toast('Copied', 'info');
    vi.advanceTimersByTime(4000);
    toast('Copied', 'info');
    vi.advanceTimersByTime(4000);
    expect(useStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(600);
    expect(useStore.getState().toasts).toHaveLength(0);
  });

  it('at most five show; the oldest goes', () => {
    for (let i = 0; i < 7; i++) toast(`message ${i}`, 'info');
    expect(useStore.getState().toasts.map((t) => t.text)).toEqual(['message 2', 'message 3', 'message 4', 'message 5', 'message 6']);
  });

  it('known failures are said in plain words, with the way out attached', () => {
    toast('Claude Code returned an error result: OAuth session expired', 'error');
    toast('429 Too Many Requests', 'error');
    toast('fetch failed: ECONNREFUSED 127.0.0.1:8080', 'error');
    toast('OAuth session expired', 'warning'); // only errors are rewritten
    const ts = useStore.getState().toasts;
    expect(ts[0].text).toMatch(/^Claude Code is signed out/);
    expect(ts[0].action?.label).toBe('Settings');
    ts[0].action?.run();
    expect(useStore.getState().dialogs.settings).toBe('claude');
    setDialog('settings', false);
    expect(ts[1].text).toMatch(/^The model is busy right now/);
    expect(ts[2].text).toBe('Could not reach the model endpoint — fetch failed: ECONNREFUSED 127.0.0.1:8080');
    expect(ts[3].text).toBe('OAuth session expired');
  });
});

describe('evictModels', () => {
  afterEach(() => {
    models.clear();
    set({ rt: {}, activeId: null });
  });

  it('keeps the six most recent, and never the one on screen, one loading, or one Claude is working in', () => {
    const ids = Array.from({ length: 10 }, (_, i) => `s${i}`);
    for (const id of ids) models.set(id, new TranscriptModel());
    set({
      activeId: 's0',
      rt: { s1: { rev: 0, loaded: true, loading: false, status: 'running' }, s2: { rev: 0, loaded: false, loading: true }, s3: { rev: 0, loaded: true, loading: false, status: 'idle', pending: [{ requestId: 'r', toolName: 'Bash', input: {} }] as never } },
    });
    evictModels();
    expect([...models.keys()]).toEqual(['s0', 's1', 's2', 's3', 's8', 's9']);
  });

  it('what a session remembers goes with its model, except unsent work and its terminals', () => {
    for (const id of ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']) models.set(id, new TranscriptModel());
    set({ activeId: 'h', rt: { a: { rev: 3, loaded: true, loading: false } } });
    Object.assign(memo('a'), { draft: 'half a thought', sent: ['earlier'], scroll: { index: 4, offset: 10, bottom: false } });
    memo('a').folds.set('W-1', true);
    memo('b').terms = [{ id: 't1', title: 'pilcrow' }];
    memo('b').preview = 'http://localhost:3000';
    evictModels();
    expect(models.has('a') || models.has('b')).toBe(false);
    expect(useStore.getState().rt.a.loaded).toBe(false);
    expect(memo('a')).toMatchObject({ draft: 'half a thought', sent: [], scroll: null });
    expect(memo('a').folds.size).toBe(0);
    expect(memo('b')).toMatchObject({ terms: [{ id: 't1', title: 'pilcrow' }], preview: '' });
  });
});

describe('applyConfigUi', () => {
  const doc = globalThis as unknown as { document?: unknown };
  beforeEach(() => (doc.document = { documentElement: { style: { setProperty: vi.fn() } } }));
  afterEach(() => delete doc.document);

  it('takes the saved layout field by field, and leaves out what an older version wrote wrong', () => {
    const before = useStore.getState().ui;
    applyConfigUi({ ui: { sidebar: false, panelTab: 'files', panelW: 600, view: 'loud', diffMode: 'split', sidebarW: '300' } } as unknown as Config);
    expect(useStore.getState().ui).toEqual({ ...before, sidebar: false, panelTab: 'files', panelW: 600, diffMode: 'split' });
  });
});

describe('isBusy and isWorking', () => {
  it('busy covers waiting on the person; working does not', () => {
    expect(['running', 'starting', 'requires_action', 'idle', 'error', undefined].map((s) => [isBusy(s), isWorking(s)])).toEqual([
      [true, true],
      [true, true],
      [true, false],
      [false, false],
      [false, false],
      [false, false],
    ]);
  });
});

describe('findInItems', () => {
  const user = (text: string, rev = 0): Item => ({ kind: 'user', id: 'u' + text, uuid: 'uu', rev, text, images: [], files: [] });
  const text = (t: string): Item => ({ kind: 'text', id: 't' + t, rev: 0, text: t, streaming: false });
  const tool: Item = { kind: 'tool', id: 'x', rev: 0, name: 'Bash', input: {}, inputJson: '', inputDone: true, status: 'done', children: [], startTs: 0, parentId: null, result: { text: 'module module', images: [], isError: false } };

  it('finds every match in what the person and Claude said, in order, case aside; not in a step\'s output', () => {
    const items = [user('Fix the Module'), tool, text('module one, MODULE two')];
    expect(findInItems(items, 'module').map((h) => [h.item.id, h.k])).toEqual([
      ['uFix the Module', 0],
      ['tmodule one, MODULE two', 0],
      ['tmodule one, MODULE two', 1],
    ]);
    expect(findInItems(items, 'module', 2)).toHaveLength(2);
    expect(findInItems(items, '')).toEqual([]);
  });

  it('an item that changed is read again', () => {
    const u = user('abc');
    expect(findInItems([u], 'x')).toHaveLength(0);
    Object.assign(u, { text: 'x x', rev: 1 });
    expect(findInItems([u], 'x')).toHaveLength(2);
  });
});
