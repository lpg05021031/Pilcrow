#!/usr/bin/env node
// Keyboard and screen-reader checks: the built interface, served by the mock server, driven in headless
// Chromium with the keyboard alone, its accessibility tree read through the DevTools protocol (the
// names Chrome itself computes), and smoke runs in the light theme and with reduced motion.
//
//   MOCK_PORT=5331 node test/e2e/ux.mjs [--grep text] [--headed]     (npm run test:ux; needs a build)
//
// Failures leave a screenshot in test/out/.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import WebSocket from 'ws';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const OUT = join(ROOT, 'test', 'out');
// Never the mock's default port: a developer's own mock server may be running there.
const PORT = Number(process.env.MOCK_PORT || 5331);
const URL_ = `http://127.0.0.1:${PORT}/`;
const args = process.argv.slice(2);
const grep = args.includes('--grep') ? args[args.indexOf('--grep') + 1] : '';

if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) {
  console.error('web/dist is missing: run `npm run build` first');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '400' }, stdio: ['ignore', 'pipe', 'inherit'] });
mock.stdout.on('data', () => {});
process.on('exit', () => mock.kill());
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

function findBrowser() {
  if (process.env.PILCROW_CHROME) return { executablePath: process.env.PILCROW_CHROME };
  try {
    if (existsSync(chromium.executablePath())) return {};
  } catch {}
  const p = ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find((x) => existsSync(x));
  if (p) return { executablePath: p };
  console.error('no browser: npx playwright install chromium, or set PILCROW_CHROME');
  process.exit(2);
}
const browser = await chromium.launch({ headless: !args.includes('--headed'), ...findBrowser() });

/**
 * Wait until the page holds still: no change to the DOM and no finite animation running, for five
 * frames in a row, counted from now (at most `timeout`). What a fixed pause stood in for, measured.
 */
async function settled(page, timeout = 8000) {
  await page.evaluate(() => {
    const w = window;
    if (!w.__still) {
      w.__still = { n: 0, dirty: false };
      new MutationObserver(() => (w.__still.dirty = true)).observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      const tick = () => {
        const s = w.__still;
        const moving = document.getAnimations().some((a) => a.playState === 'running' && a.effect?.getComputedTiming().iterations !== Infinity);
        s.n = s.dirty || moving ? 0 : s.n + 1;
        s.dirty = false;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
    w.__still.n = 0;
  });
  await page.waitForFunction(() => window.__still.n >= 5, null, { timeout, polling: 30 });
}

async function fresh(opts = {}) {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, reducedMotion: 'no-preference', ...opts });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('   page error:', e.message));
  await page.goto(URL_);
  await page.waitForSelector('.sidebar .side-row', { timeout: 20_000 });
  return { context, page };
}
async function open(page, sid) {
  await page.click(`[data-sid="${sid}"] .side-row-main`);
  await page.waitForFunction(() => document.querySelector('.transcript .units')?.children.length > 0 || document.querySelector('.session-empty'), null, { timeout: 15_000 });
  await settled(page);
}
function expect(cond, what) {
  if (!cond) throw new Error(what);
}
/** What has the focus: its role, its accessible name (Chrome's own), and whether it is inside `within`. */
const focused = (page, within) =>
  page.evaluate((within) => {
    const a = document.activeElement;
    return { tag: a?.tagName, role: a?.getAttribute('role'), id: a?.id, label: a?.getAttribute('aria-label') || a?.textContent?.trim().slice(0, 40), inside: Boolean(within && a?.closest(within)) };
  }, within);

/**
 * Controls Chrome exposes without a name, from the full accessibility tree. The roles are the ones a
 * person operates; a name is what a screen reader says for them (aria-label, a <label>, the text).
 */
