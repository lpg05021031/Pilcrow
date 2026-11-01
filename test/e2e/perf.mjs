#!/usr/bin/env node
// Performance budgets for the conversation view: the built interface, served by the mock server,
// measured in headless Chrome. Generated conversations of 400 and 2,000 turns are opened, then a long
// turn is streamed into the larger one while the view follows it.
//
//   MOCK_PORT=5321 node test/e2e/perf.mjs [--headed] [--json]     (npm run test:perf; needs a build)
//
// What is measured, and where it comes from:
//   open → modelled / first rows   the app's own performance.measure entries (store.ts, Transcript.tsx)
//   long tasks                     PerformanceObserver 'longtask' (main-thread tasks over 50 ms)
//   frame p95                      requestAnimationFrame intervals while the view follows the stream
//   script / style / layout        Chrome's own counters (CDP Performance.getMetrics), per second streamed
//   DOM nodes, heap                after the view has settled; the heap after a forced collection
//   eager JS                       the scripts fetched before the first rows (Resource Timing)
//   a 60 kB TSX block              every line coloured, and no task over 50 ms meanwhile
// Budgets sit well above what this machine measures, so they catch a regression, not noise.
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import WebSocket from 'ws';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
// Never the mock's default port: a developer's own mock server may be running there.
const PORT = Number(process.env.MOCK_PORT || 5321);
const URL_ = `http://127.0.0.1:${PORT}/`;
const headed = process.argv.includes('--headed');

const BUDGET = {
  400: { modelled: 800, rows: 1000, longMax: 400, nodes: 4000, heapMB: 40 },
  2000: { modelled: 2000, rows: 2500, longMax: 500, nodes: 4000, heapMB: 80 },
  // Under the fourfold CPU slowdown (measureStream). Before the streaming work: frame p95 83 ms,
  // 58 long tasks adding up to 4.1 s, 414 ms of script per second.
  stream: { p95: 100, longMax: 300, longTotal: 3000, scriptPerSec: 350 },
  // JavaScript parsed before a conversation's first rows: 1,664 kB before the editor, the terminal and
  // the highlighter loaded on first use, 780 kB before the control room, inbox, dispatch and settings
  // did, 662 kB now. vite.config.mjs warns past the same figure.
  eagerKB: 720,
  // Coloured a slice at a time and painted a block per frame (lib/highlight, Markdown): with the TSX
  // grammar compiled (by an earlier block), no task over 50 ms, so no long task at all.
  code: { longMax: 50, ms: 15000 },
};

if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) {
  console.error('web/dist is missing: run `npm run build` first');
  process.exit(2);
}

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

/** A page that records every long task from its first moment. */
async function freshPage() {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, reducedMotion: 'no-preference' });
  await context.addInitScript(() => {
    window.__long = [];
    new PerformanceObserver((l) => window.__long.push(...l.getEntries().map((e) => ({ at: e.startTime, ms: e.duration })))).observe({ type: 'longtask', buffered: true });
  });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('   page error:', e.message));
  await page.goto(URL_);
  await page.waitForSelector('.sidebar .side-row', { timeout: 20_000 });
  const cdp = await context.newCDPSession(page);
  return { context, page, cdp };
}

const longSince = (page, t0) => page.evaluate((t0) => {
  const xs = window.__long.filter((e) => e.at >= t0).map((e) => e.ms);
  return { n: xs.length, max: Math.round(Math.max(0, ...xs)), total: Math.round(xs.reduce((a, b) => a + b, 0)) };
}, t0);

async function heapMB(cdp) {
  await cdp.send('HeapProfiler.collectGarbage');
  const { usedSize } = await cdp.send('Runtime.getHeapUsage');
  return Math.round(usedSize / 1048576);
}

const nodes = (page) => page.evaluate(() => document.getElementsByTagName('*').length);

/** Open a conversation from the sidebar and take its numbers once the view has settled. */
async function measureOpen(turns) {
  const id = `perf-${turns}`;
  const gen = await ctl.call('mock.generate', { id, turns, shape: 'heavy' });
  const { context, page, cdp } = await freshPage();
  const t0 = await page.evaluate(() => performance.now());
  await page.click(`[data-sid="${id}"] .side-row-main`);
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0, null, { timeout: 30_000 });
  const wall = Math.round((await page.evaluate(() => performance.now())) - t0);
  await page.waitForFunction((id) => performance.getEntriesByType('measure').some((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id), id, { timeout: 30_000 }).catch(() => {});
  await sleep(2000); // the landing passes and the pages near the end being drawn
  const measures = await page.evaluate((id) => Object.fromEntries(performance.getEntriesByType('measure').filter((m) => m.detail?.id === id).map((m) => [m.name, Math.round(m.duration)])), id);
  const fromBottom = await page.evaluate(() => { const el = document.querySelector('.transcript'); return Math.round(el.scrollHeight - el.scrollTop - el.clientHeight); });
  // Up to the first rows of the first conversation shown (the window may reopen the last one by itself).
  const eagerKB = await page.evaluate(() => {
    const end = Math.min(...performance.getEntriesByType('measure').filter((m) => m.name === 'pilcrow:first-rows').map((m) => m.startTime + m.duration));
    const js = performance.getEntriesByType('resource').filter((r) => /\.m?js(\?|$)/.test(r.name) && r.responseEnd <= end);
    return Math.round(js.reduce((n, r) => n + r.decodedBodySize, 0) / 1024);
  });
  const out = { turns, eagerKB, messages: gen.messages, mb: +(gen.bytes / 1048576).toFixed(1), wall, modelled: measures['pilcrow:modelled'], rows: measures['pilcrow:first-rows'], long: await longSince(page, t0), nodes: await nodes(page), heapMB: await heapMB(cdp), fromBottom };
  return { out, context, page, cdp };
}

