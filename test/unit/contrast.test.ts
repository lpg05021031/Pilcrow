import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// The contrast tokens.css promises, checked against its own values: the secondary and tertiary text
// on every opaque surface, in both themes, and white on the solid fills that carry words.
const css = readFileSync(new URL('../../web/src/styles/tokens.css', import.meta.url), 'utf8');

function theme(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf('\n}', start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}
const dark = theme(":root,\n[data-theme='dark']");
const light = theme("[data-theme='light']");

function hex(v: string, t: Record<string, string>): string {
  const ref = v.match(/^var\(--([\w-]+)\)$/);
  if (ref) return hex(t[ref[1]], t);
  if (!/^#[0-9a-f]{6}$/i.test(v)) throw new Error(`not an opaque colour: ${v}`);
  return v;
}
function luminance(h: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const ratio = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

const SURFACES = ['canvas', 'chrome', 'raised', 'inset', 'card-ask', 'card'];
const SOLIDS = ['accent-solid', 'accent-solid-2', 'err-solid'];

describe('tokens.css contrast', () => {
  for (const [name, t] of [
    ['dark', dark],
    ['light', light],
  ] as const) {
    it(`${name}: --text-2 and --text-3 are 4.5:1 or more on every surface`, () => {
      for (const s of SURFACES) for (const ink of ['text-2', 'text-3']) expect(ratio(hex(t[ink], t), hex(t[s], t)), `${ink} on ${s}`).toBeGreaterThanOrEqual(4.5);
    });
    it(`${name}: words on a solid fill (white) are 4.5:1 or more`, () => {
      for (const s of SOLIDS) expect(ratio(hex(t['accent-ink'], t), hex(t[s], t)), `accent-ink on ${s}`).toBeGreaterThanOrEqual(4.5);
    });
  }
});
