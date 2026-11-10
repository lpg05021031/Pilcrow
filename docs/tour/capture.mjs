// Captures the frames the interactive tour replays (docs/tour). It drives the built interface (web/dist)
// over the mock server (test/mock) through one prepared story, and at each state saves the markup of the
// app, of whatever is portalled to <body> (dialogs, menus), and a screenshot for checking by eye.
//   node docs/tour/capture.mjs [outDir] [theme]      default: test/out/tour, dark
// The theme only decides which values are baked into inline styles; the tour itself can switch themes.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(join(ROOT, 'package.json'));
const { chromium } = require('playwright');
const OUT = process.argv[2] || join(ROOT, 'test', 'out', 'tour');
const THEME = process.argv[3] || 'dark';
const W = 1280;
const H = 720;
const PORT = 5347;
const URL_ = `http://127.0.0.1:${PORT}/`;
const PROJ = '/home/tester/code/pilcrow';
mkdirSync(OUT, { recursive: true });

// A small sign-in page for the Preview tab.
const PREVIEW_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>Acme · Sign in</title><style>
body{margin:0;font:15px/1.5 Inter,system-ui,sans-serif;background:#f4f5f7;color:#16181d;display:flex;flex-direction:column;min-height:100vh}
header{display:flex;align-items:center;gap:10px;padding:14px 22px;background:#fff;border-bottom:1px solid #e3e5ea;font-weight:600}
header i{width:22px;height:22px;border-radius:6px;background:#2f5bd8;display:inline-block}
main{flex:1;display:flex;align-items:center;justify-content:center}
form{width:300px;background:#fff;border:1px solid #e3e5ea;border-radius:12px;padding:26px 26px 22px;box-shadow:0 6px 24px rgba(20,24,40,.06)}
h1{font-size:20px;margin:0 0 4px}p{margin:0 0 18px;color:#5b6270;font-size:13px}
label{display:block;font-size:12px;font-weight:600;margin:12px 0 5px;color:#3a4050}
input{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #cfd3db;border-radius:8px;font:inherit}
button{width:100%;margin-top:18px;padding:10px;border:0;border-radius:8px;background:#2f5bd8;color:#fff;font:inherit;font-weight:600}
small{display:block;margin-top:14px;color:#7a8190;font-size:12px;text-align:center}
</style></head><body><header><i></i>Acme Console</header><main><form><h1>Sign in</h1><p>You will return to <b>/reports/weekly</b> after signing in.</p>
<label>Email</label><input value="tester@example.com"><label>Password</label><input type="password" value="........">
<button type="button">Sign in</button><small>next=/reports/weekly</small></form></main></body></html>`;
const preview = createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(PREVIEW_HTML);
});
let previewPort = 3000;
await new Promise((ok) => {
  preview.once('error', () => {
    previewPort = 4173;
    preview.listen(previewPort, '127.0.0.1', ok);
  });
  preview.listen(previewPort, '127.0.0.1', ok);
});

const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '60' }, stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((ok, bad) => {
  mock.stdout.on('data', () => {});
  const t = setInterval(async () => {
    try {
      if ((await fetch(`${URL_}api/hello`)).ok) {
        clearInterval(t);
        ok();
      }
    } catch {}
  }, 100);
  mock.on('exit', (c) => bad(new Error(`mock server exited (${c})`)));
});
function findBrowser() {
  try {
    if (existsSync(chromium.executablePath())) return {};
  } catch {}
  const local = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'];
  const p = local.find((x) => existsSync(x));
  return p ? { executablePath: p } : {};
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch({ headless: true, args: ['--lang=en-US'], ...findBrowser() });
const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, colorScheme: THEME, reducedMotion: 'reduce', locale: 'en-US' });
await context.addInitScript((t) => localStorage.setItem('pilcrow.theme', t), THEME);
const page = await context.newPage();
page.on('pageerror', (e) => console.error('   page error:', e.message));

/** One request to the mock over its WebSocket; resolves with the result. */
const rpc = (m, p = {}) =>
  page.evaluate(
    ([m, p]) =>
      new Promise((ok) => {
        const ws = new WebSocket(`ws://${location.host}/ws`);
        const id = 900000 + Math.floor(Math.random() * 90000);
        const done = (v) => {
          try {
            ws.close();
          } catch {}
          ok(v);
        };
        ws.onopen = () => ws.send(JSON.stringify({ t: 'req', id, m, p }));
        ws.onmessage = (e) => {
          const d = JSON.parse(e.data);
          if (d.t === 'res' && d.id === id) done(d.ok ? d.r : { error: d.e });
        };
        setTimeout(() => done({ error: 'timeout' }), 5000);
      }),
    [m, p],
  );
const answer = (method, result) => rpc('mock.answer', { method, result });

async function open(sid) {
  await page.click(`[data-sid="${sid}"] .side-row-main`);
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0 || document.querySelector('.session-empty'), null, { timeout: 15000 });
  await sleep(700);
}
async function send(text) {
  const ta = page.locator('.composer-input');
  await ta.click();
  await ta.fill(text);
  await ta.press('Enter');
}
const turnDone = (timeout = 30000) => page.waitForFunction(() => !document.querySelector('.session-head .status-pill') && !document.querySelector('.work.live'), null, { timeout });
const key = async (k, wait = 500) => {
  await page.keyboard.press(k);
  await sleep(wait);
};
const step = async (name, fn) => {
  try {
    await fn();
  } catch (e) {
    console.error(`!! ${name}: ${String(e.message).split('\n')[0]}`);
  }
};
// ---------------------------------------------------------------- the story the tour tells, played on the real interface
const D = String.fromCharCode(36), BT = String.fromCharCode(96);
const CODE = '/home/tester/code';
const CON = CODE + '/console';
const MODEL = 'claude-opus-5-5';
const now = Date.now();
const MIN = 60e3;
const ICONS = {};
const FRAMES = [];
const PARTS = {};
const save = () => {
  writeFileSync(join(OUT, 'frames.json'), JSON.stringify(FRAMES));
  writeFileSync(join(OUT, 'icons.json'), JSON.stringify(ICONS));
  writeFileSync(join(OUT, 'ids.json'), JSON.stringify(ID));
  writeFileSync(join(OUT, 'parts.json'), JSON.stringify(PARTS));
};

// ---- frames also carry what is portalled to <body> (dialogs, menus) and, on request, live form values
async function grab(name, opts = {}) {
  await page.screenshot({ path: join(OUT, name + '.png') });
  const r = await page.evaluate((vals) => {
    const el = document.querySelector('.app');
    if (!el) return null;
    const icons = {};
    const hash = (s) => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(36); };
    const take = (node, withVals) => {
      const c = node.cloneNode(true);
      if (withVals) {
        const src = node.querySelectorAll('input,textarea,select'), dst = c.querySelectorAll('input,textarea,select');
        src.forEach((s, i) => {
          const d = dst[i];
          if (s.tagName === 'TEXTAREA') d.textContent = s.value;
          else if (s.tagName === 'SELECT') [...d.options].forEach((o, j) => { if (j === s.selectedIndex) o.setAttribute('selected', ''); else o.removeAttribute('selected'); });
          else if (s.type === 'checkbox' || s.type === 'radio') { if (s.checked) d.setAttribute('checked', ''); else d.removeAttribute('checked'); }
          else d.setAttribute('value', s.value);
        });
      }
      const kl = node.querySelectorAll('.panel-keep'), kc = c.querySelectorAll('.panel-keep');
      kl.forEach((k, i) => { if (k.offsetParent === null && kc[i]) kc[i].innerHTML = ''; });
      c.querySelectorAll('svg').forEach((s) => {
        const at = {};
        [...s.attributes].forEach((a) => { if (!['class', 'width', 'height', 'style', 'aria-hidden'].includes(a.name)) at[a.name] = a.value; });
        const k = 'i' + hash(JSON.stringify(at) + s.innerHTML);
        icons[k] = { at, inner: s.innerHTML };
        s.innerHTML = '';
        Object.keys(at).forEach((n) => s.removeAttribute(n));
        s.setAttribute('data-ic', k);
      });
      return c.outerHTML;
    };
    const overs = [...document.querySelectorAll('body > .scrim, body > .popover')].filter((x) => !x.classList.contains('leaving'));
    const canv = el.querySelectorAll('canvas').length, ifr = el.querySelectorAll('iframe').length;
    return { h: take(el, vals), over: overs.map((x) => take(x, true)).join(''), icons, canv, ifr, kids: [...document.body.children].map((x) => x.tagName + '.' + x.className) };
  }, !!opts.vals);
  if (!r) { console.log('   no .app for ' + name); return; }
  Object.assign(ICONS, r.icons);
  FRAMES.push({ name, html: r.h, over: r.over });
  writeFileSync(join(OUT, name + '.html'), (r.h + '\n<!--OVER-->\n' + r.over).replace(/></g, '>\n<'));
  console.log('frame ' + name + ' ' + r.h.length + (r.over ? ' over=' + r.over.length : '') + (r.canv ? ' canvas=' + r.canv : '') + (r.ifr ? ' iframe=' + r.ifr : ''));
}
// ---- transcript message shapes (the SDK's)
let mi = 0;
const USAGE = { input_tokens: 1200, output_tokens: 80, cache_read_input_tokens: 20000 };
const U = (text) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] }, parent_tool_use_id: null, _local: true });
const A = (content) => ({ type: 'assistant', message: { id: 'msg_demo' + ++mi, role: 'assistant', model: MODEL, content, usage: USAGE }, parent_tool_use_id: null });
const T = (text) => A([{ type: 'text', text }]);
const TU = (id, name, input) => A([{ type: 'tool_use', id, name, input }]);
const TR = (id, content, structured) => ({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: false }] }, parent_tool_use_id: null, tool_use_result: structured });
const RES = (text, ms, cost) => ({ type: 'result', subtype: 'success', duration_ms: ms, duration_api_ms: ms, is_error: false, num_turns: 1, result: text, total_cost_usd: cost, usage: { ...USAGE, output_tokens: 640 }, modelUsage: { [MODEL]: { inputTokens: 1200, outputTokens: 640, contextWindow: 200000 } } });

