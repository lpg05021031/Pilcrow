import { describe, expect, it, vi } from 'vitest';
import { extendBlocks, growBlock, openFenceCode, renderMarkdown, splitBlocks, type Growth, type Split } from '../../web/src/lib/markdown';

// Sanitising needs a DOM, which these tests (lexing only) run without.
vi.mock('dompurify', () => ({ default: { addHook: () => {}, setConfig: () => {}, sanitize: (s: string) => s } }));

const DOCS = [
  '# Title\n\nA paragraph that runs\nonto a second line.\n\n- one\n- two\n\n  continued in two\n- three\n\n```ts\nconst a = 1;\n\nconst b = 2;\n```\n\nAfter the code.\n',
  'Setext heading\n===\n\nText then a table:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> a quote\n> on two lines\n\n1. first\n2. second\n\n    indented code\n\nend',
  'para\n```js\nfenced right after a paragraph\n```\n<div>\nhtml block\n</div>\n\n---\n\n* [ ] task\n* [x] done\n',
  'A fence that grows:\n\n```tsx\nconst a = `x`;\n\n``not a fence\n   ```tsx not closing either\n  ```\nafter the indented close\n```\n\nTail text.\n',
  // Lists, tables and paragraphs, which are lexed again from their last item, row or line as they grow.
  '- one `a`\n- two **b**\n  - nested\n  - more\n- three\n\n- now spaced apart\n- and on\n\nA paragraph after.\n\n1. first\n2. second\n10. tenth\n\n* other bullet\n- new list\n',
  'Header line\n| a | b |\n|---|---|\n| 1 | `x` |\n| 2 | **y** |\nlast row\n\n| c |\n|:-:|\n\nA paragraph that\nruns over lines\nand then\n---\nTitle\n====\n\n| h | i |\n|---|---|\n# heading after\n',
  'One long line of prose with `code`, **bold**, _em_, snake_case_names and a [link](https://example.com) that goes on, and on, and *on* past ~~this~~ that.\n  - not a list\n2. not a list either\n- but this is\n',
];

/** What Markdown renders while text streams in: the blocks of each longer prefix, built on the last. */
function stream(doc: string, step: number) {
  let prev: Split = { text: '', blocks: [] };
  for (let n = 1; ; n += step) {
    const text = doc.slice(0, Math.min(n, doc.length));
    prev = extendBlocks(prev, text);
    expect(prev.blocks, `at ${text.length} characters`).toEqual(splitBlocks(text));
    if (n >= doc.length) break;
  }
  return prev;
}

describe('extendBlocks', () => {
  it('splits a growing text exactly as splitting it from the start would, at every length', () => {
    for (const doc of DOCS) for (const step of [1, 3, 17]) stream(doc, step);
  });

  it('keeps the finished blocks as they were (the same strings) and lexes only from the last', () => {
    const a = { text: '# One\n\nTwo\n\nThr', blocks: splitBlocks('# One\n\nTwo\n\nThr') };
    const b = extendBlocks(a, a.text + 'ee and more').blocks;
    expect(b.slice(0, -1)).toEqual(a.blocks.slice(0, -1));
    expect(b.at(-1)).toBe('Three and more');
  });

  it('lexes a growing list, table or paragraph again from its last item, row or lines, not from its start', () => {
    const items = Array.from({ length: 300 }, (_, i) => `- item ${i}\n`).join('');
    const list = stream('Intro.\n\n' + items + '- item 300 is being', 40);
    expect(list.resume).toEqual({ type: 'list', from: items.length, head: '' });
    const rows = '| a | b |\n|---|---|\n' + Array.from({ length: 300 }, (_, i) => `| ${i} | x |\n`).join('');
    expect(stream(rows + '| 300 | y', 40).resume).toEqual({ type: 'table', from: rows.length, head: '| a | b |\n|---|---|\n' });
    const lines = Array.from({ length: 300 }, (_, i) => `line ${i}`).join('\n');
    expect(stream(lines, 40).resume?.from).toBe(lines.indexOf('line 297'));
  });

  it('starts over when the text was changed rather than added to', () => {
    const a = { text: 'one\n\ntwo', blocks: splitBlocks('one\n\ntwo') };
    expect(extendBlocks(a, 'uno\n\ntwo three').blocks).toEqual(splitBlocks('uno\n\ntwo three'));
    expect(extendBlocks(a, 'one').blocks).toEqual(['one']);
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
    let prev: Split = { text: '', blocks: [] };
    const doc = 'Intro.\n\n```ts\n' + 'const x = 1;\n'.repeat(50);
    for (let n = 1; n <= doc.length; n += 7) {
      const text = doc.slice(0, n);
      const next = extendBlocks(prev, text);
      expect(next.blocks).toEqual(splitBlocks(text));
      if (prev.blocks.length && openFenceCode(prev.blocks[prev.blocks.length - 1]) !== null) expect(next.blocks[0]).toBe(prev.blocks[0]);
      prev = next;
    }
  });
});

