// cli.js, the parts that need no network and no browser: the options it reads, the running instance it
// finds from the lock (or does not, when the lock is stale), retiring an outdated one, and telling a
// snap browser from a packaged one.
import { afterAll, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { existingInstance, parseArgs, retireOutdated, snapOf } from '../../server/cli.js';
import { LOCK } from '../../server/main.js';
import { VERSION } from '../../server/util.js';
import { serve } from './serve.js';

const { app } = await serve(); // it writes LOCK, as a running Pilcrow does
afterAll(() => app.stop());
const writeLock = (lock: object) => fs.writeFileSync(LOCK, JSON.stringify(lock));
const alive = (pid: number) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

describe('the command line', () => {
  it('reads --name=value and --flag, and nothing else', () => {
    expect(parseArgs(['--shell=none', '--port=4000', '--doctor', 'stray', '-x', '--empty='])).toEqual({ shell: 'none', port: '4000', doctor: true, empty: '' });
  });
});

describe('the running instance', () => {
  it('is found from the lock when it answers as itself', async () => {
    expect(await existingInstance()).toMatchObject({ pid: process.pid, port: app.port, version: VERSION, busy: 0, url: `http://127.0.0.1:${app.port}/` });
  });

  it('is not found from a lock whose process is gone, or that nothing answers for', async () => {
    const child = spawn(process.execPath, ['-e', '']);
    await new Promise((r) => child.on('exit', r));
    writeLock({ pid: child.pid, port: app.port });
    expect(await existingInstance()).toBe(null);
    writeLock({ pid: process.pid, port: 1 }); // alive, but no Pilcrow on that port
    expect(await existingInstance()).toBe(null);
    fs.writeFileSync(LOCK, 'not json');
    expect(await existingInstance()).toBe(null);
  });

  it('is retired when it is outdated and idle, and left alone when it is current or busy', async () => {
    expect(await retireOutdated({ pid: process.pid, version: VERSION, busy: 0 })).toBe(false);
    const old = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
    expect(await retireOutdated({ pid: old.pid, version: '0.0.1', busy: 2 })).toBe(false);
    expect(alive(old.pid!)).toBe(true);
    expect(await retireOutdated({ pid: old.pid, version: '0.0.1', busy: 0 })).toBe(true);
    expect(alive(old.pid!)).toBe(false);
  });
});

describe('snapOf', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-snap-'));
  it('names the snap of a launcher script, and none for a plain binary', () => {
    const script = path.join(dir, 'chromium-browser');
    fs.writeFileSync(script, '#!/bin/sh\nexec /snap/bin/chromium "$@"\n');
    expect(snapOf(script)).toBe('chromium');
    expect(snapOf('/snap/bin/firefox')).toBe('firefox');
    const binary = path.join(dir, 'chrome');
    fs.writeFileSync(binary, Buffer.alloc(20_000));
    expect(snapOf(binary)).toBe(null);
    expect(snapOf(path.join(dir, 'missing'))).toBe(null);
    expect(snapOf(null)).toBe(null);
  });
});
