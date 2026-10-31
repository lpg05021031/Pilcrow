// App state (zustand) plus the RPC event wiring. Transcript models live outside React state
// and are referenced by a `rev` counter so streaming updates re-render cheaply.
import { create } from 'zustand';
import { rpc } from './rpc';
import { TranscriptModel } from './transcript';
import { setHome } from './format';
import { EASE_EXIT, reducedMotion } from './motion';
import type { AppInfo, Config, SessionState, SessionSummary, PermissionMode, Effort, RawMsg, GatewayModels, TimelineStep, PermissionDecision, DispatchJob, DispatchRun, DispatchDelta, Playbook } from './types';
import type { RepoEntry } from './types';
import type { PilcrowNative } from './globals';
import { errText } from './format';

export type PanelTab = 'changes' | 'files' | 'terminal' | 'preview' | 'tasks' | 'history' | 'info';

export interface Runtime extends Partial<SessionState> {
  rev: number;
  loaded: boolean;
  loading: boolean;
  thinkingTokens?: number;
  requesting?: boolean;
  meta?: Record<string, unknown>;
}

export interface Toast {
  id: number;
  text: string;
  level: 'info' | 'success' | 'error' | 'warning';
  action?: { label: string; run: () => void };
  /** How many times the same message arrived while it was showing. */
  count: number;
}

export interface ConfirmSpec {
  title: string;
  body?: string;
  confirm: string;
  danger?: boolean;
  checkbox?: { label: string; value: boolean };
  /** Monospace list shown under the body, e.g. files affected. */
  list?: string[];
  onConfirm: (checked: boolean) => void;
}

interface Ui {
  sidebar: boolean;
  panel: boolean;
  panelTab: PanelTab;
  sidebarW: number;
  panelW: number;
  showArchived: boolean;
  diffMode: 'unified' | 'split';
  /** How much of Claude's work the transcript shows: tools summarised, plus thinking, or every step opened. */
  view: ViewMode;
}
export type ViewMode = 'normal' | 'thinking' | 'verbose';
export const VIEW_MODES: { id: ViewMode; label: string; hint: string }[] = [
  { id: 'normal', label: 'Normal', hint: 'Steps summarised, thinking folded' },
  { id: 'thinking', label: 'Thinking', hint: 'Thinking opened' },
  { id: 'verbose', label: 'Verbose', hint: 'Every step and its output opened' },
];
export function cycleView() {
  const cur = get().ui.view;
  const next = VIEW_MODES[(VIEW_MODES.findIndex((v) => v.id === cur) + 1) % VIEW_MODES.length].id;
  setUi({ view: next });
  toast(`View: ${VIEW_MODES.find((v) => v.id === next)!.label}`, 'info');
}

interface Dialogs {
  newSession: null | { cwd?: string; prompt?: string };
  settings: boolean | 'general' | 'repos' | 'claude' | 'skills' | 'mcp' | 'memory' | 'diagnostics';
  /** OAuth sign-in to an MCP server, driven by one session's Claude Code process. */
  mcpAuth: null | { sessionId: string; name: string };
  palette: boolean;
  shortcuts: boolean;
  confirm: ConfirmSpec | null;
}

interface State {
  connected: boolean;
  everConnected: boolean;
  info: AppInfo | null;
  config: Config | null;
  sessions: SessionSummary[];
  sessionsLoaded: boolean;
  activeId: string | null;
  rt: Record<string, Runtime>;
  ui: Ui;
  dialogs: Dialogs;
  toasts: Toast[];
  /** Bumped when files change outside Claude's own tool calls (restore, discard, commit, save). */
  fsRev: number;
  unread: Record<string, boolean>;
  fileOpen: { path: string; line?: number; nonce: number } | null;
  composerInsert: { id: string; text: string; nonce: number; replace?: boolean } | null;
  theme: 'dark' | 'light';
  /** The endpoint's own model list; null until asked, 'loading' while asking. */
  gatewayModels: GatewayModels | 'loading' | null;
  /** Timeline steps per session, pushed by the server as they are recorded. */
  timeline: Record<string, TimelineStep[]>;
  /** What the main column shows: the open session, or one of the app-wide pages. */
  page: 'session' | 'overview' | 'inbox' | 'dispatch';
  /** Dispatch: job summaries and their runs, kept in line by dispatch.changed deltas. */
  jobs: Record<string, DispatchJob>;
  jobRuns: Record<string, Record<string, DispatchRun>>;
  jobsLoaded: boolean;
  /** The dispatch page: null = the board, 'new' = the composer, else a job id. */
  dispatchView: null | 'new' | string;
  playbooks: Playbook[];
  /** Unread results in the inbox (scheduled runs). */
  inboxUnread: number;
  /** Bumped when schedules or the inbox change on the server. */
  schedRev: number;
  /** Edit or create a schedule; `null` closed, `{}` new. */
  scheduleDialog: null | { id?: string; cwd?: string; kind?: 'prompt' | 'dispatch'; playbookId?: string };
  /** An image opened full-size over the app. */
  lightbox: string | null;
  /** The find bar is open: every row of the conversation must be on the page to be searched. */
  findOpen: boolean;
}