const OPERATED = new Set(['button', 'textbox', 'searchbox', 'combobox', 'checkbox', 'switch', 'radio', 'tab', 'menuitem', 'menuitemradio', 'option', 'slider', 'spinbutton', 'link']);
async function unnamed(page) {
  const cdp = await page.context().newCDPSession(page);
  try {
    const { nodes } = await cdp.send('Accessibility.getFullAXTree');
    const out = [];
    for (const n of nodes) {
      if (n.ignored || !OPERATED.has(n.role?.value) || (n.name?.value || '').trim()) continue;
      const { node } = await cdp.send('DOM.describeNode', { backendNodeId: n.backendDOMNodeId });
      const attrs = node.attributes || [];
      out.push(`${n.role.value} <${node.localName} class="${attrs[attrs.indexOf('class') + 1] || ''}">`);
    }
    return out;
  } finally {
    await cdp.detach();
  }
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test('a dialog keeps Tab and Shift+Tab inside, names itself by its title, and gives the focus back on Esc', async ({ page }) => {
  await open(page, 'sess-login');
  await page.locator('.composer-input').focus();
  await page.keyboard.press('Control+Shift+N');
  await page.waitForSelector('.dialog');
  const dialog = page.getByRole('dialog', { name: 'New session' });
  expect(await dialog.count(), 'the dialog is named by its title');
  expect((await focused(page, '.dialog')).inside, 'the focus starts inside the dialog');
  expect(await page.evaluate(() => document.querySelector('#root').inert), 'the app behind is inert');
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press('Tab');
    expect((await focused(page, '.dialog')).inside, `Tab ${i + 1} stays in the dialog`);
  }
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press('Shift+Tab');
    expect((await focused(page, '.dialog')).inside, `Shift+Tab ${i + 1} stays in the dialog`);
  }
  await page.keyboard.press('Escape');
  await page.waitForSelector('.scrim:not([data-ghost])', { state: 'detached' });
  const back = await focused(page);
  expect(back.tag === 'TEXTAREA' && back.label === 'Message to Claude', `the focus is back in the message box (${JSON.stringify(back)})`);
  expect(!(await page.evaluate(() => document.querySelector('#root').inert)), 'the app is awake again');
});

test('an informational sheet starts on itself; Esc in a menu over a dialog closes the menu only', async ({ page }) => {
  await page.keyboard.press('Control+/');
  await page.waitForSelector('.dialog');
  const f = await focused(page, '.dialog');
  expect(f.inside && f.role === 'dialog', `the Keyboard shortcuts sheet holds the focus itself (${JSON.stringify(f)})`);
  expect(await page.getByRole('dialog', { name: 'Keyboard shortcuts' }).count(), 'named by its title');
  await page.keyboard.press('Escape');
  await page.waitForSelector('.scrim:not([data-ghost])', { state: 'detached' });

  await page.keyboard.press('Control+,');
  await page.waitForSelector('.settings');
  await page.getByRole('button', { name: 'Claude Code' }).click();
  const effort = page.getByRole('button', { name: 'Default effort' });
  await effort.focus();
  await page.keyboard.press('Enter');
  await page.waitForSelector('.popover [role="menu"]');
  await page.waitForFunction(() => document.activeElement?.closest('[role="menu"]'), null, { timeout: 2000 }).catch(() => {});
  let at = await focused(page);
  expect(at.role === 'menuitemradio', `the menu takes the focus, on the chosen item (${JSON.stringify(at)})`);
  await page.keyboard.press('ArrowDown');
  const next = await focused(page);
  expect(next.role === 'menuitemradio' && next.label !== at.label, `ArrowDown moves the focus to the next item (${at.label} → ${next.label})`);
  expect(await page.locator('.menu-item.active').count(), 'the keyboard lights the row it moved to');
  await page.keyboard.press('Escape');
  await page.waitForSelector('.popover [role="menu"]', { state: 'detached', timeout: 3000 });
  await settled(page);
  expect(await page.locator('.settings').count(), 'Esc closed the menu, not Settings');
  at = await focused(page);
  expect(at.label === 'Default effort' || at.role === null, `the focus is back on the menu's button (${JSON.stringify(at)})`);
  expect((await page.locator('.select-btn[aria-haspopup="menu"]').count()) > 0, 'a Select says it opens a menu');
});

test('the command palette is a combobox: the arrows move the active option it points at', async ({ page }) => {
  await open(page, 'sess-login');
  await page.keyboard.press('Control+k');
  const box = page.getByRole('combobox', { name: 'Search sessions and commands' });
  await box.waitFor();
  expect(await page.getByRole('dialog', { name: 'Command palette' }).count(), 'the palette is a named dialog');
  const active = () => page.evaluate(() => {
    const input = document.activeElement;
    const id = input.getAttribute('aria-activedescendant');
    const opt = id && document.getElementById(id);
    return { id, role: opt?.getAttribute('role'), selected: opt?.getAttribute('aria-selected'), text: opt?.textContent?.trim().slice(0, 30), controls: input.getAttribute('aria-controls') && document.getElementById(input.getAttribute('aria-controls'))?.getAttribute('role') };
  });
  const a = await active();
  expect(a.role === 'option' && a.selected === 'true' && a.controls === 'listbox', `the input points at a selected option of its listbox (${JSON.stringify(a)})`);
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  const b = await active();
  expect(b.id !== a.id && b.selected === 'true', `ArrowDown moves the active option (${a.text} → ${b.text})`);
  await page.keyboard.press('Escape');
});

