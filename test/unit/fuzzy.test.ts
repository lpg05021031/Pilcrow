import { describe, expect, it } from 'vitest';
import { fuzzyFilter, fuzzyScore } from '../../web/src/lib/fuzzy';

describe('fuzzyScore', () => {
  it('empty query matches everything with no hits', () => {
    expect(fuzzyScore('', 'anything')).toEqual({ score: 0, hits: [] });
  });
  it('null when the characters are not all there in order', () => {
    expect(fuzzyScore('xyz', 'abc')).toBeNull();
    expect(fuzzyScore('ba', 'ab')).toBeNull();
  });
  it('a substring in the basename beats one in the folder', () => {
    const inBase = fuzzyScore('store', 'web/src/lib/store.ts')!;
    const inDir = fuzzyScore('store', 'store/src/lib/x.ts')!;
    expect(inBase.score).toBeGreaterThan(inDir.score);
    expect(inBase.hits).toEqual([12, 13, 14, 15, 16]);
  });
  it('scattered matches score below a contiguous one', () => {
    const contiguous = fuzzyScore('sess', 'sessions.ts')!;
    const scattered = fuzzyScore('sess', 'src/e/s/sx.ts')!;
    expect(contiguous.score).toBeGreaterThan(scattered.score);
  });
});

describe('fuzzyFilter', () => {
  it('sorts by score and respects the limit', () => {
    const items = ['web/src/lib/store.ts', 'server/store.js', 'README.md', 'scripts/release.mjs'];
    const r = fuzzyFilter('store', items, (s) => s, 2);
    expect(r.map((m) => m.item)).toEqual(['server/store.js', 'web/src/lib/store.ts']);
  });
  it('leaves out items that do not match', () => {
    expect(fuzzyFilter('zzz', ['a', 'b'], (s) => s)).toEqual([]);
  });
});
