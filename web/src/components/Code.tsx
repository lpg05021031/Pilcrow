import { Fragment, memo, useEffect, useMemo, useState, type ReactNode } from 'react';
import { diffWordsWithSpace } from 'diff';
import { MessageSquarePlus, Trash, Check } from 'lucide-react';
import { highlightTokens, cachedTokens, type Tok } from '../lib/highlight';
import { parseAnsi } from '../lib/ansi';
import { langFromPath } from '../lib/format';
import type { DiffHunk, DiffLine } from '../lib/types';

type Range = [number, number];

function renderContent(text: string, toks?: Tok[] | null, ranges?: Range[] | null): ReactNode {
  if (!toks && !ranges?.length) return text || '​';
  const segs = toks && toks.length ? toks : [{ t: text } as Tok];
  const rs = ranges || [];
  const out: ReactNode[] = [];
  let pos = 0;
  let ri = 0;
  let k = 0;
  for (const tok of segs) {
    const s = tok.t;
    let start = 0;
    while (start < s.length) {
      const abs = pos + start;
      while (ri < rs.length && rs[ri][1] <= abs) ri++;
      let end = s.length;
      let marked = false;
      if (ri < rs.length) {
        const [a, b] = rs[ri];
        if (abs >= a) {
          marked = true;
          end = Math.min(s.length, b - pos);
        } else end = Math.min(s.length, a - pos);
      }
      const piece = s.slice(start, end);
      const span = (
        <span key={k++} style={tok.s as React.CSSProperties}>
          {piece}
        </span>
      );
      out.push(marked ? <mark key={k++}>{span}</mark> : span);
      start = end;
    }
    pos += s.length;
  }
  return out.length ? out : '​';
}

/** Token lines for a text, filled in once highlighting finishes. */
export function useTokens(text: string, lang: string | null, enabled = true) {
  const [toks, setToks] = useState<Tok[][] | null>(() => (enabled ? cachedTokens(text, lang) : null));
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const hit = cachedTokens(text, lang);
    if (hit) setToks(hit);
    else
      highlightTokens(text, lang).then((t) => {
        if (alive) setToks(t);
      });
    return () => {
      alive = false;
    };
  }, [text, lang, enabled]);
  return toks;
}

// ---------------------------------------------------------------- CodeView

export const CodeView = memo(function CodeView({ text, path, lang, startLine = 1, maxLines = 400, wrap = false, highlightLine }: { text: string; path?: string; lang?: string; startLine?: number; maxLines?: number; wrap?: boolean; highlightLine?: number }) {
  const [all, setAll] = useState(false);
  const language = lang || (path ? langFromPath(path) : 'text');
  const lines = useMemo(() => {
    const l = text.split('\n');
    if (l.length > 1 && l[l.length - 1] === '') l.pop();
    return l;
  }, [text]);
  const shown = all ? lines : lines.slice(0, maxLines);
  const toks = useTokens(text, language, lines.length < 20000);
  return (
    <div className={`code-view${wrap ? ' wrap' : ''}${lines.length > shown.length ? ' clipped' : ''}`}>
      <div className="code-grid" data-hl={toks ? '1' : '0'}>
        {shown.map((l, i) => (
          <Fragment key={i}>
            <span className={`ln${highlightLine === startLine + i ? ' hit' : ''}`}>{startLine + i}</span>
            <span className={`hl-line${highlightLine === startLine + i ? ' hit' : ''}`}>{renderContent(l, toks?.[i])}</span>
          </Fragment>
        ))}
      </div>
      {lines.length > shown.length && (
        <button className="code-more fade" onClick={() => setAll(true)}>
          Show all {lines.length} lines
        </button>
      )}
    </div>
  );
});

// ---------------------------------------------------------------- Ansi

export const AnsiText = memo(function AnsiText({ text }: { text: string }) {
  const segs = useMemo(() => parseAnsi(text), [text]);
  return (
    <>
      {segs.map((s, i) =>
        s.cls || s.style ? (
          <span key={i} className={s.cls} style={s.style}>
            {s.text}
          </span>
        ) : (
          <Fragment key={i}>{s.text}</Fragment>
        ),
      )}
    </>
  );
});

