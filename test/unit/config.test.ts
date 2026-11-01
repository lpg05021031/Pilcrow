// config.js and the JSON files the server keeps (util.jsonFile): a config.json edited by hand into
// something setConfig would refuse reads as the defaults, the old type scale moves to the new one, and
// a write that a newer one overtook (or the quit's synchronous one) never lands over it.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const DIR = path.join(process.env.XDG_CONFIG_HOME!, 'pilcrow');
fs.mkdirSync(DIR, { recursive: true });
fs.writeFileSync(path.join(DIR, 'config.json'), JSON.stringify({ configVersion: 1, fontSize: 14, recentProjects: 'oops', helpers: null, theme: 'dark', ui: [] }));

const { store, DEFAULT_CONFIG } = await import('../../server/config.js');
const { jsonFile, flushSaves } = await import('../../server/util.js');

describe('config.json as it was left', () => {
  it('reads a value of the wrong kind as the default and keeps the rest', () => {
    const c = store.getConfig();
    expect(c.recentProjects).toEqual([]);
    expect(c.helpers).toEqual(DEFAULT_CONFIG.helpers);
    expect(c.ui).toEqual({});
    expect(c.theme).toBe('dark');
    store.addRecentProject('/home/me/p'); // what session.create does with it
    expect(store.getConfig().recentProjects).toEqual(['/home/me/p']);
  });

  it('moves a size saved as the old default to the new default', () => {
    expect(store.getConfig()).toMatchObject({ fontSize: DEFAULT_CONFIG.fontSize, configVersion: 2 });
  });

  it('merges ui one level down', () => {
    store.setConfig({ ui: { sidebar: true } });
    store.setConfig({ ui: { panel: false } });
    expect(store.getConfig().ui).toEqual({ sidebar: true, panel: false });
  });
});

describe('jsonFile', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-json-')), 'data.json');
  const read = () => JSON.parse(fs.readFileSync(file, 'utf8'));

  it('writes a burst once, after the delay', async () => {
    let data = { n: 0 };
    const f = jsonFile(file, () => data, 30);
    for (let i = 1; i <= 5; i++) {
      data = { n: i };
      f.save();
    }
    expect(fs.existsSync(file)).toBe(false);
    await new Promise((r) => setTimeout(r, 120));
    expect(read()).toEqual({ n: 5 });
  });

  it('on quit writes what is waiting at once, and the write already under way does not land over it', async () => {
    let data = { n: 'async' };
    const f = jsonFile(file, () => data);
    f.save();
    await new Promise((r) => setTimeout(r, 0)); // the async write has started
    data = { n: 'quit' };
    flushSaves();
    expect(read()).toEqual({ n: 'quit' });
    await new Promise((r) => setTimeout(r, 50));
    expect(read()).toEqual({ n: 'quit' });
    expect(fs.readdirSync(path.dirname(file)).filter((x) => x.endsWith('.tmp'))).toEqual([]);
  });

  it('on quit with nothing waiting writes nothing', () => {
    fs.rmSync(file, { force: true });
    jsonFile(file, () => ({ n: 1 }));
    flushSaves();
    expect(fs.existsSync(file)).toBe(false);
  });
});
