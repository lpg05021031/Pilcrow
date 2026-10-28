// Small fuzzy matcher tuned for file paths and command names.

export interface Match<T> {
  item: T;
  score: number;
  hits: number[];
}

/** Score `text` against `query`; null when not all query chars appear in order. */
export function fuzzyScore(query: string, text: string): { score: number; hits: number[] } | null {
  if (!query) return { score: 0, hits: [] };
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  // Fast path: contiguous substring scores highest, especially in the basename.
  const sub = t.indexOf(q);
  const base = t.lastIndexOf('/') + 1;
  if (sub >= 0) {
    const hits = Array.from({ length: q.length }, (_, i) => sub + i);
    let score = 1000 - sub - t.length * 0.5;
    if (sub >= base) score += 400;
    if (sub === base) score += 300;
    return { score, hits };
  }
  const hits: number[] = [];
  let ti = 0;
  let score = 0;
  let prev = -2;
  for (let qi = 0; qi < q.length; qi++) {
    const ch = q[qi];
    let found = -1;
    while (ti < t.length) {
      if (t[ti] === ch) {
        found = ti;
        ti++;
        break;
      }
      ti++;
    }
    if (found < 0) return null;
    hits.push(found);
    if (found === prev + 1) score += 15;
    if (found === 0 || '/._-'.includes(t[found - 1])) score += 25;
    if (found >= base) score += 8;
    prev = found;
  }
  score -= t.length * 0.4 + (hits[0] || 0) * 0.2;
  return { score, hits };
}

export function fuzzyFilter<T>(query: string, items: T[], key: (t: T) => string, limit = 50): Match<T>[] {
  const out: Match<T>[] = [];
  for (const item of items) {
    const r = fuzzyScore(query, key(item));
    if (r) out.push({ item, score: r.score, hits: r.hits });
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, limit);
}
