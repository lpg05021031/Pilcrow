import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Plus, SlidersHorizontal, PanelLeft, PanelRight, GitCompare, FolderTree, SquareTerminal, AppWindow, ListChecks, Info, Sun, Settings, Keyboard, CircleStop, GitFork, Archive, RefreshCw, MessageSquare, File as FileIcon, Search, X, CircleCheck, CircleAlert, TriangleAlert, Minimize2, Power, Eye, ScrollText, Plug, History, LayoutGrid } from 'lucide-react';
import { useStore, setDialog, setUi, setTheme, openSession, interrupt, setMode, forkSession, refreshSessions, openFile, sendMessage, dismissToast, call, native, quickNewSession, type PanelTab, cycleView, showPage, holdToast, releaseToast, closeLightbox, type Toast } from '../lib/store';
import { useExitGhost, useFlipRows } from '../lib/motion';
import { fuzzyFilter } from '../lib/fuzzy';
import { basename, timeAgo } from '../lib/format';
import { Dialog } from './ui/Dialog';
import { MODES } from './Composer';
import { Keys } from './ui/bits';
import type { LucideIcon } from 'lucide-react';

function quitNow() {
  call('app.quit', {}, { quiet: true }).catch(() => {});
  if (native()) return; // the desktop shell closes its own window
  // A browser tab cannot always close itself; leave a clear end state instead.
  setTimeout(() => {
    window.close();
    document.body.innerHTML = '<div class="quit-note">Pilcrow has quit. You can close this window.</div>';
  }, 300);
}

/** Quit, asking first when Claude is still working (the desktop shell asks natively instead). */
export function quitApp() {
  if (native()) return quitNow();
  const busy = useStore.getState().sessions.filter((s) => s.status === 'running' || s.status === 'requires_action').length;
  if (!busy) return quitNow();
  setDialog('confirm', { title: 'Quit Pilcrow?', body: `Claude is still working in ${busy} session${busy > 1 ? 's' : ''}. Quitting stops ${busy > 1 ? 'them' : 'it'}.`, confirm: 'Quit', danger: true, onConfirm: quitNow });
}

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  kbd?: string;
  group: 'Actions' | 'Sessions' | 'Files';
  run: () => void;
}

