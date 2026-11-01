// Read Claude Code's on-disk session history (~/.claude/projects) through the Agent SDK,
// enriching tool results with the structured data the SDK readers leave out.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { listSessions, getSessionMessages, getSubagentMessages, renameSession, deleteSession, forkSession } from '@anthropic-ai/claude-agent-sdk';
import { claudeConfigDir, log } from './util.js';

const LIST_TTL_MS = 4000; // the list is asked for on every change; the SDK reads every transcript's head
/** @type {{ at: number, data: import('@anthropic-ai/claude-agent-sdk').SDKSessionInfo[], pending: Promise<import('@anthropic-ai/claude-agent-sdk').SDKSessionInfo[]> | null }} */
let listCache = { at: 0, data: [], pending: null };

export async function listHistory({ force = false } = {}) {
  const fresh = Date.now() - listCache.at < LIST_TTL_MS;
  if (!force && fresh) return listCache.data;
  if (listCache.pending) return listCache.pending;
  listCache.pending = (async () => {
    try {
      const data = await listSessions({ limit: 400 });
      listCache = { at: Date.now(), data, pending: null };
    } catch (e) {
      log('listSessions failed', e);
      listCache.pending = null;
    }
    return listCache.data;
  })();
  return listCache.pending;
}

export function invalidateList() {
  listCache.at = 0;
}

/** Find the raw JSONL transcript for a session id (one folder per project under ~/.claude/projects). */
export async function findTranscriptFile(sessionId) {
  const root = path.join(claudeConfigDir(), 'projects');
  const dirs = await fs.promises.readdir(root, { withFileTypes: true }).catch(() => []);
  for (const d of dirs) {
    if (!d.isDirectory()) continue;
    const f = path.join(root, d.name, `${sessionId}.jsonl`);
    if (await fs.promises.access(f).then(() => true, () => false)) return f;
  }
  return null;
}

/**
 * One read of the raw transcript for what the SDK's reader leaves out: each record's toolUseResult by
 * uuid, and, with `all` (after a compaction), every record by uuid for withEarlierMessages. A line is
 * parsed only when it can hold what is asked for. Null when the file cannot be read.
 * @param {string} file @param {{ all?: boolean }} [opts]
 */
export async function readTranscript(file, { all = false } = {}) {
  const results = new Map();
  const byUuid = new Map();
  try {
    const rl = readline.createInterface({ input: fs.createReadStream(file, { encoding: 'utf8' }), crlfDelay: Infinity });
    for await (const line of rl) {
      if (!line.includes(all ? '"uuid"' : '"toolUseResult"')) continue;
      try {
        const e = JSON.parse(line);
        if (!e.uuid) continue;
        if (all) byUuid.set(e.uuid, e);
        if (e.toolUseResult !== undefined) results.set(e.uuid, e.toolUseResult);
      } catch {
        /* skip a malformed line */
      }
    }
  } catch (e) {
    log('reading the transcript failed', file, e);
    return null;
  }
  return { results, byUuid };
}

export async function loadTranscript(sessionId, dir) {
  let msgs = [];
  try {
    msgs = await getSessionMessages(sessionId, { dir, includeSystemMessages: true });
  } catch (e) {
    log('getSessionMessages failed', sessionId, e);
    try {
      msgs = await getSessionMessages(sessionId, { includeSystemMessages: true });
    } catch {
      msgs = [];
    }
  }
  const file = await findTranscriptFile(sessionId);
  // After a compaction the SDK reader starts at the newest compact boundary and leaves out what came before.
  const compacted = msgs.some((m) => m.isCompactSummary);
  const raw = file ? await readTranscript(file, { all: compacted }) : null;
  if (raw && compacted) msgs = withEarlierMessages(msgs, raw.byUuid, sessionId);
  for (const m of msgs) {
    m._history = true;
    if (m.type === 'user' && raw?.results.has(m.uuid)) m.tool_use_result = raw.results.get(m.uuid);
  }
  return msgs;
}