test('the panel tabs: Alt+1…7 pick a tab, Left and Right move along them, the panel is labelled by the tab', async ({ page }) => {
  await open(page, 'sess-login');
  const names = ['Changes', 'Files', 'Terminal', 'Preview', 'Tasks', 'Timeline', 'Session'];
  for (let i = 0; i < names.length; i++) {
    await page.locator('.composer-input').focus();
    await page.keyboard.press(`Alt+${i + 1}`);
    const tab = page.getByRole('tab', { name: names[i] });
    await page.waitForFunction((n) => [...document.querySelectorAll('[role="tab"]')].some((t) => t.getAttribute('aria-selected') === 'true' && t.textContent.includes(n)), names[i]);
    const panel = await page.evaluate(() => {
      const p = document.querySelector('[role="tabpanel"]');
      return document.getElementById(p?.getAttribute('aria-labelledby'))?.textContent;
    });
    expect(panel?.includes(names[i]), `Alt+${i + 1}: the tabpanel is labelled by ${names[i]} (${panel})`);
    expect((await tab.getAttribute('aria-controls')) === (await page.locator('[role="tabpanel"]').getAttribute('id')), 'the tab controls the panel');
  }
  await page.getByRole('tab', { name: 'Changes' }).click();
  await page.getByRole('tab', { name: 'Changes' }).focus();
  await page.keyboard.press('ArrowRight');
  let f = await focused(page);
  expect(f.role === 'tab' && f.label.includes('Files'), `ArrowRight moves to Files (${JSON.stringify(f)})`);
  expect((await page.getByRole('tab', { name: 'Files' }).getAttribute('aria-selected')) === 'true', 'and shows it');
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  f = await focused(page);
  expect(f.label.includes('Session'), `ArrowLeft wraps round to the last tab (${JSON.stringify(f)})`);
  expect((await page.locator('[role="tab"][tabindex="0"]').count()) === 1, 'one tab stop for the tab list');
});

test('every control on the main screens has a name a screen reader can say', async ({ page }) => {
  const problems = [];
  const scan = async (where) => {
    for (const p of await unnamed(page)) problems.push(`${where}: ${p}`);
  };
  await scan('welcome');
  await open(page, 'sess-login');
  expect(await page.getByRole('textbox', { name: 'Message to Claude' }).count(), 'the message box keeps its name whatever its placeholder says');
  expect(await page.getByRole('button', { name: 'Attach images or files' }).count(), 'an icon button is named');
  await scan('session');
  for (const k of [1, 2, 5, 6, 7]) {
    await page.keyboard.press(`Alt+${k}`);
    await settled(page);
    await scan(`panel tab ${k}`);
  }
  await page.keyboard.press('Control+,');
  await page.waitForSelector('.settings');
  for (const t of ['General', 'Repositories', 'Claude Code', 'Skills', 'MCP servers', 'Memory', 'Diagnostics']) {
    await page.click(`.settings-nav button:text-is("${t}")`);
    await settled(page);
    await scan(`settings ${t}`);
  }
  await page.keyboard.press('Escape');
  await page.waitForSelector('.settings', { state: 'detached', timeout: 3000 });
  await page.keyboard.press('Control+Shift+N');
  await page.waitForSelector('.dialog');
  await page.click('.ns-disclose');
  await settled(page);
  await scan('new session');
  await page.keyboard.press('Escape');
  await page.waitForSelector('.dialog', { state: 'detached', timeout: 3000 });
  await page.keyboard.press('Control+Shift+O');
  await page.waitForSelector('.ov-stats', { timeout: 5000 });
  await settled(page);
  await scan('control room');
  await page.click('.side-page:has-text("Inbox")');
  await page.waitForSelector('.inbox-rail-h', { timeout: 5000 });
  await settled(page);
  await scan('inbox');
  await page.click('.inbox-rail-h button:has-text("New")');
  await page.waitForSelector('.dialog');
  await scan('schedule');
  expect(!problems.length, `unnamed controls:\n${problems.join('\n')}`);
});

