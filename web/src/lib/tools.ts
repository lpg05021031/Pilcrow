// Presentation metadata for Claude Code tools: icon, a verb, and a one-line target.
import {
  SquareTerminal, FileText, FilePen, FilePlus, Search, FolderSearch, Globe, ListChecks, Bot, ClipboardCheck, MessageCircleQuestionMark,
  BookOpen, Plug, Sparkles, Wrench, Clock, GitBranch, CircleStop, ListTodo, Map as MapIcon, Bell, Layers, type LucideIcon, Workflow } from 'lucide-react';
import { ArrowUpRight as HelperStart, Clock as HelperWait, Eye as HelperRead, Send as HelperSend, Square as HelperStop, List as HelperList } from 'lucide-react';
import { basename, relPath, truncate, firstLine } from './format';
import type { ToolInput } from './types';

/** A string field of a tool input, or '' — inputs are loose JSON and a wrong type must not throw. */
export const str = (i: ToolInput | undefined | null, k: string): string => (i && typeof i[k] === 'string' ? (i[k] as string) : '');
export const num = (i: ToolInput | undefined | null, k: string): number | undefined => (i && typeof i[k] === 'number' ? (i[k] as number) : undefined);


export interface ToolMeta {
  icon: LucideIcon;
  verb: string;
  target: string;
  mono?: boolean;
}

const short = (p: string, cwd?: string) => (p ? relPath(p, cwd) : '');

