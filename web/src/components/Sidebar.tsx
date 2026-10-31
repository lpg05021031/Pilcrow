import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { EASE, reducedMotion } from '../lib/motion';
import { Plus, Search, Settings, Sun, Moon, Monitor, ChevronRight, Archive, FolderGit2, GitBranch, Pin, Ellipsis, Pencil, GitFork, Trash, PinOff, ArchiveRestore, X, SquarePen, SlidersHorizontal, PanelLeftClose, LayoutGrid, Inbox, Send } from 'lucide-react';
import { foldingRows, useStore, openSession, setDialog, setTheme, setUi, archiveSession, pinSession, forkSession, deleteSession, confirmAction, renameSession, quickNewSession, showPage, showDispatch, dispatchNeeds } from '../lib/store';
import type { SessionSummary } from '../lib/types';
import { basename, timeAgo, tildify } from '../lib/format';
import { fuzzyScore } from '../lib/fuzzy';
import { Mark, StatusGlyph, useNow } from './ui/bits';
import { MenuButton, Popover, Menu } from './ui/Popover';
import { Collapse } from './ui/Collapse';

interface Group {
  key: string;
  label: string;
  path: string;
  items: SessionSummary[];
}

export function Sidebar() {
  const sessions = useStore((s) => s.sessions);
  const sessionsLoaded = useStore((s) => s.sessionsLoaded);
  const activeId = useStore((s) => s.activeId);
  const unread = useStore((s) => s.unread);
  const page = useStore((s) => s.page);
  const inboxUnread = useStore((s) => s.inboxUnread);
  const jobs = useStore((s) => s.jobs);
  const dispatchNeedsN = dispatchNeeds(jobs);
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const ui = useStore((s) => s.ui);
  const [q, setQ] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});
  const now = useNow(30_000);
  const list = useRef<HTMLElement>(null);
  useFlip(list, sessions, [q, collapsed, showAll, ui.showArchived, activeId]);

  const { attention, pinned, groups, archivedCount } = useMemo(() => {
    let list = sessions.filter((s) => (ui.showArchived ? s.archived : !s.archived));
    // Runs started by a schedule live in the inbox; the active one still shows so its row can be found.
    if (!q.trim()) list = list.filter((s) => (!s.scheduled && !s.dispatch) || s.id === activeId);
    if (q.trim()) list = list.filter((s) => fuzzyScore(q, `${s.title} ${basename(s.project || s.cwd)}`));
    // The conversation on screen never jumps into "Needs you": you are already looking at it, and its
    // row shows the amber mark where it is. Only other sessions gather there.
    const attention = list.filter((s) => s.id !== activeId && (s.status === 'requires_action' || (s.pending || 0) > 0));
    const pinned = list.filter((s) => s.pinned && !attention.includes(s));
    const rest = list.filter((s) => !attention.includes(s) && !pinned.includes(s));
    const map = new Map<string, Group>();
    for (const s of rest) {
      const key = s.project || s.cwd;
      if (!map.has(key)) map.set(key, { key, label: basename(key), path: key, items: [] });
      map.get(key)!.items.push(s);
    }
    const groups = [...map.values()].sort((a, b) => (b.items[0]?.lastModified || 0) - (a.items[0]?.lastModified || 0));
    return { attention, pinned, groups, archivedCount: sessions.filter((s) => s.archived).length };
  }, [sessions, q, ui.showArchived, activeId]);
  const working = sessions.filter((s) => !s.archived && (s.status === 'running' || s.status === 'starting')).length;
  const needsAll = sessions.filter((s) => !s.archived && (s.status === 'requires_action' || (s.pending || 0) > 0)).length;

  const theme = config?.theme || 'system';
  const nextTheme = theme === 'system' ? 'dark' : theme === 'dark' ? 'light' : 'system';
  const ThemeIcon = theme === 'system' ? Monitor : theme === 'dark' ? Moon : Sun;

  return (
    <aside className="sidebar" style={{ width: ui.sidebarW }}>
      <div className="side-top">
        <div className="brand">
          <Mark size={17} tight />
          <span className="brand-name grow">Pilcrow</span>
          <button className="icon-btn sm side-collapse" title="Hide sidebar (Ctrl+B)" aria-label="Hide sidebar" onClick={() => setUi({ sidebar: false })}>
            <PanelLeftClose size={15} />
          </button>
        </div>
        <div className="side-new-wrap">
          <button className="side-new" onClick={(e) => (e.shiftKey || e.altKey ? setDialog('newSession', {}) : quickNewSession())} title="New session in the current folder (Ctrl+N) · Shift+click for options">
            <SquarePen size={15} />
            <span className="grow">New session</span>
            <span className="side-kbd">Ctrl N</span>
          </button>
          <button className="side-new-opts" onClick={() => setDialog('newSession', {})} title="New session with options… (Ctrl+Shift+N)" aria-label="New session with options">
            <SlidersHorizontal size={13} />
          </button>
        </div>
        <label className="side-search">
          <Search size={15} />
          <input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && setQ('')} />
          {q && (
            <button onClick={() => setQ('')} aria-label="Clear search">
              <X size={12} />
            </button>
          )}
        </label>
        <div className="side-pages">
          <button className={`side-page${page === 'overview' ? ' on' : ''}`} onClick={() => showPage(page === 'overview' ? 'session' : 'overview')} title="Every session at a glance (Ctrl+Shift+O)">
            <LayoutGrid size={15} />
            <span className="grow">Control room</span>
            {needsAll > 0 ? <span className="side-page-n needs tnum">{needsAll}</span> : working > 0 ? <span className="side-page-n tnum">{working}</span> : null}
          </button>
          <button className={`side-page${page === 'inbox' ? ' on' : ''}`} onClick={() => showPage(page === 'inbox' ? 'session' : 'inbox')} title="Results of scheduled sessions">
            <Inbox size={15} />
            <span className="grow">Inbox</span>
            {inboxUnread > 0 && <span className="side-page-n unread tnum">{inboxUnread}</span>}
          </button>
          <button className={`side-page${page === 'dispatch' ? ' on' : ''}`} onClick={() => (page === 'dispatch' ? showPage('session') : showDispatch(null))} title="One job across many repositories (Ctrl+Shift+D)">
            <Send size={15} />
            <span className="grow">Dispatch</span>
            {dispatchNeedsN > 0 && <span className="side-page-n needs tnum">{dispatchNeedsN}</span>}
          </button>
        </div>
      </div>

      <nav className="side-list" ref={list}>
        {/* the highlight behind the open session: anchored to its row, it glides when another is chosen */}
        <span className="side-sel" aria-hidden="true" />
        {attention.length > 0 && (
          <Section label="Needs you" tone="accent">
            {attention.map((s) => (
              <Row key={s.id} s={s} active={s.id === activeId} unread={unread[s.id]} now={now} showProject q={q} />
            ))}
          </Section>
        )}
        {pinned.length > 0 && (
          <Section label="Pinned">
            {pinned.map((s) => (
              <Row key={s.id} s={s} active={s.id === activeId} unread={unread[s.id]} now={now} showProject q={q} />
            ))}
          </Section>
        )}
        {groups.map((g) => {
          const isCollapsed = collapsed[g.key];
          const limit = showAll[g.key] || q ? g.items.length : 6;
          return (
            <div key={g.key} className="side-group">
              <div className="side-group-head">
                <button className="side-group-toggle" onClick={() => setCollapsed({ ...collapsed, [g.key]: !isCollapsed })} title={tildify(g.path, info?.home)}>
                  <span className="ellipsis">{g.label}</span>
                  <ChevronRight size={12} className={`side-group-chev${isCollapsed ? '' : ' rot'}`} />
                </button>
                <button className="icon-btn sm side-group-add" title={`New session in ${g.label} · Shift+click for options`} onClick={(e) => (e.shiftKey || e.altKey ? setDialog('newSession', { cwd: g.path }) : quickNewSession(g.path))}>
                  <Plus size={13} />
                </button>
              </div>
              <Collapse open={!isCollapsed}>
                {() => (
                  <>
                    {g.items.slice(0, limit).map((s) => (
                      <Row key={s.id} s={s} active={s.id === activeId} unread={unread[s.id]} now={now} q={q} />
                    ))}
                    {g.items.length > limit && (
                      <button className="side-more" onClick={() => setShowAll({ ...showAll, [g.key]: true })}>
                        Show {g.items.length - limit} more
                      </button>
                    )}
                  </>
                )}
              </Collapse>
            </div>
          );
        })}
        {sessionsLoaded && !sessions.length && <div className="side-empty">No sessions yet. Start one to see it here.</div>}
        {sessions.length > 0 && !groups.length && !attention.length && !pinned.length && <div className="side-empty">{ui.showArchived ? 'Nothing archived.' : 'No matching sessions.'}</div>}
      </nav>

      <div className="side-foot">
        <button className="side-foot-btn" title="Settings (Ctrl+,)" onClick={() => setDialog('settings', true)}>
          <Settings size={15} />
          <span>Settings</span>
        </button>
        <div className="grow" />
        <button className={`icon-btn${ui.showArchived ? ' active' : ''}`} title={ui.showArchived ? 'Back to active sessions' : `Archived sessions (${archivedCount})`} onClick={() => setUi({ showArchived: !ui.showArchived })}>
          <Archive size={15} />
        </button>
        <button className="icon-btn" title={`Appearance: ${theme} — click for ${nextTheme}`} onClick={(e) => setTheme(nextTheme, { x: e.clientX, y: e.clientY })}>
          <ThemeIcon size={15} />
        </button>
      </div>
    </aside>
  );
}

