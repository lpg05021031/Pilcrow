#!/usr/bin/env node
// Performance budgets for the conversation view: the built interface, served by the mock server,
// measured in headless Chrome on generated conversations.
//
//   MOCK_PORT=5321 [PILCROW_PERF=1] node test/e2e/perf.mjs [--headed] [--json] [--only name,name]
//                                                                  (npm run test:perf; needs a build)
//
// The scenarios, each with what it measures:
//   open400 / open2000  open a heavy conversation: modelled and first rows (the app's own performance.measure
//                       entries, store.ts and Transcript.tsx), long tasks, DOM nodes, heap
//   stream / panel      a long turn streamed into the 2,000-turn one, the CPU slowed fourfold, the side panel
//                       closed and then open on Changes: frame intervals, long tasks, script time per second,
//                       how far behind the live end the view falls
//   find                Ctrl+F, then typing a six-letter word that matches all through the 2,000 turns: the
//                       longest task, the DOM nodes after, the time to the first match shown
//   history             from the end of the 2,000 turns, a fling to the top, then reading down with the wheel:
//                       frame intervals and long tasks while it moves, the pages drawn once it stops
//   switch              after reading far up the 2,000 turns, another conversation and back: the time to the
//                       place the reader left, and the longest task
//   read                the 400 turns read from top to end, every page drawn: DOM nodes and heap; then thirty
//                       session.state events that change nothing drawn (CPU ×4), and the system theme flipping
//   fence, list30, list60, table30, paragraph60
//                       a reply that is one long block, streamed 200 characters a time (CPU ×4) until it is
//                       drawn whole: frame intervals, script time per second, long tasks once it is whole
//   write               a 60 kB Write streamed with its card open (CPU ×4): frame intervals and long tasks
//   memory              ten 300-turn conversations opened one after another: the heap after a collection
//   code                a 60 kB TSX block coloured on screen (three of them): no task over 50 ms; and the
//                       first TSX block of the window, the grammar loaded with it: its longest task
//
// Budgets. Every scenario that takes time is measured three times in a row and its medians are what is
// checked (thrice); find, history and memory once (their figures hold still from run to run).
// BASELINE holds those medians, the median of three runs, on the machine that wrote it (the date is
// below), and a budget is that times MARGIN: a twofold regression is over it, the noise of runs in a row
// here under it. Some figures are dominated by noise or quantised, and have a FLOOR instead, the
// larger of the two: a frame interval comes in steps of 16.7 ms (its p50 is always one or two steps),
// a long task is 50 ms or more by definition, a GC pause lands anywhere, and the long tasks of a theme
// flip or of thirty state events are near none. Those budgets catch a figure that leaves its band, not
// a doubling inside it. Long tasks in all while something streams move by two or three times from
// run to run and are reported, not budgeted; the frame intervals, longest task and script time per
// second are. Two budgets are rules rather than measurements (RULES).
//
// What fails the run. The counts, sizes and distances (DOM nodes, heap, pages drawn, px from the live
// end and moved, lines left uncoloured, JavaScript before the first rows) come out the same on any
// machine, busy or not, and fail every run. The times (ms, frame intervals, long tasks, script per
// second) are the machine's as much as the app's: another process on the cores pushes them over
// budget with the same code, so they fail the run only under PILCROW_PERF=1 and are otherwise
// reported with their budgets. The Qt client splits its budgets the same way (qt/PERF.md §5).
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
const FULL = process.env.PILCROW_PERF === '1';
// The baseline figures that are counts, not times (see "What fails the run" above).
const COUNTS = new Set(['nodes', 'heapMB', 'drawn']);
// The median of three runs (each figure itself the median of thrice where measured so): headless Chrome,
// 1400×900, the development container, 2026-11-01, with other work on the machine now and then.
const BASELINE = {
  open400: { modelled: 163, rows: 213, longMax: 109, nodes: 1072, heapMB: 12 },
  open2000: { modelled: 550, rows: 660, longMax: 160, nodes: 1997, heapMB: 25 },
  stream: { p95: 49.9, longMax: 115, scriptPerSec: 159 },
  panel: { p95: 50, longMax: 133, scriptPerSec: 160 },
  find: { openMax: 0, typeMax: 0, nodes: 11781, firstMs: 6 },
  history: { p95: 16.8, longTotal: 0, nodes: 37795, drawn: 49 },
  switch: { ms: 119, longMax: 0 },
  read: { nodes: 51635, heapMB: 23, stateScript: 199, longMax: 0, longTotal: 0 },
  fence: { p50: 16.7, p95: 50.1, scriptPerSec: 219, closing: 422 },
  list30: { p50: 33.3, p95: 50.1, scriptPerSec: 237, closing: 0 },
  list60: { p50: 33.3, p95: 50, scriptPerSec: 188, closing: 0 },
  table30: { p50: 33.3, p95: 83.3, scriptPerSec: 213, closing: 0 },
  paragraph60: { p50: 49.9, p95: 83.3, scriptPerSec: 187, closing: 0 },
  write: { p50: 16.7, p95: 33.3, scriptPerSec: 211, longTotal: 64 },
  memory: { heapMB: 32 },
  code: { ms: 1227 },
  codeCold: { longMax: 66 },
};
// Where a baseline is near nothing a margin means nothing: a frame interval comes in steps of 16.7 ms,
// a long task is 50 ms or more by definition, and a GC pause can land on any step. These budgets are
// the floor instead, set over the noise measured here (a 56 ms task while typing in Find in one of
// nine runs, the rest none) and under what the work cost before it was made cheap (a system theme
// flip: 473 ms of long tasks; thirty state events over every page drawn: a 322 ms task).
// The DOM nodes after opening move by a page: the page above the landing one is drawn or not before the
// view holds still (1,072 or 1,839 nodes for the 400 turns over twelve opens).
const FLOOR = { longMax: 120, openMax: 100, typeMax: 100, longTotal: 200, closing: 200, p95: 40, firstMs: 100, nodes: 2000 };
const RULES = {
  // JavaScript parsed before a conversation's first rows: the editor, the terminal, the highlighter and
  // the app pages load on first use. vite.config.mjs warns past the same figure.
  eagerKB: 720,
  // Coloured a slice at a time and painted a block per frame (lib/highlight, Markdown): no long task at all.
  codeLongMax: 50,
  codeMs: 15000,
};
// A frame interval moves in whole frames, so its budget is rounded up to one: a p95 of 50 ms (three
// frames) gets 83.4, which one frame of noise stays under and a doubling (100) does not.
const FRAME = 1000 / 60;
const budget = (scenario, key) => {
  const b = Math.max(BASELINE[scenario][key] * MARGIN, FLOOR[key] || 0);
  return { max: Math.round((/^p\d+$/.test(key) ? Math.ceil(b / FRAME - 0.01) * FRAME + 0.1 : b) * 10) / 10, timed: !COUNTS.has(key) };
};

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
  // The theme follows the system's, as it does out of the box, so `theme` can flip it the way the OS does.
  await ctl.call('config.set', { theme: 'system', ui: { lastSession: '' } });
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, reducedMotion: 'no-preference', colorScheme: 'dark' });
  await context.addInitScript(() => {
    window.__long = [];
    new PerformanceObserver((l) => window.__long.push(...l.getEntries().map((e) => ({ at: e.startTime, ms: e.duration })))).observe({ type: 'longtask', buffered: true });
  });
  const page = await context.newPage();
  page.__console = [];
  page.on('console', (m) => page.__console.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => (page.__console.push(`page error: ${e.message}`), pageErrors.push(e.message)));
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
  const code = tsx('Transcript', 'Composer', 'Sidebar', 'Board');
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

