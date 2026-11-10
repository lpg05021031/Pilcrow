/* plays: how each stop arrives. Every state on stage is a captured frame of the real interface. */
async function typeInto(sel, frame, text, sl) {
  holdText = true;
  const ta0 = q(sel, stage);
  if (ta0 && !RM && matchMedia('(pointer:fine)').matches) ta0.focus({ preventScroll: true });
  const inc = text.length > 60 ? 3 : text.length > 30 ? 2 : 1;
  for (let i = 1; i <= text.length; i += inc) {
    const ta = q(sel, stage);
    if (ta) ta.value = text.slice(0, i);
    if (i === 1) show(frame);
    await sl(55);
  }
  const full = q(sel, stage);
  if (full) full.value = text;
  await sl(300);
}
async function stream(sel, sl) {
  const el = q(sel, stage);
  if (!el) return;
  const nodes = [], hid = qa('*', el), w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let total = 0;
  while (w.nextNode()) { nodes.push([w.currentNode, w.currentNode.nodeValue]); total += w.currentNode.nodeValue.length; }
  const step = Math.max(2, Math.ceil(total / 110));
  nodes.forEach((x) => { x[0].nodeValue = ''; });
  hid.forEach((x) => { x.style.display = 'none'; });
  for (const [node, text] of nodes) {
    for (let p = node.parentElement; p && p !== el; p = p.parentElement) p.style.display = '';
    for (let i = 0; i < text.length; i += step) { node.nodeValue = text.slice(0, i + step); toEnd(); await sl(30); }
  }
  hid.forEach((x) => { x.style.display = ''; });
}
async function play1(sl) {
  marks([]);
  await typeInto('.welcome-composer textarea', 'f00-typed', TXT.u1, sl);
  await point('.welcome-composer .send', sl);
  const from = q('.welcome-composer', stage), a = from && from.getBoundingClientRect();
  holdText = false;
  show('f10-sent');
  const sc = q('.composer-wrap .composer', stage);
  if (a && sc && !RM) {
    const b = sc.getBoundingClientRect();
    const t = 'translate(' + (a.left - b.left) / Z + 'px,' + (a.top - b.top) / Z + 'px) scale(' + a.width / b.width + ')';
    sc.animate([{ transform: t, transformOrigin: '0 0' }, { transform: 'none', transformOrigin: '0 0' }], { duration: 620, easing: EASE });
  }
  await sl(820);
  show('f11-a1');
  marks([{ sel: '#text-t-1', tag: '回覆即時串流' }]);
  await stream('#text-t-1', sl);
  await sl(260);
  const seq = ['t0a', 't0b', 't1a', 't1b', 't2a', 't2b', 't3a', 't3b'];
  for (let i = 0; i < seq.length; i++) { show('f12-' + seq[i]); await sl(i % 2 ? 200 : i < 4 ? 420 : 720); }
  show('f12-stat');
  await sl(420);
  await typeInto('.composer-wrap textarea', 'f12-typed', TXT.u2, sl);
  await point('.composer-wrap .send', sl);
  holdText = false;
  show('f13-queued');
  await sl(280);
}
async function play3(sl) {
  marks([]);
  show('f13-queued');
  await sl(460);
  show('f30-u2');
  await sl(760);
  show('f31-a2');
  await stream('#text-t-2', sl);
  await sl(280);
  show('f32-perm');
  await sl(560);
}
async function play4(sl) {
  marks([]);
  show('f40-a3');
  marks([{ sel: '#text-t-3 h2, #text-t-3 ul', all: true, tag: 'Handoff：固定四欄', cls: 'corner' }]);
  await stream('#text-t-3', sl);
  await sl(260);
  show('f41-done');
  await sl(260);
}
async function act8(sl) {
  show('f81-woke');
  await sl(980);
}
/* each stop arrives the way a person would get there: the pointer goes to the control, clicks, and the thing opens */
const open = async (sel, frame, sl, o) => { await point(sel, sl, o); show(frame, o && o.focus ? { focus: o.focus } : null); await sl((o && o.wait) || 640); };
const base = async (frame, sl) => { if (frameNow !== frame) { show(frame); await sl(360); } };
const tab = (i) => () => qa('.panel-tab', stage)[i];
const nav = (name) => byText('#tx-over .settings-nav button', name);
/* a new session, a typed prompt, and what the prompt sets off */
async function prompted(sl, pre, text, live, end, during) {
  marks([]);
  await point('.side-new', sl, { x: 0.3 });
  show(pre + '-empty');
  await sl(520);
  await point('.composer-input', sl, { x: 0.3, y: 0.4 });
  await typeInto('.composer-input', pre + '-typed', text, sl);
  await point(() => q('.send', stage), sl);
  holdText = false;
  show(live, { focus: '.msg-user' });
  marks(during);
  if (!end) return;
  await sl(2600);
  show(end, { focus: '.msg-user' });
  await sl(640);
}
const PLAYS = {
  sidebar: async (sl) => { marks([]); await base('f00-welcome', sl); await open(rowSel(ID.flag), 'f02-sidemenu', sl, { x: 0.94 }); },
  'new-session': async (sl) => { marks([]); await base('f00-welcome', sl); if (!(await point('.side-new-opts', sl))) await point('.side-new', sl, { x: 0.9 }); show('f03-newsession'); await sl(640); },
  stream: async (sl) => { marks([]); await base('f00-welcome', sl); await point('.welcome-composer textarea', sl, { x: 0.3, y: 0.4 }); await play1(sl); },
  fold: async (sl) => {
    marks([{ sel: '.work-head', tag: '工具步驟收合為一行', cls: 'side' }]);
    await sl(900);
    await open('.work-head', 'f20-open', sl, { x: 0.12, focus: '.work-head', wait: 520 });
    await open('#tool-toolu_e1 .tool-toggle', 'f21-diff', sl, { x: 0.2, focus: '.work-head', wait: 520 });
  },
  permission: play3,
  handoff: play4,
  modes: (sl) => { marks([]); return open('.cbtn.mode', 'f42-modes', sl); },
  changes: (sl) => { marks([]); return open('.diffstat', 'f50-changes', sl); },
  files: (sl) => { marks([]); return open(tab(1), 'f51-files', sl); },
  terminal: (sl) => { marks([]); return open(tab(2), 'f52-terminal', sl); },
  preview: (sl) => { marks([]); return open(tab(3), 'f53-preview', sl); },
  tasks: (sl) => { marks([]); return open(tab(4), 'f55-tasks', sl); },
  timeline: async (sl) => { marks([]); await open(tab(5), 'f56-timeline0', sl, { wait: 520 }); await open('.tl-row[data-step="2"] .tl-row-btn', 'f56-timeline', sl, { x: 0.3 }); },
  info: (sl) => { marks([]); return open(tab(6), 'f57-info', sl); },
  find: async (sl) => { marks([]); await base('f41-done', sl); await keycap(['Ctrl', 'F'], sl); show('f43-find'); await sl(640); },
  palette: async (sl) => { marks([]); await keycap(['Ctrl', 'K'], sl); show('f44-palette'); await sl(640); },
  shortcuts: async (sl) => { marks([]); await keycap(['Ctrl', '/'], sl); show('f45-shortcuts'); await sl(640); },
  settings: async (sl) => { marks([]); await base('f41-done', sl); await open('.side-foot-btn', 'f90-settings', sl); },
  skills: (sl) => { marks([]); return open(nav('Skills'), 'f91-skills', sl); },
  mcp: (sl) => { marks([]); return open(nav('MCP servers'), 'f92-mcp', sl); },
  diagnostics: (sl) => { marks([]); return open(nav('Diagnostics'), 'f93-diag', sl); },
  board: async (sl) => {
    marks([]);
    await open(() => qa('.side-page', stage)[0], 'f60-board', sl, { wait: 900 });
    marks([{ sel: '.bd-lane', all: true, tag: '五欄：依工作歸屬劃分', cls: 'below' }]);
    await sl(2000);
  },
  'board-new': (sl) => { marks([]); return open('button[title^="New card"]', 'f61-new', sl); },
  review: async (sl) => { marks([]); await base('f60-board', sl); await open(cardSel(ID.login), 'f70-review', sl, { wait: 900 }); },
  wait: (sl) => { marks([]); return open(cardSel(ID.ci), 'f80-wait', sl, { wait: 760 }); },
  schedule: async (sl) => {
    marks([]);
    await base('f82-end', sl);
    await open('.bd-lane-menu', 'f83-lanemenu', sl, { wait: 420 });
    await open(byText('#tx-over .menu-item', 'New schedule'), 'f83-schedule', sl);
  },
  dispatch: async (sl) => {
    marks([]);
    await base('f82-end', sl);
    await open(() => qa('.side-page', stage)[2], 'f84-dispatch', sl, { wait: 760 });
    await open('.ovr.dp-fin', 'f84-dispatch2', sl, { x: 0.2 });
  },
  helpers: (sl) => prompted(sl, 'f85-lead', 'Start two helpers: one for the api and one for the web.', 'f85-helpers-live', 'f86-helpers', [{ sel: '.msg-user .msg-bubble', tag: '提示：要求啟動 helper', cls: 'lside', pad: 4 }, { sel: '.helpers-bar', tag: 'Helpers 列：各 helper 的狀態', cls: 'below', pad: 0 }]),
  workflow: async (sl) => {
    await prompted(sl, 'f88-wf', 'Use a workflow to review the login fix: look for bugs and performance issues, then verify each finding.', 'f88-wf-live', '', [{ sel: '.msg-user .msg-bubble', tag: '提示：要求以 workflow 執行', cls: 'lside', pad: 4 }]);
    await sl(900);
    await point(() => qa('.work-head', stage).pop(), sl, { x: 0.12 });
    show('f88-wf-open', { focus: '.msg-user' });
    await sl(1100);
    await point('.wfi .btn.xs', sl);
    show('f88-wf-tasks', { focus: '.msg-user' });
    await sl(2200);
    show('f88-wf-done', { focus: '.msg-user' });
    await sl(640);
  },
  repos: async (sl) => {
    marks([]);
    await base('f82-end', sl);
    await open('.side-foot-btn', 'f90-settings', sl, { wait: 460 });
    await open(nav('Repositories'), 'f97-repos', sl, { wait: 700 });
    await open('#tx-over .rp-row .rp-head .icon-btn', 'f97-repo-open', sl);
  },
  'helpers-review': (sl) => { marks([]); return open('.helpers-bar .btn.xs', 'f87-helpers-review', sl); },
  memory: async (sl) => { marks([]); await base('f82-end', sl); await open('.side-foot-btn', 'f90-settings', sl, { wait: 460 }); await open(nav('Memory'), 'f94-memory', sl); },
  'memory-conflict': async (sl) => { marks([]); await point('#tx-over .dialog-head .icon-btn', sl); show('f82-end'); await sl(760); show('f96-conflict'); await sl(700); },
};
const ACT = {
  permission: async (sl) => { marks([]); await point('.perm .btn.primary', sl); show('f33-run'); await sl(1500); show('f34-ran'); await sl(320); },
  review: async (sl) => { marks([]); await point(() => qa('.bd-actions button', stage)[0], sl); show('f71-approved'); await sl(980); },
  wait: act8,
};
function playOf(n) {
  const p = PLAYS[STOPS[n].id];
  if (p) return p;
  return async (sl) => { marks([]); show(frameOf(n, false), { focus: specOf(n).focus || null }); await sl(specOf(n).card ? 420 : 640); };
}
