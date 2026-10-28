// Syntax highlighting with Shiki (JavaScript regex engine, no WASM), lazy-loaded per language.
// Two hand-tuned themes are emitted as CSS variables so dark/light switch without re-highlighting.
import { createHighlighterCore, type HighlighterCore, type ThemeRegistration } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';

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

const dark: ThemeRegistration = {
  name: 'pilcrow-dark',
  type: 'dark',
  colors: { 'editor.foreground': '#D9D9DE', 'editor.background': '#00000000' },
  tokenColors: [
    { scope: ['comment', 'punctuation.definition.comment', 'string.comment'], settings: { foreground: '#6C6C75', fontStyle: 'italic' } },
    { scope: ['keyword', 'storage', 'storage.type', 'keyword.control', 'keyword.operator.new', 'keyword.operator.expression', 'variable.language', 'keyword.other'], settings: { foreground: '#C3A6EC' } },
    { scope: ['string', 'string.quoted', 'string.template', 'markup.inline.raw'], settings: { foreground: '#A2CF98' } },
    { scope: ['constant.numeric', 'constant.language', 'constant.character', 'support.constant', 'constant.other', 'keyword.other.unit'], settings: { foreground: '#E9B07C' } },
    { scope: ['entity.name.function', 'support.function', 'meta.function-call', 'variable.function'], settings: { foreground: '#8FB8F2' } },
    { scope: ['entity.name.type', 'entity.name.class', 'support.type', 'support.class', 'entity.other.inherited-class', 'storage.type.primitive', 'entity.name.namespace'], settings: { foreground: '#84CFC3' } },
    { scope: ['variable.parameter'], settings: { foreground: '#E5C391' } },
    { scope: ['variable.other.property', 'support.variable.property', 'meta.object-literal.key', 'entity.name.tag.yaml', 'support.type.property-name'], settings: { foreground: '#C8CAD2' } },
    { scope: ['entity.name.tag', 'punctuation.definition.tag'], settings: { foreground: '#EF9B98' } },
    { scope: ['entity.other.attribute-name'], settings: { foreground: '#E8C68A' } },
    { scope: ['string.regexp', 'constant.character.escape'], settings: { foreground: '#ECACCB' } },
    { scope: ['punctuation', 'meta.brace', 'keyword.operator'], settings: { foreground: '#8D8D96' } },
    { scope: ['markup.heading', 'entity.name.section'], settings: { foreground: '#8FB8F2', fontStyle: 'bold' } },
    { scope: ['markup.bold'], settings: { fontStyle: 'bold' } },
    { scope: ['markup.italic'], settings: { fontStyle: 'italic' } },
    { scope: ['markup.inserted', 'meta.diff.header.to-file'], settings: { foreground: '#82D3A5' } },
    { scope: ['markup.deleted', 'meta.diff.header.from-file'], settings: { foreground: '#F08A80' } },
    { scope: ['meta.diff.range', 'meta.diff.header'], settings: { foreground: '#8FB8F2' } },
    { scope: ['entity.name.label', 'support.other.namespace'], settings: { foreground: '#E9B07C' } },
  ],
};

