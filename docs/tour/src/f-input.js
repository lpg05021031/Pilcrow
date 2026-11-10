/* input: the real controls on stage drive the tour */
const pending = () => !!STOPS[cur].act && !done && !busy;
const NO = '示範版未收錄此功能';
function stageClick(x, e) {
  const b = x.closest('button,.side-row,.bd-card,.composer');
  if (!b) return;
  if (e.detail && b.blur && b.tagName === 'BUTTON') b.blur();
  const text = b.textContent.trim();
  if (b.matches('.work-head')) { if (!busy) toggleWork(b); return; }
  if (b.matches('.tool-toggle')) { if (!busy) toggleTool(b); return; }
  if (b.matches('.tool-head,.side-group-toggle')) return;
  if (b.closest('.perm-actions')) {
    if (!at('permission') || !pending()) return;
    return /Allow/.test(text) ? next() : /Deny/.test(text) ? toast('示範版請選擇 Allow 以繼續') : toast(NO);
  }
  if (b.closest('.bd-actions')) {
    if (at('review') && pending() && /^Approve/.test(text)) return next();
    return toast(at('review') && pending() ? '示範版僅示範核准流程' : NO);
  }
  if (b.matches('.panel-tab')) {
    if (at('helpers-review')) return toast(NO);
    const tabs = ['changes', 'files', 'terminal', 'preview', 'tasks', 'timeline', 'info'], ti = qa('.panel-tab', stage).indexOf(b);
    if (tabs[ti]) { settle(); paint(IDX[tabs[ti]], false); }
    return;
  }
  if (b.closest('.settings-nav')) {
    const to = { General: 'settings', Repositories: 'repos', Skills: 'skills', 'MCP servers': 'mcp', Diagnostics: 'diagnostics', Memory: 'memory' }[text];
    if (!to) return toast(NO);
    settle(); paint(IDX[to], false);
    return;
  }
  if (b.matches('.side-page')) {
    const pi = qa('.side-page', stage).indexOf(b);
    if (pi === 1) return toast('示範版未收錄收件匣');
    if (pi === 2) { if (!at('dispatch')) jump(IDX.dispatch); return; }
    if (cur < IDX.board) jump(IDX.board);
    return;
  }
  if (b.matches('.side-new')) { settle(); paint(IDX.install, false); return; }
  if ((b.getAttribute('title') || '').indexOf('Appearance') === 0) return theme();
  if (text === 'Search') return palOpen();
  if (b.matches('.side-row')) {
    if (b.getAttribute('data-sid') !== ID.login) return toast('示範版僅收錄「修正登入後的導向」這項工作');
    if (cur <= IDX['new-session']) return jump(IDX.stream);
    if (cur >= IDX.board) { settle(); paint(IDX.handoff, false); }
    return;
  }
  if (b.matches('.bd-card')) {
    const id = b.getAttribute('data-card');
    if (busy) return;
    if (id === ID.login && (at('board') || at('board-new'))) return jump(IDX.review);
    if (id === ID.ci && at('review') && done) return next();
    return;
  }
  if (b.matches('.send,.composer')) {
    if (at('install') && !busy) return jump(IDX.stream);
    if (b.matches('.send')) toast('示範版的對話內容為預先編排，請按 → 繼續');
    return;
  }
  if (b.tagName === 'BUTTON') toast(NO);
}
document.addEventListener('click', (e) => {
  const x = e.target;
  if (!x.closest) return;
  const tx = x.closest('[data-tx]');
  if (tx) {
    const a = tx.getAttribute('data-tx');
    if (e.detail) tx.blur();
    return a === 'next' ? next() : a === 'prev' ? back() : toggleNotes();
  }
  if (x.closest('#tx-card')) return next();
  if (x.closest('#tx-pal')) {
    const it = x.closest('.pal-item');
    if (it) palRun(+it.getAttribute('data-pal')); else if (!x.closest('.dialog')) palClose();
    return;
  }
  if (x.closest('#tx-stage')) stageClick(x, e);
});
q('#tx-pal').addEventListener('input', (e) => { palSel = 0; palDraw(e.target.value); });
stage.addEventListener('beforeinput', (e) => e.preventDefault());
document.addEventListener('keydown', (e) => {
  const k = e.key;
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'k') { e.preventDefault(); if (palIsOpen()) palClose(); else palOpen(); return; }
  if (palIsOpen()) {
    if (k === 'Escape') palClose();
    else if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault();
      const n = palItems.length || 1;
      palSel = (palSel + (k === 'ArrowDown' ? 1 : n - 1)) % n;
      palDraw(q('#tx-q').value);
    } else if (k === 'Enter') { e.preventDefault(); palRun(palSel); }
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const onBtn = e.target.closest && e.target.closest('button,input,a');
  const low = k.toLowerCase();
  if (k === 'ArrowRight' || k === 'PageDown') { e.preventDefault(); next(); }
  else if (k === 'ArrowLeft' || k === 'PageUp') { e.preventDefault(); back(); }
  else if ((k === ' ' || k === 'Enter') && !onBtn) { e.preventDefault(); next(); }
  else if (k === 'Escape') toggleNotes(false);
  else if (k === 'Home') jump(0);
  else if (low === 'n') toggleNotes();
  else if (k === '/') { e.preventDefault(); palOpen(); }
  else if (at('permission') && pending() && (k === '1' || k === '2')) next();
  else if (at('permission') && pending() && k === '3') toast('示範版請選擇 Allow 以繼續');
  else if (at('review') && pending() && low === 'e') next();
  else if (at('review') && pending() && (low === 'r' || low === 'w')) toast('示範版僅示範核准流程');
});

q('#tx-segs').innerHTML = STOPS.map(() => '<i class="tx-seg"></i>').join('');
q('#tx-prev').innerHTML = svg(ICO.left, 16);
q('#tx-next').innerHTML = svg(ICO.right, 16);
fit();
addEventListener('resize', fit);
const startAt = STOPS.findIndex((s) => s.id === location.hash.slice(1));
paint(startAt > 0 ? startAt : 0, false);
requestAnimationFrame(trackRings);
window.tx = { state: () => ({ cur, done, busy, id: STOPS[cur].id, n: STOPS.length }), theme, jump, paint, idx: IDX };
