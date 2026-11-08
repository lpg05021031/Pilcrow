import { useEffect, useMemo, useRef, useState } from 'react';
import { Search, ChevronUp, ChevronDown, X } from 'lucide-react';
import { useExitGhost } from '../lib/motion';
import { useStore, setFindQuery, models } from '../lib/store';
import { renderMarkdown } from '../lib/markdown';
import { findInItems, findText, type FindHit, type Item } from '../lib/transcript';
import { revealItem } from './Transcript';
import { REVEAL_EVENT } from './useFollow';
import { useEvent } from './ui/hooks';

const supported = typeof CSS !== 'undefined' && 'highlights' in CSS;

/** How often the matches are looked for again while the conversation changes underneath (streaming, pages drawn). */
const AGAIN_MS = 250;

/** The element an item is drawn in, when it is drawn (TranscriptRows, ToolCard). */
function domIdOf(h: FindHit): string {
  const it = h.item;
  switch (it.kind) {
    case 'text':
      return `text-${it.id}`;
    case 'user':
      return `msg-${it.uuid || it.id}`;
    case 'tool':
      return `tool-${it.id}`;
    case 'notice':
      return `notice-${it.id}`;
    case 'command-output':
      return `co-${it.id}`;
    default:
      return '';
  }
}

// The parts of a row that are its controls, not its text: never matched, in the DOM or in the model.
const NOT_TEXT = '.md-code-head, .msg-actions, .msg-queued, .tool-meta, .tool-open, .tool-ico, button.link';

/** Markdown as it reads: rendered, its code heads left out, as text (what rangesIn walks once drawn). */
function plain(md: string): string {
  const t = document.createElement('template');
  t.innerHTML = renderMarkdown(md);
  t.content.querySelectorAll('.md-code-head').forEach((n) => n.remove());
  return t.content.textContent || '';
}

/** A row's whole text is drawn: a step only while it is open (closed, only its head is). */
const drawnWhole = (it: Item, el: HTMLElement) => it.kind !== 'tool' || el.classList.contains('open');

/** The text of a drawn item, matched case-insensitively, as ranges in reading order (its controls left out). */
function rangesIn(el: HTMLElement, needle: string): Range[] {
  const out: Range[] = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest(NOT_TEXT) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
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

  const cwd = useStore((s) => s.sessions.find((x) => x.id === sessionId)?.cwd);
  /** The model's matches: every row as it reads (findText), Markdown as rendered. */
  const textOf = useMemo(() => (it: Item) => findText(it, plain, cwd), [cwd]); // stable, so the model caches each row's text
  const search = useEvent((needle: string) => findInItems(models.get(sessionId)?.items || [], needle, 3000, textOf));

  /**
   * Paint the matches that are drawn, and the current one; with `reveal`, bring the current one into
   * view first (its page drawn, its fold and, for a match in a step's output, its card opened). A row
   * that is drawn whole is the truth: when it shows a different number of matches than the model
   * counted (a word split by formatting, text the model reads differently), the list follows it, so
   * the count, the steps and the paint agree.
   */
  const paint = useEvent(async (reveal: boolean, again = true): Promise<void> => {
    let { hits, index } = at.current;
    const { needle } = at.current;
    const hl = CSS.highlights;
    const cur = hits[index];
    let el = cur ? document.getElementById(domIdOf(cur)) : null;
    if (cur && !el && reveal) el = await revealItem(sessionId, domIdOf(cur), cur.item.id);
    if (at.current.hits !== hits || at.current.index !== index) return; // moved on meanwhile
    // a match in a closed step's output: open the card, then paint once it is drawn
    if (reveal && again && cur && el && !drawnWhole(cur.item, el) && cur.k >= rangesIn(el, needle).length) {
      el.querySelector<HTMLElement>('.tool-toggle')?.click();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      if (at.current.hits === hits && at.current.index === index) return paint(true, false);
      return;
    }
    const all: Range[] = [];
    const drawn = new Map<Item, Range[]>();
    for (const h of hits) {
      if (drawn.has(h.item)) continue;
      const id = domIdOf(h);
      const e = id ? document.getElementById(id) : null;
      if (!e) continue;
      const rs = rangesIn(e, needle);
      drawn.set(h.item, rs);
      all.push(...rs);
    }
    // the drawn rows correct the model's counts
    let fixed = false;
    const next: FindHit[] = [];
    for (let i = 0; i < hits.length; ) {
      const item = hits[i].item;
      let n = 0;
      while (i + n < hits.length && hits[i + n].item === item) n++;
      const rs = drawn.get(item);
      const e = rs ? document.getElementById(domIdOf(hits[i])) : null;
      const shown = rs && e && drawnWhole(item, e) ? rs.length : n;
      if (shown !== n) fixed = true;
      for (let k = 0; k < shown; k++) next.push(k < n ? hits[i + k] : { item, k });
      i += n;
    }
    if (fixed) {
      const keep = cur ? next.findIndex((h) => h.item === cur.item && h.k === Math.min(cur.k, (drawn.get(cur.item)?.length || 1) - 1)) : -1;
      hits = next;
      index = keep >= 0 ? keep : Math.min(index, Math.max(0, next.length - 1));
      at.current = { hits, index, needle };
      setHits(hits);
      setIndex(index);
    }
    const now = hits[index];
    const current = now ? drawn.get(now.item)?.[now.k] : undefined;
    if (all.length) hl.set('find', new Highlight(...all));
    else hl.delete('find');
    if (current) hl.set('find-current', new Highlight(current));
    else hl.delete('find-current');
    if (reveal && el) bringIntoView(el, current);
  });

  // Search as you type: from the match nearest the top of the view.
  useEffect(() => {
    setFindQuery(q);
    const found = search(needle);
    at.current = { hits: found, index: nearest(found, needle), needle };
    setHits(found);
    setIndex(at.current.index);
    paint(true);
  }, [q, needle, sessionId, paint, search]);

  // Again as the conversation grows (at most every AGAIN_MS, however fast it streams), keeping the place.
  const timer = useRef<number | undefined>(undefined);
  const again = useRef(() => {});
  again.current = () => {
    const { needle, index } = at.current;
    const found = search(needle);
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
  // the reader asked for this place: the conversation stops following its live end, or the next
  // streamed line (or a code block coloured) would pull the view back down (useFollow)
  scroller.dispatchEvent(new Event(REVEAL_EVENT));
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
