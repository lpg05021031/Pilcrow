// The command line around the server, for the shells that are not Electron: find a Pilcrow already
// running (and retire one an update left behind), open a Chrome/Chromium app window or the browser on
// it, or start the server; and `--doctor`, the plain-text health check. main.js hands over to cli()
// when it is run as a program.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { store, CONFIG_DIR } from './config.js';
import { resolveClaude } from './claude.js';
import { gatewayModels } from './models.js';
import * as term from './terminal.js';
import { start, gatewayInfo, openExternal, ROOT, DIST, LOCK } from './main.js';
import { which, log, xdg, HOME, IS_ROOT, IS_WIN, slash, readJson, privateDir, VERSION } from './util.js';

/** `--name=value` and `--flag` (true); anything else is not an option. */
export function parseArgs(argv) {
  const out = {};
  for (const a of argv) {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    if (m) out[m[1]] = m[2] ?? true;
  }
  return out;
}

const BROWSERS = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'microsoft-edge-stable', 'brave-browser', 'vivaldi'];

/** Chrome first, then Edge (always present on Windows 10/11), then other Chromium browsers. */
function findWindowsBrowser() {
  const bases = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA].flatMap((b) => (b ? [b] : []));
  const rel = ['Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe', 'Chromium/Application/chrome.exe', 'BraveSoftware/Brave-Browser/Application/brave.exe'];
  for (const r of rel) {
    for (const b of bases) {
      const p = path.join(b, r);
      if (fs.existsSync(p)) return slash(p);
    }
  }
  return which('chrome') || which('msedge');
}

function findChrome() {
  if (process.env.PILCROW_CHROME) return which(process.env.PILCROW_CHROME);
  if (IS_WIN) return findWindowsBrowser();
  for (const b of BROWSERS) {
    const p = which(b);
    if (p) return p;
  }
  for (const p of ['/opt/google/chrome/chrome', '/snap/bin/chromium', '/usr/lib/chromium/chromium']) if (fs.existsSync(p)) return p;
  return null;
}

/** The Pilcrow server already running for this user, if any. */
export async function existingInstance() {
  const lock = readJson(LOCK, null);
  if (!lock?.pid || !lock.port) return null;
  try {
    process.kill(lock.pid, 0);
  } catch {
    return null;
  }
  try {
    const r = await fetch(`http://127.0.0.1:${lock.port}/api/hello`, { signal: AbortSignal.timeout(1500) });
    const j = /** @type {{ app?: string, pid?: number, version?: string, busy?: number }} */ (await r.json());
    if (j.app === 'pilcrow' && j.pid === lock.pid) return { ...lock, version: j.version || '?', busy: typeof j.busy === 'number' ? j.busy : null, url: `http://127.0.0.1:${lock.port}/` };
  } catch {
    /* stale lock */
  }
  return null;
}

/**
 * After an update, the previous version can still be running in the background; a new window would then
 * talk to old code. Stop it when that is safe: it reports no busy sessions, or it predates busy reporting
 * (only possible right after upgrading from 1.2.2 or older). Returns true when the old process is gone.
 */
export async function retireOutdated(existing, { force = false } = {}) {
  if (!existing || existing.version === VERSION) return false;
  if (!force && existing.busy) return false;
  try {
    process.kill(existing.pid, 'SIGTERM');
  } catch {
    return true;
  }
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 100));
    try {
      process.kill(existing.pid, 0);
    } catch {
      return true;
    }
  }
  try {
    process.kill(existing.pid, 'SIGKILL');
  } catch {
    /* already gone */
  }
  return true;
}

/** The snap a browser binary belongs to (Ubuntu ships Firefox and Chromium as snaps), or null. */
export function snapOf(bin) {
  if (!bin) return null;
  const name = path.basename(bin).split('.')[0];
  if (bin.startsWith('/snap/bin/')) return name;
  try {
    if (fs.realpathSync(bin) === '/usr/bin/snap') return name;
    // Ubuntu's /usr/bin/chromium-browser is a small script that runs /snap/bin/chromium.
    if (fs.statSync(bin).size < 16384) {
      const m = fs.readFileSync(bin, 'utf8').match(/\/snap\/bin\/([\w-]+)/);
      if (m) return m[1];
    }
  } catch {
    /* not readable: assume a normal package */
  }
  return null;
}

