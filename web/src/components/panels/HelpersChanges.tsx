import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { useStore, call, confirmAction, toast } from '../../lib/store';
import type { DiffFile, HelperState } from '../../lib/types';
import { errText, plural } from '../../lib/format';
import { DiffView } from '../Code';
import { Collapse } from '../ui/Collapse';

/**
 * The lead's helpers in the Changes panel (HELPERS.md §6.5): a row with Dispatch's finish actions —
 * Commit all, Open pull requests, Keep, Discard — and one section per helper worktree with its diff.
 */
const TERMINAL = new Set(['done', 'failed', 'stopped', 'skipped']);
const WORDS: Record<string, string> = { commit: 'Committed', pr: 'Pull requests opened', keep: 'Kept', discard: 'Discarded' };

export function HelpersChanges({ sessionId }: { sessionId: string }) {
  const helpers = useStore((s) => s.rt[sessionId]?.helpers);
  const [busy, setBusy] = useState(false);
  if (!helpers?.length) return null;
  const open = helpers.filter((h) => TERMINAL.has(h.state) && !h.finished && h.worktree);
  const done = open.filter((h) => h.state === 'done');
  const files = helpers.reduce((n, h) => n + (h.files || 0), 0);
  const finish = (action: 'commit' | 'pr' | 'keep' | 'discard') => {
    const targets = action === 'commit' || action === 'pr' ? done : open;
    if (!targets.length || busy) return;
    const names = targets.map((h) => h.name).join(', ');
    const go = async () => {
      setBusy(true);
      try {
        const r = await call<{ results: { name: string; ok: boolean; output: string }[] }>('helpers.finish', { leadId: sessionId, action });
        const bad = r.results.filter((x) => !x.ok);
        toast(bad.length ? `${WORDS[action]}: ${bad.map((x) => x.name).join(', ')} failed` : `${WORDS[action]} · ${names}`, bad.length ? 'error' : 'success');
      } catch (e) {
        toast(errText(e), 'error');
      } finally {
        setBusy(false);
      }
    };
    if (action === 'keep') return void go();
    const spec = {
      commit: { title: `Commit ${plural(targets.length, 'helper')}?`, body: `Each helper's changes are committed on its own branch; the worktree folder is removed and the branch kept. Helpers: ${names}.`, confirm: 'Commit all', danger: false },
      pr: { title: `Open ${plural(targets.length, 'pull request')}?`, body: `Each helper's changes are committed, pushed, and a pull request is opened through gh; the pull requests link to each other. Helpers: ${names}.`, confirm: 'Open pull requests', danger: false },
      discard: { title: `Discard the work of ${plural(targets.length, 'helper')}?`, body: `The worktrees and their branches are removed; nothing is kept. Helpers: ${names}.`, confirm: 'Discard', danger: true },
    }[action];
    confirmAction({ ...spec, onConfirm: go });
  };
  return (
    <div className="hc">
      <div className="hc-row">
        <span className="faint small grow">
          {plural(helpers.length, 'helper')} · {plural(files, 'file')}
          {open.length ? ` · ${open.length} to finish` : ''}
        </span>
        {done.length > 0 && (
          <button className="btn sm" disabled={busy} onClick={() => finish('commit')} title="A commit in each helper's worktree, on its branch">
            Commit all…
          </button>
        )}
        {done.length > 0 && (
          <button className="btn sm ghost" disabled={busy} onClick={() => finish('pr')} title="Commit, push and open a pull request for each, through gh">
            Open pull requests…
          </button>
        )}
        {open.length > 0 && (
          <button className="btn sm ghost" disabled={busy} onClick={() => finish('keep')} title="Leave the worktrees as they are">
            Keep
          </button>
        )}
        {open.length > 0 && (
          <button className="btn sm ghost" disabled={busy} onClick={() => finish('discard')} title="Remove the worktrees and their branches">
            Discard…
          </button>
        )}
      </div>
      {helpers.map((h) => (
        <HelperSection key={h.id} h={h} />
      ))}
    </div>
  );
}

function HelperSection({ h }: { h: HelperState }) {
  const [open, setOpen] = useState(true);
  const [files, setFiles] = useState<DiffFile[] | null>(null);
  const sig = `${h.state}:${h.files}:${h.add}:${h.del}:${h.finished || ''}`;
  useEffect(() => {
    if (!h.worktree || h.finished === 'commit' || h.finished === 'discard') {
      setFiles([]);
      return;
    }
    let alive = true;
    call<{ files?: DiffFile[] }>('git.diff', { cwd: h.worktree }, { quiet: true })
      .then((r) => alive && setFiles(r?.files || []))
      .catch(() => alive && setFiles([]));
    return () => {
      alive = false;
    };
  }, [h.worktree, h.finished, sig]);
  const note = !h.worktree ? 'No worktree.' : h.finished === 'commit' ? `Committed on its branch${h.commit ? ` (${h.commit})` : ''}.` : h.finished === 'discard' ? 'Discarded.' : files === null ? 'Loading…' : files.length ? '' : TERMINAL.has(h.state) ? 'No changes.' : 'No changes yet.';
  return (
    <div className="hc-sec">
      <div className="hc-head">
        <button className="icon-btn sm" onClick={() => setOpen(!open)} title={open ? 'Fold' : 'Unfold'}>
          <ChevronRight size={13} className={open ? 'rot' : ''} />
        </button>
        <span className={`dp-glyph ${h.state}`} aria-hidden="true" />
        <span className="hc-name">{h.name}</span>
        <span className="faint small">{h.state.replace('_', ' ')}{h.reason && h.state !== 'done' ? ` · ${h.reason}` : ''}</span>
        <span className="mono faint small grow ellipsis">{h.branch || ''}</span>
        <span className={`num-add${h.add ? '' : ' zero'}`}>+{h.add}</span>
        <span className={`num-del${h.del ? '' : ' zero'}`}>−{h.del}</span>
        {h.finished && <span className="chip tiny">{h.finished === 'commit' ? `committed ${h.commit || ''}` : h.finished}</span>}
      </div>
      <Collapse open={open}>
        {() => (
          <div className="hc-body">
            {note ? <div className="faint small hc-note">{note}</div> : null}
            {(files || []).map((f) => (
              <div key={f.path} className="hc-file">
                <div className="hc-file-head">
                  <span className="mono">{f.path}</span>
                  <span className={`num-add${f.additions ? '' : ' zero'}`}>+{f.additions}</span>
                  <span className={`num-del${f.deletions ? '' : ' zero'}`}>−{f.deletions}</span>
                </div>
                {f.binary ? <div className="faint small">Binary file</div> : <DiffView hunks={f.hunks} path={f.path} wrap maxRows={300} />}
              </div>
            ))}
          </div>
        )}
      </Collapse>
    </div>
  );
}
