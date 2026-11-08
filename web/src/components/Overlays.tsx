import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Plus, SlidersHorizontal, PanelLeft, PanelRight, GitCompare, FolderTree, SquareTerminal, AppWindow, ListChecks, Info, Sun, Settings, Keyboard, CircleStop, GitFork, Archive, RefreshCw, MessageSquare, File as FileIcon, Search, X, CircleCheck, CircleAlert, TriangleAlert, Minimize2, Power, Eye, ScrollText, Plug, History, LayoutGrid } from 'lucide-react';
import { useStore, setDialog, setUi, setTheme, openSession, interrupt, setMode, forkSession, refreshSessions, openFile, sendMessage, dismissToast, useFileIndex, openFind, quitApp, quickNewSession, type PanelTab, cycleView, showPage, holdToast, releaseToast, closeLightbox, type Toast, type ConfirmSpec } from '../lib/store';
import { useExitGhost, useFlipRows } from '../lib/motion';
import { fuzzyFilter } from '../lib/fuzzy';
import { basename, timeAgo } from '../lib/format';
import { Dialog } from './ui/Dialog';
import { MODES } from './composer/Menus';
import { Keys, useEscape } from './ui/keys';
import type { LucideIcon } from 'lucide-react';
import { SHORTCUTS as K } from '../lib/shortcuts';
import { Empty } from './ui/Empty';

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
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();
  const close = () => setDialog('palette', false);
  const active = sessions.find((s) => s.id === activeId);
  const cwd = rt?.cwd || active?.cwd;

  const files = useFileIndex(cwd, q.startsWith('@'));

  const panel = (tab: PanelTab) => () => setUi({ panel: true, panelTab: tab });
  const actions: Cmd[] = useMemo(
    () => [
      { id: 'new', label: 'New session', hint: 'In the current folder, with your defaults', icon: Plus, kbd: K.newSession.keys, group: 'Actions', run: () => quickNewSession() },
      { id: 'new-opts', label: 'New session with options…', hint: 'Folder, worktree, permissions, model', icon: SlidersHorizontal, kbd: K.newSessionWith.keys, group: 'Actions', run: () => setDialog('newSession', {}) },
      { id: 'side', label: 'Toggle sidebar', icon: PanelLeft, kbd: K.sidebar.keys, group: 'Actions', run: () => setUi({ sidebar: !useStore.getState().ui.sidebar }) },
      { id: 'panel', label: 'Toggle side panel', icon: PanelRight, kbd: K.panel.keys, group: 'Actions', run: () => setUi({ panel: !useStore.getState().ui.panel }) },
      { id: 'view', label: 'Cycle view: Normal → Thinking → Verbose', icon: Eye, kbd: K.view.keys, group: 'Actions', run: () => cycleView() },
      { id: 'board', label: 'Workboard', hint: 'Every piece of work by whose hands it is in; approve, send back, answer in place', icon: LayoutGrid, kbd: K.board.keys, group: 'Actions', run: () => showPage('board') },
      { id: 'skills', label: 'Skills…', hint: 'Personal and project skills', icon: ScrollText, group: 'Actions', run: () => setDialog('settings', 'skills') },
      { id: 'mcp', label: 'MCP servers…', hint: 'Add, sign in, reconnect', icon: Plug, group: 'Actions', run: () => setDialog('settings', 'mcp') },
      ...(activeId
        ? [
            { id: 'find', label: 'Find in the conversation', icon: Search, kbd: K.find.keys, group: 'Actions', run: () => openFind() } as Cmd,
            { id: 'changes', label: 'Show changes', icon: GitCompare, kbd: K.tabs.combos[0], group: 'Actions', run: panel('changes') } as Cmd,
            { id: 'files', label: 'Show files', icon: FolderTree, kbd: K.tabs.combos[1], group: 'Actions', run: panel('files') } as Cmd,
            { id: 'term', label: 'Open terminal', icon: SquareTerminal, kbd: K.tabs.combos[2], group: 'Actions', run: panel('terminal') } as Cmd,
            { id: 'preview', label: 'Preview a web app', icon: AppWindow, kbd: K.tabs.combos[3], group: 'Actions', run: panel('preview') } as Cmd,
            { id: 'tasks', label: 'Show tasks', icon: ListChecks, kbd: K.tabs.combos[4], group: 'Actions', run: panel('tasks') } as Cmd,
            { id: 'history', label: 'Show the timeline', hint: 'Every step that changed files; restore from any point', icon: History, kbd: K.tabs.combos[5], group: 'Actions', run: panel('history') } as Cmd,
            { id: 'info', label: 'Session details and context usage', icon: Info, kbd: K.tabs.combos[6], group: 'Actions', run: panel('info') } as Cmd,
            { id: 'stop', label: 'Stop Claude', icon: CircleStop, kbd: K.stop.keys, group: 'Actions', run: () => interrupt(activeId) } as Cmd,
            { id: 'compact', label: 'Compact the conversation', icon: Minimize2, group: 'Actions', run: () => sendMessage(activeId, { text: '/compact' }) } as Cmd,
            { id: 'fork', label: 'Fork this session', icon: GitFork, group: 'Actions', run: () => forkSession(activeId) } as Cmd,
            ...MODES.map((m) => ({ id: 'mode-' + m.id, label: `Permission mode: ${m.label}`, hint: m.hint, icon: m.icon, group: 'Actions' as const, run: () => setMode(activeId, m.id) })),
          ]
        : []),
      { id: 'theme', label: `Switch to ${config?.theme === 'dark' ? 'light' : 'dark'} theme`, icon: Sun, group: 'Actions', run: () => setTheme(config?.theme === 'dark' ? 'light' : 'dark') },
      { id: 'archived', label: 'Show archived sessions', icon: Archive, group: 'Actions', run: () => setUi({ showArchived: !useStore.getState().ui.showArchived }) },
      { id: 'refresh', label: 'Reload session list', icon: RefreshCw, group: 'Actions', run: () => refreshSessions(true) },
      { id: 'settings', label: 'Settings', icon: Settings, kbd: K.settings.keys, group: 'Actions', run: () => setDialog('settings', true) },
      { id: 'keys', label: 'Keyboard shortcuts', icon: Keyboard, kbd: K.shortcuts.keys, group: 'Actions', run: () => setDialog('shortcuts', true) },
      { id: 'quit', label: 'Quit Pilcrow', hint: 'Stops all sessions', icon: Power, kbd: K.quit.keys, group: 'Actions', run: () => quitApp() },
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
  const option = (i: number) => `${listId}-${i}`;
  return (
    <Dialog onClose={close} width={600} className="palette" label="Command palette">
      <div className="pal-input">
        <Search size={16} aria-hidden="true" />
        <input
          autoFocus
          role="combobox"
          aria-label="Search sessions and commands"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={items[idx] ? option(idx) : undefined}
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
      <div className="pal-list" ref={list} role="listbox" id={listId} aria-label="Results">
        {items.map((c, i) => {
          const head = c.group !== lastGroup ? c.group : null;
          lastGroup = c.group;
          return (
            <div key={c.id} role="presentation">
              {head && (
                <div className="pal-group" role="presentation">
                  {head}
                </div>
              )}
              <button id={option(i)} role="option" aria-selected={i === idx} tabIndex={-1} className={`pal-item${i === idx ? ' active' : ''}`} onMouseEnter={() => setIdx(i)} onClick={() => run(c)}>
                <c.icon size={14} aria-hidden="true" />
                <span className="pal-label ellipsis">{c.label}</span>
                {c.hint && <span className="pal-hint ellipsis">{c.hint}</span>}
                {c.kbd && <Keys combo={c.kbd} className="pal-keys" />}
              </button>
            </div>
          );
        })}
        {!items.length && <Empty title={`Nothing matches “${q}”.`} />}
      </div>
    </Dialog>
  );
}


export function ShortcutsDialog() {
  return (
    <Dialog title="Keyboard shortcuts" onClose={() => setDialog('shortcuts', false)} width={480}>
      <div className="shortcuts">
        {Object.values(K).map(({ keys: k, label: v }) => (
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

export function ConfirmDialog({ spec }: { spec: ConfirmSpec }) {
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
          {/* Enter right away should never be the destructive answer: a danger asks from Cancel. */}
          <button className="btn" onClick={close} autoFocus={spec.danger}>
            Cancel
          </button>
          <button
            className={`btn ${spec.danger ? 'danger solid' : 'primary'}`}
            autoFocus={!spec.danger}
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
    <>
      <div className="toasts" ref={ref}>
        {toasts.map((t) => (
          <ToastItem key={t.id} t={t} />
        ))}
      </div>
      {/* What a screen reader hears: errors at once, the rest when it is free. A region of its own,
          outside the app, so it is still heard while a dialog puts the app to sleep (ui/Dialog). */}
      {createPortal(
        <div className="sr-only" data-awake="">
          <div role="alert">
            {toasts.filter((t) => t.level === 'error').map((t) => (
              <p key={t.id}>{t.text}</p>
            ))}
          </div>
          <div role="status">
            {toasts.filter((t) => t.level !== 'error').map((t) => (
              <p key={t.id}>{t.text}</p>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </>
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
  useEscape(closeLightbox);
  // The focus goes in with it, and back to what opened it.
  const x = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    x.current?.focus();
    return () => {
      if (back?.isConnected) back.focus();
    };
  }, []);
  return createPortal(
    <div ref={ref} className="lightbox" role="dialog" aria-modal="true" aria-label="Image" onClick={closeLightbox}>
      <img src={src} alt="" onClick={(e) => e.stopPropagation()} draggable={false} />
      <button ref={x} className="lightbox-x" onClick={closeLightbox} aria-label="Close" title="Close (Esc)">
        <X size={18} />
      </button>
    </div>,
    document.body,
  );
}