const light: ThemeRegistration = {
  name: 'pilcrow-light',
  type: 'light',
  colors: { 'editor.foreground': '#1F1F25', 'editor.background': '#00000000' },
  tokenColors: [
    { scope: ['comment', 'punctuation.definition.comment', 'string.comment'], settings: { foreground: '#8A8A93', fontStyle: 'italic' } },
    { scope: ['keyword', 'storage', 'storage.type', 'keyword.control', 'keyword.operator.new', 'keyword.operator.expression', 'variable.language', 'keyword.other'], settings: { foreground: '#7B3BC0' } },
    { scope: ['string', 'string.quoted', 'string.template', 'markup.inline.raw'], settings: { foreground: '#2D7A33' } },
    { scope: ['constant.numeric', 'constant.language', 'constant.character', 'support.constant', 'constant.other', 'keyword.other.unit'], settings: { foreground: '#B2560E' } },
    { scope: ['entity.name.function', 'support.function', 'meta.function-call', 'variable.function'], settings: { foreground: '#1D5FC0' } },
    { scope: ['entity.name.type', 'entity.name.class', 'support.type', 'support.class', 'entity.other.inherited-class', 'storage.type.primitive', 'entity.name.namespace'], settings: { foreground: '#0B786D' } },
    { scope: ['variable.parameter'], settings: { foreground: '#8A5A00' } },
    { scope: ['variable.other.property', 'support.variable.property', 'meta.object-literal.key', 'entity.name.tag.yaml', 'support.type.property-name'], settings: { foreground: '#3A4556' } },
    { scope: ['entity.name.tag', 'punctuation.definition.tag'], settings: { foreground: '#B42430' } },
    { scope: ['entity.other.attribute-name'], settings: { foreground: '#946400' } },
    { scope: ['string.regexp', 'constant.character.escape'], settings: { foreground: '#B2357A' } },
    { scope: ['punctuation', 'meta.brace', 'keyword.operator'], settings: { foreground: '#5B6474' } },
    { scope: ['markup.heading', 'entity.name.section'], settings: { foreground: '#1D5FC0', fontStyle: 'bold' } },
    { scope: ['markup.bold'], settings: { fontStyle: 'bold' } },
    { scope: ['markup.italic'], settings: { fontStyle: 'italic' } },
    { scope: ['markup.inserted', 'meta.diff.header.to-file'], settings: { foreground: '#237A3E' } },
    { scope: ['markup.deleted', 'meta.diff.header.from-file'], settings: { foreground: '#B42430' } },
    { scope: ['meta.diff.range', 'meta.diff.header'], settings: { foreground: '#1D5FC0' } },
    { scope: ['entity.name.label', 'support.other.namespace'], settings: { foreground: '#B2560E' } },
  ],
};

let hlPromise: Promise<HighlighterCore> | null = null;
const loaded = new Set<string>();
const loading = new Map<string, Promise<void>>();

function highlighter() {
  if (!hlPromise) hlPromise = createHighlighterCore({ themes: [dark, light], langs: [], engine: createJavaScriptRegexEngine() });
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

const htmlCache = new Map<string, string>();
const tokCache = new Map<string, Tok[][]>();
const MAX_CACHE = 600;

function remember<V>(map: Map<string, V>, key: string, v: V) {
  if (map.size > MAX_CACHE) map.delete(map.keys().next().value!);
  map.set(key, v);
}

export function cachedHtml(code: string, lang: string | null): string | null {
  const l = normalizeLang(lang);
  return l ? htmlCache.get(l + '\0' + code) ?? null : null;
}

/** Highlight a code block; resolves to `<span class="line">…` markup (no <pre>). */
export async function highlightHtml(code: string, lang: string | null): Promise<string | null> {
  const l = normalizeLang(lang);
  if (!l || code.length > 200_000) return null;
  const key = l + '\0' + code;
  const hit = htmlCache.get(key);
  if (hit) return hit;
  try {
    await ensureLang(l);
    const hl = await highlighter();
    const html = hl.codeToHtml(code, { lang: l, themes: { dark: 'pilcrow-dark', light: 'pilcrow-light' }, defaultColor: false });
    const inner = html.replace(/^<pre[^>]*><code[^>]*>/, '').replace(/<\/code><\/pre>$/, '');
    remember(htmlCache, key, inner);
    return inner;
  } catch (e) {
    console.warn('highlight failed', l, e);
    return null;
  }
}

export function cachedTokens(code: string, lang: string | null): Tok[][] | null {
  const l = normalizeLang(lang);
  return l ? tokCache.get(l + '\0' + code) ?? null : null;
}

/** Tokens per line, for diff rendering where lines are laid out individually. */
export async function highlightTokens(code: string, lang: string | null): Promise<Tok[][] | null> {
  const l = normalizeLang(lang);
  if (!l || code.length > 300_000) return null;
  const key = l + '\0' + code;
  const hit = tokCache.get(key);
  if (hit) return hit;
  try {
    await ensureLang(l);
    const hl = await highlighter();
    const res = hl.codeToTokens(code, { lang: l, themes: { dark: 'pilcrow-dark', light: 'pilcrow-light' }, defaultColor: false });
    const lines: Tok[][] = res.tokens.map((line) => line.map((t) => ({ t: t.content, s: (t.htmlStyle as Record<string, string>) || undefined })));
    remember(tokCache, key, lines);
    return lines;
  } catch (e) {
    console.warn('tokenize failed', l, e);
    return null;
  }
}
