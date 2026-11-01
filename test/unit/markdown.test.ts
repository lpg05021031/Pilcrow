import { describe, expect, it, vi } from 'vitest';
import { extendBlocks, openFenceCode, splitBlocks } from '../../web/src/lib/markdown';

// Sanitising needs a DOM, which these tests (lexing only) run without.
vi.mock('dompurify', () => ({ default: { addHook: () => {}, sanitize: (s: string) => s } }));

const DOCS = [
  '# Title\n\nA paragraph that runs\nonto a second line.\n\n- one\n- two\n\n  continued in two\n- three\n\n```ts\nconst a = 1;\n\nconst b = 2;\n```\n\nAfter the code.\n',
  'Setext heading\n===\n\nText then a table:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> a quote\n> on two lines\n\n1. first\n2. second\n\n    indented code\n\nend',
  'para\n```js\nfenced right after a paragraph\n```\n<div>\nhtml block\n</div>\n\n---\n\n* [ ] task\n* [x] done\n',
  'A fence that grows:\n\n```tsx\nconst a = `x`;\n\n``not a fence\n   ```tsx not closing either\n  ```\nafter the indented close\n```\n\nTail text.\n',
];

/** What Markdown renders while text streams in: the blocks of each longer prefix, built on the last. */
function stream(doc: string, step: number) {
  let prev = { text: '', blocks: [] as string[] };
  for (let n = 1; n <= doc.length; n += step) {
    const text = doc.slice(0, Math.min(n, doc.length));
    prev = { text, blocks: extendBlocks(prev, text) };
    expect(prev.blocks, `at ${text.length} characters`).toEqual(splitBlocks(text));
  }
  return prev;
}

describe('extendBlocks', () => {
  it('splits a growing text exactly as splitting it from the start would, at every length', () => {
    for (const doc of DOCS) for (const step of [1, 3, 17]) stream(doc, step);
  });

  it('keeps the finished blocks as they were (the same strings) and lexes only from the last', () => {
    const a = { text: '# One\n\nTwo\n\nThr', blocks: splitBlocks('# One\n\nTwo\n\nThr') };
    const b = extendBlocks(a, a.text + 'ee and more');
    expect(b.slice(0, -1)).toEqual(a.blocks.slice(0, -1));
    expect(b.at(-1)).toBe('Three and more');
  });

  it('starts over when the text was changed rather than added to', () => {
    const a = { text: 'one\n\ntwo', blocks: splitBlocks('one\n\ntwo') };
    expect(extendBlocks(a, 'uno\n\ntwo three')).toEqual(splitBlocks('uno\n\ntwo three'));
    expect(extendBlocks(a, 'one')).toEqual(['one']);
  });
});

describe('openFenceCode', () => {
  it('is the code of a fence still being written, as drawn, and null once a line might close it', () => {
    expect(openFenceCode('```ts\nconst a = 1;\n')).toBe('const a = 1;');
    expect(openFenceCode('```ts\nconst a = 1;\nconst')).toBe('const a = 1;\nconst');
    expect(openFenceCode('```ts\na\n```')).toBeNull();
    expect(openFenceCode('```ts\na\n  ```')).toBeNull();
    expect(openFenceCode('para\n```ts\na')).toBeNull();
    expect(openFenceCode('```')).toBeNull();
  });

  it('a growing fence is extended without being lexed again, and still splits as from the start', () => {
    let prev = { text: '', blocks: [] as string[] };
    const doc = 'Intro.\n\n```ts\n' + 'const x = 1;\n'.repeat(50);
    for (let n = 1; n <= doc.length; n += 7) {
      const text = doc.slice(0, n);
      const blocks = extendBlocks(prev, text);
      expect(blocks).toEqual(splitBlocks(text));
      if (prev.blocks.length && openFenceCode(prev.blocks[prev.blocks.length - 1]) !== null) expect(blocks[0]).toBe(prev.blocks[0]);
      prev = { text, blocks };
    }
  });
});
