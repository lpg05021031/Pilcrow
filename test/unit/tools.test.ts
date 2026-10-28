import { describe, expect, it } from 'vitest';
import { expandByDefault, filePathOf, isFileTool, toolMeta } from '../../web/src/lib/tools';

describe('toolMeta', () => {
  const cwd = '/home/me/proj';
  it('Bash: the description when given, else the first line of the command in mono', () => {
    expect(toolMeta('Bash', { command: 'ls -la\nwc -l' }, cwd)).toMatchObject({ verb: 'Run', target: 'ls -la', mono: true });
    expect(toolMeta('Bash', { command: 'ls', description: 'List files' }, cwd)).toMatchObject({ verb: '', target: 'List files', mono: false });
    expect(toolMeta('Bash', { command: 'sleep 9', run_in_background: true }, cwd)).toMatchObject({ verb: 'Run in background' });
  });
  it('Read shows the path relative to the folder, with the line range', () => {
    expect(toolMeta('Read', { file_path: '/home/me/proj/src/a.ts', offset: 10, limit: 5 }, cwd).target).toBe('src/a.ts:10–15');
    expect(toolMeta('Read', { file_path: '/home/me/proj/src/a.ts' }, cwd).target).toBe('src/a.ts');
  });
  it('Edit / Write / Grep / Glob', () => {
    expect(toolMeta('Edit', { file_path: '/home/me/proj/x.py' }, cwd)).toMatchObject({ verb: 'Edit', target: 'x.py' });
    expect(toolMeta('Write', { file_path: '/elsewhere/x.py' }, cwd)).toMatchObject({ verb: 'Write', target: '/elsewhere/x.py' });
    expect(toolMeta('Grep', { pattern: 'TODO', path: '/home/me/proj/src', glob: '*.ts' }, cwd).target).toBe('TODO in src (*.ts)');
    expect(toolMeta('Glob', { pattern: '**/*.md' }, cwd).target).toBe('**/*.md');
  });
  it('wrong input types never throw', () => {
    expect(toolMeta('Read', { file_path: 42 } as never, cwd).target).toBe('');
    expect(toolMeta('TodoWrite', { todos: 'nope' }, cwd).target).toBe('');
    expect(toolMeta('Bash', null, cwd).target).toBe('');
  });
  it('TodoWrite summarises progress', () => {
    const todos = [{ status: 'completed' }, { status: 'in_progress', activeForm: 'Writing tests' }, { status: 'pending' }];
    expect(toolMeta('TodoWrite', { todos }, cwd).target).toBe('1/3 done · Writing tests');
  });
  it('Agent verb carries the subagent type', () => {
    expect(toolMeta('Agent', { subagent_type: 'Explore', description: 'find callers' }, cwd)).toMatchObject({ verb: 'Agent · Explore', target: 'find callers' });
    expect(toolMeta('Agent', { subagent_type: 'general-purpose', prompt: 'Look\nmore' }, cwd)).toMatchObject({ verb: 'Agent', target: 'Look' });
  });
  it('Workflow name comes from the script when not given', () => {
    expect(toolMeta('Workflow', { script: "export const meta = { name: 'review-all' }" }, cwd).target).toBe('review-all');
  });
  it('MCP tools: server and tool names, opaque ids dropped', () => {
    expect(toolMeta('mcp__github__list_issues', { query: 'open', repo: 'a/b' }, cwd).verb).toBe('Github · list issues');
    expect(toolMeta('mcp__github__list_issues', { query: 'open', repo: 'a/b' }, cwd).target).toBe('query open · repo a/b');
    expect(toolMeta('mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__read', {}, cwd).verb).toBe('Read');
  });
  it('unknown tools fall back to a string field', () => {
    expect(toolMeta('Mystery', { url: 'http://x', other: 1 }, cwd)).toMatchObject({ verb: 'Mystery', target: 'http://x' });
    expect(toolMeta('Mystery', { foo: 'bar' }, cwd).target).toBe('bar');
  });
});

describe('helpers', () => {
  it('expandByDefault / isFileTool / filePathOf', () => {
    expect(expandByDefault('Edit')).toBe(true);
    expect(expandByDefault('Read')).toBe(false);
    expect(isFileTool('NotebookEdit')).toBe(true);
    expect(isFileTool('Bash')).toBe(false);
    expect(filePathOf('NotebookEdit', { notebook_path: '/n.ipynb' })).toBe('/n.ipynb');
    expect(filePathOf('Read', null)).toBeNull();
  });
});
