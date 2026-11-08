import { describe, expect, it } from 'vitest';
import { actionsFor, awaitsReview, cardLine, cardState, laneOf, ledgerOf, step } from '../../web/src/lib/board';
import type { Lane } from '../../web/src/lib/board';
import type { SessionSummary } from '../../web/src/lib/types';

const NOW = new Date(2026, 10, 8, 10, 30).getTime();
const base: SessionSummary = { id: 's', title: 'Fix the login redirect', cwd: '/p', project: '/p', archived: false, pinned: false, worktree: null, status: 'idle', live: false, createdAt: NOW - 3600e3 };
const card = (patch: Partial<SessionSummary>): SessionSummary => ({ ...base, intent: 'Carry ?next through the login form.', ...patch });

describe('laneOf', () => {
  it('keeps plain conversations off the board unless they are doing something', () => {
    expect(laneOf(base, undefined, NOW)).toBeNull();
    expect(laneOf({ ...base, turnEnd: NOW - 60e3 }, undefined, NOW)).toBeNull();
    expect(laneOf({ ...base, status: 'running' }, undefined, NOW)).toBe('agent');
    expect(laneOf({ ...base, status: 'requires_action' }, undefined, NOW)).toBe('me');
  });
  it('never shows archived sessions', () => {
    expect(laneOf(card({ archived: true, status: 'running' }), undefined, NOW)).toBeNull();
  });
  it('puts a card with an intent and no turns in the backlog', () => {
    expect(laneOf(card({}), undefined, NOW)).toBe('backlog');
  });
  it('asks for review once a turn ends and nothing has been decided', () => {
    const s = card({ turnEnd: NOW - 60e3 });
    expect(awaitsReview(s)).toBe(true);
    expect(laneOf(s, undefined, NOW)).toBe('me');
    expect(cardState(s, undefined, NOW)).toBe('review');
  });
  it('a prompt beats everything, and is a different state than a review', () => {
    const s = card({ turnEnd: NOW - 60e3, status: 'requires_action', pending: 1 });
    expect(cardState(s, undefined, NOW)).toBe('prompt');
    expect(cardState(card({ status: 'idle' }), { pending: [{ requestId: 'r', toolName: 'Bash', input: {}, suggestions: [] }] }, NOW)).toBe('prompt');
  });
  it('a reviewed turn leaves "Needs you"; where it lands depends on the decision', () => {
    const t = NOW - 60e3;
    expect(laneOf(card({ turnEnd: t, reviewedTurn: t, decisions: [{ at: NOW - 30e3, kind: 'approve', commit: 'abc1234' }] }), undefined, NOW)).toBe('done');
    expect(laneOf(card({ turnEnd: t, reviewedTurn: t, decisions: [{ at: NOW - 30e3, kind: 'park' }] }), undefined, NOW)).toBe('backlog');
    expect(laneOf(card({ turnEnd: t, reviewedTurn: t, decisions: [{ at: NOW - 30e3, kind: 'stop' }] }), undefined, NOW)).toBe('backlog');
  });
  it('an approval from yesterday is no longer on the board as done', () => {
    const t = NOW - 86400e3;
    expect(laneOf(card({ turnEnd: t, reviewedTurn: t, decisions: [{ at: t + 1000, kind: 'approve' }] }), undefined, NOW)).toBe('backlog');
  });
  it('a new turn after an approval asks for review again', () => {
    expect(laneOf(card({ turnEnd: NOW - 10e3, reviewedTurn: NOW - 60e3, decisions: [{ at: NOW - 50e3, kind: 'approve' }] }), undefined, NOW)).toBe('me');
  });
  it('running wins over an unreviewed turn (a send-back is in flight)', () => {
    expect(laneOf(card({ turnEnd: NOW - 60e3, status: 'running' }), undefined, NOW)).toBe('agent');
  });
  it('an error is stuck, in the agent lane; a wait condition is its own lane', () => {
    expect(cardState(card({ status: 'error', turnEnd: NOW - 60e3 }), undefined, NOW)).toBe('stuck');
    expect(laneOf(card({ wait: { kind: 'ci', cwd: '/p', branch: 'pilcrow/x', since: NOW } }), undefined, NOW)).toBe('wait');
  });
});