// ---- the story's content (the demo's approved wording)
const TXT = {
  title: '修正登入後的導向',
  u1: '修正登入後被導向 /undefined 的問題',
  a1: '我先確認登入表單如何傳遞 ' + BT + 'next' + BT + ' 參數，再修正伺服器端的導向。',
  u2: '完成後請執行測試',
  a2: '執行測試指令前，需要先取得核准。',
  a3: '完成。登入表單現在會帶上 ' + BT + 'next' + BT + '；缺少時伺服器導回 ' + BT + '/' + BT + '，148 項測試全數通過。',
  cmd: 'npm test -- auth',
};
const HO = { asked: '修正登入後被導向 /undefined 的問題。', did: '表單以隱藏欄位帶上 next；缺少時伺服器導回 /。', unverified: '僅執行單元測試，尚未實際登入驗證。', ask: '是否不經人工登入驗證就提交？' };
const HANDOFF_MD = '\n\n## Handoff\n\n- **Asked**: ' + HO.asked + '\n- **Did**: ' + HO.did + '\n- **Unverified**: ' + HO.unverified + '\n- **Decide**: ' + HO.ask;
const F_AUTH = CON + '/web/src/lib/auth.ts';
const F_LOGIN = CON + '/server/routes/login.js';
const AUTH_OLD = '  return ' + BT + '/login?next=' + D + '{next}' + BT + ';';
const AUTH_NEW = ['  const to = next ?? location.pathname;', '  const q = new URLSearchParams({ next: to });', '  return ' + BT + '/login?' + D + '{q}' + BT + ';'];
const LOGIN_NEW = ['  // 缺少 next 時導回首頁', "  if (!next) return res.redirect('/');"];
const PATCH_AUTH = [{ oldStart: 41, oldLines: 3, newStart: 41, newLines: 5, lines: [' export function loginUrl(next?: string) {', '-' + AUTH_OLD, ...AUTH_NEW.map((l) => '+' + l), ' }'] }];
const PATCH_LOGIN = [{ oldStart: 18, oldLines: 2, newStart: 18, newLines: 4, lines: ['   const next = req.body.next;', ...LOGIN_NEW.map((l) => '+' + l), '   res.redirect(next);'] }];
const hunkLines = (p) => { let o = p.oldStart, n = p.newStart; return p.lines.map((l) => (l[0] === '-' ? { t: '-', s: l.slice(1), o: o++ } : l[0] === '+' ? { t: '+', s: l.slice(1), n: n++ } : { t: ' ', s: l.slice(1), o: o++, n: n++ })); };
const gfile = (path, add, del, p, header) => ({ path, oldPath: path, status: 'M', binary: false, additions: add, deletions: del, hunks: [{ oldStart: p.oldStart, oldLines: p.oldLines, newStart: p.newStart, newLines: p.newLines, header, lines: hunkLines(p) }], truncated: false });
const GIT_DIFF = (n) => ({ isRepo: true, root: CON, base: 'HEAD', baseRef: 'HEAD', branch: 'fix-login-redirect', files: [gfile('server/routes/login.js', 2, 0, PATCH_LOGIN[0], "router.post('/login', async (req, res) => {"), gfile('web/src/lib/auth.ts', 3, 1, PATCH_AUTH[0], '')].slice(2 - n), truncated: 0, error: null });
const GIT_INFO = (add, del, changed) => ({ isRepo: true, root: CON, branch: 'fix-login-redirect', head: '4e5f13e0a1b2', upstream: 'origin/fix-login-redirect', ahead: 0, behind: 0, changed, untracked: 0, isWorktree: false, mainRoot: CON, insertions: add, deletions: del });
const TEST_OUT = ['> console@2.4.0 test', '> vitest run auth', '', ' ✓ test/unit/auth.test.ts (62 tests) 148ms', ' ✓ test/unit/login-route.test.ts (51 tests) 203ms', ' ✓ test/unit/session.test.ts (35 tests) 96ms', '', ' Test Files  3 passed (3)', '      Tests  148 passed (148)', '   Duration  3.12s'].join('\n');

