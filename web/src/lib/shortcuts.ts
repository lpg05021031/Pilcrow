// The keyboard shortcuts, in one table: the window's key handler (App) runs the ones with `run`, the
// command palette shows their keys, and the Keyboard shortcuts sheet lists every entry in this order.
import { useStore, setUi, setDialog, openSession, interrupt, native, quickNewSession, cycleView, showPage, showDispatch, openFind, toasted, quitApp, PANEL_TABS } from './store';
import { isWorking } from './types';

export interface Shortcut {
  /** As shown: modifiers first, then the key ("Ctrl+Shift+N"). Ctrl also means ⌘ on a Mac. */
  keys: string;
  label: string;
  /** The key presses that trigger it, when they differ from `keys` (a range, a pair of arrows). */
  combos?: string[];
  /** Left out for keys that work where they apply (the composer, a permission card, the editor). */
  run?: (index: number) => void;
  /** Only while this holds; otherwise the key goes on to the page. */
  when?: (e: KeyboardEvent) => boolean;
}

const st = () => useStore.getState();
const inSession = () => Boolean(st().activeId);
const activeStatus = () => {
  const id = st().activeId;
  return id ? st().rt[id]?.status : undefined;
};
const typing = (e: KeyboardEvent) => {
  const t = e.target as HTMLElement | null;
  return Boolean(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable));
};

export const SHORTCUTS = {
  palette: { keys: 'Ctrl+K', label: 'Command palette', run: () => setDialog('palette', !st().dialogs.palette) },
  newSession: { keys: 'Ctrl+N', label: 'New session', run: () => quickNewSession() },
  newSessionWith: { keys: 'Ctrl+Shift+N', label: 'New session with options', run: () => setDialog('newSession', {}) },
  // The file editor and the terminal keep their own Ctrl+F.
  find: { keys: 'Ctrl+F', label: 'Find in the conversation', run: () => openFind(), when: (e) => inSession() && !(e.target as HTMLElement | null)?.closest?.('.cm-editor, .xterm') },
  sidebar: { keys: 'Ctrl+B', label: 'Toggle sidebar', run: () => setUi({ sidebar: !st().ui.sidebar }) },
  panel: { keys: 'Ctrl+\\', label: 'Toggle side panel', run: () => setUi({ panel: !st().ui.panel }) },
  view: { keys: 'Ctrl+O', label: 'Cycle view: Normal, Thinking, Verbose', run: () => cycleView() },
  tabs: {
    keys: 'Alt+1 … 7',
    label: 'Changes, Files, Terminal, Preview, Tasks, Timeline, Session',
    combos: PANEL_TABS.map((_, i) => `Alt+${i + 1}`),
    when: inSession,
    run: (i) => {
      const { panel, panelTab } = st().ui;
      setUi(panel && panelTab === PANEL_TABS[i] ? { panel: false } : { panel: true, panelTab: PANEL_TABS[i] });
    },
  },
  board: { keys: 'Ctrl+Shift+O', label: 'Workboard', run: () => showPage(st().page === 'board' ? 'session' : 'board') },
  dispatch: { keys: 'Ctrl+Shift+D', label: 'Dispatch', run: () => (st().page === 'dispatch' ? showPage('session') : showDispatch(null)) },
  nextSession: {
    keys: 'Ctrl+Alt+↑ / ↓',
    label: 'Previous / next session',
    combos: ['Ctrl+Alt+ArrowUp', 'Ctrl+Alt+ArrowDown'],
    run: (i) => {
      const list = st().sessions.filter((s) => !s.archived);
      const at = list.findIndex((s) => s.id === st().activeId);
      const next = list[i ? Math.min(list.length - 1, at + 1) : Math.max(0, at - 1)];
      if (next) openSession(next.id).catch(toasted);
    },
  },
  send: { keys: 'Enter', label: 'Send (Shift+Enter for a new line)' },
  mode: { keys: 'Shift+Tab', label: 'Cycle permission mode' },
  // Esc stops Claude; when nothing is running in a session opened from the workboard, it goes back to the board, on the same card.
  stop: {
    keys: 'Esc',
    label: 'Stop Claude',
    combos: ['Escape'],
    when: (e) => !typing(e) && (isWorking(activeStatus()) || (st().page === 'session' && st().ui.returnTo === 'board')),
    run: () => {
      const id = st().activeId;
      if (isWorking(activeStatus())) {
        if (id) interrupt(id).catch(toasted);
      } else showPage('board');
    },
  },
  recall: { keys: '↑ in an empty box', label: 'Recall your previous message' },
  mention: { keys: '@', label: 'Mention a file or folder' },
  command: { keys: '/', label: 'Run a slash command' },
  answer: { keys: '1 · 2 · 3', label: 'Answer a permission request' },
  save: { keys: 'Ctrl+S', label: 'Save the open file' },
  settings: { keys: 'Ctrl+,', label: 'Settings', run: () => setDialog('settings', true) },
  shortcuts: { keys: 'Ctrl+/', label: 'Keyboard shortcuts', run: () => setDialog('shortcuts', true) },
  // In the desktop app the window menu owns Ctrl+Q; in a browser window the background process is asked to quit.
  quit: { keys: 'Ctrl+Q', label: 'Quit Pilcrow', run: () => quitApp(), when: () => !native() },
} satisfies Record<string, Shortcut>;

/** Whether a key press is this combo: every modifier as written, no other, and the key itself. */
export function matches(combo: string, e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>) {
  const parts = combo.split(/\+(?!$)/);
  const key = parts.pop() || '';
  return (e.ctrlKey || e.metaKey) === parts.includes('Ctrl') && e.shiftKey === parts.includes('Shift') && e.altKey === parts.includes('Alt') && (key.length === 1 ? e.key.toLowerCase() === key.toLowerCase() : e.key === key);
}

/** The shortcut a key press triggers, if any, with the index of the combo that matched. */
export function shortcutFor(e: KeyboardEvent): { run: (i: number) => void; index: number } | null {
  for (const s of Object.values(SHORTCUTS) as Shortcut[]) {
    if (!s.run) continue;
    const index = (s.combos || [s.keys]).findIndex((c) => matches(c, e));
    if (index >= 0 && (!s.when || s.when(e))) return { run: s.run, index };
  }
  return null;
}
