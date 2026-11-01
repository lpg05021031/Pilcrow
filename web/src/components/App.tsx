import { useCallback, useEffect, useRef, useState } from 'react';
import { WifiOff, RefreshCw } from 'lucide-react';
import { useStore, setUi, setDialog, openSession, interrupt, native, quickNewSession, type PanelTab, cycleView, showPage, showDispatch } from '../lib/store';
import { Sidebar } from './Sidebar';
import { SessionView } from './SessionView';
import { RightPanel } from './RightPanel';
import { Welcome } from './Welcome';
import { Overview } from './Overview';
import { InboxPage } from './Inbox';
import { DispatchPage } from './dispatch/Page';
import { NewSessionDialog } from './NewSessionDialog';
import { SettingsDialog } from './SettingsDialog';
import { McpAuthDialog } from './McpAuthDialog';
import { CommandPalette, ShortcutsDialog, ConfirmDialog, Toasts, Lightbox, quitApp } from './Overlays';
import { Resizer } from './ui/bits';
import { Slot } from './ui/Slot';

const TAB_KEYS: Record<string, PanelTab> = { '1': 'changes', '2': 'files', '3': 'terminal', '4': 'preview', '5': 'tasks', '6': 'history', '7': 'info' };

export function App() {
  const ui = useStore((s) => s.ui);
  const dialogs = useStore((s) => s.dialogs);
  const activeId = useStore((s) => s.activeId);
  const sessions = useStore((s) => s.sessions);
  const connected = useStore((s) => s.connected);
  const everConnected = useStore((s) => s.everConnected);
  const config = useStore((s) => s.config);
  const page = useStore((s) => s.page);
  const session = sessions.find((s) => s.id === activeId) || null;
  const waiting = sessions.filter((s) => s.status === 'requires_action').length;

  useEffect(() => {
    document.title = `${waiting ? `(${waiting}) ` : ''}${page === 'overview' ? 'Control room — ' : page === 'inbox' ? 'Inbox — ' : page === 'dispatch' ? 'Dispatch — ' : session ? `${session.title} — ` : ''}Pilcrow`;
  }, [waiting, session?.title, page]); // eslint-disable-line react-hooks/exhaustive-deps -- only the title is read

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const st = useStore.getState();
      if (mod && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setDialog('palette', !st.dialogs.palette);
      } else if (mod && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        if (e.shiftKey) setDialog('newSession', {});
        else quickNewSession();
      } else if (mod && e.key.toLowerCase() === 'b' && !e.shiftKey) {
        e.preventDefault();
        setUi({ sidebar: !st.ui.sidebar });
      } else if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        cycleView();
      } else if (mod && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        showPage(st.page === 'overview' ? 'session' : 'overview');
      } else if (mod && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'd') {
        e.preventDefault();
        if (st.page === 'dispatch') showPage('session');
        else showDispatch(null);
      } else if (mod && e.key === '\\') {
        e.preventDefault();
        setUi({ panel: !st.ui.panel });
      } else if (mod && e.key === ',') {
        e.preventDefault();
        setDialog('settings', true);
      } else if (mod && !e.shiftKey && e.key.toLowerCase() === 'q' && !native()) {
        // In the desktop app the window menu owns Ctrl+Q; in a browser window we ask the server to quit.
        e.preventDefault();
        quitApp();
      } else if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'f' && st.activeId) {
        // The file editor and the terminal keep their own Ctrl+F.
        const t = e.target as HTMLElement | null;
        if (t?.closest?.('.cm-editor, .xterm')) return;
        e.preventDefault();
        window.dispatchEvent(new Event('pilcrow:find'));
      } else if (mod && e.key === '/') {
        e.preventDefault();
        setDialog('shortcuts', true);
      } else if (e.altKey && !mod && TAB_KEYS[e.key] && st.activeId) {
        e.preventDefault();
        const tab = TAB_KEYS[e.key];
        setUi(st.ui.panel && st.ui.panelTab === tab ? { panel: false } : { panel: true, panelTab: tab });
      } else if (mod && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        const list = st.sessions.filter((s) => !s.archived);
        const i = list.findIndex((s) => s.id === st.activeId);
        const next = list[e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1)];
        if (next) openSession(next.id);
      } else if (e.key === 'Escape' && st.activeId && !Object.values(st.dialogs).some(Boolean)) {
        const rt = st.rt[st.activeId];
        const target = e.target as HTMLElement;
        const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
        if (!typing && (rt?.status === 'running' || rt?.status === 'starting')) interrupt(st.activeId);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Commands from the desktop window menu.
  useEffect(
    () =>
      native()?.onMenu?.((command) => {
        const st = useStore.getState();
        if (command === 'new-session') quickNewSession();
        else if (command === 'palette') setDialog('palette', true);
        else if (command === 'settings') setDialog('settings', true);
        else if (command === 'diagnostics') setDialog('settings', 'diagnostics');
        else if (command === 'shortcuts') setDialog('shortcuts', true);
        else if (command === 'toggle-sidebar') setUi({ sidebar: !st.ui.sidebar });
        else if (command === 'toggle-panel') setUi({ panel: !st.ui.panel });
      }),
    [],
  );

  const resizeSidebar = useCallback((dx: number) => setUi({ sidebarW: Math.max(200, Math.min(440, useStore.getState().ui.sidebarW + dx)) }), []);
  const resizePanel = useCallback((dx: number) => setUi({ panelW: Math.max(340, Math.min(Math.round(window.innerWidth * 0.62), useStore.getState().ui.panelW - dx)) }), []);

  const booting = !config && !everConnected;
  const [bootLeaving, setBootLeaving] = useState(false);
  const [bootDone, setBootDone] = useState(false);
  useEffect(() => {
    if (booting || bootDone) return;
    // The mark has finished arriving; let it leave before the app rises underneath.
    setBootLeaving(true);
    const t = setTimeout(() => setBootDone(true), matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 320);
    return () => clearTimeout(t);
  }, [booting, bootDone]);
  // The side panel glides shut when the page changes away from a session; it keeps showing that
  // session while it goes, rather than emptying first.
  const panelOpen = Boolean(session && page === 'session' && ui.panel);
  const lastPanelSession = useRef(session);
  if (session) lastPanelSession.current = session;
  const panelSession = session || lastPanelSession.current;
  const shell = (
    <div className={`app${ui.sidebar ? '' : ' no-side'}`}>
      <Slot open={ui.sidebar} side="left">
        <Sidebar />
      </Slot>
      {ui.sidebar && <Resizer side="left" onResize={resizeSidebar} />}
      <main className="main">
        {!connected && everConnected && (
          <div className="offline">
            <WifiOff size={14} /> Lost the connection to Pilcrow's background process. Reconnecting…
          </div>
        )}
        <UpdateNotice />
        {page === 'overview' ? <Overview /> : page === 'inbox' ? <InboxPage /> : page === 'dispatch' ? <DispatchPage /> : session ? <SessionView key={session.id} session={session} /> : <Welcome />}
        <Toasts />
      </main>
      {panelOpen && <Resizer side="right" onResize={resizePanel} />}
      <Slot open={panelOpen} side="right" className="panel-shell">
        {panelSession && <RightPanel key={panelSession.id} session={panelSession} />}
      </Slot>
      {dialogs.newSession && <NewSessionDialog />}
      {dialogs.settings && config && <SettingsDialog />}
      {dialogs.palette && <CommandPalette />}
      {dialogs.shortcuts && <ShortcutsDialog />}
      {dialogs.confirm && <ConfirmDialog />}
      {dialogs.mcpAuth && <McpAuthDialog />}
      <Lightbox />
    </div>
  );
  if (booting || !bootDone) {
    return (
      <>
        {!booting && shell}
        <Boot connected={connected} leaving={bootLeaving} />
      </>
    );
  }
  return shell;
}