// Replies of one long block, as they come from Claude: a list, a table and a paragraph that grow at
// their end, made of the inline code, bold and links Claude writes.
const PROSE = ['the reader', 'scrolls back', 'to `useFollow`', 'and **every** row', 'keeps its place', 'while [the list](https://example.com/list)', 'grows at its end', 'in `web/src/lib/markdown.ts`', 'without a jump'];
const prose = (i, n) => Array.from({ length: n }, (_, k) => PROSE[(i * 3 + k) % PROSE.length]).join(' ');
const upTo = (kB, line) => {
  let s = '';
  for (let i = 0; s.length < kB * 1024; i++) s += line(i);
  return s;
};
const SHAPES = {
  list30: () => upTo(30, (i) => `- Item ${i}: ${prose(i, 4)}.\n`),
  list60: () => upTo(60, (i) => `- Item ${i}: ${prose(i, 4)}.\n`),
  table30: () => '| # | Where | What | State |\n|---|---|---|---|\n' + upTo(30, (i) => `| ${i} | \`src/f${i}.ts\` | ${prose(i, 3)} | ok |\n`),
  paragraph60: () => upTo(60, (i) => `${prose(i, 5)}, `) + 'and that is all.',
  fence60: () => '```tsx\n' + sixty() + '\n```',
};

