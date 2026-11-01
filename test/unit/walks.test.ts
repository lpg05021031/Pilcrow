// The two walks over many folders: skills.list over a plugin cache of 600 skills, and findRepos over a
// workspace of 6 000 folders. Each finds what is there and lets the event loop in while it walks, a turn
// at least every 10 ms of the walk (util.slicer yields every 4 ms). Counted in turns rather than timed:
// the longest stall moves with whatever else the machine runs (alone: 32 and 53 ms before, about 5 now).
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { listSkills, createSkill } from '../../server/customize.js';
import { findRepos } from '../../server/workspace.js';

const TURN_EVERY_MS = 10;
const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-walks-'));

/** What `fn` returned, how long it took, how many turns the event loop had meanwhile, and the longest stall. */
async function walked<T>(fn: () => Promise<T>) {
  const h = monitorEventLoopDelay({ resolution: 1 });
  h.enable();
  await new Promise((r) => setTimeout(r, 10));
  let turns = 0;
  let done = false;
  const spin = () => !done && (turns++, setImmediate(spin));
  setImmediate(spin);
  const t0 = performance.now();
  const r = await fn();
  const ms = performance.now() - t0;
  done = true;
  h.disable();
  console.log(`  ${ms.toFixed(0)} ms, ${turns} turns of the event loop, longest stall ${(h.max / 1e6).toFixed(1)} ms`);
  return { r, ms, turns };
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
    const { r, ms, turns } = await walked(() => listSkills({}));
    expect(r.plugin).toHaveLength(600);
    expect(r.plugin[0]).toMatchObject({ name: 's-0-0', description: 'does a thing', plugin: '1.0.0', files: 5, hasScripts: true, scope: 'plugin' });
    expect(r.user.map((s) => s.name)).toEqual(['my-own-skill']);
    expect(turns).toBeGreaterThanOrEqual(Math.floor(ms / TURN_EVERY_MS));
  });
});

describe('findRepos', () => {
  it('finds the repositories in a workspace of 6 000 folders without holding the loop', async () => {
    const ws = path.join(base, 'ws');
    for (let a = 0; a < 20; a++) for (let b = 0; b < 15; b++) for (let c = 0; c < 20; c++) fs.mkdirSync(path.join(ws, `a${a}`, `b${b}`, `c${c}`), { recursive: true });
    for (let a = 0; a < 20; a++) fs.mkdirSync(path.join(ws, `a${a}`, 'b1', '.git'));
    fs.mkdirSync(path.join(ws, 'a0', 'node_modules', 'dep', '.git'), { recursive: true }); // never looked into
    const { r, ms, turns } = await walked(() => findRepos(ws, 3));
    expect(r).toEqual(Array.from({ length: 20 }, (_, a) => path.join(ws, `a${a}`, 'b1')).sort());
    expect(turns).toBeGreaterThanOrEqual(Math.floor(ms / TURN_EVERY_MS));
  });
});
