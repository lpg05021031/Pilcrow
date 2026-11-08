// The session's context figures, from the messages Claude Code sends.
//
// What a reply was made against is what the model read for it: the uncached input, the cache writes
// and the cache reads of that call. Claude Code's own `/context` and status line count those three
// and leave the reply's output out (the output joins the next call's input, where it is counted).
// Only the session's own replies count: a sub-agent's messages carry parent_tool_use_id and speak of
// the sub-agent's context, not this one's.
//
// After a compaction the context is a summary and the system prompt; its size is known only when the
// next reply comes back. Until then the figure is marked `afterCompact` and shown as such, instead of
// the pre-compaction number, which would be wrong by most of the window.

/** The context one assistant message was made against, or 0 when the message carries no usage. */
export function contextOf(message) {
  const u = message?.usage;
  if (!u) return 0;
  return (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0);
}

/** Take a reply of this session's own (not a sub-agent's) into the usage. */
export function applyAssistant(usage, message) {
  const ctx = contextOf(message);
  if (ctx > 0) {
    usage.contextTokens = ctx;
    usage.afterCompact = false;
  }
  if (message?.model) usage.model = message.model;
  return usage;
}

/** A compaction happened: the old figure is gone; the new one arrives with the next reply. */
export function applyCompact(usage, meta) {
  usage.compactedFrom = Number(meta?.pre_tokens) || usage.contextTokens || 0;
  usage.contextTokens = Number(meta?.post_tokens) || 0;
  usage.afterCompact = true;
  return usage;
}

/**
 * The figures as the transcript leaves them: the newest reply of the session's own, unless a
 * compaction came after it. `msgs` are the SDK-shaped messages history.loadTranscript returns.
 */
export function fromHistory(usage, msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.type === 'system' && m.subtype === 'compact_boundary') return applyCompact(usage, m.compact_metadata);
    if (m.type === 'assistant' && !m.parent_tool_use_id && m.message?.usage) return applyAssistant(usage, m.message);
  }
  return usage;
}
