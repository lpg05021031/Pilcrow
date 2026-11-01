import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// The contrast tokens.css promises, checked against its own values: the secondary and tertiary text
// on every opaque surface, in both themes, white on the solid fills that carry words, and the text on
// the rows lit by the keyboard or the selection, where a translucent fill sits on a surface.
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
/** A stack of tokens from the bottom up (an opaque surface, then translucent layers) as the opaque colour it shows. */
function stack(names: string[], t: Record<string, string>): string {
  let rgb = [1, 3, 5].map((i) => parseInt(hex(t[names[0]], t).slice(i, i + 2), 16));
  for (const n of names.slice(1)) {
    const m = t[n].match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
    if (!m) throw new Error(`not a translucent colour: --${n}: ${t[n]}`);
    const a = Number(m[4]);
    rgb = rgb.map((c, i) => Math.round(Number(m[i + 1]) * a + c * (1 - a)));
  }
  return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('');
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
// Rows lit by the keyboard or the selection, from the surface up, and the text on them: menus and the
// / and @ list are glass over the conversation (menus.css, composer.css), the palette a sheet over it
// (dialogs.css), the sidebar's rows sit on the chrome (sidebar.css), the chosen mode in a dialog is a row
// of a sheet (dialogs.css) and the chosen answer one of a question's card (transcript.css); the timeline's
// steps sit on the side panel's canvas and a file's history is a popover (panels.css), the control room's
// finished sessions a card (overview.css), the folder picker a popover (session.css). Each lifts its
// secondary text to --text on the lit or hovered row, as --text-3 (and in the dark theme --text-2) falls
// short of 4.5:1 there.
const LIT: [what: string, layers: string[]][] = [
  ['a menu row (.menu-item.active: label, hint, keys)', ['canvas', 'glass', 'fill-2']],
  ['a / or @ row (.suggest-item.active: label, hint)', ['canvas', 'glass', 'fill-2']],
  ['a palette row (.pal-item.active)', ['canvas', 'glass-2', 'fill-2']],
  ['the open session in the sidebar (.side-row.active: title, folder, time)', ['chrome', 'fill-2']],
  ['the chosen mode in a dialog (.radio-row.on: label, hint)', ['canvas', 'glass-2', 'fill-2']],
  ["the chosen answer on a question's card (.ask-opt.on: label, description)", ['card-ask', 'fill-2']],
  ['the picked or hovered step in the timeline (.tl-row.on, :hover: kind and time)', ['canvas', 'fill-1']],
  ['a finished session in the control room, hovered or focused (.ovr: folder or reply, time)', ['card', 'fill-1']],
  ["a session in a file's history, hovered (.touches-row: what it was about, when)", ['canvas', 'glass', 'fill-1']],
  ['a recent folder in the folder picker, hovered (.fpick-row: path)', ['canvas', 'glass', 'fill-2']],
];
// A sidebar row under the pointer (or with its menu open) sets its folder and time in --text-2.
const HOVER: [what: string, layers: string[]][] = [['a sidebar row under the pointer (.side-row:hover)', ['chrome', 'fill-1']]];
const SOLIDS = ['accent-solid', 'accent-solid-2', 'err-solid'];

describe('tokens.css contrast', () => {
  for (const [name, t] of [
    ['dark', dark],
    ['light', light],
  ] as const) {
    it(`${name}: --text-2 and --text-3 are 4.5:1 or more on every surface`, () => {
      for (const s of SURFACES) for (const ink of ['text-2', 'text-3']) expect(ratio(hex(t[ink], t), hex(t[s], t)), `${ink} on ${s}`).toBeGreaterThanOrEqual(4.5);
    });
    it(`${name}: the text on a lit row is 4.5:1 or more, where --text-3 is not`, () => {
      for (const [what, layers] of LIT) {
        const bg = stack(layers, t);
        expect(ratio(hex(t.text, t), bg), `--text on ${what}`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(hex(t['text-3'], t), bg), `--text-3 on ${what}`).toBeLessThan(4.5);
      }
    });
    it(`${name}: --text-2 on a row under the pointer is 4.5:1 or more`, () => {
      for (const [what, layers] of HOVER) expect(ratio(hex(t['text-2'], t), stack(layers, t)), `--text-2 on ${what}`).toBeGreaterThanOrEqual(4.5);
    });
    it(`${name}: words on a solid fill (white) are 4.5:1 or more`, () => {
      for (const s of SOLIDS) expect(ratio(hex(t['accent-ink'], t), hex(t[s], t)), `accent-ink on ${s}`).toBeGreaterThanOrEqual(4.5);
    });
  }
});
