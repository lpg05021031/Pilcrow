import { lazy, Suspense, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { GitCompare, FolderTree, SquareTerminal, AppWindow, ListChecks, Info, X, History } from 'lucide-react';
import { useStore, setUi, type PanelTab } from '../lib/store';
import { SHORTCUTS } from '../lib/shortcuts';
import type { SessionSummary } from '../lib/types';
import { EASE, reducedMotion } from '../lib/motion';
import { ChangesPanel } from './panels/ChangesPanel';
import { FilesPanel } from './panels/FilesPanel';
import { PreviewPanel } from './panels/PreviewPanel';
import { TasksPanel } from './panels/TasksPanel';
import { InfoPanel } from './panels/InfoPanel';
import { HistoryPanel } from './panels/HistoryPanel';
import type { LucideIcon } from 'lucide-react';

// xterm is a third of a megabyte and only this tab uses it: it loads the first time the tab opens.
const TerminalPanel = lazy(() => import('./panels/TerminalPanel').then((m) => ({ default: m.TerminalPanel })));

// In the order of the store's PANEL_TABS, which the Alt+1…7 shortcuts follow.
const TABS: { id: PanelTab; label: string; icon: LucideIcon }[] = [
  { id: 'changes', label: 'Changes', icon: GitCompare },
  { id: 'files', label: 'Files', icon: FolderTree },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal },
  { id: 'preview', label: 'Preview', icon: AppWindow },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
  { id: 'history', label: 'Timeline', icon: History },
  { id: 'info', label: 'Session', icon: Info },
];

export function RightPanel({ session }: { session: SessionSummary }) {
  const ui = useStore((s) => s.ui);
  // Narrow reads: the runtime record changes with every streamed frame, these two rarely.
  const cwd = useStore((s) => s.rt[session.id]?.cwd) || session.cwd;
  const running = useStore((s) => (s.rt[session.id]?.tasks || []).filter((t) => t.status === 'running').length);
  const [mounted, setMounted] = useState<Record<string, boolean>>({ [ui.panelTab]: true });
  useEffect(() => setMounted((m) => (m[ui.panelTab] ? m : { ...m, [ui.panelTab]: true })), [ui.panelTab]);
  const tabsRef = useRef<HTMLDivElement>(null);
  const ids = useId();
  // Left and Right (Home, End) move along the tabs and show each as they go, the focus with them.
  const onTabKey = (e: React.KeyboardEvent) => {
    const at = TABS.findIndex((t) => t.id === ui.panelTab);
    const to = e.key === 'ArrowRight' ? (at + 1) % TABS.length : e.key === 'ArrowLeft' ? (at - 1 + TABS.length) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    setUi({ panelTab: TABS[to].id });
    document.getElementById(`${ids}-${TABS[to].id}`)?.focus();
  };
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const el = tabsRef.current?.querySelector<HTMLElement>('.panel-tab.on');
      if (el) setInk({ left: el.offsetLeft, width: el.offsetWidth });
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (tabsRef.current) ro.observe(tabsRef.current);
    // The chosen tab grows its name in a narrow panel; follow it as it settles.
    const on = tabsRef.current?.querySelector<HTMLElement>('.panel-tab.on');
    if (on) ro.observe(on);
    return () => ro.disconnect();
  }, [ui.panelTab]);

  // The chosen tab's content settles in under the ink; the first tab shown simply is there.
  const bodyRef = useRef<HTMLDivElement>(null);
  const firstTab = useRef(true);
  useLayoutEffect(() => {
    if (firstTab.current) {
      firstTab.current = false;
      return;
    }
    if (reducedMotion()) return;
    const el = Array.from(bodyRef.current?.children || []).find((c) => (c as HTMLElement).style.display !== 'none') as HTMLElement | undefined;
    el?.animate([{ opacity: 0, transform: 'translateY(5px)' }, { opacity: 1, transform: 'none' }], { duration: 220, easing: EASE });
  }, [ui.panelTab]);

  return (
    <section className="panel" style={{ width: ui.panelW }}>
      <div className="panel-tabs" role="tablist" aria-label="Side panel" ref={tabsRef} onKeyDown={onTabKey}>
        {ink && <span className="panel-ink" style={{ transform: `translateX(${ink.left}px)`, width: ink.width }} />}
        {TABS.map((t, i) => (
          <button key={t.id} id={`${ids}-${t.id}`} role="tab" aria-selected={ui.panelTab === t.id} aria-controls={`${ids}-panel`} tabIndex={ui.panelTab === t.id ? 0 : -1} className={`panel-tab${ui.panelTab === t.id ? ' on' : ''}`} onClick={() => setUi({ panelTab: t.id })} title={`${t.label} (${SHORTCUTS.tabs.combos[i]})`}>
            <t.icon size={15} className="panel-tab-ico" aria-hidden="true" />
            <span className="panel-tab-label">{t.label}</span>
            {t.id === 'tasks' && running > 0 && <span className="tab-count">{running}</span>}
          </button>
        ))}
        <div className="grow" />
        <button className="icon-btn sm" title="Close panel" aria-label="Close panel" onClick={() => setUi({ panel: false })}>
          <X size={14} />
        </button>
      </div>
      <div className="panel-body" ref={bodyRef} role="tabpanel" id={`${ids}-panel`} aria-labelledby={`${ids}-${ui.panelTab}`}>
        {ui.panelTab === 'changes' && <ChangesPanel session={session} cwd={cwd} />}
        {mounted.files && (
          <div className="panel-keep" style={{ display: ui.panelTab === 'files' ? 'flex' : 'none' }}>
            <FilesPanel session={session} cwd={cwd} visible={ui.panelTab === 'files'} />
          </div>
        )}
        {mounted.terminal && (
          <div className="panel-keep" style={{ display: ui.panelTab === 'terminal' ? 'flex' : 'none' }}>
            <Suspense fallback={null}>
              <TerminalPanel session={session} cwd={cwd} visible={ui.panelTab === 'terminal'} />
            </Suspense>
          </div>
        )}
        {ui.panelTab === 'preview' && <PreviewPanel session={session} />}
        {ui.panelTab === 'tasks' && <TasksPanel session={session} />}
        {ui.panelTab === 'history' && <HistoryPanel session={session} cwd={cwd} />}
        {ui.panelTab === 'info' && <InfoPanel session={session} cwd={cwd} />}
      </div>
    </section>
  );
}
