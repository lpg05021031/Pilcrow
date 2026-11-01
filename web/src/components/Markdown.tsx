import { memo, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { renderMarkdown, asPathRef, extendBlocks, growBlock, openFenceCode, GROWS_IN, type Growth, type Split } from '../lib/markdown';
import { CHUNK_OPEN, escapeHtml, highlightHtml, PAINT_LINES } from '../lib/highlight';
import { rpc } from '../lib/rpc';
import { openFile, toast } from '../lib/store';
import { copyText } from './ui/bits';

export function resolvePath(p: string, cwd?: string) {
  if (!p) return p;
  if (p.startsWith('/')) return p;
  if (p.startsWith('~/')) return p;
  const base = (cwd || '').replace(/\/+$/, '');
  return base ? `${base}/${p.replace(/^\.\//, '')}` : p;
}

/**
 * Swap a code block's plain text for its colour. A long one (lib/highlight linesToHtml) is first cut
 * into the same blocks of plain lines, then coloured a block per frame, so no frame styles, lays out
 * and paints all of it at once; it arrives gradually that way, so it goes without the fade-in.
 */
async function paintCode(pre: HTMLPreElement, code: string, html: string) {
  const c = pre.querySelector('code');
  if (!c) return;
  const chunks = html.split(CHUNK_OPEN).slice(1);
  if (!chunks.length) {
    c.innerHTML = html;
    pre.dataset.hl = '1';
    return;
  }
  const lines = code.split('\n');
  c.style.animation = 'none';
  c.innerHTML = chunks.map((_, i) => `${CHUNK_OPEN}${escapeHtml(lines.slice(i * PAINT_LINES, (i + 1) * PAINT_LINES).join('\n'))}${i < chunks.length - 1 ? '\n' : ''}</span>`).join('');
  for (let i = 0; i < chunks.length; i++) {
    await new Promise(requestAnimationFrame);
    if (!c.isConnected) return; // the block was drawn again meanwhile
    c.children[i].outerHTML = CHUNK_OPEN + chunks[i];
  }
  pre.dataset.hl = '1';
}

/**
 * Add text to a code block being written, PAINT_LINES lines to a block of their own (the blocks a long
 * one is coloured in, paintCode): one growing run of text is laid out and painted whole for every
 * piece, while a new piece in the last of these blocks leaves the others as they were.
 */
function appendCode(code: Element, text: string) {
  let chunk = code.lastElementChild instanceof HTMLElement && code.lastElementChild.classList.contains('hl-chunk') ? code.lastElementChild : null;
  if (!chunk) {
    chunk = code.appendChild(newChunk());
    chunk.append(...[...code.childNodes].filter((n) => n !== chunk));
    chunk.dataset.lines = String((chunk.textContent || '').split('\n').length - 1);
  }
  let lines = Number(chunk.dataset.lines);
  for (const part of text.split(/(?<=\n)/)) {
    if (lines >= PAINT_LINES) {
      chunk.dataset.lines = String(lines);
      chunk = code.appendChild(newChunk());
      lines = 0;
    }
    chunk.append(part);
    if (part.endsWith('\n')) lines++;
  }
  chunk.dataset.lines = String(lines);
}

function newChunk() {
  const el = document.createElement('span');
  el.className = 'hl-chunk';
  el.style.display = 'block';
  return el;
}

/** Colour the finished code blocks in a block or a piece of one, and make paths in its inline code open in Files. */
function decorate(el: Element, open: boolean) {
  const within = <T extends Element>(sel: string) => [...(el.matches(sel) ? [el as T] : []), ...el.querySelectorAll<T>(sel)];
  const pres = within<HTMLPreElement>('pre.hl:not([data-hl])');
  pres.forEach((pre, i) => {
    if (open && i === pres.length - 1) return; // still being written
    const code = pre.textContent || '';
    pre.dataset.hl = 'pending';
    highlightHtml(code, pre.dataset.lang || null, () => pre.isConnected).then((h) => {
      if (h && pre.isConnected && pre.textContent === code) paintCode(pre, code, h);
    });
  });
  within<HTMLElement>('code:not([data-pr])').forEach((c) => {
    c.dataset.pr = '1';
    if (c.closest('pre')) return;
    const r = asPathRef(c.textContent || '');
    if (!r) return;
    c.classList.add('md-path');
    c.dataset.path = r.path;
    if (r.line) c.dataset.line = String(r.line);
    c.title = 'Open in Files';
  });
}

/**
 * What the DOM of a block being written holds: the markup drawn whole and the source it shows now,
 * and how it grows from there. An open code fence by its code, a list, a table or a paragraph by its
 * pieces (lib/markdown growBlock): `grow` is null until the first piece is drawn that way, and false
 * for a block that cannot be (it is drawn whole each time).
 */
type Drawn = { html: string; raw: string; code: string | null; grow: Growth | null | false; keep: number };

/**
 * One top-level block. Finished blocks never re-render, so their DOM (and the reader's selection)
 * survives streaming; while a reply streams, only the open block at the end renders. It grows at its
 * end, so what arrives is added to its DOM (below) instead of the block being parsed, sanitised and
 * laid out whole again for every piece: the code of a fence is appended to its text, the item, row or
 * run of text being written is drawn again and the ones before it are left alone. The block is drawn
 * whole once it closes. `kind` is what the split says the open block is now (lib/markdown extendBlocks).
 */
const Block = memo(function Block({ raw, open, kind, baseDir }: { raw: string; open: boolean; kind?: Growth['kind']; baseDir?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const drawn = useRef<Drawn | null>(null);
  const d = open ? drawn.current : null;
  const after = d !== null && raw.startsWith(d.raw);
  const code = open ? openFenceCode(raw) : null;
  const fence = after && code !== null && d.code !== null && code.startsWith(d.code);
  const tries = after && code === null && kind !== undefined && d.grow !== false && (!d.grow || d.grow.kind === kind);
  const grown = tries ? growBlock(raw, d.grow || null) : null;
  const html = (fence || grown) && d ? d.html : renderMarkdown(raw, { streaming: open, baseDir });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !open) {
      drawn.current = null;
      return;
    }
    if (d && fence && code !== null) {
      const c = el.querySelector('pre:last-of-type code');
      // Not what was drawn (something else changed it): the next piece draws the block whole.
      if (!c || c.textContent !== d.code) drawn.current = null;
      else {
        appendCode(c, code.slice((d.code || '').length));
        drawn.current = { ...d, raw, code };
      }
      return;
    }
    if (d && grown) {
      const box = el.querySelector(GROWS_IN[grown.g.kind]);
      if (!box || box.childNodes.length < d.keep) {
        drawn.current = null;
        return;
      }
      while (box.childNodes.length > d.keep) box.lastChild?.remove();
      box.insertAdjacentHTML('beforeend', grown.settled);
      const keep = box.childNodes.length;
      box.insertAdjacentHTML('beforeend', grown.rest);
      drawn.current = { ...d, raw, grow: grown.g, keep };
      for (let i = d.keep, n: ChildNode | null = box.childNodes[i]; n; n = n.nextSibling, i++) if (n instanceof Element) decorate(n, i >= keep);
      return;
    }
    // Drawn whole. A block that could not grow from its start never will; one that stopped growing starts over.
    const c = el.querySelector('pre:last-of-type code');
    drawn.current = { html, raw, code: code !== null && c?.textContent === code ? code : null, grow: tries && d && !d.grow ? false : null, keep: 0 };
  });

  useEffect(() => {
    if (ref.current) decorate(ref.current, open);
  }, [html, open]);

  return <div ref={ref} className="md-b" dangerouslySetInnerHTML={{ __html: html }} />;
});