let CONF = [];
// ---- what the side panel, settings and memory show
const ent = (dir, name, type, size = 0) => ({ name, path: dir + '/' + name, type, size });
const ROOT_ENTRIES = [ent(CON, 'docs', 'dir'), ent(CON, 'server', 'dir'), ent(CON, 'test', 'dir'), ent(CON, 'web', 'dir'), ent(CON, '.gitignore', 'file', 212), ent(CON, 'README.md', 'file', 4180), ent(CON, 'package.json', 'file', 1320), ent(CON, 'tsconfig.json', 'file', 540)];
const SERVER_ENTRIES = [ent(CON + '/server', 'routes', 'dir'), ent(CON + '/server', 'auth.js', 'file', 2210), ent(CON + '/server', 'main.js', 'file', 3105), ent(CON + '/server', 'util.js', 'file', 1480)];
const ROUTES_ENTRIES = [ent(CON + '/server/routes', 'index.js', 'file', 640), ent(CON + '/server/routes', 'login.js', 'file', 812), ent(CON + '/server/routes', 'reports.js', 'file', 1904)];
const LOGIN_JS = ["import { Router } from 'express';", "import { verify } from '../auth.js';", '', 'const router = Router();', '', "router.get('/login', (req, res) => {", "  res.render('login', { next: req.query.next || '/' });", '});', '',
  "router.post('/login', async (req, res) => {", '  const { email, password } = req.body;', '  const user = await verify(email, password);', '  if (!user) {', "    return res.status(401).render('login', { error: 'Wrong email or password.' });", '  }',
  '  req.session.userId = user.id;', '  const next = req.body.next;', ...LOGIN_NEW, '  res.redirect(next);', '});', '', "router.post('/logout', (req, res) => {", "  req.session.destroy(() => res.redirect('/login'));", '});', '', 'export default router;', ''].join('\n');
const STEP_DIFF = { files: [gfile('web/src/lib/auth.ts', 3, 1, PATCH_AUTH[0], '')] };
const tstep = (n, minsAgo, kind, label, extra = {}) => ({ n, at: now - minsAgo * MIN, kind, tool: null, toolUseId: null, uuid: null, label, commit: 'c0ffee' + n, changed: false, files: [], add: 0, del: 0, ...extra });
const TIMELINE = { cwd: CON, enabled: true, reason: null, steps: [
  tstep(0, 9, 'start', 'Session started'),
  tstep(1, 8, 'message', TXT.u1),
  tstep(2, 7, 'tool', 'web/src/lib/auth.ts', { tool: 'Edit', changed: true, files: [{ path: 'web/src/lib/auth.ts', status: 'M', add: 3, del: 1 }], add: 3, del: 1 }),
  tstep(3, 6, 'tool', 'server/routes/login.js', { tool: 'Edit', changed: true, files: [{ path: 'server/routes/login.js', status: 'M', add: 2, del: 0 }], add: 2, del: 0 }),
  tstep(4, 5, 'message', TXT.u2),
  tstep(5, 4, 'tool', TXT.cmd, { tool: 'Bash' }),
] };
const E = String.fromCharCode(27);
const PS1 = E + '[1;32mtester@mock' + E + '[0m:' + E + '[1;34m~/code/console' + E + '[0m' + D + ' ';
const okLine = (f, n, ms) => ' ' + E + '[32m✓' + E + '[0m ' + f + ' ' + E + '[2m(' + n + ' tests) ' + ms + 'ms' + E + '[0m';
const TERM = [PS1 + TXT.cmd, '', '> console@2.4.0 test', '> vitest run auth', '', okLine('test/unit/auth.test.ts', 62, 148), okLine('test/unit/login-route.test.ts', 51, 203), okLine('test/unit/session.test.ts', 35, 96), '',
  ' ' + E + '[2m Test Files ' + E + '[0m ' + E + '[1;32m3 passed' + E + '[0m (3)', ' ' + E + '[2m      Tests ' + E + '[0m ' + E + '[1;32m148 passed' + E + '[0m (148)', ' ' + E + '[2m   Duration ' + E + '[0m 3.12s', '',
  PS1 + 'git status --short', ' ' + E + '[31mM' + E + '[0m server/routes/login.js', ' ' + E + '[31mM' + E + '[0m web/src/lib/auth.ts', PS1].join('\r\n');