/**
 * A reply streamed into a small conversation 200 characters every 16 ms (CPU ×4), ended as the SDK
 * ends it, with the whole message: the frames from its first piece until it is drawn whole (the
 * reveal catches up after the reply ends, Transcript useSmoothText), then the long tasks of the
 * half second after, where it is drawn whole once and coloured.
 */
let replies = 0;
async function measureReply(page, cdp, sid, text) {
  const ask = `reply ${++replies}, please`;
  const msgId = `msg_reply_${replies}`;
  await ctl.call('mock.messages', { id: sid, msgs: [{ type: 'user', message: { role: 'user', content: [{ type: 'text', text: ask }] } }] });
  await page.waitForFunction((ask) => [...document.querySelectorAll('.msg-user-text')].some((e) => e.textContent === ask), ask, { timeout: 5000 });
  await settle(page); // the question lands first; the reply is what is measured
  await cdp.send('Performance.enable');
  const m0 = await metrics(cdp);
  const t0 = await startFrames(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await feed(sid, [[ev({ type: 'message_start', message: { id: msgId } }), ev({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } })], ...pieces(text, 200).map((p) => [ev({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: p } })])]);
  await feed(sid, [[ev({ type: 'content_block_stop', index: 0 }), ev({ type: 'message_stop' }), { type: 'assistant', message: { id: msgId, role: 'assistant', content: [{ type: 'text', text }] } }]]);
  await page.waitForFunction(() => !document.querySelector('.md.streaming'), null, { timeout: 120_000 });
  const f = await stopFrames(page);
  const per = perSecond(m0, await metrics(cdp), f.ms);
  const long = await longSince(page, t0);
  const t1 = await now(page);
  await sleep(500);
  const closing = await longSince(page, t1);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  return { ...f, ...per, kB: Math.round(text.length / 1024), long, closing };
}

/**
 * Load the TSX grammar before a streaming scenario, with a block shown first: the first block of a
 * language loads its grammar and runs each of its patterns for the first time (Chrome compiles a
 * regular expression when it is first used), a one-off per window (measureCode budgets it), which
 * would otherwise land on whatever is coloured first.
 */
async function warmTsx(page, sid) {
  const warm = tsx('Markdown', 'RightPanel', 'SessionView', 'Inbox');
  await ctl.call('mock.messages', { id: sid, msgs: codeTurn(warm) });
  await page.waitForFunction((n) => [...document.querySelectorAll('pre[data-hl="1"]')].some((p) => p.textContent.length >= n), warm.length - 1, { timeout: RULES.codeMs });
  await settle(page); // the last coloured block is laid out in the frame after it is marked done
}

