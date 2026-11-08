#!/usr/bin/env node
// Browser tests: the built interface, served by the mock server, driven in headless Chromium with
// Playwright. Real clicks, scrolls and typing; assertions on the DOM and on where things are.
//
//   node test/e2e/run.mjs [--grep text] [--headed] [--keep]
//
// Needs web/dist (npm run build) and Playwright's Chromium (npx playwright install chromium); when
// that download is not possible, PILCROW_CHROME=/path/to/chrome (or an installed Chrome/Edge) is used.
// Failures leave a screenshot in test/out/.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import WebSocket from 'ws';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const OUT = join(ROOT, 'test', 'out');
const PORT = Number(process.env.MOCK_PORT || 5309);
const URL_ = `http://127.0.0.1:${PORT}/`;
const args = process.argv.slice(2);
const grep = args.includes('--grep') ? args[args.indexOf('--grep') + 1] : '';
const headed = args.includes('--headed');

if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) {
  console.error('web/dist is missing: run `npm run build` first');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

// ---------------------------------------------------------------- mock server + control socket

const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '400' }, stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((ok, bad) => {
  mock.stdout.on('data', () => {});
  const t = setInterval(async () => {
    try {
      const r = await fetch(`${URL_}api/hello`);
      if (r.ok) {
        clearInterval(t);
        ok();
      }
    } catch {}
  }, 100);
  mock.on('exit', (c) => bad(new Error(`mock server exited (${c})`)));
});

/** The tests talk to the mock through the same JSON-RPC the interface uses. */
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

// ---------------------------------------------------------------- browser

function findBrowser() {
  if (process.env.PILCROW_CHROME) return { executablePath: process.env.PILCROW_CHROME };
  try {
    if (existsSync(chromium.executablePath())) return {};
  } catch {}
  const local = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];
  const p = local.find((x) => existsSync(x));
  if (p) return { executablePath: p };
  console.error("Playwright's Chromium is not installed (npx playwright install chromium) and no Chrome/Edge was found; set PILCROW_CHROME.");
  process.exit(2);
}
const launch = findBrowser();
const browser = await chromium.launch({ headless: !headed, ...launch });
if (launch.executablePath) console.log(`(using ${launch.executablePath})`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];

async function fresh() {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, reducedMotion: 'no-preference' });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('   page error:', e.message));
  await page.goto(URL_);
  await page.waitForSelector('.sidebar .side-row', { timeout: 20_000 });
  return { context, page };
}

async function open(page, sid) {
  await page.click(`[data-sid="${sid}"] .side-row-main`);
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0 || document.querySelector('.session-empty'), null, { timeout: 15_000 });
  await sleep(500); // let the landing passes settle
}

const transcript = (page) => page.evaluate(() => {
  const el = document.querySelector('.transcript');
  return { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight, fromBottom: el.scrollHeight - el.scrollTop - el.clientHeight };
});
const atBottom = async (page) => (await transcript(page)).fromBottom < 60;

/** A reader's scroll (wheel events over the conversation), which the follow logic treats as leaving the live end. */
async function wheelUp(page, ticks = 6, dy = 600) {
  const box = await page.locator('.transcript').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < ticks; i++) {
    await page.mouse.wheel(0, -dy);
    await sleep(40);
  }
  await sleep(300);
}

async function send(page, text) {
  const ta = page.locator('.composer-input');
  await ta.click();
  await ta.fill(text);
  await ta.press('Enter');
}

/** Wait for the turn to end: the status pill leaves and no fold is live. */
async function turnDone(page, timeout = 30_000) {
  await page.waitForFunction(() => !document.querySelector('.session-head .status-pill') && !document.querySelector('.work.live'), null, { timeout });
  await sleep(300);
}

function expect(cond, what) {
  if (!cond) throw new Error(what);
}

// ---------------------------------------------------------------- the tests

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test('normal view: a finished conversation shows only the root folds', async ({ page }) => {
  await open(page, 'sess-login');
  const n = await page.locator('.work').count();
  expect(n >= 2, `expected the turns with tools to fold, found ${n} folds`);
  expect((await page.locator('.work.open').count()) === 0, 'a fold is open although nothing is running');
  expect((await page.locator('.tool').count()) === 0, 'tool cards are on the page in Normal view');
  const words = await page.locator('.msg-assistant').count();
  expect(words >= 4, `Claude's words should stay on the page (narration and answers); found ${words}`);
  expect((await page.locator('.work-mark:not(.gone)').count()) === 0, 'a working mark is still showing');
  await expectAtBottom(page);
});