export function CommandPalette() {
  const sessions = useStore((s) => s.sessions);
  const activeId = useStore((s) => s.activeId);
  const config = useStore((s) => s.config);
  const rt = useStore((s) => (activeId ? s.rt[activeId] : undefined));
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const close = () => setDialog('palette', false);
  const active = sessions.find((s) => s.id === activeId);
  const cwd = rt?.cwd || active?.cwd;

  useEffect(() => {
    if (q.startsWith('@') && cwd && !files.length) call<string[]>('fs.index', { cwd }, { quiet: true }).then(setFiles).catch(() => {});
  }, [q, cwd, files.length]);

  const panel = (tab: PanelTab) => () => setUi({ panel: true, panelTab: tab });
  const actions: Cmd[] = useMemo(
    () => [
      { id: 'new', label: 'New session', hint: 'In the current folder, with your defaults', icon: Plus, kbd: 'Ctrl+N', group: 'Actions', run: () => quickNewSession() },
      { id: 'new-opts', label: 'New session with options…', hint: 'Folder, worktree, permissions, model', icon: SlidersHorizontal, kbd: 'Ctrl+Shift+N', group: 'Actions', run: () => setDialog('newSession', {}) },
      { id: 'side', label: 'Toggle sidebar', icon: PanelLeft, kbd: 'Ctrl+B', group: 'Actions', run: () => setUi({ sidebar: !useStore.getState().ui.sidebar }) },
      { id: 'panel', label: 'Toggle side panel', icon: PanelRight, kbd: 'Ctrl+\\', group: 'Actions', run: () => setUi({ panel: !useStore.getState().ui.panel }) },
      { id: 'view', label: 'Cycle view: Normal → Thinking → Verbose', icon: Eye, kbd: 'Ctrl+O', group: 'Actions', run: () => cycleView() },
      { id: 'overview', label: 'Control room', hint: 'Every session at a glance; answer prompts in place', icon: LayoutGrid, kbd: 'Ctrl+Shift+O', group: 'Actions', run: () => showPage('overview') },
      { id: 'skills', label: 'Skills…', hint: 'Personal and project skills', icon: ScrollText, group: 'Actions', run: () => setDialog('settings', 'skills') },
      { id: 'mcp', label: 'MCP servers…', hint: 'Add, sign in, reconnect', icon: Plug, group: 'Actions', run: () => setDialog('settings', 'mcp') },
      ...(activeId
        ? [
            { id: 'find', label: 'Find in the conversation', icon: Search, kbd: 'Ctrl+F', group: 'Actions', run: () => setTimeout(() => window.dispatchEvent(new Event('pilcrow:find')), 60) } as Cmd,
            { id: 'changes', label: 'Show changes', icon: GitCompare, kbd: 'Alt+1', group: 'Actions', run: panel('changes') } as Cmd,
            { id: 'files', label: 'Show files', icon: FolderTree, kbd: 'Alt+2', group: 'Actions', run: panel('files') } as Cmd,
            { id: 'term', label: 'Open terminal', icon: SquareTerminal, kbd: 'Alt+3', group: 'Actions', run: panel('terminal') } as Cmd,
            { id: 'preview', label: 'Preview a web app', icon: AppWindow, kbd: 'Alt+4', group: 'Actions', run: panel('preview') } as Cmd,
            { id: 'tasks', label: 'Show tasks', icon: ListChecks, kbd: 'Alt+5', group: 'Actions', run: panel('tasks') } as Cmd,
            { id: 'history', label: 'Show the timeline', hint: 'Every step that changed files; restore from any point', icon: History, kbd: 'Alt+6', group: 'Actions', run: panel('history') } as Cmd,
            { id: 'info', label: 'Session details and context usage', icon: Info, kbd: 'Alt+7', group: 'Actions', run: panel('info') } as Cmd,
            { id: 'stop', label: 'Stop Claude', icon: CircleStop, kbd: 'Esc', group: 'Actions', run: () => interrupt(activeId) } as Cmd,
            { id: 'compact', label: 'Compact the conversation', icon: Minimize2, group: 'Actions', run: () => sendMessage(activeId, { text: '/compact' }) } as Cmd,
            { id: 'fork', label: 'Fork this session', icon: GitFork, group: 'Actions', run: () => forkSession(activeId) } as Cmd,
            ...MODES.map((m) => ({ id: 'mode-' + m.id, label: `Permission mode: ${m.label}`, hint: m.hint, icon: m.icon, group: 'Actions' as const, run: () => setMode(activeId, m.id) })),
          ]
        : []),
      { id: 'theme', label: `Switch to ${config?.theme === 'dark' ? 'light' : 'dark'} theme`, icon: Sun, group: 'Actions', run: () => setTheme(config?.theme === 'dark' ? 'light' : 'dark') },
      { id: 'archived', label: 'Show archived sessions', icon: Archive, group: 'Actions', run: () => setUi({ showArchived: !useStore.getState().ui.showArchived }) },
      { id: 'refresh', label: 'Reload session list', icon: RefreshCw, group: 'Actions', run: () => refreshSessions(true) },
      { id: 'settings', label: 'Settings', icon: Settings, kbd: 'Ctrl+,', group: 'Actions', run: () => setDialog('settings', true) },
      { id: 'keys', label: 'Keyboard shortcuts', icon: Keyboard, kbd: 'Ctrl+/', group: 'Actions', run: () => setDialog('shortcuts', true) },
      { id: 'quit', label: 'Quit Pilcrow', hint: 'Stops all sessions', icon: Power, kbd: 'Ctrl+Q', group: 'Actions', run: () => quitApp() },
    ],
    [activeId, config?.theme],  
  );

  const items: Cmd[] = useMemo(() => {
    if (q.startsWith('@')) {
      const fq = q.slice(1);
      return fuzzyFilter(fq, files.filter((f) => !f.endsWith('/')), (f) => f, 60).map((m) => ({ id: 'f:' + m.item, label: m.item, icon: FileIcon, group: 'Files' as const, run: () => openFile(`${cwd}/${m.item}`) }));
    }
    const sess: Cmd[] = sessions.map((s) => ({ id: 's:' + s.id, label: s.title, hint: `${basename(s.project || s.cwd)} · ${timeAgo(s.lastModified)}`, icon: MessageSquare, group: 'Sessions' as const, run: () => openSession(s.id) }));
    if (!q.trim()) return [...actions.slice(0, 8), ...sess.slice(0, 12)];
    return fuzzyFilter(q, [...actions, ...sess], (c) => `${c.label} ${c.hint || ''}`, 60).map((m) => m.item);
  }, [q, actions, sessions, files, cwd]);

  useEffect(() => setIdx(0), [q]);
  useEffect(() => {
    list.current?.querySelector('.pal-item.active')?.scrollIntoView({ block: 'nearest' });
  }, [idx]);

  const run = (c?: Cmd) => {
    if (!c) return;
    close();
    setTimeout(c.run, 0);
  };

  let lastGroup = '';
  return (
    <Dialog onClose={close} width={600} className="palette">
      <div className="pal-input">
        <Search size={16} />
        <input
          autoFocus
          value={q}
          placeholder="Search sessions and commands, or type @ for files"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setIdx((i) => Math.min(items.length - 1, i + 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setIdx((i) => Math.max(0, i - 1));
            } else if (e.key === 'Enter') {
              e.preventDefault();
              run(items[idx]);
            }
          }}
        />
      </div>
      <div className="pal-list" ref={list}>
        {items.map((c, i) => {
          const head = c.group !== lastGroup ? c.group : null;
          lastGroup = c.group;
          return (
            <div key={c.id}>
              {head && <div className="pal-group">{head}</div>}
              <button className={`pal-item${i === idx ? ' active' : ''}`} onMouseEnter={() => setIdx(i)} onClick={() => run(c)}>
                <c.icon size={14} />
                <span className="pal-label ellipsis">{c.label}</span>
                {c.hint && <span className="pal-hint ellipsis">{c.hint}</span>}
                {c.kbd && <Keys combo={c.kbd} className="pal-keys" />}
              </button>
            </div>
          );
        })}
        {!items.length && <div className="empty">Nothing matches “{q}”.</div>}
      </div>
    </Dialog>
  );
}

