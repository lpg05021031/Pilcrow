// Syntax highlighting with Shiki (JavaScript regex engine, no WASM), lazy-loaded: the highlighter
// with the first code block, each language with its first block. Two hand-tuned themes are emitted
// as CSS variables so dark/light switch without re-highlighting.
import type { GrammarState, HighlighterCore, ThemeRegistration } from 'shiki/core';
import { LruBytes } from './cache';
import { yieldToEvents } from './motion';

const LANGS: Record<string, () => Promise<unknown>> = {
  javascript: () => import('shiki/langs/javascript.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  jsonc: () => import('shiki/langs/jsonc.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  bash: () => import('shiki/langs/bash.mjs'),
  fish: () => import('shiki/langs/fish.mjs'),
  powershell: () => import('shiki/langs/powershell.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  kotlin: () => import('shiki/langs/kotlin.mjs'),
  swift: () => import('shiki/langs/swift.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  // Listed so the bundler gives the C++ macro grammar a chunk of its own: folded into C++, which is
  // the only grammar using it, the two make one 640 kB chunk. They still load together.
  'cpp-macro': () => import('shiki/langs/cpp-macro.mjs'),
  csharp: () => import('shiki/langs/csharp.mjs'),
  ruby: () => import('shiki/langs/ruby.mjs'),
  php: () => import('shiki/langs/php.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  vue: () => import('shiki/langs/vue.mjs'),
  svelte: () => import('shiki/langs/svelte.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  scss: () => import('shiki/langs/scss.mjs'),
  less: () => import('shiki/langs/less.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
  toml: () => import('shiki/langs/toml.mjs'),
  ini: () => import('shiki/langs/ini.mjs'),
  xml: () => import('shiki/langs/xml.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
  dockerfile: () => import('shiki/langs/dockerfile.mjs'),
  make: () => import('shiki/langs/make.mjs'),
  cmake: () => import('shiki/langs/cmake.mjs'),
  verilog: () => import('shiki/langs/verilog.mjs'),
  'system-verilog': () => import('shiki/langs/system-verilog.mjs'),
  vhdl: () => import('shiki/langs/vhdl.mjs'),
  tcl: () => import('shiki/langs/tcl.mjs'),
  perl: () => import('shiki/langs/perl.mjs'),
  lua: () => import('shiki/langs/lua.mjs'),
  r: () => import('shiki/langs/r.mjs'),
  matlab: () => import('shiki/langs/matlab.mjs'),
  scala: () => import('shiki/langs/scala.mjs'),
  dart: () => import('shiki/langs/dart.mjs'),
  elixir: () => import('shiki/langs/elixir.mjs'),
  haskell: () => import('shiki/langs/haskell.mjs'),
  graphql: () => import('shiki/langs/graphql.mjs'),
  proto: () => import('shiki/langs/proto.mjs'),
  hcl: () => import('shiki/langs/hcl.mjs'),
  nix: () => import('shiki/langs/nix.mjs'),
  zig: () => import('shiki/langs/zig.mjs'),
  julia: () => import('shiki/langs/julia.mjs'),
  latex: () => import('shiki/langs/latex.mjs'),
  'fortran-free-form': () => import('shiki/langs/fortran-free-form.mjs'),
  asm: () => import('shiki/langs/asm.mjs'),
  csv: () => import('shiki/langs/csv.mjs'),
  log: () => import('shiki/langs/log.mjs'),
};

const ALIASES: Record<string, string> = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript', py: 'python', python3: 'python', sh: 'bash', shell: 'bash', zsh: 'bash',
  shellscript: 'bash', console: 'bash', terminal: 'bash', yml: 'yaml', md: 'markdown', rs: 'rust', 'c++': 'cpp', cc: 'cpp', hpp: 'cpp', h: 'c', cs: 'csharp',
  rb: 'ruby', kt: 'kotlin', ps1: 'powershell', pwsh: 'powershell', sv: 'system-verilog', systemverilog: 'system-verilog', v: 'verilog', vhd: 'vhdl',
  makefile: 'make', docker: 'dockerfile', golang: 'go', patch: 'diff', htm: 'html', svg: 'xml', tf: 'hcl', terraform: 'hcl', tex: 'latex', pl: 'perl',
  jsonl: 'json', json5: 'jsonc', fortran: 'fortran-free-form', f90: 'fortran-free-form', sdc: 'tcl', xdc: 'tcl', protobuf: 'proto', gql: 'graphql',
};

export function normalizeLang(lang?: string | null): string | null {
  if (!lang) return null;
  const l = lang.toLowerCase().trim().split(/[\s{]/)[0];
  const n = ALIASES[l] || l;
  return LANGS[n] ? n : null;
}

/**
 * The two themes, as one table: the scopes, then the colour in the dark theme and in the light one,
 * then a font style. Shiki is given a single theme whose colours stand for rows of this table, so it
 * tokenizes once instead of once per theme (half the time), and each token gets both colours.
 */
const RULES: [scopes: string[], dark: string | null, light: string | null, fontStyle?: string][] = [
  [['comment', 'punctuation.definition.comment', 'string.comment'], '#6C6C75', '#8A8A93', 'italic'],
  [['keyword', 'storage', 'storage.type', 'keyword.control', 'keyword.operator.new', 'keyword.operator.expression', 'variable.language', 'keyword.other'], '#C3A6EC', '#7B3BC0'],
  [['string', 'string.quoted', 'string.template', 'markup.inline.raw'], '#A2CF98', '#2D7A33'],
  [['constant.numeric', 'constant.language', 'constant.character', 'support.constant', 'constant.other', 'keyword.other.unit'], '#E9B07C', '#B2560E'],
  [['entity.name.function', 'support.function', 'meta.function-call', 'variable.function'], '#8FB8F2', '#1D5FC0'],
  [['entity.name.type', 'entity.name.class', 'support.type', 'support.class', 'entity.other.inherited-class', 'storage.type.primitive', 'entity.name.namespace'], '#84CFC3', '#0B786D'],
  [['variable.parameter'], '#E5C391', '#8A5A00'],
  [['variable.other.property', 'support.variable.property', 'meta.object-literal.key', 'entity.name.tag.yaml', 'support.type.property-name'], '#C8CAD2', '#3A4556'],
  [['entity.name.tag', 'punctuation.definition.tag'], '#EF9B98', '#B42430'],
  [['entity.other.attribute-name'], '#E8C68A', '#946400'],
  [['string.regexp', 'constant.character.escape'], '#ECACCB', '#B2357A'],
  [['punctuation', 'meta.brace', 'keyword.operator'], '#8D8D96', '#5B6474'],
  [['markup.heading', 'entity.name.section'], '#8FB8F2', '#1D5FC0', 'bold'],
  [['markup.bold'], null, null, 'bold'],
  [['markup.italic'], null, null, 'italic'],
  [['markup.inserted', 'meta.diff.header.to-file'], '#82D3A5', '#237A3E'],
  [['markup.deleted', 'meta.diff.header.from-file'], '#F08A80', '#B42430'],
  [['meta.diff.range', 'meta.diff.header'], '#8FB8F2', '#1D5FC0'],
  [['entity.name.label', 'support.other.namespace'], '#E9B07C', '#B2560E'],
];
const FOREGROUND = { dark: '#D9D9DE', light: '#1F1F25' };
const rowColour = (i: number) => `#0000${(i + 1).toString(16).padStart(2, '0')}`;
const theme: ThemeRegistration = {
  name: 'pilcrow',
  colors: { 'editor.foreground': '#000000', 'editor.background': '#00000000' },
  tokenColors: RULES.map(([scope, dark, , fontStyle], i) => ({ scope, settings: { ...(dark ? { foreground: rowColour(i) } : {}), ...(fontStyle ? { fontStyle } : {}) } })),
};
const COLOURS = new Map(RULES.map(([, dark, light], i) => [rowColour(i).toUpperCase(), dark && light ? { dark, light } : FOREGROUND]));

/** A token's style, as Shiki writes it for two themes: the colours and font as CSS variables (tokens.css). */
function styleOf(colour: string | undefined, fontStyle = 0): Record<string, string> {
  const c = COLOURS.get((colour || '').toUpperCase()) || FOREGROUND;
  const s: Record<string, string> = { '--shiki-dark': c.dark, '--shiki-light': c.light };
  if (fontStyle & 1) s['--shiki-dark-font-style'] = s['--shiki-light-font-style'] = 'italic';
  if (fontStyle & 2) s['--shiki-dark-font-weight'] = s['--shiki-light-font-weight'] = 'bold';
  return s;
}

let hlPromise: Promise<HighlighterCore> | null = null;
const loaded = new Set<string>();
const loading = new Map<string, Promise<void>>();

function highlighter() {
  hlPromise ||= Promise.all([import('shiki/core'), import('shiki/engine/javascript')]).then(([{ createHighlighterCore }, { createJavaScriptRegexEngine }]) => createHighlighterCore({ themes: [theme], langs: [], engine: createJavaScriptRegexEngine() }));
  return hlPromise;
}

async function ensureLang(lang: string) {
  if (loaded.has(lang)) return;
  if (!loading.has(lang)) {
    loading.set(
      lang,
      (async () => {
        const hl = await highlighter();
        const mod = (await LANGS[lang]()) as { default?: unknown };
        await hl.loadLanguage((mod.default || mod) as Parameters<typeof hl.loadLanguage>[0]);
        loaded.add(lang);
      })(),
    );
  }
  await loading.get(lang);
}

export interface Tok {
  t: string;
  s?: Record<string, string>;
}

/**
 * Code longer than this is shown plain: past it, even coloured a slice at a time, a block would keep
 * the highlighter busy for many seconds (measured here, a 60 kB TSX block takes about 1.2 s).
 */
export const MAX_HIGHLIGHT = 200_000;

/**
 * How long one slice of highlighting may run before the browser gets a turn. Shiki works a line at a
 * time and carries the grammar's state from one line to the next, so a block is coloured line by line
 * in tasks of about this length: a slice is this plus at most one line (up to ~30 ms for the densest
 * lines of TSX here).
 */
const SLICE_MS = 5;

// Keyed by language and the whole code, so a code block shown again is coloured in its first frame.
const htmlCache = new LruBytes<string>(4 << 20, (k, v) => k.length + v.length);
// A token is an object with its text and a style record: about 100 bytes each besides the text.
const tokCache = new LruBytes<Tok[][]>(8 << 20, (k, v) => k.length + v.reduce((n, line) => n + line.length * 100, 0));

/**
 * Blocks waiting to be coloured. One loop colours them all, a slice of SLICE_MS at a time (each after
 * a yield, so none runs with the caller's task), each slice on the next block in turn: blocks drawn
 * together share the slices instead of each running a loop of its own. A block no longer `wanted`
 * (its code left the page) is dropped at the next line.
 */
type Job = { lines: string[]; lang: string; out: Tok[][]; state?: GrammarState; wanted: () => boolean; done: (lines: Tok[][] | null) => void };
const queue: Job[] = [];
let running = false;

async function run(hl: HighlighterCore) {
  running = true;
  while (queue.length) {
    await yieldToEvents();
    const job = queue.shift() as Job;
    const start = performance.now();
    try {
      do {
        if (!job.wanted()) break;
        const { tokens } = hl.codeToTokens(job.lines[job.out.length], { lang: job.lang, theme: 'pilcrow', grammarState: job.state });
        job.state = hl.getLastGrammarState(tokens);
        job.out.push((tokens[0] || []).map((t) => ({ t: t.content, s: styleOf(t.color, t.fontStyle) })));
      } while (job.out.length < job.lines.length && performance.now() - start < SLICE_MS);
    } catch (e) {
      console.warn('highlight failed', job.lang, e);
      job.done(null);
      continue;
    }
    if (!job.wanted()) job.done(null);
    else if (job.out.length === job.lines.length) job.done(job.out);
    else queue.push(job);
  }
  running = false;
}

/** Tokens per line, or null when the block stopped being wanted before it was done. */
async function tokenize(code: string, lang: string, wanted: () => boolean): Promise<Tok[][] | null> {
  await ensureLang(lang);
  const hl = await highlighter();
  return new Promise((done) => {
    queue.push({ lines: code.split('\n'), lang, out: [], wanted, done });
    if (!running) void run(hl);
  });
}

export const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const css = (s: Record<string, string> | undefined) => (s ? Object.entries(s).map(([k, v]) => `${k}:${v}`).join(';') : '');

/**
 * Lines per block of a long code block's markup. Colour for a dense TSX line costs about 0.13 ms of
 * style, layout and paint here, so a 60 kB block swapped in at once is a 170 ms frame; Markdown swaps
 * a long one in a block of these per frame (paintCode).
 */
export const PAINT_LINES = 100;
export const CHUNK_OPEN = '<span class="hl-chunk" style="display:block">';

/**
 * The markup Shiki's codeToHtml puts inside <code>: a span per line, a styled span per token. Past
 * PAINT_LINES lines, the lines are grouped in blocks of that many (the newline between two blocks
 * ends the first, so the text reads the same).
 */
export function linesToHtml(lines: Tok[][]): string {
  const html = lines.map((line) => `<span class="line">${line.map((t) => `<span style="${escapeHtml(css(t.s))}">${escapeHtml(t.t)}</span>`).join('')}</span>`);
  if (html.length <= PAINT_LINES) return html.join('\n');
  let out = '';
  for (let i = 0; i < html.length; i += PAINT_LINES) out += `${CHUNK_OPEN}${html.slice(i, i + PAINT_LINES).join('\n')}${i + PAINT_LINES < html.length ? '\n' : ''}</span>`;
  return out;
}

/** Colour already worked out, to draw a block with in its first frame; a long one is painted a block at a time instead. */
export function cachedHtml(code: string, lang: string | null): string | null {
  const l = normalizeLang(lang);
  const html = l && code.length <= MAX_HIGHLIGHT ? htmlCache.get(l + '\0' + code) : undefined;
  return html && !html.startsWith(CHUNK_OPEN) ? html : null;
}

/** Highlight a code block; resolves to `<span class="line">…` markup (no <pre>), or null if it was not done. */
export async function highlightHtml(code: string, lang: string | null, wanted: () => boolean = () => true): Promise<string | null> {
  const l = normalizeLang(lang);
  if (!l || code.length > MAX_HIGHLIGHT) return null;
  const key = l + '\0' + code;
  const hit = htmlCache.get(key);
  if (hit) return hit;
  try {
    const lines = await tokenize(code, l, wanted);
    if (!lines) return null;
    const html = linesToHtml(lines);
    htmlCache.set(key, html);
    return html;
  } catch (e) {
    console.warn('highlight failed', l, e);
    return null;
  }
}

export function cachedTokens(code: string, lang: string | null): Tok[][] | null {
  const l = normalizeLang(lang);
  return l && code.length <= MAX_HIGHLIGHT ? (tokCache.get(l + '\0' + code) ?? null) : null;
}

/** Tokens per line, for diff rendering where lines are laid out individually; null if it was not done. */
export async function highlightTokens(code: string, lang: string | null, wanted: () => boolean = () => true): Promise<Tok[][] | null> {
  const l = normalizeLang(lang);
  if (!l || code.length > MAX_HIGHLIGHT) return null;
  const key = l + '\0' + code;
  const hit = tokCache.get(key);
  if (hit) return hit;
  try {
    const lines = await tokenize(code, l, wanted);
    if (lines) tokCache.set(key, lines);
    return lines;
  } catch (e) {
    console.warn('tokenize failed', l, e);
    return null;
  }
}
