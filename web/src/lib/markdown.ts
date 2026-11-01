// Markdown → sanitized HTML. Code blocks get a header and are highlighted after mount.
import { Lexer, Marked, Parser, type Token, type Tokens } from 'marked';
import DOMPurify from 'dompurify';
import { cachedHtml, normalizeLang } from './highlight';
import { LruBytes } from './cache';

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

// Set once: a config passed with every call is parsed again with every call, which while a reply
// streams is most of the cost of sanitising the piece being written.
DOMPurify.setConfig({ ADD_ATTR: ['target', 'data-lang', 'data-hl', 'tabindex', 'role'], FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'script'] });
const purify = (html: string) => DOMPurify.sanitize(html);

// Finished blocks by their source: a conversation drawn again, or a block scrolled back into view, costs nothing.
const cache = new LruBytes<string>(4 << 20, (k, v) => k.length + v.length);

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
  const out = purify(marked.parse(text, { async: false }) as string);
  currentBase = null;
  if (!streaming) cache.set(key, out);
  return out;
}

/** The top-level blocks of markdown, without the inline lexing a split has no use for. */
function lexBlocks(src: string): Token[] {
  return new Lexer(marked.defaults).blockTokens(src.replace(/\r\n|\r/g, '\n'), []);
}

/**
 * Where the last block of a split can be lexed again from as it grows: the start of its last item
 * (a list), of its last row after the two lines that make it a table, of its third line from the
 * end (a paragraph: a line can turn the one above it into a heading, or end the paragraph above
 * that one where the two could start a table). Null for other blocks.
 */
type Resume = { type: 'list' | 'table' | 'paragraph'; from: number; head: string };
function resumeAt(t: Token, block: string): Resume | null {
  if (t.type === 'list') return { type: 'list', from: (t as Tokens.List).items.slice(0, -1).reduce((n, it) => n + it.raw.length, 0), head: '' };
  if (t.type === 'table') {
    const nl = block.indexOf('\n', block.indexOf('\n') + 1);
    if (nl < 0) return null;
    return { type: 'table', from: Math.max(nl + 1, block.trimEnd().lastIndexOf('\n') + 1), head: block.slice(0, nl + 1) };
  }
  if (t.type === 'paragraph') {
    let from = block.replace(/\n+$/, '').length;
    for (let i = 0; i < 3 && from > 0; i++) from = block.lastIndexOf('\n', from - 1);
    return { type: 'paragraph', from: from + 1, head: '' };
  }
  return null;
}

export interface Split {
  text: string;
  blocks: string[];
  resume?: Resume | null;
}

/**
 * Split markdown into top-level blocks (paragraphs, lists, tables, code fences…). Each block renders
 * on its own, so while a reply streams only the block being written changes; finished blocks keep
 * their DOM, their highlighting and any text the reader has selected.
 */
export function splitBlocks(src: string): string[] {
  return split(src).blocks;
}

function split(src: string): Split {
  if (!src) return { text: src, blocks: [] };
  let tokens: Token[];
  try {
    tokens = lexBlocks(src);
  } catch {
    return { text: src, blocks: [src] };
  }
  const blocks: string[] = [];
  let last: Token | null = null;
  for (const t of tokens) {
    if (t.type === 'space' && blocks.length) blocks[blocks.length - 1] += t.raw;
    else {
      blocks.push(t.raw);
      last = t;
    }
  }
  if (!blocks.length) return { text: src, blocks: [src] };
  // The blocks as written: a token's raw can end differently (a list item's trailing space is given
  // back as a line break), and the block being written must stay a prefix of what it grows into.
  if (blocks.join('').length === src.length) {
    let at = 0;
    for (let i = 0; i < blocks.length; i++) blocks[i] = src.slice(at, (at += blocks[i].length));
  }
  return { text: src, blocks, resume: last && resumeAt(last, blocks[blocks.length - 1]) };
}

/**
 * splitBlocks for text that grows at its end, as a reply does while it streams. What is added can only
 * change the last block (or follow it with new ones), so the blocks before it are kept and only the
 * end of the last one is lexed again: a code fence still open needs no lexing at all, and a list, a
 * table or a paragraph is lexed from its last item, row or line (resumeAt), so the work per frame
 * follows the piece being written, not the length of the reply. Anything else (an edit in the middle,
 * text that lexing normalised so the blocks no longer add up to it) is split from the start.
 */
