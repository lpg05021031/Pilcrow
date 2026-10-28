import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw, Rows2, Columns2, ChevronRight, Undo2, FileText, Send, GitCommitHorizontal, MessageSquareText, Eye, CircleCheck, GitPullRequestArrow } from 'lucide-react';
import { useStore, call, setUi, openFile, confirmAction, sendMessage, toast, insertIntoComposer, models, bumpFs } from '../../lib/store';
import { Select } from '../ui/Select';
import type { DiffFile, DiffLine, SessionSummary } from '../../lib/types';
import { basename, dirname, plural } from '../../lib/format';
import { DiffView, hunksFromPatch } from '../Code';
import { Collapse } from '../ui/Collapse';
import { Spinner, Seg } from '../ui/bits';
import { FileTouches, useFileHistory } from '../FileTouches';
import { str } from '../../lib/tools';
import { errText } from '../../lib/format';

interface DiffResult {
  isRepo: boolean;
  root?: string;
  base?: string;
  branch?: string | null;
  files: DiffFile[];
  error?: string | null;
}

// Review notes survive tab switches: sessionId -> path -> key -> note
const notesStore = new Map<string, Record<string, Record<string, { text: string; line: string; lineNo: number }>>>();

const STATUS_LABEL: Record<string, string> = { M: 'Modified', A: 'Added', D: 'Deleted', R: 'Renamed' };

