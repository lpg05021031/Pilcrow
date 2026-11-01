// The keyboard: window keys, the Esc stack, keyboard activation, keycaps, and focus that waits its turn.
import { useEffect } from 'react';
import { useStore } from '../../lib/store';
import { useEvent } from './hooks';

/** A keydown listener on the window while `active`, subscribed once; it always calls the latest `onKey`. */
export function useWindowKey(onKey: (e: KeyboardEvent) => void, active = true) {
  const h = useEvent(onKey);
  useEffect(() => {
    if (!active) return;
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [active, h]);
}

// Esc closes the innermost layer only: a menu over a dialog, the dialog over the window. Each open
// layer (dialog, popover, lightbox) is on this stack; one capture listener serves the top of it.
const layers: (() => void)[] = [];

function onEscapeKey(e: KeyboardEvent) {
  const top = layers[layers.length - 1];
  if (e.key !== 'Escape' || !top) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  top();
}

/** Close this layer with Esc while `active`, before anything under it hears the key. */
export function useEscape(onEscape: () => void, active = true) {
  const close = useEvent(onEscape);
  useEffect(() => {
    if (!active) return;
    if (!layers.length) window.addEventListener('keydown', onEscapeKey, true);
    layers.push(close);
    return () => {
      layers.splice(layers.indexOf(close), 1);
      if (!layers.length) window.removeEventListener('keydown', onEscapeKey, true);
    };
  }, [active, close]);
}

/** Keyboard activation for a card that is a role="button": Enter or Space on the card itself, not on a control inside it. */
export function onActivate(run: () => void) {
  return (e: React.KeyboardEvent) => {
    if ((e.key !== 'Enter' && e.key !== ' ') || e.target !== e.currentTarget) return;
    e.preventDefault();
    e.stopPropagation();
    run();
  };
}

/** Focus an element unless the user is busy elsewhere (a dialog, the palette, another text field). */
export function focusIfFree(el: HTMLElement | null | undefined) {
  if (!el) return;
  const a = document.activeElement as HTMLElement | null;
  const busy = a && a !== document.body && a !== el && (a.closest('[role="dialog"], .palette, .dialog') || a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable);
  // A dialog or the palette open over the window (store.ts dialogs): what is under it waits.
  if (busy || Object.values(useStore.getState().dialogs).some(Boolean)) return;
  el.focus();
}

/** A keyboard shortcut drawn as separate keycaps ("Ctrl+K" → Ctrl K), the same way everywhere. */
export function Keys({ combo, className = '' }: { combo: string; className?: string }) {
  const parts = combo === '+' ? ['+'] : combo.split(/\+(?!$)/);
  return (
    <span className={`keys ${className}`}>
      {parts.map((k, i) => (
        <kbd key={i}>{k}</kbd>
      ))}
    </span>
  );
}
