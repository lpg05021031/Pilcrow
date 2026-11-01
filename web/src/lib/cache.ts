import type { FileTouch } from './types';

/** One entry of a folder listing (fs.list). */
export interface DirEntry {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size: number;
  heavy?: boolean;
}

/**
 * The window's caches. Every cache keeps to a budget, and one about a folder also to an age, so
 * nothing grows for as long as the window stays open (store.ts says where other state lives).
 *
 * LruBytes: reading an entry makes it the most recent, and once the entries together pass the
 * budget the least recently used ones go. Sizes are what `measure` says: the caches of text pass
 * string lengths, about a byte a character (V8 keeps ASCII strings one byte each); the ones of
 * promises, whose size is not known when they are kept, count entries. An entry older than `ttl`
 * milliseconds is gone when next asked for.
 */
export class LruBytes<V> {
  private map = new Map<string, { value: V; size: number; at: number }>();
  /** The sum of the sizes of what is held. */
  bytes = 0;

  constructor(
    private readonly budget: number,
    private readonly measure: (key: string, value: V) => number,
    private readonly ttl = Infinity,
  ) {}

  get size() {
    return this.map.size;
  }

  get(key: string): V | undefined {
    const e = this.map.get(key);
    if (!e) return undefined;
    this.map.delete(key);
    if (Date.now() - e.at > this.ttl) {
      this.bytes -= e.size;
      return undefined;
    }
    // A Map iterates in insertion order, so moving the entry to the end makes the first one the oldest.
    this.map.set(key, e);
    return e.value;
  }

  set(key: string, value: V) {
    this.delete(key);
    const size = this.measure(key, value);
    if (size > this.budget) return; // would only push everything else out, and then itself
    this.map.set(key, { value, size, at: Date.now() });
    this.bytes += size;
    for (const [k, e] of this.map) {
      if (this.bytes <= this.budget) break;
      this.map.delete(k);
      this.bytes -= e.size;
    }
  }

  delete(key: string) {
    const e = this.map.get(key);
    if (!e) return;
    this.map.delete(key);
    this.bytes -= e.size;
  }

  clear() {
    this.map.clear();
    this.bytes = 0;
  }
}

/** One load per key while it is fresh, shared by everyone who asks; a load that fails is not kept, so the next ask tries again. */
export function loadOnce<V>(cache: LruBytes<Promise<V>>, key: string, load: () => Promise<V>): Promise<V> {
  let p = cache.get(key);
  if (!p) {
    p = load();
    cache.set(key, p);
    p.catch(() => cache.get(key) === p && cache.delete(key));
  }
  return p;
}

const one = () => 1;

/** What is known about folders, by path. */
export const folders = {
  /** A folder's files for @ mentions (fs.index: the composer and the palette). */
  index: new LruBytes<Promise<string[]>>(8, one, 20_000),
  /** Which sessions changed which files in a folder (project.fileHistory: FileTouches). */
  history: new LruBytes<Promise<Record<string, FileTouch[]>>>(16, one, 15_000),
  /** The entries of each folder the Files tree has opened (fs.list), until Refresh. About 80 bytes an entry. */
  dirs: new LruBytes<DirEntry[]>(2 << 20, (k, v) => k.length + v.length * 80),
};
