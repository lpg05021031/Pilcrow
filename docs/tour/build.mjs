// Builds the interactive tour: one self-contained HTML file that replays captured frames of the real
// interface under the real stylesheet, with the tour layer (src/) on top.
//   node docs/tour/build.mjs [captureDir] [outFile]     default: test/out/tour, docs/tour/pilcrow-tour.html
//   --artifact <file>   also write the page without its document shell (for hosts that add their own)
// Needs a built interface (npm run build) and a capture (node docs/tour/capture.mjs).
import { readFileSync as rd, writeFileSync as wr, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('./', import.meta.url));
const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const args = process.argv.slice(2);
const ai = args.indexOf('--artifact');
const artifact = ai >= 0 ? args.splice(ai, 2)[1] : null;
const CAP = args[0] || join(ROOT, 'test', 'out', 'tour');
const OUT = args[1] || join(HERE, 'pilcrow-tour.html');
const DIST = join(ROOT, 'web', 'dist', 'assets');
const t = (p) => rd(p, 'utf8');

// The interface's own stylesheet, with the two fonts the tour shows inlined and the rest dropped.
const FONTS = ['inter-latin-opsz-normal-', 'jetbrains-mono-latin-wght-normal-'];
const sheet = readdirSync(DIST).find((f) => f.startsWith('index-') && f.endsWith('.css'));
if (!sheet) throw new Error('no built stylesheet in web/dist/assets: run npm run build first');
let inlined = 0, dropped = 0;
let css = t(join(DIST, sheet)).replace(/@font-face\{[^}]*\}/g, (rule) => {
  const m = rule.match(/url\(\.\/([^)]+)\)/);
  if (!m) return rule;
  if (!FONTS.some((p) => m[1].startsWith(p))) { dropped++; return ''; }
  inlined++;
  return rule.replace(m[0], 'url(data:font/woff2;base64,' + rd(join(DIST, m[1])).toString('base64') + ')');
});
if (/url\(\.\//.test(css)) throw new Error('the stylesheet still points at a file: ' + css.match(/url\(\.\/[^)]*\)/)[0]);

// Captured frames. A few are steps on the way that the tour never shows; the Preview tab's iframe cannot
// travel in a single file, so its page is redrawn as static markup.
const SKIP = ['f94-memory0', 'f95-note', 'f01-empty'];
const PREVIEW = '<div class="tx-preview"><header><i></i>Acme Console</header><main><form><h1>Sign in</h1><p>You will return to <b>/reports/weekly</b> after signing in.</p><label>Email</label><span>tester@example.com</span><label>Password</label><span>••••••••</span><b class="go">Sign in</b><small>next=/reports/weekly</small></form></main></div>';
const frames = {}, overs = {};
JSON.parse(t(join(CAP, 'frames.json'))).forEach((f) => {
  if (SKIP.includes(f.name)) return;
  const a = f.html.indexOf('<iframe'), b = f.html.indexOf('</iframe>');
  frames[f.name] = a >= 0 && b > a ? f.html.slice(0, a) + PREVIEW + f.html.slice(b + 9) : f.html;
  if (f.over) overs[f.name] = f.over;
});
const data = { frames, overs, icons: JSON.parse(t(join(CAP, 'icons.json'))), parts: JSON.parse(t(join(CAP, 'parts.json'))), ids: JSON.parse(t(join(CAP, 'ids.json'))) };
const json = JSON.stringify(data).split('<').join(String.fromCharCode(92) + 'u003c');

const SRC = ['0-stops', 'a-core', 'b-view', 'c-engine', 'c-move', 'd-cursor', 'd-play', 'e-ui', 'e-cards', 'f-input'];
const js = SRC.map((n) => t(join(HERE, 'src', n + '.js'))).join('\n');
const tour = ['tour-a', 'tour-b', 'tour-c'].map((n) => t(join(HERE, 'src', n + '.css'))).join('');
const NL = String.fromCharCode(10);
const body = '<title>Pilcrow 互動導覽</title>' + NL + '<meta name="claude:viewport-fit-sides" content="cover">' + NL +
  '<style>' + css + '</style>' + NL + '<style>' + t(join(CAP, 'extra.css')) + '</style>' + NL + '<style>' + tour + '</style>' + NL +
  t(join(HERE, 'src', 'body.html')) + NL + '<script type="application/json" id="tx-data">' + json + '</script>' + NL +
  '<script>(() => {' + NL + js + NL + '})();</script>' + NL;
wr(OUT, '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>' + body + '</body></html>');
if (artifact) wr(artifact, body);
console.log('fonts inlined', inlined, 'dropped', dropped, '| frames', Object.keys(frames).length, '| overlays', Object.keys(overs).length, '| bytes', Buffer.byteLength(body));
