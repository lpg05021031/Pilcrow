// Read Claude Code's on-disk session history (~/.claude/projects) through the Agent SDK,
// enriching tool results with the structured data the SDK readers leave out.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { listSessions, getSessionMessages, getSubagentMessages, renameSession, deleteSession, forkSession, listSubagents } from '@anthropic-ai/claude-agent-sdk';
import { claudeConfigDir, log } from './util.js';

let listCache = { at: 0, data: [], pending: null };

export async function listHistory({ force = false } = {}) {
  const fresh = Date.now() - listCache.at < 4000;
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

/** Find the raw JSONL transcript for a session id. */
export function findTranscriptFile(sessionId) {
  const root = path.join(claudeConfigDir(), 'projects');
  let dirs = [];
  try {
    dirs = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const d of dirs) {
    if (!d.isDirectory()) continue;
    const f = path.join(root, d.name, `${sessionId}.jsonl`);
    if (fs.existsSync(f)) return f;
  }
  return null;
}

/** uuid -> toolUseResult from the raw transcript (best effort; format may evolve). */
async function readToolResults(file) {
  const out = new Map();
  if (!file) return out;
  try {
    const rl = readline.createInterface({ input: fs.createReadStream(file, { encoding: 'utf8' }), crlfDelay: Infinity });
    for await (const line of rl) {
      if (!line.includes('"toolUseResult"')) continue;
      try {
        const e = JSON.parse(line);
        if (e.uuid && e.toolUseResult !== undefined) out.set(e.uuid, e.toolUseResult);
      } catch {
        /* skip a malformed line */
      }
    }
  } catch (e) {
    log('readToolResults failed', file, e);
  }
  return out;
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
  const file = findTranscriptFile(sessionId);
  // After a compaction the SDK reader starts at the newest compact boundary and leaves out what came before.
  if (file && msgs.some((m) => m.isCompactSummary)) msgs = await withEarlierMessages(msgs, file, sessionId);
  const results = await readToolResults(file);
  for (const m of msgs) {
    m._history = true;
    if (m.type === 'user' && results.has(m.uuid)) m.tool_use_result = results.get(m.uuid);
  }
  return msgs;
}

/**
 * Claude Code links a compact boundary to the conversation before it through `logicalParentUuid` (its
 * `parentUuid` is null), so the SDK's reader, which follows `parentUuid`, shows only what came after the
 * newest compaction, and returns the boundary without its details. Put the earlier conversation back
 * (across older compactions too) and give each boundary its trigger and token counts, so a reopened
 * session reads the same as it did live.
 */
async function withEarlierMessages(msgs, file, sessionId) {
  const byUuid = new Map();
  try {
    const rl = readline.createInterface({ input: fs.createReadStream(file, { encoding: 'utf8' }), crlfDelay: Infinity });
    for await (const line of rl) {
      if (!line.includes('"uuid"')) continue;
      try {
        const e = JSON.parse(line);
        if (e.uuid) byUuid.set(e.uuid, e);
      } catch {
        /* skip a malformed line */
      }
    }
  } catch (e) {
    log('withEarlierMessages failed', file, e);
    return msgs;
  }
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
  return all.map((m, i) => [m, i]).sort((a, b) => t(a[0]) - t(b[0]) || a[1] - b[1]).map(([m]) => m);
}

export async function loadSubagent(sessionId, agentId, dir) {
  try {
    const msgs = await getSubagentMessages(sessionId, agentId, { dir });
    for (const m of msgs) m._history = true;
    return msgs;
  } catch (e) {
    log('getSubagentMessages failed', e);
    return [];
  }
}

export async function subagentIds(sessionId, dir) {
  try {
    return await listSubagents(sessionId, { dir });
  } catch {
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
    const f = findTranscriptFile(sessionId);
    if (f) fs.rmSync(f, { force: true });
  }
  invalidateList();
}

export async function fork(sessionId, { dir, upToMessageId, title } = {}) {
  const r = await forkSession(sessionId, { dir, upToMessageId, title });
  invalidateList();
  return r;
}