const TODOS = [
  { content: '重現導向至 /undefined 的問題', status: 'completed', activeForm: '重現問題' },
  { content: '登入表單帶上 next 參數', status: 'completed', activeForm: '修改登入表單' },
  { content: '缺少 next 時由伺服器導回首頁', status: 'completed', activeForm: '加入伺服器端的預設導向' },
  { content: '執行單元測試', status: 'in_progress', activeForm: '執行單元測試' },
  { content: '補上登入流程的端對端檢查', status: 'pending', activeForm: '補上端對端檢查' },
];
const TASKS = [
  { id: 'bg-0', description: 'npm run build', type: 'local_bash', background: true, status: 'completed', startedAt: now - 6 * MIN, endedAt: now - 5.9 * MIN, summary: 'Built in 6.1 s' },
  { id: 'bg-1', description: 'npm run test:e2e', type: 'local_bash', background: true, status: 'running', startedAt: now - 48e3 },
  { id: 'ag-1', description: '檢查登入修改是否造成回歸', type: 'local_agent', subagentType: 'code-reviewer', status: 'running', startedAt: now - 95e3, lastTool: 'Read', progress: 'Reading server/routes/login.js', usage: { total_tokens: 18400, tool_uses: 7, duration_ms: 95000 } },
];
const DAY = 24 * 60 * MIN;
const note = (id, daysAgo, text, extra = {}) => ({ id, at: now - daysAgo * DAY, text, kind: 'auto', source: 'turn', sessionId: ID.login, sessionTitle: TXT.title, pinned: false, helpful: 0, harmful: 0, handed: 0, standing: 'fresh', disputed: null, ...extra });
const MEMORY = () => ({
  notes: [
    note('n-test', 12, '測試請從 repository 根目錄執行 npm test；web 測試需要 5311 埠的 mock server。', { kind: 'manual', source: 'hand', pinned: true, helpful: 6, handed: 9, standing: 'confirmed', sessionTitle: '建立測試環境' }),
    note('n-next', 1, '登入後的導向讀取 ?next=；缺少此參數時一律導回 /。', { helpful: 3, handed: 4, standing: 'confirmed' }),
    note('n-legacy', 5, '不要修改 server/routes/legacy/*：遷移完成前，此資料夾維持凍結。', { source: 'reject', helpful: 2, handed: 3, sessionTitle: '整理路由表' }),
    note('n-cents', 3, '金額以分為單位儲存，僅在顯示時除以 100。', { helpful: 1, handed: 2, sessionTitle: '修正購物車總額' }),
    note('n-ci', 9, 'CI 工作的名稱是 build-and-test。', { harmful: 2, handed: 5, standing: 'retired', sessionTitle: '縮短 CI 時間' }),
  ],
  stats: { withNotes: { approve: 14, reject: 2 }, without: { approve: 9, reject: 5 } },
});
const CONFLICT = () => ({ id: 'note-cents', cwd: CON, text: '購物車 API 的金額單位是元，顯示時保留兩位小數。', standing: 'fresh', at: now - 2 * 60 * MIN,
  correction: '不對，API 回傳的單位已經是分，這樣會重複換算。', sessionId: ID.flag, sessionTitle: '修正購物車總額', replacement: { id: 'note-cents-2', text: '金額以分為單位儲存，僅在顯示時除以 100。' } });
