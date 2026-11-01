// The two walks over many folders: skills.list over a plugin cache of 600 skills, and findRepos over a
// workspace of 6 000 folders. Each finds what is there and never holds the event loop for long (a slice
// at a time, util.slicer). In this test, before: 32 ms and 53 ms in one piece; now 6-11 ms and 5-8 ms.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { listSkills, createSkill } from '../../server/customize.js';
import { findRepos } from '../../server/workspace.js';

const STALL_MS = 16; // a slice is 4 ms; the rest is the collector and the test runner
const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-walks-'));

/** The longest the event loop waited while `fn` ran, and what it returned. */
async function stallOf<T>(fn: () => Promise<T>) {
  const h = monitorEventLoopDelay({ resolution: 1 });
  h.enable();
  await new Promise((r) => setTimeout(r, 10));
  const r = await fn();
  await new Promise((r) => setTimeout(r, 10));
  h.disable();
  return { r, ms: h.max / 1e6 };
}

describe('skills.list', () => {
  it('finds every plugin skill once, by plugin, without holding the loop', async () => {
    for (let p = 0; p < 30; p++)
      for (let k = 0; k < 20; k++) {
        const d = path.join(process.env.CLAUDE_CONFIG_DIR!, 'plugins', 'cache', 'market', `plugin-${p}`, '1.0.0', 'skills', `skill-${k}`);
        fs.mkdirSync(path.join(d, 'scripts'), { recursive: true });
        fs.writeFileSync(path.join(d, 'SKILL.md'), `---\nname: s-${p}-${k}\ndescription: "does a thing"\n---\n\nBody.\n`);
        for (let f = 0; f < 4; f++) fs.writeFileSync(path.join(d, 'scripts', `s${f}.sh`), 'echo\n');
      }
    await createSkill({ scope: 'user', name: 'My Own Skill' });
    const { r, ms } = await stallOf(() => listSkills({}));
    console.log(`skills.list over 600 plugin skills: longest stall ${ms.toFixed(1)} ms`);
    expect(r.plugin).toHaveLength(600);
    expect(r.plugin[0]).toMatchObject({ name: 's-0-0', description: 'does a thing', plugin: '1.0.0', files: 5, hasScripts: true, scope: 'plugin' });
    expect(r.user.map((s) => s.name)).toEqual(['my-own-skill']);
    expect(ms).toBeLessThan(STALL_MS);
  });
});

describe('findRepos', () => {
  it('finds the repositories in a workspace of 6 000 folders without holding the loop', async () => {
    const ws = path.join(base, 'ws');
    for (let a = 0; a < 20; a++) for (let b = 0; b < 15; b++) for (let c = 0; c < 20; c++) fs.mkdirSync(path.join(ws, `a${a}`, `b${b}`, `c${c}`), { recursive: true });
    for (let a = 0; a < 20; a++) fs.mkdirSync(path.join(ws, `a${a}`, 'b1', '.git'));
    fs.mkdirSync(path.join(ws, 'a0', 'node_modules', 'dep', '.git'), { recursive: true }); // never looked into
    const { r, ms } = await stallOf(() => findRepos(ws, 3));
    console.log(`findRepos over 6 000 folders: longest stall ${ms.toFixed(1)} ms`);
    expect(r).toEqual(Array.from({ length: 20 }, (_, a) => path.join(ws, `a${a}`, 'b1')).sort());
    expect(ms).toBeLessThan(STALL_MS);
  });
});