describe('actionsFor', () => {
  it('makes approve the primary action of a review and start the primary of the backlog', () => {
    expect(actionsFor('review')[0]).toMatchObject({ id: 'approve', key: 'e', primary: true });
    expect(actionsFor('backlog')[0]).toMatchObject({ id: 'start', primary: true });
    expect(actionsFor('running').map((a) => a.id)).toEqual(['open', 'stop']);
  });
  it('names the prompt by the tool waiting', () => {
    expect(actionsFor('prompt', { pending: [{ requestId: 'r', toolName: 'AskUserQuestion', input: {}, suggestions: [] }] })[0].label).toBe('Answer the question');
    expect(actionsFor('prompt', { pending: [{ requestId: 'r', toolName: 'ExitPlanMode', input: {}, suggestions: [] }] })[0].label).toBe('Review the plan');
  });
});

describe('cardLine', () => {
  it('prefers the report question, then the last reply, for a review', () => {
    expect(cardLine(card({ report: { asked: '', did: '', files: [], unverified: '', ask: 'Merge without Wayland?', turn: 1 } }), 'review').text).toBe('Merge without Wayland?');
    expect(cardLine(card({}), 'review', { lastReply: { text: 'All 148 tests pass.', at: 1 } }).text).toBe('All 148 tests pass.');
  });
  it('says what a permission is about', () => {
    expect(cardLine(card({}), 'prompt', { pending: [{ requestId: 'r', toolName: 'Bash', input: { command: 'npm test' }, suggestions: [] }] })).toEqual({ text: 'npm test', tone: 'ask' });
  });
  it('shows the first sentence of the intent in the backlog', () => {
    expect(cardLine(card({ intent: 'Do the thing. Then the other.' }), 'backlog').text).toBe('Do the thing.');
  });
});

describe('ledgerOf', () => {
  it('orders intent, decisions, commits and the last turn by time', () => {
    const s = card({ turnEnd: NOW - 60e3, costUsd: 0.4, decisions: [{ at: NOW - 50e3, kind: 'reject', note: 'No QWebEngine.' }, { at: NOW - 10e3, kind: 'approve', commit: 'abc1234def' }] });
    const l = ledgerOf(s, { activeModel: 'claude-opus-5-5' });
    expect(l.map((e) => e.kind)).toEqual(['intent', 'turn', 'decision', 'decision', 'commit']);
    expect(l[1].note).toBe('opus-5-5 · $0.40');
    expect(l[2].note).toBe('No QWebEngine.');
    expect(l[4].label).toBe('Committed abc1234');
  });
});

describe('step', () => {
  const lanes: Record<Lane, SessionSummary[]> = {
    backlog: [card({ id: 'b1' }), card({ id: 'b2' })],
    agent: [card({ id: 'a1', status: 'running' })],
    wait: [],
    me: [card({ id: 'm1', turnEnd: 1 }), card({ id: 'm2', turnEnd: 1 }), card({ id: 'm3', turnEnd: 1 })],
    done: [],
  };
  it('moves within a lane and clamps at its ends', () => {
    expect(step(lanes, 'm1', 'down')).toBe('m2');
    expect(step(lanes, 'm3', 'down')).toBe('m3');
    expect(step(lanes, 'm1', 'up')).toBe('m1');
  });
  it('crosses to the next non-empty lane at the same index, clamped', () => {
    expect(step(lanes, 'b2', 'right')).toBe('a1');
    expect(step(lanes, 'a1', 'right')).toBe('m1');
    expect(step(lanes, 'm3', 'left')).toBe('a1');
    expect(step(lanes, 'm3', 'right')).toBe('m3');
  });
  it('starts at the first card anywhere when nothing is selected', () => {
    expect(step(lanes, null, 'down')).toBe('b1');
  });
});