// ---------------------------------------------------------------- DiffView

interface Row {
  kind: 'hunk' | 'line' | 'split';
  hunk?: DiffHunk;
  hi?: number;
  line?: DiffLine;
  left?: DiffLine | null;
  right?: DiffLine | null;
}

function wordRanges(hunks: DiffHunk[]) {
  const map = new WeakMap<DiffLine, Range[]>();
  for (const h of hunks) {
    const ls = h.lines;
    for (let i = 0; i < ls.length; ) {
      if (ls[i].t !== '-') {
        i++;
        continue;
      }
      const dels: DiffLine[] = [];
      while (i < ls.length && ls[i].t === '-') dels.push(ls[i++]);
      const adds: DiffLine[] = [];
      while (i < ls.length && ls[i].t === '+') adds.push(ls[i++]);
      const n = Math.min(dels.length, adds.length);
      for (let k = 0; k < n; k++) {
        const a = dels[k].s;
        const b = adds[k].s;
        if (a.length > 2000 || b.length > 2000) continue;
        const parts = diffWordsWithSpace(a, b);
        const rd: Range[] = [];
        const ra: Range[] = [];
        let pa = 0;
        let pb = 0;
        let changed = 0;
        for (const p of parts) {
          const len = p.value.length;
          if (p.removed) {
            rd.push([pa, pa + len]);
            pa += len;
            changed += len;
          } else if (p.added) {
            ra.push([pb, pb + len]);
            pb += len;
            changed += len;
          } else {
            pa += len;
            pb += len;
          }
        }
        if (changed < (a.length + b.length) * 0.7) {
          map.set(dels[k], rd);
          map.set(adds[k], ra);
        }
      }
    }
  }
  return map;
}

function useHunkTokens(hunks: DiffHunk[], lang: string) {
  const [state, setState] = useState<Map<DiffLine, Tok[]>>(new Map());
  useEffect(() => {
    let alive = true;
    (async () => {
      const map = new Map<DiffLine, Tok[]>();
      for (const h of hunks) {
        const oldL = h.lines.filter((l) => l.t !== '+');
        const newL = h.lines.filter((l) => l.t !== '-');
        const [ot, nt] = await Promise.all([highlightTokens(oldL.map((l) => l.s).join('\n'), lang), highlightTokens(newL.map((l) => l.s).join('\n'), lang)]);
        if (!ot && !nt) return;
        oldL.forEach((l, i) => ot?.[i] && map.set(l, ot[i]));
        newL.forEach((l, i) => nt?.[i] && map.set(l, nt[i]));
      }
      if (alive) setState(map);
    })();
    return () => {
      alive = false;
    };
  }, [hunks, lang]);
  return state;
}

export interface DiffComment {
  key: string;
  text: string;
}

const NO_ANIM: React.CSSProperties = { animation: 'none' };

