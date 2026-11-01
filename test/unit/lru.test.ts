import { describe, expect, it } from 'vitest';
import { LruBytes } from '../../web/src/lib/lru';

const byLength = (k: string, v: string) => k.length + v.length;

describe('LruBytes', () => {
  it('keeps what fits and counts its size', () => {
    const c = new LruBytes<string>(100, byLength);
    c.set('a', 'x'.repeat(9));
    c.set('b', 'y'.repeat(19));
    expect(c.get('a')).toBe('x'.repeat(9));
    expect(c.bytes).toBe(30);
    expect(c.size).toBe(2);
  });

  it('lets the least recently used go once the budget is passed, reads counting as use', () => {
    const c = new LruBytes<string>(30, byLength);
    c.set('a', '123456789'); // 10
    c.set('b', '123456789'); // 20
    c.set('c', '123456789'); // 30
    c.get('a'); // a is now the most recent; b is the oldest
    c.set('d', '123456789'); // 40: b goes
    expect(c.get('b')).toBeUndefined();
    expect(['a', 'c', 'd'].map((k) => c.get(k) !== undefined)).toEqual([true, true, true]);
    expect(c.bytes).toBe(30);
  });

  it('replacing an entry swaps its size instead of adding to it', () => {
    const c = new LruBytes<string>(100, byLength);
    c.set('a', 'x'.repeat(49));
    c.set('a', 'x'.repeat(9));
    expect(c.bytes).toBe(10);
    expect(c.size).toBe(1);
  });

  it('an entry larger than the whole budget is not kept and pushes nothing out', () => {
    const c = new LruBytes<string>(20, byLength);
    c.set('a', '123456789');
    c.set('huge', 'x'.repeat(100));
    expect(c.get('huge')).toBeUndefined();
    expect(c.get('a')).toBe('123456789');
    expect(c.bytes).toBe(10);
  });

  it('several old entries go to make room for one large new one', () => {
    const c = new LruBytes<string>(40, byLength);
    for (const k of ['a', 'b', 'c', 'd']) c.set(k, '123456789');
    c.set('e', 'x'.repeat(24)); // 25: a, b and c have to go
    expect(['a', 'b', 'c', 'd', 'e'].map((k) => c.get(k) !== undefined)).toEqual([false, false, false, true, true]);
    expect(c.bytes).toBe(35);
  });
});
