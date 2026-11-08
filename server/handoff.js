// The handoff report: when Claude hands work back it ends its reply with a short, fixed-shape
// section, and the server keeps it on the session so the workboard can show what was asked, what
// was done, what was not checked and what you have to decide — without opening the conversation.
// What changed is never taken from Claude's words: it is measured from the diff.
import { store } from './config.js';
import * as git from './git.js';
import { log } from './util.js';

/** Appended to the system prompt (after the project memory) unless `config.handoffReport` is false. */
export const HANDOFF_PROMPT = `When you finish a piece of work and hand it back, end your reply with a section headed exactly "## Handoff" containing these four lines, each starting with its label:
Asked: what you were asked to do, in one sentence.
Did: what you did, in one or two sentences.
Unverified: what you did not test or could not check. Write "nothing" if there is nothing.
Decide: the one question the person must answer before this can be considered done, or "none".
Leave the section out of ordinary conversation that produces no work.`;

const LABELS = ['asked', 'did', 'unverified', 'decide'];

/**
 * The last "## Handoff" section of a reply, as { asked, did, unverified, ask }, or null when the
 * reply has none. Labels may be bold and in any case; a value runs until the next label or heading.
 */
export function parseHandoff(text) {
  if (typeof text !== 'string' || !text) return null;
  const heads = [...text.matchAll(/^\s{0,3}#{1,4}\s*handoff\b[^\n]*$/gim)];
  if (!heads.length) return null;
  const start = heads[heads.length - 1];
  const body = text.slice(start.index + start[0].length);
  const out = { asked: '', did: '', unverified: '', ask: '' };
  let current = null;
  let any = false;
  for (const raw of body.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (/^\s{0,3}#{1,6}\s/.test(line)) break; // the next heading ends the section
    const m = /^\s*(?:[-*]\s*)?\**\s*(asked|did|unverified|decide)\s*\**\s*[:：]\s*(.*)$/i.exec(line);
    if (m) {
      current = m[1].toLowerCase();
      const key = current === 'decide' ? 'ask' : current;
      out[key] = m[2].trim();
      any = true;
      continue;
    }
    if (current && line.trim()) {
      const key = current === 'decide' ? 'ask' : current;
      out[key] = (out[key] ? out[key] + ' ' : '') + line.trim();
    }
  }
  if (!any) return null;
  for (const k of Object.keys(out)) out[k] = out[k].replace(/\s+/g, ' ').trim().slice(0, 600);
  if (/^(none|nothing|n\/a|-)\.?$/i.test(out.ask)) out.ask = '';
  if (/^(none|nothing|n\/a|-)\.?$/i.test(out.unverified)) out.unverified = '';
  return out;
}

/** The files the session changed, measured: against the worktree's base, or the working tree against HEAD. */
export async function filesChanged(cwd, worktree) {
  try {
    const d = await git.diff(cwd, worktree?.base ? { base: worktree.base } : {});
    if (!d.isRepo) return [];
    return d.files
      .slice(0, 60)
      .map((f) => ({ path: f.path || f.oldPath, add: f.additions || 0, del: f.deletions || 0 }))
      .filter((f) => f.path);
  } catch (e) {
    log('handoff: diff failed', e?.message);
    return [];
  }
}

/** Keep (or clear) the report of the turn that just ended. Runs after the turn end is recorded; failures only log. */
export async function recordTurn(session, resultText, turnEnd) {
  const parsed = parseHandoff(resultText);
  if (!parsed) {
    if (store.getMeta(session.id)?.report) store.setMeta(session.id, { report: null });
    return null;
  }
  const meta = store.getMeta(session.id) || {};
  const files = await filesChanged(session.cwd, meta.worktree);
  const report = { ...parsed, files, turn: turnEnd };
  store.setMeta(session.id, { report });
  return report;
}

export { LABELS };