export function toolMeta(name: string, input: ToolInput | undefined | null, cwd?: string): ToolMeta {
  const i: ToolInput = input || {};
  const S = (k: string) => str(i, k);
  const N = (k: string) => num(i, k);
  switch (name) {
    case 'Bash':
    case 'PowerShell':
      return { icon: SquareTerminal, verb: S('description') ? '' : Boolean(i.run_in_background) ? 'Run in background' : 'Run', target: S('description') || firstLine(S('command') || ''), mono: !S('description') };
    case 'BashOutput':
      return { icon: SquareTerminal, verb: 'Read output', target: S('bash_id') || S('shell_id') || '' };
    case 'KillShell':
    case 'KillBash':
    case 'TaskStop':
      return { icon: CircleStop, verb: 'Stop task', target: S('task_id') || S('shell_id') || '' };
    case 'Read':
      return { icon: FileText, verb: 'Read', target: short(S('file_path'), cwd) + (N('offset') ? `:${N('offset')}${N('limit') ? '–' + (N('offset')! + N('limit')!) : ''}` : ''), mono: true };
    case 'Edit':
    case 'MultiEdit':
      return { icon: FilePen, verb: 'Edit', target: short(S('file_path'), cwd), mono: true };
    case 'Write':
      return { icon: FilePlus, verb: 'Write', target: short(S('file_path'), cwd), mono: true };
    case 'NotebookEdit':
      return { icon: BookOpen, verb: 'Edit notebook', target: short(S('notebook_path'), cwd), mono: true };
    case 'Grep':
      return { icon: Search, verb: 'Search', target: `${S('pattern') || ''}${S('path') ? ' in ' + short(S('path'), cwd) : ''}${S('glob') ? ' (' + S('glob') + ')' : ''}`, mono: true };
    case 'Glob':
      return { icon: FolderSearch, verb: 'Find files', target: `${S('pattern') || ''}${S('path') ? ' in ' + short(S('path'), cwd) : ''}`, mono: true };
    case 'WebFetch':
      return { icon: Globe, verb: 'Fetch', target: S('url') || '' };
    case 'WebSearch':
    case 'web_search':
      return { icon: Globe, verb: 'Search the web', target: S('query') || '' };
    case 'TodoWrite':
      return { icon: ListChecks, verb: 'Update to-dos', target: todoSummary(i.todos) };
    case 'TaskCreate':
      return { icon: ListTodo, verb: 'Add task', target: S('subject') || '' };
    case 'TaskUpdate':
      return { icon: ListTodo, verb: 'Update task', target: `#${S('taskId')}${S('status') ? ' → ' + S('status').replace('_', ' ') : ''}${S('subject') ? ' ' + i.subject : ''}` };
    case 'TaskList':
    case 'TaskGet':
      return { icon: ListTodo, verb: 'Check tasks', target: S('taskId') ? `#${S('taskId')}` : '' };
    case 'Workflow':
    case 'RunWorkflow': {
      const m = typeof i.script === 'string' ? i.script.match(/\bname\s*:\s*['"`]([^'"`]+)['"`]/) : null;
      return { icon: Workflow, verb: 'Workflow', target: S('name') || (m ? m[1] : '') || S('description') || '' };
    }
    case 'Agent':
    case 'Task':
      return { icon: Bot, verb: S('subagent_type') && S('subagent_type') !== 'general-purpose' ? `Agent · ${S('subagent_type')}` : 'Agent', target: S('description') || truncate(firstLine(S('prompt') || ''), 80) };
    case 'SendMessage':
      return { icon: Bot, verb: 'Message agent', target: S('to') || '' };
    case 'ExitPlanMode':
      return { icon: ClipboardCheck, verb: 'Plan ready', target: '' };
    case 'EnterPlanMode':
      return { icon: MapIcon, verb: 'Enter plan mode', target: '' };
    case 'AskUserQuestion':
      return { icon: MessageCircleQuestionMark, verb: 'Question', target: (Array.isArray(i.questions) ? (i.questions as { header?: string }[]) : []).map((q) => q.header).filter(Boolean).join(' · ') };
    case 'Skill':
      return { icon: Sparkles, verb: 'Use skill', target: S('skill') || S('name') || S('command') || '' };
    case 'SlashCommand':
      return { icon: Sparkles, verb: 'Run command', target: S('command') || '' };
    case 'ToolSearch':
      return { icon: Layers, verb: 'Load tools', target: S('query') || '' };
    case 'EnterWorktree':
    case 'ExitWorktree':
      return { icon: GitBranch, verb: name === 'EnterWorktree' ? 'Enter worktree' : 'Leave worktree', target: S('name') || S('path') || '' };
    case 'CronCreate':
    case 'CronDelete':
    case 'CronList':
    case 'ScheduleWakeup':
      return { icon: Clock, verb: name.replace(/([a-z])([A-Z])/g, '$1 $2'), target: S('schedule') || S('cron_expression') || S('prompt') || '' };
    case 'PushNotification':
      return { icon: Bell, verb: 'Notify', target: S('message') || '' };
    case 'ListMcpResourcesTool':
    case 'ReadMcpResourceTool':
      return { icon: Plug, verb: name === 'ReadMcpResourceTool' ? 'Read resource' : 'List resources', target: S('uri') || S('server') || '' };
    default:
      if (name.startsWith('mcp__helpers__')) {
        // the lead's tools (HELPERS.md): named by what happened, not by the server
        const tool = name.slice('mcp__helpers__'.length);
        const meta: Record<string, [LucideIcon, string]> = {
          start: [HelperStart, 'Started helper'],
          wait: [HelperWait, 'Waited for'],
          read: [HelperRead, 'Read helper'],
          send: [HelperSend, 'Told helper'],
          stop: [HelperStop, 'Stopped helper'],
          list: [HelperList, 'Listed helpers'],
        };
        const [icon, verb] = meta[tool] || [HelperStart, `Helpers · ${tool}`];
        const names = Array.isArray(i?.names) ? (i!.names as unknown[]).map(String) : null;
        const where = S('repo') || (S('cwd') ? basename(S('cwd')) : '');
        const target = tool === 'wait' ? (names?.length ? names.join(', ') : 'all helpers') : tool === 'list' ? '' : tool === 'start' && where ? `${S('name')} in ${where}` : S('name');
        return { icon, verb, target };
      }
      if (name.startsWith('mcp__')) {
        const [, server, ...rest] = name.split('__');
        const tool = rest.join('__').replace(/_/g, ' ');
        // Servers are often registered under an id rather than a name; an id tells the reader nothing.
        const opaque = /^[0-9a-f-]{20,}$/i.test(server) || /^[0-9a-z]{24,}$/i.test(server);
        const verb = (opaque ? tool : `${server.replace(/[_-]+/g, ' ')} · ${tool}`).replace(/^./, (c) => c.toUpperCase());
        return { icon: Plug, verb, target: summarizeMcp(i) };
      }
      return { icon: Wrench, verb: name, target: summarizeInput(i) };
  }
}

function todoSummary(todos: unknown) {
  if (!Array.isArray(todos)) return '';
  const list = todos as { status?: string; activeForm?: string; content?: string }[];
  const done = list.filter((t) => t.status === 'completed').length;
  const active = list.find((t) => t.status === 'in_progress');
  return `${done}/${list.length} done${active ? ' · ' + (active.activeForm || active.content) : ''}`;
}

/** The gist of an MCP call: the one or two short values that say what it was asked to do. */
function summarizeMcp(i: ToolInput) {
  if (!i || typeof i !== 'object') return '';
  const pref = ['action', 'prompt', 'query', 'name', 'path', 'url', 'command', 'text', 'title', 'id'];
  const entries = Object.entries(i).filter(([, v]) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean');
  entries.sort((a, b) => (pref.indexOf(a[0]) + 1 || 99) - (pref.indexOf(b[0]) + 1 || 99));
  return entries
    .slice(0, 2)
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${truncate(firstLine(String(v)), 40)}`)
    .join(' · ');
}

function summarizeInput(i: ToolInput) {
  if (!i || typeof i !== 'object') return '';
  for (const k of ['path', 'file_path', 'url', 'query', 'name', 'command', 'pattern', 'id']) {
    const v = i[k];
    if (typeof v === 'string') return truncate(v, 90);
  }
  const first = Object.values(i).find((v) => typeof v === 'string') as string | undefined;
  return first ? truncate(firstLine(first), 90) : '';
}

/** Tools whose cards should start expanded. */
export function expandByDefault(name: string) {
  return ['Edit', 'MultiEdit', 'Write', 'ExitPlanMode', 'AskUserQuestion', 'TodoWrite', 'NotebookEdit'].includes(name);
}

export function isFileTool(name: string) {
  return ['Read', 'Edit', 'MultiEdit', 'Write', 'NotebookEdit'].includes(name);
}

export function filePathOf(name: string, input: ToolInput | undefined | null): string | null {
  if (!input) return null;
  return str(input, 'file_path') || str(input, 'notebook_path') || null;
}

export { basename };