export function ChangesPanel({ session, cwd }: { session: SessionSummary; cwd: string }) {
  const rt = useStore((s) => s.rt[session.id]);
  const mode = useStore((s) => s.ui.diffMode);
  const [data, setData] = useState<DiffResult | null>(null);
  const history = useFileHistory(cwd);
  const [loading, setLoading] = useState(false);
  const [base, setBase] = useState<string>(session.worktree?.base || 'HEAD');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState(() => notesStore.get(session.id) || {});
  const [commitOpen, setCommitOpen] = useState(false);
  const [message, setMessage] = useState('');
  const lastLoad = useRef(0);
  // Side-by-side needs room; fall back to unified while the panel is narrow.
  const box = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const viewMode = mode === 'split' && narrow ? 'unified' : mode;

  useEffect(() => {
    notesStore.set(session.id, notes);
  }, [notes, session.id]);

  const load = useCallback(async () => {
    setLoading(true);
    lastLoad.current = Date.now();
    try {
      const r = await call<DiffResult>('git.diff', { cwd, base: base === 'HEAD' ? undefined : base }, { quiet: true });
      setData(r);
    } catch (e) {
      setData({ isRepo: false, files: [], error: errText(e) });
    } finally {
      setLoading(false);
    }
  }, [cwd, base]);

  useEffect(() => {
    load();
  }, [load]);

  // Refresh when Claude finishes a step that may have touched files.
  const status = rt?.status;
  const rev = rt?.rev || 0;
  useEffect(() => {
    if (status === 'idle') load();
  }, [status, load]);
  const fsRev = useStore((s) => s.fsRev);
  useEffect(() => {
    if (fsRev) load();
  }, [fsRev]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (Date.now() - lastLoad.current < 2500) return;
    const t = setTimeout(load, 1200);
    return () => clearTimeout(t);
  }, [Math.floor(rev / 25)]); // eslint-disable-line react-hooks/exhaustive-deps

  const files = useMemo(() => data?.files || [], [data]);
  const totals = useMemo(() => files.reduce((a, f) => ({ add: a.add + f.additions, del: a.del + f.deletions }), { add: 0, del: 0 }), [files]);
  const noteCount = Object.values(notes).reduce((n, m) => n + Object.keys(m).length, 0);

  const onComment = (path: string) => (key: string, text: string | null, line: DiffLine) => {
    setNotes((all) => {
      const cur = { ...(all[path] || {}) };
      if (text) cur[key] = { text, line: line.s, lineNo: (line.t === '-' ? line.o : line.n) || 0 };
      else delete cur[key];
      return { ...all, [path]: cur };
    });
  };

  const notesMessage = () => {
    const parts = ['Please address my review notes on the current changes:', ''];
    for (const [path, m] of Object.entries(notes)) {
      const entries = Object.entries(m).sort((a, b) => a[1].lineNo - b[1].lineNo);
      if (!entries.length) continue;
      parts.push(`${path}`);
      for (const [key, n] of entries) parts.push(`- line ${n.lineNo}${key.startsWith('old') ? ' (removed line)' : ''}: ${n.text}\n  > ${n.line.trim().slice(0, 160)}`);
      parts.push('');
    }
    return parts.join('\n').trim();
  };

  const sendNotes = async () => {
    await sendMessage(session.id, { text: notesMessage() });
    setNotes({});
    toast('Sent your notes to Claude', 'success');
  };

  const review = () => sendMessage(session.id, { text: rt?.init?.slashCommands?.includes('code-review') ? '/code-review' : 'Review the uncommitted changes in this repository. Look for bugs, missed edge cases and anything that does not match the surrounding code. Report findings with file and line references; do not change files yet.' });

  const commit = async () => {
    if (!message.trim()) return;
    const r = await call<{ ok: boolean; output: string }>('git.commit', { cwd, message: message.trim() });
    if (r.ok) {
      toast(r.output.split('\n')[0] || 'Committed', 'success');
      setCommitOpen(false);
      setMessage('');
      bumpFs();
    } else toast(r.output || 'Commit failed', 'error');
  };

  const discard = (f: DiffFile) =>
    confirmAction({
      title: `Discard changes to ${basename(f.path)}?`,
      body: f.untracked ? 'This deletes the new file.' : 'The file goes back to its last committed version. This cannot be undone.',
      confirm: f.untracked ? 'Delete file' : 'Discard changes',
      danger: true,
      onConfirm: async () => {
        const r = await call<{ ok: boolean; output: string }>('git.discard', { cwd, path: f.path, untracked: f.untracked });
        if (!r.ok) toast(r.output || 'Could not discard', 'error');
        bumpFs();
      },
    });

  if (data && !data.isRepo) return <SessionEdits sessionId={session.id} cwd={cwd} />;

  return (
    <div className="changes" ref={box}>
      <div className="panel-toolbar changes-bar">
        {session.worktree ? (
          <Select
            className="ghost-select"
            value={base}
            onChange={setBase}
            width={260}
            options={[
              { value: 'HEAD', label: 'Uncommitted', hint: 'Changes not committed yet' },
              { value: session.worktree.base, label: `Since ${session.worktree.base}`, hint: 'Everything this worktree changed' },
            ]}
          />
        ) : (
          <span className="changes-scope">Uncommitted</span>
        )}
        {files.length > 0 ? (
          <span className="changes-stat tnum">
            <span className={`num-add${totals.add ? '' : ' zero'}`}>+{totals.add}</span>
            <span className={`num-del${totals.del ? '' : ' zero'}`}>−{totals.del}</span>
            <span className="faint">· {plural(files.length, 'file')}</span>
          </span>
        ) : null}
        <div className="grow" />
        {data?.branch && !['main', 'master', 'trunk', 'develop'].includes(data.branch) && (
          <button
            className="btn sm ghost"
            title="Ask Claude to push this branch and open a pull request"
            onClick={() =>
              sendMessage(session.id, {
                text: `Push the branch ${data.branch} and open a pull request for it. Use the gh or glab CLI if one is installed and signed in; otherwise push and give me the link to open the request myself. Write a clear title and a short description of the changes.${files.length ? ' Commit the uncommitted changes first.' : ''}`,
              })
            }
          >
            <GitPullRequestArrow size={13} /> PR
          </button>
        )}
        {files.length > 0 && (
          <>
            <button className="btn sm ghost" onClick={review} title="Ask Claude to review these changes">
              <Eye size={13} /> Review
            </button>
            <button className={`btn sm${commitOpen ? ' on' : ''}`} onClick={() => setCommitOpen(!commitOpen)}>
              <GitCommitHorizontal size={13} /> Commit
            </button>
          </>
        )}
        <Seg
          size="sm"
          label="Diff layout"
          value={viewMode as 'unified' | 'split'}
          onChange={(v) => setUi({ diffMode: v })}
          options={[
            { value: 'unified', label: <Rows2 size={13} />, title: 'Unified' },
            { value: 'split', label: <Columns2 size={13} />, title: narrow ? 'Side by side (widen the panel to use it)' : 'Side by side', disabled: narrow },
          ]}
        />
        <button className="icon-btn sm" title="Refresh" onClick={load}>
          {loading ? <Spinner size={13} /> : <RefreshCw size={13} />}
        </button>
      </div>

      {commitOpen && (
        <div className="commit-box">
          <textarea className="textarea" rows={3} autoFocus placeholder="Commit message" value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.metaKey || e.ctrlKey) && commit()} />
          <div className="row">
            <button className="btn sm ghost" onClick={() => sendMessage(session.id, { text: 'Commit the current changes with a clear, conventional commit message. Do not push.' }).then(() => setCommitOpen(false))}>
              Let Claude write it
            </button>
            <div className="grow" />
            <button className="btn sm" onClick={() => setCommitOpen(false)}>
              Cancel
            </button>
            <button className="btn sm primary" disabled={!message.trim()} onClick={commit}>
              Commit all
            </button>
          </div>
        </div>
      )}

      <div className="changes-files">
        {files.map((f) => {
          const isOpen = open[f.path] ?? files.length <= 8;
          const fileNotes = notes[f.path] || {};
          const noteMap = Object.fromEntries(Object.entries(fileNotes).map(([k, v]) => [k, v.text]));
          return (
            <div key={f.path} className={`cfile${isOpen ? ' open' : ''}`}>
              <div className="cfile-head">
                <button className="cfile-toggle" onClick={() => setOpen({ ...open, [f.path]: !isOpen })}>
                  <ChevronRight size={13} className={isOpen ? 'rot' : ''} />
                  <span className={`fstat s-${f.status}${f.untracked ? ' s-U' : ''}`} title={f.untracked ? 'Untracked' : STATUS_LABEL[f.status]}>
                    {f.untracked ? 'U' : f.status}
                  </span>
                  <span className="cfile-name">{basename(f.path)}</span>
                  <span className="cfile-dir faint">{dirname(f.path) === '/' || !f.path.includes('/') ? '' : dirname(f.path)}</span>
                </button>
                <FileTouches touches={history?.[f.path]} compact />
                {Object.keys(fileNotes).length > 0 && (
                  <span className="badge accent" title="Review notes">
                    <MessageSquareText size={11} /> {Object.keys(fileNotes).length}
                  </span>
                )}
                <span className={`num-add${f.additions ? '' : ' zero'}`}>+{f.additions}</span>
                <span className={`num-del${f.deletions ? '' : ' zero'}`}>−{f.deletions}</span>
                <button className="icon-btn sm" title="Open file" onClick={() => openFile(`${data!.root}/${f.path}`)}>
                  <FileText size={13} />
                </button>
                <button className="icon-btn sm" title={f.untracked ? 'Delete new file' : 'Discard changes'} onClick={() => discard(f)}>
                  <Undo2 size={13} />
                </button>
              </div>
              <Collapse open={isOpen}>
                {() => (
                  <div className="cfile-body">
                    {f.binary ? (
                      <div className="faint cfile-note">Binary file</div>
                    ) : f.truncated && !f.hunks.length ? (
                      <div className="faint cfile-note">This diff is too large to show here.</div>
                    ) : (
                      <DiffView hunks={f.hunks} path={f.path} mode={viewMode} wrap={narrow} dual comments={noteMap} onComment={onComment(f.path)} maxRows={600} />
                    )}
                  </div>
                )}
              </Collapse>
            </div>
          );
        })}
        {!data && (
          <div className="changes-loading" aria-label="Loading changes">
            {[0, 1, 2].map((k) => (
              <div key={k} className="sk-file">
                <div className="sk" style={{ width: `${46 - k * 9}%` }} />
                <div className="sk short" />
                <div className="sk" />
              </div>
            ))}
          </div>
        )}
        {data && !files.length && !loading && (
          <div className="empty">
            <CircleCheck size={22} />
            <div>Working tree is clean.</div>
            <div className="hint">Changes Claude makes will show up here for review. Hover a line and click the note icon to leave feedback.</div>
          </div>
        )}
      </div>

      {noteCount > 0 && (
        <div className="notes-bar">
          <MessageSquareText size={14} />
          <span className="grow">{plural(noteCount, 'note')} on this diff</span>
          <button className="btn sm ghost" onClick={() => (insertIntoComposer(session.id, notesMessage()), setNotes({}))}>
            Add to message
          </button>
          <button className="btn sm primary" onClick={sendNotes}>
            <Send size={13} /> Send to Claude
          </button>
        </div>
      )}
    </div>
  );
}

