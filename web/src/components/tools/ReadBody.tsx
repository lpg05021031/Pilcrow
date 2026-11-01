// A step card's body for Read.
import type { ToolItem } from '../../lib/transcript';
import { relPath } from '../../lib/format';
import { openFile } from '../../lib/store';
import { CodeView } from '../Code';
import { str } from '../../lib/tools';
import type { ToolCtx } from './ToolCard';
import { ResultText } from './Result';

function stripLineNumbers(text: string) {
  const lines = text.split('\n');
  if (lines.filter((l) => /^\s*\d+[\t→]/.test(l)).length < lines.length * 0.6) return { text, start: 1 };
  const m = lines[0].match(/^\s*(\d+)/);
  return { text: lines.map((l) => l.replace(/^\s*\d+[\t→]/, '')).join('\n'), start: m ? Number(m[1]) : 1 };
}

export function ReadBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const s = item.structured;
  const r = item.result;
  const path = str(item.input, 'file_path');
  let content = '';
  let start = 1;
  if (s?.file?.content !== undefined) {
    content = s.file.content || '';
    start = s.file.startLine || 1;
  } else if (r?.text) ({ text: content, start } = stripLineNumbers(r.text.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '').trimEnd()));
  if (r?.isError) return <ResultText item={item} />;
  if (s?.type === 'image' || r?.images.length) return <ResultText item={item} />;
  return (
    <div className="tool-section">
      <div className="tool-file-bar">
        <button className="link mono" onClick={() => openFile(path, start)}>
          {relPath(path, ctx.cwd)}
        </button>
        {/* Only worth saying when Claude read part of the file. */}
        {s?.file?.totalLines && s.file.numLines !== undefined && s.file.numLines < s.file.totalLines - 1 ? (
          <span className="faint tnum">
            Lines {start}–{start + s.file.numLines - 1} of {s.file.totalLines}
          </span>
        ) : null}
      </div>
      {content ? <CodeView text={content} path={path} startLine={start} maxLines={60} /> : <div className="faint">Waiting…</div>}
    </div>
  );
}