export function extendBlocks(prev: Split, text: string): Split {
  const n = prev.blocks.length;
  if (!n || !text.startsWith(prev.text)) return split(text);
  let from = 0;
  for (let i = 0; i < n - 1; i++) from += prev.blocks[i].length;
  const last = prev.blocks[n - 1];
  if (from + last.length !== prev.text.length) return split(text);
  const grown = text.slice(from); // the last block as written: lexing can give a block's end back changed
  const kept = prev.blocks.slice(0, -1);
  // A code fence still open stays one block until a line could close it: everything up to that is its code.
  if (openFenceCode(last) !== null && openFenceCode(grown) !== null) return { text, blocks: kept.concat(grown) };
  const r = prev.resume;
  if (r) {
    const src = r.head + grown.slice(r.from);
    let all: Token[] = [];
    try {
      all = lexBlocks(src);
    } catch {
      /* split from the block's start, below */
    }
    const toks = all.filter((t) => t.type !== 'space');
    // Still one block of the same kind, all of it: it is the grown text, resumed from its new last piece.
    if (toks.length === 1 && toks[0].type === r.type && all.reduce((n, t) => n + t.raw.length, 0) === src.length) {
      const at = resumeAt(toks[0], grown);
      return { text, blocks: kept.concat(grown), resume: at && r.type === 'list' ? { ...at, from: r.from + at.from } : at };
    }
  }
  const tail = split(text.slice(from));
  return { text, blocks: kept.concat(tail.blocks), resume: tail.resume };
}

/**
 * A list, a table or a paragraph being written, drawn a piece at a time: its items, its rows, its
 * runs of inline text. `from` is where the piece still being written starts in the block's source;
 * everything before it is drawn and final, and is not parsed or drawn again until the block closes.
 */
export interface Growth {
  kind: 'list' | 'table' | 'paragraph';
  from: number;
  /** A list whose items are spaced apart (marked's "loose"): each item's text is a paragraph. */
  loose: boolean;
  /** A table's header and delimiter rows, which make the rows after them a table. */
  head: string;
}
/** The markup of a growing block's container: the pieces now final, then the one still being written. */
interface Grown {
  g: Growth;
  settled: string;
  rest: string;
}
/** Where each kind's pieces go in the markup renderMarkdown draws. */
export const GROWS_IN: Record<Growth['kind'], string> = { list: ':scope > ul, :scope > ol', table: ':scope > .md-table tbody', paragraph: ':scope > p' };

/** What is inside the element `tag` that a piece of markup is: the pieces of a container, without it. */
function inner(html: string, tag: string) {
  const a = html.indexOf('>', html.indexOf(`<${tag}`)) + 1;
  const b = html.lastIndexOf(`</${tag}>`);
  return a > 0 && b >= a ? html.slice(a, b).trim() : '';
}

/**
 * The pieces of a growing block from where its last piece starts (`g`, or from its start the first
 * time), drawn as renderMarkdown would draw them in place: null when the block is no longer of that
 * kind, or grew in a way that changes what is drawn already (a list whose items become spaced
 * apart), and is to be drawn whole.
 */
export function growBlock(raw: string, g: Growth | null): Grown | null {
  try {
    if (g?.kind === 'paragraph') {
      const text = raw.replace(/\n+$/, '');
      return growParagraph(text, g.from, Lexer.lexInline(text.slice(g.from), marked.defaults));
    }
    const toks = marked.lexer((g?.head || '') + raw.slice(g?.from || 0)).filter((t) => t.type !== 'space');
    const t = toks[0];
    if (toks.length !== 1 || (g && t.type !== g.kind)) return null;
    if (t.type === 'list') return growList(t as Tokens.List, g);
    if (t.type === 'table') return growTable(raw, t as Tokens.Table, g);
    if (t.type === 'paragraph' && !g) {
      const p = t as Tokens.Paragraph;
      return p.text === raw.replace(/\n+$/, '') ? growParagraph(p.text, 0, p.tokens) : null;
    }
  } catch {
    /* drawn whole */
  }
  return null;
}

function growList(t: Tokens.List, g: Growth | null): Grown | null {
  const loose = g ? g.loose : t.loose;
  if ((t.loose && !loose) || t.items.map((it) => it.raw).join('').trimEnd() !== t.raw.trimEnd()) return null;
  // The items lexed from the last one on are spaced apart as the whole list is, which marked marks this way.
  if (loose)
    for (const it of t.items) {
      it.loose = true;
      for (const k of it.tokens) if (k.type === 'text') k.type = 'paragraph';
    }
  const tag = t.ordered ? 'ol' : 'ul';
  const draw = (items: Tokens.ListItem[]) => (items.length ? inner(purify(marked.parser([{ ...t, items }])), tag) : '');
  const done = t.items.slice(0, -1);
  return { g: { kind: 'list', from: (g?.from || 0) + done.reduce((n, it) => n + it.raw.length, 0), loose, head: '' }, settled: draw(done), rest: draw(t.items.slice(-1)) };
}