test('light theme: no surface is left dark', async ({ page }) => {
  await ctl.call('config.set', { theme: 'light' });
  await page.reload();
  await page.waitForSelector('.sidebar .side-row');
  await open(page, 'sess-login');
  expect((await page.evaluate(() => document.documentElement.dataset.theme)) === 'light', 'the light theme is on');
  const dark = [];
  const sample = async (where) => {
    const rows = await page.evaluate(() => {
      const lum = (c) => {
        const m = c.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const [r, g, b, a = 1] = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
        return a < 0.3 ? null : (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      };
      return ['.app', '.sidebar', '.main', '.session', '.composer', '.panel', '.popover', '.dialog', '.toast', '.findbar', '.ovc', '.msg-bubble', '.md-code', 'kbd']
        .flatMap((s) => [...document.querySelectorAll(s)].slice(0, 3).map((el) => ({ s, l: lum(getComputedStyle(el).backgroundColor) })))
        .filter((x) => x.l !== null);
    });
    for (const r of rows) if (r.l < 0.5) dark.push(`${where}: ${r.s} ${r.l.toFixed(2)}`);
    return rows.length;
  };
  let n = await sample('session');
  await page.keyboard.press('Alt+1');
  await settled(page);
  n += await sample('changes');
  await page.locator('.composer .cbtn.mode').click();
  await page.waitForSelector('.popover');
  n += await sample('menu');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+,');
  await page.waitForSelector('.settings');
  n += await sample('settings');
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.__pilcrowToast('A light toast', 'info'));
  await page.waitForSelector('.toast', { timeout: 3000 });
  await settled(page);
  n += await sample('toast');
  await page.keyboard.press('Control+Shift+O');
  await page.waitForSelector('.ov-stats', { timeout: 5000 });
  await settled(page);
  n += await sample('control room');
  expect(n > 20, `sampled enough surfaces (${n})`);
  expect(!dark.length, `dark surfaces in the light theme:\n${dark.join('\n')}`);
});

test('reduced motion: nothing waits, menus and dialogs open at once, the app still works', async () => {
  const { context, page } = await fresh({ reducedMotion: 'reduce' });
  try {
    await open(page, 'sess-login');
    await page.locator('.composer .cbtn.mode').click();
    await page.waitForSelector('.menu-item');
    const timing = await page.evaluate(() =>
      [...document.querySelectorAll('.menu-item')].map((el) => {
        const cs = getComputedStyle(el);
        return [parseFloat(cs.animationDelay), parseFloat(cs.animationDuration)];
      }),
    );
    expect(timing.length >= 4 && timing.every(([d, t]) => d === 0 && t < 0.01), `menu rows neither wait nor play (${JSON.stringify(timing)})`);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+Shift+O');
    await page.waitForSelector('.ov-stats', { timeout: 5000 });
    await settled(page);
    const inline = await page.evaluate(() => [...document.querySelectorAll('[style*="animation-delay"]')].map((el) => getComputedStyle(el).animationDelay));
    expect(inline.every((d) => parseFloat(d) === 0), `inline stagger delays are cancelled (${inline.join(', ')})`);
    await page.keyboard.press('Control+k');
    await page.waitForSelector('.palette');
    const sheet = await page.evaluate(() => getComputedStyle(document.querySelector('.dialog')).animationDuration);
    expect(parseFloat(sheet) < 0.01, `the sheet does not animate (${sheet})`);
    await page.keyboard.press('Escape');
  } finally {
    await context.close();
  }
});

// ---------------------------------------------------------------- run

const chosen = tests.filter((t) => !grep || t.name.includes(grep));
console.log(`Pilcrow keyboard and accessibility tests (${chosen.length} of ${tests.length})`);
let failed = 0;
for (const t of chosen) {
  const started = Date.now();
  let ctx;
  try {
    await ctl.call('mock.reset');
    ctx = await fresh();
    await t.fn(ctx);
    console.log(`  ✓ ${t.name}  (${Date.now() - started}ms)`);
  } catch (e) {
    failed++;
    const file = join(OUT, `ux-${t.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 60)}.png`);
    await ctx?.page.screenshot({ path: file }).catch(() => {});
    console.log(`  ✗ ${t.name}\n      ${e.message.split('\n').join('\n      ')}\n      screenshot: ${file}`);
  } finally {
    await ctx?.context.close().catch(() => {});
  }
}
await browser.close();
ctl.close();
console.log(failed ? `\n${failed} of ${chosen.length} failed` : `\nall ${chosen.length} passed`);
process.exit(failed ? 1 : 0);
