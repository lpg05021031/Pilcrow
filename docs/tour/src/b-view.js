/* view: put a captured frame on stage, and animate what moved */
let frameNow = '', holdText = false, followUntil = 0;
const fk = (el) => (el.hasAttribute('data-card') ? 'c:' + el.getAttribute('data-card') : 's:' + el.getAttribute('data-sid'));
function rects() {
  const m = new Map();
  qa('[data-sid],[data-card]', stage).forEach((el) => m.set(fk(el), el.getBoundingClientRect()));
  return m;
}
function flip(before) {
  if (RM) return;
  qa('[data-sid],[data-card]', stage).forEach((el) => {
    const r0 = before.get(fk(el));
    if (!r0 || !r0.width) return;
    const r1 = el.getBoundingClientRect(), dx = (r0.left - r1.left) / Z, dy = (r0.top - r1.top) / Z;
    if (Math.abs(dx) + Math.abs(dy) < 2) return;
    const far = Math.abs(dx) > 40, cards = el.closest('.bd-cards'), lane = el.closest('.bd-lane');
    if (far && cards) { cards.style.overflow = 'visible'; lane.style.position = 'relative'; lane.style.zIndex = '3'; }
    const an = el.animate([{ transform: 'translate(' + dx + 'px,' + dy + 'px)' }, { transform: 'none' }], { duration: far ? 760 : 520, easing: EASE });
    an.onfinish = an.oncancel = () => { if (far && cards) { cards.style.overflow = ''; lane.style.position = ''; lane.style.zIndex = ''; } };
  });
}
function grow(list) {
  if (RM) return;
  list.forEach((n) => {
    if (!n.matches('.collapse') || !n.isConnected) return;
    const h = n.offsetHeight;
    if (!h) return;
    n.style.overflow = 'hidden';
    const an = n.animate([{ height: '0px', opacity: 0 }, { height: h + 'px', opacity: 1 }], { duration: 360, easing: EASE });
    an.onfinish = an.oncancel = () => { n.style.overflow = ''; };
  });
}
function toEnd() { const t = q('.transcript', stage); if (t) t.scrollTop = t.scrollHeight; }
function follow(ms) {
  const first = followUntil < performance.now();
  followUntil = performance.now() + ms;
  if (!first) return;
  const tick = () => { if (performance.now() > followUntil) return; toEnd(); requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}
function reveal(sel, cut) {
  const t = q('.transcript', stage), el = q(sel, stage);
  if (!t || !el) return;
  followUntil = 0;
  const top = (el.getBoundingClientRect().top - t.getBoundingClientRect().top) / Z + t.scrollTop;
  t.scrollTo({ top: Math.max(0, top - 150), behavior: cut || RM ? 'auto' : 'smooth' });
}
function cardInView() {
  const sel = q('.bd-card.sel', stage), box = sel && sel.closest('.bd-lanes');
  if (!box) return;
  const a = sel.getBoundingClientRect(), b = box.getBoundingClientRect();
  if (a.right > b.right) box.scrollLeft += (a.right - b.right) / Z + 16;
  else if (a.left < b.left) box.scrollLeft -= (b.left - a.left) / Z + 16;
}
function show(name, o) {
  o = o || {};
  const t = tree(name), live = stage.firstElementChild, fresh = [];
  const before = live && !o.cut ? rects() : null;
  if (!live || !live.matches('.app')) stage.insertBefore(t.cloneNode(true), stage.firstChild); else morph(live, t, fresh);
  frameNow = name;
  over(name, o.cut);
  findMarks(name === 'f43-find' ? 'next' : '');
  if (before) { flip(before); grow(fresh); enter(fresh); }
  if (o.focus) reveal(o.focus, o.cut); else { toEnd(); if (!o.cut) follow(480); }
  cardInView();
  return fresh;
}
/* what the real app portals to <body>: dialogs and menus, laid over the stage */
let overNow = '';
const POP = { 'f02-sidemenu': () => rowSel(ID.flag), 'f42-modes': () => '.cbtn.mode', 'f83-lanemenu': () => '.bd-lane-menu' };
function over(name, cut) {
  const box = q('#tx-over'), key = OV[name] ? name : '';
  box.classList.toggle('tx-cut', !!cut || RM);
  if (key === overNow) return;
  overNow = key;
  box.innerHTML = '';
  if (!key) return;
  const t = document.createElement('template');
  t.innerHTML = OV[name];
  Array.from(t.content.children).forEach((el) => { box.appendChild(hydrate(el)); });
  qa('input,textarea,select', box).forEach((x) => { x.setAttribute('tabindex', '-1'); x.setAttribute('readonly', ''); });
  placeOver();
  const sp = SCROLL[name];
  if (sp) { const sc = q(sp[0], box); if (sc) sc.scrollTop = sp[1]; }
}
const SCROLL = { 'f94-memory': ['.settings-body', 260], 'f83-schedule': ['.dialog-body', 150] };
function placeOver() {
  const p = q('#tx-over .popover'), sel = POP[overNow];
  if (!p || !sel) return;
  const a = q(sel(), stage), r = a ? a.getBoundingClientRect() : null;
  p.style.display = r && r.width ? '' : 'none';
  if (!r || !r.width) return;
  const s = stage.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;
  const right = p.style.transformOrigin.indexOf('right') >= 0, up = p.getAttribute('data-placement') === 'top';
  const left = right ? (r.right - s.left) / Z - w : (r.left - s.left) / Z;
  p.style.left = Math.max(8, Math.min(left, s.width / Z - w - 8)) + 'px';
  p.style.top = (up ? (r.top - s.top) / Z - h - 6 : (r.bottom - s.top) / Z + 6) + 'px';
}
function findMarks(word) {
  if (!window.Highlight || !window.CSS || !CSS.highlights) return;
  CSS.highlights.delete('find');
  CSS.highlights.delete('find-current');
  const t = word && q('.transcript', stage);
  if (!t) return;
  const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT), rs = [];
  while (w.nextNode()) {
    const v = w.currentNode.nodeValue;
    for (let i = v.indexOf(word); i >= 0; i = v.indexOf(word, i + 1)) { const r = new Range(); r.setStart(w.currentNode, i); r.setEnd(w.currentNode, i + word.length); rs.push(r); }
  }
  if (!rs.length) return;
  CSS.highlights.set('find-current', new Highlight(rs[0]));
  if (rs.length > 1) CSS.highlights.set('find', new Highlight(...rs.slice(1)));
}
/* folding by hand: swap in the captured open or closed markup for that block */
function toggleWork(head) {
  if (frameNow.indexOf('f8') === 0) return false;
  const unit = head.closest('.unit.work'), k = qa('.unit.work', stage).indexOf(unit), work = q('.work', unit);
  const open = head.getAttribute('aria-expanded') === 'true', name = 'w' + k + (open ? ':closed' : ':open');
  if (!PT[name]) return false;
  const liveNow = work.classList.contains('live'), sum = q('.work-sum', unit).textContent, fresh = [];
  if (liveNow && k > 0) return false;
  morph(unit, part(name), fresh);
  if (liveNow) {
    q('.work', unit).classList.add('live');
    q('.work-sum', unit).classList.add('shimmer');
    q('.work-sum', unit).textContent = sum;
    q('.work-mark', unit).classList.remove('gone');
  }
  grow(fresh);
  return true;
}
function toggleTool(btn) {
  if (frameNow.indexOf('f8') === 0) return false;
  const tool = btn.closest('.tool'), open = btn.getAttribute('aria-expanded') === 'true', name = tool.id + (open ? ':closed' : ':open'), fresh = [];
  if (!tool.id || !PT[name] || tool.classList.contains('awaiting')) return false;
  morph(tool, part(name), fresh);
  grow(fresh);
  return true;
}
function enter(list) {
  if (RM) return;
  list.forEach((n) => {
    if (!n.isConnected) return;
    if (n.matches('.panel,.bd-panel')) n.animate([{ transform: 'translateX(36px)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 460, easing: EASE });
    else if (n.parentElement && n.parentElement.matches('.main')) n.animate([{ transform: 'translateY(10px)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 420, easing: EASE });
  });
}
