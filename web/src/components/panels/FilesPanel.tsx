import { useCallback, useEffect, useRef, useState, createContext, useContext } from 'react';
import { ChevronRight, File as FileIcon, Folder, FolderOpen, RefreshCw, X, Save, PanelLeft, Eye, EyeOff, ExternalLink, AtSign, BookOpen, PencilLine, Columns2, RotateCw } from 'lucide-react';
import { useStore, call, toast, confirmAction, insertIntoComposer, bumpFs } from '../../lib/store';
import { FileTouches, useFileHistory } from '../FileTouches';
import { Markdown } from '../Markdown';
import type { FileTouch, SessionSummary } from '../../lib/types';
import { basename, bytes, relPath, dirname } from '../../lib/format';
import { Editor } from './Editor';
import { Spinner, Seg } from '../ui/bits';
import type { FileRead, FileWrite } from '../../lib/types';
import { errText } from '../../lib/format';

interface Entry {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size: number;
  heavy?: boolean;
}

interface Tab {
  path: string;
  doc: string;
  saved: string;
  mtime?: number;
  binary?: boolean;
  dataUrl?: string;
  tooLarge?: boolean;
  size?: number;
  error?: string;
  loading?: boolean;
  /** Documents can be read as rendered pages, edited as text, or both side by side. */
  view?: 'preview' | 'edit' | 'split';
}

interface FilesState {
  tabs: Tab[];
  active: string | null;
  expanded: Record<string, boolean>;
  showTree: boolean;
  showHidden: boolean;
}

const perCwd = new Map<string, FilesState>();
const dirCache = new Map<string, Entry[]>();

const HistoryCtx = createContext<{ cwd: string; map: Record<string, FileTouch[]> | null }>({ cwd: '', map: null });