export const useStore = create<State>(() => ({
  connected: false,
  everConnected: false,
  info: null,
  config: null,
  sessions: [],
  sessionsLoaded: false,
  activeId: null,
  rt: {},
  ui: { sidebar: true, panel: false, panelTab: 'changes', sidebarW: 268, panelW: 560, showArchived: false, diffMode: 'unified', view: 'normal' },
  dialogs: { newSession: null, settings: false, palette: false, shortcuts: false, confirm: null, mcpAuth: null },
  toasts: [],
  fsRev: 0,
  unread: {},
  fileOpen: null,
  composerInsert: null,
  theme: 'dark',
  gatewayModels: null,
  timeline: {},
  page: 'session',
  jobs: {},
  jobRuns: {},
  jobsLoaded: false,
  dispatchView: null,
  playbooks: [],
  inboxUnread: 0,
  schedRev: 0,
  scheduleDialog: null,
  lightbox: null,
  findOpen: false,
}));

export const openLightbox = (src: string) => set({ lightbox: src });
export const closeLightbox = () => set({ lightbox: null });

const get = useStore.getState;
const set = useStore.setState;

export const models = new Map<string, TranscriptModel>();
export function getModel(id: string) {
  let m = models.get(id);
  if (!m) models.set(id, (m = new TranscriptModel()));
  return m;
}

// ---- batched rev updates (one React update per frame per session)
const dirty = new Set<string>();
let raf = 0;
function markDirty(id: string) {
  dirty.add(id);
  if (!raf) raf = requestAnimationFrame(flushDirty);
}
function flushDirty() {
  raf = 0;
  const rt = { ...get().rt };
  for (const id of dirty) {
    const m = models.get(id);
    if (m) rt[id] = { ...(rt[id] || { loaded: true, loading: false, rev: 0 }), rev: m.rev };
  }
  dirty.clear();
  set({ rt });
}

function patchRt(id: string, patch: Partial<Runtime>) {
  const cur = get().rt[id] || { rev: 0, loaded: false, loading: false };
  set({ rt: { ...get().rt, [id]: { ...cur, ...patch } } });
}

// ---- toasts
let toastSeq = 0;
const toastTimers = new Map<number, ReturnType<typeof setTimeout>>();
export function toast(text: string, level: Toast['level'] = 'info', action?: Toast['action']) {
  const friendly = humanizeError(text, level);
  text = friendly.text;
  action = action || friendly.action;
  // The same message again while it is still up: count it on the one that is showing, do not stack a twin.
  const twin = get().toasts.find((t) => t.text === text && t.level === level);
  if (twin) {
    set({ toasts: get().toasts.map((t) => (t.id === twin.id ? { ...t, count: t.count + 1 } : t)) });
    clearTimeout(toastTimers.get(twin.id));
    toastTimers.set(twin.id, setTimeout(() => dismissToast(twin.id), level === 'error' ? 9000 : 4500));
    return;
  }
  const id = ++toastSeq;
  set({ toasts: [...get().toasts.slice(-4), { id, text, level, action, count: 1 }] });
  toastTimers.set(id, setTimeout(() => dismissToast(id), level === 'error' ? 9000 : 4500));
}

