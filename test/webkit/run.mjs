#!/usr/bin/env node
// WebKitGTK checks: the engine the Linux desktop shell (pilcrow-app.sh) renders with. The interface
// is loaded from the mock server into a real WebKit2 view and driven with JavaScript; the checks are
// the ones Chromium cannot stand in for (what WebKit lays out and paints).
//
//   node test/webkit/run.mjs
//
// Needs Linux with python3-gi and gir1.2-webkit2-4.1 (or 4.0), and a display (xvfb-run without one).
// Without them it prints one "skipped" line and passes, unless PILCROW_REQUIRE_WEBKIT=1.
import { spawn, spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, mkdirSync } from 'node:fs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const require_ = process.env.PILCROW_REQUIRE_WEBKIT === '1';
const PORT = 5311;

function skip(why) {
  if (require_) {
    console.error(`webkit: ${why} (PILCROW_REQUIRE_WEBKIT=1, so this is a failure)`);
    process.exit(1);
  }
  console.log(`webkit: skipped — ${why}`);
  process.exit(0);
}

if (process.platform !== 'linux') skip(`WebKitGTK only runs on Linux (this is ${process.platform})`);
if (!existsSync(join(ROOT, 'web', 'dist', 'index.html'))) skip('web/dist is missing (run npm run build)');

// The system python has the GTK bindings; a pyenv/conda one first on PATH usually does not.
const candidates = ['/usr/bin/python3', 'python3'];
let python = null;
for (const p of candidates) {
  const r = spawnSync(p, ['-c', "import gi\ngi.require_version('Gtk','3.0')\nok=False\nfor v in ('4.1','4.0'):\n  try:\n    gi.require_version('WebKit2', v); ok=True; break\n  except ValueError: pass\nraise SystemExit(0 if ok else 1)"], { encoding: 'utf8' });
  if (r.status === 0) {
    python = p;
    break;
  }
}
if (!python) skip('no python3 with WebKit2 bindings (apt install python3-gi gir1.2-webkit2-4.1)');
if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
  if (spawnSync('which', ['xvfb-run']).status !== 0) skip('no display and no xvfb-run');
}

mkdirSync(join(ROOT, 'test', 'out'), { recursive: true });
const mock = spawn(process.execPath, [join(ROOT, 'test', 'mock', 'mock-server.mjs'), String(PORT)], { cwd: ROOT, env: { ...process.env, MOCK_LONG: '120' }, stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((ok, bad) => {
  mock.stdout.on('data', (d) => process.stdout.write(`[mock] ${d}`));
  const t = setInterval(async () => {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/api/hello`);
      if (r.ok) {
        clearInterval(t);
        ok();
      }
    } catch {}
  }, 150);
  mock.on('exit', (c) => bad(new Error(`mock server exited (${c})`)));
});

const driver = join(ROOT, 'test', 'webkit', 'driver.py');
const cmd = process.env.DISPLAY || process.env.WAYLAND_DISPLAY ? python : 'xvfb-run';
const args = cmd === python ? ['-I', driver] : ['-a', '-s', '-screen 0 1400x900x24', python, '-I', driver];
const r = spawnSync(cmd, [...args, `http://127.0.0.1:${PORT}/`, join(ROOT, 'test', 'out')], { stdio: 'inherit', timeout: 180_000 });
mock.kill();
process.exit(r.status ?? 1);