export const DiffView = memo(function DiffView({
  hunks,
  path,
  mode = 'unified',
  maxRows = 0,
  comments,
  onComment,
  numbers = true,
  wrap = false,
  dual = false,
}: {
  hunks: DiffHunk[];
  path?: string;
  mode?: 'unified' | 'split';
  maxRows?: number;
  comments?: Record<string, string>;
  onComment?: (key: string, text: string | null, line: DiffLine) => void;
  numbers?: boolean;
  wrap?: boolean;
  /** Separate old and new line-number columns (unified view), as in a code review. */
  dual?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const lang = path ? langFromPath(path) : 'text';
  const words = useMemo(() => wordRanges(hunks), [hunks]);
  const toks = useHunkTokens(hunks, lang);

  const rows = useMemo(() => {
    const out: Row[] = [];
    hunks.forEach((h, hi) => {
      if (hunks.length > 1 || h.oldStart > 1 || h.header) out.push({ kind: 'hunk', hunk: h, hi });
      if (mode === 'unified') {
        for (const l of h.lines) out.push({ kind: 'line', line: l });
        return;
      }
      const ls = h.lines;
      for (let i = 0; i < ls.length; ) {
        if (ls[i].t === ' ') {
          out.push({ kind: 'split', left: ls[i], right: ls[i] });
          i++;
          continue;
        }
        const dels: DiffLine[] = [];
        while (i < ls.length && ls[i].t === '-') dels.push(ls[i++]);
        const adds: DiffLine[] = [];
        while (i < ls.length && ls[i].t === '+') adds.push(ls[i++]);
        for (let k = 0; k < Math.max(dels.length, adds.length); k++) out.push({ kind: 'split', left: dels[k] || null, right: adds[k] || null });
      }
    });
    return out;
  }, [hunks, mode]);

  const two = dual && numbers && mode === 'unified';
  const limit = maxRows && !expanded ? maxRows : rows.length;
  const shown = rows.slice(0, limit);
  const keyOf = (l: DiffLine) => (l.t === '-' ? `old:${l.o}` : `new:${l.n}`);
  // The first rows write themselves in one after another (the stylesheet staggers on --i); the rest
  // simply appear, so a long diff never has hundreds of cells animating at once.
  const STAGGER = 40;
  const order = (i: number) => (i < STAGGER ? ({ '--i': i } as React.CSSProperties) : NO_ANIM);

  const commentBlock = (l: DiffLine, span: number) => {
    const key = keyOf(l);
    const existing = comments?.[key];
    if (editing !== key && !existing) return null;
    return (
      <div className="diff-comment-row" style={{ gridColumn: `1 / span ${span}` }}>
        {editing === key ? (
          <CommentEditor
            initial={existing || ''}
            onCancel={() => setEditing(null)}
            onSave={(text) => {
              onComment?.(key, text.trim() || null, l);
              setEditing(null);
            }}
          />
        ) : (
          <div className="diff-comment">
            <span className="grow">{existing}</span>
            <button className="icon-btn sm" title="Edit comment" onClick={() => setEditing(key)}>
              <MessageSquarePlus size={13} />
            </button>
            <button className="icon-btn sm" title="Delete comment" onClick={() => onComment?.(key, null, l)}>
              <Trash size={13} />
            </button>
          </div>
        )}
      </div>
    );
  };

  const gutterBtn = (l: DiffLine) =>
    onComment ? (
      <button className="diff-add-comment" title="Comment on this line" onClick={() => setEditing(keyOf(l))}>
        <MessageSquarePlus size={12} />
      </button>
    ) : null;

  return (
    <div className={`diff ${mode}${numbers ? '' : ' no-numbers'}${two ? ' dual' : ''}${wrap ? ' wrap' : ''}${rows.length > shown.length ? ' clipped' : ''}`}>
      <div className="diff-grid" data-hl={toks.size ? '1' : '0'}>
        {shown.map((r, i) => {
          if (r.kind === 'hunk') {
            const h = r.hunk!;
            const prev = r.hi! > 0 ? hunks[r.hi! - 1] : null;
            const gap = prev ? h.oldStart - (prev.oldStart + prev.oldLines) : h.oldStart - 1;
            return (
              <div key={i} className="diff-hunk" style={{ gridColumn: '1 / -1' }}>
                <span className="diff-hunk-dots">⋯</span>
                <span>{gap > 0 ? `${gap} unchanged line${gap > 1 ? 's' : ''}` : ''}</span>
                {h.header && <span className="diff-hunk-ctx mono ellipsis">{h.header}</span>}
              </div>
            );
          }
          if (r.kind === 'line') {
            const l = r.line!;
            const cls = l.t === '+' ? 'add' : l.t === '-' ? 'del' : 'ctx';
            const st = order(i);
            return (
              <Fragment key={i}>
                {two ? (
                  <>
                    <span className={`dn ${cls}`}>{l.t === '+' ? '' : l.o}</span>
                    <span className={`dn dn-new ${cls}`}>{l.t === '-' ? '' : l.n}</span>
                  </>
                ) : (
                  numbers && <span className={`dn ${cls}`}>{l.t === '-' ? l.o : l.n}</span>
                )}
                <span className={`ds ${cls}`}>
                  {gutterBtn(l)}
                  {l.t === ' ' ? ' ' : l.t === '-' ? '−' : '+'}
                </span>
                <span className={`dc ${cls} hl-line`} style={st}>{renderContent(l.s, toks.get(l), words.get(l))}</span>
                {commentBlock(l, two ? 4 : numbers ? 3 : 2)}
              </Fragment>
            );
          }
          const L = r.left;
          const R = r.right;
          const lc = !L ? 'void' : L.t === '-' ? 'del' : 'ctx';
          const rc = !R ? 'void' : R.t === '+' ? 'add' : 'ctx';
          const st = order(i);
          return (
            <Fragment key={i}>
              <span className={`dn ${lc}`}>{L?.o ?? ''}</span>
              <span className={`dc ${lc} hl-line`} style={st}>{L ? renderContent(L.s, toks.get(L), words.get(L)) : ''}</span>
              <span className={`dn ${rc}`}>
                {R && gutterBtn(R)}
                {R?.n ?? ''}
              </span>
              <span className={`dc ${rc} hl-line`} style={st}>{R ? renderContent(R.s, toks.get(R), words.get(R)) : ''}</span>
              {R && commentBlock(R, 4)}
              {L && L !== R && L.t === '-' && commentBlock(L, 4)}
            </Fragment>
          );
        })}
      </div>
      {rows.length > shown.length && (
        <button className="code-more fade" onClick={() => setExpanded(true)}>
          Show all {rows.length} lines
        </button>
      )}
    </div>
  );
});

function CommentEditor({ initial, onSave, onCancel }: { initial: string; onSave: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  return (
    <div className="comment-editor">
      <textarea
        className="textarea"
        autoFocus
        rows={2}
        value={text}
        placeholder="Leave a note for Claude about this line…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSave(text);
          if (e.key === 'Escape') {
            e.stopPropagation();
            onCancel();
          }
        }}
      />
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn sm ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn sm primary" onClick={() => onSave(text)}>
          <Check size={13} /> Save note
        </button>
      </div>
    </div>
  );
}

