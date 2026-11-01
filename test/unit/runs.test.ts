// runs.js: the minute cap every capped run shares (interrupt, a grace, then the end, with the reason
// on the record so an idle after the interrupt reads as stopped), what a run changed, and the inbox's verdict.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { STOP_GRACE_MS, capRun, changesOf, tally } from '../../server/runs.js';

afterEach(() => vi.useRealTimers());

describe('capRun', () => {
  it('marks the record, interrupts the session, and ends the run after the grace', async () => {
    vi.useFakeTimers();
    const rec: { stopping?: string | null } = {};
    const interrupt = vi.fn(async () => undefined);
    const stop = vi.fn();
    capRun(rec, 2, () => ({ interrupt }), stop);
    vi.advanceTimersByTime(2 * 60e3 - 1);
    expect(interrupt).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(rec.stopping).toBe('Stopped after 2 minutes');
    expect(interrupt).toHaveBeenCalledOnce();
    expect(stop).not.toHaveBeenCalled();
    vi.advanceTimersByTime(STOP_GRACE_MS);
    expect(stop).toHaveBeenCalledWith('Stopped after 2 minutes');
  });

  it('takes the time left for a run picked up after a restart, and a session that is gone', () => {
    vi.useFakeTimers();
    const stop = vi.fn();
    capRun({}, 20, () => null, stop, 1000);
    vi.advanceTimersByTime(1000 + STOP_GRACE_MS);
    expect(stop).toHaveBeenCalledWith('Stopped after 20 minutes');
  });
});

describe('changesOf', () => {
  const steps = [
    { at: 5, files: [{ path: 'old.txt', add: 9, del: 9 }] },
    { at: 10, files: [{ path: 'a.ts', add: 2, del: 1 }] },
    { at: 11, files: [{ path: 'a.ts', add: 1, del: 0 }] },
  ];
  it('sums the steps since the start when there is no diff', () => {
    expect(changesOf({ steps, since: 10 })).toEqual({ files: [{ path: 'a.ts', add: 3, del: 1 }], add: 3, del: 1 });
  });
  it("lets the worktree's diff decide the totals and adds its files", () => {
    const r = changesOf({ steps, since: 10, diff: [{ path: 'a.ts', additions: 2, deletions: 1 }, { path: 'b.ts', additions: 4, deletions: 0 }] });
    expect(r.files.map((f) => f.path)).toEqual(['a.ts', 'b.ts']);
    expect([r.add, r.del]).toEqual([6, 1]);
  });
});

describe('tally', () => {
  it('is completed when one is done, failed when none is, and counts what did not finish', () => {
    const r = tally(
      [
        { name: 'a', state: 'done', files: 2, add: 3, del: 1, gist: 'Bumped\n the version.' },
        { name: 'b', state: 'stopped', reason: 'Stopped after 20 minutes' },
        { name: 'c', state: 'skipped' },
      ],
      'repositories done',
    );
    expect(r.status).toBe('completed');
    expect(r.reason).toBe('1 of 3 did not finish');
    expect(r.summary.split('\n')).toEqual(['1 of 3 repositories done.', '- a: done; 2 files (+3 −1) — Bumped the version.', '- b: stopped (Stopped after 20 minutes); 0 files (+0 −0)', '- c: skipped; 0 files (+0 −0)']);
    expect(tally([{ name: 'a', state: 'failed' }], 'helpers done')).toMatchObject({ status: 'failed', reason: '1 of 1 did not finish' });
  });
});
