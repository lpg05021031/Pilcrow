// App-wide tooltips. Any element with a `title` gets a styled tooltip instead of the browser's:
// it appears after a short pause, or at once while the pointer moves between controls ("warm"),
// shows keyboard shortcuts as keycaps, and never covers the thing you are pointing at.
// Components keep using plain `title=…`; nothing else needs to change.

const SHOW_DELAY = 480;
const WARM_FOR = 450;
const SHORTCUT = /^(.*?)\s*\(((?:Ctrl|Alt|Shift|Esc|Enter|Tab|Space|⌘|F\d{1,2})[^()]*)\)\s*$/;

let tip: HTMLDivElement | null = null;
let current: HTMLElement | null = null;
let timer: number | undefined;
let lastHidden = 0;
// After a click, that control stays quiet until the pointer leaves it.
let suppressed: HTMLElement | null = null;

function text(el: HTMLElement) {
  const t = el.getAttribute('title');
  if (t !== null) {
    // Keep the accessible name the title used to provide, then take the title over.
    if (!el.getAttribute('aria-label') && !(el.textContent || '').trim()) el.setAttribute('aria-label', t);
    el.dataset.tip = t;
    el.removeAttribute('title');
  }
  return el.dataset.tip || '';
}

function render(content: string) {
  const el = tip!;
  el.textContent = '';
  const m = content.match(SHORTCUT);
  const label = document.createElement('span');
  label.textContent = m ? m[1] : content;
  el.append(label);
  if (m) {
    // "(Shift+Tab to switch)": the first word is the key combo, anything after it is a note.
    const combo = m[2].split(' ')[0];
    const note = m[2].slice(combo.length).trim();
    const keys = document.createElement('span');
    keys.className = 'tip-keys';
    for (const k of combo.split(/\+(?!$)/)) {
      const kbd = document.createElement('kbd');
      kbd.textContent = k.trim();
      keys.append(kbd);
    }
    if (note) {
      const n = document.createElement('span');
      n.className = 'tip-note';
      n.textContent = note;
      keys.append(n);
    }
    el.append(keys);
  }
}

function place(anchor: HTMLElement) {
  const el = tip!;
  const a = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  const below = a.bottom + 8 + h < innerHeight - 8;
  const top = below ? a.bottom + 8 : a.top - 8 - h;
  const left = Math.max(8, Math.min(innerWidth - w - 8, a.left + a.width / 2 - w / 2));
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
  el.dataset.side = below ? 'bottom' : 'top';
}

function show(anchor: HTMLElement) {
  const content = text(anchor);
  if (!content || !anchor.isConnected) return;
  render(content);
  tip!.classList.remove('show');
  tip!.style.visibility = 'hidden';
  tip!.hidden = false;
  place(anchor);
  tip!.style.visibility = '';
  requestAnimationFrame(() => tip?.classList.add('show'));
}

function hide() {
  window.clearTimeout(timer);
  if (current && tip && !tip.hidden) lastHidden = performance.now();
  current = null;
  if (tip) {
    tip.classList.remove('show');
    tip.hidden = true;
  }
}

function target(node: EventTarget | null): HTMLElement | null {
  const el = (node as Element | null)?.closest?.('[title], [data-tip]') as HTMLElement | null;
  if (!el || el.closest('.tip') || el.dataset.notip !== undefined) return null;
  if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'IFRAME') return null;
  return el;
}

function arm(el: HTMLElement, immediate = false) {
  if (el === current || el === suppressed) return;
  hide();
  current = el;
  text(el); // take the title over now, before the browser's own tooltip can appear
  const warm = performance.now() - lastHidden < WARM_FOR;
  timer = window.setTimeout(() => current === el && show(el), immediate || warm ? 0 : SHOW_DELAY);
}

export function installTooltips() {
  if (tip) return;
  tip = document.createElement('div');
  tip.className = 'tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.append(tip);

  document.addEventListener('pointerover', (e) => {
    if ((e as PointerEvent).pointerType === 'touch') return;
    const el = target(e.target);
    if (el) arm(el);
    else if (current && !current.contains(e.target as Node)) hide();
  });
  document.addEventListener('pointerout', (e) => {
    if (suppressed && !suppressed.contains(e.relatedTarget as Node)) suppressed = null;
    if (current && !current.contains(e.relatedTarget as Node)) hide();
  });
  document.addEventListener(
    'pointerdown',
    (e) => {
      suppressed = target(e.target);
      window.clearTimeout(timer);
      current = null;
      tip?.classList.remove('show');
      if (tip) tip.hidden = true;
    },
    true,
  );
  document.addEventListener('focusin', (e) => {
    const el = target(e.target);
    if (el && (e.target as HTMLElement).matches?.(':focus-visible')) arm(el, true);
  });
  document.addEventListener('focusout', () => hide());
  // A control can fade away under a still pointer (row actions that only show on hover); never leave
  // its tooltip floating on its own.
  document.addEventListener(
    'pointermove',
    () => {
      if (current && (!current.isConnected || !current.matches(':hover') || getComputedStyle(current).opacity === '0')) hide();
    },
    { passive: true },
  );
  for (const ev of ['keydown', 'wheel']) document.addEventListener(ev, hide, true);
  document.addEventListener('scroll', hide, true);
  window.addEventListener('blur', hide);
}