const SK = '/home/tester/.claude/skills';
const skill = (name, description, scope, files, hasScripts) => ({ name, dirName: name, description, scope, dir: SK + '/' + name, file: SK + '/' + name + '/SKILL.md', mtime: now - 3 * DAY, files, hasScripts, body: '# ' + name + '\n\n' + description + '\n' });
const SKILLS = {
  roots: { user: SK, project: CON + '/.claude/skills' },
  user: [skill('release-notes', '依上一個 tag 之後的 commit 撰寫 release notes，並依範圍分組。', 'user', 2, false), skill('pdf-report', '由指標 CSV 填寫並匯出每週 PDF 報告。', 'user', 5, true)],
  project: [skill('db-migration', '撰寫可回復的 migration，於測試資料庫執行後更新 schema 說明。', 'project', 3, true), skill('e2e-check', '對本機伺服器執行端對端測試，並彙整失敗項目。', 'project', 2, true)],
  plugin: [],
};
const MCP = { file: '/home/tester/.claude.json', servers: [
  { name: 'github', scope: 'user', source: '/home/tester/.claude.json', config: { type: 'http', url: 'https://api.githubcopilot.com/mcp/' }, raw: {} },
  { name: 'postgres-dev', scope: 'project', source: CON + '/.mcp.json', config: { type: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-postgres', 'postgresql://localhost/console_dev'] }, raw: {}, approved: true },
  { name: 'sentry', scope: 'project', source: CON + '/.mcp.json', config: { type: 'sse', url: 'https://mcp.sentry.dev/sse' }, raw: {}, approved: null },
] };
const panelAnswers = async (info) => {
  await answer('app.info', { ...info, displayName: '', user: '', terminal: { python: '/usr/bin/python3', script: '/usr/bin/script', pty: true } });
  await answer('memory.list', MEMORY());
  await answer('timeline.steps', TIMELINE);
  await answer('timeline.stepDiff', STEP_DIFF);
  await answer('timeline.diff', STEP_DIFF);
  await answer('fs.list', { dir: CON, entries: ROOT_ENTRIES });
  await answer('fs.read', { path: CON + '/server/routes/login.js', size: LOGIN_JS.length, mtime: now - 6 * MIN, text: LOGIN_JS });
  await answer('term.open', { id: 'term-1', title: 'bash' });
  await answer('term.snapshot', { buffer: TERM });
  await answer('term.resize', true);
  await answer('term.input', true);
  await answer('term.close', true);
  await answer('skills.list', SKILLS);
  await answer('mcp.list', MCP);
};
const tabTo = async (name, wait = 900) => { await page.click('.settings-nav button:text-is("' + name + '")'); await sleep(wait); };
// ---- the seven cards: real sessions in the mock, their list rows laid over
const ID = {};
const base = {};
const ov = {};
const live = {};
const shown = [];
const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
const LIST = () => shown.map((k) => ({ ...base[ID[k]], ...ov[k] })).sort((a, b) => b.lastModified - a.lastModified);
const FLEET = () => {
  const l = {};
  for (const k of shown) if (live[k]) l[ID[k]] = live[k];
  const per = shown.filter((k) => ov[k].costUsd > 0).map((k) => ({ id: ID[k], title: ov[k].title, cost: ov[k].costUsd })).sort((a, b) => b.cost - a.cost);
  const done = shown.filter((k) => (ov[k].decisions || []).at(-1)?.kind === 'approve').length;
  return { live: l, today: { cost: per.reduce((a, b) => a + b.cost, 0), sessions: per.length, done, perSession: per }, spend: per.map((p) => ({ at: now - 10 * MIN, id: p.id, delta: p.cost })), limit: { max: 3, running: Object.keys(l).filter((i) => l[i].status === 'running').length }, scheduled: [], conflicts: CONF, caps: { ci: true } };
};
const push = async (wait = 350) => {
  const list = LIST();
  const fleet = FLEET();
  await answer('sessions.list', list);
  await answer('sessions.refresh', list);
  await answer('fleet.state', fleet);
  await answer('board.state', fleet);
  await rpc('mock.touch', { id: ID.login });
  await sleep(wait);
};
const runState = (k, activity, mins, cost) => ({ id: ID[k], status: 'running', live: true, cwd: ov[k].cwd, mode: 'default', model: '', effort: '', activeModel: MODEL, usage: { contextTokens: 48200, contextWindow: 200000, costUsd: cost, outputTokens: 900, model: MODEL }, error: null, activity: { ...activity, at: now - 5000 }, lastActivity: now - 5000, turnStartedAt: now - mins * MIN, processStartedAt: now - mins * MIN, pending: [], tasks: [], bgTasks: [], queued: [], held: [] });

const mk = async (key, cwd, title, intent, over = {}) => {
  const r = await rpc('session.create', { cwd, title, intent });
  ID[key] = r.id;
  ov[key] = { title, cwd, project: cwd, intent, firstPrompt: intent, createdAt: now - 300 * MIN, ...over };
};
const msgs = async (...m) => { await rpc('mock.messages', { id: ID.login, msgs: m }); await sleep(320); };
const patch = async (state) => { await rpc('mock.patch', { id: ID.login, state }); await sleep(160); };
const act = (tool, label) => ({ kind: 'tool', tool, label, at: Date.now() });
const STOP_AT = process.argv[4] || 'zzz';
const stage = (n) => { if (n > STOP_AT) throw new Error('stop at ' + STOP_AT); console.log('-- stage ' + n); };
try {
  await page.goto(URL_);
  await rpc('config.set', { theme: THEME });
  const info = await rpc('app.info');
  await answer('app.info', { ...info, displayName: '', user: '' });
  await answer('models.gateway', { ok: true, base: 'https://gateway.example.com', url: 'https://gateway.example.com/v1/models', models: [{ id: MODEL, displayName: 'Claude Opus 5.5', created: null }, { id: 'claude-sonnet-5-5', displayName: 'Claude Sonnet 5.5', created: null }], fetchedAt: now });
  const tDone = Math.max(dayStart.getTime() + MIN, now - 70 * MIN);
  await mk('docs', CODE + '/docs', '更新 API 文件範例', '更新 API 文件中的請求與回應範例。', { gitBranch: 'main', lastModified: now - 190 * MIN });
  await mk('flag', CON, '整理過期的 feature flag', '移除已全面啟用的 feature flag 與相關分支。', { gitBranch: 'main', lastModified: now - 150 * MIN });
  await mk('pay', CODE + '/billing', '重構付款模組的錯誤處理', '統一付款模組的錯誤型別與重試策略。', { gitBranch: 'pay-errors', status: 'running', live: true, lastModified: now - 2 * MIN, costUsd: 0.31 });
  await mk('parser', CODE + '/core', '補齊 parser 的單元測試', '為 parser 的邊界情況補上單元測試。', { gitBranch: 'parser-tests', status: 'running', live: true, lastModified: now - 1 * MIN, costUsd: 0.12 });
  await mk('ci', CON, '升級 Node 22 後重跑整合測試', '升級至 Node 22，並在 CI 通過後重跑整合測試。', { gitBranch: 'node-22', lastModified: now - 40 * MIN, turnEnd: now - 40 * MIN, reviewedTurn: now - 40 * MIN, decisions: [{ at: now - 39 * MIN, kind: 'wait', turn: now - 40 * MIN }], wait: { kind: 'ci', cwd: CON, branch: 'node-22', since: now - 39 * MIN }, costUsd: 0.22 });
  await mk('home', CON, '調整首頁載入順序', '調整首頁資源的載入順序，縮短首次繪製時間。', { gitBranch: 'home-order', lastModified: tDone, turnEnd: tDone - MIN, reviewedTurn: tDone - MIN, decisions: [{ at: tDone, kind: 'approve', commit: 'a41c9e27d0b3', turn: tDone - MIN }], costUsd: 0.18 });
  await mk('login', CON, TXT.title, TXT.u1, { gitBranch: 'fix-login-redirect', lastModified: now, createdAt: now });
  for (const s of await rpc('sessions.list')) base[s.id] = s;
  live.pay = runState('pay', { kind: 'tool', tool: 'Edit', label: 'server/pay/errors.js' }, 12, 0.31);
  live.parser = runState('parser', { kind: 'tool', tool: 'Bash', label: 'npm test' }, 4, 0.12);
  shown.push('docs', 'flag', 'pay', 'parser', 'ci', 'home');
  await push(0);
  await answer('git.info', GIT_INFO(0, 0, 0));
  await answer('git.diff', GIT_DIFF(0));
  await panelAnswers(info);
  await page.reload();
  await page.waitForSelector('[data-sid]', { timeout: 15000 });
  await sleep(1200);
  stage('a');
  const chip = page.locator('.folder-chip', { hasText: 'console' });
  if (await chip.count()) { await chip.first().click(); await sleep(300); }
  await grab('f00-welcome');
  await page.fill('.welcome-composer textarea', TXT.u1); await sleep(350); await grab('f00-typed'); await page.fill('.welcome-composer textarea', ''); await sleep(250);
  await step('side-menu', async () => {
    await page.hover('[data-sid="' + ID.flag + '"]');
    await page.click('[data-sid="' + ID.flag + '"] .icon-btn');
    await sleep(500);
    await grab('f02-sidemenu');
    await key('Escape', 400);
  });
  await step('new-session', async () => {
    await key('Control+Shift+N', 900);
    await page.locator('.scrim .folder-chip', { hasText: 'console' }).first().click();
    await sleep(300);
    await page.click('.ns-disclose');
    await sleep(600);
    await grab('f03-newsession');
    await key('Escape', 500);
  });
  await page.mouse.move(700, 80);
  await sleep(300);


  stage('b');
  shown.push('login');
  await push(500);
  await open(ID.login);
  await grab('f01-empty');
  await msgs(U(TXT.u1));
  await patch({ status: 'running', live: true, activeModel: MODEL, model: '', turnStartedAt: Date.now(), usage: { contextTokens: 21280, contextWindow: 200000, costUsd: 0, outputTokens: 0, model: MODEL } });
  await sleep(400);
  await grab('f10-sent');
  await msgs(T(TXT.a1));
  await grab('f11-a1');
  const readOut = (n) => ({ numLines: n, startLine: 1, totalLines: n });
  const tools = [
    ['toolu_r1', 'Read', { file_path: F_AUTH }, 'export function loginUrl(next?: string) {', { type: 'text', file: { filePath: F_AUTH, content: '', ...readOut(86) } }, 'web/src/lib/auth.ts'],
    ['toolu_r2', 'Read', { file_path: F_LOGIN }, 'const next = req.body.next;', { type: 'text', file: { filePath: F_LOGIN, content: '', ...readOut(41) } }, 'server/routes/login.js'],
    ['toolu_e1', 'Edit', { file_path: F_AUTH, old_string: AUTH_OLD, new_string: AUTH_NEW.join('\n') }, 'The file has been updated.', { filePath: F_AUTH, oldString: AUTH_OLD, newString: AUTH_NEW.join('\n'), originalFile: '', structuredPatch: PATCH_AUTH, userModified: false, replaceAll: false }, 'web/src/lib/auth.ts'],
    ['toolu_e2', 'Edit', { file_path: F_LOGIN, old_string: '  res.redirect(next);', new_string: LOGIN_NEW.join('\n') + '\n  res.redirect(next);' }, 'The file has been updated.', { filePath: F_LOGIN, oldString: '  res.redirect(next);', newString: LOGIN_NEW.join('\n') + '\n  res.redirect(next);', originalFile: '', structuredPatch: PATCH_LOGIN, userModified: false, replaceAll: false }, 'server/routes/login.js'],
  ];
  for (let i = 0; i < tools.length; i++) {
    const [id, name, input, out, structured, label] = tools[i];
    await patch({ activity: act(name, label) });
    await msgs(TU(id, name, input));
    await grab('f12-t' + i + 'a');
    if (i === 2) { await answer('git.info', GIT_INFO(3, 1, 1)); await answer('git.diff', GIT_DIFF(1)); }
    if (i === 3) { await answer('git.info', GIT_INFO(5, 1, 2)); await answer('git.diff', GIT_DIFF(2)); }
    await msgs(TR(id, out, structured));
    await sleep(300);
    await grab('f12-t' + i + 'b');
  }
  await patch({ status: "idle" }); await sleep(1800); await patch({ status: "running", activity: null }); await sleep(500); await grab("f12-stat");
  await page.fill('.composer-wrap textarea', TXT.u2); await sleep(350); await grab('f12-typed'); await page.fill('.composer-wrap textarea', ''); await sleep(250);
  await patch({ activity: null, held: [{ uuid: 'held-demo-1', text: TXT.u2, images: 0, files: [], at: Date.now(), note: null, from: null }] });
  await sleep(300);
  await grab('f13-queued');
  const part = async (name, sel, nth = 0) => {
    const r = await page.evaluate(([sel, nth]) => {
      const el = document.querySelectorAll(sel)[nth];
      if (!el) return null;
      const c = el.cloneNode(true);
      const icons = {};
      const hash = (s) => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(36); };
      [c, ...c.querySelectorAll('svg')].filter((s) => s.tagName.toLowerCase() === 'svg').forEach((s) => {
        const at = {};
        [...s.attributes].forEach((a) => { if (!['class', 'width', 'height', 'style', 'aria-hidden'].includes(a.name)) at[a.name] = a.value; });
        const k = 'i' + hash(JSON.stringify(at) + s.innerHTML);
        icons[k] = { at, inner: s.innerHTML };
        s.innerHTML = '';
        Object.keys(at).forEach((n) => s.removeAttribute(n));
        s.setAttribute('data-ic', k);
      });
      return { h: c.outerHTML, icons };
    }, [sel, nth]);
    if (!r) { console.log('   no part ' + name); return; }
    Object.assign(ICONS, r.icons);
    PARTS[name] = r.h;
    console.log('part ' + name + ' ' + r.h.length);
  };
  const tog = async (sel, nth = 0, wait = 450) => { await page.locator(sel).nth(nth).click(); await sleep(wait); };

  stage('c');
  await page.locator('.work-head').first().click();
  await sleep(500);
  await grab('f20-open');
  await page.locator('#tool-toolu_e1 .tool-toggle').click();
  await sleep(500);
  await grab('f21-diff');
  await page.locator('#tool-toolu_e1 .tool-toggle').click();
  await sleep(250);
  await page.locator('.work-head').first().click();
  await sleep(400);

  stage('d');
  await patch({ held: [] });
  await msgs(U(TXT.u2));
  await grab('f30-u2');
  await msgs(T(TXT.a2));
  await grab('f31-a2');
  const REQ = { requestId: 'req-demo-1', toolName: 'Bash', input: { command: TXT.cmd, description: 'Run the auth tests' }, toolUseId: 'toolu_b1', title: 'Run the auth tests', description: TXT.cmd,
    suggestions: [{ type: 'addRules', behavior: 'allow', destination: 'localSettings', rules: [{ toolName: 'Bash', ruleContent: 'npm test:*' }] }], interactive: false, at: Date.now() };
  await msgs(TU('toolu_b1', 'Bash', { command: TXT.cmd, description: 'Run the auth tests' }));
  await patch({ status: 'requires_action', pending: [REQ], activity: null });
  ov.login = { ...ov.login, status: 'requires_action', pending: 1 };
  await push(1900);
  await grab('f32-perm');
  await patch({ status: 'running', pending: [], activity: act('Bash', TXT.cmd) });
  ov.login = { ...ov.login, status: 'running', pending: 0 };
  await push(500);
  await grab('f33-run');
  await msgs(TR('toolu_b1', TEST_OUT, { stdout: TEST_OUT, stderr: '', interrupted: false, isImage: false }));
  await patch({ activity: null });
  await sleep(300);
  await grab('f34-ran');

  stage('e');
  await msgs(T(TXT.a3 + HANDOFF_MD));
  await grab('f40-a3');
  const tEnd = Date.now();
  await msgs(RES(TXT.a3, 42000, 0.27));
  await patch({ status: 'idle', activity: null, usage: { contextTokens: 24900, contextWindow: 200000, costUsd: 0.27, outputTokens: 640, model: MODEL } });
  ov.login = { ...ov.login, status: 'idle', live: true, pending: 0, turnEnd: tEnd, lastModified: tEnd, costUsd: 0.27,
    report: { asked: HO.asked, did: HO.did, unverified: HO.unverified, ask: HO.ask, turn: tEnd, files: [{ path: 'web/src/lib/auth.ts', add: 3, del: 1 }, { path: 'server/routes/login.js', add: 2, del: 0 }] } };
  await push(1900);
  await grab('f41-done');

  await step('modes', async () => {
    await page.click('.cbtn.mode');
    await sleep(600);
    await grab('f42-modes');
    await key('Escape', 400);
  });
  await step('find', async () => {
    await key('Control+f', 500);
    await page.locator('.findbar input').fill('next');
    await sleep(600);
    await grab('f43-find', { vals: true });
    await key('Escape', 400);
  });
  // open and closed forms of every fold, for the demo's own clicks
  await part('w0:closed', '.unit.work', 0);
  await part('w1:closed', '.unit.work', 1);
  await tog('.work-head', 0);
  await part('w0:open', '.unit.work', 0);
  for (const t of ['toolu_e1', 'toolu_e2']) {
    await part('tool-' + t + ':closed', '#tool-' + t);
    await tog('#tool-' + t + ' .tool-toggle');
    await part('tool-' + t + ':open', '#tool-' + t);
    await tog('#tool-' + t + ' .tool-toggle', 0, 300);
  }
  await grab('x-w0');
  await tog('.work-head', 0);
  await tog('.work-head', 1);
  await part('w1:open', '.unit.work', 1);
  await part('tool-toolu_b1:closed', '#tool-toolu_b1');
  await tog('#tool-toolu_b1 .tool-toggle');
  await part('tool-toolu_b1:open', '#tool-toolu_b1');
  await grab('x-w1');
  await tog('#tool-toolu_b1 .tool-toggle', 0, 300);
  await tog('.work-head', 1);

  stage('f');
  await key('Alt+1', 1100);
  await grab('f50-changes');
  await step('files', async () => {
    await key('Alt+2', 1200);
    await answer('fs.list', { dir: CON + '/server', entries: SERVER_ENTRIES });
    await page.locator('.tree-row', { hasText: 'server' }).first().click();
    await sleep(700);
    await answer('fs.list', { dir: CON + '/server/routes', entries: ROUTES_ENTRIES });
    await page.locator('.tree-row', { hasText: 'routes' }).first().click();
    await sleep(700);
    await page.locator('.tree-row', { hasText: 'login.js' }).first().click();
    await sleep(1500);
    await grab('f51-files');
    await answer('fs.list', { dir: CON, entries: ROOT_ENTRIES });
  });
  await step('terminal', async () => {
    await key('Alt+3', 2200);
    await grab('f52-terminal');
  });
  await step('preview', async () => {
    await key('Alt+4', 900);
    const bar = page.locator('.url-bar input');
    await bar.fill('http://localhost:' + previewPort + '/login?next=/reports/weekly');
    await bar.press('Enter');
    await sleep(2000);
    await grab('f53-preview', { vals: true });
  });
  await step('timeline', async () => {
    await key('Alt+6', 1300);
    await grab('f56-timeline0');
    await page.locator('.tl-row[data-step="2"] .tl-row-btn').click();
    await sleep(1200);
    await grab('f56-timeline');
  });
  await step('info', async () => {
    await key('Alt+7', 1200);
    await grab('f57-info');
  });
  await key('Alt+1', 700);
  await key('Alt+1', 700);

  await step('palette', async () => {
    await key('Control+k', 700);
    await grab('f44-palette');
    await key('Escape', 500);
  });
  await step('shortcuts', async () => {
    await key('Control+/', 700);
    await grab('f45-shortcuts');
    await key('Escape', 500);
  });
  await step('settings', async () => {
    await key('Control+,', 900);
    await tabTo('General');
    await grab('f90-settings', { vals: true });
    await tabTo('Repositories');
    await grab('f97-repos', { vals: true });
    await page.locator('.rp-row .rp-head .icon-btn').first().click();
    await sleep(700);
    await grab('f97-repo-open', { vals: true });
    await page.locator('.rp-row .rp-head .icon-btn').first().click();
    await sleep(300);
    await tabTo('Skills');
    await grab('f91-skills', { vals: true });
    await tabTo('MCP servers');
    await grab('f92-mcp', { vals: true });
    await tabTo('Diagnostics');
    await grab('f93-diag', { vals: true });
    await tabTo('Memory');
    await grab('f94-memory0', { vals: true });
    await page.locator('.settings-body, .settings-main, .settings-page').first().evaluate((el) => {
      let n = el;
      while (n && n.scrollHeight <= n.clientHeight + 4) n = n.parentElement;
      if (n) n.scrollTop = 260;
    });
    await sleep(500);
    await grab('f94-memory', { vals: true });
  });
  await key('Escape', 500);
  await step('tasks', async () => {
    await msgs(TU('toolu_todo_1', 'TodoWrite', { todos: TODOS }), TR('toolu_todo_1', 'Todos have been modified successfully.', { oldTodos: [], newTodos: TODOS }));
    await patch({ tasks: TASKS, bgTasks: [{ task_id: 'bg-1', task_type: 'local_bash', description: 'npm run test:e2e' }, { task_id: 'ag-1', task_type: 'local_agent', description: TASKS[2].description, subagent_type: 'code-reviewer' }] });
    await key('Alt+5', 1300);
    await grab('f55-tasks');
    await patch({ tasks: [], bgTasks: [] });
    await key('Alt+5', 700);
  });
  stage('g');
  await key('Control+Shift+O', 1100);
  await grab('f60-board');
  await step('board-new', async () => {
    await key('n', 500);
    await page.locator('.bd-new-title').fill('為登入路由加上嘗試次數限制');
    await page.locator('.bd-new-intent').fill('連續五次登入失敗後，鎖定帳號十分鐘。完成條件：路由測試涵蓋鎖定情境。');
    await sleep(400);
    await grab('f61-new', { vals: true });
    await key('Escape', 300);
    await key('Escape', 300);
  });
  await page.click('.bd-card[data-card="' + ID.login + '"]');
  await sleep(900);
  await grab('f70-review');
  await key('Escape', 500);
  const tOk = Date.now();
  ov.login = { ...ov.login, reviewedTurn: tEnd, decisions: [{ at: tOk, kind: 'approve', commit: 'c0ffee1d2e3f', turn: tEnd }], lastModified: tOk };
  await answer('git.info', GIT_INFO(0, 0, 0));
  await answer('git.diff', GIT_DIFF(0));
  await push(900);
  await grab('f71-approved');
  await page.click('.bd-card[data-card="' + ID.ci + '"]');
  await sleep(900);
  await grab('f80-wait');
  ov.ci = { ...ov.ci, wait: null, status: 'running', live: true, lastModified: Date.now(), decisions: [...ov.ci.decisions, { at: Date.now(), kind: 'wake', turn: ov.ci.turnEnd }] };
  live.ci = runState('ci', { kind: 'tool', tool: 'Bash', label: 'npm run test:integration' }, 0.1, 0.22);
  await push(900);
  await grab('f81-woke');
  await key('Escape', 500);
  await grab('f82-end');
  await step('schedule', async () => {
    await page.click('.bd-lane.wait .bd-lane-menu');
    await sleep(300);
    await grab('f83-lanemenu');
    await page.locator('text=New schedule').first().click();
    await sleep(700);
    const dlg = page.locator('[role="dialog"]').last();
    await dlg.locator('input[type="text"], input:not([type])').first().fill('每日相依套件稽核');
    await dlg.locator('textarea').first().fill('檢查相依套件的安全性公告與可用更新，彙整成一份報告。不要自行升級，也不要 push。');
    await sleep(300);
    await grab('f83-schedule');
    await key('Escape', 400);
  });
  await key('Escape', 300);
  await step('conflict', async () => {
    CONF = [CONFLICT()];
    await push(900);
    await grab('f95-note');
    await page.locator('.bd-card.note').first().click();
    await sleep(700);
    await grab('f96-conflict');
    await key('Escape', 300);
    CONF = [];
    await push(600);
  });
  await step('dispatch', async () => {
    await page.locator('.side-page', { hasText: 'Dispatch' }).click();
    await sleep(1200);
    await grab('f84-dispatch');
    await page.locator('.ovr.dp-fin').first().click();
    await sleep(1200);
    await grab('f84-dispatch2');
  });
  await step('css', async () => {
    const css = await page.evaluate(() => {
      const out = [];
      (document.adoptedStyleSheets || []).forEach((s) => { try { out.push([...s.cssRules].map((r) => r.cssText).join(String.fromCharCode(10))); } catch (e) {} });
      document.querySelectorAll('head style').forEach((s) => out.push(s.textContent));
      return out.join(String.fromCharCode(10));
    });
    writeFileSync(join(OUT, 'extra.css'), css);
    console.log('extra css ' + css.length);
  });
  const sync = async (wait = 700) => {
    await answer('sessions.list', null);
    await answer('fleet.state', null);
    const real = await rpc('sessions.list'), fl = await rpc('fleet.state');
    for (const s of real) {
      if (s.id === ID.lead || s.id === ID.wf) base[s.id] = s;
      if (s.helper && s.helper.leadId === ID.lead) { const k = 'h_' + s.helper.name; if (!ID[k]) { ID[k] = s.id; ov[k] = {}; shown.push(k); } base[s.id] = s; }
    }
    for (const k of ['lead', 'h_api', 'h_web', 'wf']) if (ID[k]) { if (fl.live && fl.live[ID[k]]) live[k] = fl.live[ID[k]]; else delete live[k]; }
    await push(wait);
  };
  await step('helpers', async () => {
    await mk('lead', CODE + '/shop', '訂單 API 與前端同步改版', '同步修改訂單 API 與前端的下單按鈕。', { gitBranch: 'main', lastModified: Date.now(), createdAt: Date.now() });
    shown.push('lead');
    await sync(600);
    await open(ID.lead);
    await rpc('mock.patch', { id: ID.lead, state: { activeModel: MODEL, model: '' } });
    await sleep(300);
    await grab('f85-lead-empty');
    await page.locator('.composer-input').click();
    await page.locator('.composer-input').fill('Start two helpers: one for the api and one for the web.');
    await sleep(350);
    await grab('f85-lead-typed');
    await page.locator('.composer-input').press('Enter');
    await page.waitForFunction(() => document.querySelectorAll('.helpers-bar .helper-chip').length === 2, null, { timeout: 20000 });
    await page.waitForSelector('.helper-chip.warm', { timeout: 30000 });
    await sleep(900);
    await sync(900);
    await grab('f85-helpers-live');
    for (let i = 0; i < 60; i++) {
      const hl = await rpc('helpers.list', { leadId: ID.lead });
      const hp = (hl.helpers || []).find((h) => h.pending);
      const st = hp ? await rpc('mock.state', { id: hp.id }) : null;
      const rq = st && st.pending && st.pending[0];
      if (rq) { console.log('   allow ' + rq.requestId); await rpc('helpers.allow', { leadId: ID.lead, requestId: rq.requestId }); break; }
      await sleep(500);
    }
    await page.waitForSelector('.msg-notes .session-card', { timeout: 60000 });
    await page.waitForFunction(() => document.querySelectorAll('.helper-chip .dp-glyph.done').length === 2, null, { timeout: 60000 });
    await sleep(900);
    await sync(900);
    await grab('f86-helpers');
    await page.click('.helpers-bar .btn:has-text("Review")');
    await page.waitForSelector('.hc .hc-sec', { timeout: 10000 });
    await sleep(900);
    await grab('f87-helpers-review');
  });
  await step('workflow', async () => {
    await mk('wf', CON, '以 workflow 審查登入修正', '以 workflow 審查登入修正的變更。', { gitBranch: 'fix-login-redirect', lastModified: Date.now(), createdAt: Date.now() });
    shown.push('wf');
    await sync(600);
    await open(ID.wf);
    await rpc('mock.patch', { id: ID.wf, state: { activeModel: MODEL, model: '' } });
    await key('Control+Backslash', 600);
    await grab('f88-wf-empty');
    await page.locator('.composer-input').click();
    await page.locator('.composer-input').fill('Use a workflow to review the login fix: look for bugs and performance issues, then verify each finding.');
    await sleep(350);
    await grab('f88-wf-typed');
    await page.locator('.composer-input').press('Enter');
    const unfold = async () => {
      if (await page.locator('.wfi').count()) return;
      if (!(await page.locator('.unit.work').last().locator('.tool-toggle').count())) { await page.locator('.work-head').last().click(); await sleep(450); }
      const t = page.locator('.unit.work').last().locator('.tool-toggle');
      if (await t.count()) { await t.first().click(); await sleep(450); }
    };
    await page.waitForSelector('.work-head', { timeout: 15000 });
    await sleep(400);
    await sync(200);
    await grab('f88-wf-live');
    await unfold();
    await page.waitForSelector('.wfi', { timeout: 8000 });
    await sleep(500);
    await grab('f88-wf-open');
    await page.locator('.wfi .btn.xs').first().click();
    await sleep(900);
    await grab('f88-wf-tasks');
    await turnDone(40000);
    await sleep(900);
    await unfold();
    await sync(500);
    await grab('f88-wf-done');
  });
} catch (e) {
  console.error('!! ' + String(e.message).split('\n')[0]);
} finally {
  save();
  await browser.close();
  mock.kill();
  preview.close();
}