/** Fallback for folders that are not git repositories: show what this session's tools changed. */
function SessionEdits({ sessionId, cwd }: { sessionId: string; cwd: string }) {
  const rev = useStore((s) => s.rt[sessionId]?.rev);
  const model = models.get(sessionId);
  const edits = useMemo(() => {
    const out: { id: string; path: string; hunks: ReturnType<typeof hunksFromPatch> }[] = [];
    for (const t of model?.tools.values() || []) {
      if (!['Edit', 'Write', 'MultiEdit'].includes(t.name) || t.status !== 'done') continue;
      const s = t.structured;
      const path = str(t.input, 'file_path');
      if (Array.isArray(s?.structuredPatch) && s.structuredPatch.length) out.push({ id: t.id, path, hunks: hunksFromPatch(s.structuredPatch) });
      else if (t.name === 'Write' && typeof t.input?.content === 'string') out.push({ id: t.id, path, hunks: [{ oldStart: 0, oldLines: 0, newStart: 1, newLines: 0, lines: t.input.content.split('\n').map((s2: string, k: number) => ({ t: '+', s: s2, n: k + 1 })) }] });
    }
    return out;
  }, [model, rev]); // eslint-disable-line react-hooks/exhaustive-deps -- the model mutates in place; rev marks a change
  return (
    <div className="changes">
      <div className="changes-summary">
        <span className="faint">Not a git repository — showing edits made in this session.</span>
      </div>
      <div className="changes-files">
        {edits.map((e) => (
          <div key={e.id} className="cfile open">
            <div className="cfile-head">
              <button className="cfile-toggle" onClick={() => openFile(e.path)}>
                <span className="cfile-name">{basename(e.path)}</span>
                <span className="cfile-dir faint">{dirname(e.path).replace(cwd, '.')}</span>
              </button>
            </div>
            <div className="cfile-body">
              <DiffView hunks={e.hunks} path={e.path} wrap maxRows={300} />
            </div>
          </div>
        ))}
        {!edits.length && <div className="empty">No file edits in this session yet.</div>}
      </div>
    </div>
  );
}
