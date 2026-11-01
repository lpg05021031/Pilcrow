import { useEffect, useRef, useState } from 'react';
import { Search, ChevronUp, ChevronDown, X } from 'lucide-react';
import { useExitGhost } from '../lib/motion';
import { useStore, setFindQuery, models } from '../lib/store';
import { findInItems, type FindHit } from '../lib/transcript';
import { revealItem } from './Transcript';
import { useEvent } from './ui/hooks';

const supported = typeof CSS !== 'undefined' && 'highlights' in CSS;

/** How often the matches are looked for again while the conversation changes underneath (streaming, pages drawn). */
const AGAIN_MS = 250;

/** The element an item is drawn in, when it is drawn. */
const domIdOf = (h: FindHit) => (h.item.kind === 'text' ? `text-${h.item.id}` : h.item.uuid ? `msg-${h.item.uuid}` : '');

/** The text of a drawn item, matched case-insensitively, as ranges in reading order (its controls left out). */
function rangesIn(el: HTMLElement, needle: string): Range[] {
  const out: Range[] = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest('.md-code-head, .msg-actions, .msg-queued') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.nodeValue || '').toLocaleLowerCase();
    for (let i = t.indexOf(needle); i >= 0; i = t.indexOf(needle, i + needle.length)) {
      const r = new Range();
      r.setStart(n, i);
      r.setEnd(n, i + needle.length);
      out.push(r);
    }
  }
  return out;
}

/**
 * Find in the conversation. The matches are found in the transcript model (lib/transcript
 * findInItems), so nothing has to be drawn to be counted; stepping to one reveals it (revealItem
 * draws its page), and only what is drawn is painted, with the CSS Custom Highlight API, so the
 * transcript's DOM is never rewritten and streaming keeps working underneath. The matches are looked
 * for again as output arrives and as pages are drawn; Enter / Shift+Enter step through them.
 */
