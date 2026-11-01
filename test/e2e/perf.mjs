#!/usr/bin/env node
// Performance budgets for the conversation view: the built interface, served by the mock server,
// measured in headless Chrome on generated conversations.
//
//   MOCK_PORT=5321 node test/e2e/perf.mjs [--headed] [--json] [--only name,name]   (npm run test:perf; needs a build)
//
// The scenarios, each with what it measures:
//   open400 / open2000  open a heavy conversation: modelled and first rows (the app's own performance.measure
//                       entries, store.ts and Transcript.tsx), long tasks, DOM nodes, heap
//   stream              a long turn streamed into the 2,000-turn one, the CPU slowed fourfold: frame intervals,
//                       long tasks, script time per second, how far behind the live end the view falls
//   find                Ctrl+F, then typing a six-letter word that matches all through the 2,000 turns: the
//                       longest task, the DOM nodes after, the time to the first match shown
//   history             from the end of the 2,000 turns, a fling to the top, then reading down with the wheel:
//                       frame intervals and long tasks while it moves, the pages drawn once it stops
//   switch              after reading far up the 2,000 turns, another conversation and back: the time to the
//                       place the reader left, and the longest task
//   fence / write       a 60 kB code fence, and a 60 kB Write with its card open, streamed in pieces (CPU ×4):
//                       frame intervals and long tasks
//   memory              ten 300-turn conversations opened one after another: the heap after a collection
//   code                a 60 kB TSX block coloured once on screen: no task over 50 ms
//
// Budgets. BASELINE holds the median of three runs on the machine that wrote it (the date is below). A
// budget is that times MARGIN, so a twofold regression is over it and the noise of three runs in a row
// here is under it. A figure whose own noise is larger than that is reported and not budgeted: the long
// tasks in all while the marathon and the code fence stream (318 to 998 ms, and 225 to 684 ms, over
// the three runs below); their frame intervals, longest task and script time per second are budgeted
// instead. A few figures sit near nothing, so their budget has a FLOOR and is the larger of the two.
// Two budgets are rules rather than measurements (RULES).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import WebSocket from 'ws';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const OUT = join(ROOT, 'test', 'out');
// Never the mock's default port: a developer's own mock server may be running there.
const PORT = Number(process.env.MOCK_PORT || 5321);
const URL_ = `http://127.0.0.1:${PORT}/`;
const args = process.argv.slice(2);
const headed = args.includes('--headed');
const only = args.includes('--only') ? args[args.indexOf('--only') + 1].split(',') : null;
const runs = (name) => !only || only.includes(name);

const MARGIN = 1.6;
// The median of three runs: headless Chrome, 1400×900, the development container, 2026-11-01.
const BASELINE = {
  open400: { modelled: 126, rows: 184, longMax: 95, nodes: 1070, heapMB: 12 },
  open2000: { modelled: 561, rows: 641, longMax: 163, nodes: 1995, heapMB: 25 },
  stream: { p95: 50, longMax: 81, scriptPerSec: 206 },
  find: { openMax: 0, typeMax: 0, nodes: 6186, firstMs: 6 },
  history: { p95: 33.3, longTotal: 0, nodes: 31433, drawn: 40 },
  switch: { ms: 122, longMax: 0 },
  fence: { p50: 16.7, p95: 50, scriptPerSec: 234, closing: 61 },
  write: { p50: 16.7, p95: 33.3, scriptPerSec: 245, longTotal: 52 },
  memory: { heapMB: 24 },
  code: { ms: 1125 },
};
// Where a baseline is near nothing a margin means nothing: a frame interval comes in steps of 16.7 ms,
// a long task is 50 ms or more by definition, and a GC pause can land on any step. These budgets are
// the floor instead, which is still under what the work measured before it was made cheap.
const FLOOR = { longMax: 120, openMax: 60, typeMax: 60, longTotal: 200, closing: 200, p95: 40, firstMs: 100 };
const RULES = {
  // JavaScript parsed before a conversation's first rows: the editor, the terminal, the highlighter and
  // the app pages load on first use. vite.config.mjs warns past the same figure.
  eagerKB: 720,
  // Coloured a slice at a time and painted a block per frame (lib/highlight, Markdown): no long task at all.
  codeLongMax: 50,
  codeMs: 15000,
};
const budget = (scenario, key) => Math.round(Math.max(BASELINE[scenario][key] * MARGIN, FLOOR[key] || 0) * 10) / 10;

if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) {
  console.error('web/dist is missing: run `npm run build` first');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '0' }, stdio: ['ignore', 'pipe', 'inherit'] });
