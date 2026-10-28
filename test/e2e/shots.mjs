#!/usr/bin/env node
// Reference screenshots of the web interface, one per scene the Qt client also has (qt/tests/snap.py,
// qt/tests/panel_probe.py), at the same window size, for comparing the two side by side.
//
//   node test/e2e/shots.mjs [--size 1480x940] [--out test/out]
//
// Needs web/dist (npm run build) and a browser the way test/e2e/run.mjs does. Writes test/out/web-*.png.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const args = process.argv.slice(2);
const arg = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const OUT = resolve(ROOT, arg('--out', join('test', 'out')));
const [W, H] = arg('--size', '1480x940').split('x').map(Number);
const PORT = Number(process.env.MOCK_PORT || 5331);
const URL_ = `http://127.0.0.1:${PORT}/`;

if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) {
  console.error('web/dist is missing: run `npm run build` first');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '400' }, stdio: ['ignore', 'pipe', 'inherit'] });
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
  if (process.env.PILCROW_CHROME) return { executablePath: process.env.PILCROW_CHROME };
  try {
    if (existsSync(chromium.executablePath())) return {};
  } catch {}
  const local = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  const p = local.find((x) => existsSync(x));
  if (p) return { executablePath: p };
  console.error('no browser: npx playwright install chromium, or set PILCROW_CHROME');
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch({ headless: true, ...findBrowser() });
const context = await browser.newContext({ viewport: { width: W, height: H }, colorScheme: 'dark', reducedMotion: 'no-preference' });
await context.addInitScript(() => localStorage.setItem('pilcrow.theme', 'dark'));
const page = await context.newPage();
page.on('pageerror', (e) => console.error('   page error:', e.message));

const shot = async (name) => {
  const file = join(OUT, `web-${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`screenshot ${file}`);
};
async function open(sid) {
  await page.click(`[data-sid="${sid}"] .side-row-main`);
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0 || document.querySelector('.session-empty'), null, { timeout: 15_000 });
  await sleep(700);
}
async function send(text) {
  const ta = page.locator('.composer-input');
  await ta.click();
  await ta.fill(text);
  await ta.press('Enter');
}
const turnDone = (timeout = 30_000) => page.waitForFunction(() => !document.querySelector('.session-head .status-pill') && !document.querySelector('.work.live'), null, { timeout });
const key = async (k, wait = 500) => {
  await page.keyboard.press(k);
  await sleep(wait);
};

try {
  await page.goto(URL_);
  await page.waitForSelector('[data-sid]', { timeout: 15_000 });
  await sleep(1200);
  await shot('welcome');

  await open('sess-login');
  await shot('session');
  const tabs = ['changes', 'files', 'terminal', 'preview', 'tasks', 'history', 'info'];
  for (let i = 0; i < tabs.length; i++) {
    await key(`Alt+${i + 1}`, 900);
    await shot(`tab-${tabs[i]}`);
  }
  await key('Control+Backslash', 700);
  await shot('panel-closed');

  await key('Control+f', 500);
  await page.locator('.findbar input').fill('redirect');
  await sleep(600);
  await shot('find');
  await key('Escape', 400);

  await key('Control+Shift+O', 900);
  await shot('overview');
  await page.click('button[title="Results of scheduled sessions"]');
  await sleep(900);
  await shot('inbox');
  await open('sess-login');

  await key('Control+,', 900);
  await shot('settings');
  await key('Escape', 500);
  await key('Control+Backslash', 700);
  await shot('palette');
  await key('Escape', 500);
  await key('Control+Shift+N', 900);
  await shot('new-session-dialog');
  await key('Escape', 500);
  await key('Control+Backslash', 700);
  await shot('shortcuts');
  await key('Escape', 500);

  await open('sess-long');
  await shot('long');

  await open('sess-empty');
  await send('please look at the parser and fix the comment');
  await sleep(1200);
  await shot('turn-live');
  await turnDone();
  await sleep(400);
  await shot('turn-done');

  await open('sess-tests');
  await send('please ask permission before you run a command');
  await sleep(2500);
  await shot('permission');

  await page.evaluate(() => localStorage.setItem('pilcrow.theme', 'light'));
  await page.emulateMedia({ colorScheme: 'light' });
  await page.reload();
  await page.waitForSelector('[data-sid]', { timeout: 15_000 });
  await sleep(800);
  await open('sess-login');
  await shot('light');
  await key('Control+,', 900);
  await shot('settings-light');
} finally {
  await browser.close();
  mock.kill();
}