const SHORTCUTS: [string, string][] = [
  ['Ctrl+K', 'Command palette'],
  ['Ctrl+N', 'New session'],
  ['Ctrl+Shift+N', 'New session with options'],
  ['Ctrl+F', 'Find in the conversation'],
  ['Ctrl+B', 'Toggle sidebar'],
  ['Ctrl+\\', 'Toggle side panel'],
  ['Ctrl+O', 'Cycle view: Normal, Thinking, Verbose'],
  ['Alt+1 … 7', 'Changes, Files, Terminal, Preview, Tasks, Timeline, Session'],
  ['Ctrl+Shift+O', 'Control room'],
  ['Ctrl+Alt+↑ / ↓', 'Previous / next session'],
  ['Enter', 'Send (Shift+Enter for a new line)'],
  ['Shift+Tab', 'Cycle permission mode'],
  ['Esc', 'Stop Claude'],
  ['↑ in an empty box', 'Recall your previous message'],
  ['@', 'Mention a file or folder'],
  ['/', 'Run a slash command'],
  ['1 · 2 · 3', 'Answer a permission request'],
  ['Ctrl+S', 'Save the open file'],
  ['Ctrl+,', 'Settings'],
];

export function ShortcutsDialog() {
  return (
    <Dialog title="Keyboard shortcuts" onClose={() => setDialog('shortcuts', false)} width={480}>
      <div className="shortcuts">
        {SHORTCUTS.map(([k, v]) => (
          <div key={k} className="shortcut">
            <span>{v}</span>
            <span className="keys">
              {k.split(' ').map((p, i) => (p === '…' || p === '·' || p === 'in' || p === 'an' || p === 'empty' || p === 'box' ? <span key={i} className="faint"> {p} </span> : p === '/' && k.includes(' / ') ? <span key={i} className="faint"> / </span> : <Keys key={i} combo={p} />))}
            </span>
          </div>
        ))}
      </div>
    </Dialog>
  );
}

