// claude.js: which Claude Code a preference picks (the bundled one, the one on PATH, a path you typed),
// and its version, read once per program.
import { afterAll, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { bundledPath, resolveClaude } from '../../server/claude.js';

const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-claude-')));
const fake = (name: string, v: string) => {
  const p = path.join(dir, name);
  fs.writeFileSync(p, `#!/bin/sh\necho "${v} (Claude Code)"\n`, { mode: 0o755 });
  return p;
};
const onPath = fake('claude', '2.0.1');
const PATH = process.env.PATH;
process.env.PATH = `${dir}${path.delimiter}${PATH}`;
afterAll(() => (process.env.PATH = PATH));

describe('resolveClaude', () => {
  it('takes the system one for system, with its version', async () => {
    expect(await resolveClaude('system')).toMatchObject({ path: onPath, source: 'system', version: '2.0.1', system: { path: onPath, version: '2.0.1' } });
  });

  it('takes a path you typed, ~ and all, and none when nothing runs there', async () => {
    const mine = fake('my-claude', '3.1.4');
    expect(await resolveClaude(mine)).toMatchObject({ path: mine, source: 'custom', version: '3.1.4' });
    expect(await resolveClaude(path.join(dir, 'missing'))).toMatchObject({ path: null, source: null, version: null });
  });

  it('prefers the bundled one for auto when the SDK has it, else the system one', async () => {
    const auto = await resolveClaude('auto');
    expect(auto.source).toBe(bundledPath() ? 'bundled' : 'system');
    expect((await resolveClaude('bundled')).path).toBe(bundledPath());
  });
});
