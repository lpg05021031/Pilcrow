import { memo, useEffect, useMemo, useRef } from 'react';
import { renderMarkdown, asPathRef, extendBlocks } from '../lib/markdown';
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
 * One top-level block. Finished blocks never re-render, so their DOM (and the reader's selection)
 * survives streaming; while a reply streams, only the open block at the end renders, and only its
 * own DOM is looked over afterwards.
 */
const Block = memo(function Block({ raw, open, baseDir }: { raw: string; open: boolean; baseDir?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = useMemo(() => renderMarkdown(raw, { streaming: open, baseDir }), [raw, open, baseDir]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const pres = el.querySelectorAll<HTMLPreElement>('pre.hl:not([data-hl])');
    pres.forEach((pre, i) => {
      if (open && i === pres.length - 1) return; // still being written
      const code = pre.textContent || '';
      pre.dataset.hl = 'pending';
      highlightHtml(code, pre.dataset.lang || null).then((h) => {
        if (h && pre.isConnected && pre.textContent === code) paintCode(pre, code, h);
      });
    });
    el.querySelectorAll<HTMLElement>('code:not([data-pr])').forEach((c) => {
      c.dataset.pr = '1';
      if (c.closest('pre')) return;
      const r = asPathRef(c.textContent || '');
      if (!r) return;
      c.classList.add('md-path');
      c.dataset.path = r.path;
      if (r.line) c.dataset.line = String(r.line);
      c.title = 'Open in Files';
    });
  }, [html, open]);

  return <div ref={ref} className="md-b" dangerouslySetInnerHTML={{ __html: html }} />;
});

export const Markdown = memo(function Markdown({ text, streaming = false, cwd, className = '', baseDir }: { text: string; streaming?: boolean; cwd?: string; className?: string; baseDir?: string }) {
  // The last split is kept so a growing text is lexed again only from its last block (lib/markdown).
  const split = useRef<{ text: string; blocks: string[] }>({ text: '', blocks: [] });
  const blocks = useMemo(() => {
    const next = { text, blocks: extendBlocks(split.current, text) };
    split.current = next;
    return next.blocks;
  }, [text]);

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
        <Block key={i} raw={b} open={streaming && i === blocks.length - 1} baseDir={baseDir} />
      ))}
    </div>
  );
});
