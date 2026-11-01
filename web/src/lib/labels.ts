// What the transcript says about Claude's work, as words: the line on a folded turn (live and done),
// the tally under a chapter heading, the "Explored" line, and the cards a helper's note becomes.
// Pure, so they can be tested without drawing anything.
import type { Item, ToolItem } from './transcript';
import { str } from './tools';
import { basename, truncate, plural } from './format';

/** What Claude is doing right now, for the fold line of a turn that is still running. */
export function liveLabel(work: Item[]): string | null {
  for (let i = work.length - 1; i >= 0; i--) {
    const it = work[i];
    if (it.kind === 'tool' && (it.status === 'running' || it.status === 'streaming')) {
      const file = str(it.input, 'file_path') || str(it.input, 'notebook_path');
      switch (toolWord(it)) {
        case 'edit':
          return file ? `Editing ${basename(file)}` : 'Editing a file';
        case 'read':
          return file ? `Reading ${basename(file)}` : 'Reading a file';
        case 'search':
          return 'Searching';
        case 'command':
          return it.input?.description ? truncate(String(it.input.description), 64) : 'Running a command';
        case 'agent':
          return 'Running an agent';
        case 'mcp':
          return 'Using a connector';
        case 'helper':
          return HELPER_LIVE[it.name.slice('mcp__helpers__'.length)] || 'Working with helpers';
        case 'ask':
          return 'Asking you a question';
        case 'plan':
          return 'Waiting for your review of the plan';
        default:
          return `Using ${it.name}`;
      }
    }
    if (it.kind === 'thinking' && it.streaming) return 'Thinking';
  }
  return null;
}

const HELPER_LIVE: Record<string, string> = { start: 'Starting a helper', wait: 'Waiting for helpers', read: "Reading a helper's result", send: 'Telling a helper', stop: 'Stopping a helper', list: 'Listing helpers' };

export function toolWord(t: ToolItem) {
  const n = t.name;
  if (['Edit', 'MultiEdit', 'Write', 'NotebookEdit'].includes(n)) return 'edit';
  if (n === 'Read' || n === 'NotebookRead') return 'read';
  if (['Grep', 'Glob', 'LS', 'WebSearch', 'WebFetch', 'ToolSearch'].includes(n)) return 'search';
  if (n === 'Bash' || n === 'PowerShell' || n === 'BashOutput') return 'command';
  if (n === 'Task' || n === 'Agent' || n === 'Workflow' || n === 'RunWorkflow') return 'agent';
  if (n.startsWith('mcp__helpers__')) return 'helper';
  if (n === 'AskUserQuestion') return 'ask';
  if (n === 'ExitPlanMode') return 'plan';
  if (n.startsWith('mcp__')) return 'mcp';
  return 'other';
}

export function workSummary(work: Item[]) {
  const tools = work.filter((it): it is ToolItem => it.kind === 'tool');
  const files = new Set<string>();
  let reads = 0;
  let searches = 0;
  let commands = 0;
  let agents = 0;
  let mcp = 0;
  let other = 0;
  let failed = 0;
  let starts = 0;
  let helperSteps = 0;
  let asks = 0;
  let plans = 0;
  for (const t of tools) {
    if (t.status === 'error' || t.status === 'denied') failed++;
    switch (toolWord(t)) {
      case 'edit':
        files.add(str(t.input, 'file_path') || str(t.input, 'notebook_path') || t.id);
        break;
      case 'read':
        reads++;
        break;
      case 'search':
        searches++;
        break;
      case 'command':
        commands++;
        break;
      case 'agent':
        agents++;
        break;
      case 'helper':
        if (t.name.endsWith('__start')) starts++;
        else helperSteps++;
        break;
      case 'ask':
        asks++;
        break;
      case 'plan':
        plans++;
        break;
      case 'mcp':
        mcp++;
        break;
      default:
        other++;
    }
  }
  for (const it of work) if (it.kind === 'notice' && it.level === 'error') failed++;
  const parts: string[] = [];
  if (files.size) parts.push(`edited ${files.size === 1 ? 'a file' : `${files.size} files`}`);
  if (commands) parts.push(`ran ${commands === 1 ? 'a command' : `${commands} commands`}`);
  if (reads) parts.push(`read ${reads === 1 ? 'a file' : `${reads} files`}`);
  if (searches) parts.push(`searched ${searches === 1 ? 'once' : `${searches} times`}`);
  if (agents) parts.push(`${agents === 1 ? 'ran an agent' : `ran ${agents} agents`}`);
  if (starts) parts.push(`started ${starts === 1 ? 'a helper' : `${starts} helpers`}`);
  if (helperSteps && !starts) parts.push('worked with helpers');
  if (asks) parts.push(`asked you ${asks === 1 ? 'a question' : `${asks} questions`}`);
  if (plans) parts.push('proposed a plan');
  if (mcp) parts.push(`used ${mcp === 1 ? 'a connector' : `${mcp} connector tools`}`);
  if (other) parts.push(`${other} other ${other === 1 ? 'step' : 'steps'}`);
  if (!parts.length) parts.push(work.some((it) => it.kind === 'thinking') ? 'thought it through' : 'worked');
  const text = parts.join(', ');
  return { text: text.charAt(0).toUpperCase() + text.slice(1), failed, steps: tools.length };
}

