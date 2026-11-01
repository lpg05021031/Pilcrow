/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// memory.js: a project's notes by hand (add, edit, pin, remove, clear), the cap that drops the oldest
// automatic ones first, what a new session is handed, when a turn is worth distilling, and a distil
// against a fake one-shot run; the notes file is written off the event loop and at once on quit.
import { describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const sdk = vi.hoisted(() => ({ reply: '', prompts: [] as string[] }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({
  query: ({ prompt }: any) => {
    sdk.prompts.push(prompt);
    return (async function* () {
      yield { type: 'assistant', parent_tool_use_id: null, message: { content: [{ type: 'text', text: sdk.reply }] } };
      yield { type: 'result', subtype: 'success', result: sdk.reply };
    })();
  },
}));
vi.mock('../../server/claude.js', () => ({ resolveClaude: async () => ({ path: '/bin/true' }) }));

const memory = await import('../../server/memory.js');
const { store } = await import('../../server/config.js');
const { flushSaves, folderKey, xdg } = await import('../../server/util.js');

let n = 0;
const project = () => `/home/me/project-${++n}`;
const session = (cwd: string, messages: any[] = []) => ({ id: `s${n}`, cwd, messages });
const asked = (text: string, at = Date.now()) => ({ type: 'user', timestamp: new Date(at).toISOString(), message: { content: [{ type: 'text', text }] } });
const toolUse = (name: string, input: any) => ({ type: 'assistant', timestamp: new Date().toISOString(), message: { content: [{ type: 'tool_use', name, input }] } });

describe('notes by hand', () => {
  it('adds, edits, pins, removes and clears all but the pinned', () => {
    const cwd = project();
    const a = memory.add(cwd, { text: '  Use pnpm, not npm  ' });
    const b = memory.add(cwd, { text: 'The API returns cents' });
    expect(a.text).toBe('Use pnpm, not npm');
    expect(() => memory.add(cwd, { text: ' ' })).toThrow('The note is empty.');
    memory.update(cwd, a.id, { pinned: true, text: 'Use pnpm' });
    expect(memory.list(cwd).notes.map((x) => x.text)).toEqual(['Use pnpm', 'The API returns cents']); // pinned first
    expect(() => memory.update(cwd, 'nope', { text: 'x' })).toThrow('No such note.');
    memory.remove(cwd, b.id);
    memory.add(cwd, { text: 'another' });
    memory.clear(cwd);
    expect(memory.list(cwd).notes.map((x) => x.id)).toEqual([a.id]);
  });

  it('keeps 80, dropping the oldest automatic notes before any by hand', () => {
    const cwd = project();
    const mine = memory.add(cwd, { text: 'by hand, oldest of all' });
    for (let i = 0; i < 85; i++) memory.add(cwd, { text: `auto ${i}`, kind: 'auto' });
    const { notes } = memory.list(cwd);
    expect(notes).toHaveLength(80);
    expect(notes.some((x) => x.id === mine.id)).toBe(true);
    expect(notes.some((x) => x.text === 'auto 0')).toBe(false);
  });
});

describe('what a new session is handed', () => {
  it('nothing for a project without notes; otherwise the notes within the budget', () => {
    const cwd = project();
    expect(memory.promptFor(cwd)).toBe('');
    for (let i = 0; i < 20; i++) memory.add(cwd, { text: `note ${i} `.padEnd(400, 'x') });
    const text = memory.promptFor(cwd);
    expect(text).toMatch(/^\n\n# Project memory\n/);
    expect(text.length).toBeLessThan(2600 + 400);
    expect(text.split('\n').filter((l) => l.startsWith('- ')).length).toBeLessThan(20);
  });

  it('a worktree reads the notes of its repository', () => {
    store.setMeta('wt-session', { worktree: { repoRoot: '/home/me/repo' }, project: '/home/me/repo' });
    expect(memory.projectOf({ id: 'wt-session', cwd: '/home/me/.local/share/pilcrow/worktrees/repo/x' })).toBe('/home/me/repo');
    store.deleteMeta('wt-session');
  });
});

describe('distilling', () => {
  it('is worth it after two prompts or three tool calls, not more often than every ten minutes', async () => {
    const cwd = project();
    expect(memory.worthIt(session(cwd, [asked('one')]))).toBe(false);
    expect(memory.worthIt(session(cwd, [asked('one'), asked('two')]))).toBe(true);
    expect(memory.worthIt(session(cwd, [toolUse('Read', {}), toolUse('Edit', {}), toolUse('Bash', {})]))).toBe(true);
    sdk.reply = 'NONE';
    const s = session(cwd, [asked('one'), asked('two')]);
    await memory.distill(s);
    expect(memory.worthIt(s)).toBe(false);
  });

  it('adds the new bullets, skips the ones it has, and keeps no more than five', async () => {
    const cwd = project();
    memory.add(cwd, { text: 'Tests need Docker' });
    sdk.reply = ['- Tests need Docker', '- The build caches in .turbo', 'not a bullet', ...Array.from({ length: 6 }, (_, i) => `* extra ${i}`)].join('\n');
    const { added } = await memory.distill(session(cwd, [asked('make the build faster')]));
    expect(added.map((x: any) => x.text)).toEqual(['The build caches in .turbo', 'extra 0', 'extra 1', 'extra 2', 'extra 3']);
    expect(sdk.prompts.at(-1)).toContain('USER: make the build faster');
    expect(sdk.prompts.at(-1)).toContain('- Tests need Docker'); // the existing notes, so it does not repeat them
  });
});

describe('the notes file', () => {
  it('is written off the event loop, and at once on quit', async () => {
    const cwd = project();
    const file = path.join(xdg('data'), 'memory', `${folderKey(cwd)}.json`);
    memory.add(cwd, { text: 'later' });
    expect(fs.existsSync(file)).toBe(false);
    await vi.waitFor(() => expect(JSON.parse(fs.readFileSync(file, 'utf8')).notes).toHaveLength(1));
    memory.add(cwd, { text: 'on quit' });
    flushSaves();
    expect(JSON.parse(fs.readFileSync(file, 'utf8')).notes.map((x: any) => x.text)).toEqual(['later', 'on quit']);
  });
});
