/* engine: which stop we are on, what it frames, and how to move between stops */
let cur = 0, done = false, busy = false, run = 0, ringList = [];
const CANCEL = {}, LAST = STOPS.length - 1;
const TXT = { u1: '修正登入後被導向 /undefined 的問題', u2: '完成後請執行測試' };
const rowSel = (id) => '.side-row[data-sid="' + id + '"]';
const cardSel = (id) => '.bd-card[data-card="' + id + '"]';
const SPEC = {};
const P = (id, f, rings, x) => { SPEC[id] = Object.assign({ f, rings: rings || [] }, x || {}); };
const IN = { cls: 'inside bot', pad: -3 };
const R = (sel, tag, cls, pad, all) => ({ sel, tag, cls: cls || '', pad, all: !!all });
const O = '#tx-over ';
P('cover', 'f00-welcome', [], { card: 1 });
P('path', 'f00-welcome', [], { card: 1 });
P('principle', 'f00-welcome', [], { card: 1 });
P('install', 'f00-welcome', [R('.welcome-composer', '輸入需求以建立 session')]);
P('sidebar', 'f02-sidemenu', [R('.side-page', '工作台、收件匣、Dispatch', 'side', 3, true), R(O + '.popover', '右鍵選單：管理 session', 'side', 3)]);
P('new-session', 'f03-newsession', [R(O + '.ns-row', '獨立 worktree', 'lside', 5), R(O + '.radio-list', '權限模式', 'lside', 5)]);
P('stream', 'f13-queued', [R('#text-t-1', '回覆即時串流'), R('.queue', '執行期間可預先排入下一則訊息')]);
P('fold', 'f21-diff', [R('.work-head', '工具步驟收合為一行', 'side'), R('#tool-toolu_e1 .diff', '內嵌顯示 Diff', 'inside')], { focus: '.work-head' });
P('permission', ['f32-perm', 'f34-ran'], (d) => (d ? [] : [R('#tool-toolu_b1', '核准卡片：指令、選項與範圍', 'edge'), R(rowSel(ID.login), '側欄同步提示', 'side', 3)]));
P('handoff', 'f41-done', [R('#text-t-3 h2, #text-t-3 ul', 'Handoff：固定四欄', 'corner', undefined, true)]);
P('modes', 'f42-modes', [R(O + '.popover', '五種權限模式', 'side', 3)]);
P('overview', 'f50-changes', [R('.sidebar', 'Session 側欄', IN.cls, IN.pad), R('.app > .main', '對話區', IN.cls, IN.pad), R('.panel', '側邊面板', IN.cls, IN.pad)]);
P('changes', 'f50-changes', [R('.changes-files', '逐檔 Diff', 'inside bot', -4), R('.changes-bar', '審查與提交', 'lside', 0)]);
P('files', 'f51-files', [R('.tree', '檔案樹', IN.cls, IN.pad), R('.files-editor', '檔案內容', IN.cls, IN.pad)]);
P('terminal', 'f52-terminal', [R('.term-host', '於工作資料夾執行指令', IN.cls, IN.pad)]);
P('preview', 'f53-preview', [R('.tx-preview', '面板內的頁面預覽', IN.cls, IN.pad)]);
P('tasks', 'f55-tasks', [R('.tasks-panel .ps:nth-of-type(1)', '待辦清單與進度', 'lside', 5), R('.tasks-panel .ps:nth-of-type(2)', '執行中的背景工作', 'lside', 5)]);
P('timeline', 'f56-timeline', [R('.tl-scrub', '各步驟變更的時間軸', 'lside', 4), R('.tl-detail-head .btn', '還原至此步驟', 'lside', 4)]);
P('info', 'f57-info', [R('.panel-body .ps:nth-of-type(1)', 'Context 用量與費用', 'lside', 5), R('.mem-peek', '專案記憶', 'lside', 5)]);
P('find', 'f43-find', [R('.findbar', '搜尋列與符合筆數', 'lside', 3), R('#text-t-1 code', '標示符合之處', 'below', 3)]);
P('palette', 'f44-palette', [R(O + '.pal-input', '搜尋指令、session 與檔案', 'lside', 2), R(O + '.pal-list', '常用指令與快捷鍵', 'lside', 2)]);
P('shortcuts', 'f45-shortcuts', [R(O + '.shortcuts', '快捷鍵總表', 'lside', 6)]);
P('settings', 'f90-settings', [R(O + '.settings-nav', '七個分類', 'lside', 3), R(O + '.settings-page', '一般設定：主題、字級、傳送鍵、通知', 'side', 3)]);
P('skills', 'f91-skills', [R(O + '.cust-group:nth-of-type(1)', '個人 skills', 'side', 5), R(O + '.cust-group:nth-of-type(2)', '專案 skills', 'side', 5), R(O + '.cust-group:nth-of-type(3)', 'Plugin 提供的 skills', 'side', 5)]);
P('mcp', 'f92-mcp', [R(O + '.cust-table', '已設定的 MCP server：範圍與連線狀態', 'side', 5)]);
P('diagnostics', 'f93-diag', [R(O + '.diag', '環境檢查結果', 'side', 5)]);
P('board', 'f60-board', [R('.bd-lane.me', 'Needs you：待使用者決定', 'below')]);
P('board-new', 'f61-new', [R('.bd-new-title, .bd-new-intent', '意圖與完成條件', 'edge', 10, true), R('.bd-new-row.acts', '交付 Claude 或保留於 Backlog', 'side', 4)]);
P('review', ['f70-review', 'f71-approved'], (d) => (d ? [] : [R('.bd-panel-body .bd-sec:nth-child(1)', 'Intent：原始意圖', 'corner', 3), R('.bd-panel-body .bd-sec:nth-child(2)', 'Handoff：交接報告', 'corner', 3), R('.bd-actions', '核准、退回或暫緩', 'redge', -4)]));
P('wait', ['f80-wait', 'f81-woke'], (d) => [R(cardSel(ID.ci), d ? '條件成立後自動喚醒' : '時間、CI 或相依卡片', d ? 'side' : 'below')]);
P('schedule', 'f83-schedule', [R(O + 'textarea', '執行指示', 'lside', 4), R(O + '.field:has(.sd-time)', '執行時間', 'lside', 4), R(O + '.radio-list', '無人值守時的權限', 'lside', 4)]);
P('dispatch', 'f84-dispatch2', [R('.dp-table', '各 repository 的執行結果', 'below', 4)]);
P('repos', 'f97-repo-open', [R(O + '.rp-bar', '掃描資料夾或手動新增', 'side', 4), R(O + '.rp-row:nth-child(1) .rp-head, ' + O + '.rp-row:nth-child(1) .rp-aliases', '名稱、別名與權限政策', 'side', 4, true), R(O + '.rp-detail', '預設值與提供給 agent 的常備說明', 'side', 3)]);
P('workflow', 'f88-wf-done', [R('.msg-user .msg-bubble', '提示：要求以 workflow 執行', 'lside', 4), R('.wfi', '對話內的流程摘要', 'below', 5), R('.wfd', '流程卡：各階段的 agent 與進度', 'inside bot', 3)], { focus: '.msg-user' });
P('helpers', 'f86-helpers', [R('.msg-user .msg-bubble', '提示：要求啟動 helper', 'lside', 4), R('.helpers-bar', 'Helpers 列：各 helper 的狀態', 'below', 0), R('.side-row.sub', '側欄：helper 列於 lead 之下', 'side', 3, true), R('.session-card', '完成通知：附交接摘要', 'corner', 4)], { focus: '.msg-user' });
P('helpers-review', 'f87-helpers-review', [R('.hc-row', '收尾：提交、建立 PR、保留或捨棄', '', 2), R('.hc-sec', '變更：每個 helper 一個區段', 'inside', 2, true)]);
P('memory', 'f94-memory', [R(O + '.mem-stats', '有無筆記的退回比例', 'side', 5), R(O + '.mem-list', '每則筆記的來源與成效', 'side', 3)]);
P('memory-conflict', 'f96-conflict', [R('.bd-card.note', '矛盾提示卡片', 'below', 3), R('.bd-panel-body', '原筆記、修正與改寫對照', 'corner', -2), R('.bd-actions', '刪除或保留', 'redge', -4)]);
P('agenda', 'f00-welcome', [], { card: 1 });
[['g-start', 'f00-welcome'], ['g-chat', 'f00-welcome'], ['g-panel', 'f41-done'], ['g-keys', 'f41-done'], ['g-set', 'f41-done'], ['g-board', 'f41-done'], ['g-fan', 'f82-end'], ['g-mem', 'f82-end'], ['g-how', 'f82-end']].forEach((x) => P(x[0], x[1], [], { card: 1 }));
P('how-outcome', 'f82-end', [], { card: 1 });
P('how-bar', 'f82-end', [], { card: 1 });
P('how-borrow', 'f82-end', [], { card: 1 });
P('end', 'f82-end', [], { card: 1 });
const specOf = (n) => SPEC[STOPS[n].id];
function frameOf(n, d) { const f = specOf(n).f; return Array.isArray(f) ? f[d ? 1 : 0] : f; }
function ringsFor(n, d) { const r = specOf(n).rings; return typeof r === 'function' ? r(d) : r; }
function marks(list) {
  const box = q('#tx-rings'), old = ringList;
  ringList = (list || []).map((m) => {
    const key = m.sel + '|' + m.tag, keep = old.find((x) => x.key === key);
    if (keep) { keep.kept = true; keep.m = m; return keep; }
    const el = make('<div class="tx-ring ' + (m.cls || '') + '"><span class="tx-tag">' + m.tag + '</span></div>');
    box.appendChild(el);
    return { m, el, key };
  });
  old.forEach((x) => { if (!x.kept) box.removeChild(x.el); x.kept = false; });
}
function rectOf(m) {
  const els = m.all ? qa(m.sel, stage) : [q(m.sel, stage)].filter(Boolean);
  if (!els.length) return null;
  const rs = els.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0);
  if (!rs.length) return null;
  const L = Math.min(...rs.map((r) => r.left)), T = Math.min(...rs.map((r) => r.top)), R = Math.max(...rs.map((r) => r.right)), B = Math.max(...rs.map((r) => r.bottom));
  return { el: els[0], left: L, top: T, right: R, bottom: B, width: R - L, height: B - T };
}
function trackRings() {
  const o = scaleEl.getBoundingClientRect(), w = stage.getBoundingClientRect();
  ringList.forEach(({ m, el }) => {
    let ok = false, r = rectOf(m);
    if (r) {
      const host = r.el.closest('.transcript,.bd-lanes,.changes-files,.bd-panel-body,.settings-body,.panel-body,.pal-list,.dialog-body'), c = host ? host.getBoundingClientRect() : w;
      const L = Math.max(r.left, c.left, w.left), T = Math.max(r.top, c.top, w.top), R = Math.min(r.right, c.right, w.right), B = Math.min(r.bottom, c.bottom, w.bottom);
      ok = r.width > 4 && r.height > 4 && R - L > r.width * 0.4 && B - T > Math.min(r.height * 0.4, 60);
      r = { left: L, top: T, width: R - L, height: B - T };
    }
    el.classList.toggle('on', ok);
    if (!ok) return;
    const p = m.pad == null ? 6 : m.pad;
    el.style.left = (r.left - o.left) / Z - p + 'px';
    el.style.top = (r.top - o.top) / Z - p + 'px';
    el.style.width = r.width / Z + p * 2 + 'px';
    el.style.height = r.height / Z + p * 2 + 'px';
  });
  placeOver();
  requestAnimationFrame(trackRings);
}

function ringView() {
  for (const x of ringList) {
    const r = rectOf(x.m), box = r && r.el.closest(".bd-lanes");
    if (!box) continue;
    const b = box.getBoundingClientRect();
    let dx = 0;
    if (r.right > b.right) dx = (r.right - b.right) / Z + 16;
    else if (r.left < b.left) dx = -((b.left - r.left) / Z + 16);
    if (dx) box.scrollBy({ left: dx, behavior: RM ? "auto" : "smooth" });
    return;
  }
}