/** Known failure texts said in plain words, with the way out attached. */
function humanizeError(text: string, level: Toast['level']): { text: string; action?: Toast['action'] } {
  if (level !== 'error') return { text };
  const t = text.replace(/^Claude Code returned an error result:\s*/i, '');
  if (/OAuth session expired|Failed to authenticate|not logged in|Invalid API key|authentication_error/i.test(t)) {
    return { text: 'Claude Code is signed out. Sign in again with `claude login` in a terminal, or set a gateway key in Settings → Claude Code.', action: { label: 'Settings', run: () => setDialog('settings', 'claude') } };
  }
  if (/rate limit|429|overloaded|529/i.test(t)) return { text: 'The model is busy right now. Claude Code will retry; if it keeps happening, try again in a minute.' };
  if (/ECONNREFUSED|ENOTFOUND|fetch failed|network/i.test(t)) return { text: `Could not reach the model endpoint — ${t.length > 120 ? t.slice(0, 120) + '…' : t}` };
  return { text: t };
}
/** Hovering a toast keeps it; it leaves a moment after the pointer does. */
export function holdToast(id: number) {
  clearTimeout(toastTimers.get(id));
}
export function releaseToast(id: number) {
  clearTimeout(toastTimers.get(id));
  toastTimers.set(id, setTimeout(() => dismissToast(id), 1800));
}

export function dismissToast(id: number) {
  set({ toasts: get().toasts.filter((t) => t.id !== id) });
}

export async function call<T = unknown>(method: string, params: Record<string, unknown> = {}, { quiet = false } = {}): Promise<T> {
  try {
    return await rpc.call<T>(method, params);
  } catch (e) {
    if (!quiet) toast(errText(e), 'error');
    throw e;
  }
}

// ---- desktop notifications
/** Bridge exposed by the Electron preload script; absent in browser/Chrome-app windows. */
export function native(): PilcrowNative | undefined {
  return window.pilcrowNative;
}
function canNotify() {
  return get().config?.notifications !== false && typeof Notification !== 'undefined';
}
function shouldNotify(id: string) {
  return document.hidden || !document.hasFocus() || get().activeId !== id;
}
function notify(id: string, title: string, body: string) {
  if (!canNotify() || !shouldNotify(id)) return;
  const go = () => {
    try {
      const n = new Notification(title, { body, tag: `pilcrow-${id}`, silent: false });
      n.onclick = () => {
        native()?.focus?.();
        window.focus();
        openSession(id);
        n.close();
      };
    } catch {
      /* notifications unavailable */
    }
  };
  if (Notification.permission === 'granted') go();
  else if (Notification.permission !== 'denied') Notification.requestPermission().then((p) => p === 'granted' && go());
}

function titleOf(id: string) {
  return get().sessions.find((s) => s.id === id)?.title || 'Session';
}

// ---- event wiring
let listTimer: ReturnType<typeof setTimeout> | null = null;
function sameSummary(a: SessionSummary, b: SessionSummary) {
  const ka = Object.keys(a) as (keyof SessionSummary)[];
  const kb = Object.keys(b) as (keyof SessionSummary)[];
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    const x = a[k];
    const y = b[k];
    if (x === y) continue;
    // worktree / scheduled are small plain objects
    if (x && y && typeof x === 'object' && typeof y === 'object' && JSON.stringify(x) === JSON.stringify(y)) continue;
    return false;
  }
  return true;
}

let listWaiters: (() => void)[] = [];
/** Re-fetch the session list (debounced; `force` re-reads the transcripts on disk). Resolves once the list is set. */
export function refreshSessions(force = false): Promise<void> {
  if (listTimer) clearTimeout(listTimer);
  return new Promise<void>((done) => {
    listWaiters.push(done);
    listTimer = setTimeout(async () => {
      const waiters = listWaiters;
      listWaiters = [];
      try {
        const fresh = await rpc.call<SessionSummary[]>(force ? 'sessions.refresh' : 'sessions.list');
        // Keep the row objects that did not change, so only the rows that did re-render.
        const prev = new Map(get().sessions.map((s) => [s.id, s]));
        const sessions = fresh.map((s) => {
          const p = prev.get(s.id);
          return p && sameSummary(p, s) ? p : s;
        });
        set({ sessions, sessionsLoaded: true });
      } catch {
        /* disconnected */
      }
      waiters.forEach((w) => w());
    }, force ? 0 : 60);
  });
}

const buffered = new Map<string, RawMsg[]>();

