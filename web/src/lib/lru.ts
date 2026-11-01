/**
 * A cache with a size budget: reading an entry makes it the most recent, and once the entries
 * together pass the budget the least recently used ones go. Sizes are what `measure` says; the
 * caches here pass string lengths, about a byte a character (V8 keeps ASCII strings one byte each).
 */
export class LruBytes<V> {
  private map = new Map<string, { value: V; size: number }>();
  /** The sum of the sizes of what is held. */
  bytes = 0;

  constructor(
    private readonly budget: number,
    private readonly measure: (key: string, value: V) => number,
  ) {}

  get size() {
    return this.map.size;
  }

  get(key: string): V | undefined {
    const e = this.map.get(key);
    if (!e) return undefined;
    // A Map iterates in insertion order, so moving the entry to the end makes the first one the oldest.
    this.map.delete(key);
    this.map.set(key, e);
    return e.value;
  }

  set(key: string, value: V) {
    const old = this.map.get(key);
    if (old) {
      this.map.delete(key);
      this.bytes -= old.size;
    }
    const size = this.measure(key, value);
    if (size > this.budget) return; // would only push everything else out, and then itself
    this.map.set(key, { value, size });
    this.bytes += size;
    for (const [k, e] of this.map) {
      if (this.bytes <= this.budget) break;
      this.map.delete(k);
      this.bytes -= e.size;
    }
  }
}
