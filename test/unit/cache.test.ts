import { describe, expect, it, vi } from 'vitest';
import { LruBytes, loadOnce } from '../../web/src/lib/cache';

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

describe('LruBytes with an age', () => {
  it('an entry older than the ttl is gone when next asked for, and its size with it', () => {
    vi.useFakeTimers();
    try {
      const c = new LruBytes<string>(100, byLength, 1000);
      c.set('a', '123456789');
      vi.advanceTimersByTime(999);
      expect(c.get('a')).toBe('123456789');
      vi.advanceTimersByTime(2);
      expect(c.get('a')).toBeUndefined();
      expect(c.bytes).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('clear and delete give the budget back', () => {
    const c = new LruBytes<string>(100, byLength);
    c.set('a', '123');
    c.set('b', '123');
    c.delete('a');
    expect(c.bytes).toBe(4);
    c.clear();
    expect([c.bytes, c.size]).toEqual([0, 0]);
  });
});

describe('loadOnce', () => {
  it('shares one load per key, and forgets a load that failed so the next ask tries again', async () => {
    const c = new LruBytes<Promise<string>>(8, () => 1);
    let n = 0;
    const ok = () => Promise.resolve(`v${++n}`);
    expect(await Promise.all([loadOnce(c, 'k', ok), loadOnce(c, 'k', ok)])).toEqual(['v1', 'v1']);
    const bad = loadOnce(c, 'x', () => Promise.reject(new Error('no')));
    await expect(bad).rejects.toThrow('no');
    expect(await loadOnce(c, 'x', ok)).toBe('v2');
  });
});
