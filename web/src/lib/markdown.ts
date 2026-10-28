// Markdown → sanitized HTML. Code blocks get a header and are highlighted after mount.
import { Marked, type Tokens } from 'marked';
import DOMPurify from 'dompurify';
import { cachedHtml, normalizeLang } from './highlight';

// How code-block languages are labelled: people say "TypeScript", not "ts".
const LANG_NAMES: Record<string, string> = {
  ts: 'TypeScript', tsx: 'TSX', typescript: 'TypeScript', js: 'JavaScript', jsx: 'JSX', javascript: 'JavaScript', mjs: 'JavaScript', cjs: 'JavaScript',
  py: 'Python', python: 'Python', sh: 'Shell', bash: 'Shell', zsh: 'Shell', shell: 'Shell', console: 'Terminal', json: 'JSON', jsonc: 'JSON', yaml: 'YAML', yml: 'YAML',
  toml: 'TOML', md: 'Markdown', markdown: 'Markdown', html: 'HTML', css: 'CSS', scss: 'SCSS', sql: 'SQL', c: 'C', h: 'C', cpp: 'C++', 'c++': 'C++', cc: 'C++', hpp: 'C++',
  cs: 'C#', java: 'Java', kt: 'Kotlin', go: 'Go', rs: 'Rust', rust: 'Rust', rb: 'Ruby', php: 'PHP', swift: 'Swift', diff: 'Diff', patch: 'Diff', dockerfile: 'Dockerfile',
  makefile: 'Makefile', make: 'Makefile', verilog: 'Verilog', v: 'Verilog', sv: 'SystemVerilog', systemverilog: 'SystemVerilog', vhdl: 'VHDL', tcl: 'Tcl', perl: 'Perl', pl: 'Perl',
  r: 'R', matlab: 'MATLAB', lua: 'Lua', xml: 'XML', ini: 'INI', text: 'Text', txt: 'Text', plaintext: 'Text', csv: 'CSV', graphql: 'GraphQL', proto: 'Protobuf',
};

const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Folder that relative image paths resolve against while a document file is being rendered. */
let currentBase: string | null = null;
function resolveSrc(src: string): string {
  if (!currentBase || /^(https?:|data:|blob:|\/api\/)/i.test(src) || src.startsWith('/')) return src;
  const abs = `${currentBase.replace(/\/+$/, '')}/${src.replace(/^\.\//, '')}`;
  return `/api/raw?path=${encodeURIComponent(abs)}`;
}

const marked = new Marked({
  gfm: true,
  breaks: false,
  renderer: {
    code(token: Tokens.Code) {
      const lang = (token.lang || '').trim().split(/\s+/)[0];
      const code = token.text.replace(/\n$/, '');
      const cached = cachedHtml(code, lang);
      const label = LANG_NAMES[lang.toLowerCase()] || lang || 'Text';
      const body = cached ?? escape(code);
      return `<div class="md-code"><div class="md-code-head"><span class="md-code-lang">${escape(label)}</span><span class="md-copy" role="button" tabindex="0" title="Copy code">Copy</span></div><pre class="hl" data-lang="${escape(lang)}"${cached ? ' data-hl="1"' : ''}><code>${body}</code></pre></div>`;
    },
    image(token: Tokens.Image) {
      const title = token.title ? ` title="${escape(token.title)}"` : '';
      return `<img src="${escape(resolveSrc(token.href || ''))}" alt="${escape(token.text || '')}"${title} loading="lazy">`;
    },
    link(token: Tokens.Link) {
      const text = this.parser.parseInline(token.tokens);
      const href = token.href || '';
      const title = token.title ? ` title="${escape(token.title)}"` : '';
      return `<a href="${escape(href)}"${title} target="_blank" rel="noopener noreferrer">${text}</a>`;
    },
    table(token: Tokens.Table) {
      // Let marked render, then wrap so wide tables scroll.
      const header = token.header.map((c, i) => `<th${token.align[i] ? ` style="text-align:${token.align[i]}"` : ''}>${this.parser.parseInline(c.tokens)}</th>`).join('');
      const rows = token.rows.map((r) => `<tr>${r.map((c, i) => `<td${token.align[i] ? ` style="text-align:${token.align[i]}"` : ''}>${this.parser.parseInline(c.tokens)}</td>`).join('')}</tr>`).join('');
      return `<div class="md-table"><table><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
    },
  },
});

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

const cache = new Map<string, string>();

export function renderMarkdown(src: string, { streaming = false, baseDir }: { streaming?: boolean; baseDir?: string } = {}): string {
  if (!src) return '';
  const key = baseDir ? `${baseDir}\0${src}` : src;
  if (!streaming) {
    const hit = cache.get(key);
    if (hit) return hit;
  }
  currentBase = baseDir || null;
  let text = src;
  // While streaming, close an unterminated code fence so the block renders as code.
  if (streaming && (text.match(/^```/gm) || []).length % 2 === 1) text += '\n```';
  const out = DOMPurify.sanitize(marked.parse(text, { async: false }) as string, {
    ADD_ATTR: ['target', 'data-lang', 'data-hl', 'tabindex', 'role'],
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'script'],
  });
  currentBase = null;
  if (!streaming) {
    if (cache.size > 400) cache.delete(cache.keys().next().value!);
    cache.set(key, out);
  }
  return out;
}

