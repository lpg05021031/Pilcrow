// A step card's body for Edit / Write.
import { useMemo } from 'react';
import { structuredPatch } from 'diff';
import type { ToolItem } from '../../lib/transcript';
import { CodeView, DiffView, hunksFromPatch } from '../Code';
import { str } from '../../lib/tools';
import type { PatchHunk, DiffHunk } from '../../lib/types';
import { LruBytes } from '../../lib/cache';
import type { ToolCtx } from './ToolCard';
import { ResultText } from './Result';

// An edit's diff by its two strings, so a card drawn again costs nothing. The hunks hold the strings'
// lines again, with a little for each line: twice the key is a fair measure.
const hunkCache = new LruBytes<DiffHunk[]>(4 << 20, (k) => k.length * 2);
export function computeHunks(oldS: string, newS: string) {
  const key = oldS + '\u0000' + newS;
  const hit = hunkCache.get(key);
  if (hit) return hit;
  const p = structuredPatch('a', 'b', oldS, newS, '', '', { context: 3 });
  const h = hunksFromPatch(p.hunks as PatchHunk[]);
  hunkCache.set(key, h);
  return h;
}

/**
 * Text still being written into an Edit or Write is shown plain: coloured, each streamed piece would
 * have the whole draft tokenised again (a 60 kB file, every frame). It is coloured once, when complete.
 */
const DRAFT = 'text';

export function EditBody({ item }: { item: ToolItem; ctx: ToolCtx }) {
  const i = item.input || {};
  const s = item.structured;
  const path = str(i, 'file_path');
  const hunks = useMemo(() => {
    if (Array.isArray(s?.structuredPatch) && s.structuredPatch.length) return { hunks: hunksFromPatch(s.structuredPatch), numbered: true };
    if (Array.isArray(i.edits)) return { hunks: (i.edits as { old_string?: string; new_string?: string }[]).flatMap((e) => computeHunks(String(e.old_string ?? ''), String(e.new_string ?? ''))), numbered: false };
    if (i.old_string !== undefined || i.new_string !== undefined) return { hunks: computeHunks(String(i.old_string ?? ''), String(i.new_string ?? '')), numbered: false };
    return { hunks: [], numbered: false };
  }, [s, i.old_string, i.new_string, i.edits]);
  // While the edit is still arriving, a diff against half a replacement would show phantom deletions.
  // Show the new text as it is written instead, plain (DRAFT), and the real diff once the edit is complete.
  if (item.status === 'streaming') {
    const edits = Array.isArray(i.edits) ? (i.edits as { new_string?: string }[]) : null;
    const draft = typeof i.new_string === 'string' ? i.new_string : edits ? String(edits[edits.length - 1]?.new_string ?? '') : '';
    return (
      <div className="tool-section">
        <div className="tool-note shimmer-soft">Writing the change…</div>
        {draft ? <CodeView text={draft} lang={DRAFT} maxLines={16} /> : null}
      </div>
    );
  }
  return (
    <div className="tool-section">
      {Boolean(i.replace_all) && <div className="tool-note">Replaced every occurrence</div>}
      {hunks.hunks.length ? <DiffView hunks={hunks.hunks} path={path} numbers={hunks.numbered} maxRows={16} /> : null}
      {item.result?.isError && <ResultText item={item} />}
    </div>
  );
}

export function WriteBody({ item }: { item: ToolItem; ctx: ToolCtx }) {
  const i = item.input || {};
  const s = item.structured;
  const path = str(i, 'file_path');
  const patch = s?.type === 'update' && Array.isArray(s.structuredPatch) && s.structuredPatch.length ? s.structuredPatch : null;
  const hunks = useMemo(() => (patch ? hunksFromPatch(patch) : null), [patch]);
  return (
    <div className="tool-section">
      {hunks ? <DiffView hunks={hunks} path={path} maxRows={16} /> : typeof i.content === 'string' ? <CodeView text={i.content} path={path} lang={item.inputDone ? undefined : DRAFT} maxLines={16} /> : null}
      {item.result?.isError && <ResultText item={item} />}
    </div>
  );
}