/** A 60 kB Write streamed as its input arrives (CPU ×4), its fold and card open. */
async function measureWrite(page, cdp, sid) {
  const input = JSON.stringify({ file_path: '/home/me/pilcrow/web/src/components/Big.tsx', content: sixty() });
  const msgId = `msg_write_${++replies}`;
  const id = `toolu_write_${replies}`;
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

/** To the top of a conversation with the wheel, then down to its end a little at a time, so every page is drawn. */
async function readAll(page) {
  const box = await page.locator('.transcript').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < 80 && (await page.evaluate(() => document.querySelector('.transcript').scrollTop)) > 50; i++) {
    await page.mouse.wheel(0, -6000);
    await sleep(16);
  }
  await settle(page);
  for (let i = 0; i < 4000; i++) {
    await page.mouse.wheel(0, 700);
    await sleep(40);
    const left = await page.evaluate(() => {
      const el = document.querySelector('.transcript');
      return el.scrollHeight - el.scrollTop - el.clientHeight > 2 || document.querySelector('.page.far') !== null;
    });
    if (!left) break;
  }
  await settle(page);
}

/**
 * Thirty session.state events, one every 33 ms, that change nothing the conversation shows (the
 * server sends one for every task event, status and permission change), with every page drawn and
 * the CPU slowed fourfold: the main thread's time per second, and the longest task.
 */
async function measureState(page, cdp, id) {
  await cdp.send('Performance.enable');
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  const t0 = await now(page);
  const m0 = await metrics(cdp);
  for (let i = 0; i < 30; i++) {
    await ctl.call('mock.patch', { id, state: { lastActivity: Date.now() } });
    await sleep(33);
  }
  await sleep(300);
  const per = perSecond(m0, await metrics(cdp), (await now(page)) - t0);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  return { ...per, long: await longSince(page, t0) };
}