export function FindBar({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [q, setQ] = useState(() => useStore.getState().findQuery);
  const [hits, setHits] = useState<FindHit[]>([]);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  useExitGhost(box, { duration: 160 });
  const needle = q.trim().toLocaleLowerCase();
  const rev = useStore((s) => s.rt[sessionId]?.rev || 0);
  const at = useRef({ hits, index, needle });
  at.current = { hits, index, needle };

  /** Paint the matches that are drawn, and the current one; with `reveal`, bring the current one into view first. */
  const paint = useEvent(async (reveal: boolean) => {
    const { hits, index, needle } = at.current;
    const hl = CSS.highlights;
    const cur = hits[index];
    let el = cur ? document.getElementById(domIdOf(cur)) : null;
    if (cur && !el && reveal) el = await revealItem(sessionId, domIdOf(cur), cur.item.id);
    if (at.current.hits !== hits || at.current.index !== index) return; // moved on meanwhile
    const all: Range[] = [];
    const seen = new Set<string>();
    let current: Range | undefined;
    for (const h of hits) {
      const id = domIdOf(h);
      if (seen.has(id)) continue;
      seen.add(id);
      const e = id ? document.getElementById(id) : null;
      if (!e) continue;
      const rs = rangesIn(e, needle);
      all.push(...rs);
      if (h.item === cur?.item) current = rs[Math.min(cur.k, rs.length - 1)];
    }
    if (all.length) hl.set('find', new Highlight(...all));
    else hl.delete('find');
    if (current) hl.set('find-current', new Highlight(current));
    else hl.delete('find-current');
    if (reveal && el) bringIntoView(el, current);
  });

  // Search as you type: from the match nearest the top of the view.
  useEffect(() => {
    setFindQuery(q);
    const found = findInItems(models.get(sessionId)?.items || [], needle);
    at.current = { hits: found, index: nearest(found, needle), needle };
    setHits(found);
    setIndex(at.current.index);
    paint(true);
  }, [q, needle, sessionId, paint]);

  // Again as the conversation grows (at most every AGAIN_MS, however fast it streams), keeping the place.
  const timer = useRef<number | undefined>(undefined);
  const again = useRef(() => {});
  again.current = () => {
    const { needle, index } = at.current;
    const found = findInItems(models.get(sessionId)?.items || [], needle);
    const i = Math.min(index, Math.max(0, found.length - 1));
    at.current = { hits: found, index: i, needle };
    setHits(found);
    setIndex(i);
    paint(false);
  };
  const soon = (fn: () => void) => {
    if (timer.current === undefined)
      timer.current = window.setTimeout(() => {
        timer.current = undefined;
        fn();
      }, AGAIN_MS);
  };
  useEffect(() => {
    if (at.current.needle) soon(() => again.current());
  }, [rev]);
  // Pages drawn as the reader scrolls, folds opened: only the painting changes.
  useEffect(() => {
    const root = document.querySelector('.session .transcript-inner');
    if (!root) return;
    const mo = new MutationObserver(() => at.current.needle && soon(() => again.current()));
    mo.observe(root, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      window.clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    input.current?.select();
    return () => {
      CSS.highlights?.delete('find');
      CSS.highlights?.delete('find-current');
    };
  }, []);

  const count = hits.length;
  const step = (d: number) => {
    if (!count) return;
    const i = (index + d + count) % count;
    at.current = { ...at.current, index: i };
    setIndex(i);
    paint(true);
  };

  if (!supported) return null;
  return (
    <div className="findbar" role="search" ref={box}>
      <Search size={14} className="findbar-ico" aria-hidden="true" />
      <input
        ref={input}
        autoFocus
        aria-label="Find in the conversation"
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
      <span className={`findbar-count tnum${needle && !count ? ' none' : ''}`}>{needle ? (count ? `${index + 1} of ${count}` : 'No matches') : ''}</span>
      <button className="icon-btn sm" title="Previous match (Shift+Enter)" aria-label="Previous match" disabled={!count} onClick={() => step(-1)}>
        <ChevronUp size={14} />
      </button>
      <button className="icon-btn sm" title="Next match (Enter)" aria-label="Next match" disabled={!count} onClick={() => step(1)}>
        <ChevronDown size={14} />
      </button>
      <button className="icon-btn sm" title="Close (Esc)" aria-label="Close find" onClick={onClose}>
        <X size={14} />
      </button>
    </div>
  );
}

/** Scroll the conversation so the match (or, without one, its row) sits a little above the middle. */
function bringIntoView(el: HTMLElement, r: Range | undefined) {
  const scroller = el.closest<HTMLElement>('.transcript');
  if (!scroller) return;
  let box = (r || el).getBoundingClientRect();
  if (!box.width && !box.height) {
    // A row far off screen has no layout yet (content-visibility): bring it in first, which lays it out.
    el.scrollIntoView({ block: 'center' });
    box = (r || el).getBoundingClientRect();
  }
  const view = scroller.getBoundingClientRect();
  if (box.top >= view.top + 56 && box.bottom <= view.bottom - 24) return;
  // A short way glides; a long one jumps, since gliding past the history would draw it as it went.
  const by = box.top - view.top - view.height * 0.38;
  scroller.scrollTo({ top: scroller.scrollTop + by, behavior: Math.abs(by) > view.height * 2 ? 'instant' : 'smooth' });
}

/**
 * Start from the first match at or below the top of the view. The rows in view are drawn, so their
 * matches are in the DOM; when none is below, the next one after the last drawn one above, which the
 * reader at the end of a conversation, scrolled past everything drawn, reads as the latest.
 */
function nearest(hits: FindHit[], needle: string) {
  if (!hits.length) return 0;
  const scroller = document.querySelector('.session .transcript');
  const top = scroller ? scroller.getBoundingClientRect().top + 40 : 0;
  let lastAbove = -1;
  for (let i = 0; i < hits.length; i++) {
    const el = document.getElementById(domIdOf(hits[i]));
    if (!el) continue;
    const r = rangesIn(el, needle)[hits[i].k] || el;
    if (r.getBoundingClientRect().top >= top) return i;
    lastAbove = i;
  }
  return lastAbove < 0 ? hits.length - 1 : Math.min(lastAbove + 1, hits.length - 1);
}
