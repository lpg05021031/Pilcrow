import { describe, expect, it } from 'vitest';
import { agentState, parseWorkflowMeta, workflowColumns, workflowCounts } from '../../web/src/lib/workflow';
import type { TaskInfo, WorkflowAgent } from '../../web/src/lib/types';

describe('parseWorkflowMeta', () => {
  it('reads name, description and phases from the literal', () => {
    const script = `export const meta = {
      name: 'review-changes',
      description: "Review changed files, verify each finding",
      phases: [{ title: 'Review' }, { title: "Verify", kind: 'check' }, 'Report'],
    }
    const x = 1;`;
    expect(parseWorkflowMeta(script)).toEqual({
      name: 'review-changes',
      description: 'Review changed files, verify each finding',
      phases: [{ title: 'Review', kind: undefined }, { title: 'Verify', kind: 'check' }, { title: 'Report' }],
    });
  });
  it('is empty without a meta block', () => {
    expect(parseWorkflowMeta('const a = 1')).toEqual({ phases: [] });
    expect(parseWorkflowMeta(undefined)).toEqual({ phases: [] });
  });
});

const agent = (p: Partial<WorkflowAgent>): WorkflowAgent => ({ type: 'workflow_agent', index: 0, state: 'start', ...p });

describe('agentState / workflowCounts', () => {
  it('maps SDK states to card states', () => {
    expect(agentState(agent({ state: 'done' }))).toBe('done');
    expect(agentState(agent({ state: 'done', cached: true }))).toBe('cached');
    expect(agentState(agent({ state: 'error' }))).toBe('error');
    expect(agentState(agent({ state: 'error', blocked: true }))).toBe('blocked');
    expect(agentState(agent({ state: 'start', queuedAt: 1 }))).toBe('queued');
    expect(agentState(agent({ state: 'start', queuedAt: 1, startedAt: 2 }))).toBe('running');
    expect(agentState(agent({ state: 'progress' }))).toBe('running');
  });
  it('counts', () => {
    const c = workflowCounts([agent({ state: 'done' }), undefined, agent({ state: 'start', queuedAt: 1 }), agent({ state: 'error' }), agent({ state: 'progress' })]);
    expect(c).toEqual({ total: 4, queued: 1, running: 1, done: 1, error: 1 });
  });
});

describe('workflowColumns', () => {
  it('uses announced phases, fills in declared ones, and invents one for stray agents', () => {
    const task: TaskInfo = {
      id: 't',
      status: 'running',
      workflow: {
        phases: [{ index: 0, title: 'Review (live)' }],
        agents: [agent({ index: 1, phaseIndex: 0 }), agent({ index: 0, phaseIndex: 0 }), agent({ index: 2, phaseIndex: 2, phaseTitle: 'Extra' })],
        logs: [],
      },
    };
    const cols = workflowColumns(task, { phases: [{ title: 'Review' }, { title: 'Verify' }] });
    expect(cols.map((c) => c.phase.title)).toEqual(['Review (live)', 'Verify', 'Extra']);
    expect(cols[0].agents.map((a) => a.index)).toEqual([0, 1]);
    expect(cols[1].agents).toEqual([]);
    expect(cols[2].agents.map((a) => a.index)).toEqual([2]);
  });
});