/** The system's theme flipping under every page drawn: the long tasks until it has settled. */
async function measureTheme(page, scheme) {
  const t0 = await now(page);
  await page.emulateMedia({ colorScheme: scheme });
  await page.waitForFunction((scheme) => document.documentElement.dataset.theme === scheme, scheme, { timeout: 5000 });
  await sleep(800);
  return { long: await longSince(page, t0) };
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
 * The window first shows other TSX (48 kB of other components), which loads the grammar and runs its
 * patterns for the first time: a one-off per window, measured apart in three windows of its own, with
 * a budget of its own (the grammars come precompiled, lib/highlight; translated from Oniguruma on
 * first use, as they were, it was one task of 110–270 ms).
 */
async function measureCode(warm, codes) {
  const cold = await thrice(() => onPage(({ page }) => colour(page, 'perf-warm', warm.length - 1)));
  return onPage(async ({ page }) => {
    await colour(page, 'perf-warm', warm.length - 1);
    let i = 0;
    const out = await thrice(async () => {
      const code = codes[i];
      const c = await colour(page, `perf-code-${i++}`, code.length - 1);
      const drawn = await page.evaluate(() => {
        const pre = [...document.querySelectorAll('pre[data-hl="1"]')].at(-1);
        return { coloured: pre.querySelectorAll('.line').length, styled: pre.querySelectorAll('.line span[style*="--shiki"]').length };
      });
      return { ...c, ...drawn, lines: code.split('\n').length };
    });
    return { ...out, kB: Math.round(codes[0].length / 1024), cold: { kB: Math.round(warm.length / 1024), ...cold } };
  });
}

// ---------------------------------------------------------------- the run

/** The median of each figure over runs of one measurement, nested figures included. */
function median(outs) {
  const first = outs[0];
  if (typeof first === 'number') return outs.slice().sort((a, b) => a - b)[Math.floor(outs.length / 2)];
  if (first && typeof first === 'object') return Object.fromEntries(Object.keys(first).map((k) => [k, median(outs.map((o) => o[k]))]));
  return first;
}
/** A measurement taken three times in a row, and its medians: one run's figure moves by half again from run to run here. */
async function thrice(fn) {
  const outs = [];
  for (let i = 0; i < 3; i++) outs.push(await fn(i));
  return median(outs);
}

const failures = [];
const timesOver = [];
/** A figure against its budget: a plain number (a rule or a distance) or budget()'s, which says whether it is a time. */
const check = (what, value, b) => {
  const { max, timed } = typeof b === 'number' ? { max: b, timed: false } : b;
  if (value == null || value > max) (timed && !FULL ? timesOver : failures).push(`${what}: ${value} (budget ${max})`);
};
const results = {};
let current = null;

/** What the pages threw and nothing caught (a handler, a timer, a promise) since the last scenario ended. */
const pageErrors = [];
/** One scenario; a failure, or anything the page threw, prints the page's state and counts against the run, and the run goes on. */
async function scenario(name, fn) {
  if (!runs(name)) return;
  try {
    await fn();
    if (pageErrors.length) throw new Error(`the page threw: ${pageErrors.join(' | ')}`);
  } catch (e) {
    failures.push(`${name}: ${e.message.split('\n')[0]}`);
    await dump(current, name, e);
  } finally {
    pageErrors.length = 0;
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

/** Opening, three times, each in a window of its own; the last window is handed to `keep` when given. */
async function openThrice(id, keep) {
  let i = 0;
  let last = null;
  const out = await thrice(async () => {
    const p = await freshPage();
    current = p.page;
    const o = await measureOpen(p.page, p.cdp, id);
    if (++i < 3 || !keep) await p.context.close();
    else last = p;
    return o;
  });
  if (last) await keep(last);
  return out;
}

await scenario('open400', async () => reportOpen('open400', 400, await openThrice('perf-400')));

function reportOpen(name, turns, out) {
  results[name] = out;
  const b = (k) => budget(name, k);
  console.log(`  open ${turns} turns (${gen[turns].messages} messages, ${(gen[turns].bytes / 1048576).toFixed(1)} MB): ${out.eagerKB} kB of JS before the first rows · modelled ${out.modelled} ms · first rows ${out.rows} ms (wall ${out.wall} ms) · longest task ${out.long.max} ms, long tasks in all ${out.long.total} ms · ${out.nodes} nodes · heap ${out.heapMB} MB · ${out.fromBottom}px from the end`);
  check(`${name}: JavaScript loaded before the first rows (kB)`, out.eagerKB, RULES.eagerKB);
  check(`${name}: open → modelled (ms)`, out.modelled, b('modelled'));
  check(`${name}: open → first rows (ms)`, out.rows, b('rows'));
  check(`${name}: longest task while opening (ms)`, out.long.max, b('longMax'));
  check(`${name}: DOM nodes`, out.nodes, b('nodes'));
  check(`${name}: heap (MB)`, out.heapMB, b('heapMB'));
  check(`${name}: distance from the live end after opening (px)`, out.fromBottom, 60);
}

/** A streaming figure, said the same way for every scenario that streams. */
const said = (s) => `${s.frames} frames · frame p50 ${s.p50} ms, p95 ${s.p95} ms, max ${s.max} ms · long tasks ${s.long.n}, max ${s.long.max} ms, total ${s.long.total} ms · main thread ${s.taskPerSec} ms/s (script ${s.scriptPerSec}, style ${s.stylePerSec}, layout ${s.layoutPerSec})`;

// The 2,000-turn conversation, one window for all its scenarios after the opening, in the order a session goes.
let big = null;
await scenario('open2000', async () => reportOpen('open2000', 2000, await openThrice('perf-2000', (p) => (big = p))));
if (['stream', 'panel', 'find', 'history', 'switch'].some(runs))
  await (async () => {
    if (!big) {
      big = await freshPage();
      await openSession(big.page, 'perf-2000');
    }
    const { page, cdp } = big;
    current = page;
    const stream = async (name, what) => {
      const s = await thrice(() => measureStream(page, cdp));
      results[name] = s;
      const b = (k) => budget(name, k);
      console.log(`  ${what}: ${said(s)} · ${s.nodes} nodes · at most ${s.behindMax}px behind, ${s.behind}px at the end`);
      check(`${name}: frame interval p95 (ms)`, s.p95, b('p95'));
      check(`${name}: longest task (ms)`, s.long.max, b('longMax'));
      check(`${name}: script time per second of streaming (ms)`, s.scriptPerSec, b('scriptPerSec'));
      check(`${name}: distance from the live end once it ends (px)`, s.behind, 60);
    };
    await scenario('stream', () => stream('stream', 'stream into 2000 turns'));
    await scenario('panel', async () => {
      await page.locator('.composer-input').click();
      await page.keyboard.press('Alt+1');
      await page.waitForSelector('.changes', { timeout: 10_000 });
      await settle(page);
      await stream('panel', 'the same with the Changes panel open');
      await page.keyboard.press('Alt+1');
      await page.waitForSelector('.changes', { state: 'detached', timeout: 10_000 });
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
      const s = await thrice(() => measureSwitch(page, 'perf-2000', 'perf-400'));
      results.switch = s;
      const b = (k) => budget('switch', k);
      console.log(`  back to 2000 turns after reading far up (${s.drawnBefore} pages drawn) and another conversation: the place back in ${s.ms} ms · longest task ${s.long.max} ms · ${s.drawnAfter} pages drawn · the place moved ${s.moved}px`);
      check('switch: back to the place left (ms)', s.ms, b('ms'));
      check('switch: longest task (ms)', s.long.max, b('longMax'));
      check('switch: how far the place moved (px)', s.moved, 80);
    });
    await big.context.close();
  })();

// The 400-turn conversation read from its first message to its last: every page drawn, which is as
// heavy as a window gets (drawn pages stay drawn while the conversation is on screen, Transcript).
await scenario('read', () =>
  onPage(async ({ page, cdp }) => {
    await openSession(page, 'perf-400');
    await settle(page);
    await readAll(page);
    const r = { drawn: await drawn(page), nodes: await nodes(page), heapMB: await heapMB(cdp) };
    r.state = await thrice(() => measureState(page, cdp, 'perf-400'));
    let scheme = 'dark';
    r.theme = await thrice(() => measureTheme(page, (scheme = scheme === 'dark' ? 'light' : 'dark')));
    results.read = r;
    const b = (k) => budget('read', k);
    console.log(`  400 turns read to the end: ${r.drawn} pages drawn, ${r.nodes} nodes, heap ${r.heapMB} MB · 30 session.state events (CPU ×4): main thread ${r.state.taskPerSec} ms/s (script ${r.state.scriptPerSec}), longest task ${r.state.long.max} ms · the system theme flipping: long tasks ${r.theme.long.n}, max ${r.theme.long.max} ms, total ${r.theme.long.total} ms`);
    check('read: DOM nodes with every page drawn', r.nodes, b('nodes'));
    check('read: heap with every page drawn (MB)', r.heapMB, b('heapMB'));
    check('read: script time per second of session.state events (ms)', r.state.scriptPerSec, b('stateScript'));
    check('read: longest task of session.state events (ms)', r.state.long.max, b('longMax'));
    check('read: long tasks of a system theme flip in all (ms)', r.theme.long.total, b('longTotal'));
  }),
);

// Replies of one long block each, each in a small conversation and a window of its own (what the
// replies before it drew would otherwise weigh on it): the fence (and the Write below) after the TSX
// grammar has compiled, which is a one-off per window (measureCode).
async function small(page, sid) {
  await ctl.call('mock.generate', { id: sid, turns: 4, shape: 'mixed' });
  await ctl.call('mock.touch', { id: sid }); // the sidebar lists a folder's most recent rows
  await openSession(page, sid);
}
for (const [name, what] of [
  ['fence', 'code fence'],
  ['list30', 'bullet list'],
  ['list60', 'bullet list'],
  ['table30', 'table'],
  ['paragraph60', 'paragraph'],
])
  await scenario(name, () =>
    onPage(async ({ page, cdp }) => {
      const sid = `perf-${name}`;
      await small(page, sid);
      if (name === 'fence') await warmTsx(page, sid);
      const text = SHAPES[name === 'fence' ? 'fence60' : name]();
      const f = await thrice(() => measureReply(page, cdp, sid, text));
      results[name] = f;
      const b = (k) => budget(name, k);
      console.log(`  a ${f.kB} kB ${what} streamed (CPU ×4), until drawn whole: ${said(f)} · the half second after, long tasks ${f.closing.total} ms in all`);
      check(`${name}: frame interval p50 (ms)`, f.p50, b('p50'));
      check(`${name}: frame interval p95 (ms)`, f.p95, b('p95'));
      check(`${name}: script time per second (ms)`, f.scriptPerSec, b('scriptPerSec'));
      check(`${name}: long tasks once drawn whole (ms)`, f.closing.total, b('closing'));
    }),
  );

await scenario('write', () =>
  onPage(async ({ page, cdp }) => {
    await small(page, 'perf-write');
    await warmTsx(page, 'perf-write');
    const w = await thrice(() => measureWrite(page, cdp, 'perf-write'));
    results.write = w;
    const b = (k) => budget('write', k);
    console.log(`  a ${w.kB} kB Write streamed, its card open (CPU ×4): ${said(w)}`);
    check('write: frame interval p50 (ms)', w.p50, b('p50'));
    check('write: frame interval p95 (ms)', w.p95, b('p95'));
    check('write: script time per second (ms)', w.scriptPerSec, b('scriptPerSec'));
    check('write: long tasks in total (ms)', w.long.total, b('longTotal'));
  }),
);

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
  // Three blocks, each a little different so none is coloured from the cache of the one before.
  const codes = [0, 1, 2].map((i) => `${sixty()}\n// block ${i}`);
  await ctl.call('mock.generate', { id: 'perf-warm', turns: 1, shape: 'mixed' });
  await ctl.call('mock.messages', { id: 'perf-warm', msgs: codeTurn(warm) });
  for (let i = 0; i < 3; i++) {
    await ctl.call('mock.generate', { id: `perf-code-${i}`, turns: 1, shape: 'mixed' });
    await ctl.call('mock.messages', { id: `perf-code-${i}`, msgs: codeTurn(codes[i]) });
  }
  // To the top of the sidebar, which shows only the most recent rows of a folder.
  for (const id of ['perf-warm', 'perf-code-0', 'perf-code-1', 'perf-code-2']) await ctl.call('mock.touch', { id });
  const c = await measureCode(warm, codes);
  results.code = c;
  console.log(`  first TSX block in a window (${c.cold.kB} kB, the grammar loading): coloured in ${c.cold.ms} ms · long tasks ${c.cold.long.n}, max ${c.cold.long.max} ms`);
  console.log(`  a ${c.kB} kB TSX block: every line coloured in ${c.ms} ms (${c.coloured} of ${c.lines} lines, ${c.styled} coloured tokens) · long tasks ${c.long.n}, max ${c.long.max} ms`);
  check('code: lines coloured short of the whole block', c.lines - c.coloured, 0);
  check('code: longest task while it is coloured (ms)', c.long.max, { max: RULES.codeLongMax, timed: true });
  check('code: longest task while the first block of a window is coloured (ms)', c.cold.long.max, budget('codeCold', 'longMax'));
  check('code: every line coloured (ms)', c.ms, budget('code', 'ms'));
});

await browser.close();
ctl.close();
mock.kill();
if (args.includes('--json')) console.log(JSON.stringify(results));
if (timesOver.length) console.log(`\ntimes over budget, reported (PILCROW_PERF=1 fails on them):\n  ${timesOver.join('\n  ')}`);
if (failures.length) {
  console.log(`\nover budget or failed:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(FULL ? '\nall within budget' : `\ncounts within budget; ${timesOver.length} timing figure${timesOver.length === 1 ? '' : 's'} reported over budget (PILCROW_PERF=1 enforces the times)`);
