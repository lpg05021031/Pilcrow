import { memo, useEffect, useMemo, useRef } from 'react';
import { renderMarkdown, asPathRef, splitBlocks } from '../lib/markdown';
import { highlightHtml } from '../lib/highlight';
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

/** One top-level block. Finished blocks never re-render, so their DOM (and the reader's selection) survives streaming. */
const Block = memo(
  function Block({ raw, open, baseDir }: { raw: string; open: boolean; baseDir?: string }) {
    const html = useMemo(() => renderMarkdown(raw, { streaming: open, baseDir }), [raw, open, baseDir]);
    return <div className="md-b" dangerouslySetInnerHTML={{ __html: html }} />;
  },
  (a, b) => a.raw === b.raw && a.open === b.open && a.baseDir === b.baseDir,
);

export const Markdown = memo(function Markdown({ text, streaming = false, cwd, className = '', baseDir }: { text: string; streaming?: boolean; cwd?: string; className?: string; baseDir?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const blocks = useMemo(() => splitBlocks(text), [text]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const pres = Array.from(el.querySelectorAll<HTMLPreElement>('pre.hl:not([data-hl])'));
    pres.forEach((pre, i) => {
      if (streaming && i === pres.length - 1 && !pre.closest('.md-b')?.nextElementSibling) return; // still being written
      const code = pre.textContent || '';
      pre.dataset.hl = 'pending';
      highlightHtml(code, pre.dataset.lang || null).then((h) => {
        if (h && pre.isConnected && pre.textContent === code) {
          const c = pre.querySelector('code');
          if (c) c.innerHTML = h;
          pre.dataset.hl = '1';
        }
      });
    });
    el.querySelectorAll('code:not([data-pr])').forEach((c) => {
      (c as HTMLElement).dataset.pr = '1';
      if (c.closest('pre')) return;
      const r = asPathRef(c.textContent || '');
      if (!r) return;
      c.classList.add('md-path');
      (c as HTMLElement).dataset.path = r.path;
      if (r.line) (c as HTMLElement).dataset.line = String(r.line);
      (c as HTMLElement).title = 'Open in Files';
    });
  }, [blocks, streaming]);

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
    <div ref={ref} className={`md ${className}${streaming ? ' streaming' : ''}`} onClick={onClick}>
      {blocks.map((b, i) => (
        <Block key={i} raw={b} open={streaming && i === blocks.length - 1} baseDir={baseDir} />
      ))}
    </div>
  );
});