function openChromeWindow(chrome, url) {
  const snap = snapOf(chrome);
  const common = snap && path.join(HOME, 'snap', snap, 'common');
  // A snap browser cannot use a profile in a hidden folder; give it one inside its own snap folder.
  // On Windows the profile stays in %LOCALAPPDATA% so it never roams with the user profile.
  const profile = common ? (fs.existsSync(common) ? privateDir(path.join(common, 'pilcrow-profile')) : null) : privateDir(path.join(xdg(IS_WIN ? 'data' : 'config'), 'chrome-profile'));
  const win = store.getConfig().window || {};
  const args = [`--app=${url}`, '--class=pilcrow', '--name=pilcrow', '--no-first-run', '--no-default-browser-check', '--disable-features=Translate,MediaRouter', `--window-size=${win.width || 1480},${win.height || 940}`];
  if (profile) args.push(`--user-data-dir=${profile}`);
  if (IS_ROOT) args.push('--no-sandbox');
  if (process.env.PILCROW_CHROME_FLAGS) args.push(...process.env.PILCROW_CHROME_FLAGS.split(' ').filter(Boolean));
  // Chromium builds without Google keys show a warning bar; these values silence it (no Google services are used).
  const env = { ...process.env, GOOGLE_API_KEY: 'no', GOOGLE_DEFAULT_CLIENT_ID: 'no', GOOGLE_DEFAULT_CLIENT_SECRET: 'no' };
  return spawn(chrome, args, { stdio: 'ignore', env });
}

