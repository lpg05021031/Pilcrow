// schedules.js: the cadences a schedule can have and the five-field cron it reads (nextRun); a schedule
// saved, checked, switched and removed, its file written on quit; and a run that cannot start landing
// in the inbox as failed.
import { afterAll, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as schedules from '../../server/schedules.js';
import * as inbox from '../../server/inbox.js';
import { sessions } from '../../server/sessions.js';
import { store } from '../../server/config.js';
import { flushSaves } from '../../server/util.js';

const { nextRun, describeCadence } = schedules;

/** Local time, as the scheduler works in it: 2026-03-04 is a Wednesday. */
const local = (d: number, h: number, m: number) => new Date(2026, 2, d, h, m).getTime();
const when = (t: number | null) => {
  const d = new Date(t!);
  return [d.getDate(), d.getHours(), d.getMinutes()];
};

describe('nextRun', () => {
  const wed0930 = local(4, 9, 30);

  it('daily: later today, else tomorrow', () => {
    expect(when(nextRun({ kind: 'daily', time: '10:00' }, wed0930))).toEqual([4, 10, 0]);
    expect(when(nextRun({ kind: 'daily', time: '09:30' }, wed0930))).toEqual([5, 9, 30]);
  });

  it('weekdays skip the weekend; weekly lands on its day', () => {
    const fri18 = local(6, 18, 0);
    expect(when(nextRun({ kind: 'weekdays', time: '09:00' }, fri18))).toEqual([9, 9, 0]);
    expect(when(nextRun({ kind: 'weekly', weekday: 1, time: '08:15' }, wed0930))).toEqual([9, 8, 15]);
    expect(when(nextRun({ kind: 'weekly', weekday: 3, time: '09:00' }, wed0930))).toEqual([11, 9, 0]);
  });

  it('intervals stay on the chosen hour of day', () => {
    expect(when(nextRun({ kind: 'hourly', time: '00:05' }, wed0930))).toEqual([4, 10, 5]);
    expect(when(nextRun({ kind: 'interval', everyHours: 6, time: '02:00' }, wed0930))).toEqual([4, 14, 0]);
  });

  it('cron: steps, ranges, lists, and 7 as Sunday', () => {
    expect(when(nextRun({ kind: 'cron', cron: '*/15 * * * *' }, wed0930))).toEqual([4, 9, 45]);
    expect(when(nextRun({ kind: 'cron', cron: '0 9-17/4 * * 1-5' }, wed0930))).toEqual([4, 13, 0]);
    expect(when(nextRun({ kind: 'cron', cron: '30 8 1,15 * *' }, wed0930))).toEqual([15, 8, 30]);
    expect(when(nextRun({ kind: 'cron', cron: '0 0 * * 7' }, wed0930))).toEqual([8, 0, 0]);
  });

  it('cron: a date that never comes is null; a malformed expression says what is wrong', () => {
    expect(nextRun({ kind: 'cron', cron: '0 0 31 2 *' }, wed0930)).toBe(null);
    expect(() => nextRun({ kind: 'cron', cron: '* * *' })).toThrow('five fields');
    expect(() => nextRun({ kind: 'cron', cron: '* * * * mon' })).toThrow('Cannot read "mon"');
  });

  it('describes a cadence in words', () => {
    expect(describeCadence({ kind: 'weekdays', time: '09:00' })).toBe('Weekdays at 09:00');
    expect(describeCadence({ kind: 'weekly', weekday: 0, time: '07:30' })).toBe('Sundays at 07:30');
    expect(describeCadence({ kind: 'interval', everyHours: 3 })).toBe('Every 3 hours');
  });
});

describe('a schedule', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-schedule-'));
  const FILE = path.join(process.env.XDG_CONFIG_HOME!, 'pilcrow', 'schedules.json');
  afterAll(() => sessions.stop());

  it('is saved with its next run, refused without a folder, a prompt or a readable cron, and written on quit', () => {
    const s = schedules.save({ name: ' Nightly ', cwd, prompt: 'Tidy up', cadence: { kind: 'daily', time: '03:00' } });
    expect(s).toMatchObject({ name: 'Nightly', kind: 'prompt', enabled: true, maxMinutes: 45, cadenceLabel: 'Every day at 03:00', running: null });
    expect(s.nextRunAt).toBeGreaterThan(Date.now());
    expect(() => schedules.save({ name: 'x', prompt: 'p' })).toThrow('Pick a folder');
    expect(() => schedules.save({ name: 'x', cwd, prompt: ' ' })).toThrow('Write what Claude should do');
    expect(() => schedules.save({ name: 'x', cwd, prompt: 'p', cadence: { kind: 'cron', cron: 'nope' } })).toThrow('five fields');
    expect(schedules.toggle(s.id, false)).toMatchObject({ enabled: false, nextRunAt: null });
    expect(() => schedules.toggle('gone', true)).toThrow('No such schedule.');
    flushSaves();
    expect(JSON.parse(fs.readFileSync(FILE, 'utf8')).schedules.map((x: { id: string }) => x.id)).toContain(s.id);
    schedules.remove(s.id);
    expect(schedules.list().some((x) => x.id === s.id)).toBe(false);
  });

  it('that cannot start Claude Code lands in the inbox as failed, and says why', async () => {
    store.setConfig({ claudeBinary: path.join(cwd, 'no-claude-here') });
    const s = schedules.save({ name: 'Broken', cwd, prompt: 'Do it', cadence: { kind: 'hourly' } });
    await expect(schedules.runNow(s.id)).rejects.toThrow(/No Claude Code program at/);
    expect(inbox.list().items[0]).toMatchObject({ scheduleId: s.id, scheduleName: 'Broken', status: 'failed', reason: expect.stringMatching(/No Claude Code program/) });
    expect(schedules.list().find((x) => x.id === s.id)).toMatchObject({ lastStatus: 'failed', runs: 1, running: null });
  });
});
