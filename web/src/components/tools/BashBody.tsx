// A step card's body for Bash.
import { useState } from 'react';
import { FileText } from 'lucide-react';
import type { ToolItem } from '../../lib/transcript';
import { openFile } from '../../lib/store';
import { CopyButton } from '../ui/clipboard';
import { AnsiText } from '../Code';
import { str } from '../../lib/tools';
import type { ToolCtx } from './ToolCard';
import { unwrapError } from './Result';

export function BashBody({ item, ctx }: { item: ToolItem; ctx: ToolCtx }) {
  const waiting = ctx.pending.has(item.id);
  const i = item.input || {};
  const s = item.structured && typeof item.structured === 'object' ? item.structured : null;
  const r = item.result;
  const [full, setFull] = useState(false);
  let out = '';
  if (s) out = [s.stdout, s.stderr].filter(Boolean).join(s.stdout && s.stderr ? '\n' : '');
  if (!out && r) out = r.isError ? unwrapError(r.text) : r.text;
  const exit = r?.isError ? (r.text.match(/^(?:Error: )?Exit code (\d+)/) || [])[1] : null;
  if (exit && out.startsWith('Exit code')) out = out.replace(/^Exit code \d+\n?/, '');
  if (exit && out.startsWith('Error: Exit code')) out = out.replace(/^Error: Exit code \d+\n?/, '');
  const lines = out.split('\n');
  const long = lines.length > 18 && !full;
  return (
    <div className="bash">
      <div className="bash-cmd">
        <span className="bash-prompt">$</span>
        <code className="grow">{str(i, 'command')}</code>
        <CopyButton text={str(i, 'command')} title="Copy command" size={12} />
      </div>
      {item.status === 'running' && !r && !waiting && <div className="bash-running faint">{`Running${item.elapsed ? ` · ${Math.round(item.elapsed)}s` : '…'}`}</div>}
      {out && (
        <pre className={`bash-out${r?.isError ? ' err' : ''}`}>
          <AnsiText text={long ? lines.slice(-18).join('\n') : out} />
        </pre>
      )}
      {long && (
        <button className="code-more" onClick={() => setFull(true)}>
          Show all {lines.length} lines
        </button>
      )}
      {(exit || s?.backgroundTaskId || s?.interrupted || s?.returnCodeInterpretation) && (
        <div className="bash-foot">
          {exit && <span className="badge err">exit {exit}</span>}
          {s?.interrupted && <span className="badge">interrupted</span>}
          {s?.returnCodeInterpretation && <span className="faint">{s.returnCodeInterpretation}</span>}
          {s?.backgroundTaskId && (
            <span className="faint">
              Background task <code>{s.backgroundTaskId}</code>
              {item.task?.status ? ` · ${item.task.status}` : ''}
            </span>
          )}
          {item.task?.outputFile && (
            <button className="btn sm ghost" onClick={() => item.task?.outputFile && openFile(item.task.outputFile)}>
              <FileText size={12} /> Output file
            </button>
          )}
        </div>
      )}
      {s?.gitOperation?.commit && <div className="bash-foot faint">Committed {String(s.gitOperation.commit).slice(0, 8)}</div>}
    </div>
  );
}
