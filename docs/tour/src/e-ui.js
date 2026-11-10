/* ui: fit to the screen, dock, notes, toast, theme, palette */
const svg = (d, n) => '<svg width="' + n + '" height="' + n + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
const ICO = {
  left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>', x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  note: '<path d="M4 6h16M4 12h16M4 18h10"/>',
};
function fit() {
  const W = innerWidth, H = innerHeight, narrow = W < 900;
  const z = narrow ? W / Math.max(W, 520) : Math.max(0.85, Math.min(W / 1500, H / 860, 2.4));
  Z = z;
  rootEl.classList.toggle('tx-narrow', narrow);
  scaleEl.style.width = W / z + 'px';
  scaleEl.style.height = H / z + 'px';
  scaleEl.style.transform = z === 1 ? '' : 'scale(' + z + ')';
}
let toastT;
function toast(msg) {
  const t = q('#tx-toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove('show'), 2400);
}
function fillNotes() {
  const s = STOPS[cur];
  q('#tx-notes').innerHTML = '<div class="tx-notes-h">講稿<b>' + s.t + '</b><span class="tx-notes-say">' + s.say + '</span><button class="tx-x" data-tx="notes" aria-label="關閉講稿">' + svg(ICO.x, 14) + '</button></div><p>' + s.notes[0] + '</p>' + (s.notes[1] ? '<p>' + s.notes[1] + '</p>' : '');
}
function toggleNotes(on) {
  const n = q('#tx-notes');
  on = on == null ? !n.classList.contains('open') : on;
  if (on) fillNotes();
  n.classList.toggle('open', on);
}
function theme() { html.setAttribute('data-theme', html.getAttribute('data-theme') === 'light' ? 'dark' : 'light'); }

let palSel = 0, palItems = [];
const palIsOpen = () => !!q('#tx-pal').firstChild;
function palDraw(text) {
  const t = text.trim().toLowerCase();
  const all = STOPS.map((s, i) => ({ g: s.chap, label: s.t, hint: s.say, n: two(i), run: () => jump(i) })).concat([
    { g: '指令', label: '切換深色／淺色', hint: '', ic: 'sun', run: theme },
    { g: '指令', label: '顯示或隱藏講稿', hint: '', ic: 'note', k: 'N', run: () => toggleNotes() },
  ]);
  palItems = all.filter((x) => !t || (x.label + ' ' + x.g + ' ' + x.hint + ' ' + (x.n || '')).toLowerCase().includes(t));
  palSel = Math.max(0, Math.min(palSel, palItems.length - 1));
  let g = null;
  q('#tx-pal-list').innerHTML = palItems.length ? '<div role="presentation">' + palItems.map((x, i) =>
    (x.g !== g ? '<div class="pal-group" role="presentation">' + (g = x.g) + '</div>' : '') +
    '<button class="pal-item' + (i === palSel ? ' active' : '') + '" role="option" aria-selected="' + (i === palSel) + '" tabindex="-1" data-pal="' + i + '">' +
    (x.n ? '<span class="tx-pal-n">' + x.n + '</span>' : '<span class="tx-pal-n">' + svg(ICO[x.ic], 14) + '</span>') +
    '<span class="pal-label ellipsis">' + x.label + '</span><span class="pal-hint ellipsis">' + x.hint + '</span>' +
    (x.k ? '<span class="keys pal-keys"><kbd>' + x.k + '</kbd></span>' : '') + '</button>').join('') + '</div>' : '<div class="tx-none">沒有符合的項目</div>';
  const sel = q('#tx-pal .pal-item.active');
  if (sel) sel.scrollIntoView({ block: 'nearest' });
}
function palOpen() {
  q('#tx-pal').innerHTML = '<div class="scrim"><div class="dialog palette" role="dialog" aria-modal="true" aria-label="跳至主題" style="width: 600px;"><div class="dialog-body"><div class="pal-input">' + svg(ICO.search, 16) +
    '<input id="tx-q" role="combobox" aria-expanded="true" placeholder="跳至主題或執行指令…" autocomplete="off" spellcheck="false" aria-label="搜尋主題"></div><div class="pal-list" role="listbox" id="tx-pal-list"></div></div></div></div>';
  palSel = cur;
  palDraw('');
  q('#tx-q').focus();
}
function palClose() { q('#tx-pal').innerHTML = ''; }
function palRun(i) { const it = palItems[i]; if (!it) return; palClose(); it.run(); }
