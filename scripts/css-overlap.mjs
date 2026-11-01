#!/usr/bin/env node
// The rules of web/src/styles/README.md that a script can hold the stylesheets to:
//   - selectors that two stylesheets both declare with a property in common. With equal specificity
//     the file imported later (web/src/main.tsx) wins on order alone, and the earlier rule is dead
//     weight that reads as if it rendered. Printed as dead → winning.
//   - motion outside motion.css: an `animation…` or `transition…` declaration, or a @keyframes;
//   - a @keyframes that no animation in the stylesheets or the components names.
// Exits 1 if there are any.
//   node scripts/css-overlap.mjs
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const dir = 'web/src/styles';
const order = [...readFileSync('web/src/main.tsx', 'utf8').matchAll(/import '\.\/styles\/([\w-]+\.css)'/g)].map((m) => m[1]);
// The same property, or a shorthand and one of its longhands (transition and transition-duration).
const SHORT = /^(transition|animation|background|margin|padding|inset|outline|font|grid|flex|border-radius)$/;
const clash = (p, q) => p === q || (SHORT.test(p) && q.startsWith(p + '-')) || (SHORT.test(q) && p.startsWith(q + '-'));

const rules = new Map(); // "context selector" -> [{ file, line, props }]
const frames = new Map(); // @keyframes name -> "file:line", the later of two silently replaces the earlier
const MOTION = /^(animation|transition)(-|$)/;
const named = new Set(); // what the stylesheets' animations name
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
        if (file !== 'motion.css') {
          n++;
          console.log(`@keyframes ${kf[1]}  ${file}:${line}: motion lives in motion.css`);
        }
        frames.set(kf[1], `${file}:${line}`);
      }
      stack.push(stack.includes(null) || /^@(keyframes|font-face|property)/.test(head) ? null : { head, line });
      buf = '';
    } else if (ch === '}') {
      const top = stack.pop();
      if (top && !top.head.startsWith('@')) {
        const decls = [...buf.matchAll(/(?:^|;)\s*([-\w]+)\s*:([^;]*)/g)].map((m) => [m[1].replace(/^-webkit-/, ''), m[2]]);
        const props = decls.map(([p]) => p);
        for (const [p, v] of decls) {
          if (/^animation(-name)?$/.test(p)) for (const w of v.match(/[-\w]+/g) || []) named.add(w);
          if (MOTION.test(p) && file !== 'motion.css') {
            n++;
            console.log(`${top.head.replace(/\s+/g, ' ')}  ${file}:${top.line}: ${p} outside motion.css`);
          }
        }
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
// Keyframes the components play themselves (an inline animation, a class added from script) count as named.
const code = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? code(join(d, f)) : /\.tsx?$/.test(f) ? [readFileSync(join(d, f), 'utf8')] : []));
const scripts = code('web/src').join('\n');
for (const [name, at] of frames)
  if (!named.has(name) && !new RegExp(`\\b${name}\\b`).test(scripts)) {
    n++;
    console.log(`@keyframes ${name}  ${at}: nothing plays it`);
  }
console.log(`${n} problem${n === 1 ? '' : 's'}`);
process.exit(n ? 1 : 0);