async function expectAtBottom(page) {
  const t = await transcript(page);
  expect(t.fromBottom < 60, `not at the live end: ${JSON.stringify(t)}`);
}

test('a turn: the message lands, the work folds live with the mark, the answer stays out', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 12 });
  await open(page, 'sess-empty');
  await send(page, 'please look at the parser and fix the comment');
  await page.waitForSelector('.msg-user', { timeout: 5000 });
  expect((await page.locator('.msg-user .msg-user-text').first().innerText()).includes('look at the parser'), 'the sent message is not on the page');
  await page.waitForSelector('.work.live', { timeout: 15_000 });
  expect((await page.locator('.work.live .work-mark:not(.gone)').count()) === 1, 'the live fold has no working mark');
  expect((await page.locator('.work.live.open').count()) === 0, 'the live fold opened by itself in Normal view');
  await turnDone(page);
  const last = await page.locator('.msg-assistant').last().innerText();
  expect(/Done/.test(last), `the final answer is not the last thing on the page: ${JSON.stringify(last)}`);
  expect((await page.locator('.work.open').count()) === 0, 'the work stayed open after the turn');
  const sums = await page.locator('.work-sum').allInnerTexts();
  expect(sums.some((s) => /edited a file/i.test(s)), `the fold line should say what was done: ${sums.join(' | ')}`);
  await expectAtBottom(page);
});

test('permission: the card opens the fold, 1 allows, the turn goes on', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 6 });
  await open(page, 'sess-tests');
  await send(page, 'please ask permission before you run a command');
  await page.waitForSelector('.perm', { timeout: 15_000 });
  expect(await page.locator('.perm .btn', { hasText: 'Allow' }).first().isVisible(), 'the Allow button is not visible');
  expect((await page.locator('.work.open').count()) >= 1, 'the fold with the waiting tool did not open');
  expect(await page.locator('.session-head .status-pill', { hasText: 'Needs you' }).isVisible(), 'the status pill does not say Needs you');
  // The composer has focus and is empty: a lone "1" answers the prompt.
  await page.locator('.composer-input').click();
  await page.keyboard.press('1');
  await page.waitForSelector('.perm', { state: 'detached', timeout: 5000 });
  await turnDone(page);
  const last = await page.locator('.msg-assistant').last().innerText();
  expect(/finished cleanly/.test(last), `after allowing, the turn should have run the command: ${last}`);
});

test('permission: Deny tells Claude so', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 6 });
  await open(page, 'sess-site');
  await send(page, 'run a dangerous command');
  await page.waitForSelector('.perm', { timeout: 15_000 });
  await page.locator('.perm .btn', { hasText: 'Deny' }).click();
  await turnDone(page);
  const last = await page.locator('.msg-assistant').last().innerText();
  expect(/did not run it/.test(last), `after denying, Claude should say so: ${last}`);
  expect((await page.locator('.work.has-failed').count()) >= 1, 'the fold should show the denied step as failed');
});

test('archive: the row folds away and the other rows keep their DOM', async ({ page }) => {
  await open(page, 'sess-login');
  const before = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.side-list [data-sid]')];
    for (const r of rows) r.__probe = true;
    return rows.map((r) => r.dataset.sid);
  });
  expect(before.includes('sess-site'), 'sess-site is not listed');
  const row = page.locator('[data-sid="sess-site"]');
  await row.hover();
  await row.locator('.side-row-menu').click();
  await page.getByRole('menuitem', { name: 'Archive' }).click();
  await page.waitForSelector('[data-sid="sess-site"]', { state: 'detached', timeout: 5000 });
  await sleep(400);
  const after = await page.evaluate(() => [...document.querySelectorAll('.side-list [data-sid]')].map((r) => ({ id: r.dataset.sid, kept: r.__probe === true })));
  const lost = after.filter((r) => !r.kept).map((r) => r.id);
  expect(lost.length === 0, `rows were re-created on archive: ${lost.join(', ')}`);
  expect(after.length === before.length - 1, `expected one row fewer, had ${before.length}, now ${after.length}`);
  await ctl.call('session.archive', { id: 'sess-site', archived: false });
});