// `pilcrow --doctor`: plain-text health check for the environment Pilcrow runs in.
/** Git for Windows' bash.exe, which Claude Code uses for its Bash tool on Windows. */
function gitBash() {
  if (!IS_WIN) return null;
  const env = process.env.CLAUDE_CODE_GIT_BASH_PATH;
  if (env && fs.existsSync(env)) return slash(env);
  const git = which('git');
  const candidates = [
    git && path.join(path.dirname(path.dirname(git)), 'bin', 'bash.exe'),
    ...[process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs')].flatMap((b) => (b ? [path.join(b, 'Git', 'bin', 'bash.exe')] : [])),
  ].filter(Boolean);
  for (const c of candidates) if (fs.existsSync(c)) return slash(c);
  return null;
}

async function doctor() {
  const tty = process.stdout.isTTY;
  const paint = (c, s) => (tty ? `\x1b[${c}m${s}\x1b[0m` : s);
  const ok = (label, detail = '') => console.log(`  ${paint('32', '✓')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  const warn = (label, detail = '') => console.log(`  ${paint('33', '!')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  let failures = 0;
  const bad = (label, detail = '') => {
    failures++;
    console.log(`  ${paint('31', '✗')} ${label}${detail ? paint('2', '  ' + detail) : ''}`);
  };
  console.log(paint('1', `Pilcrow ${VERSION} doctor`) + paint('2', `  (${ROOT})`));

  console.log(paint('1', '\nRuntime'));
  const major = Number(process.versions.node.split('.')[0]);
  (major >= 20 ? ok : bad)(`Node ${process.version}`, process.execPath);
  fs.existsSync(path.join(DIST, 'index.html')) ? ok('Interface', path.join('web', 'dist')) : bad('Interface missing', 'run the installer again (it ships the built interface)');
  which('git') ? ok('git', which('git')) : warn('git not found', 'Changes, worktrees and commits are disabled');
  const caps = term.capabilities();
  if (IS_WIN) caps.conpty ? ok('Terminal', 'ConPTY (node-pty)') : warn('Terminal is basic (line mode)', 'node-pty was not installed; full-screen programs will not work');
  else caps.python ? ok('Terminal', `python3 PTY bridge (${caps.python})`) : caps.script ? warn('Terminal uses `script` fallback', 'install python3 for a full PTY') : warn('Terminal unavailable', 'needs python3 or util-linux script');

  console.log(paint('1', '\nClaude Code'));
  const cfg = store.getConfig();
  const claude = await resolveClaude(cfg.claudeBinary);
  if (claude.path) ok(`Claude Code ${claude.version || '(version unknown)'}`, `${claude.source}: ${claude.path}`);
  else bad('Claude Code not found', 'reinstall Pilcrow, or install `claude` on PATH');
  if (claude.system && claude.source === 'bundled') ok(`Also on PATH: Claude Code ${claude.system.version || ''}`.trim(), `${claude.system.path} (switch in Settings → Claude Code)`);
  if (IS_WIN) {
    const bash = gitBash();
    bash ? ok('Git Bash (for the Bash tool)', bash) : warn('No Git for Windows', 'Claude Code runs commands with PowerShell instead; install Git for Windows for the Bash tool');
  }
  const gw = await gatewayInfo(process.cwd());
  if (gw.error) warn('Could not read Claude Code settings', gw.error);
  else {
    if (gw.baseUrl) ok('API endpoint', `${gw.baseUrl}${gw.source ? ` (from ${gw.source} settings)` : ' (from environment)'}`);
    else ok('API endpoint', 'api.anthropic.com (default)');
    gw.hasToken ? ok('Credentials configured') : warn('No API key or token in settings/env', 'fine if you log in with `claude` instead');
    if (gw.model) ok('Default model', gw.model);
    const target = gw.baseUrl || 'https://api.anthropic.com';
    try {
      const r = await fetch(target, { method: 'GET', signal: AbortSignal.timeout(6000) });
      ok('Endpoint reachable', `HTTP ${r.status}`);
    } catch (e) {
      warn('Endpoint not reachable from here', e.cause?.code || e.message);
    }
    if (gw.hasToken) {
      const m = await gatewayModels(process.cwd(), { version: VERSION });
      if (m.ok) ok('Models on the gateway', `${m.models.length ? `${m.models.length}: ${m.models.slice(0, 4).map((x) => x.id).join(', ')}${m.models.length > 4 ? ', …' : ''}` : 'the list is empty'} (${m.url})`);
      else warn('Could not list the gateway models', m.error);
    }
  }

  console.log(paint('1', '\nDesktop window'));
  if (!IS_WIN) {
    const display = process.env.WAYLAND_DISPLAY ? `Wayland ${process.env.WAYLAND_DISPLAY}` : process.env.DISPLAY ? `X11 ${process.env.DISPLAY}` : null;
    display ? ok('Display', display) : warn('No display', 'use `pilcrow --server` and open the printed URL');
  }
  const electron = path.join(ROOT, 'node_modules', 'electron', 'dist', IS_WIN ? 'electron.exe' : 'electron');
  if (fs.existsSync(electron)) {
    let version = '';
    try {
      version = fs.readFileSync(path.join(ROOT, 'node_modules', 'electron', 'dist', 'version'), 'utf8').trim();
    } catch {
      /* ignore */
    }
    ok(`Electron ${version}`, electron);
    const noSandbox = fs.existsSync(path.join(xdg('state'), 'electron-no-sandbox'));
    if (noSandbox) warn('Electron runs with --no-sandbox', 'set automatically because the sandbox failed on this machine');
  } else {
    const chrome = findChrome();
    warn('Electron not downloaded', chrome ? `will use a ${path.basename(chrome)} app window instead` : 'will open in the default browser');
  }
  const chrome = findChrome();
  chrome ? ok(IS_WIN ? 'Browser window fallback' : 'Chrome/Chromium fallback', chrome) : warn(IS_WIN ? 'No Chrome or Edge found' : 'No Chrome/Chromium', 'fallback is the default browser');

  console.log(paint('1', '\nFiles'));
  ok('Config', CONFIG_DIR);
  ok('Logs', path.join(xdg('state'), 'pilcrow.log'));
  const lock = await existingInstance();
  if (lock) ok('Running instance', `pid ${lock.pid}, port ${lock.port}, ${lock.shell}`);
  console.log(failures ? paint('31', `\n${failures} problem(s) found.`) : paint('32', '\nAll essential checks passed.'));
  return failures;
}

/**
 * Used by the installer right after an update: if an older Pilcrow is still running, offer to stop it so
 * the next launch runs the new version. Busy sessions are never stopped without asking.
 */
async function restartOutdatedPrompt() {
  const existing = await existingInstance();
  if (!existing || existing.version === VERSION) return 0;
  const say = (s) => console.log(s);
  const busy = existing.busy;
  if (busy === 0) {
    await retireOutdated(existing);
    say(`  ✓ Stopped Pilcrow ${existing.version}, which was still running in the background; the next launch runs ${VERSION}.`);
    return 0;
  }
  const what = busy ? `Claude is still working in ${busy} session${busy > 1 ? 's' : ''}; stopping interrupts ${busy > 1 ? 'them' : 'it'} (the conversations are kept).` : 'If Claude is working, stopping interrupts it (the conversation is kept).';
  if (!process.stdin.isTTY) {
    say(`  ! Pilcrow ${existing.version} is still running in the background. ${what}`);
    say('    Close every Pilcrow window and open it again to use the new version.');
    return 0;
  }
  const rl = (await import('node:readline/promises')).createInterface({ input: process.stdin, output: process.stdout });
  const ans = (await rl.question(`  ? Pilcrow ${existing.version} is still running in the background. ${what} Stop it now? [Y/n] `)).trim().toLowerCase();
  rl.close();
  if (ans === '' || ans === 'y' || ans === 'yes') {
    await retireOutdated(existing, { force: true });
    say(`  ✓ Stopped; the next launch runs ${VERSION}.`);
  } else {
    say('  ! Not stopped. Close every Pilcrow window and open it again to use the new version.');
  }
  return 0;
}

/** `node server/main.js [--shell=…] [--port=N] [--doctor] [--restart-outdated]` */
export async function cli() {
  const args = parseArgs(process.argv.slice(2));
  if (args.doctor) process.exit((await doctor()) ? 1 : 0);
  if (args['restart-outdated']) process.exit(await restartOutdatedPrompt());
  let shell = args.shell || (args.server || args.headless ? 'none' : 'chrome');
  let existing = await existingInstance();
  if (existing && existing.version !== VERSION && (await retireOutdated(existing))) {
    log(`stopped Pilcrow ${existing.version} (pid ${existing.pid}) so ${VERSION} can start`);
    console.log(`Restarted Pilcrow: the older ${existing.version} was still running in the background.`);
    existing = null;
  }
  if (existing) {
    if (shell === 'none') {
      console.log(`Pilcrow is already running: ${existing.url}`);
      return;
    }
    const chrome = shell === 'chrome' ? findChrome() : null;
    if (chrome) openChromeWindow(chrome, existing.url).unref();
    else openExternal(existing.url);
    console.log('Pilcrow is already running; opened another window.');
    return;
  }
  const chrome = shell === 'chrome' ? findChrome() : null;
  if (shell === 'chrome' && !chrome) {
    console.log('No Chrome/Chromium found; opening Pilcrow in your default browser instead.');
    shell = 'browser';
  }
  let app;
  try {
    app = await start({ shell, port: args.port, quiet: true });
  } catch (e) {
    console.error(`Pilcrow could not start: ${e.message}`);
    process.exit(1);
  }
  const shutdown = () => app.stop().finally(() => process.exit(0));
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  process.on('SIGHUP', shutdown);

  const toBrowser = (why) => {
    if (why) console.log(`${why} Opening Pilcrow in your default browser instead.`);
    if (!openExternal(app.url)) console.log(`Open ${app.url} in a browser.`);
    console.log(`Pilcrow keeps running at ${app.url} until you quit it (Ctrl+Q in the window, or Ctrl+C here).`);
  };

  if (shell === 'none') {
    console.log(`Pilcrow is running at ${app.url}`);
    console.log('Press Ctrl+C to quit.');
  } else if (shell === 'browser') {
    toBrowser();
  } else {
    const child = openChromeWindow(chrome, app.url);
    const startedAt = Date.now();
    let fellBack = false;
    const fallback = (why) => {
      if (fellBack) return;
      fellBack = true;
      log(why);
      toBrowser(why);
    };
    child.on('error', (e) => fallback(`Could not start ${path.basename(chrome)} (${e.message}).`));
    child.on('exit', (code) => {
      if (fellBack) return;
      if (Date.now() - startedAt < 3000) {
        // Exit code 0 right away means Chrome handed the window to its running instance.
        if (code === 0) return console.log('Chrome opened the window in its running instance. Quit Pilcrow with Ctrl+Q in the window.');
        return fallback(`${path.basename(chrome)} exited right away (code ${code}).`);
      }
      shutdown();
    });
    // If no window ever connects (blocked sandbox, broken GPU, policy), do not leave a server with no window.
    setTimeout(() => {
      if (!app.connections()) fallback('No Pilcrow window connected within 25 seconds.');
    }, 25_000).unref();
  }
}