mock.stdout.on('data', () => {});
process.on('exit', () => mock.kill()); // also when a step throws
await new Promise((ok, bad) => {
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
const ctl = await new Promise((ok, bad) => {
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws`);
  let id = 0;
  const pending = new Map();
  ws.on('message', (d) => {
    const m = JSON.parse(d.toString());
    if (m.t === 'res' && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      if (m.ok) p.ok(m.r);
      else p.bad(new Error(m.e?.message));
    }
  });
  ws.on('open', () => ok({ call: (m, p = {}) => new Promise((ok, bad) => (pending.set(++id, { ok, bad }), ws.send(JSON.stringify({ t: 'req', id, m, p })))), close: () => ws.close() }));
  ws.on('error', bad);
});

const chrome = ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find((p) => existsSync(p));
const executablePath = process.env.PILCROW_CHROME || (existsSync(chromium.executablePath()) ? undefined : chrome);
const browser = await chromium.launch({ headless: !headed, executablePath });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- pages, and what a failure leaves behind

/**
 * A page that records every long task from its first moment, and its console, for a failure's report.
 * It starts on no conversation: the window reopens the one it showed last, which would land an
 * earlier scenario's opening in this one's figures.
 */
async function freshPage() {
  await ctl.call('config.set', { ui: { lastSession: '' } });
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, reducedMotion: 'no-preference' });
  await context.addInitScript(() => {
    window.__long = [];
    new PerformanceObserver((l) => window.__long.push(...l.getEntries().map((e) => ({ at: e.startTime, ms: e.duration })))).observe({ type: 'longtask', buffered: true });
  });
  const page = await context.newPage();
  page.__console = [];
  page.on('console', (m) => page.__console.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => page.__console.push(`page error: ${e.message}`));
  await page.goto(URL_);
  await page.waitForSelector('.sidebar .side-row', { timeout: 20_000 });
  const cdp = await context.newCDPSession(page);
  return { context, page, cdp };
}

/** What the page looked like when a step failed: its console, a summary of the DOM, a screenshot. */
async function dump(page, scenario, error) {
  console.log(`  ✗ ${scenario}: ${error.message.split('\n')[0]}`);
  if (!page) return;
  const dom = await page
    .evaluate(() => {
      const t = document.querySelector('.transcript');
      return {
        url: location.href,
        active: document.querySelector('.side-row.active')?.getAttribute('data-sid') || null,
        rows: document.querySelectorAll('.sidebar [data-sid]').length,
        transcript: t ? { top: Math.round(t.scrollTop), height: t.scrollHeight, client: t.clientHeight } : null,
        pages: document.querySelectorAll('.page').length,
        far: document.querySelectorAll('.page.far').length,
        nodes: document.getElementsByTagName('*').length,
        dialogs: [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].map((d) => d.getAttribute('aria-label') || d.className),
        focus: document.activeElement?.className || document.activeElement?.tagName,
        faults: [...document.querySelectorAll('.render-fault')].map((f) => f.textContent?.slice(0, 120)),
      };
    })
    .catch((e) => ({ unavailable: e.message }));
  console.log(`    DOM: ${JSON.stringify(dom)}`);
  const lines = page.__console.slice(-25);
  console.log(`    console (last ${lines.length}):${lines.length ? '\n      ' + lines.join('\n      ') : ' empty'}`);
  const file = join(OUT, `perf-${scenario}.png`);
  await page.screenshot({ path: file }).then(() => console.log(`    screenshot: ${file}`), () => {});
}

/**
 * Open a conversation from the sidebar. The list can re-render under the pointer (a refresh after the
 * mock adds a session), so the row is looked up and clicked in one step, and again if the click did
 * not take.
 */
async function openSession(page, id, timeout = 30_000) {
  const end = Date.now() + timeout;
  const left = (cap) => Math.min(cap, Math.max(1000, end - Date.now()));
  while (Date.now() < end) {
    await page.waitForFunction((id) => document.querySelector(`[data-sid="${id}"] .side-row-main`), id, { timeout: left(timeout) });
    await page.evaluate((id) => document.querySelector(`[data-sid="${id}"] .side-row-main`)?.click(), id);
    const open = await page.waitForFunction((id) => document.querySelector(`.side-row.active[data-sid="${id}"]`) && document.querySelector('.session .transcript .units')?.children.length > 0, id, { timeout: left(10_000) }).then(
      () => true,
      () => false,
    );
    if (open) return;
  }
  throw new Error(`could not open ${id}`);
}

const longSince = (page, t0) =>
  page.evaluate((t0) => {
    const xs = window.__long.filter((e) => e.at >= t0).map((e) => e.ms);
    return { n: xs.length, max: Math.round(Math.max(0, ...xs)), total: Math.round(xs.reduce((a, b) => a + b, 0)) };
  }, t0);
const now = (page) => page.evaluate(() => performance.now());

async function heapMB(cdp) {
  await cdp.send('HeapProfiler.collectGarbage');
  const { usedSize } = await cdp.send('Runtime.getHeapUsage');
  return Math.round(usedSize / 1048576);
}

const nodes = (page) => page.evaluate(() => document.getElementsByTagName('*').length);
const drawn = (page) => page.evaluate(() => document.querySelectorAll('.page:not(.far)').length);
const fromBottom = (page) =>
  page.evaluate(() => {
    const el = document.querySelector('.transcript');
    return el ? Math.round(el.scrollHeight - el.scrollTop - el.clientHeight) : null;
  });

/** Frame intervals from now until stopFrames: requestAnimationFrame times, read without touching layout. */
const startFrames = (page) =>
  page.evaluate(() => {
    window.__frames = [];
    window.__stopFrames = false;
    const tick = (t) => {
      window.__frames.push(t);
      if (!window.__stopFrames) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return performance.now();
  });
const stopFrames = (page) =>
  page.evaluate(() => {
    window.__stopFrames = true;
    const t = window.__frames;
    const gaps = t
      .slice(1)
      .map((x, i) => x - t[i])
      .sort((a, b) => a - b);
    const q = (p) => gaps[Math.min(gaps.length - 1, Math.floor(gaps.length * p))] || 0;
    return { frames: t.length, ms: Math.round(t[t.length - 1] - t[0]), p50: +q(0.5).toFixed(1), p95: +q(0.95).toFixed(1), max: Math.round(gaps[gaps.length - 1] || 0) };
  });

/** Wait until the drawn pages and the scroll position hold still for half a second (at most 8 s). */
async function settle(page) {
  let last = '';
  let still = 0;
  for (let i = 0; i < 80 && still < 5; i++) {
    await sleep(100);
    const s = await page.evaluate(() => {
      const t = document.querySelector('.transcript');
      return `${document.querySelectorAll('.page:not(.far)').length}:${t?.scrollTop}:${t?.scrollHeight}`;
    });
    still = s === last ? still + 1 : 0;
    last = s;
  }
}

const METRICS = ['TaskDuration', 'ScriptDuration', 'RecalcStyleDuration', 'LayoutDuration'];
const metrics = async (cdp) => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.filter((m) => METRICS.includes(m.name)).map((m) => [m.name, m.value]));
/** Main-thread time between two metrics readings, as the (throttled) CPU spent it, per second. */
const perSecond = (m0, m1, ms) => {
  const per = (name) => Math.round(((m1[name] - m0[name]) * 1e6) / ms);
  return { taskPerSec: per('TaskDuration'), scriptPerSec: per('ScriptDuration'), stylePerSec: per('RecalcStyleDuration'), layoutPerSec: per('LayoutDuration') };
};

// ---------------------------------------------------------------- scenarios

/** Open a conversation from the sidebar and take its numbers once the view has settled. */
async function measureOpen(page, cdp, id) {
  const t0 = await now(page);
  await openSession(page, id);
  const wall = Math.round((await now(page)) - t0);
  await page.waitForFunction((id) => performance.getEntriesByType('measure').some((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id), id, { timeout: 30_000 });
  await settle(page); // the landing passes and the pages near the end being drawn
  const measures = await page.evaluate((id) => Object.fromEntries(performance.getEntriesByType('measure').filter((m) => m.detail?.id === id).map((m) => [m.name, Math.round(m.duration)])), id);
  // Up to the first rows of the first conversation shown.
  const eagerKB = await page.evaluate(() => {
    const end = Math.min(...performance.getEntriesByType('measure').filter((m) => m.name === 'pilcrow:first-rows').map((m) => m.startTime + m.duration));
    const js = performance.getEntriesByType('resource').filter((r) => /\.m?js(\?|$)/.test(r.name) && r.responseEnd <= end);
    return Math.round(js.reduce((n, r) => n + r.decodedBodySize, 0) / 1024);
  });
  return { eagerKB, wall, modelled: measures['pilcrow:modelled'], rows: measures['pilcrow:first-rows'], long: await longSince(page, t0), nodes: await nodes(page), heapMB: await heapMB(cdp), fromBottom: await fromBottom(page) };
}

/**
 * Stream a long turn (thinking, steps, a sub-agent, a long answer) while the view follows it. The CPU
 * is slowed fourfold for it, standing in for a slower machine (and WebKitGTK): on this one a frame
 * has room to spare, and per-frame costs that grow with the reply would not show.
 */
async function measureStream(page, cdp) {
  await ctl.call('mock.pace', { ms: 4 });
  await cdp.send('Performance.enable');
  const ta = page.locator('.composer-input');
  await ta.click();
  await ta.fill('run the marathon');
  const t0 = await startFrames(page);
  // How far behind the live end the view is, sampled apart from the frames: a layout read in the frame
  // callback would itself cost a layout per frame.
  await page.evaluate(() => {
    window.__follow = [];
    const sample = setInterval(() => {
      if (window.__stopFrames) return clearInterval(sample);
      const el = document.querySelector('.transcript');
      window.__follow.push(el ? el.scrollHeight - el.scrollTop - el.clientHeight : 0);
    }, 250);
  });
  const m0 = await metrics(cdp);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await ta.press('Enter');
  await page.waitForSelector('.session-head .status-pill', { timeout: 10_000 });
  await page.waitForFunction(() => !document.querySelector('.session-head .status-pill') && !document.querySelector('.work.live'), null, { timeout: 120_000 });
  const f = await stopFrames(page);
  const behindMax = await page.evaluate(() => Math.round(Math.max(0, ...window.__follow)));
  const per = perSecond(m0, await metrics(cdp), f.ms);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  await ctl.call('mock.pace', { ms: 0 });
  await page
    .waitForFunction(() => {
      const el = document.querySelector('.transcript');
      return el && el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    }, null, { timeout: 5000 })
    .catch(() => {}); // what is left is the figure checked
  return { ...f, ...per, behindMax, behind: await fromBottom(page), long: await longSince(page, t0), nodes: await nodes(page) };
}

/** Ctrl+F, then a word typed a key at a time that matches all through the conversation. */
async function measureFind(page) {
  await page.locator('.composer-input').click();
  const t0 = await now(page);
  await page.keyboard.press('Control+f');
  await page.waitForSelector('.findbar input', { timeout: 10_000 });
  await sleep(1500);
  const opened = await longSince(page, t0);
  const nodesOpen = await nodes(page);
  await page.locator('.findbar input').fill('');
  const t1 = await now(page);
  await page.keyboard.type('module', { delay: 120 });
  const typedAt = await now(page);
  await page.waitForFunction(() => /of \d+/.test(document.querySelector('.findbar-count')?.textContent || '') && CSS.highlights.has('find-current'), null, { timeout: 30_000 });
  const firstMs = Math.round((await now(page)) - typedAt);
  await sleep(1500);
  const typed = await longSince(page, t1);
  const count = await page.evaluate(() => document.querySelector('.findbar-count')?.textContent);
  const out = { openMax: opened.max, openTotal: opened.total, nodesOpen, typeMax: typed.max, typeTotal: typed.total, firstMs, count, nodes: await nodes(page), drawn: await drawn(page) };
  await page.keyboard.press('Escape');
  await page.waitForSelector('.findbar', { state: 'detached', timeout: 5000 });
  return out;
}

/** From the live end: a fling to the top (big wheel steps, no pause), then reading down (small steps, a pause each). */
async function measureHistory(page) {
  const box = await page.locator('.transcript').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const t0 = await startFrames(page);
  for (let i = 0; i < 80 && (await page.evaluate(() => document.querySelector('.transcript').scrollTop)) > 50; i++) {
    await page.mouse.wheel(0, -6000);
    await sleep(16);
  }
  for (let i = 0; i < 30; i++) {
    await page.mouse.wheel(0, 300);
    await sleep(60);
  }
  const f = await stopFrames(page);
  const long = await longSince(page, t0);
  await settle(page);
  return { ...f, long, nodes: await nodes(page), drawn: await drawn(page), pages: await page.locator('.page').count() };
}

/** The first of the person's messages in view, and how far it is from the top of the view. */
const placeOf = (page) =>
  page.evaluate(() => {
    const view = document.querySelector('.transcript').getBoundingClientRect();
    const el = [...document.querySelectorAll('.transcript .msg-user-text')].find((e) => e.getBoundingClientRect().top >= view.top);
    return { text: el?.textContent || '', top: Math.round((el?.getBoundingClientRect().top || 0) - view.top) };
  });

/** Read far up the conversation (pages drawn as the wheel goes), then another conversation and back. */
async function measureSwitch(page, id, other) {
  await page.locator('.jump').click().catch(() => {}); // from the live end
  await settle(page);
  const box = await page.locator('.transcript').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < 150; i++) {
    await page.mouse.wheel(0, -700);
    await sleep(40);
  }
  await settle(page);
  const before = await placeOf(page);
  const drawnBefore = await drawn(page);
  await openSession(page, other);
  await settle(page);
  const t0 = await now(page);
  await openSession(page, id);
  // Back: the person's message at the place left is on screen.
  await page.waitForFunction(
    (text) =>
      [...document.querySelectorAll('.transcript .msg-user-text')].some((e) => {
        const r = e.getBoundingClientRect();
        const v = document.querySelector('.transcript').getBoundingClientRect();
        return e.textContent === text && r.height > 0 && r.top >= v.top - 1 && r.bottom <= v.bottom;
      }),
    before.text,
    { timeout: 15_000 },
  );
  const ms = Math.round((await now(page)) - t0);
  await settle(page);
  const after = await placeOf(page);
  return { ms, long: await longSince(page, t0), drawnBefore, drawnAfter: await drawn(page), moved: before.text === after.text ? Math.abs(after.top - before.top) : null };
}

const tsx = (...names) => names.map((f) => readFileSync(join(ROOT, 'web', 'src', 'components', `${f}.tsx`), 'utf8')).join('');
const sixty = () => {
  const code = tsx('Transcript', 'Composer', 'Sidebar', 'Overview');
  return code.slice(0, code.lastIndexOf('\n', 60_000));
};
const ev = (event) => ({ type: 'stream_event', parent_tool_use_id: null, event });
const pieces = (s, n) => Array.from({ length: Math.ceil(s.length / n) }, (_, i) => s.slice(i * n, (i + 1) * n));

/** Send messages to a conversation as the server would stream them, one batch every `every` ms. */
async function feed(sid, batches, every = 16) {
  for (const msgs of batches) {
    await ctl.call('mock.messages', { id: sid, msgs });
    await sleep(every);
  }
}

/** A reply that is one 60 kB code fence, streamed 200 characters at a time (CPU ×4); it ends as the SDK ends it, with the whole message. */
async function measureFence(page, cdp, sid) {
  await warmTsx(page, sid);
  const code = sixty();
  const text = '```tsx\n' + code + '\n```';
  const msgId = 'msg_fence';
  await ctl.call('mock.messages', { id: sid, msgs: [{ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'show me the code' }] } }] });
  await page.waitForFunction(() => [...document.querySelectorAll('.msg-user-text')].some((e) => e.textContent === 'show me the code'), null, { timeout: 5000 });
  await settle(page); // the question lands first; the reply is what is measured
  await cdp.send('Performance.enable');
  const m0 = await metrics(cdp);
  const t0 = await startFrames(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await feed(sid, [[ev({ type: 'message_start', message: { id: msgId } }), ev({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } })], ...pieces(text, 200).map((p) => [ev({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: p } })])]);
  // While it streams, apart from the moment it ends: the closed block is drawn whole once, then coloured.
  const f = await stopFrames(page);
  const per = perSecond(m0, await metrics(cdp), f.ms);
  const long = await longSince(page, t0);
  const t1 = await now(page);
  await feed(sid, [[ev({ type: 'content_block_stop', index: 0 }), ev({ type: 'message_stop' }), { type: 'assistant', message: { id: msgId, role: 'assistant', content: [{ type: 'text', text }] } }]]);
  await page.waitForFunction((n) => [...document.querySelectorAll('.md:not(.streaming) pre')].some((p) => p.textContent.length >= n), code.length - 1, { timeout: 60_000 });
  await sleep(500);
  const closing = await longSince(page, t1);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  return { ...f, ...per, kB: Math.round(text.length / 1024), long, closing };
}

/**
 * Compile the TSX grammar before a streaming scenario, with a block shown first: Shiki compiles a
 * grammar's patterns the first time each is needed, a one-off per window (measureCode reports it),
 * which would otherwise land on whatever is coloured first.
 */
async function warmTsx(page, sid) {
  const warm = tsx('Markdown', 'RightPanel', 'SessionView', 'Inbox');
  await ctl.call('mock.messages', { id: sid, msgs: codeTurn(warm) });
  await page.waitForFunction((n) => [...document.querySelectorAll('pre[data-hl="1"]')].some((p) => p.textContent.length >= n), warm.length - 1, { timeout: RULES.codeMs });
  await settle(page); // the last coloured block is laid out in the frame after it is marked done
}

/** A 60 kB Write streamed as its input arrives (CPU ×4), its fold and card open. */
async function measureWrite(page, cdp, sid) {
  await warmTsx(page, sid);
  const input = JSON.stringify({ file_path: '/home/me/pilcrow/web/src/components/Big.tsx', content: sixty() });
  const msgId = 'msg_write';
  const id = 'toolu_write_perf';
  const delta = (p) => [ev({ type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: p } })];
  const parts = pieces(input, 200);
  await feed(sid, [[ev({ type: 'message_start', message: { id: msgId } }), ev({ type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id, name: 'Write', input: {} } })], ...parts.slice(0, 5).map(delta)]);
  await page.waitForFunction(() => document.querySelectorAll('.work-head').length > 0, null, { timeout: 5000 });
  await page.locator('.work-head').last().click();
  await page.waitForSelector('.tool[data-tool="Write"].open .code-view', { timeout: 5000 });
  await settle(page); // the fold and the card open first; the rest of the input is what is measured
  const t0 = await startFrames(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await cdp.send('Performance.enable');
  const m0 = await metrics(cdp);
  await feed(sid, parts.slice(5).map(delta));
  await feed(sid, [[ev({ type: 'content_block_stop', index: 0 }), ev({ type: 'message_stop' }), { type: 'assistant', message: { id: msgId, role: 'assistant', content: [{ type: 'tool_use', id, name: 'Write', input: JSON.parse(input) }] } }]]);
  await sleep(2000); // the card coloured once the input is complete
  const f = await stopFrames(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  return { ...f, ...perSecond(m0, await metrics(cdp), f.ms), kB: Math.round(input.length / 1024), long: await longSince(page, t0) };
}

/** Conversations opened one after another: what stays in memory (store.ts keeps the last few modelled). */
async function measureMemory(page, cdp, ids) {
  for (const id of ids) {
    await ctl.call('mock.touch', { id }); // the sidebar lists a folder's most recent rows
    await openSession(page, id);
    await settle(page);
  }
  return { sessions: ids.length, heapMB: await heapMB(cdp) };
}

const codeTurn = (code) => [{ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'show me the code' }] } }, { type: 'assistant', message: { id: `msg_${code.length}`, role: 'assistant', content: [{ type: 'text', text: 'Here it is:\n\n```tsx\n' + code + '\n```' }] } }];

/** Open a conversation and wait until its last code block is coloured; the long tasks from its first rows on. */
async function colour(page, id, minLength) {
  await openSession(page, id);
  await page.waitForFunction((id) => performance.getEntriesByType('measure').some((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id), id, { timeout: 30_000 });
  // From the commit of the first rows on: the render that put the block on the page is not the highlighter's.
  const t0 = await page.evaluate((id) => {
    const m = performance.getEntriesByType('measure').find((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id);
    return m.startTime + m.duration;
  }, id);
  await page.waitForFunction((n) => [...document.querySelectorAll('pre[data-hl="1"]')].some((p) => p.textContent.length >= n), minLength, { timeout: RULES.codeMs });
  return { ms: Math.round((await now(page)) - t0), long: await longSince(page, t0) };
}

/**
 * A 60 kB TSX block (this app's own components) in a reply: how it is coloured once it is on screen.
 * The window first shows other TSX (48 kB of other components): Shiki compiles a grammar's patterns
 * the first time each is needed, 50–350 ms at a time here, a one-off per pattern and window that
 * slicing cannot split. That first block is reported apart, without a budget.
 */
async function measureCode(page, warm, code) {
  const cold = await colour(page, 'perf-warm', warm.length - 1);
  const out = await colour(page, 'perf-code', code.length - 1);
  Object.assign(
    out,
    await page.evaluate((lines) => {
      const pre = [...document.querySelectorAll('pre[data-hl="1"]')].at(-1);
      return { lines, coloured: pre.querySelectorAll('.line').length, styled: pre.querySelectorAll('.line span[style*="--shiki"]').length };
    }, code.split('\n').length),
  );
  return { ...out, kB: Math.round(code.length / 1024), cold: { kB: Math.round(warm.length / 1024), ...cold } };
}

// ---------------------------------------------------------------- the run

const failures = [];
const check = (what, value, max) => {
  if (value == null || value > max) failures.push(`${what}: ${value} (budget ${max})`);
};
const results = {};
let current = null;

/** One scenario; a failure prints the page's state and counts against the run, and the run goes on. */
async function scenario(name, fn) {
  if (!runs(name)) return;
  try {
    await fn();
  } catch (e) {
    failures.push(`${name}: ${e.message.split('\n')[0]}`);
    await dump(current, name, e);
  }
}

/** A page of its own for a scenario, closed after it whatever happens. */
async function onPage(fn) {
  const p = await freshPage();
  current = p.page;
  try {
    return await fn(p);
  } finally {
    await p.context.close().catch(() => {});
  }
}

console.log('Pilcrow performance (headless Chrome, heavy generated conversations)');
const gen = {};
for (const turns of [400, 2000]) gen[turns] = await ctl.call('mock.generate', { id: `perf-${turns}`, turns, shape: 'heavy' });

/**
 * Opening is measured three times, each in a window of its own, and the median taken: one open is a
 * few hundred milliseconds of work, and one run's figure moves by half again from run to run here.
 */
async function openThrice(id, keep) {
  const outs = [];
  for (let i = 0; i < 3; i++) {
    const p = await freshPage();
    current = p.page;
    outs.push(await measureOpen(p.page, p.cdp, id));
    if (i < 2 || !keep) await p.context.close();
    else await keep(p);
  }
  const mid = (f) => outs.map(f).sort((a, b) => a - b)[1];
  return { ...outs[2], modelled: mid((o) => o.modelled), rows: mid((o) => o.rows), wall: mid((o) => o.wall), longMax: mid((o) => o.long.max), longTotal: mid((o) => o.long.total), nodes: mid((o) => o.nodes), heapMB: mid((o) => o.heapMB), fromBottom: Math.max(...outs.map((o) => o.fromBottom)) };
}

await scenario('open400', async () => reportOpen('open400', 400, await openThrice('perf-400')));

function reportOpen(name, turns, out) {
  results[name] = out;
  const b = (k) => budget(name, k);
  console.log(`  open ${turns} turns (${gen[turns].messages} messages, ${(gen[turns].bytes / 1048576).toFixed(1)} MB): ${out.eagerKB} kB of JS before the first rows · modelled ${out.modelled} ms · first rows ${out.rows} ms (wall ${out.wall} ms) · longest task ${out.longMax} ms, long tasks in all ${out.longTotal} ms · ${out.nodes} nodes · heap ${out.heapMB} MB (medians of three)`);
  check(`${name}: JavaScript loaded before the first rows (kB)`, out.eagerKB, RULES.eagerKB);
  check(`${name}: open → modelled (ms)`, out.modelled, b('modelled'));
  check(`${name}: open → first rows (ms)`, out.rows, b('rows'));
  check(`${name}: longest task while opening (ms)`, out.longMax, b('longMax'));
  check(`${name}: DOM nodes`, out.nodes, b('nodes'));
  check(`${name}: heap (MB)`, out.heapMB, b('heapMB'));
  check(`${name}: distance from the live end after opening (px)`, out.fromBottom, 60);
}

// The 2,000-turn conversation, one window for all its scenarios after the opening, in the order a session goes.
let big = null;
await scenario('open2000', async () => reportOpen('open2000', 2000, await openThrice('perf-2000', (p) => (big = p))));
if (['stream', 'find', 'history', 'switch'].some(runs))
  await (async () => {
    if (!big) {
      big = await freshPage();
      await openSession(big.page, 'perf-2000');
    }
    const { page, cdp } = big;
    current = page;
    await scenario('stream', async () => {
      const s = await measureStream(page, cdp);
      results.stream = s;
      const b = (k) => budget('stream', k);
      console.log(`  stream into 2000 turns: ${s.frames} frames in ${s.ms} ms · frame p50 ${s.p50} ms, p95 ${s.p95} ms, max ${s.max} ms · long tasks ${s.long.n}, max ${s.long.max} ms, total ${s.long.total} ms · main thread ${s.taskPerSec} ms/s (script ${s.scriptPerSec}, style ${s.stylePerSec}, layout ${s.layoutPerSec}) · ${s.nodes} nodes · at most ${s.behindMax}px behind, ${s.behind}px at the end`);
      check('stream: frame interval p95 (ms)', s.p95, b('p95'));
      check('stream: longest task (ms)', s.long.max, b('longMax'));
      check('stream: script time per second of streaming (ms)', s.scriptPerSec, b('scriptPerSec'));
      check('stream: distance from the live end once it ends (px)', s.behind, 60);
    });
    await scenario('find', async () => {
      const f = await measureFind(page);
      results.find = f;
      const b = (k) => budget('find', k);
      console.log(`  find in 2000 turns: Ctrl+F longest task ${f.openMax} ms (${f.nodesOpen} nodes) · typing "module" longest task ${f.typeMax} ms, total ${f.typeTotal} ms · "${f.count}" shown ${f.firstMs} ms after the last key · ${f.nodes} nodes, ${f.drawn} pages drawn`);
      check('find: longest task opening the bar (ms)', f.openMax, b('openMax'));
      check('find: longest task while typing (ms)', f.typeMax, b('typeMax'));
      check('find: DOM nodes after typing', f.nodes, b('nodes'));
      check('find: the first match shown after the last key (ms)', f.firstMs, b('firstMs'));
    });
    await scenario('history', async () => {
      const h = await measureHistory(page);
      results.history = h;
      const b = (k) => budget('history', k);
      console.log(`  history of 2000 turns, a fling to the top and reading down: frame p50 ${h.p50} ms, p95 ${h.p95} ms, max ${h.max} ms · long tasks ${h.long.n}, max ${h.long.max} ms, total ${h.long.total} ms · ${h.drawn} of ${h.pages} pages drawn, ${h.nodes} nodes`);
      check('history: frame interval p95 (ms)', h.p95, b('p95'));
      check('history: long tasks in total (ms)', h.long.total, b('longTotal'));
      check('history: DOM nodes once it stops', h.nodes, b('nodes'));
      check('history: pages drawn once it stops', h.drawn, b('drawn'));
    });
    await scenario('switch', async () => {
      const s = await measureSwitch(page, 'perf-2000', 'perf-400');
      results.switch = s;
      const b = (k) => budget('switch', k);
      console.log(`  back to 2000 turns after reading far up (${s.drawnBefore} pages drawn) and another conversation: the place back in ${s.ms} ms · longest task ${s.long.max} ms · ${s.drawnAfter} pages drawn · the place moved ${s.moved}px`);
      check('switch: back to the place left (ms)', s.ms, b('ms'));
      check('switch: longest task (ms)', s.long.max, b('longMax'));
      check('switch: how far the place moved (px)', s.moved, 80);
    });
    await big.context.close();
  })();

await scenario('fence', async () => {
  await ctl.call('mock.generate', { id: 'perf-small', turns: 4, shape: 'mixed' });
  await onPage(async ({ page, cdp }) => {
    await openSession(page, 'perf-small');
    const f = await measureFence(page, cdp, 'perf-small');
    results.fence = f;
    const b = (k) => budget('fence', k);
    console.log(`  a ${f.kB} kB code fence streamed (CPU ×4): ${f.frames} frames · frame p50 ${f.p50} ms, p95 ${f.p95} ms, max ${f.max} ms · long tasks ${f.long.n}, max ${f.long.max} ms, total ${f.long.total} ms · main thread ${f.taskPerSec} ms/s (script ${f.scriptPerSec}, style ${f.stylePerSec}, layout ${f.layoutPerSec}) · once it closes, long tasks ${f.closing.total} ms in all`);
    check('fence: frame interval p50 (ms)', f.p50, b('p50'));
    check('fence: frame interval p95 (ms)', f.p95, b('p95'));
    check('fence: script time per second (ms)', f.scriptPerSec, b('scriptPerSec'));
    check('fence: long tasks once it closes (ms)', f.closing.total, b('closing'));
  });
});

await scenario('write', async () => {
  await ctl.call('mock.generate', { id: 'perf-small', turns: 4, shape: 'mixed' });
  await onPage(async ({ page, cdp }) => {
    await openSession(page, 'perf-small');
    const w = await measureWrite(page, cdp, 'perf-small');
    results.write = w;
    const b = (k) => budget('write', k);
    console.log(`  a ${w.kB} kB Write streamed, its card open (CPU ×4): ${w.frames} frames · frame p50 ${w.p50} ms, p95 ${w.p95} ms, max ${w.max} ms · long tasks ${w.long.n}, max ${w.long.max} ms, total ${w.long.total} ms · main thread ${w.taskPerSec} ms/s (script ${w.scriptPerSec}, style ${w.stylePerSec}, layout ${w.layoutPerSec})`);
    check('write: frame interval p50 (ms)', w.p50, b('p50'));
    check('write: frame interval p95 (ms)', w.p95, b('p95'));
    check('write: script time per second (ms)', w.scriptPerSec, b('scriptPerSec'));
    check('write: long tasks in total (ms)', w.long.total, b('longTotal'));
  });
});

await scenario('memory', async () => {
  const ids = [];
  for (let i = 0; i < 10; i++) ids.push((await ctl.call('mock.generate', { id: `perf-mem-${i}`, turns: 300, shape: 'heavy' })).id);
  await onPage(async ({ page, cdp }) => {
    const m = await measureMemory(page, cdp, ids);
    results.memory = m;
    console.log(`  memory: ${m.sessions} conversations of 300 turns opened one after another · heap ${m.heapMB} MB`);
    check('memory: heap after them all (MB)', m.heapMB, budget('memory', 'heapMB'));
  });
});

await scenario('code', async () => {
  const warm = tsx('Markdown', 'RightPanel', 'SessionView', 'Inbox');
  const code = sixty();
  await ctl.call('mock.generate', { id: 'perf-warm', turns: 1, shape: 'mixed' });
  await ctl.call('mock.messages', { id: 'perf-warm', msgs: codeTurn(warm) });
  await ctl.call('mock.generate', { id: 'perf-code', turns: 1, shape: 'mixed' });
  await ctl.call('mock.messages', { id: 'perf-code', msgs: codeTurn(code) });
  // To the top of the sidebar, which shows only the most recent rows of a folder.
  for (const id of ['perf-warm', 'perf-code']) await ctl.call('mock.touch', { id });
  await onPage(async ({ page }) => {
    const c = await measureCode(page, warm, code);
    results.code = c;
    console.log(`  first TSX block in the window (${c.cold.kB} kB, the grammar compiling): coloured in ${c.cold.ms} ms · long tasks ${c.cold.long.n}, max ${c.cold.long.max} ms`);
    console.log(`  a ${c.kB} kB TSX block: every line coloured in ${c.ms} ms (${c.coloured} of ${c.lines} lines, ${c.styled} coloured tokens) · long tasks ${c.long.n}, max ${c.long.max} ms`);
    check('code: lines coloured short of the whole block', c.lines - c.coloured, 0);
    check('code: longest task while it is coloured (ms)', c.long.max, RULES.codeLongMax);
    check('code: every line coloured (ms)', c.ms, budget('code', 'ms'));
  });
});

await browser.close();
ctl.close();
mock.kill();
if (args.includes('--json')) console.log(JSON.stringify(results));
if (failures.length) {
  console.log(`\nover budget or failed:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log('\nall within budget');