function onMsgs({ id, msgs }: { id: string; msgs: RawMsg[] }) {
  const r = get().rt[id];
  if (r?.loading) {
    buffered.set(id, [...(buffered.get(id) || []), ...msgs]);
    return;
  }
  const m = models.get(id);
  if (!m) return;
  for (const msg of msgs) {
    if (msg.type === 'system' && msg.subtype === 'thinking_tokens') {
      patchRt(id, { thinkingTokens: msg.estimated_tokens });
      continue;
    }
    if (msg.type === 'system' && msg.subtype === 'notification') {
      toast(msg.text || '', msg.priority === 'high' || msg.priority === 'immediate' ? 'warning' : 'info');
      continue;
    }
    if (msg.type === 'stream_event' && msg.event?.type === 'message_start' && !msg.parent_tool_use_id) patchRt(id, { thinkingTokens: 0 });
    m.push(msg);
  }
  markDirty(id);
}

function onState(st: SessionState) {
  const prev = get().rt[st.id];
  patchRt(st.id, st);
  const sessions = get().sessions.map((s) => (s.id === st.id ? { ...s, status: st.status, live: st.live, pending: st.pending.length } : s));
  set({ sessions });
  const wasBusy = prev && (prev.status === 'running' || prev.status === 'requires_action');
  if (wasBusy && st.status === 'idle') {
    if (get().activeId !== st.id || document.hidden) set({ unread: { ...get().unread, [st.id]: true } });
    notify(st.id, 'Claude finished', titleOf(st.id));
  }
  if (st.status === 'error' && prev?.status !== 'error' && st.error) notify(st.id, 'Session stopped with an error', titleOf(st.id));
}

export function initApp() {
  window.__pilcrowToast = toast; // for screenshots and quick checks from the console

  rpc.on('connection', async ({ connected }) => {
    set({ connected, everConnected: get().everConnected || connected });
    if (!connected) return;
    try {
      const [info, config] = await Promise.all([rpc.call<AppInfo>('app.info'), rpc.call<Config>('config.get')]);
      setHome(info.home);
      set({ info, config });
      loadGatewayModels();
      rpc.call<{ unread: number }>('inbox.list').then((r) => set({ inboxUnread: r.unread })).catch(() => {});
      loadJobs();
      loadPlaybooks();
      applyConfigUi(config);
      applyTheme();
      refreshSessions();
      // Re-sync any open session after a reconnect.
      const active = get().activeId;
      for (const id of [...models.keys()]) {
        models.delete(id);
        patchRt(id, { loaded: false });
      }
      if (active) openSession(active);
      else if (typeof config.ui?.lastSession === 'string') openSession(config.ui.lastSession).catch(() => set({ activeId: null }));
    } catch (e) {
      console.error(e);
    }
  });
  rpc.on('session.msgs', onMsgs);
  rpc.on('session.state', onState);
  rpc.on('session.permission', ({ id, request }) => {
    const label = request.toolName === 'AskUserQuestion' ? 'Claude has a question' : request.toolName === 'ExitPlanMode' ? 'A plan is ready for review' : `Claude wants to use ${request.displayName || request.toolName}`;
    notify(id, label, titleOf(id));
    if (!document.hasFocus()) native()?.attention?.();
    if (get().activeId !== id) set({ unread: { ...get().unread, [id]: true } });
  });
  rpc.on('sessions.changed', () => refreshSessions());
  rpc.on('schedules.changed', () => set({ schedRev: get().schedRev + 1 }));
  rpc.on('memory.changed', () => set({ schedRev: get().schedRev + 1 }));
  rpc.on('repos.changed', ({ repos }: { repos: RepoEntry[] }) => {
    const config = get().config;
    if (config) set({ config: { ...config, repos } });
  });
  rpc.on('inbox.changed', (unread: number) => set({ inboxUnread: typeof unread === 'number' ? unread : get().inboxUnread, schedRev: get().schedRev + 1 }));
  rpc.on('dispatch.changed', (d: DispatchDelta) => onDispatch(d));
  rpc.on('dispatch.playbooks', () => loadPlaybooks());
  rpc.on('timeline.step', ({ id, step }: { id: string; step: TimelineStep }) => {
    const cur = get().timeline[id];
    if (!cur) return; // nobody has asked for this session's timeline yet
    if (cur.some((s) => s.n === step.n)) return;
    set({ timeline: { ...get().timeline, [id]: [...cur, step] } });
  });
  rpc.connect();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
}

