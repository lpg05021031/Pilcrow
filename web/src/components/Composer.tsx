import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, Square, Plus, X, ChevronDown, ShieldCheck, FilePen, Map as MapIcon, Zap, ShieldOff, File as FileIcon, Folder, FolderGit2, CornerDownLeft, RefreshCw, Clock3, Image as ImageIcon } from 'lucide-react';
import { useStore, sendMessage, interrupt, setMode, setModel, setEffort, setUi, toast, call, loadGatewayModels, removeHeld, updateHeld, sendHeldNow } from '../lib/store';
import { ALIASES, useGatewayModels, modelLabel as nameOf } from '../lib/models';
import type { PermissionMode, Effort, SlashCommandInfo, HeldMsg } from '../lib/types';
import { fuzzyFilter } from '../lib/fuzzy';
import { bytes, tildify } from '../lib/format';
import { Popover, Menu, type MenuItem } from './ui/Popover';
import { ContextRing, contextLabel, focusIfFree, Leave, Num } from './ui/bits';
import { markLaunch, reducedMotion, spring } from '../lib/motion';
import type { LucideIcon } from 'lucide-react';
import type { ModelInfo } from '../lib/types';

export const MODES: { id: PermissionMode; label: string; hint: string; icon: LucideIcon; tone: string }[] = [
  { id: 'default', label: 'Ask before edits', hint: 'Asks before changing files or running commands', icon: ShieldCheck, tone: '' },
  { id: 'acceptEdits', label: 'Edit automatically', hint: 'File edits are applied without asking', icon: FilePen, tone: 'accent' },
  { id: 'plan', label: 'Plan mode', hint: 'Explores and proposes a plan; changes nothing', icon: MapIcon, tone: 'plan' },
  { id: 'auto', label: 'Auto', hint: 'Approves routine actions, asks about risky ones', icon: Zap, tone: 'info' },
  { id: 'bypassPermissions', label: 'Bypass permissions', hint: 'Runs everything without asking — sandboxes only', icon: ShieldOff, tone: 'err' },
];

const BUILTIN_COMMANDS: SlashCommandInfo[] = [
  { name: 'compact', description: 'Summarize the conversation to free up context' },
  { name: 'clear', description: 'Start the conversation over' },
  { name: 'context', description: 'Show what is using the context window' },
  { name: 'usage', description: 'Show cost and token usage' },
  { name: 'init', description: 'Write a CLAUDE.md for this project' },
  { name: 'code-review', description: 'Review the current changes' },
  { name: 'model', description: 'Change the model' },
];

const drafts = new Map<string, string>();
const histories = new Map<string, string[]>();
const fileIndexCache = new Map<string, { at: number; files: string[] }>();

interface Img {
  name: string;
  mediaType: string;
  data: string;
  url: string;
  size: number;
}

type Popup = { kind: 'mention' | 'slash'; query: string; start: number; index: number } | null;

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

