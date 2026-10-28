import { useMemo } from 'react';
import { ListChecks, Bot, SquareTerminal, CircleStop, Check, X, Activity, Workflow } from 'lucide-react';
import { WorkflowDetail } from '../tools/WorkflowBody';
import { useStore, models, call } from '../../lib/store';
import type { SessionSummary } from '../../lib/types';
import { duration, tokens, plural, timeAgo } from '../../lib/format';
import { TodoBody } from '../tools/ToolCard';
import { Spinner } from '../ui/bits';
import { str } from '../../lib/tools';
import type { Todo } from '../tools/ToolCard';

function reconstructTodos(sessionId: string) {
  const m = models.get(sessionId);
  if (!m) return null;
  const todo = m.latestTool('TodoWrite');
  // Newer Claude Code versions track tasks with TaskCreate/TaskUpdate instead.
  const tasks = new Map<string, { id: string; content: string; status: string; activeForm?: string }>();
  let order = 0;
  let lastTaskTs = 0;
  for (const t of m.tools.values()) {
    if (t.name === 'TaskCreate' && t.status === 'done') {
      const task = t.structured?.task as { id?: string | number } | undefined;
      const id = String(task?.id ?? t.structured?.id ?? (t.result?.text.match(/#?(\d+)/) || [])[1] ?? ++order);
      tasks.set(id, { id, content: str(t.input, 'subject'), status: 'pending', activeForm: str(t.input, 'activeForm') || undefined });
      lastTaskTs = Math.max(lastTaskTs, t.startTs);
    } else if (t.name === 'TaskUpdate' && t.status === 'done') {
      const id = String(t.input?.taskId);
      const cur = tasks.get(id);
      if (!cur) continue;
      if (t.input?.status === 'deleted') tasks.delete(id);
      else Object.assign(cur, { status: t.input?.status || cur.status, content: t.input?.subject || cur.content, activeForm: t.input?.activeForm || cur.activeForm });
      lastTaskTs = Math.max(lastTaskTs, t.startTs);
    }
  }
  if (tasks.size && (!todo || lastTaskTs > todo.startTs)) return [...tasks.values()];
  return todo?.input?.todos || null;
}

export function TasksPanel({ session }: { session: SessionSummary }) {
  const rt = useStore((s) => s.rt[session.id]);
  const todos = useMemo(() => reconstructTodos(session.id), [session.id, rt?.rev]); // eslint-disable-line react-hooks/exhaustive-deps
  const tasks = rt?.tasks || [];
  const workflows = tasks.filter((t) => t.type === 'local_workflow' && !t.ambient);
  // Sub-agents that a workflow spawned are drawn inside the workflow; everything else lists here.
  const inWorkflow = (t: (typeof tasks)[number]) => t.type === 'local_agent' && t.ownedBySubagent && workflows.some((w) => w.startedAt && t.startedAt && t.startedAt >= w.startedAt && (!w.endedAt || t.startedAt <= w.endedAt + 2000));
  const running = tasks.filter((t) => t.status === 'running' && t.type !== 'local_workflow' && !t.ambient && !inWorkflow(t));
  const finished = tasks.filter((t) => t.status !== 'running' && t.type !== 'local_workflow' && !t.ambient && !inWorkflow(t)).slice(-12).reverse();
  const done = Array.isArray(todos) ? (todos as Todo[]).filter((t) => t.status === 'completed').length : 0;

  return (
    <div className="tasks-panel">
      <section className="ps">
        <div className="ps-head">
          <ListChecks size={14} /> To-do list
          {Array.isArray(todos) && todos.length > 0 && (
            <span className="faint">
              {done} of {todos.length} done
            </span>
          )}
        </div>
        {Array.isArray(todos) && todos.length ? (
          <>
            <div className="progress">
              <div style={{ width: `${(done / todos.length) * 100}%` }} />
            </div>
            <TodoBody todos={todos} />
          </>
        ) : (
          <div className="ps-empty">Claude's plan for multi-step work appears here.</div>
        )}
      </section>

      {workflows.length > 0 && (
        <section className="ps">
          <div className="ps-head">
            <Workflow size={14} /> Workflows
          </div>
          {workflows
            .slice()
            .reverse()
            .map((t) => (
              <WorkflowDetail key={t.id} task={t} sessionId={session.id} allTasks={tasks} />
            ))}
        </section>
      )}

      <section className="ps">
        <div className="ps-head">
          <Activity size={14} /> Running now
        </div>
        {running.length ? (
          running.map((t) => (
            <div key={t.id} className="task-row">
              {t.type === 'local_bash' ? <SquareTerminal size={13} /> : <Bot size={13} />}
              <div className="grow">
                <div className="ellipsis">{t.description || t.subagentType || t.type}</div>
                <div className="faint task-sub">
                  <Spinner size={10} /> {t.progress || (t.lastTool ? `using ${t.lastTool}` : 'working')}
                  {t.usage ? ` · ${plural(t.usage.tool_uses, 'tool use')} · ${tokens(t.usage.total_tokens)} tokens` : ''}
                  {t.startedAt ? ` · ${duration(Date.now() - t.startedAt)}` : ''}
                </div>
              </div>
              <button className="btn sm ghost danger" title="Stop this task" onClick={() => call('session.stopTask', { id: session.id, taskId: t.id })}>
                <CircleStop size={13} /> Stop
              </button>
            </div>
          ))
        ) : (
          <div className="ps-empty">No background commands or agents running.</div>
        )}
      </section>

      {finished.length > 0 && (
        <section className="ps">
          <div className="ps-head">Finished</div>
          {finished.map((t) => (
            <div key={t.id} className="task-row done">
              {t.status === 'completed' ? <Check size={13} className="st-ok" /> : <X size={13} className="st-err" />}
              <div className="grow">
                <div className="ellipsis">{t.description || t.type}</div>
                <div className="faint task-sub">
                  {t.status}
                  {t.endedAt ? ` · ${timeAgo(t.endedAt)}` : ''}
                  {t.summary ? ` · ${t.summary.slice(0, 120)}` : ''}
                </div>
              </div>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