test('sidebar order: a list refresh keeps the order; sending moves the session up', async ({ page }) => {
  await open(page, 'sess-login');
  // The project groups only: "Needs you" and "Pinned" sit above them and are ordered by their own rules.
  const order = () => page.evaluate(() => [...document.querySelectorAll('.side-list .side-group:not(.section) [data-sid]')].map((r) => r.dataset.sid));
  const a = await order();
  await ctl.call('session.rename', { id: 'sess-old', title: 'Old archived exploration' }); // a refresh that moves nothing
  await sleep(500);
  const b = await order();
  expect(a.join() === b.join(), `the order changed on a plain refresh:\n${a.join(' ')}\n${b.join(' ')}`);
  expect(a.indexOf('sess-tests') > 0, 'sess-tests should not start at the top');
  await ctl.call('mock.pace', { ms: 0 });
  await open(page, 'sess-tests');
  await send(page, 'hello there');
  await turnDone(page);
  await sleep(300);
  const c = await order();
  expect(c[0] === 'sess-tests', `sending a message should move the session to the top of its group: ${c.join(' ')}`);
});

test('brand mark lines up with the row icons', async ({ page }) => {
  const r = await page.evaluate(() => {
    const brand = document.querySelector('.brand svg').getBoundingClientRect();
    const icon = document.querySelector('.side-page svg').getBoundingClientRect();
    return { brand: brand.left, icon: icon.left };
  });
  expect(Math.abs(r.brand - r.icon) <= 1, `brand mark at ${r.brand}, row icon at ${r.icon}`);
});

test('a 400-turn conversation opens in under a second and draws only what is near', async ({ page }) => {
  const t0 = Date.now();
  await page.click('[data-sid="sess-long"] .side-row-main');
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0, null, { timeout: 5000 });
  const ms = Date.now() - t0;
  expect(ms < 1000, `took ${ms}ms to open`);
  await sleep(600);
  const far = await page.locator('.page.far').count();
  const drawn = await page.locator('.page:not(.far)').count();
  expect(far > 20, `expected most pages to be stand-ins, far=${far} drawn=${drawn}`);
  expect(drawn <= 8, `too many pages drawn for a fresh open: ${drawn}`);
  // The stand-in heights settle over a few frames; the landing keeps up with them.
  await page.waitForFunction(() => { const el = document.querySelector('.transcript'); return el.scrollHeight - el.scrollTop - el.clientHeight < 60; }, null, { timeout: 4000 });
});

test('Latest appears when new rows arrive while you are up, and leaves when you go back', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 0 });
  await open(page, 'sess-long');
  expect((await page.locator('.jump').count()) === 0, 'Latest is showing at the live end');
  await wheelUp(page);
  expect(!(await atBottom(page)), 'still at the bottom after scrolling up');
  await sleep(400);
  expect((await page.locator('.jump').count()) === 0, 'Latest should wait for something new to point at');
  // Something new at the live end (sent from another window, say) is what Latest offers the way to.
  await ctl.call('session.send', { id: 'sess-long', text: 'a message from another window' });
  await page.waitForSelector('.jump', { timeout: 5000 });
  expect(!(await atBottom(page)), 'new rows must not drag the reader down');
  await page.locator('.jump').click();
  await page.waitForFunction(() => { const el = document.querySelector('.transcript'); return el.scrollHeight - el.scrollTop - el.clientHeight < 60; }, null, { timeout: 4000 });
  await page.waitForSelector('.jump', { state: 'detached', timeout: 3000 });
});

test('sending from up in the history returns to the live end and follows the reply', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 10 });
  await open(page, 'sess-long');
  await wheelUp(page, 8);
  expect(!(await atBottom(page)), 'not scrolled up');
  await send(page, 'slow reply please');
  await page.waitForSelector('.msg-user:last-of-type', { timeout: 5000 });
  await page.waitForFunction(() => { const el = document.querySelector('.transcript'); return el.scrollHeight - el.scrollTop - el.clientHeight < 60; }, null, { timeout: 4000 });
  // While the reply streams the view stays on it.
  const samples = [];
  for (let i = 0; i < 8; i++) {
    samples.push((await transcript(page)).fromBottom);
    await sleep(150);
  }
  expect(samples.every((d) => d < 80), `the view did not follow the streaming reply: ${samples.join(', ')}`);
  await turnDone(page);
  await expectAtBottom(page);
  expect((await page.locator('.jump').count()) === 0, 'Latest is showing after the reply');
});

