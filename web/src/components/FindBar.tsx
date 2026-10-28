import { useCallback, useEffect, useRef, useState } from 'react';
import { Search, ChevronUp, ChevronDown, X } from 'lucide-react';
import { useExitGhost } from '../lib/motion';

const supported = typeof CSS !== 'undefined' && 'highlights' in CSS;
let lastQuery = '';

/**
 * Find in the conversation. Matches are painted with the CSS Custom Highlight API, so the transcript's
 * DOM is never rewritten and streaming keeps working underneath. The list refreshes as new output
 * arrives; Enter / Shift+Enter step through matches and the current one is brought into view.
 */
export function FindBar({ root, onClose }: { root: () => HTMLElement | null; onClose: () => void }) {
  const [q, setQ] = useState(lastQuery);
  const [count, setCount] = useState(0);
  const [index, setIndex] = useState(0);
  const ranges = useRef<Range[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const indexRef = useRef(0);
  indexRef.current = index;
  const box = useRef<HTMLDivElement>(null);
  useExitGhost(box, { duration: 160 });

  const paintCurrent = useCallback((i: number, reveal: boolean) => {
    const r = ranges.current[i];
    const hl = CSS.highlights;
    if (!r) {
      hl.delete('find-current');
      return;
    }
    hl.set('find-current', new Highlight(r));
    if (!reveal) return;
    const scroller = (r.startContainer.parentElement as HTMLElement | null)?.closest('.transcript') as HTMLElement | null;
    if (!scroller) return;
    let box = r.getBoundingClientRect();
    if (!box.width && !box.height) {
      // A match in a row far off screen has no layout yet (content-visibility): bring the row in
      // first, which lays it out, then place the match.
      (r.startContainer.parentElement as HTMLElement | null)?.scrollIntoView({ block: 'center' });
      box = r.getBoundingClientRect();
    }
    const view = scroller.getBoundingClientRect();
    if (box.top < view.top + 56 || box.bottom > view.bottom - 24) {
      scroller.scrollTo({ top: scroller.scrollTop + (box.top - view.top) - view.height * 0.38, behavior: 'smooth' });
    }
  }, []);

  const search = useCallback(
    (keepIndex: boolean) => {
      const el = root();
      const hl = CSS.highlights;
      const found: Range[] = [];
      const needle = q.trim().toLocaleLowerCase();
      if (el && needle) {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
          acceptNode: (n) => (n.parentElement?.closest('.findbar, script, style') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
        });
        for (let n = walker.nextNode(); n && found.length < 3000; n = walker.nextNode()) {
          const t = (n.nodeValue || '').toLocaleLowerCase();
          for (let i = t.indexOf(needle); i >= 0; i = t.indexOf(needle, i + needle.length)) {
            const r = new Range();
            r.setStart(n, i);
            r.setEnd(n, i + needle.length);
            found.push(r);
          }
        }
      }
      ranges.current = found;
      setCount(found.length);
      if (found.length) hl.set('find', new Highlight(...found));
      else hl.delete('find');
      const next = keepIndex ? Math.min(indexRef.current, Math.max(0, found.length - 1)) : nearest(found);
      setIndex(next);
      paintCurrent(next, !keepIndex);
    },
    [q, root, paintCurrent],
  );

  // Search as you type, and again whenever the conversation changes underneath.
  useEffect(() => {
    lastQuery = q;
    search(false);
    const el = root();
    if (!el) return;
    let t: number | undefined;
    const mo = new MutationObserver(() => {
      window.clearTimeout(t);
      t = window.setTimeout(() => search(true), 250);
    });
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      mo.disconnect();
      window.clearTimeout(t);
    };
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    input.current?.select();
    return () => {
      CSS.highlights?.delete('find');
      CSS.highlights?.delete('find-current');
    };
  }, []);

  const step = (d: number) => {
    if (!count) return;
    const i = (index + d + count) % count;
    setIndex(i);
    paintCurrent(i, true);
  };

  if (!supported) return null;
  return (
    <div className="findbar" role="search" ref={box}>
      <Search size={14} className="findbar-ico" />
      <input
        ref={input}
        autoFocus
        value={q}
        placeholder="Find in conversation"
        spellCheck={false}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            step(e.shiftKey ? -1 : 1);
          } else if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }}
      />
      <span className={`findbar-count tnum${q.trim() && !count ? ' none' : ''}`}>{q.trim() ? (count ? `${index + 1} of ${count}` : 'No matches') : ''}</span>
      <button className="icon-btn sm" title="Previous match (Shift+Enter)" disabled={!count} onClick={() => step(-1)}>
        <ChevronUp size={14} />
      </button>
      <button className="icon-btn sm" title="Next match (Enter)" disabled={!count} onClick={() => step(1)}>
        <ChevronDown size={14} />
      </button>
      <button className="icon-btn sm" title="Close (Esc)" onClick={onClose}>
        <X size={14} />
      </button>
    </div>
  );
}

/** Start from the first match below the top of the visible part of the conversation. */
function nearest(found: Range[]) {
  if (!found.length) return 0;
  const scroller = (found[0].startContainer.parentElement as HTMLElement | null)?.closest('.transcript');
  const top = scroller ? scroller.getBoundingClientRect().top + 40 : 0;
  const i = found.findIndex((r) => r.getBoundingClientRect().top >= top);
  return i < 0 ? found.length - 1 : i;
}