/**
 * Move pure insertions/deletions as far down as they can go, like git does. A line-diff often
 * splits "}\n\nfunction b() {\n}" as "+}" … " }"; sliding the block keeps the original "}" as context.
 */
function slideBlocks(src: string[]): string[] {
  const l = src.slice();
  let i = 0;
  while (i < l.length) {
    const t = l[i][0];
    if (t !== '+' && t !== '-') {
      i++;
      continue;
    }
    let j = i;
    while (j < l.length && l[j][0] === t) j++;
    const other = t === '+' ? '-' : '+';
    const mixed = (i > 0 && l[i - 1][0] === other) || (j < l.length && l[j][0] === other);
    if (!mixed) {
      while (j < l.length && l[j][0] === ' ' && l[j].slice(1) === l[i].slice(1)) {
        l[i] = ' ' + l[i].slice(1);
        l[j] = t + l[j].slice(1);
        i++;
        j++;
      }
    }
    i = j;
  }
  return l;
}

/** Build hunks from an Edit/Write tool's structuredPatch (lines prefixed with ' ', '+', '-'). */
export function hunksFromPatch(patch: { oldStart: number; newStart: number; oldLines: number; newLines: number; lines: string[] }[]): DiffHunk[] {
  return patch.map((p) => {
    let o = p.oldStart;
    let n = p.newStart;
    const lines: DiffLine[] = [];
    for (const raw of slideBlocks(p.lines)) {
      const t = raw[0];
      const s = raw.slice(1);
      if (t === '+') lines.push({ t: '+', s, n: n++ });
      else if (t === '-') lines.push({ t: '-', s, o: o++ });
      else if (t === '\\') continue;
      else lines.push({ t: ' ', s, o: o++, n: n++ });
    }
    return { oldStart: p.oldStart, oldLines: p.oldLines, newStart: p.newStart, newLines: p.newLines, lines };
  });
}