export const Markdown = memo(function Markdown({ text, streaming = false, cwd, className = '', baseDir }: { text: string; streaming?: boolean; cwd?: string; className?: string; baseDir?: string }) {
  // The last split is kept so a growing text is lexed again only from the end of its last block (lib/markdown).
  const last = useRef<Split>({ text: '', blocks: [] });
  const split = useMemo(() => (last.current = extendBlocks(last.current, text)), [text]);
  const blocks = split.blocks;

  const onClick = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    const copy = t.closest('.md-copy') as HTMLElement | null;
    if (copy) {
      const pre = copy.closest('.md-code')?.querySelector('pre');
      copyText(pre?.textContent || '').then(
        () => {
          copy.textContent = 'Copied';
          copy.classList.add('done');
          setTimeout(() => {
            copy.textContent = 'Copy';
            copy.classList.remove('done');
          }, 1200);
        },
        () => toast('Could not copy to the clipboard', 'error'),
      );
      return;
    }
    const a = t.closest('a') as HTMLAnchorElement | null;
    if (a && a.href) {
      e.preventDefault();
      if (/^https?:|^mailto:/i.test(a.getAttribute('href') || '')) rpc.call('shell.openExternal', { url: a.href }).catch(() => toast('Could not open the link', 'error'));
      return;
    }
    const p = t.closest('.md-path') as HTMLElement | null;
    if (p?.dataset.path) openFile(resolvePath(p.dataset.path, cwd), p.dataset.line ? Number(p.dataset.line) : undefined);
  };

  return (
    <div className={`md ${className}${streaming ? ' streaming' : ''}`} onClick={onClick}>
      {blocks.map((b, i) => (
        <Block key={i} raw={b} open={streaming && i === blocks.length - 1} kind={streaming && i === blocks.length - 1 ? split.resume?.type : undefined} baseDir={baseDir} />
      ))}
    </div>
  );
});
