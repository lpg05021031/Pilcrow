// Dispatch wired to the real server: sessions through the Agent SDK, git worktrees, the config store,
// snapshots for the files a run touched, the scheduler's inbox for the report. The state machine lives
// in dispatch_core.js so the tests can drive it with fakes.
import path from 'node:path';
import { store } from './config.js';
import { sessions, bus, textOfContent } from './sessions.js';
import * as git from './git.js';
import * as snapshots from './snapshots.js';
import { pushInbox, updateInbox } from './schedules.js';
import { xdg, readJson, writeJson, uuid, log, slash, slugify, run, which, IS_WIN } from './util.js';
import { createDispatcher } from './dispatch_core.js';
import { repos } from './repos.js';

const FILE = path.join(xdg('state'), 'dispatch.json');

/** A command in a worktree, through the user's shell; never rejects. */
export async function shell(cmd, cwd, timeout = 600_000) {
  const r = IS_WIN ? await run(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', cmd], { cwd, timeout }) : await run('/bin/sh', ['-c', cmd], { cwd, timeout });
  return { code: r.code, output: `${r.stdout || ''}${r.stderr ? (r.stdout ? '\n' : '') + r.stderr : ''}` };
}

export const dispatch = createDispatcher({
  file: FILE,
  sessions,
  bus,
  store,
  git,
  snapshots,
  pushInbox,
  updateInbox,
  shell,
  run,
  which,
  log,
  uuid,
  slugify,
  slash,
  textOfContent,
  registry: repos,
  readJson,
  writeJson,
});

export const events = dispatch.events;
export const start = () => dispatch.begin();
export const stop = () => dispatch.end();