test('typing in the composer does not move the conversation', async ({ page }) => {
  await open(page, 'sess-long');
  // The composer sits over the end of the conversation; the last row keeps its distance above it.
  const gap = () => page.evaluate(() => {
    const composer = document.querySelector('.composer-wrap').getBoundingClientRect();
    const rows = document.querySelectorAll('.page:not(.far) .unit');
    const last = rows[rows.length - 1].getBoundingClientRect();
    const el = document.querySelector('.transcript');
    return { gap: Math.round(composer.top - last.bottom), fromBottom: el.scrollHeight - el.scrollTop - el.clientHeight };
  });
  const ta = page.locator('.composer-input');
  await ta.click();
  const g0 = await gap();
  const seen = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.type(`line ${i + 1} of a longer message`);
    await page.keyboard.press('Shift+Enter');
    await sleep(120);
    seen.push(await gap());
  }
  const drift = seen.map((g) => Math.abs(g.gap - g0.gap));
  expect(Math.max(...drift) <= 2, `the last row moved relative to the bottom edge while typing: ${seen.map((g) => g.gap).join(', ')} (was ${g0.gap})`);
  expect(seen.every((g) => g.fromBottom < 4), `the view left the live end while typing: ${seen.map((g) => g.fromBottom).join(', ')}`);
});

test('find draws every page and counts matches across the whole conversation', async ({ page }) => {
  await open(page, 'sess-long');
  expect((await page.locator('.page.far').count()) > 0, 'no stand-in pages before find');
  await page.keyboard.press('Control+f');
  await page.waitForSelector('.findbar', { timeout: 3000 });
  await page.waitForFunction(() => document.querySelectorAll('.page.far').length === 0, null, { timeout: 8000 });
  await page.locator('.findbar input').fill('typo');
  await page.waitForFunction(() => /of \d+/.test(document.querySelector('.findbar-count')?.textContent || ''), null, { timeout: 8000 });
  const parse = async () => (await page.locator('.findbar-count').innerText()).match(/(\d+) of (\d+)/).slice(1, 3).map(Number);
  const [at, count] = await parse();
  expect(count >= 150, `expected the word in every fourth turn to be found across 400 turns, got ${count}`);
  await page.keyboard.press('Enter');
  await sleep(300);
  const [next] = await parse();
  expect(next === (at % count) + 1, `Enter should step from match ${at} to ${(at % count) + 1}, got ${next}`);
  await page.keyboard.press('Escape');
  await page.waitForSelector('.findbar', { state: 'detached', timeout: 3000 });
  expect((await page.locator('.page.far').count()) === 0, 'closing find should not undraw pages (that would move the view)');
});

test('switching away and back lands on the same spot', async ({ page }) => {
  await open(page, 'sess-long');
  await wheelUp(page, 10);
  const before = await transcript(page);
  expect(before.fromBottom > 300, 'not far enough up to tell');
  await open(page, 'sess-login');
  await open(page, 'sess-long');
  await sleep(600);
  const after = await transcript(page);
  expect(Math.abs(after.top - before.top) < 80, `scrolled to ${after.top}, was at ${before.top}`);
  expect((await page.locator('.jump').count()) === 1, 'Latest should offer the way back down');
});

test('workboard: five lanes, and the seeded sessions land in the right ones', async ({ page }) => {
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.waitForSelector('.bd-lanes', { timeout: 5000 });
  await sleep(400);
  const r = await page.evaluate(() => {
    const lanes = [...document.querySelectorAll('.bd-lane')].map((l) => ({ key: [...l.classList].find((c) => c !== 'bd-lane'), cards: [...l.querySelectorAll('.bd-card')].map((c) => c.dataset.card) }));
    const body = document.querySelector('.bd-lanes');
    return { lanes, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, lanesWide: body.scrollWidth > body.clientWidth + 1 };
  });
  expect(r.lanes.map((l) => l.key).join(',') === 'backlog,agent,wait,me,done', `lanes are ${r.lanes.map((l) => l.key).join(',')}`);
  const lane = (k) => r.lanes.find((l) => l.key === k).cards;
  expect(lane('me').includes('sess-login'), 'the finished, unreviewed session should need you');
  expect(lane('done').includes('sess-tests'), 'the approved session should be done');
  expect(lane('backlog').includes('sess-empty'), 'the session with an intent and no turns should be in the backlog');
  expect(!r.lanes.some((l) => l.cards.includes('sess-site')), 'a plain conversation at rest is not a card');
  expect(r.overflow === 0, `the page scrolls sideways by ${r.overflow}px`);
  expect(await page.locator('.bd-card[data-card="sess-login"] .bd-chip', { hasText: 'To review' }).isVisible(), 'the review card has no chip');
});