export function Composer({ sessionId, cwd }: { sessionId: string; cwd: string }) {
  const rt = useStore((s) => s.rt[sessionId]);
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const insert = useStore((s) => s.composerInsert);
  const [text, setText] = useState(() => drafts.get(sessionId) || '');
  const [images, setImages] = useState<Img[]>([]);
  const [files, setFiles] = useState<{ name: string; text: string }[]>([]);
  const [popup, setPopup] = useState<Popup>(null);
  const [index, setIndex] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [histPos, setHistPos] = useState(-1);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const modeBtn = useRef<HTMLButtonElement>(null);
  const modelBtn = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState<null | 'mode' | 'model'>(null);

  const status = rt?.status;
  const busy = status === 'running' || status === 'starting' || status === 'requires_action';
  const held: HeldMsg[] = rt?.held || [];
  const mode = (rt?.mode || config?.defaultMode || 'default') as PermissionMode;
  const modeInfo = MODES.find((m) => m.id === mode) || MODES[0];
  const sendWithEnter = (config?.sendKey || 'enter') === 'enter';

  // Keep drafts per session.
  useEffect(() => {
    setText(drafts.get(sessionId) || '');
    setImages([]);
    setFiles([]);
    setPopup(null);
    setHistPos(-1);
    requestAnimationFrame(() => focusIfFree(ta.current));
  }, [sessionId]);
  useEffect(() => {
    drafts.set(sessionId, text);
  }, [sessionId, text]);

  // Text inserted from elsewhere (diff notes, edit & resend, example prompts).
  useEffect(() => {
    if (!insert || insert.id !== sessionId) return;
    setText((t) => (insert.replace || !t.trim() ? insert.text : `${t.replace(/\s+$/, '')}\n\n${insert.text}`));
    requestAnimationFrame(() => {
      const el = ta.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }, [insert, sessionId]);

  // Auto-size the textarea. After a send it settles back to one line on a spring rather than snapping.
  const [sent, setSent] = useState(0);
  const lastH = useRef(0);
  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    const prev = lastH.current;
    // Measuring at height:auto lays the page out with the field at its smallest for an instant; the
    // conversation above would be clamped to that taller view and left a few pixels short of its end
    // once the field takes its real height. So note whether it was at its end, and put it back there.
    const sc = el.closest('.session')?.querySelector<HTMLElement>('.transcript');
    const atEnd = sc ? sc.scrollHeight - sc.scrollTop - sc.clientHeight < 2 : false;
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, Math.round(window.innerHeight * 0.42));
    el.style.height = next + 'px';
    lastH.current = next;
    if (sc && atEnd) sc.scrollTop = sc.scrollHeight - sc.clientHeight;
    if (!text && prev > next + 2 && Date.now() - sent < 600 && !reducedMotion()) {
      const { soft, softT } = spring();
      el.animate([{ height: `${prev}px` }, { height: `${next}px` }], { duration: softT, easing: soft });
    }
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadIndex = useCallback(async () => {
    const hit = fileIndexCache.get(cwd);
    if (hit && Date.now() - hit.at < 20_000) {
      setIndex(hit.files);
      return;
    }
    try {
      const files = await call<string[]>('fs.index', { cwd }, { quiet: true });
      fileIndexCache.set(cwd, { at: Date.now(), files });
      setIndex(files);
    } catch {
      /* ignore */
    }
  }, [cwd]);

  const commands: SlashCommandInfo[] = useMemo(() => {
    if (rt?.commands?.length) return rt.commands;
    if (rt?.init?.slashCommands?.length) return rt.init.slashCommands.map((n) => BUILTIN_COMMANDS.find((b) => b.name === n) || { name: n, description: '' });
    return BUILTIN_COMMANDS;
  }, [rt?.commands, rt?.init?.slashCommands]);

  const suggestions = useMemo(() => {
    if (!popup) return [];
    if (popup.kind === 'slash') return fuzzyFilter(popup.query, commands, (c) => c.name, 40).map((m) => ({ key: m.item.name, label: '/' + m.item.name, hint: m.item.argumentHint ? `${m.item.argumentHint} — ${m.item.description}` : m.item.description, kind: 'cmd' as const }));
    const q = popup.query;
    const list = q ? fuzzyFilter(q, index, (f) => f, 40).map((m) => m.item) : index.filter((f) => !f.includes('/') || f.split('/').length <= 2).slice(0, 40);
    const files = list.map((f) => ({ key: f, label: f, hint: '', kind: f.endsWith('/') ? ('dir' as const) : ('file' as const) }));
    // registered repositories (REPOSITORIES.md) answer to their names and the person's aliases
    const norm = (s: string) => s.toLowerCase().replace(/[\s_-]+/g, '');
    const nq = norm(q);
    const repos = (config?.repos || [])
      .filter((e) => e.name && (!nq || [e.name, ...(e.aliases || [])].some((w) => norm(w).includes(nq))))
      .slice(0, 8)
      .map((e) => ({ key: e.name, label: e.name, hint: e.about || tildify(e.path, info?.home), kind: 'repo' as const }));
    return [...repos, ...files];
  }, [popup, commands, index, config?.repos, info?.home]);

  const detectPopup = (value: string, caret: number) => {
    const before = value.slice(0, caret);
    const slash = before.match(/^\/([\w:-]*)$/);
    if (slash) {
      setPopup({ kind: 'slash', query: slash[1], start: 0, index: 0 });
      return;
    }
    const at = before.match(/(?:^|\s)@([^\s@]*)$/);
    if (at) {
      if (!index.length) loadIndex();
      setPopup({ kind: 'mention', query: at[1], start: caret - at[1].length - 1, index: 0 });
      return;
    }
    setPopup(null);
  };

  const applySuggestion = (s: { key: string; kind: string }) => {
    if (!popup) return;
    const el = ta.current!;
    const caret = el.selectionStart;
    let next: string;
    let pos: number;
    if (popup.kind === 'slash') {
      next = `/${s.key} ` + text.slice(caret);
      pos = s.key.length + 2;
    } else {
      const ins = `@${s.key}${s.kind === 'dir' ? '' : ' '}`;
      next = text.slice(0, popup.start) + ins + text.slice(caret);
      pos = popup.start + ins.length;
    }
    setText(next);
    setPopup(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
      if (s.kind === 'dir') detectPopup(next, pos);
    });
  };

  const submit = async (now = false) => {
    const body = text.trim();
    if (!body && !images.length && !files.length) return;
    const payload = { text: body, images: images.map((i) => ({ mediaType: i.mediaType, data: i.data })), files };
    // The bubble that lands in the conversation flies up from here; the arrow launches with it.
    if (ta.current) markLaunch(ta.current.getBoundingClientRect());
    setSent(Date.now());
    setText('');
    setImages([]);
    setFiles([]);
    setPopup(null);
    setHistPos(-1);
    drafts.set(sessionId, '');
    if (body) histories.set(sessionId, [body, ...(histories.get(sessionId) || []).filter((h) => h !== body)].slice(0, 50));
    try {
      await sendMessage(sessionId, payload, { now: now && busy });
    } catch {
      setText(body);
    }
  };

  const cycleMode = () => {
    const order: PermissionMode[] = MODES.filter((m) => !(m.id === 'bypassPermissions' && info?.isRoot) && m.id !== 'auto').map((m) => m.id);
    const next = order[(order.indexOf(mode) + 1) % order.length];
    setMode(sessionId, next).catch(() => {});
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (popup && suggestions.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const d = e.key === 'ArrowDown' ? 1 : -1;
        setPopup({ ...popup, index: (popup.index + d + suggestions.length) % suggestions.length });
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        applySuggestion(suggestions[popup.index]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        setPopup(null);
        return;
      }
    }
    if (e.key === 'Tab' && e.shiftKey) {
      e.preventDefault();
      cycleMode();
      return;
    }
    if (e.key === 'Tab' && !text && rt?.suggestion) {
      e.preventDefault();
      setText(rt.suggestion);
      return;
    }
    const isEnter = e.key === 'Enter' && !e.nativeEvent.isComposing;
    if (isEnter && busy && (e.metaKey || e.ctrlKey) && e.shiftKey) {
      // Steer: interrupt the current step and make this the next thing Claude reads.
      e.preventDefault();
      submit(true);
      return;
    }
    if (isEnter && ((sendWithEnter && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) || (!sendWithEnter && (e.metaKey || e.ctrlKey) && !e.shiftKey))) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === 'Escape' && busy) {
      e.preventDefault();
      interrupt(sessionId);
      return;
    }
    const hist = histories.get(sessionId) || [];
    if (e.key === 'ArrowUp' && text === '' && held.length && ta.current?.selectionStart === 0) {
      // Like the terminal: ↑ takes the last queued message back so it can be changed.
      e.preventDefault();
      const last = held[held.length - 1];
      removeHeld(sessionId, last.uuid).catch(() => {});
      setText(last.text);
      return;
    }
    if (e.key === 'ArrowUp' && hist.length && (text === '' || histPos >= 0) && ta.current?.selectionStart === 0) {
      e.preventDefault();
      const p = Math.min(hist.length - 1, histPos + 1);
      setHistPos(p);
      setText(hist[p]);
      return;
    }
    if (e.key === 'ArrowDown' && histPos >= 0 && ta.current?.selectionStart === text.length) {
      e.preventDefault();
      const p = histPos - 1;
      setHistPos(p);
      setText(p >= 0 ? hist[p] : '');
    }
  };

  const addFiles = async (list: FileList | File[]) => {
    for (const f of Array.from(list)) {
      if (f.type.startsWith('image/')) {
        if (f.size > 8 * 1024 * 1024) {
          toast(`${f.name} is larger than 8 MB`, 'warning');
          continue;
        }
        const data = await readAsBase64(f);
        setImages((xs) => [...xs, { name: f.name || 'pasted image', mediaType: f.type, data, url: URL.createObjectURL(f), size: f.size }]);
      } else if (f.size < 400 * 1024) {
        const t = await f.text();
        if (/\u0000/.test(t.slice(0, 4000))) {
          toast(`${f.name} looks like a binary file`, 'warning');
          continue;
        }
        setFiles((xs) => [...xs, { name: f.name, text: t }]);
      } else toast(`${f.name} is too large to attach (400 KB max)`, 'warning');
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const fs = Array.from(e.clipboardData.files || []);
    if (fs.length) {
      e.preventDefault();
      addFiles(fs);
    }
  };

  const ctxUsed = rt?.usage?.contextTokens || 0;
  const ctxTotal = rt?.usage?.contextWindow || 0;
  // Before this session's first reply Claude Code has not listed its models yet; borrow the list (and
  // the default's name) from another session so the menu is never a dead end and the label never jumps.
  const elsewhere = useStore((s) => Object.values(s.rt).find((r) => r !== rt && r?.models?.length));
  const defaultSeen = useStore((s) => Object.values(s.rt).find((r) => r?.activeModel && !r?.model)?.activeModel);
  const models: ModelInfo[] = rt?.models?.length ? rt.models : elsewhere?.models?.length ? elsewhere.models : ALIASES;
  const gw = useGatewayModels();
  const currentModel = rt?.model || '';
  const modelInfo = models.find((m) => m.value === currentModel) || models.find((m) => m.value === 'default');
  const effortLevels = (modelInfo?.supportedEffortLevels || (modelInfo?.supportsEffort ? ['low', 'medium', 'high', 'xhigh', 'max'] : [])) as Effort[];
  const shown = currentModel || rt?.activeModel || defaultSeen || '';
  const modelLabel = shown ? nameOf(shown, gw.models, models) : 'Default model';
  const effortLabel = rt?.effort ? (rt.effort === 'xhigh' ? 'Extra high' : rt.effort[0].toUpperCase() + rt.effort.slice(1)) : '';

  const effortName = (l: string) => (l === 'xhigh' ? 'Extra high' : l[0].toUpperCase() + l.slice(1));
  const modeItems: MenuItem[] = MODES.filter((m) => !(m.id === 'bypassPermissions' && info?.isRoot)).map((m) => ({ label: m.label, hint: m.hint, icon: m.icon, checked: m.id === mode, danger: m.id === 'bypassPermissions', onSelect: () => setMode(sessionId, m.id).catch(() => {}) }));
  const modelItems: MenuItem[] = [
    { heading: 'Model' },
    { label: 'Default', hint: 'Whatever Claude Code is configured to use', checked: !currentModel, onSelect: () => setModel(sessionId, '') },
    // The gateway's own models. Claude Code's built-in aliases (Opus/Sonnet/Haiku) are not offered:
    // everything goes through the gateway, which may not know them.
    ...(gw.models.length || gw.loading || gw.error
      ? [
          { heading: gw.base ? `On ${hostOf(gw.base)}` : 'On your gateway' },
          ...(gw.loading ? [{ label: 'Asking the gateway…', disabled: true }] : []),
          ...gw.models.map((m) => ({ label: m.displayName ? m.displayName.replace(/^Claude\s+/i, '') : m.id, hint: m.displayName ? m.id : undefined, mono: true, checked: currentModel === m.id, onSelect: () => setModel(sessionId, m.id) })),
          ...(gw.error ? [{ label: "Couldn't list the models", hint: gw.error, disabled: true }] : []),
          ...(!gw.loading ? [{ label: gw.error ? 'Try again' : 'Refresh the list', icon: RefreshCw, onSelect: () => loadGatewayModels(true) }] : []),
        ]
      : []),
    ...(effortLevels.length
      ? [{ separator: true }, { heading: 'Effort' }, { label: 'Default', checked: !rt?.effort, onSelect: () => setEffort(sessionId, '') }, ...effortLevels.map((l) => ({ label: effortName(l), checked: rt?.effort === l, onSelect: () => setEffort(sessionId, l) }))]
      : []),
  ];
  const ctxPct = ctxTotal > 0 ? ctxUsed / ctxTotal : 0;
  const hasContent = Boolean(text.trim() || images.length || files.length);
  const showStop = busy && !hasContent;
  const suggestion = rt?.suggestion && !text ? rt.suggestion : '';
  const placeholder = suggestion ? '' : busy ? (held.length ? 'Queue another message…' : 'Queue a message for after this step…') : 'Ask Claude to build, fix or explain…';

  // Toasts sit just above the composer, so they never cover the send button.
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const apply = () => document.documentElement.style.setProperty('--composer-h', `${el.offsetHeight}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.setProperty('--composer-h', '0px');
    };
  }, []);

  return (
    <div
      ref={wrap}
      className={`composer-wrap${dragging ? ' dragging' : ''}${text ? '' : ' is-empty'}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
      }}
    >
      {popup && suggestions.length > 0 && (
        <Leave className="suggest" role="listbox" duration={160}>
          <div className="suggest-head">{popup.kind === 'slash' ? 'Commands' : popup.query ? (suggestions.some((s) => s.kind === 'repo') ? 'Repositories and files' : 'Files') : 'Files — keep typing to narrow down'}</div>
          <div className="suggest-list" style={{ ['--name-ch' as string]: `${Math.min(30, Math.max(8, ...suggestions.filter((x) => x.kind === 'cmd').map((x) => x.label.length)))}ch` }}>
            {suggestions.slice(0, 40).map((s, i) => (
              <button key={s.key} role="option" aria-selected={i === popup.index} className={`suggest-item${i === popup.index ? ' active' : ''}`} onMouseDown={(e) => e.preventDefault()} onClick={() => applySuggestion(s)} onMouseEnter={() => setPopup({ ...popup, index: i })} ref={i === popup.index ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined}>
                {s.kind === 'repo' ? <FolderGit2 size={14} className="suggest-repo" /> : s.kind === 'dir' ? <Folder size={14} /> : s.kind === 'file' ? <FileIcon size={14} /> : null}
                <span className={`suggest-label${s.kind === 'cmd' ? ' cmd' : ''}`}>
                  {s.kind === 'cmd' ? (
                    <>
                      <span className="suggest-slash">/</span>
                      {s.label.slice(1)}
                    </>
                  ) : (
                    s.label
                  )}
                </span>
                {s.hint && <span className="suggest-hint">{s.hint}</span>}
              </button>
            ))}
          </div>
        </Leave>
      )}
      {held.length > 0 && <QueueTray sessionId={sessionId} held={held} />}
      <div className={`composer mode-${mode}${busy ? ' busy' : ''}`} onClick={(e) => e.target === e.currentTarget && ta.current?.focus()}>
        {(images.length > 0 || files.length > 0) && (
          <div className="attachments">
            {images.map((img) => (
              <Leave key={img.url} className="attach-img" title={`${img.name} · ${bytes(img.size)}`} duration={180}>
                <img src={img.url} alt={img.name} />
                <button className="attach-x" onClick={() => setImages((xs) => xs.filter((x) => x !== img))} aria-label="Remove image">
                  <X size={11} />
                </button>
              </Leave>
            ))}
            {files.map((f, k) => (
              <Leave key={`${f.name}:${k}`} className="attach-file" duration={180}>
                <FileIcon size={13} /> <span className="ellipsis">{f.name}</span>
                <button onClick={() => setFiles((xs) => xs.filter((x) => x !== f))} aria-label="Remove file">
                  <X size={11} />
                </button>
              </Leave>
            ))}
          </div>
        )}
        <div className="composer-field">
          {suggestion && (
            <div className="composer-ghost" aria-hidden="true">
              <span>{suggestion}</span>
              <kbd>Tab</kbd>
            </div>
          )}
          <textarea
            ref={ta}
            className="composer-input"
            rows={1}
            value={text}
            placeholder={placeholder}
            onChange={(e) => {
              setText(e.target.value);
              setHistPos(-1);
              detectPopup(e.target.value, e.target.selectionStart);
            }}
            onClick={(e) => detectPopup(text, (e.target as HTMLTextAreaElement).selectionStart)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onBlur={() => setTimeout(() => setPopup(null), 120)}
            spellCheck={false}
          />
        </div>
        <div className="composer-bar">
          <button className="cbtn icon" title="Attach images or files" onClick={() => fileInput.current?.click()}>
            <Plus size={16} />
          </button>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => e.target.files && addFiles(e.target.files)} />
          <button ref={modeBtn} className={`cbtn mode ${mode !== 'default' ? modeInfo.tone || 'set' : ''}`} onClick={() => setMenu(menu === 'mode' ? null : 'mode')} title="Permission mode (Shift+Tab to switch)" aria-expanded={menu === 'mode'}>
            <modeInfo.icon size={14} />
            <span className="cbtn-label">{modeInfo.label}</span>
            <ChevronDown size={12} className="cbtn-chev" />
          </button>
          <div className="grow" />
          <button
            ref={modelBtn}
            className="cbtn"
            onClick={() => {
              if (menu !== 'model') loadGatewayModels();
              setMenu(menu === 'model' ? null : 'model');
            }}
            title="Model and effort"
            aria-expanded={menu === 'model'}
          >
            <span>{modelLabel}</span>
            {effortLabel && <span className="cbtn-sub">· {effortLabel}</span>}
            <ChevronDown size={12} className="cbtn-chev" />
          </button>
          {ctxTotal > 0 && (
            <button className={`ctx-btn${ctxPct > 0.85 ? ' hot' : ''}${ctxPct >= 0.5 ? ' with-num' : ''}`} title={`Context ${Math.round(ctxPct * 100)}% used · ${contextLabel(ctxUsed, ctxTotal)}${ctxPct >= 0.5 ? ' — /compact frees space' : ''}`} aria-label="Context window usage" onClick={() => setUi({ panel: true, panelTab: 'info' })}>
              <ContextRing used={ctxUsed} total={ctxTotal} size={16} />
              {ctxPct >= 0.5 && (
                <span className="tnum">
                  <Num value={ctxPct * 100} format={(v) => `${Math.round(v)}%`} />
                </span>
              )}
            </button>
          )}
          <button
            className={`send${hasContent ? ' ready' : ''}${showStop ? ' stop' : ''}`}
            title={showStop ? 'Stop (Esc)' : sendWithEnter ? 'Send (Enter)' : 'Send (Ctrl+Enter)'}
            disabled={!hasContent && !showStop}
            onClick={() => (showStop ? interrupt(sessionId) : submit())}
            aria-label={showStop ? 'Stop' : 'Send'}
          >
            <ArrowUp key={sent} size={16} strokeWidth={2.25} className={`send-arrow${sent ? ' go' : ''}`} />
            <Square size={11} fill="currentColor" className="send-stop" />
          </button>
        </div>
        <Popover anchor={modeBtn.current} open={menu === 'mode'} onClose={() => setMenu(null)} placement="top-start" width={372}>
          <Menu items={modeItems} onClose={() => setMenu(null)} />
        </Popover>
        <Popover anchor={modelBtn.current} open={menu === 'model'} onClose={() => setMenu(null)} placement="top-end" width={320}>
          <Menu items={modelItems} onClose={() => setMenu(null)} />
        </Popover>
      </div>
      <div className="composer-foot">
        {busy ? (
          <span>
            <CornerDownLeft size={11} /> queues for after this step · <kbd>Ctrl</kbd> <kbd>Shift</kbd> <CornerDownLeft size={11} /> interrupts and sends now · <kbd>Esc</kbd> to stop
          </span>
        ) : (
          <span>
            <CornerDownLeft size={11} /> to send · <kbd>Shift</kbd> + <CornerDownLeft size={11} /> for a new line · <b>@</b> files · <b>/</b> commands
          </span>
        )}
      </div>
      {dragging && <div className="drop-hint">Drop images or text files to attach</div>}
    </div>
  );
}

