/* cards: the few stops that are a statement, not a screen. They sit over the live interface. */
const KEYS = '<div class="tx-c-foot"><span><kbd>→</kbd>開始</span><span><kbd>Ctrl</kbd><kbd>K</kbd>目錄</span><span><kbd>N</kbd>講稿</span></div>';
const CARDS = {
  cover: '<div class="tx-c-kick">Pilcrow 3.1.0</div><h1>打造 Claude Code 工作台</h1><p class="tx-c-sub">從 Command Line 到 GUI，到多 agent 協作</p>' +
    '<div class="tx-c-steps"><span>Command Line</span><i></i><span>GUI</span><i></i><span>多 agent 協作</span></div>' + KEYS,
  path: '<div class="tx-c-kick">背景與動機</div><h2>命令列介面的兩項限制</h2><div class="tx-c-cols">' +
    '<div><b>01</b><h3>命令列介面</h3><p>Claude Code 功能完整，惟命令列介面存在兩項限制：長對話不易回溯；多個 session 並行時，各自的狀態難以掌握。</p></div>' +
    '<div><b>02</b><h3>圖形介面</h3><p>以圖形介面處理瀏覽問題，將對話、變更與檔案整合於單一視窗。</p></div>' +
    '<div><b>03</b><h3>工作台</h3><p>以工作台處理並行管理問題，涵蓋多個 agent 的交付、等待與審核。</p></div></div>',
  principle: '<div class="tx-c-kick">設計原則</div><h2>Pilcrow 定位為 Claude Code 的介面，<br>而非獨立的 agent</h2>' +
    '<div class="tx-c-chips"><span>工具</span><span>權限</span><span>CLAUDE.md</span><span>Skills</span><span>MCP</span></div>' +
    '<p class="tx-c-line">透過官方 SDK 驅動 Claude Code，不變更其行為；命令列環境中的既有設定，於 Pilcrow 中同樣適用。</p>',
  'how-outcome': '<div class="tx-c-kick">開發方法 · 1</div><h2>以預期結果界定任務，而非指定實作方式</h2><div class="tx-c-vs">' +
    '<div class="tx-c-q dim"><span>指定做法</span><blockquote>新增一個看板</blockquote><p>產出：一個看板</p></div>' +
    '<div class="tx-c-q"><span>描述結果</span><blockquote>每日上午於十分鐘內，處理完畢 agent 前一晚留下的所有事項</blockquote><p>產出：五欄看板、審核關卡、交接報告</p></div></div>',
  'how-bar': '<div class="tx-c-kick">開發方法 · 2</div><h2>設定具挑戰性且可量測的標準</h2><div class="tx-c-nums">' +
    '<div><b>&lt; 1 秒</b><span>開啟 400 輪的對話</span></div><div><b>7 ms</b><span>單次重繪</span></div><div><b>5%</b><span>閒置時的 CPU 使用率</span></div><div><b>逐像素</b><span>Qt 視窗與網頁版一致</span></div></div>' +
    '<p class="tx-c-line">達標與否，由測試、diff 與截圖比對判定。</p>',
  'how-borrow': '<div class="tx-c-kick">開發方法 · 3</div><h2>流程問題優先參考其他領域的既有解法</h2><div class="tx-c-rows">' +
    '<div><span>豐田生產方式</span><i></i><b>同時執行的工作上限</b></div><div><span>GTD</span><i></i><b>Waiting 欄位</b></div><div><span>臨床交班</span><i></i><b>固定格式的交接報告</b></div></div>',
  end: '<div class="tx-c-kick">Pilcrow 3.1.0</div><h1>Q &amp; A</h1><p class="tx-c-sub">安裝方式：Linux 解壓縮 tar，Windows 解壓縮 zip，即可啟動</p>',
};
let cardNow = '', cardT = 0;
function setCard(id) {
  const c = q('#tx-card');
  if (id === cardNow) return;
  cardNow = id;
  clearTimeout(cardT);
  if (!id) {
    if (RM) { c.className = ''; c.innerHTML = ''; return; }
    c.classList.add('out');
    cardT = setTimeout(() => { c.className = ''; c.innerHTML = ''; }, 360);
    return;
  }
  c.className = 'on';
  c.innerHTML = '<div class="tx-c tx-c-' + id + '">' + CARDS[id] + '</div>';
}
function mapOf(kind) {
  if (!kind) return '';
  const on = (k) => (kind === k || kind === 'all' || (kind === 'page' && k !== 'side') ? ' class="on"' : '');
  return '<span class="tx-map ' + kind + '"><i' + on('side') + '></i><i' + on('main') + '></i><i' + on('panel') + '></i></span>';
}
function groupCard(g) {
  const part = PARTS[g.part], sibs = GROUPS.filter((x) => x.part === g.part), me = sibs.indexOf(g);
  return '<div class="tx-c-kick">' + part.n + ' · ' + part.t + '</div>' +
    '<div class="tx-c-trail">' + sibs.map((x, i) => '<span class="' + (i === me ? 'cur' : i < me ? 'past' : '') + '">' + x.t + '</span>').join('<i></i>') + '</div>' +
    '<div class="tx-c-head"><div><h2>' + g.t + '</h2><p class="tx-c-sub">' + g.sub + '</p></div>' +
    (g.map ? '<div class="tx-c-where">' + mapOf(g.map) + '<span>位置<b>' + g.where + '</b></span></div>' : '') + '</div>' +
    '<ol class="tx-c-list' + (g.stops.length > 4 ? ' two' : '') + '">' + g.stops.map((id, i) => { const s = STOPS[IDX[id]]; return '<li><b>' + two(i + 1) + '</b><span>' + s.t + '</span><em>' + s.say + '</em></li>'; }).join('') + '</ol>' + (GREF[g.id] ? refLine(GREF[g.id]) : '');
}
function agendaCard() {
  return '<div class="tx-c-kick">報告大綱</div><h2>本報告分為三個部分</h2><div class="tx-c-cols agenda">' + PARTS.map((p, i) =>
    '<div><b>' + p.n + '</b><h3>' + p.t + '</h3><p>' + p.sub + '</p><ul>' + GROUPS.filter((g) => g.part === i).map((g) => '<li>' + g.t + '<em>' + g.stops.length + ' 項</em></li>').join('') + '</ul></div>').join('') + '</div>';
}
/* sources: what the memory design and the working method are drawn from */
const REFS = {
  ace: ['ACE', 'Agentic Context Engineering', 'Zhang et al.，ICLR 2026，arXiv:2510.04618'],
  amb: ['文章', 'How to be more ambitious: A tutorial for humans with models', 'Felix Rieseberg，2026'],
};
const refText = (k) => '<em>' + REFS[k][1] + '</em><span>' + REFS[k][2] + '</span>';
const refLine = (k) => '<div class="tx-c-ref"><b>參考</b>' + refText(k) + '</div>';
const GREF = { 'g-mem': 'ace', 'g-how': 'amb' };
CARDS['how-outcome'] += refLine('amb');
CARDS['how-bar'] += refLine('amb');
CARDS['how-borrow'] += refLine('amb');
CARDS.end += '<ul class="tx-c-refs"><li class="h">參考資料</li>' + ['ace', 'amb'].map((k) => '<li><b>' + REFS[k][0] + '</b><span>' + refText(k) + '</span></li>').join('') + '</ul>';
SPEC.memory.cite = 'ace';
SPEC['memory-conflict'].cite = 'ace';
function setCite(k) {
  const c = q('#tx-cite');
  if (c.getAttribute('data-k') === k) return;
  c.setAttribute('data-k', k);
  c.className = k ? 'on' : '';
  c.innerHTML = k ? '<b>參考</b>' + refText(k) : '';
}
GROUPS.forEach((g) => { CARDS[g.id] = groupCard(g); });
CARDS.agenda = agendaCard();