test('workboard: approving a review card commits it and moves it to Done', async ({ page }) => {
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.click('.bd-card[data-card="sess-login"]');
  await page.waitForSelector('.bd-panel', { timeout: 5000 });
  expect(await page.locator('.bd-panel-title', { hasText: 'Fix the login redirect bug' }).isVisible(), 'the panel does not show the chosen card');
  expect(await page.locator('.bd-ledger .bd-ev').count() >= 2, 'the ledger should list the intent and the turn');
  await page.locator('.bd-actions .btn.primary', { hasText: 'Approve' }).click();
  await page.waitForSelector('.bd-lane.done .bd-card[data-card="sess-login"]', { timeout: 5000 });
  expect(await page.locator('.bd-lane.me .bd-card[data-card="sess-login"]').count() === 0, 'the card is still in Needs you');
  // the sidebar's Needs you section follows the same rule
  expect(await page.locator('.side-section-label', { hasText: 'Needs you' }).count() === 0, 'the sidebar still says something needs you');
});

test('workboard: a permission waiting on a card can be allowed from the panel', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 40 });
  await ctl.call('session.send', { id: 'sess-empty', text: 'please ask permission before running a command' });
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.waitForSelector('.bd-card.prompt', { timeout: 15_000 });
  const card = page.locator('.bd-card.prompt').first();
  const lane = await page.locator('.bd-lane.me').boundingBox();
  const box = await card.boundingBox();
  expect(box.x >= lane.x - 1 && box.x + box.width <= lane.x + lane.width + 1, 'the card leaves its lane');
  await card.click();
  await page.waitForSelector('.bd-perm-tool', { timeout: 5000 });
  expect(await page.locator('.bd-perm-tool', { hasText: 'Bash' }).isVisible(), 'the panel does not name the tool');
  await page.locator('.bd-perm-row .btn.primary').click(); // Allow
  await page.waitForSelector('.bd-card.prompt', { state: 'detached', timeout: 15_000 });
  await page.waitForSelector('.bd-lane.agent .bd-card[data-card="sess-empty"], .bd-lane.me .bd-card[data-card="sess-empty"]', { timeout: 15_000 });
});

test('workboard: a card written in the backlog stays there; Ctrl+Enter hands it to Claude', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 0 });
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.waitForSelector('.bd-lanes', { timeout: 5000 });
  await page.keyboard.press('n');
  await page.waitForSelector('.bd-new', { timeout: 3000 });
  await page.locator('.bd-new-title').fill('Rename the config file');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.bd-new', { state: 'detached', timeout: 5000 });
  const made = await page.locator('.bd-lane.backlog .bd-card-title', { hasText: 'Rename the config file' }).count();
  expect(made === 1, 'the card is not in the backlog');
  expect(await page.locator('.board-page').count() === 1, 'writing a card must not leave the board');
  await page.keyboard.press('n');
  await page.locator('.bd-new-title').fill('Look at the parser');
  await page.locator('.bd-new-intent').fill('Read the parser and fix the unrecoverable branch.');
  await page.keyboard.press('Control+Enter');
  await page.waitForSelector('.bd-lane.agent .bd-card-title:has-text("Look at the parser"), .bd-lane.me .bd-card-title:has-text("Look at the parser")', { timeout: 15_000 });
});

test('workboard: a finished turn shows its handoff on the card and in the panel', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 0 });
  await ctl.call('session.send', { id: 'sess-empty', text: 'look at the parser and fix it' });
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.waitForSelector('.bd-lane.me .bd-card[data-card="sess-empty"]', { timeout: 15_000 });
  await sleep(300);
  const line = await page.locator('.bd-card[data-card="sess-empty"] .bd-card-line').textContent();
  expect(line.includes('Is a comment enough'), `the card line should carry the Decide question, got: ${line}`);
  await page.click('.bd-card[data-card="sess-empty"]');
  await page.waitForSelector('.bd-report', { timeout: 5000 });
  const dd = await page.locator('.bd-report dd').allTextContents();
  expect(dd.some((d) => d.includes('Added one comment')), 'the panel should show what was done');
  expect(dd.some((d) => d.includes('partialJson.ts')), 'the panel should list the changed file');
});