export function ConfirmDialog() {
  const spec = useStore((s) => s.dialogs.confirm)!;
  const [checked, setChecked] = useState(spec.checkbox?.value || false);
  const close = () => setDialog('confirm', null);
  return (
    <Dialog
      title={spec.title}
      onClose={close}
      width={440}
      footer={
        <>
          <div className="grow" />
          <button className="btn" onClick={close}>
            Cancel
          </button>
          <button
            className={`btn ${spec.danger ? 'danger solid' : 'primary'}`}
            autoFocus
            onClick={() => {
              close();
              spec.onConfirm(checked);
            }}
          >
            {spec.confirm}
          </button>
        </>
      }
    >
      {spec.body && <p className="confirm-body">{spec.body}</p>}
      {spec.list && spec.list.length > 0 && (
        <ul className="confirm-list mono">
          {spec.list.slice(0, 12).map((x) => (
            <li key={x}>{x}</li>
          ))}
          {spec.list.length > 12 && <li className="faint">and {spec.list.length - 12} more</li>}
        </ul>
      )}
      {spec.checkbox && (
        <label className="check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} /> {spec.checkbox.label}
        </label>
      )}
    </Dialog>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const ref = useRef<HTMLDivElement>(null);
  // When one toast leaves, the ones above it settle down into its place instead of jumping.
  useFlipRows(ref, '.toast', toasts);
  return (
    <div className="toasts" role="status" aria-live="polite" ref={ref}>
      {toasts.map((t) => (
        <ToastItem key={t.id} t={t} />
      ))}
    </div>
  );
}

function ToastItem({ t }: { t: Toast }) {
  const ref = useRef<HTMLDivElement>(null);
  useExitGhost(ref, { duration: 220 });
  const Icon = t.level === 'success' ? CircleCheck : t.level === 'error' ? CircleAlert : t.level === 'warning' ? TriangleAlert : Info;
  return (
    <div ref={ref} data-flip={t.id} className={`toast ${t.level}`} onMouseEnter={() => holdToast(t.id)} onMouseLeave={() => releaseToast(t.id)}>
      <Icon size={15} />
      <span className="grow">{t.text}</span>
      {t.count > 1 && (
        <span key={t.count} className="toast-n tnum">
          ×{t.count}
        </span>
      )}
      {t.action && (
        <button className="btn sm ghost" onClick={t.action.run}>
          {t.action.label}
        </button>
      )}
      <button className="icon-btn sm" onClick={() => dismissToast(t.id)} aria-label="Dismiss">
        <X size={13} />
      </button>
    </div>
  );
}

/** An image, full size, over everything. Click anywhere or press Esc to put it back. */
export function Lightbox() {
  const src = useStore((s) => s.lightbox);
  if (!src) return null;
  return <LightboxView src={src} />;
}

function LightboxView({ src }: { src: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useExitGhost(ref, { duration: 200, pin: false });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeLightbox();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, []);
  return createPortal(
    <div ref={ref} className="lightbox" role="dialog" aria-modal="true" aria-label="Image" onClick={closeLightbox}>
      <img src={src} alt="" onClick={(e) => e.stopPropagation()} draggable={false} />
      <button className="lightbox-x" onClick={closeLightbox} aria-label="Close" title="Close (Esc)">
        <X size={18} />
      </button>
    </div>,
    document.body,
  );
}
