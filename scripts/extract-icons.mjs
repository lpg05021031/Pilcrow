#!/usr/bin/env node
// Writes the Lucide icons the interface uses as plain SVG files for the Qt client
// (qt/pilcrow_qt/assets/icons/<kebab-name>.svg). Lucide ships them as data inside lucide-react;
// the SVGs here use currentColor so the client can tint them.
//
//   node scripts/extract-icons.mjs
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'web', 'src');
const OUT = join(ROOT, 'qt', 'pilcrow_qt', 'assets', 'icons');
const ICONS = join(ROOT, 'node_modules', 'lucide-react', 'dist', 'esm', 'icons');

// Everything imported from lucide-react anywhere in web/src, plus a few the Qt client needs of its own.
const extra = ['check', 'minus', 'maximize-2', 'chevron-left', 'circle', 'dot', 'loader', 'square-check', 'square-x', 'arrow-left', 'file-code', 'folder-closed', 'hash', 'list', 'menu'];
const names = new Set(extra);
const walk = (d) => {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.tsx?$/.test(e.name)) {
      const src = readFileSync(p, 'utf8');
      for (const m of src.matchAll(/import \{([^}]+)\} from 'lucide-react'/g)) {
        for (let n of m[1].split(',')) {
          n = n.trim().replace(/^type /, '').replace(/ as .*$/, '');
          if (n && n !== 'LucideIcon') names.add(n.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/([A-Z])([A-Z][a-z])/g, '$1-$2').replace(/([a-zA-Z])(\d)/g, '$1-$2').toLowerCase());
        }
      }
    }
  }
};
walk(SRC);

mkdirSync(OUT, { recursive: true });
let n = 0;
const missing = [];
for (const name of [...names].sort()) {
  const file = join(ICONS, `${name}.mjs`);
  let src;
  try {
    src = readFileSync(file, 'utf8');
    // Renamed icons are re-exports of the new name (home → house); follow them.
    const alias = src.match(/export \{ default \} from '\.\/([\w-]+)\.mjs'/);
    if (alias) src = readFileSync(join(ICONS, `${alias[1]}.mjs`), 'utf8');
  } catch {
    missing.push(name);
    continue;
  }
  // `node: [ ... ]` — walk to the bracket that closes it (one-line and multi-line forms both occur).
  const start = src.indexOf('node: [');
  if (start < 0) {
    missing.push(name);
    continue;
  }
  let depth = 0;
  let end = start + 6;
  for (; end < src.length; end++) {
    if (src[end] === '[') depth++;
    else if (src[end] === ']' && --depth === 0) break;
  }
  const body = src.slice(start + 7, end);
  // Each node is ["tag", { attr: "value", key: "…" }]
  // Long paths are written one attribute per line: `[\n "path",\n { d: "…", key: "…" }\n ]`.
  const nodes = [...body.matchAll(/\[\s*"(\w+)"\s*,\s*\{([^}]*)\}\s*\]/g)].map(([, tag, attrs]) => {
    const a = [...attrs.matchAll(/(\w+):\s*"([^"]*)"/g)].filter(([, k]) => k !== 'key').map(([, k, v]) => `${k}="${v}"`);
    return `<${tag} ${a.join(' ')}/>`;
  });
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${nodes.join('')}</svg>\n`;
  writeFileSync(join(OUT, `${name}.svg`), svg);
  n++;
}
console.log(`${n} icons written to ${OUT}${missing.length ? `; missing: ${missing.join(', ')}` : ''}`);
