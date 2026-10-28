import type { TaskInfo, WorkflowAgent, WorkflowPhase } from './types';

export interface WorkflowMeta {
  name?: string;
  description?: string;
  phases: { title: string; kind?: string }[];
}

/**
 * The workflow script opens with `export const meta = { name, description, phases }` as a pure
 * literal. Read it without executing anything so the card has a title and columns before the
 * first progress event arrives.
 */
export function parseWorkflowMeta(script?: string): WorkflowMeta {
  const out: WorkflowMeta = { phases: [] };
  if (!script) return out;
  const m = script.match(/export\s+const\s+meta\s*=\s*\{/);
  if (!m || m.index === undefined) return out;
  // The body runs to the brace that closes the literal, not the first `}` inside it (phases are
  // objects of their own), so walk the braces, skipping over strings.
  const start = m.index + m[0].length;
  let depth = 1;
  let i = start;
  let quote: string | null = null;
  for (; i < script.length && depth > 0; i++) {
    const c = script[i];
    if (quote) {
      if (c === '\\') i++;
      else if (c === quote) quote = null;
    } else if (c === "'" || c === '"' || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}') depth--;
  }
  const body = script.slice(start, depth === 0 ? i - 1 : i);
  const str = (key: string) => {
    const r = body.match(new RegExp(`\\b${key}\\s*:\\s*(?:'((?:[^'\\\\]|\\\\.)*)'|"((?:[^"\\\\]|\\\\.)*)"|\`((?:[^\`\\\\]|\\\\.)*)\`)`));
    return r ? (r[1] ?? r[2] ?? r[3]).replace(/\\(.)/g, '$1') : undefined;
  };
  out.name = str('name');
  out.description = str('description');
  const ph = body.match(/\bphases\s*:\s*\[([\s\S]*?)\]/);
  if (ph) {
    const re = /'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|\{([^}]*)\}/g;
    let x: RegExpExecArray | null;
    while ((x = re.exec(ph[1]))) {
      if (x[3] !== undefined) {
        const t = x[3].match(/\b(?:title|name)\s*:\s*(?:'([^']*)'|"([^"]*)")/);
        const k = x[3].match(/\bkind\s*:\s*(?:'([^']*)'|"([^"]*)")/);
        out.phases.push({ title: t ? t[1] ?? t[2] : `Phase ${out.phases.length + 1}`, kind: k ? k[1] ?? k[2] : undefined });
      } else out.phases.push({ title: (x[1] ?? x[2]).replace(/\\(.)/g, '$1') });
    }
  }
  return out;
}

export type AgentState = 'queued' | 'running' | 'done' | 'error' | 'cached' | 'blocked';

export function agentState(a: WorkflowAgent): AgentState {
  if (a.state === 'done') return a.cached ? 'cached' : 'done';
  if (a.state === 'error') return a.blocked ? 'blocked' : 'error';
  if (a.state === 'start' && a.queuedAt !== undefined && a.startedAt === undefined) return 'queued';
  return 'running';
}

export function workflowCounts(agents: (WorkflowAgent | undefined)[]) {
  const c = { total: 0, queued: 0, running: 0, done: 0, error: 0 };
  for (const a of agents) {
    if (!a) continue;
    c.total++;
    const s = agentState(a);
    if (s === 'queued') c.queued++;
    else if (s === 'running') c.running++;
    else if (s === 'done' || s === 'cached') c.done++;
    else c.error++;
  }
  return c;
}

/** Phases as columns: the announced ones, then any the script declared that have not started yet. */
export function workflowColumns(task: TaskInfo | undefined, meta: WorkflowMeta): { phase: WorkflowPhase; agents: WorkflowAgent[] }[] {
  const phases: WorkflowPhase[] = [];
  const seen = new Set<number>();
  for (const p of task?.workflow?.phases || [])
    if (p) {
      phases[p.index] = p;
      seen.add(p.index);
    }
  meta.phases.forEach((p, i) => {
    if (!seen.has(i)) phases[i] = { index: i, title: p.title, kind: p.kind };
  });
  const cols = phases.filter(Boolean).map((phase) => ({ phase, agents: [] as WorkflowAgent[] }));
  const byIndex = new Map(cols.map((c) => [c.phase.index, c]));
  for (const a of task?.workflow?.agents || []) {
    if (!a) continue;
    const pi = a.phaseIndex ?? 0;
    let col = byIndex.get(pi);
    if (!col) {
      col = { phase: { index: pi, title: a.phaseTitle || `Phase ${pi + 1}` }, agents: [] };
      byIndex.set(pi, col);
      cols.push(col);
      cols.sort((x, y) => x.phase.index - y.phase.index);
    }
    col.agents.push(a);
  }
  for (const c of cols) c.agents.sort((x, y) => x.index - y.index);
  return cols;
}