function Section({ label, tone, children }: { label: string; tone?: string; children: React.ReactNode }) {
  return (
    <div className={`side-group section ${tone || ''}`}>
      <div className="side-section-label">
        <span>{label}</span>
      </div>
      {children}
    </div>
  );
}

const Row = memo(function Row({ s, active, unread, now, showProject, q }: { s: SessionSummary; active: boolean; unread?: boolean; now: number; showProject?: boolean; q?: string }) {
  const [renaming, setRenaming] = useState(false);
  // Right-click opens the same actions as the ⋯ button, at the pointer.
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const pointAnchor = useMemo(() => (menuAt ? ({ getBoundingClientRect: () => new DOMRect(menuAt.x, menuAt.y, 0, 0), contains: () => false } as unknown as HTMLElement) : null), [menuAt]);
  const [title, setTitle] = useState(s.title);
  const items = [
    { label: 'Rename', icon: Pencil, onSelect: () => (setTitle(s.title), setRenaming(true)) },
    { label: s.pinned ? 'Unpin' : 'Pin to top', icon: s.pinned ? PinOff : Pin, onSelect: () => pinSession(s.id, !s.pinned) },
    { label: 'Fork', icon: GitFork, onSelect: () => forkSession(s.id) },
    { label: s.archived ? 'Unarchive' : 'Archive', icon: s.archived ? ArchiveRestore : Archive, onSelect: () => archiveSession(s.id, !s.archived) },
    { separator: true },
    {
      label: 'Delete…',
      icon: Trash,
      danger: true,
      onSelect: () =>
        confirmAction({
          title: 'Delete this session?',
          body: 'The conversation history is removed. Files Claude changed stay as they are.',
          confirm: 'Delete session',
          danger: true,
          checkbox: s.worktree ? { label: `Also remove the worktree and branch ${s.worktree.branch}`, value: false } : undefined,
          onConfirm: (checked) => deleteSession(s.id, checked),
        }),
    },
  ];
  return (
    <div
      className={`side-row${active ? ' active' : ''}${unread && !active ? ' unread' : ''}${menuAt ? ' menu-open' : ''}`}
      data-sid={s.id}
      onContextMenu={(e) => {
        if (renaming) return;
        e.preventDefault();
        setMenuAt({ x: e.clientX, y: e.clientY });
      }}
    >
      <button className="side-row-main" onClick={() => openSession(s.id)}>
        <span className="side-row-text">
          {renaming ? (
            <input
              className="input side-rename"
              autoFocus
              value={title}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => {
                setRenaming(false);
                if (title.trim() && title !== s.title) renameSession(s.id, title.trim());
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                if (e.key === 'Escape') setRenaming(false);
              }}
            />
          ) : (
            <span className="side-row-title">{q ? <Highlighted text={s.title} q={q} /> : s.title}</span>
          )}
          {(showProject || s.worktree || (s.gitBranch && !['main', 'master', 'HEAD'].includes(s.gitBranch))) && (
            <span className="side-row-meta">
              {showProject && <span className="ellipsis">{basename(s.project || s.cwd)}</span>}
              {s.worktree ? (
                <span className="side-wt" title={`Worktree branch ${s.worktree.branch}`}>
                  <FolderGit2 size={11} /> <span className="ellipsis">{s.worktree.branch.replace(/^pilcrow\//, '')}</span>
                </span>
              ) : s.gitBranch && !['main', 'master', 'HEAD'].includes(s.gitBranch) ? (
                <span className="side-wt">
                  <GitBranch size={11} /> <span className="ellipsis">{s.gitBranch}</span>
                </span>
              ) : null}
            </span>
          )}
        </span>
      </button>
      <span className="side-row-end">
        <span className="side-row-status">
          <StatusGlyph status={s.status} pending={s.pending} unread={unread && !active} />
          {!(s.status === 'running' || s.status === 'starting' || s.status === 'requires_action' || s.pending || s.status === 'error' || (unread && !active)) && <span className="side-time">{timeAgo(s.lastModified, now, true)}</span>}
        </span>
        <MenuButton items={items} className="icon-btn sm side-row-menu" title="Session actions" width={210}>
          <Ellipsis size={14} />
        </MenuButton>
      </span>
      <Popover anchor={pointAnchor} open={Boolean(menuAt)} onClose={() => setMenuAt(null)} placement="bottom-start" width={210}>
        <Menu items={items} onClose={() => setMenuAt(null)} />
      </Popover>
    </div>
  );
});

function Highlighted({ text, q }: { text: string; q: string }) {
  const i = text.toLocaleLowerCase().indexOf(q.trim().toLocaleLowerCase());
  if (i < 0 || !q.trim()) return <>{text}</>;
  const n = q.trim().length;
  return (
    <>
      {text.slice(0, i)}
      <mark className="side-match">{text.slice(i, i + n)}</mark>
      {text.slice(i + n)}
    </>
  );
}

/**
 * When sessions reorder (one starts working and moves up, one needs you and joins that section),
 * rows glide to their new places instead of jumping (FLIP). Only data changes animate; typing in the
 * search box or collapsing a group just re-measures.
 */
function useFlip(list: React.RefObject<HTMLElement | null>, data: unknown, layoutDeps: unknown[]) {
  const last = useRef<Map<string, number>>(new Map());
  const lastData = useRef<unknown>(null);
  const lastShape = useRef('');
  const lastOrder = useRef('');
  const pendingMeasure = useRef<number | null>(null);
  // Measured only when the rows can have moved (new data, a search, a group folded): a measure forces
  // a layout of the whole page, which is dear while a reply is streaming.
  useLayoutEffect(() => {
    const root = list.current;
    if (!root) return;
    // Layout positions, not bounding rects: a row still gliding from the last change carries a
    // transform, and measuring through it would start the next glide from the wrong place.
    const topOf = (el: HTMLElement) => {
      let y = 0;
      for (let n: HTMLElement | null = el; n && n !== root; n = n.offsetParent as HTMLElement | null) y += n.offsetTop;
      return y;
    };
    const rows = Array.from(root.querySelectorAll<HTMLElement>('[data-sid]'));
    const measure = () => {
      const next = new Map<string, number>();
      for (const el of rows) if (!next.has(el.dataset.sid!)) next.set(el.dataset.sid!, topOf(el));
      return next;
    };
    // When a section appears or disappears ("Needs you", "Pinned"), headers move too; gliding rows past
    // headers that jump would look broken, so that change is shown as a plain re-layout.
    const shape = Array.from(root.querySelectorAll('.side-section-label, .side-group-head'), (h) => h.textContent).join('|');
    // Only a change in which rows are listed, or in what order, is worth a glide: a re-render that
    // keeps the same rows must not restart one. A row that folded itself away before leaving has
    // already closed the gap under it, so its departure is shown as plain layout too.
    const order = rows.map((el) => el.dataset.sid).join();
    const ids = new Set(rows.map((el) => el.dataset.sid!));
    const left = [...last.current.keys()].filter((id) => !ids.has(id));
    const animate = lastData.current !== null && lastData.current !== data && last.current.size > 0 && order !== lastOrder.current && shape === lastShape.current && !left.some((id) => foldingRows.has(id)) && !reducedMotion();
    lastShape.current = shape;
    lastOrder.current = order;
    lastData.current = data;
    if (!animate) {
      // Nothing to glide: remember the rows' places once the frame has painted, when layout is
      // already done and the read costs nothing. (Read here, it would force a layout mid-stream.)
      if (pendingMeasure.current != null) cancelAnimationFrame(pendingMeasure.current);
      pendingMeasure.current = requestAnimationFrame(() => {
        // the frame has painted by the time this timeout runs; rAF alone would still be before layout
        setTimeout(() => {
          pendingMeasure.current = null;
          if (root.isConnected) last.current = measure();
        }, 0);
      });
      return;
    }
    if (pendingMeasure.current != null) {
      cancelAnimationFrame(pendingMeasure.current);
      pendingMeasure.current = null;
    }
    const next = measure();
    {
      let moved = false;
      for (const el of rows) {
        const id = el.dataset.sid!;
        const before = last.current.get(id);
        const after = next.get(id)!;
        if (before !== undefined && Math.abs(before - after) > 1) {
          el.animate([{ transform: `translateY(${before - after}px)` }, { transform: 'none' }], { duration: 320, easing: EASE });
          moved = true;
        }
      }
      // The selection highlight follows layout, not the glide; it steps aside until the rows have landed.
      if (moved) {
        root.classList.add('flipping');
        setTimeout(() => root.classList.remove('flipping'), 340);
      }
    }
    last.current = next;
  }, [data, ...layoutDeps]); // eslint-disable-line react-hooks/exhaustive-deps
}

