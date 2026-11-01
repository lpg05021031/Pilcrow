import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { cachedHtml, CHUNK_OPEN, highlightHtml, highlightTokens, LANGS, MAX_HIGHLIGHT, PAINT_LINES } from '../../web/src/lib/highlight';

// Real TSX with JSX, template literals and comments spanning lines: the grammar's state matters.
const code = readFileSync(new URL('../../web/src/components/Markdown.tsx', import.meta.url), 'utf8').trimEnd();

describe('highlighting in slices', () => {
  it('carries the grammar from line to line: a comment or template spanning lines stays one', async () => {
    const lines = (await highlightTokens(['/* a comment', '   const inside = 1;', '*/', 'const outside = `one', 'const still = 1;`;'].join('\n'), 'ts'))!;
    const style = (line: number, text: string) => JSON.stringify(lines[line].find((t) => t.t.includes(text))?.s);
    expect(style(1, 'const')).toBe(style(0, 'comment')); // still inside the comment
    expect(style(3, 'const')).not.toBe(style(1, 'const')); // a keyword again after it
    expect(style(4, 'const')).toBe(style(3, 'one')); // inside the template string
  });

  it('colours a whole real block, every line, and the tokens add up to the code', async () => {
    const lines = (await highlightTokens(code, 'tsx'))!;
    expect(lines.map((line) => line.map((t) => t.t).join(''))).toEqual(code.split('\n'));
    expect(lines.flat().filter((t) => t.s).length).toBeGreaterThan(code.length / 20);
  }, 30_000);

  it('the markup reads back as the code (Markdown swaps it in only then); a long block comes in blocks of lines', async () => {
    const html = (await highlightHtml(code, 'tsx'))!;
    const text = html.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    expect(text).toBe(code);
    expect(html.split('<span class="line">')).toHaveLength(code.split('\n').length + 1);
    expect(html.split(CHUNK_OPEN)).toHaveLength(Math.ceil(code.split('\n').length / PAINT_LINES) + 1);
    // Painted a block at a time (Markdown), never drawn whole from the cache in one frame.
    expect(cachedHtml(code, 'tsx')).toBeNull();
    const short = 'const a = 1;\nexport default a;';
    const shortHtml = await highlightHtml(short, 'ts');
    expect(shortHtml).not.toContain(CHUNK_OPEN);
    expect(cachedHtml(short, 'ts')).toBe(shortHtml);
  }, 30_000);

  it('blocks coloured together share the slices, and one no longer wanted stops at the next line', async () => {
    const big = code + '\n// not coloured before';
    const n = big.split('\n').length;
    const order: string[] = [];
    let lines = 0;
    const [a, b, gone] = await Promise.all([
      highlightTokens(big, 'tsx').then((t) => (order.push('big'), t)),
      highlightTokens('const small = 1;', 'tsx').then((t) => (order.push('small'), t)),
      highlightTokens(big + '\n// another', 'tsx', () => ++lines < 5),
    ]);
    expect(a).toHaveLength(n);
    expect(b).toHaveLength(1);
    expect(order).toEqual(['small', 'big']); // the small one is not kept waiting behind the big one
    expect(gone).toBeNull();
    expect(lines).toBeLessThan(10);
    expect(await highlightTokens(big + '\n// another', 'tsx')).toHaveLength(n + 1); // a dropped block is not cached
  }, 30_000);

  it('every language listed is coloured: the raw engine runs only grammars that come precompiled', async () => {
    const plain: string[] = [];
    for (const lang of Object.keys(LANGS)) if (!(await highlightTokens('x = "a" // b', lang))) plain.push(lang);
    expect(plain).toEqual([]);
  }, 30_000);

  it('past the cap, a block stays plain', async () => {
    expect(await highlightHtml('x'.repeat(MAX_HIGHLIGHT + 1), 'ts')).toBeNull();
  });
});