const METRICS = ['TaskDuration', 'ScriptDuration', 'RecalcStyleDuration', 'LayoutDuration'];
const metrics = async (cdp) => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.filter((m) => METRICS.includes(m.name)).map((m) => [m.name, m.value]));

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
  const t0 = await page.evaluate(() => {
    window.__frames = [];
    window.__follow = [];
    const tick = (t) => {
      window.__frames.push(t);
      if (!window.__stopFrames) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    // How far behind the live end the view is, sampled apart from the frames: a layout read in the
    // frame callback would itself cost a layout per frame.
    const sample = setInterval(() => {
      if (window.__stopFrames) return clearInterval(sample);
      const el = document.querySelector('.transcript');
      window.__follow.push(el ? el.scrollHeight - el.scrollTop - el.clientHeight : 0);
    }, 250);
    return performance.now();
  });
  const m0 = await metrics(cdp);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await ta.press('Enter');
  await page.waitForSelector('.session-head .status-pill', { timeout: 10_000 });
  await page.waitForFunction(() => !document.querySelector('.session-head .status-pill') && !document.querySelector('.work.live'), null, { timeout: 120_000 });
  const f = await page.evaluate(() => {
    window.__stopFrames = true;
    const t = window.__frames;
    const gaps = t.slice(1).map((x, i) => x - t[i]).sort((a, b) => a - b);
    const q = (p) => gaps[Math.min(gaps.length - 1, Math.floor(gaps.length * p))] || 0;
    return { frames: t.length, ms: Math.round(t[t.length - 1] - t[0]), p50: +q(0.5).toFixed(1), p95: +q(0.95).toFixed(1), max: Math.round(gaps[gaps.length - 1] || 0), behindMax: Math.round(Math.max(...window.__follow)) };
  });
  // Main-thread time, as the throttled CPU spent it, per second of streaming.
  const m1 = await metrics(cdp);
  const perSec = (name) => Math.round(((m1[name] - m0[name]) * 1e6) / f.ms);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  await sleep(800);
  const behind = await page.evaluate(() => { const el = document.querySelector('.transcript'); return Math.round(el.scrollHeight - el.scrollTop - el.clientHeight); });
  return { ...f, behind, taskPerSec: perSec('TaskDuration'), scriptPerSec: perSec('ScriptDuration'), stylePerSec: perSec('RecalcStyleDuration'), layoutPerSec: perSec('LayoutDuration'), long: await longSince(page, t0), nodes: await nodes(page) };
}

const tsx = (...names) => names.map((f) => readFileSync(join(ROOT, 'web', 'src', 'components', `${f}.tsx`), 'utf8')).join('');
const codeTurn = (code) => [{ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'show me the code' }] } }, { type: 'assistant', message: { id: `msg_${code.length}`, role: 'assistant', content: [{ type: 'text', text: 'Here it is:\n\n```tsx\n' + code + '\n```' }] } }];

