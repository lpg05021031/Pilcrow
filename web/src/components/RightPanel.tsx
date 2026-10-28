import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { GitCompare, FolderTree, SquareTerminal, AppWindow, ListChecks, Info, X, History } from 'lucide-react';
import { useStore, setUi, type PanelTab } from '../lib/store';
import type { SessionSummary } from '../lib/types';
import { EASE, reducedMotion } from '../lib/motion';
import { ChangesPanel } from './panels/ChangesPanel';
import { FilesPanel } from './panels/FilesPanel';
import { TerminalPanel } from './panels/TerminalPanel';
import { PreviewPanel } from './panels/PreviewPanel';
import { TasksPanel } from './panels/TasksPanel';
import { InfoPanel } from './panels/InfoPanel';
import { HistoryPanel } from './panels/HistoryPanel';
import type { LucideIcon } from 'lucide-react';

const TABS: { id: PanelTab; label: string; icon: LucideIcon; key: string }[] = [
  { id: 'changes', label: 'Changes', icon: GitCompare, key: '1' },
  { id: 'files', label: 'Files', icon: FolderTree, key: '2' },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal, key: '3' },
  { id: 'preview', label: 'Preview', icon: AppWindow, key: '4' },
  { id: 'tasks', label: 'Tasks', icon: ListChecks, key: '5' },
  { id: 'history', label: 'Timeline', icon: History, key: '6' },
  { id: 'info', label: 'Session', icon: Info, key: '7' },
];

export function RightPanel({ session }: { session: SessionSummary }) {
  const ui = useStore((s) => s.ui);
  const rt = useStore((s) => s.rt[session.id]);
  const cwd = rt?.cwd || session.cwd;
  const [mounted, setMounted] = useState<Record<string, boolean>>({ [ui.panelTab]: true });
  useEffect(() => setMounted((m) => (m[ui.panelTab] ? m : { ...m, [ui.panelTab]: true })), [ui.panelTab]);
  const running = (rt?.tasks || []).filter((t) => t.status === 'running').length;
  const tabsRef = useRef<HTMLDivElement>(null);
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
      <div className="panel-tabs" role="tablist" ref={tabsRef}>
        {ink && <span className="panel-ink" style={{ transform: `translateX(${ink.left}px)`, width: ink.width }} />}
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={ui.panelTab === t.id} className={`panel-tab${ui.panelTab === t.id ? ' on' : ''}`} onClick={() => setUi({ panelTab: t.id })} title={`${t.label} (Alt+${t.key})`}>
            <t.icon size={15} className="panel-tab-ico" />
            <span className="panel-tab-label">{t.label}</span>
            {t.id === 'tasks' && running > 0 && <span className="tab-count">{running}</span>}
          </button>
        ))}
        <div className="grow" />
        <button className="icon-btn sm" title="Close panel" onClick={() => setUi({ panel: false })}>
          <X size={14} />
        </button>
      </div>
      <div className="panel-body" ref={bodyRef}>
        {ui.panelTab === 'changes' && <ChangesPanel session={session} cwd={cwd} />}
        {mounted.files && (
          <div className="panel-keep" style={{ display: ui.panelTab === 'files' ? 'flex' : 'none' }}>
            <FilesPanel session={session} cwd={cwd} />
          </div>
        )}
        {mounted.terminal && (
          <div className="panel-keep" style={{ display: ui.panelTab === 'terminal' ? 'flex' : 'none' }}>
            <TerminalPanel session={session} cwd={cwd} visible={ui.panelTab === 'terminal'} />
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
