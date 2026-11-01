import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { WifiOff, RefreshCw } from 'lucide-react';
import { useStore, setUi, setDialog, native, quickNewSession } from '../lib/store';
import { isBusy } from '../lib/types';
import { shortcutFor } from '../lib/shortcuts';
import { Sidebar } from './Sidebar';
import { SessionView } from './SessionView';
import { RightPanel } from './RightPanel';
import { Welcome } from './Welcome';
import { NewSessionDialog } from './NewSessionDialog';
import { McpAuthDialog } from './McpAuthDialog';
import { CommandPalette, ShortcutsDialog, ConfirmDialog, Toasts, Lightbox } from './Overlays';
import { Resizer } from './ui/bits';
import { useWindowKey } from './ui/keys';
import { Slot } from './ui/Slot';
import { ErrorBoundary } from './ui/ErrorBoundary';

// The pages and the settings sheet a window may never open load on first use, each a chunk of its own,
// so a window parses only what the conversation needs before its first rows.
const Overview = lazy(() => import('./Overview').then((m) => ({ default: m.Overview })));
const InboxPage = lazy(() => import('./Inbox').then((m) => ({ default: m.InboxPage })));
const DispatchPage = lazy(() => import('./dispatch/Page').then((m) => ({ default: m.DispatchPage })));
const SettingsDialog = lazy(() => import('./SettingsDialog').then((m) => ({ default: m.SettingsDialog })));

const PAGE_NAMES = { session: 'The conversation', overview: 'The control room', inbox: 'The inbox', dispatch: 'Dispatch' };

export function App() {
  // The layout fields read here, each on its own: dragging a width re-renders only what shows it.
  const sidebar = useStore((s) => s.ui.sidebar);
  const panel = useStore((s) => s.ui.panel);
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

  useWindowKey((e) => {
    const hit = shortcutFor(e);
    if (!hit) return;
    e.preventDefault();
    hit.run(hit.index);
  });

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
  const panelOpen = Boolean(session && page === 'session' && panel);
  const lastPanelSession = useRef(session);
  if (session) lastPanelSession.current = session;
  const panelSession = session || lastPanelSession.current;
  const shell = (
    <div className={`app${sidebar ? '' : ' no-side'}`}>
      <Slot open={sidebar} side="left">
        <Sidebar />
      </Slot>
      {sidebar && <Resizer side="left" onResize={resizeSidebar} />}
      <main className="main">
        {!connected && everConnected && (
          <div className="offline">
            <WifiOff size={14} /> Lost the connection to Pilcrow's background process. Reconnecting…
          </div>
        )}
        <UpdateNotice />
        {/* A page that fails to draw says so in place; the sidebar and the rest of the window keep working. */}
        <ErrorBoundary key={page === 'session' ? session?.id : page} label={PAGE_NAMES[page]}>
          <Suspense fallback={null}>{page === 'overview' ? <Overview /> : page === 'inbox' ? <InboxPage /> : page === 'dispatch' ? <DispatchPage /> : session ? <SessionView key={session.id} session={session} /> : <Welcome />}</Suspense>
        </ErrorBoundary>
        <Toasts />
      </main>
      {panelOpen && <Resizer side="right" onResize={resizePanel} />}
      <Slot open={panelOpen} side="right" className="panel-shell">
        {panelSession && (
          <ErrorBoundary key={panelSession.id} label="The side panel">
            <RightPanel session={panelSession} />
          </ErrorBoundary>
        )}
      </Slot>
      {dialogs.newSession && <NewSessionDialog spec={dialogs.newSession} />}
      {dialogs.settings && config && (
        <Suspense fallback={null}>
          <SettingsDialog config={config} />
        </Suspense>
      )}
      {dialogs.palette && <CommandPalette />}
      {dialogs.shortcuts && <ShortcutsDialog />}
      {dialogs.confirm && <ConfirmDialog spec={dialogs.confirm} />}
      {dialogs.mcpAuth && <McpAuthDialog spec={dialogs.mcpAuth} />}
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
  const busy = useStore((s) => s.sessions.some((x) => isBusy(x.status)));
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
        sessionStorage.setItem('pilcrow.reloadedFor', server || '');
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
