// Locate the Claude Code executable: the one bundled with the Agent SDK, or the system `claude`.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { which, run, isExecutable, expandHome, IS_WIN, slash } from './util.js';

const require = createRequire(import.meta.url);
const cache = new Map();

function isMusl() {
  try {
    const header = process.report?.getReport?.().header;
    return header ? !header.glibcVersionRuntime : false;
  } catch {
    return false;
  }
}

export function bundledPath() {
  const plat = process.platform;
  const arch = process.arch;
  const names = [`@anthropic-ai/claude-agent-sdk-${plat}-${arch}${plat === 'linux' && isMusl() ? '-musl' : ''}`, `@anthropic-ai/claude-agent-sdk-${plat}-${arch}`];
  for (const name of names) {
    try {
      const dir = path.dirname(require.resolve(`${name}/package.json`));
      const bin = path.join(dir, plat === 'win32' ? 'claude.exe' : 'claude');
      if (isExecutable(bin)) return slash(bin);
    } catch {
      /* not installed */
    }
  }
  return null;
}

export function systemPath() {
  if (IS_WIN) {
    // Only a real claude.exe: npm's claude.cmd shim cannot be started without a shell.
    const found = which('claude.exe');
    if (found) return slash(found);
    const candidates = ['~/.local/bin/claude.exe', process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs', 'claude', 'claude.exe'), process.env.APPDATA && path.join(process.env.APPDATA, 'npm', 'node_modules', '@anthropic-ai', 'claude-code', 'bin', 'claude.exe')].filter(Boolean);
    for (const c of candidates) {
      const p = expandHome(c);
      if (isExecutable(p)) return slash(p);
    }
    return null;
  }
  const found = which('claude');
  if (found) return fs.realpathSync(found);
  // Common install locations when PATH is minimal (e.g. launched from a desktop entry).
  const candidates = ['~/.local/bin/claude', '~/.claude/local/claude', '~/.npm-global/bin/claude', '/usr/local/bin/claude', '/usr/bin/claude'];
  for (const c of candidates) {
    const p = expandHome(c);
    if (isExecutable(p)) return fs.realpathSync(p);
  }
  return null;
}

export async function version(bin) {
  if (!bin) return null;
  if (cache.has(bin)) return cache.get(bin);
  const r = await run(bin, ['--version'], { timeout: 20_000 });
  const v = (r.stdout.match(/\d+\.\d+\.\d+/) || [null])[0];
  cache.set(bin, v);
  return v;
}

/** Resolve the executable for a preference: 'auto' | 'bundled' | 'system' | absolute path. */
export async function resolveClaude(pref = 'auto') {
  const bundled = bundledPath();
  const system = systemPath();
  let chosen = null;
  let source = null;
  if (pref === 'bundled') [chosen, source] = [bundled, 'bundled'];
  else if (pref === 'system') [chosen, source] = [system, 'system'];
  else if (pref && pref !== 'auto') [chosen, source] = [expandHome(pref), 'custom'];
  else if (bundled) [chosen, source] = [bundled, 'bundled'];
  else [chosen, source] = [system, 'system'];
  if (chosen && !isExecutable(chosen)) chosen = null;
  return {
    path: chosen,
    source: chosen ? source : null,
    version: await version(chosen),
    bundled: bundled ? { path: bundled, version: await version(bundled) } : null,
    system: system ? { path: system, version: await version(system) } : null,
  };
}
