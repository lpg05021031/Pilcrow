const D = JSON.parse(document.getElementById('tx-data').textContent);
const FR = D.frames, IC = D.icons, PT = D.parts, ID = D.ids, OV = D.overs || {};
const GRAFT = { 'f55-tasks': ['.transcript', 'f50-changes'] };
const q = (s, r) => (r || document).querySelector(s);
const qa = (s, r) => Array.from((r || document).querySelectorAll(s));
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const html = document.documentElement;
const rootEl = q('#tx-root'), scaleEl = q('#tx-scale'), stage = q('#tx-stage');
const two = (n) => (n < 10 ? '0' : '') + n;
const EASE = 'cubic-bezier(.22,1,.36,1)';
let Z = 1;

html.setAttribute('data-os', 'win');
html.setAttribute('data-engine', 'blink');
html.setAttribute('data-theme', 'light');
html.style.setProperty('--font-size', '14.5px');

function make(src) { const t = document.createElement('template'); t.innerHTML = src; return t.content.firstElementChild; }
function hydrate(el) {
  qa('svg[data-ic]', el).concat(el.matches('svg[data-ic]') ? [el] : []).forEach((s) => {
    const ic = IC[s.getAttribute('data-ic')];
    s.removeAttribute('data-ic');
    if (!ic) return;
    Object.keys(ic.at).forEach((k) => s.setAttribute(k, ic.at[k]));
    s.innerHTML = ic.inner;
  });
  qa('textarea', el).forEach((t) => { t.setAttribute('inputmode', 'none'); t.setAttribute('tabindex', '-1'); });
  qa('[draggable]', el).forEach((c) => c.removeAttribute('draggable'));
  qa('[contenteditable]', el).forEach((c) => c.removeAttribute('contenteditable'));
  return el;
}
function fix(el) {
  const w = q('.welcome-title', el), b = q('.bd-title', el), now = new Date(), hr = now.getHours();
  if (w) w.textContent = hr < 5 ? 'Working late' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
  if (b) b.textContent = now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  return el;
}
const cache = {};
function tree(name) {
  if (!cache[name]) {
    const el = fix(hydrate(make(FR[name]))), g = GRAFT[name];
    if (g) { const a = q(g[0], el), b = q(g[0], tree(g[1])); if (a && b) a.replaceWith(b.cloneNode(true)); }
    cache[name] = el;
  }
  return cache[name];
}
function part(name) {
  const k = 'p:' + name;
  if (!cache[k]) cache[k] = hydrate(make(PT[name]));
  return cache[k];
}

/* morph: turn the live tree into the target tree, keeping every node that can be kept */
function keyOf(n) {
  if (n.nodeType !== 1) return null;
  if (n.id) return n.id;
  const k = n.getAttribute('data-sid') || n.getAttribute('data-card');
  if (k) return k;
  if (n.classList.contains('side-group')) { const t = q('.side-group-toggle', n); return t ? 'g:' + t.title : null; }
  return null;
}
function sig(n) {
  if (n.nodeType !== 1) return '#' + n.nodeType;
  const c = n.classList;
  return n.tagName + '.' + (c[0] || '') + (c[0] === 'unit' ? '.' + c[1] : '');
}
function syncAttrs(a, b) {
  const at = b.attributes;
  for (let i = 0; i < at.length; i++) if (a.getAttribute(at[i].name) !== at[i].value) a.setAttribute(at[i].name, at[i].value);
  Array.from(a.attributes).forEach((x) => { if (!b.hasAttribute(x.name)) a.removeAttribute(x.name); });
}
function morph(a, b, fresh) {
  syncAttrs(a, b);
  if (a.tagName === 'svg') { if (a.innerHTML !== b.innerHTML) a.innerHTML = b.innerHTML; return; }
  if (a.tagName === 'TEXTAREA') { if (!holdText && a.value !== b.value) a.value = b.value; return; }
  const olds = Array.from(a.childNodes), used = new Set(), byKey = new Map(), out = [];
  olds.forEach((o) => { const k = keyOf(o); if (k) byKey.set(k, o); });
  let ptr = 0;
  b.childNodes.forEach((t) => {
    const k = keyOf(t), s = sig(t);
    let m = null;
    if (k) { m = byKey.get(k) || null; if (m && (used.has(m) || m.tagName !== t.tagName)) m = null; }
    else for (let i = ptr; i < olds.length; i++) {
      const o = olds[i];
      if (used.has(o) || keyOf(o) || sig(o) !== s) continue;
      m = o; ptr = i + 1; break;
    }
    if (m) {
      used.add(m);
      if (m.nodeType === 1) morph(m, t, fresh); else if (m.nodeValue !== t.nodeValue) m.nodeValue = t.nodeValue;
    } else {
      m = t.cloneNode(true);
      if (m.nodeType === 1) fresh.push(m);
    }
    out.push(m);
  });
  olds.forEach((o) => { if (!used.has(o)) a.removeChild(o); });
  let at = a.firstChild;
  out.forEach((n) => { if (n === at) at = at.nextSibling; else a.insertBefore(n, at); });
}