export function FilesPanel({ session, cwd }: { session: SessionSummary; cwd: string }) {
  const fileOpen = useStore((s) => s.fileOpen);
  const history = useFileHistory(cwd);
  const rev = useStore((s) => s.rt[session.id]?.rev || 0);
  const [st, setSt] = useState<FilesState>(() => perCwd.get(cwd) || { tabs: [], active: null, expanded: { [cwd]: true }, showTree: true, showHidden: false });
  const [line, setLine] = useState<{ line?: number; nonce: number }>({ nonce: 0 });
  const [treeNonce, setTreeNonce] = useState(0);
  const update = (fn: (s: FilesState) => FilesState) =>
    setSt((s) => {
      const n = fn(s);
      perCwd.set(cwd, n);
      return n;
    });

  const openTab = useCallback(
    async (path: string, ln?: number) => {
      update((s) => (s.tabs.some((t) => t.path === path) ? { ...s, active: path } : { ...s, active: path, tabs: [...s.tabs, { path, doc: '', saved: '', loading: true }] }));
      setLine({ line: ln, nonce: Date.now() });
      const existing = (perCwd.get(cwd) || st).tabs.find((t) => t.path === path);
      if (existing && !existing.loading && !existing.error) return;
      try {
        const r = await call<FileRead>('fs.read', { path }, { quiet: true });
        update((s) => ({ ...s, tabs: s.tabs.map((t) => (t.path === path ? { path, doc: r.text ?? '', saved: r.text ?? '', mtime: r.mtime, binary: r.binary, dataUrl: r.dataUrl, tooLarge: r.tooLarge, size: r.size } : t)) }));
        setLine({ line: ln, nonce: Date.now() });
      } catch (e) {
        update((s) => ({ ...s, tabs: s.tabs.map((t) => (t.path === path ? { ...t, loading: false, error: errText(e) } : t)) }));
      }
    },
    [cwd], // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (fileOpen) openTab(fileOpen.path, fileOpen.line);
  }, [fileOpen?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pick up edits Claude makes to open files (only when there are no unsaved local edits).
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(async () => {
      const cur = perCwd.get(cwd);
      if (!cur) return;
      for (const t of cur.tabs) {
        if (t.loading || t.binary || t.tooLarge || t.doc !== t.saved) continue;
        try {
          const r = await call<FileRead>('fs.read', { path: t.path }, { quiet: true });
          const text = r.text;
          if (r.mtime !== t.mtime && typeof text === 'string') update((s) => ({ ...s, tabs: s.tabs.map((x) => (x.path === t.path && x.doc === x.saved ? { ...x, doc: text, saved: text, mtime: r.mtime } : x)) }));
        } catch {
          /* file gone */
        }
      }
      setTreeNonce((n) => n + 1);
    }, 900);
  }, [rev, cwd]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = st.tabs.find((t) => t.path === st.active) || null;

  const save = async (tab: Tab, force = false) => {
    const r = await call<FileWrite>('fs.write', { path: tab.path, text: tab.doc, expectedMtime: tab.mtime, force });
    if (r.conflict) {
      confirmAction({
        title: `${basename(tab.path)} changed on disk`,
        body: 'Someone (maybe Claude) changed this file after you opened it. Overwrite their version with yours?',
        confirm: 'Overwrite',
        danger: true,
        onConfirm: () => save(tab, true),
      });
      return;
    }
    update((s) => ({ ...s, tabs: s.tabs.map((t) => (t.path === tab.path ? { ...t, saved: tab.doc, mtime: r.mtime } : t)) }));
    toast(`Saved ${basename(tab.path)}`, 'success');
    bumpFs();
  };

  const close = (path: string) => {
    const tab = st.tabs.find((t) => t.path === path);
    const doClose = () =>
      update((s) => {
        const tabs = s.tabs.filter((t) => t.path !== path);
        return { ...s, tabs, active: s.active === path ? tabs[tabs.length - 1]?.path || null : s.active };
      });
    if (tab && tab.doc !== tab.saved) confirmAction({ title: `Discard unsaved changes to ${basename(path)}?`, confirm: 'Discard', danger: true, onConfirm: doClose });
    else doClose();
  };

  return (
    <div className="files">
      <div className="panel-toolbar">
        <button className={`icon-btn sm${st.showTree ? ' active' : ''}`} title="Toggle file tree" onClick={() => update((s) => ({ ...s, showTree: !s.showTree }))}>
          <PanelLeft size={14} />
        </button>
        <span className="mono faint ellipsis grow" title={cwd}>
          {active ? relPath(active.path, cwd) : basename(cwd)}
        </span>
        {active && (
          <button className="icon-btn sm" title="Mention this file in the message" onClick={() => insertIntoComposer(session.id, `@${relPath(active.path, cwd)} `)}>
            <AtSign size={13} />
          </button>
        )}
        {active && isDoc(active.path) && !active.binary && !active.loading && (
          <Seg
            size="sm"
            className="doc-seg"
            label="Document view"
            value={docView(active)}
            onChange={(v) => update((s) => ({ ...s, showTree: v === 'split' ? false : s.showTree, tabs: s.tabs.map((t) => (t.path === active.path ? { ...t, view: v } : t)) }))}
            options={[
              { value: 'preview', label: <BookOpen size={13} />, title: docKind(active.path) === 'html' ? 'Preview' : 'Read' },
              { value: 'edit', label: <PencilLine size={13} />, title: 'Edit' },
              { value: 'split', label: <Columns2 size={13} />, title: 'Side by side' },
            ]}
          />
        )}
        {active && !active.binary && (
          <button className="icon-btn sm" title="Save (Ctrl+S)" disabled={active.doc === active.saved} onClick={() => save(active)}>
            <Save size={14} />
          </button>
        )}
        <button className="icon-btn sm" title={st.showHidden ? 'Hide dotfiles' : 'Show dotfiles'} onClick={() => update((s) => ({ ...s, showHidden: !s.showHidden }))}>
          {st.showHidden ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
        <button
          className="icon-btn sm"
          title="Refresh"
          onClick={() => {
            dirCache.clear();
            setTreeNonce((n) => n + 1);
          }}
        >
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="files-body">
        {st.showTree && (
          <div className="tree">
            <HistoryCtx.Provider value={{ cwd, map: history }}>
              <TreeDir path={cwd} depth={0} st={st} update={update} onOpen={openTab} active={st.active} nonce={treeNonce} root />
            </HistoryCtx.Provider>
          </div>
        )}
        <div className="files-editor">
          {st.tabs.length > 0 && (
            <div className="ftabs">
              {st.tabs.map((t) => (
                <div key={t.path} className={`ftab${t.path === st.active ? ' on' : ''}`} title={t.path}>
                  <button className="ftab-main" onClick={() => update((s) => ({ ...s, active: t.path }))}>
                    {basename(t.path)}
                    {t.doc !== t.saved && <span className="dirty-dot" />}
                  </button>
                  <button className="ftab-x" onClick={() => close(t.path)} aria-label="Close tab">
                    <X size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {active ? (
            active.loading ? (
              <div className="empty">
                <Spinner /> Opening…
              </div>
            ) : active.error ? (
              <div className="empty">{active.error}</div>
            ) : active.dataUrl ? (
              <div className="img-preview">
                <img src={active.dataUrl} alt={basename(active.path)} />
                <div className="faint">{bytes(active.size || 0)}</div>
              </div>
            ) : active.binary || active.tooLarge ? (
              <div className="empty">
                <div>{active.tooLarge ? 'This file is too large to open here' : 'Binary file'} · {bytes(active.size || 0)}</div>
                <button className="btn sm" onClick={() => call('shell.openPath', { path: active.path })}>
                  <ExternalLink size={13} /> Open with system app
                </button>
              </div>
            ) : (
              <div className={`doc-area${isDoc(active.path) ? ` ${docView(active)}` : ' edit'}`}>
                {(!isDoc(active.path) || docView(active) !== 'preview') && (
                  <Editor
                    key={active.path}
                    path={active.path}
                    doc={active.doc === active.saved ? active.saved : active.doc}
                    line={line.line}
                    nonce={line.nonce}
                    onChange={(text) => update((s) => ({ ...s, tabs: s.tabs.map((t) => (t.path === active.path ? { ...t, doc: text } : t)) }))}
                    onSave={() => {
                      const cur = perCwd.get(cwd)?.tabs.find((t) => t.path === active.path);
                      if (cur) save(cur);
                    }}
                  />
                )}
                {docKind(active.path) === 'md' && docView(active) !== 'edit' && (
                  <div className="doc-preview" key={`p-${active.path}`}>
                    {active.doc.trim() ? <Markdown text={active.doc} cwd={cwd} baseDir={dirname(active.path)} className="doc-md" /> : <div className="faint">This document is empty.</div>}
                  </div>
                )}
                {docKind(active.path) === 'html' && docView(active) !== 'edit' && <HtmlPreview path={active.path} saved={active.saved} dirty={active.doc !== active.saved} onSave={() => save(active)} />}
              </div>
            )
          ) : (
            <div className="empty">
              <FileIcon size={22} />
              <div>Pick a file to view or edit it.</div>
              <div className="hint">File paths in Claude's replies open here too.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TreeDir({ path, depth, st, update, onOpen, active, nonce, root }: { path: string; depth: number; st: FilesState; update: (fn: (s: FilesState) => FilesState) => void; onOpen: (p: string) => void; active: string | null; nonce: number; root?: boolean }) {
  const [entries, setEntries] = useState<Entry[] | null>(() => dirCache.get(path) || null);
  const hist = useContext(HistoryCtx);
  const [error, setError] = useState<string | null>(null);
  const open = root || st.expanded[path];
  useEffect(() => {
    if (!open) return;
    let alive = true;
    call<{ entries: Entry[]; error?: string }>('fs.list', { dir: path }, { quiet: true })
      .then((r) => {
        if (!alive) return;
        dirCache.set(path, r.entries);
        setEntries(r.entries);
        setError(r.error || null);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [path, open, nonce]);

  const list = (entries || []).filter((e) => st.showHidden || !e.name.startsWith('.'));
  return (
    <>
      {!root && (
        <button className="tree-row" style={{ paddingLeft: 6 + depth * INDENT }} onClick={() => update((s) => ({ ...s, expanded: { ...s.expanded, [path]: !s.expanded[path] } }))}>
          <ChevronRight size={12} className={open ? 'rot' : ''} />
          {open ? <FolderOpen size={13} className="tree-dir-ico" /> : <Folder size={13} className="tree-dir-ico" />}
          <span className="ellipsis">{basename(path)}</span>
        </button>
      )}
      {open && (
        <>
          {error && <div className="tree-err" style={{ paddingLeft: 24 + depth * INDENT }}>{error}</div>}
          {!entries && !error && (
            <div className="tree-row faint" style={{ paddingLeft: 24 + depth * INDENT }}>
              <Spinner size={11} />
            </div>
          )}
          {list.map((e) =>
            e.type === 'dir' ? (
              <TreeDir key={e.path} path={e.path} depth={root ? depth : depth + 1} st={st} update={update} onOpen={onOpen} active={active} nonce={nonce} />
            ) : (
              <button key={e.path} className={`tree-row file${active === e.path ? ' on' : ''}`} style={{ paddingLeft: 24 + (root ? depth : depth + 1) * INDENT }} onClick={() => onOpen(e.path)} title={`${e.name} · ${bytes(e.size)}`}>
                <FileIcon size={13} className="tree-file-ico" />
                <span className="ellipsis grow">{e.name}</span>
                <FileTouches touches={hist.map?.[e.path.startsWith(hist.cwd + '/') ? e.path.slice(hist.cwd.length + 1) : e.path]} />
              </button>
            ),
          )}
        </>
      )}
    </>
  );
}

/** Files that read better as a page than as source. */
function isDoc(path: string) {
  return docKind(path) !== null;
}
function docKind(path: string): 'md' | 'html' | null {
  if (/\.(md|markdown|mdx)$/i.test(path)) return 'md';
  if (/\.(html?|xhtml)$/i.test(path)) return 'html';
  return null;
}
/** Where the server hands a file out at its own path, so an HTML page's relative assets resolve. */
function fsUrl(path: string) {
  return '/api/fs' + (path.startsWith('/') ? '' : '/') + path.split('/').map(encodeURIComponent).join('/');
}
/** A document opens as a page; a click on Edit (or a file that is not a document) shows the source. */
function docView(t: Tab): 'preview' | 'edit' | 'split' {
  return t.view || (isDoc(t.path) ? 'preview' : 'edit');
}

// One step of nesting in the file tree. File icons line up with folder icons at the same depth
// (they sit after the space a folder's chevron takes), and each level moves in by this much.
const INDENT = 16;

/**
 * An HTML file rendered in a sandboxed frame, served from its own folder so relative stylesheets,
 * scripts and images load. The frame shows the saved file: a page's own scripts need the real URL,
 * not a copy of the text, so unsaved edits appear after a save.
 */
function HtmlPreview({ path, saved, dirty, onSave }: { path: string; saved: string; dirty: boolean; onSave: () => void }) {
  const [nonce, setNonce] = useState(0);
  const [savedAt, setSavedAt] = useState(saved);
  useEffect(() => {
    if (saved !== savedAt) {
      setSavedAt(saved);
      setNonce((n) => n + 1);
    }
  }, [saved, savedAt]);
  return (
    <div className="doc-frame-wrap">
      <div className="doc-frame-bar">
        <span className="mono faint ellipsis grow">{basename(path)}</span>
        {dirty && (
          <button className="btn xs" onClick={onSave} title="Save the file to refresh the preview (Ctrl+S)">
            <Save size={11} /> Save to refresh
          </button>
        )}
        <button className="icon-btn sm" title="Reload" onClick={() => setNonce((n) => n + 1)}>
          <RotateCw size={12} />
        </button>
        <button className="icon-btn sm" title="Open in your browser" onClick={() => call('shell.openPath', { path })}>
          <ExternalLink size={12} />
        </button>
      </div>
      <iframe key={nonce} className="doc-frame" title={basename(path)} src={fsUrl(path)} sandbox="allow-scripts allow-forms allow-popups allow-modals" />
    </div>
  );
}
