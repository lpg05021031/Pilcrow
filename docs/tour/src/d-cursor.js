/* cursor: a pointer travels to the control that opens the next thing, so nothing appears from nowhere */
const cursorEl = q('#tx-cursor'), keysEl = q('#tx-keycap');
let curX = null, curY = null, curT = 0;
const findEl = (sel) => (typeof sel === 'function' ? sel() : typeof sel === 'string' ? q(sel, stage) : sel);
const byText = (sel, text) => () => qa(sel, stage).find((b) => b.textContent.trim().indexOf(text) === 0);
async function point(sel, sl, o) {
  o = o || {};
  const el = findEl(sel), r = el ? el.getBoundingClientRect() : null;
  if (!r || !r.width || rootEl.classList.contains('tx-narrow')) return false;
  const b = scaleEl.getBoundingClientRect();
  const x = (r.left + r.width * (o.x == null ? 0.5 : o.x) - b.left) / Z, y = (r.top + r.height * (o.y == null ? 0.55 : o.y) - b.top) / Z;
  clearTimeout(curT);
  if (curX == null || !cursorEl.classList.contains('on')) {
    curX = Math.min(b.width / Z - 60, x + 240); curY = Math.min(b.height / Z - 110, y + 190);
    cursorEl.style.transition = 'none';
    cursorEl.style.transform = 'translate(' + curX + 'px,' + curY + 'px)';
    cursorEl.getBoundingClientRect();
  }
  const d = Math.hypot(x - curX, y - curY), ms = RM ? 0 : Math.round(Math.max(380, Math.min(880, d * 1.15)));
  cursorEl.style.transition = 'transform ' + ms + 'ms cubic-bezier(.45,.05,.2,1), opacity 220ms';
  cursorEl.classList.add('on');
  cursorEl.style.transform = 'translate(' + x + 'px,' + y + 'px)';
  curX = x; curY = y;
  await sl(ms + 110);
  if (o.hover) return true;
  cursorEl.classList.add('click');
  el.classList.add('tx-press');
  await sl(180);
  el.classList.remove('tx-press');
  cursorEl.classList.remove('click');
  return true;
}
function cursorRest(ms) { clearTimeout(curT); curT = setTimeout(() => cursorEl.classList.remove('on'), ms == null ? 1500 : ms); }
function cursorOff() { clearTimeout(curT); cursorEl.classList.remove('on', 'click'); keysEl.classList.remove('on'); }
async function keycap(keys, sl) {
  if (RM) return;
  keysEl.innerHTML = keys.map((k) => '<kbd>' + k + '</kbd>').join('<i>+</i>');
  keysEl.classList.add('on');
  await sl(900);
  keysEl.classList.remove('on');
  await sl(120);
}
