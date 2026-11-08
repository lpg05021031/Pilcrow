import { describe, expect, it } from 'vitest';
import { applyAssistant, applyCompact, contextOf, fromHistory } from '../../server/usage.js';

const usage = () => ({ contextTokens: 0, contextWindow: 200000, costUsd: 0, outputTokens: 0, model: null });
const reply = (u: Record<string, number>, extra: Record<string, unknown> = {}) => ({ type: 'assistant', uuid: 'a', message: { role: 'assistant', model: 'claude-x', content: [], usage: u }, parent_tool_use_id: null, ...extra });

describe('contextOf', () => {
  it('counts what the model read: input, cache writes, cache reads; not the output', () => {
    expect(contextOf({ usage: { input_tokens: 1200, cache_creation_input_tokens: 300, cache_read_input_tokens: 20000, output_tokens: 800 } })).toBe(21500);
    expect(contextOf({ usage: { input_tokens: 5 } })).toBe(5);
    expect(contextOf({})).toBe(0);
    expect(contextOf(undefined)).toBe(0);
  });
});

describe('applyAssistant', () => {
  it('sets the figure and the model, and clears the after-compact mark', () => {
    const u = { ...usage(), afterCompact: true };
    applyAssistant(u, reply({ input_tokens: 100, cache_read_input_tokens: 9900, output_tokens: 50 }).message);
    expect(u.contextTokens).toBe(10000);
    expect(u.model).toBe('claude-x');
    expect(u.afterCompact).toBe(false);
  });
  it('leaves the figure alone for a message without usage', () => {
    const u = { ...usage(), contextTokens: 777 };
    applyAssistant(u, { role: 'assistant', content: [] });
    expect(u.contextTokens).toBe(777);
  });
});

describe('applyCompact', () => {
  it('marks the figure as unknown until the next reply, remembering what it was', () => {
    const u = { ...usage(), contextTokens: 160000 };
    applyCompact(u, { trigger: 'auto', pre_tokens: 161000 });
    expect(u).toMatchObject({ contextTokens: 0, afterCompact: true, compactedFrom: 161000 });
  });
  it('takes a post-compaction figure when Claude Code gives one', () => {
    const u = { ...usage(), contextTokens: 160000 };
    applyCompact(u, { trigger: 'manual', pre_tokens: 160000, post_tokens: 14000 });
    expect(u.contextTokens).toBe(14000);
    expect(u.afterCompact).toBe(true);
  });
});

describe('fromHistory', () => {
  it("reads the newest reply of the session's own, skipping sub-agent messages", () => {
    const msgs = [
      reply({ input_tokens: 100, cache_read_input_tokens: 40000 }),
      reply({ input_tokens: 90000, cache_read_input_tokens: 0 }, { parent_tool_use_id: 'toolu_1' }), // a sub-agent's own context
      { type: 'user', uuid: 'u', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'toolu_1', content: 'ok' }] } },
    ];
    const u = fromHistory(usage(), msgs);
    expect(u.contextTokens).toBe(40100);
    expect(u.afterCompact).toBeFalsy();
  });
  it('a compaction after the last reply leaves the figure unknown, not the old number', () => {
    const msgs = [reply({ input_tokens: 100, cache_read_input_tokens: 150000 }), { type: 'system', subtype: 'compact_boundary', uuid: 'c', compact_metadata: { trigger: 'manual', pre_tokens: 150100 } }];
    const u = fromHistory(usage(), msgs);
    expect(u).toMatchObject({ contextTokens: 0, afterCompact: true, compactedFrom: 150100 });
  });
  it('a reply after the compaction is the figure again', () => {
    const msgs = [
      reply({ input_tokens: 100, cache_read_input_tokens: 150000 }),
      { type: 'system', subtype: 'compact_boundary', uuid: 'c', compact_metadata: { trigger: 'auto', pre_tokens: 150100 } },
      { type: 'user', uuid: 's', message: { role: 'user', content: [{ type: 'text', text: 'summary' }] }, isCompactSummary: true },
      reply({ input_tokens: 12000, cache_creation_input_tokens: 3000 }),
    ];
    const u = fromHistory(usage(), msgs);
    expect(u.contextTokens).toBe(15000);
    expect(u.afterCompact).toBe(false);
  });
  it('an empty transcript leaves the figures as they were', () => {
    expect(fromHistory(usage(), []).contextTokens).toBe(0);
  });
});