/**
 * The opening: the bowl of the mark arrives first, the stems set down after it, the name settles in
 * under them. While the process is still starting, the bowl breathes; once connected, the whole
 * thing lifts away and the app rises in its place.
 */
function Boot({ connected, leaving }: { connected: boolean; leaving: boolean }) {
  const [slow, setSlow] = useState(0);
  useEffect(() => {
    const a = setTimeout(() => setSlow(1), 5000);
    const b = setTimeout(() => setSlow(2), 16000);
    return () => {
      clearTimeout(a);
      clearTimeout(b);
    };
  }, []);
  const line = connected ? 'Loading your sessions' : slow >= 2 ? 'Still trying to reach the background process. If this keeps up, quit Pilcrow and open it again.' : slow === 1 ? 'Starting the background process — the first launch takes a moment' : 'Connecting';
  return (
    <div className={`boot${leaving ? ' leaving' : ''}${connected ? ' linked' : ''}`} role="status" aria-live="polite">
      <div className="boot-stage">
        <svg className="boot-mark" viewBox="4.5 4 14 16" width="44" height="44" aria-hidden="true">
          <circle className="boot-bowl" cx="10.6" cy="9.2" r="4.1" />
          <rect className="boot-bar" x="10.6" y="5.1" width="6.6" height="1.9" rx="0.5" />
          <rect className="boot-stem s1" x="12.4" y="5.1" width="1.9" height="13.8" rx="0.5" />
          <rect className="boot-stem s2" x="15.3" y="5.1" width="1.9" height="13.8" rx="0.5" />
        </svg>
        <div className="boot-name">Pilcrow</div>
      </div>
      <div className={`boot-line${slow >= 2 ? ' long' : ''}`} key={line}>
        {line}
      </div>
    </div>
  );
}

