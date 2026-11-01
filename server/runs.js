// A session run with a cap, as Dispatch, helpers and schedules each start one: the states it moves
// through, its minute cap, what it changed, landing its worktree (commit, pull request, discard), the
// review of several runs side by side, and the inbox's verdict over a set of them. Each caller keeps
// its own record (a Dispatch run, a helper, a schedule's entry); these read and set only the fields
// such records share: `worktree` ({ path, repoRoot, branch, base }), `state`, `stopping` and the
// landing fields (`finished`, `commit`, `pr`, `prError`, `finishError`).
import fs from 'node:fs';
import { groupHunks } from './workspace.js';

export const TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);
export const ACTIVE = new Set(['preparing', 'running', 'needs_action']);
/** After the cap's interrupt, how long a run has to settle before it is ended anyway. */
export const STOP_GRACE_MS = 3000;
export const SUMMARY_MAX = 1200; // characters of an inbox item's summary
export const CLOSE_AFTER_MS = 1500; // a finished run's process lingers this long for its last messages
export const MAX_FILES = 60; // changed files a run's record and an inbox item list
const GIST_MAX = 160; // characters of a run's own summary on its line of an inbox summary
const PR_TIMEOUT_MS = 60_000;
const NOTHING_TO_COMMIT = /nothing to commit|nothing added/i;

/**
 * The minute cap of a run. Past it, `rec.stopping` says why and the session is interrupted: the idle
 * that follows ends the run as stopped (the caller checks `stopping`), and a run that has not settled
 * STOP_GRACE_MS later is ended by `stop(reason)`. `ms` is the time left when less than the whole cap
 * is (a run picked up again after a restart). Returns the timer, to clear when the run ends first.
 * @param {{ stopping?: string | null }} rec
 * @param {number} minutes
 * @param {() => { interrupt?: () => Promise<unknown> } | null | undefined} session
 * @param {(reason: string) => void} stop
 */
export function capRun(rec, minutes, session, stop, ms = minutes * 60e3) {
  return setTimeout(() => {
    const reason = (rec.stopping = `Stopped after ${minutes} minutes`);
    session()?.interrupt?.().catch(() => null);
    setTimeout(() => stop(reason), STOP_GRACE_MS);
  }, ms);
}

/**
 * What a run changed: the files its timeline steps since `since` touched, and those in `diff`
 * (git.diff's files for its worktree), whose line totals win when it has any, since steps that touch
 * one file twice count its lines twice.
 * @param {{ steps?: { at: number, files?: { path: string, add?: number, del?: number }[] }[], since?: number, diff?: { path: string, additions?: number, deletions?: number }[] }} from
 */
export function changesOf({ steps = [], since = 0, diff = [] }) {
  /** @type {Map<string, { path: string, add: number, del: number }>} */
  const files = new Map();
  for (const st of steps) {
    if (st.at < since) continue;
    for (const f of st.files || []) {
      const cur = files.get(f.path) || { path: f.path, add: 0, del: 0 };
      cur.add += f.add || 0;
      cur.del += f.del || 0;
      files.set(f.path, cur);
    }
  }
  let add = 0;
  let del = 0;
  for (const f of diff) {
    add += f.additions || 0;
    del += f.deletions || 0;
    if (!files.has(f.path)) files.set(f.path, { path: f.path, add: f.additions || 0, del: f.deletions || 0 });
  }
  if (!add && !del) {
    for (const f of files.values()) {
      add += f.add;
      del += f.del;
    }
  }
  return { files: [...files.values()], add, del };
}

/**
 * What a run changed in `dir` against `base` (git.diff), counted with its timeline steps since `since`
 * when it has any (changesOf). A folder that is gone or not a repository has changed nothing.
 * @param {typeof import('./git.js')} git
 * @param {string | null | undefined} dir
 * @param {string | undefined} base
 * @param {{ steps?: Parameters<typeof changesOf>[0]['steps'], since?: number }} [from]
 */
export async function changesIn(git, dir, base, from = {}) {
  const d = dir ? await git.diff(dir, { base }).catch(() => null) : null;
  // every file of a parsed diff has a path (parseUnifiedDiff falls back to the old one)
  return changesOf({ ...from, diff: /** @type {{ path: string }[]} */ (d?.files || []) });
}

