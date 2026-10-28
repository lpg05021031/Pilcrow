#!/usr/bin/env node
// Release: turn the working tree into a new version and its three installer files.
//
//   npm run release -- 2.9.1 [--skip-tests] [--no-commit] [--dry-run]
//
//   1. CHANGELOG.md must already describe this version at the top
//   2. package.json / package-lock.json get the new version
//   3. npm run check, lint, test:unit   (skipped with --skip-tests)
//   4. npm run build            -> web/dist
//   4b. npm run test:e2e, test:webkit   (against the fresh build; skipped with --skip-tests or --skip-browser)
//   5. scripts/make-installers.py --verify   -> out/, unpacked and compared with the tree
//   6. out/* copied to the repo root
//   7. git commit + tag vX.Y.Z  (skipped with --no-commit; --dry-run stops before step 2)
//
// Any failure stops the release where it is; nothing is committed until the very end.
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const INSTALLERS = ['pilcrow-install.sh', 'pilcrow-windows.ps1', 'pilcrow-app.sh'];

const args = process.argv.slice(2);
/** `--trailer "Key: value"` lines go at the end of the release commit message. */
const trailers = [];
for (let i = 0; i < args.length; i++) if (args[i] === '--trailer' && args[i + 1]) trailers.push(args.splice(i + 1, 1)[0]);
const flags = new Set(args.filter((a) => a.startsWith('--')));
const version = args.find((a) => !a.startsWith('--'));
if (!version || !/^\d+\.\d+\.\d+$/.test(version)) {
  console.error('usage: npm run release -- X.Y.Z [--skip-tests] [--skip-browser] [--no-commit] [--dry-run] [--trailer "Key: value"]');
  process.exit(2);
}

const say = (s) => console.log(`\n==> ${s}`);
const die = (s) => { console.error(`\nx ${s}`); process.exit(1); };
// On Windows the commands are .cmd shims, so they go through the shell; arguments with spaces must then be quoted.
const win = process.platform === 'win32';
const q = (a) => (win && /[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
const run = (cmd, argv, opts = {}) => {
  const r = spawnSync(cmd, win ? argv.map(q) : argv, { cwd: ROOT, stdio: 'inherit', shell: win, ...opts });
  if (r.status !== 0) die(`${cmd} ${argv.join(' ')} failed (exit ${r.status})`);
};
const out = (cmd, argv) => execFileSync(cmd, argv, { cwd: ROOT, encoding: 'utf8', shell: process.platform === 'win32' }).trim();
const python = process.platform === 'win32' ? 'python' : 'python3';

// ---- preflight
say(`Release ${version}`);
const pkgPath = join(ROOT, 'package.json');
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
console.log(`  current version: ${pkg.version}`);
if (pkg.version === version && !flags.has('--dry-run')) console.log('  (same version: installers are regenerated, package.json left as is)');

const changelog = readFileSync(join(ROOT, 'CHANGELOG.md'), 'utf8');
const firstEntry = changelog.match(/^- \*\*(\d+\.\d+\.\d+)\*\*/m);
if (!firstEntry) die('CHANGELOG.md has no "- **X.Y.Z**：…" entry');
if (firstEntry[1] !== version) die(`CHANGELOG.md starts with ${firstEntry[1]}, not ${version}: write this version's notes first`);
console.log(`  CHANGELOG.md: ${version} described`);

if (!flags.has('--no-commit')) {
  const dirty = out('git', ['status', '--porcelain']).split('\n').filter((l) => l && !l.endsWith('CHANGELOG.md') && !l.endsWith('package.json') && !l.endsWith('package-lock.json'));
  if (dirty.length) console.log(`  note: ${dirty.length} uncommitted change(s) will be included in the release commit`);
}
if (flags.has('--dry-run')) { console.log('\n--dry-run: stopping before changing anything'); process.exit(0); }

// ---- version bump
say('Version');
for (const file of ['package.json', 'package-lock.json']) {
  const p = join(ROOT, file);
  if (!existsSync(p)) continue;
  const j = JSON.parse(readFileSync(p, 'utf8'));
  j.version = version;
  if (j.packages && j.packages['']) j.packages[''].version = version;
  writeFileSync(p, JSON.stringify(j, null, 2) + '\n');
  console.log(`  ${file}: ${version}`);
}

// ---- checks
if (flags.has('--skip-tests')) {
  console.log('\n(--skip-tests: skipping check, lint and the tests)');
} else {
  say('Checks');
  run('npm', ['run', 'check']);
  run('npm', ['run', 'lint']);
  run('npm', ['run', 'test:unit']);
  if (existsSync(join(ROOT, 'qt', 'tests'))) run(python, ['-m', 'pytest', 'qt/tests', '-q', '-p', 'no:cacheprovider']);
}

// ---- build
say('Build');
run('npm', ['run', 'build']);

// ---- browser tests run against the build that ships
if (flags.has('--skip-tests') || flags.has('--skip-browser')) {
  console.log('\n(skipping the browser tests)');
} else {
  say('Browser tests');
  run('npm', ['run', 'test:e2e']);
  run('npm', ['run', 'test:webkit']);
}

// ---- installers
say('Installers');
run(python, ['-I', join(ROOT, 'scripts', 'make-installers.py'), '--verify']);
for (const f of INSTALLERS) {
  copyFileSync(join(ROOT, 'out', f), join(ROOT, f));
  console.log(`  ${f} -> repo root`);
}

// ---- commit + tag
if (flags.has('--no-commit')) {
  console.log('\n--no-commit: installers are in out/ and the repo root; nothing committed');
  process.exit(0);
}
say('Commit');
run('git', ['add', '-A']);
// The message goes through a file: a multi-line -m argument does not survive the Windows shell.
const msgFile = join(ROOT, 'out', 'release-message.txt');
writeFileSync(msgFile, [`Release ${version}`, ...(trailers.length ? ['', ...trailers] : []), ''].join('\n'));
run('git', ['commit', '-q', '-F', msgFile]);
run('git', ['tag', '-f', `v${version}`]);
console.log(`\nPilcrow ${version} released: commit + tag v${version}. Push with: git push && git push --tags`);