/** Open a conversation and wait until its last code block is coloured; the long tasks from its first rows on. */
async function colour(page, id, minLength) {
  const rows = () => page.evaluate((id) => performance.getEntriesByType('measure').find((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id), id);
  await page.click(`[data-sid="${id}"] .side-row-main`);
  await page.waitForFunction((id) => performance.getEntriesByType('measure').some((m) => m.name === 'pilcrow:first-rows' && m.detail?.id === id), id, { timeout: 30_000 });
  // From the commit of the first rows on: the render that put the block on the page is not the highlighter's.
  const m = await rows();
  const t0 = m.startTime + m.duration;
  await page.waitForFunction((n) => [...document.querySelectorAll('pre[data-hl="1"]')].some((p) => p.textContent.length >= n), minLength, { timeout: BUDGET.code.ms });
  return { ms: Math.round((await page.evaluate(() => performance.now())) - t0), long: await longSince(page, t0) };
}

/**
 * A 60 kB TSX block (this app's own components) in a reply: how it is coloured once it is on screen.
 * The window first shows other TSX (48 kB of other components): Shiki compiles a grammar's patterns
 * the first time each is needed, 50–350 ms at a time here, a one-off per pattern and window that
 * slicing cannot split. That first block is reported apart, without a budget.
 */
async function measureCode() {
  const warm = tsx('Markdown', 'RightPanel', 'SessionView', 'Inbox');
  let code = tsx('Transcript', 'Composer', 'Sidebar', 'Overview');
  code = code.slice(0, code.lastIndexOf('\n', 60_000));
  await ctl.call('mock.generate', { id: 'perf-warm', turns: 1, shape: 'mixed' });
  await ctl.call('mock.messages', { id: 'perf-warm', msgs: codeTurn(warm) });
  await ctl.call('mock.generate', { id: 'perf-code', turns: 1, shape: 'mixed' });
  await ctl.call('mock.messages', { id: 'perf-code', msgs: codeTurn(code) });
  // To the top of the sidebar, which shows only the most recent rows of a folder.
  for (const id of ['perf-warm', 'perf-code']) await ctl.call('mock.touch', { id });
  const { context, page } = await freshPage();
  const cold = await colour(page, 'perf-warm', warm.length - 1);
  const out = await colour(page, 'perf-code', code.length - 1);
  Object.assign(out, await page.evaluate((lines) => {
    const pre = [...document.querySelectorAll('pre[data-hl="1"]')].at(-1);
    return { lines, coloured: pre.querySelectorAll('.line').length, styled: pre.querySelectorAll('.line span[style*="--shiki"]').length };
  }, code.split('\n').length));
  out.kB = Math.round(code.length / 1024);
  out.cold = { kB: Math.round(warm.length / 1024), ...cold };
  await context.close();
  return out;
}

const failures = [];
const check = (what, value, max) => {
  if (value == null || value > max) failures.push(`${what}: ${value} (budget ${max})`);
};

console.log('Pilcrow performance (headless Chrome, heavy generated conversations)');
const results = {};
for (const turns of [400, 2000]) {
  const { out, context, page, cdp } = await measureOpen(turns);
  results[turns] = out;
  console.log(`  open ${turns} turns (${out.messages} messages, ${out.mb} MB): ${out.eagerKB} kB of JS before the first rows · modelled ${out.modelled} ms · first rows ${out.rows} ms (wall ${out.wall} ms) · long tasks ${out.long.n}, max ${out.long.max} ms, total ${out.long.total} ms · ${out.nodes} nodes · heap ${out.heapMB} MB`);
  const b = BUDGET[turns];
  check(`${turns} turns: JavaScript loaded before the first rows (kB)`, out.eagerKB, BUDGET.eagerKB);
  check(`${turns} turns: open → modelled (ms)`, out.modelled, b.modelled);
  check(`${turns} turns: open → first rows (ms)`, out.rows, b.rows);
  check(`${turns} turns: longest task while opening (ms)`, out.long.max, b.longMax);
  check(`${turns} turns: DOM nodes`, out.nodes, b.nodes);
  check(`${turns} turns: heap (MB)`, out.heapMB, b.heapMB);
  check(`${turns} turns: distance from the live end after opening (px)`, out.fromBottom, 60);
  if (turns === 2000) {
    const s = await measureStream(page, cdp);
    results.stream = s;
    console.log(`  stream into ${turns} turns: ${s.frames} frames in ${s.ms} ms · frame p50 ${s.p50} ms, p95 ${s.p95} ms, max ${s.max} ms · long tasks ${s.long.n}, max ${s.long.max} ms, total ${s.long.total} ms · main thread ${s.taskPerSec} ms/s (script ${s.scriptPerSec}, style ${s.stylePerSec}, layout ${s.layoutPerSec}) · ${s.nodes} nodes · at most ${s.behindMax}px behind, ${s.behind}px at the end · heap ${await heapMB(cdp)} MB`);
    check('stream: frame interval p95 (ms)', s.p95, BUDGET.stream.p95);
    check('stream: longest task (ms)', s.long.max, BUDGET.stream.longMax);
    check('stream: long tasks in total (ms)', s.long.total, BUDGET.stream.longTotal);
    check('stream: script time per second of streaming (ms)', s.scriptPerSec, BUDGET.stream.scriptPerSec);
    check('stream: distance from the live end once it ends (px)', s.behind, 60);
  }
  await context.close();
}

const c = await measureCode();
results.code = c;
console.log(`  first TSX block in the window (${c.cold.kB} kB, the grammar compiling): coloured in ${c.cold.ms} ms · long tasks ${c.cold.long.n}, max ${c.cold.long.max} ms`);
console.log(`  a ${c.kB} kB TSX block: every line coloured in ${c.ms} ms (${c.coloured} of ${c.lines} lines, ${c.styled} coloured tokens) · long tasks ${c.long.n}, max ${c.long.max} ms`);
check('code: lines coloured short of the whole block', c.lines - c.coloured, 0);
check('code: longest task while it is coloured (ms)', c.long.max, BUDGET.code.longMax);

await browser.close();
ctl.close();
mock.kill();
if (process.argv.includes('--json')) console.log(JSON.stringify(results));
if (failures.length) {
  console.log(`\nover budget:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log('\nall within budget');