function growTable(raw: string, t: Tokens.Table, g: Growth | null): Grown | null {
  let head = g?.head || '';
  let from = g?.from || 0;
  if (!g) {
    const nl = raw.indexOf('\n', raw.indexOf('\n') + 1);
    if (nl < 0) return null;
    head = raw.slice(0, nl + 1);
    from = nl + 1;
  }
  const body = raw.slice(from).replace(/\n+$/, '');
  const lines = body ? body.split('\n') : [];
  if (lines.length !== t.rows.length) return null;
  const draw = (rows: Tokens.TableCell[][]) => (rows.length ? inner(purify(marked.parser([{ ...t, rows }])), 'tbody') : '');
  return { g: { kind: 'table', from: from + lines.slice(0, -1).reduce((n, l) => n + l.length + 1, 0), loose: false, head }, settled: draw(t.rows.slice(0, -1)), rest: draw(t.rows.slice(-1)) };
}

// In a run of plain text, what may yet open something the text after it closes: an emphasis or
// strikethrough mark, a backtick, a bracket, an angle bracket, a backslash; `_` between two letters
// opens nothing.
const OPENS = /[*~`[<\\]|(?<![\p{L}\p{N}])_|_(?![\p{L}\p{N}])/u;
// Inline tokens that are whole: what follows them cannot change how they are drawn.
const CLOSED = new Set(['strong', 'em', 'codespan', 'link', 'image', 'del', 'br', 'escape']);

/**
 * A paragraph's inline text from `from`: final up to the last place where nothing is left open (no
 * mark that a later one could pair with) and the text on one side is a space, so lexing again from
 * there reads the rest as it would read it in place. The word being written stays open.
 */
function growParagraph(text: string, from: number, toks: Token[]): Grown {
  const src = text.slice(from);
  const quiet = (at: number) => /\s/.test(src[at - 1] || '') || /\s/.test(src[at] || '');
  let at = 0; // where the final part ends in src
  let whole = 0; // the tokens wholly in it
  let part = ''; // and the start of the next one, a text cut at a space
  let pos = 0;
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    const end = i === toks.length - 1;
    if (t.type === 'text') {
      const opens = t.raw.search(OPENS);
      if (end || opens >= 0) {
        const space = t.raw.lastIndexOf(' ', opens < 0 ? Infinity : opens);
        if (space > 0) [at, whole, part] = [pos + space, i, t.raw.slice(0, space)];
        break;
      }
    } else if (end || !CLOSED.has(t.type)) break;
    pos += t.raw.length;
    if (quiet(pos)) [at, whole, part] = [pos, i + 1, ''];
  }
  const cut = toks[whole] as Tokens.Text;
  const done = part ? [...toks.slice(0, whole), { type: 'text', raw: part, text: part } as Tokens.Text] : toks.slice(0, whole);
  const rest = part ? [{ ...cut, raw: cut.raw.slice(part.length), text: cut.text.slice(part.length) }, ...toks.slice(whole + 1)] : toks.slice(whole);
  const draw = (ts: Token[]) => (ts.length ? purify(Parser.parseInline(ts, marked.defaults)) : '');
  return { g: { kind: 'paragraph', from: from + at, loose: false, head: '' }, settled: draw(done), rest: draw(rest) };
}

/**
 * The code of a block that is one ``` code fence not closed yet, as it is drawn (renderMarkdown closes
 * it for the parse, and the renderer drops the newline before the closing fence); null for anything
 * else, including a fence with any line that might close it.
 */
export function openFenceCode(raw: string): string | null {
  const open = /^```[^\n`]*\n/.exec(raw);
  if (!open || /\n {0,3}```/.test(raw.slice(open[0].length - 1))) return null;
  return raw.slice(open[0].length).replace(/\n$/, '');
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
    if (!t.includes('/') && !KNOWN_EXT.has(t.replace(/:(\d+)(-\d+)?$/, '').split('.').pop()?.toLowerCase() || '')) return null;
    return { path: t.replace(/:(\d+)(-\d+)?$/, ''), line: m[1] ? Number(m[1]) : undefined };
  }
  if (DIR_RE.test(t) && t.includes('/')) return null;
  return null;
}