/**
 * Split markdown into top-level blocks (paragraphs, lists, tables, code fences…). Each block renders
 * on its own, so while a reply streams only the block being written changes; finished blocks keep
 * their DOM, their highlighting and any text the reader has selected.
 */
export function splitBlocks(src: string): string[] {
  if (!src) return [];
  let tokens: { type: string; raw: string }[];
  try {
    tokens = marked.lexer(src) as unknown as { type: string; raw: string }[];
  } catch {
    return [src];
  }
  const out: string[] = [];
  for (const t of tokens) {
    if (t.type === 'space' && out.length) out[out.length - 1] += t.raw;
    else out.push(t.raw);
  }
  return out.length ? out : [src];
}

export { normalizeLang };

const PATH_RE = /^(?:\.{0,2}\/)?(?:[\w@.+-]+\/)*[\w@.+-]+\.[A-Za-z0-9]{1,10}(?::(\d+)(?:-\d+)?)?$/;
const KNOWN_EXT = new Set('ts tsx js jsx mjs cjs mts cts json jsonc md mdx css scss sass less html htm vue svelte astro py pyi rb go rs java kt kts swift m mm c h cc cpp hpp cs fs php pl sh bash zsh fish ps1 psm1 bat cmd yml yaml toml ini cfg conf env txt log sql graphql gql proto xml svg png jpg jpeg gif webp ico pdf csv tsv lock map wasm dockerfile makefile gradle properties plist tf hcl ex exs erl hs ml elm clj cljs scala dart lua r jl nim zig v sol ipynb'.split(' '));
const DIR_RE = /^(?:\.{0,2}\/)?(?:[\w@.+-]+\/)+$/;

/** Does an inline `code` span look like a file path (optionally with :line)? */
export function asPathRef(text: string): { path: string; line?: number } | null {
  const t = text.trim();
  if (t.length > 240 || /\s/.test(t)) return null;
  if (/^https?:/i.test(t)) return null;
  const m = t.match(PATH_RE);
  if (m) {
    // A bare `name.ext` is a file only when the extension is one; `Math.round` and `t.rawBody` are code.
    if (!t.includes('/') && !KNOWN_EXT.has(t.replace(/:(\d+)(-\d+)?$/, '').split('.').pop()!.toLowerCase())) return null;
    return { path: t.replace(/:(\d+)(-\d+)?$/, ''), line: m[1] ? Number(m[1]) : undefined };
  }
  if (DIR_RE.test(t) && t.includes('/')) return null;
  return null;
}