const PANEL_TABS: PanelTab[] = ['changes', 'files', 'terminal', 'preview', 'tasks', 'history', 'info'];

/** The saved window layout, taken field by field: the file on disk is loose JSON that an older version may have written. */
function applyConfigUi(config: Config) {
  const ui = config.ui || {};
  const oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | undefined => (typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : undefined);
  const panelTab = oneOf(ui.panelTab, PANEL_TABS);
  const diffMode = oneOf(ui.diffMode, ['unified', 'split'] as const);
  const view = oneOf(ui.view, VIEW_MODES.map((v) => v.id));
  set({
    ui: {
      ...get().ui,
      ...(typeof ui.sidebar === 'boolean' ? { sidebar: ui.sidebar } : {}),
      ...(typeof ui.panel === 'boolean' ? { panel: ui.panel } : {}),
      ...(panelTab ? { panelTab } : {}),
      ...(typeof ui.sidebarW === 'number' ? { sidebarW: ui.sidebarW } : {}),
      ...(typeof ui.panelW === 'number' ? { panelW: ui.panelW } : {}),
      ...(diffMode ? { diffMode } : {}),
      ...(view ? { view } : {}),
    },
  });
  document.documentElement.style.setProperty('--font-size', `${config.fontSize || 14.5}px`);
}

