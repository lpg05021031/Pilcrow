import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Square, Plus, CornerDownLeft } from 'lucide-react';
import { useStore, sendMessage, interrupt, setMode, setUi, removeHeld, memo, toasted, takeComposerInsert } from '../lib/store';
import { isBusy, type PermissionMode, type HeldMsg } from '../lib/types';
import { ContextRing, contextLabel, Num } from './ui/bits';
import { focusIfFree } from './ui/keys';
import { useEvent } from './ui/hooks';
import { markLaunch, reducedMotion, spring } from '../lib/motion';
import { useSuggest } from './composer/Suggest';
import { useAttachments } from './composer/Attachments';
import { MODES, ModeMenu, ModelMenu } from './composer/Menus';
import { QueueTray } from './composer/QueueTray';

const NONE: HeldMsg[] = [];

/**
 * The message field and its bar. What it holds per session (draft, sent messages) lives in the
 * session's memo (lib/store), so it survives switching away and back.
 */
export function Composer({ sessionId, cwd }: { sessionId: string; cwd: string }) {
  // Narrow reads: the runtime record changes with every streamed frame; the composer shows a few of its fields.
  const status = useStore((s) => s.rt[sessionId]?.status);
  const held = useStore((s) => s.rt[sessionId]?.held) || NONE;
  const rtMode = useStore((s) => s.rt[sessionId]?.mode);
  const rtSuggestion = useStore((s) => s.rt[sessionId]?.suggestion);
  const defaultMode = useStore((s) => s.config?.defaultMode);
  const sendWithEnter = useStore((s) => (s.config?.sendKey || 'enter') === 'enter');
  const isRoot = useStore((s) => s.info?.isRoot);
  const insert = useStore((s) => s.composerInsert);
  const [text, setText] = useState(() => memo(sessionId).draft);
  const [dragging, setDragging] = useState(false);
  const [histPos, setHistPos] = useState(-1);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const suggest = useSuggest({ sessionId, cwd, text, setText, field: ta });
  const attach = useAttachments();

  const busy = isBusy(status);
  const mode = (rtMode || defaultMode || 'default') as PermissionMode;

  // Each session keeps its own draft: another session resets the field to it.
  const reset = useEvent(() => {
    setText(memo(sessionId).draft);
    attach.clear();
    suggest.close();
    setHistPos(-1);
    requestAnimationFrame(() => focusIfFree(ta.current));
  });
  useEffect(() => reset(), [sessionId, reset]);
  useEffect(() => {
    memo(sessionId).draft = text;
  }, [sessionId, text]);

  // Text inserted from elsewhere (diff notes, edit & resend, example prompts).
  useEffect(() => {
    if (!insert || insert.id !== sessionId) return;
    takeComposerInsert();
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
  }, [text, sent]);

  const submit = async (now = false) => {
    const body = text.trim();
    if (!body && !attach.count) return;
    const payload = { text: body, ...attach.payload() };
    // The bubble that lands in the conversation flies up from here; the arrow launches with it.
    if (ta.current) markLaunch(ta.current.getBoundingClientRect());
    setSent(Date.now());
    setText('');
    attach.clear();
    suggest.close();
    setHistPos(-1);
    const m = memo(sessionId);
    m.draft = '';
    if (body) m.sent = [body, ...m.sent.filter((h) => h !== body)].slice(0, 50);
    try {
      await sendMessage(sessionId, payload, { now: now && busy });
    } catch {
      // call() has shown why; the text comes back so it can be sent again.
      setText(body);
    }
  };

  const cycleMode = () => {
    const order = MODES.filter((m) => !(m.id === 'bypassPermissions' && isRoot) && m.id !== 'auto').map((m) => m.id);
    setMode(sessionId, order[(order.indexOf(mode) + 1) % order.length]).catch(toasted);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggest.onKey(e)) return;
    if (e.key === 'Tab' && e.shiftKey) {
      e.preventDefault();
      cycleMode();
      return;
    }
    if (e.key === 'Tab' && !text && rtSuggestion) {
      e.preventDefault();
      setText(rtSuggestion);
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
      interrupt(sessionId).catch(toasted);
      return;
    }
    const hist = memo(sessionId).sent;
    if (e.key === 'ArrowUp' && text === '' && held.length && ta.current?.selectionStart === 0) {
      // Like the terminal: ↑ takes the last queued message back so it can be changed.
      e.preventDefault();
      const last = held[held.length - 1];
      removeHeld(sessionId, last.uuid).catch(toasted);
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

  const onPaste = (e: React.ClipboardEvent) => {
    const fs = Array.from(e.clipboardData.files || []);
    if (fs.length) {
      e.preventDefault();
      attach.add(fs);
    }
  };

  const hasContent = Boolean(text.trim() || attach.count);
  const showStop = busy && !hasContent;
  const suggestion = rtSuggestion && !text ? rtSuggestion : '';
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
        if (e.dataTransfer.files.length) attach.add(e.dataTransfer.files);
      }}
    >
      {suggest.view}
      {held.length > 0 && <QueueTray sessionId={sessionId} held={held} />}
      <div className={`composer mode-${mode}${busy ? ' busy' : ''}`} onClick={(e) => e.target === e.currentTarget && ta.current?.focus()}>
        {attach.view}
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
            aria-label="Message to Claude"
            aria-description={suggestion ? `Suggestion: ${suggestion}. Tab takes it.` : undefined}
            aria-autocomplete="list"
            {...suggest.fieldProps}
            rows={1}
            value={text}
            placeholder={placeholder}
            onChange={(e) => {
              setText(e.target.value);
              setHistPos(-1);
              suggest.detect(e.target.value, e.target.selectionStart);
            }}
            onClick={(e) => suggest.detect(text, (e.target as HTMLTextAreaElement).selectionStart)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onBlur={() => setTimeout(suggest.close, 120)}
            spellCheck={false}
          />
        </div>
        <div className="composer-bar">
          <button className="cbtn icon" title="Attach images or files" aria-label="Attach images or files" onClick={() => fileInput.current?.click()}>
            <Plus size={16} />
          </button>
          <input ref={fileInput} type="file" multiple hidden aria-label="Attach images or files" onChange={(e) => e.target.files && attach.add(e.target.files)} />
          <ModeMenu sessionId={sessionId} mode={mode} />
          <div className="grow" />
          <ModelMenu sessionId={sessionId} />
          <ContextButton sessionId={sessionId} />
          <button className={`send${hasContent ? ' ready' : ''}${showStop ? ' stop' : ''}`} title={showStop ? 'Stop (Esc)' : sendWithEnter ? 'Send (Enter)' : 'Send (Ctrl+Enter)'} disabled={!hasContent && !showStop} onClick={() => (showStop ? interrupt(sessionId).catch(toasted) : submit())} aria-label={showStop ? 'Stop' : 'Send'}>
            <ArrowUp key={sent} size={16} strokeWidth={2.25} className={`send-arrow${sent ? ' go' : ''}`} />
            <Square size={11} fill="currentColor" className="send-stop" />
          </button>
        </div>
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

/** How full the context window is, as a ring; opens the session details. */
function ContextButton({ sessionId }: { sessionId: string }) {
  const used = useStore((s) => s.rt[sessionId]?.usage?.contextTokens) || 0;
  const total = useStore((s) => s.rt[sessionId]?.usage?.contextWindow) || 0;
  const afterCompact = useStore((s) => s.rt[sessionId]?.usage?.afterCompact);
  if (!total) return null;
  const pct = used / total;
  return (
    <button className={`ctx-btn${pct > 0.85 ? ' hot' : ''}${pct >= 0.5 ? ' with-num' : ''}`} title={afterCompact ? 'Context compacted — its size is measured at the next reply' : `Context ${Math.round(pct * 100)}% used · ${contextLabel(used, total)}${pct >= 0.5 ? ' — /compact frees space' : ''}`} aria-label={`Context window ${Math.round(pct * 100)}% used`} onClick={() => setUi({ panel: true, panelTab: 'info' })}>
      <ContextRing used={used} total={total} size={16} />
      {pct >= 0.5 && (
        <span className="tnum">
          <Num value={pct * 100} format={(v) => `${Math.round(v)}%`} />
        </span>
      )}
    </button>
  );
}