test('workboard: a backlog card can wait for a time, then be woken from the panel', async ({ page }) => {
  await ctl.call('mock.pace', { ms: 0 });
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.click('.bd-card[data-card="sess-empty"]');
  await page.waitForSelector('.bd-panel', { timeout: 5000 });
  await page.keyboard.press('w');
  await page.waitForSelector('.bd-waitpick', { timeout: 3000 });
  expect(await page.locator('.bd-waitopt[data-k="1"]').isDisabled(), 'CI should be offered but off when gh is missing');
  await page.keyboard.press('2'); // tomorrow 09:00
  await page.waitForSelector('.bd-lane.wait .bd-card[data-card="sess-empty"]', { timeout: 5000 });
  const line = await page.locator('.bd-card[data-card="sess-empty"] .bd-card-line').textContent();
  expect(line.startsWith('Until'), `the card should say what it waits for, got: ${line}`);
  await page.click('.bd-card[data-card="sess-empty"]');
  await page.locator('.bd-actions .btn.primary', { hasText: 'Wake now' }).click();
  await page.waitForSelector('.bd-lane.agent .bd-card[data-card="sess-empty"], .bd-lane.me .bd-card[data-card="sess-empty"]', { timeout: 15_000 });
  expect(await page.locator('.bd-lane.wait .bd-card[data-card="sess-empty"]').count() === 0, 'the card is still waiting');
});

test('workboard: the Waiting lane menu lists schedules and opens the dialog for a new one', async ({ page }) => {
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.click('.bd-lane.wait .bd-lane-menu');
  await page.locator('.menu-item:has-text("New schedule")').click();
  await page.waitForSelector('.sd', { timeout: 5000 });
  await page.keyboard.press('Escape');
  await page.waitForSelector('.sd', { state: 'detached', timeout: 3000 });
  expect(await page.locator('.side-page', { hasText: 'Inbox' }).count() === 0, 'the Inbox page should be gone from the sidebar');
});

test('workboard: dragging a review card onto Done approves it; onto Needs you does nothing', async ({ page }) => {
  await page.locator('.side-page', { hasText: 'Workboard' }).click();
  await page.waitForSelector('.bd-card[data-card="sess-login"]', { timeout: 5000 });
  // a lane that takes no drop is dimmed while the drag is on; the card stays where it was
  await page.dragAndDrop('.bd-card[data-card="sess-empty"]', '.bd-lane.me .bd-cards');
  await sleep(300);
  expect(await page.locator('.bd-lane.backlog .bd-card[data-card="sess-empty"]').count() === 1, 'a backlog card must not land in Needs you');
  await page.dragAndDrop('.bd-card[data-card="sess-login"]', '.bd-lane.done .bd-cards');
  await page.waitForSelector('.bd-lane.done .bd-card[data-card="sess-login"]', { timeout: 5000 });
  expect(await page.locator('.bd-lane.no').count() === 0, 'lanes should not stay dimmed after the drop');
});

// ---------------------------------------------------------------- run

const chosen = tests.filter((t) => !grep || t.name.includes(grep));
console.log(`Pilcrow browser tests (${chosen.length} of ${tests.length})`);
for (const t of chosen) {
  const started = Date.now();
  let ctx;
  try {
    await ctl.call('mock.reset');
    ctx = await fresh();
    await t.fn(ctx);
    results.push({ name: t.name, ok: true });
    console.log(`  ✓ ${t.name}  (${Date.now() - started}ms)`);
  } catch (e) {
    results.push({ name: t.name, ok: false, error: e });
    const file = join(OUT, `${t.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png`);
    try {
      await ctx?.page.screenshot({ path: file, fullPage: false });
    } catch {}
    console.log(`  ✗ ${t.name}\n      ${e.message.split('\n').join('\n      ')}\n      screenshot: ${file}`);
  } finally {
    await ctx?.context.close().catch(() => {});
  }
}
await browser.close();
ctl.close();
mock.kill();
const failed = results.filter((r) => !r.ok);
console.log(failed.length ? `\n${failed.length} of ${results.length} failed` : `\nall ${results.length} passed`);
process.exit(failed.length ? 1 : 0);