/** Messages waiting for the current step to finish. Each can be edited, sent right away, or dropped. */
function QueueTray({ sessionId, held }: { sessionId: string; held: HeldMsg[] }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const save = (h: HeldMsg) => {
    setEditing(null);
    const t = draft.trim();
    if (!t && !h.images && !(h.files || []).length) removeHeld(sessionId, h.uuid).catch(() => {});
    else if (t !== h.text) updateHeld(sessionId, h.uuid, t).catch(() => {});
  };
  return (
    <Leave className="queue" role="list" aria-label="Queued messages">
      <div className="queue-head">
        <Clock3 size={12} />
        <span>{held.length === 1 ? 'Queued — Claude reads it after this step' : `${held.length} queued — Claude reads them in order after this step`}</span>
      </div>
      {held.map((h, i) => (
        <div key={h.uuid} className={`queue-item${editing === h.uuid ? ' editing' : ''}`} role="listitem">
          <span className="queue-n tnum">{i + 1}</span>
          {editing === h.uuid ? (
            <textarea
              className="queue-edit"
              autoFocus
              rows={Math.min(6, Math.max(1, draft.split('\n').length))}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => save(h)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  save(h);
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  e.stopPropagation();
                  setDraft(h.text);
                  setEditing(null);
                }
              }}
            />
          ) : (
            <button
              className="queue-text"
              title="Click to edit"
              onClick={() => {
                setEditing(h.uuid);
                setDraft(h.text);
              }}
            >
              {h.text || <span className="faint">(no text)</span>}
              {h.images > 0 && (
                <span className="queue-attach">
                  <ImageIcon size={11} /> {h.images}
                </span>
              )}
              {(h.files || []).map((f) => (
                <span key={f} className="queue-attach">
                  <FileIcon size={11} /> {f}
                </span>
              ))}
            </button>
          )}
          <span className="queue-actions">
            <button className="queue-btn" title="Send now — interrupts the current step" onClick={() => sendHeldNow(sessionId, h.uuid)}>
              <Zap size={12} />
              <span>Now</span>
            </button>
            <button className="queue-btn icon" title="Remove" aria-label="Remove queued message" onClick={() => removeHeld(sessionId, h.uuid)}>
              <X size={13} />
            </button>
          </span>
        </div>
      ))}
    </Leave>
  );
}

const hostOf = (url: string) => {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};
