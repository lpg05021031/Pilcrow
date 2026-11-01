#!/usr/bin/env node
// Selectors that two stylesheets both declare with a property in common. With equal specificity the
// file imported later (web/src/main.tsx) wins on order alone, and the earlier rule is dead weight
// that reads as if it rendered. Prints each pair as dead → winning; exits 1 if there are any.
//   node scripts/css-overlap.mjs
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = 'web/src/styles';
const order = [...readFileSync('web/src/main.tsx', 'utf8').matchAll(/import '\.\/styles\/([\w-]+\.css)'/g)].map((m) => m[1]);
// The same property, or a shorthand and one of its longhands (transition and transition-duration).
const SHORT = /^(transition|animation|background|margin|padding|inset|outline|font|grid|flex|border-radius)$/;
const clash = (p, q) => p === q || (SHORT.test(p) && q.startsWith(p + '-')) || (SHORT.test(q) && p.startsWith(q + '-'));

const rules = new Map(); // "context selector" -> [{ file, line, props }]
const frames = new Map(); // @keyframes name -> "file:line", the later of two silently replaces the earlier
let n = 0;
for (const file of order) {
  const src = readFileSync(join(dir, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
  const stack = []; // open blocks: an at-rule prelude, a selector list, or null inside @keyframes and the like
  let buf = '';
  let line = 1;
  for (const ch of src) {
    if (ch === '\n') line++;
    if (ch === '{') {
      const head = buf.trim();
      const kf = head.match(/^@keyframes\s+([-\w]+)/);
      if (kf) {
        if (frames.has(kf[1])) {
          n++;
          console.log(`@keyframes ${kf[1]}  ${frames.get(kf[1])} → ${file}:${line}`);
        }
        frames.set(kf[1], `${file}:${line}`);
      }
      stack.push(stack.includes(null) || /^@(keyframes|font-face|property)/.test(head) ? null : { head, line });
      buf = '';
    } else if (ch === '}') {
      const top = stack.pop();
      if (top && !top.head.startsWith('@')) {
        const props = [...buf.matchAll(/(?:^|;)\s*([-\w]+)\s*:/g)].map((m) => m[1].replace(/^-webkit-/, ''));
        const ctx = stack.filter((b) => b?.head.startsWith('@')).map((b) => b.head).join(' ');
        for (const sel of top.head.split(/,(?![^()]*\))/).map((s) => s.trim().replace(/\s+/g, ' '))) {
          const k = `${ctx ? ctx + ' ' : ''}${sel}`;
          rules.set(k, [...(rules.get(k) || []), { file, line: top.line, props }]);
        }
      }
      buf = '';
    } else buf += ch;
  }
}
for (const [sel, list] of rules)
  for (let a = 0; a < list.length; a++)
    for (let b = a + 1; b < list.length; b++) {
      if (list[a].file === list[b].file) continue;
      const common = list[a].props.filter((p) => list[b].props.some((q) => clash(p, q)));
      if (!common.length) continue;
      n++;
      console.log(`${sel}  ${list[a].file}:${list[a].line} → ${list[b].file}:${list[b].line}  (${[...new Set(common)].join(', ')})`);
    }
console.log(`${n} overlap${n === 1 ? '' : 's'}`);
process.exit(n ? 1 : 0);
