// A step card's body for Grep / Glob.
import type { ToolItem } from '../../lib/transcript';
import { relPath, plural, truncate } from '../../lib/format';
import { openFile } from '../../lib/store';
import type { ToolCtx } from './ToolCard';
import { ResultText } from './Result';

export function SearchBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const s = item.structured;
  const r = item.result;
  if (r?.isError) return <ResultText item={item} />;
  const files: string[] = s?.filenames || [];
  if (typeof s?.content === 'string' && s.content) return <pre className="tool-pre">{truncate(s.content, 40000)}</pre>;
  if (!files.length) return r?.text ? <pre className="tool-pre">{truncate(r.text, 40000)}</pre> : <div className="faint tool-wait">{r ? 'No matches' : 'Searching…'}</div>;
  return (
    <div className="file-list">
      <div className="faint">{plural(s?.numFiles ?? files.length, 'file')}{s?.truncated ? ' (truncated)' : ''}</div>
      {files.slice(0, 200).map((f) => (
        <button key={f} className="file-list-item mono" onClick={() => openFile(f.startsWith('/') ? f : `${ctx.cwd}/${f}`)}>
          {relPath(f, ctx.cwd)}
        </button>
      ))}
    </div>
  );
}