export function tally(steps: Item[]) {
  const tools = steps.filter((it): it is ToolItem => it.kind === 'tool');
  let edits = 0;
  let commands = 0;
  let reads = 0;
  let searches = 0;
  let agents = 0;
  let mcp = 0;
  let helpers = 0;
  let asks = 0;
  let plans = 0;
  let other = 0;
  let failed = 0;
  for (const t of tools) {
    if (t.status === 'error' || t.status === 'denied') failed++;
    const w = toolWord(t);
    if (w === 'edit') edits++;
    else if (w === 'command') commands++;
    else if (w === 'read') reads++;
    else if (w === 'search') searches++;
    else if (w === 'agent') agents++;
    else if (w === 'helper') helpers++;
    else if (w === 'ask') asks++;
    else if (w === 'plan') plans++;
    else if (w === 'mcp') mcp++;
    else other++;
  }
  const parts: string[] = [];
  if (edits) parts.push(plural(edits, 'edit'));
  if (commands) parts.push(plural(commands, 'command'));
  if (reads) parts.push(plural(reads, 'read'));
  if (searches) parts.push(`${searches} search${searches > 1 ? 'es' : ''}`);
  if (agents) parts.push(plural(agents, 'agent'));
  if (helpers) parts.push(plural(helpers, 'helper step'));
  if (asks) parts.push(plural(asks, 'question'));
  if (plans) parts.push(plural(plans, 'plan'));
  if (mcp) parts.push(plural(mcp, 'connector call'));
  if (other) parts.push(`${other} other`);
  const first = tools[0]?.startTs;
  const last = tools[tools.length - 1];
  const ms = first && last ? (last.endTs || last.startTs) - first : 0;
  return { text: parts.join(' · '), failed, ms, n: tools.length };
}

// Read-only steps that finished cleanly fold into one "Explored" line; edits, agents, failures and
// anything waiting on the user always stay visible as their own rows.
const QUIET = new Set(['Read', 'Grep', 'Glob', 'LS', 'WebSearch', 'WebFetch', 'ToolSearch', 'BashOutput', 'TaskList', 'TaskGet', 'NotebookRead']);
export function isQuiet(t: ToolItem, waiting: boolean) {
  if (waiting || t.status !== 'done') return false;
  return QUIET.has(t.name) || ((t.name === 'Bash' || t.name === 'PowerShell') && !t.result?.isError && !t.input?.run_in_background);
}

export function exploreLabel(items: ToolItem[]) {
  const n = (names: string[]) => items.filter((t) => names.includes(t.name)).length;
  const parts: string[] = [];
  const cmds = n(['Bash', 'PowerShell', 'BashOutput']);
  const reads = n(['Read', 'NotebookRead']);
  const searches = n(['Grep', 'Glob', 'LS', 'ToolSearch']);
  const web = n(['WebSearch', 'WebFetch']);
  const tasks = n(['TaskList', 'TaskGet']);
  if (cmds) parts.push(cmds === 1 ? 'ran a command' : `ran ${cmds} commands`);
  if (reads) parts.push(reads === 1 ? 'read a file' : `read ${reads} files`);
  if (searches) parts.push(searches === 1 ? 'searched once' : `searched ${searches} times`);
  if (web) parts.push(web === 1 ? 'checked the web' : `checked ${web} web pages`);
  if (tasks) parts.push('checked tasks');
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// ---------------------------------------------------------------- a helper's note to its lead

const NOTE_RE = /<helper-note\s+([^>]*)>\n?([\s\S]*?)\n?<\/helper-note>/g;
const ATTR_RE = /(\w+)="([^"]*)"/g;

/** A helper's note to its lead (HELPERS.md §5.4): one record per <helper-note>, and the text around them. */
export function parseNotes(text: string) {
  const parts: Record<string, string>[] = [];
  const tail: string[] = [];
  let last = 0;
  for (const m of text.matchAll(NOTE_RE)) {
    const before = text.slice(last, m.index).trim();
    if (before) tail.push(before);
    const attrs: Record<string, string> = {};
    for (const a of m[1].matchAll(ATTR_RE)) attrs[a[1]] = a[2];
    attrs.body = m[2].trim();
    parts.push(attrs);
    last = (m.index || 0) + m[0].length;
  }
  const rest = text.slice(last).trim();
  if (rest) tail.push(rest);
  return { parts, tail: tail.join(' ') };
}

const KANA = /[\u3040-\u30ff\u31f0-\u31ff]/;
const HAN = /[\u3400-\u9fff\uf900-\ufaff]/;
/**
 * The language a message is marked with, so the browser picks the right CJK glyph forms, line
 * breaking and voice: Japanese when it has kana; Chinese when it has Han characters only, in the
 * reader's own Chinese locale when they have one, else Traditional (the face the interface is set
 * in), with no region; nothing for the rest.
 */
export function langOf(text: string, locale: string = navigator.language): string | undefined {
  const head = text.slice(0, 400);
  if (KANA.test(head)) return 'ja';
  if (!HAN.test(head)) return undefined;
  return /^zh\b/i.test(locale) ? locale : 'zh-Hant';
}