/** The markup inside a block's container as renderMarkdown draws it whole, spaces between tags aside. */
function whole(raw: string, kind: Growth['kind']) {
  const html = renderMarkdown(raw);
  const tag = kind === 'table' ? 'tbody' : kind === 'paragraph' ? 'p' : /^<ol/.test(html) ? 'ol' : 'ul';
  const a = html.indexOf('>', html.indexOf(`<${tag}`)) + 1;
  return flat(html.slice(a, html.lastIndexOf(`</${tag}>`)));
}
const flat = (s: string) => s.replace(/>\s+</g, '><').trim();

describe('growBlock', () => {
  // Each piece of a block being written is drawn as the block drawn whole would draw it: the pieces
  // made final so far, then the one still open, read the same as the whole block at every length.
  const GROWING: [Growth['kind'], string][] = [
    ['list', '- one `a` and [a link](https://x.y)\n- two **b**\n  - nested\n  - more\n- three\n- four with `web/src/lib/markdown.ts`\n'],
    ['list', '1. first\n2. second\n\n   spaced, so the list is loose from here\n3. third\n4. fourth\n'],
    ['list', '- a\n\n- spaced from the start\n- c\n'],
    ['table', '| # | Where | What |\n|---|:-:|--:|\n| 1 | `src/a.ts` | **bold** |\n| 2 | b | [link](https://x.y) |\n| 3 | c | d |\n'],
    ['paragraph', 'Prose with `code`, **bold**, _em_, snake_case_names, a [link](https://example.com/a_b) and a < b, on and *on* past ~~this~~ that, \\*escaped\\*, then more words to the end.'],
  ];
  it('draws the pieces of a growing list, table or paragraph as the whole block draws them, at every length', () => {
    for (const [kind, doc] of GROWING)
      for (const step of [1, 5, 23]) {
        let g: Growth | null = null;
        let done = '';
        let grew = 0;
        for (let n = step; n <= doc.length; n += step) {
          const raw = doc.slice(0, n);
          // What Markdown passes the open block: the kind the split says it is now (Block starts over on a change).
          if (g && g.kind !== extendBlocks({ text: '', blocks: [] }, raw).resume?.type) [g, done] = [null, ''];
          const out = growBlock(raw, g);
          if (!out) {
            [g, done] = [null, '']; // drawn whole, and grown again from the start
            continue;
          }
          if (out.g.kind === kind) grew++;
          done += out.settled;
          expect(flat(done + out.rest), `${kind} at ${n}: ${JSON.stringify(raw.slice(-30))}`).toBe(whole(raw, out.g.kind));
          g = out.g;
        }
        expect(grew, `${kind}, step ${step}`).toBeGreaterThan(doc.length / step / 2);
      }
  });

  it('leaves the pieces before the last one alone: what is lexed again is the last item, row or run of text', () => {
    const list = Array.from({ length: 200 }, (_, i) => `- item ${i}\n`).join('');
    const a = growBlock(list, null)!;
    expect(a.g.from).toBe(list.lastIndexOf('- item'));
    const b = growBlock(list + '- item 200 grows', a.g)!;
    expect(b.settled).toBe('<li>item 199</li>');
    expect(b.rest).toBe('<li>item 200 grows</li>');
    const words = 'word '.repeat(2000);
    const p = growBlock(words + 'and', null)!;
    expect(p.g.from).toBe(words.length - 1);
    const q = growBlock(words + 'and more', p.g)!;
    expect([q.settled, q.rest]).toEqual([' and', ' more']);
  });

  it('is null for a block of another kind, and for a list that becomes spaced apart', () => {
    expect(growBlock('# heading', null)).toBeNull();
    const a = growBlock('- a\n- b', null)!;
    expect(growBlock('- a\n- b\n\n- c', a.g)).toBeNull();
  });
});
