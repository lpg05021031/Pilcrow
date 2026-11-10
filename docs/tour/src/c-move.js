function chrome() {
  const s = STOPS[cur], pending = !!s.act && !done;
  q('#tx-num').textContent = two(cur) + ' / ' + two(LAST);
  const g = s.group, trail = [s.chap].concat(g && !s.lead ? [g.t] : []).concat([s.t]);
  q('#tx-chap').innerHTML = cur ? trail.map((x, i) => (i === trail.length - 1 ? '<b>' + x + '</b>' : '<span>' + x + '</span>')).join('<i>›</i>') : '<b>Pilcrow 互動導覽</b>';
  q('#tx-up').innerHTML = cur < LAST && !pending ? '<span>下一項</span>' + STOPS[cur + 1].t : '';
  q('#tx-hint').textContent = pending && !busy ? s.hint : '';
  q('#tx-keys').innerHTML = (s.keys || []).map((k) => '<span><kbd>' + k[0] + '</kbd>' + k[1] + '</span>').join('');
  const seg = g ? [g.id].concat(g.stops) : [];
  q('#tx-segs').innerHTML = seg.map((id) => '<i class="tx-seg' + (IDX[id] < cur ? ' seen' : IDX[id] === cur ? ' cur' : '') + '"></i>').join('');
  q('#tx-next').classList.toggle('wait', (pending && !busy) || cur === 0);
  setCard(specOf(cur).card ? s.id : '');
  setCite(specOf(cur).cite || '');
  q('#tx-prev').disabled = cur === 0;
  q('#tx-next').disabled = cur === LAST && !pending;
  try { history.replaceState(null, '', '#' + s.id); } catch (e) {}
  if (q('#tx-notes').classList.contains('open')) fillNotes();
}
function paint(n, d) {
  cur = n; done = d;
  cursorOff();
  show(frameOf(n, d), { cut: true, focus: specOf(n).focus || null });
  chrome();
  marks(ringsFor(n, d)); ringView();
}
async function go(n, d, fn) {
  const my = ++run;
  busy = true; cur = n; done = d;
  chrome();
  const sl = (ms) => new Promise((ok, no) => setTimeout(() => (my === run ? ok() : no(CANCEL)), RM ? 0 : ms));
  try { await fn(sl); } catch (e) { if (e !== CANCEL) console.error(e); return; }
  if (my !== run) return;
  busy = false;
  cursorRest();
  chrome();
  marks(ringsFor(n, d)); ringView();
}
function settle() {
  run++; busy = false; holdText = false; followUntil = 0;
  cursorOff();
  const a = document.activeElement;
  if (a && a.tagName === 'TEXTAREA') a.blur();
  qa('.tx-press', stage).forEach((el) => el.classList.remove('tx-press'));
}
function next() {
  if (busy) { settle(); paint(cur, done); return; }
  if (STOPS[cur].act && !done) { go(cur, true, ACT[STOPS[cur].id]); return; }
  if (cur < LAST) go(cur + 1, false, playOf(cur + 1));
}
function back() {
  settle();
  if (cur === 0) return paint(0, false);
  if (done && STOPS[cur].act) return paint(cur, false);
  paint(cur - 1, !!STOPS[cur - 1].act);
}
function jump(n) {
  settle();
  if (n === 0) return paint(0, false);
  marks([]);
  show(frameOf(n - 1, !!STOPS[n - 1].act), { cut: true, focus: specOf(n - 1).focus || null });
  go(n, false, playOf(n));
}