/**
 * Land a finished run's worktree. 'commit' commits everything on the run's branch, opens a pull request
 * when `pr` says what to call it, and removes the folder, keeping the branch; 'discard' removes both;
 * 'keep' leaves them. Never throws: a failure is in the answer and in `rec.finishError`.
 * @param {Record<string, any>} rec
 * @param {string} action 'commit', 'discard' or 'keep'
 * @param {{ git: typeof import('./git.js'), which?: (cmd: string) => string | null, exec?: Function | null, message: string, pr?: { title: string, body: string } | null }} how
 * @returns {Promise<{ ok: boolean, output: string }>}
 */
export async function land(rec, action, { git, which, exec, message, pr }) {
  const wt = rec.worktree;
  try {
    if (action === 'keep' || !wt?.path || !fs.existsSync(wt.path)) {
      rec.finished = action;
      return { ok: true, output: action === 'keep' ? '' : 'No worktree.' };
    }
    if (action === 'discard') {
      const r = await git.removeWorktree({ repoRoot: wt.repoRoot, dir: wt.path, branch: wt.branch, deleteBranch: true });
      rec.finished = 'discard';
      return { ok: r.ok !== false, output: r.output || '' };
    }
    const c = await git.commit(wt.path, { message });
    if (!c.ok && !NOTHING_TO_COMMIT.test(c.output || '')) throw new Error(c.output || 'commit failed');
    rec.commit = (await git.git(wt.path, ['rev-parse', '--short', 'HEAD'])).stdout.trim() || null;
    if (pr) {
      const url = await openPr(wt, pr, { git, which, exec }).catch((e) => {
        rec.prError = e?.message || 'Could not open a pull request';
        return null;
      });
      if (url) rec.pr = url;
    }
    const r = await git.removeWorktree({ repoRoot: wt.repoRoot, dir: wt.path, branch: wt.branch, deleteBranch: false });
    rec.finished = 'commit';
    return { ok: true, output: r.output || '' };
  } catch (e) {
    rec.finishError = e?.message || String(e);
    return { ok: false, output: rec.finishError };
  }
}

/** Push the branch and open a pull request for it with `gh`; the URL gh prints. */
async function openPr(wt, { title, body }, { git, which, exec }) {
  const gh = which?.('gh');
  if (!gh || !exec) throw new Error('gh is not on the path.');
  const push = await git.git(wt.path, ['push', '-u', 'origin', wt.branch]);
  if (push.code) throw new Error((push.stderr || push.stdout || 'push failed').trim());
  const r = await exec(gh, ['pr', 'create', '--head', wt.branch, '--title', title, '--body', body], { cwd: wt.repoRoot, timeout: PR_TIMEOUT_MS });
  if (r.code) throw new Error((r.stderr || r.stdout || 'gh pr create failed').trim());
  return (r.stdout || '').trim().split('\n').pop() || null;
}

/**
 * Several runs' changes side by side, the same hunk in several of them as one item (groupHunks).
 * @param {typeof import('./git.js')} git
 * @param {{ id: string, name: string, dir?: string | null, base?: string }[]} runs those whose folder is gone are left out
 */
export async function review(git, runs) {
  const diffs = [];
  for (const r of runs) {
    if (!r.dir || !fs.existsSync(r.dir)) continue;
    const d = await git.diff(r.dir, { base: r.base }).catch(() => null);
    if (d) diffs.push({ runId: r.id, name: r.name, files: d.files || [] });
  }
  return groupHunks(diffs);
}

/**
 * The inbox's verdict over a set of runs that have all ended: failed only when none is done and some
 * failed or stopped, and a summary that opens with `${done} of ${n} ${what}` and gives each run a line.
 * @param {{ name: string, state: string, reason?: string | null, files?: number, add?: number, del?: number, gist?: string }[]} runs
 * @param {string} what e.g. 'repositories done'
 */
export function tally(runs, what) {
  const done = runs.filter((r) => r.state === 'done').length;
  const failed = runs.filter((r) => r.state === 'failed' || r.state === 'stopped').length;
  const lines = runs.map((r) => `- ${r.name}: ${r.state}${r.reason ? ` (${r.reason})` : ''}; ${r.files || 0} files (+${r.add || 0} −${r.del || 0})${r.gist ? ` — ${r.gist.replace(/\s+/g, ' ').slice(0, GIST_MAX)}` : ''}`);
  return {
    status: failed && !done ? 'failed' : 'completed',
    reason: failed ? `${failed} of ${runs.length} did not finish` : null,
    summary: `${done} of ${runs.length} ${what}.\n${lines.join('\n')}`.slice(0, SUMMARY_MAX),
  };
}
