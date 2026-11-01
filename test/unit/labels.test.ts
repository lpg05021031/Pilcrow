import { describe, expect, it } from 'vitest';
import { exploreLabel, isQuiet, langOf, liveLabel, parseNotes, tally, toolWord, workSummary } from '../../web/src/lib/labels';
import type { Item, ToolItem } from '../../web/src/lib/transcript';

let n = 0;
const tool = (name: string, input: Record<string, unknown> = {}, status: ToolItem['status'] = 'done', extra: Partial<ToolItem> = {}): ToolItem => ({ kind: 'tool', id: 'tool' + ++n, rev: 0, name, input, inputJson: '', inputDone: true, status, children: [], startTs: 0, parentId: null, ...extra });
const thinking = (streaming = false): Item => ({ kind: 'thinking', id: 'th' + ++n, rev: 0, text: 'hmm', streaming, startTs: 0 });

describe('toolWord', () => {
  it('names what kind of step a tool is', () => {
    expect(['Edit', 'Read', 'Grep', 'Bash', 'Agent', 'mcp__helpers__start', 'AskUserQuestion', 'ExitPlanMode', 'mcp__github__get', 'Skill'].map((name) => toolWord(tool(name)))).toEqual(['edit', 'read', 'search', 'command', 'agent', 'helper', 'ask', 'plan', 'mcp', 'other']);
  });
});

describe('liveLabel', () => {
  it('says what the last running step is doing', () => {
    expect(liveLabel([tool('Read', { file_path: '/a/b/parser.ts' }, 'running')])).toBe('Reading parser.ts');
    expect(liveLabel([tool('Bash', { description: 'Run the tests' }, 'running')])).toBe('Run the tests');
    expect(liveLabel([tool('Read'), thinking(true)])).toBe('Thinking');
    expect(liveLabel([tool('Read')])).toBeNull();
  });
});

describe('workSummary', () => {
  it('counts files edited (once each), commands, reads and failures, in plain words', () => {
    const s = workSummary([tool('Edit', { file_path: '/x.ts' }), tool('Edit', { file_path: '/x.ts' }), tool('Bash', {}, 'error'), tool('Read'), tool('Read')]);
    expect(s).toEqual({ text: 'Edited a file, ran a command, read 2 files', failed: 1, steps: 5 });
  });
  it('a stretch of thinking alone', () => {
    expect(workSummary([thinking()]).text).toBe('Thought it through');
  });
});

describe('tally', () => {
  it('counts the steps under a chapter heading and the time they took', () => {
    const t = tally([tool('Edit', {}, 'done', { startTs: 1000, endTs: 2000 }), tool('Grep', {}, 'done', { startTs: 2000, endTs: 4000 }), tool('Grep', {}, 'denied', { startTs: 4000, endTs: 5000 })]);
    expect(t).toEqual({ text: '1 edit · 2 searches', failed: 1, ms: 4000, n: 3 });
  });
});

describe('isQuiet and exploreLabel', () => {
  it('finished read-only steps fold; failing, waiting or backgrounded ones do not', () => {
    expect(isQuiet(tool('Read'), false)).toBe(true);
    expect(isQuiet(tool('Read'), true)).toBe(false);
    expect(isQuiet(tool('Read', {}, 'running'), false)).toBe(false);
    expect(isQuiet(tool('Bash', { run_in_background: true }), false)).toBe(false);
    expect(isQuiet(tool('Edit'), false)).toBe(false);
  });
  it('the Explored line', () => {
    expect(exploreLabel([tool('Read'), tool('Read'), tool('Grep'), tool('WebFetch')])).toBe('Read 2 files, searched once, checked the web');
  });
});

describe('parseNotes', () => {
  it('reads each helper note with its attributes, and keeps the text around them apart', () => {
    const r = parseNotes('Before.\n<helper-note name="tests" state="done" files="2">\nAll green.\n</helper-note>\nAfter.');
    expect(r.parts).toEqual([{ name: 'tests', state: 'done', files: '2', body: 'All green.' }]);
    expect(r.tail).toBe('Before. After.');
  });
});

describe('langOf', () => {
  it('marks Japanese by its kana, Chinese by Han alone, in the reader’s Chinese locale if any', () => {
    expect(langOf('このファイルを直してください', 'en-US')).toBe('ja');
    expect(langOf('漢字とかな', 'zh-TW')).toBe('ja');
    expect(langOf('請修正這個檔案', 'en-US')).toBe('zh-Hant');
    expect(langOf('请修复这个文件', 'zh-CN')).toBe('zh-CN');
    expect(langOf('Fix the parser', 'zh-TW')).toBeUndefined();
  });
});