/**
 * Claude Code links a compact boundary to the conversation before it through `logicalParentUuid` (its
 * `parentUuid` is null), so the SDK's reader, which follows `parentUuid`, shows only what came after the
 * newest compaction, and returns the boundary without its details. Put the earlier conversation back
 * (across older compactions too) and give each boundary its trigger and token counts, so a reopened
 * session reads the same as it did live. `byUuid` is every raw record (readTranscript with `all`).
 * @param {Record<string, any>[]} msgs @param {Map<string, Record<string, any>>} byUuid @param {string} sessionId
 */
export function withEarlierMessages(msgs, byUuid, sessionId) {
  const boundaryOf = (e) => ({
    type: 'system',
    subtype: 'compact_boundary',
    uuid: e.uuid,
    session_id: e.sessionId || sessionId,
    timestamp: e.timestamp,
    compact_metadata: { trigger: e.compactMetadata?.trigger || 'auto', pre_tokens: e.compactMetadata?.preTokens, post_tokens: e.compactMetadata?.postTokens },
  });
  const messageOf = (e) => ({
    type: e.type,
    uuid: e.uuid,
    session_id: e.sessionId || sessionId,
    message: e.message,
    parent_tool_use_id: null,
    timestamp: e.timestamp,
    ...(e.isMeta ? { isMeta: true } : {}),
    ...(e.isCompactSummary ? { isCompactSummary: true } : {}),
  });

  const upgraded = msgs.map((m) => {
    const e = m.type === 'system' ? byUuid.get(m.uuid) : null;
    return e?.subtype === 'compact_boundary' ? boundaryOf(e) : m;
  });
  const newest = upgraded.find((m) => m.subtype === 'compact_boundary');
  if (!newest) return upgraded;

  const earlier = [];
  const seen = new Set();
  let cursor = byUuid.get(newest.uuid)?.logicalParentUuid;
  while (cursor && !seen.has(cursor)) {
    seen.add(cursor);
    const e = byUuid.get(cursor);
    if (!e) break;
    if (e.type === 'system' && e.subtype === 'compact_boundary') {
      earlier.push(boundaryOf(e));
      cursor = e.parentUuid || e.logicalParentUuid;
      continue;
    }
    if ((e.type === 'user' || e.type === 'assistant') && !e.isSidechain && e.message) earlier.push(messageOf(e));
    cursor = e.parentUuid;
  }
  earlier.reverse();
  const have = new Set(earlier.map((m) => m.uuid));
  const all = [...earlier, ...upgraded.filter((m) => !have.has(m.uuid))];
  // Chain order puts a "/compact" command after the boundary it caused; time order reads the way it happened.
  const t = (m) => Date.parse(m.timestamp) || 0;
  return all.map((m, i) => ({ m, i })).sort((a, b) => t(a.m) - t(b.m) || a.i - b.i).map((x) => x.m);
}

export async function loadSubagent(sessionId, agentId, dir) {
  try {
    /** @type {Record<string, any>[]} transcript records, marked as read from disk */
    const msgs = await getSubagentMessages(sessionId, agentId, { dir });
    for (const m of msgs) m._history = true;
    return msgs;
  } catch (e) {
    log('getSubagentMessages failed', e);
    return [];
  }
}

export async function rename(sessionId, title, dir) {
  try {
    await renameSession(sessionId, title, { dir });
  } catch (e) {
    // A session that has not been written to disk yet cannot be renamed there; metadata still holds the title.
    log('renameSession failed', e?.message);
  }
  invalidateList();
}

export async function remove(sessionId, dir) {
  try {
    await deleteSession(sessionId, { dir });
  } catch (e) {
    log('deleteSession failed', e?.message);
    const f = await findTranscriptFile(sessionId);
    if (f) await fs.promises.rm(f, { force: true });
  }
  invalidateList();
}

/** @param {string} sessionId @param {{ dir?: string, upToMessageId?: string, title?: string }} [opts] */
export async function fork(sessionId, { dir, upToMessageId, title } = {}) {
  const r = await forkSession(sessionId, { dir, upToMessageId, title });
  invalidateList();
  return r;
}