let themeReady = false;
let quietSwitch = false;
export function applyTheme() {
  // Until the config arrives, use the preference remembered from last time so the first frame is right.
  let pref: string = get().config?.theme || '';
  if (pref) {
    try {
      localStorage.setItem('pilcrow.theme', pref);
    } catch {
      /* storage unavailable */
    }
  } else {
    try {
      pref = localStorage.getItem('pilcrow.theme') || 'system';
    } catch {
      pref = 'system';
    }
  }
  const dark = pref === 'dark' || (pref === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const theme = dark ? 'dark' : 'light';
  const root = document.documentElement;
  const startup = !themeReady;
  if (get().config) themeReady = true;
  if (!startup && !quietSwitch && root.dataset.theme && root.dataset.theme !== theme) {
    // Cross-fade colours for a moment instead of flashing to the other theme.
    root.classList.add('theme-switch');
    setTimeout(() => root.classList.remove('theme-switch'), 360);
  }
  root.dataset.theme = theme;
  set({ theme });
}

let saveUiTimer: ReturnType<typeof setTimeout> | null = null;
export function setUi(patch: Partial<Ui>) {
  set({ ui: { ...get().ui, ...patch } });
  if (saveUiTimer) clearTimeout(saveUiTimer);
  saveUiTimer = setTimeout(() => {
    const { sidebar, panel, panelTab, sidebarW, panelW, diffMode, view } = get().ui;
    rpc.call('config.set', { ui: { sidebar, panel, panelTab, sidebarW, panelW, diffMode, view } }).catch(() => {});
  }, 400);
}

/**
 * Change the appearance. When the visible theme actually flips, the new one is revealed as a circle
 * growing from where the person clicked (a View Transition), instead of every colour fading at once.
 */
export function setTheme(pref: Config['theme'], at?: { x: number; y: number }) {
  const config = get().config;
  if (!config) return;
  const before = document.documentElement.dataset.theme;
  const resolved = pref === 'dark' || (pref === 'system' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  const doc = document as Document & { startViewTransition?: (cb: () => void) => { finished: Promise<void> } };
  set({ config: { ...config, theme: pref } });
  if (resolved === before || !doc.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    applyTheme();
  } else {
    const x = at?.x ?? innerWidth / 2;
    const y = at?.y ?? innerHeight / 2;
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const vt = doc.startViewTransition(() => {
      quietSwitch = true;
      applyTheme();
      quietSwitch = false;
    });
    vt.ready
      .then(() =>
        document.documentElement.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
          { duration: 520, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', pseudoElement: '::view-transition-new(root)' },
        ),
      )
      .catch(() => {});
  }
  call<Config>('config.set', { theme: pref })
    .then((c) => set({ config: c }))
    .catch(() => {});
}

export async function setConfig(patch: Partial<Config>) {
  const config = await call<Config>('config.set', patch);
  set({ config });
  applyConfigUi(config);
  applyTheme();
}

export function setDialog<K extends keyof Dialogs>(key: K, value: Dialogs[K]) {
  set({ dialogs: { ...get().dialogs, [key]: value } });
}

export function confirmAction(spec: ConfirmSpec) {
  setDialog('confirm', spec);
}

// ---- session actions
const opening = new Map<string, Promise<void>>();

export function openSession(id: string): Promise<void> {
  if (get().page !== 'session') set({ page: 'session' });
  set({ activeId: id, unread: { ...get().unread, [id]: false } });
  rpc.call('config.set', { ui: { lastSession: id } }).catch(() => {});
  const r = get().rt[id];
  if (models.has(id) && r?.loaded) return Promise.resolve();
  // One load per session at a time; a second click must not build a second transcript model.
  let p = opening.get(id);
  if (!p) {
    p = loadSession(id).finally(() => opening.delete(id));
    opening.set(id, p);
  }
  return p;
}

async function loadSession(id: string) {
  patchRt(id, { loading: true });
  try {
    const snap = await rpc.call<{ state: SessionState; messages: RawMsg[]; inflight: RawMsg[]; meta: Record<string, unknown> }>('session.open', { id });
    const m = new TranscriptModel();
    m.pushAll(snap.messages);
    m.pushAll(snap.inflight || []);
    models.set(id, m);
    patchRt(id, { ...snap.state, meta: snap.meta, loaded: true, loading: false, rev: m.rev });
    const extra = buffered.get(id);
    buffered.delete(id);
    if (extra?.length) onMsgs({ id, msgs: extra });
  } catch (e) {
    patchRt(id, { loading: false });
    toast(errText(e), 'error');
    if (get().activeId === id) set({ activeId: null });
    throw e;
  }
}

export async function createSession(opts: { cwd: string; worktree?: { base?: string; name?: string } | null; mode?: PermissionMode; model?: string; effort?: Effort; prompt?: string; images?: { mediaType: string; data: string }[]; title?: string }) {
  const { id } = await call<{ id: string }>('session.create', { cwd: opts.cwd, worktree: opts.worktree || undefined, mode: opts.mode, model: opts.model, effort: opts.effort, title: opts.title });
  await refreshSessions(true);
  await openSession(id);
  if (opts.prompt?.trim() || opts.images?.length) {
    try {
      await sendMessage(id, { text: opts.prompt || '', images: opts.images || [] });
    } catch {
      // The session exists; keep the prompt in its composer so it can be sent once the problem is fixed.
      if (opts.prompt?.trim()) insertIntoComposer(id, opts.prompt, true);
    }
  }
  return id;
}

/** `now` steers: the current step is interrupted and this message goes first. */
export async function sendMessage(id: string, payload: { text: string; images?: { mediaType: string; data: string }[]; files?: { name: string; text: string }[] }, opts: { now?: boolean } = {}) {
  // Sending is a reason to be at the live end again, wherever the reader had scrolled to (Transcript listens).
  window.dispatchEvent(new CustomEvent('pilcrow:sent', { detail: id }));
  await call('session.send', { id, ...payload, now: Boolean(opts.now) });
}

export const removeHeld = (id: string, uuid: string) => call('session.queue.remove', { id, uuid });
export const updateHeld = (id: string, uuid: string, text: string) => call('session.queue.update', { id, uuid, text });
export const sendHeldNow = (id: string, uuid: string) => call('session.queue.sendNow', { id, uuid });

export async function setCwd(id: string, cwd: string) {
  await call('session.setCwd', { id, cwd });
  refreshSessions(true);
}

/** The folder a one-click new session starts in: the current one, else the most recent project. */
export function defaultCwd(explicit?: string) {
  const st = get();
  if (explicit) return explicit;
  const active = st.sessions.find((s) => s.id === st.activeId);
  return active?.project || active?.cwd || st.config?.recentProjects?.[0] || st.info?.home || '';
}

/** Start a session right away with the usual defaults; the dialog is only for the unusual case. */
export async function quickNewSession(cwd?: string) {
  const dir = defaultCwd(cwd);
  if (!dir) {
    setDialog('newSession', {});
    return null;
  }
  const c = get().config;
  try {
    return await createSession({ cwd: dir, mode: c?.defaultMode, model: c?.defaultModel, effort: c?.defaultEffort });
  } catch {
    setDialog('newSession', { cwd: dir });
    return null;
  }
}

export const interrupt = (id: string) => call('session.interrupt', { id });
export const setMode = (id: string, mode: PermissionMode) => call('session.setMode', { id, mode });
export const setModel = (id: string, model: string) => call('session.setModel', { id, model });
export const setEffort = (id: string, effort: Effort) => call('session.setEffort', { id, effort });
export const respond = (id: string, requestId: string, decision: PermissionDecision) => call('session.permission', { id, requestId, decision });

export async function renameSession(id: string, title: string) {
  await call('session.rename', { id, title });
  refreshSessions(true);
}

/**
 * A row that is about to leave the list folds shut first, so the rows under it close up with the
 * layout instead of jumping once the list comes back without it. Returns an undo for the case where
 * the row is still there afterwards (archiving while looking at the archive, say).
 */
export const foldingRows = new Set<string>();
async function foldRow(id: string): Promise<() => void> {
  const el = document.querySelector<HTMLElement>(`.side-list [data-sid="${id}"]`);
  if (!el || reducedMotion()) return () => {};
  foldingRows.add(id);
  const h = el.offsetHeight;
  // The row's own min-height and the list's row gap would both keep a sliver of space; fold them too.
  const gap = parseFloat(getComputedStyle(el.parentElement!).rowGap) || 0;
  el.style.overflow = 'hidden';
  el.style.minHeight = '0';
  el.style.pointerEvents = 'none';
  const a = el.animate(
    [
      { height: `${h}px`, marginBottom: '0px', opacity: 1 },
      { height: `${h}px`, marginBottom: '0px', opacity: 0, offset: 0.4 },
      { height: '0px', marginBottom: `${-gap}px`, opacity: 0 },
    ],
    { duration: 280, easing: EASE_EXIT, fill: 'forwards' },
  );
  await a.finished.catch(() => {});
  return () => {
    foldingRows.delete(id);
    a.cancel();
    el.style.overflow = '';
    el.style.minHeight = '';
    el.style.pointerEvents = '';
  };
}

export async function deleteSession(id: string, removeWorktree = false) {
  const unfold = await foldRow(id);
  try {
    await call('session.delete', { id, removeWorktree });
  } catch (e) {
    unfold();
    throw e;
  }
  requestAnimationFrame(() => foldingRows.delete(id));
  models.delete(id);
  const rt = { ...get().rt };
  delete rt[id];
  set({ rt, activeId: get().activeId === id ? null : get().activeId, sessions: get().sessions.filter((s) => s.id !== id) });
  refreshSessions(true);
}

export async function archiveSession(id: string, archived: boolean) {
  const unfold = await foldRow(id);
  try {
    await call('session.archive', { id, archived });
  } catch (e) {
    unfold();
    throw e;
  }
  await refreshSessions(true);
  // Still listed (the archive view shows archived rows): give the row back.
  requestAnimationFrame(() => {
    if (document.querySelector(`.side-list [data-sid="${id}"]`)) unfold();
    foldingRows.delete(id);
  });
}

export async function pinSession(id: string, pinned: boolean) {
  await call('session.pin', { id, pinned });
  refreshSessions(true);
}

export async function forkSession(id: string, upToMessageId?: string, sendAfter?: string) {
  const r = await call<{ id: string }>('session.fork', { id, upToMessageId });
  await refreshSessions(true);
  await openSession(r.id);
  if (sendAfter) await sendMessage(r.id, { text: sendAfter });
  toast('Forked into a new session', 'success');
  return r.id;
}

export function openFile(path: string, line?: number) {
  set({ fileOpen: { path, line, nonce: Date.now() } });
  setUi({ panel: true, panelTab: 'files' });
}

export function showPage(page: 'session' | 'overview' | 'inbox' | 'dispatch') {
  set({ page });
}

// ---- dispatch: one job across many repositories
export function loadJobs() {
  rpc
    .call<DispatchJob[]>('dispatch.list')
    .then((lst) => set({ jobs: Object.fromEntries((lst || []).map((j) => [j.id, j])), jobsLoaded: true }))
    .catch(() => {});
}
export function loadPlaybooks() {
  rpc
    .call<Playbook[]>('dispatch.playbooks')
    .then((lst) => set({ playbooks: lst || [] }))
    .catch(() => {});
}
function onDispatch(d: DispatchDelta) {
  if (!d?.id) return;
  const st = get();
  if (d.removed) {
    const jobs = { ...st.jobs };
    delete jobs[d.id];
    const jobRuns = { ...st.jobRuns };
    delete jobRuns[d.id];
    set({ jobs, jobRuns, dispatchView: st.dispatchView === d.id ? null : st.dispatchView });
    return;
  }
  const jobs = d.job ? { ...st.jobs, [d.id]: d.job } : st.jobs;
  let jobRuns = st.jobRuns;
  if (d.runs) {
    const cur = d.all ? {} : { ...(st.jobRuns[d.id] || {}) };
    for (const r of d.runs) cur[r.id] = r;
    jobRuns = { ...st.jobRuns, [d.id]: cur };
  }
  set({ jobs, jobRuns });
}
export function fetchJob(id: string) {
  return rpc
    .call<{ job: DispatchJob; runs: DispatchRun[] }>('dispatch.get', { id })
    .then((r) => {
      const st = get();
      set({ jobs: { ...st.jobs, [id]: r.job }, jobRuns: { ...st.jobRuns, [id]: Object.fromEntries(r.runs.map((x) => [x.id, x])) } });
    })
    .catch(() => {});
}
/** A job's runs in the server's order, the superseded (retried) ones left out. */
export function runsOf(runs: Record<string, Record<string, DispatchRun>>, id: string): DispatchRun[] {
  return Object.values(runs[id] || {}).filter((r) => !r.superseded);
}
/** Requests waiting across jobs, plus the canaries waiting to be read: the sidebar's badge. */
export function dispatchNeeds(jobs: Record<string, DispatchJob>): number {
  let n = 0;
  for (const j of Object.values(jobs)) if (j.state === 'running' || j.state === 'reviewing') n += (j.needsAction || 0) + (j.canaryWaiting ? 1 : 0);
  return n;
}
/** The board (null), the composer ('new') or a job's view (its id). */
export function showDispatch(view: null | 'new' | string) {
  set({ dispatchView: view });
  if (view && view !== 'new') fetchJob(view);
  if (get().page !== 'dispatch') set({ page: 'dispatch' });
}
export const openJob = (id: string) => showDispatch(id);

export function setTimeline(id: string, steps: TimelineStep[]) {
  set({ timeline: { ...get().timeline, [id]: steps } });
}

export function bumpFs() {
  set({ fsRev: get().fsRev + 1 });
}

export function insertIntoComposer(id: string, text: string, replace = false) {
  set({ composerInsert: { id, text, nonce: Date.now(), replace } });
}

/** The registered repository (REPOSITORIES.md) whose folder holds `path` (the deepest one), or null. */
export function repoFor(path: string | undefined | null, repos: RepoEntry[] | undefined = useStore.getState().config?.repos): RepoEntry | null {
  const p = String(path || '').replace(/\/+$/, '');
  if (!p) return null;
  let best: RepoEntry | null = null;
  for (const e of repos || []) {
    const ep = String(e.path || '').replace(/\/+$/, '');
    if (ep && (p === ep || p.startsWith(ep + '/')) && (!best || ep.length > best.path.length)) best = e;
  }
  return best;
}

export function activeSession() {
  const { activeId, sessions } = get();
  return sessions.find((s) => s.id === activeId) || null;
}

/** Re-read app info (paths, Claude Code program, API settings) after the person changed something outside. */
export async function refreshInfo() {
  const info = await call<AppInfo>('app.info', {}, { quiet: true });
  set({ info });
  loadGatewayModels(true);
  return info;
}

/** Ask the API endpoint which models it offers. Cached on the server for ten minutes unless forced. */
export async function loadGatewayModels(force = false) {
  const prev = get().gatewayModels;
  if (prev === 'loading') return;
  if (!force && prev && typeof prev === 'object' && prev.ok) return;
  set({ gatewayModels: 'loading' });
  try {
    const r = await call<GatewayModels>('models.gateway', { force }, { quiet: true });
    set({ gatewayModels: r });
  } catch (e) {
    const msg = errText(e);
    // Window newer than the background process (it started before an update): say how to finish updating.
    const error = /unknown method/i.test(msg) ? 'Quit Pilcrow and open it again to finish updating' : msg || 'Could not ask the gateway';
    set({ gatewayModels: { ok: false, error } });
  }
}