/** "1.2.10" vs "1.2.9", numerically. */
function compareVersions(a: string, b: string) {
  const pa = a.split('.').map((n) => parseInt(n, 10) || 0);
  const pb = b.split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
  return 0;
}

/**
 * After an update, a window and the background process can briefly be different versions: an old window
 * reconnected to a new process, or new files served by a process that started before the update.
 */
function UpdateNotice() {
  const connected = useStore((s) => s.connected);
  const server = useStore((s) => s.info?.version);
  const busy = useStore((s) => s.sessions.some((x) => x.status === 'running' || x.status === 'starting' || x.status === 'requires_action'));
  const newer = Boolean(connected && server && server !== __APP_VERSION__ && compareVersions(server, __APP_VERSION__) > 0);
  // A newer Pilcrow is installed and nothing is mid-flight: pick it up without asking. While Claude
  // is working, the window stays as it is and the line below offers the reload instead.
  useEffect(() => {
    if (!newer || busy) return;
    // One automatic reload per server version. If the window comes back still older (a build that
    // does not match its server), the line below stays and nothing loops.
    let tried = '';
    try {
      tried = sessionStorage.getItem('pilcrow.reloadedFor') || '';
    } catch {
      /* storage unavailable */
    }
    if (tried === server) return;
    const t = setTimeout(() => {
      try {
        sessionStorage.setItem('pilcrow.reloadedFor', server!);
      } catch {
        /* fine */
      }
      location.reload();
    }, 1200);
    return () => clearTimeout(t);
  }, [newer, busy, server]);
  if (!connected || !server || server === __APP_VERSION__) return null;
  if (newer) {
    return (
      <div className="offline update">
        <RefreshCw size={14} />
        {busy ? (
          <>
            Pilcrow {server} is installed. It takes over when you{' '}
            <button className="link-btn" onClick={() => location.reload()}>
              reload this window
            </button>
            {' '}— a running session continues in the background.
          </>
        ) : (
          <>
            Pilcrow {server} is installed.{' '}
            <button className="link-btn" onClick={() => location.reload()}>
              Reload this window
            </button>{' '}
            to use it.
          </>
        )}
      </div>
    );
  }
  return (
    <div className="offline update">
      <RefreshCw size={14} /> Pilcrow {__APP_VERSION__} is installed, but its background process is still {server}. Quit Pilcrow and open it again to finish updating.
    </div>
  );
}
