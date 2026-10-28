// Terminal sessions. Linux: a Python PTY bridge (no native Node addons needed), or util-linux `script`.
// Windows: ConPTY through node-pty (it ships prebuilt Windows binaries, so nothing is compiled), or a
// basic line-mode PowerShell when node-pty is not installed.
import { spawn } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { which, uuid, log, cleanEnv, IS_WIN, HOME, slash } from './util.js';

const BRIDGE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'pty_bridge.py');
const SCROLLBACK = 400 * 1024;
const require = createRequire(import.meta.url);

export const terminals = new EventEmitter();
const all = new Map();

let nodePty;
function loadNodePty() {
  if (nodePty !== undefined) return nodePty;
  try {
    nodePty = require('node-pty');
  } catch {
    nodePty = null;
  }
  return nodePty;
}

export function capabilities() {
  const python = IS_WIN ? null : which('python3') || which('python');
  const script = IS_WIN ? null : which('script');
  const conpty = IS_WIN && Boolean(loadNodePty());
  return { python, script, conpty, pty: Boolean(python || script || conpty), basic: IS_WIN && !conpty };
}

/** PILCROW_SHELL picks another shell (cmd.exe, or Git Bash's bash.exe); otherwise PowerShell. */
function windowsShell() {
  return (process.env.PILCROW_SHELL && which(process.env.PILCROW_SHELL)) || which('pwsh') || which('powershell') || process.env.ComSpec || 'cmd.exe';
}

/** A ConPTY session through node-pty. */
function openConpty({ shell, args, cwd, env, cols, rows }) {
  const p = loadNodePty().spawn(shell, args, { name: 'xterm-256color', cols, rows, cwd, env, useConpty: true });
  const ev = new EventEmitter();
  p.onData((d) => ev.emit('data', d));
  p.onExit(({ exitCode, signal }) => ev.emit('exit', exitCode, signal || null));
  return { ev, write: (d) => p.write(d), resize: (c, r) => p.resize(c, r), kill: () => p.kill(), resizable: true };
}

/**
 * No PTY available on Windows: run the shell on pipes and do the line editing here (echo, backspace,
 * Enter, Ctrl+C), so typing still looks like a terminal. Full-screen programs will not work.
 */
export function openBasic({ shell, args, cwd, env }) {
  const proc = spawn(shell, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  const ev = new EventEmitter();
  const out = new StringDecoder('utf8');
  const err = new StringDecoder('utf8');
  const crlf = (t) => t.replace(/\r?\n/g, '\r\n');
  proc.stdout.on('data', (d) => ev.emit('data', crlf(out.write(d))));
  proc.stderr.on('data', (d) => ev.emit('data', crlf(err.write(d))));
  proc.on('error', (e) => ev.emit('data', `\r\n[terminal error: ${e.message}]\r\n`));
  proc.on('exit', (code, signal) => ev.emit('exit', code, signal));
  let line = '';
  const write = (data) => {
    for (const ch of data) {
      if (ch === '\r') {
        ev.emit('data', '\r\n');
        proc.stdin.write(line + '\n');
        line = '';
      } else if (ch === '\x7f' || ch === '\b') {
        if (line) {
          line = line.slice(0, -1);
          ev.emit('data', '\b \b');
        }
      } else if (ch === '\x03') {
        line = '';
        ev.emit('data', '^C\r\n');
      } else if (ch >= ' ' || ch === '\t') {
        line += ch;
        ev.emit('data', ch);
      }
    }
  };
  return { ev, write, resize: () => {}, kill: () => proc.kill(), resizable: false };
}

/** Linux: the Python bridge, or `script`. */
function openUnix({ caps, argv, cwd, env, cols, rows }) {
  let proc;
  let resizable = false;
  if (caps.python) {
    proc = spawn(caps.python, [BRIDGE, String(cols), String(rows), ...argv], { cwd, env, stdio: ['pipe', 'pipe', 'pipe', 'pipe'] });
    resizable = true;
  } else if (caps.script) {
    env.COLUMNS = String(cols);
    env.LINES = String(rows);
    proc = spawn(caps.script, ['-qfc', argv.map((a) => `'${a.replace(/'/g, "'\\''")}'`).join(' '), '/dev/null'], { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
  } else {
    throw new Error('No PTY helper found. Install python3 to use the terminal.');
  }
  const ev = new EventEmitter();
  const out = new StringDecoder('utf8');
  const err = new StringDecoder('utf8');
  proc.stdout.on('data', (d) => ev.emit('data', out.write(d)));
  proc.stderr.on('data', (d) => ev.emit('data', err.write(d)));
  proc.on('error', (e) => {
    log('terminal error', e);
    ev.emit('data', `\r\n[terminal error: ${e.message}]\r\n`);
  });
  proc.on('exit', (code, signal) => ev.emit('exit', code, signal));
  return {
    ev,
    resizable,
    write: (d) => proc.stdin.write(d),
    resize: (c, r) => {
      try {
        proc.stdio[3].write(JSON.stringify({ cols: c, rows: r }) + '\n');
      } catch {
        /* bridge gone */
      }
    },
    kill: () => {
      try {
        proc.stdin.end();
        proc.kill('SIGHUP');
        setTimeout(() => proc.exitCode === null && proc.kill('SIGKILL'), 1500);
      } catch {
        /* already gone */
      }
    },
  };
}

export function open({ cwd, cols = 100, rows = 30, command }) {
  const caps = capabilities();
  const env = cleanEnv({ TERM: 'xterm-256color', COLORTERM: 'truecolor', TERM_PROGRAM: 'Pilcrow' });
  const dir = cwd && fs.existsSync(cwd) ? cwd : HOME;
  let io;
  if (IS_WIN) {
    const shell = windowsShell();
    const name = path.basename(shell).toLowerCase();
    const isPs = /pwsh|powershell/.test(name);
    const isBash = /bash/.test(name);
    let args;
    if (command) args = isPs ? ['-NoLogo', '-Command', command] : isBash ? ['-lc', command] : ['/d', '/s', '/c', command];
    else if (isPs) args = caps.conpty ? ['-NoLogo'] : ['-NoLogo', '-NoExit', '-Command', '-'];
    else if (isBash) args = caps.conpty ? ['--login', '-i'] : ['--login'];
    else args = [];
    const basicArgs = isPs && !command ? ['-NoLogo', '-NoExit', '-Command', '-'] : args;
    io = caps.conpty ? openConpty({ shell, args, cwd: dir, env, cols, rows }) : openBasic({ shell, args: basicArgs, cwd: dir, env });
    if (caps.conpty && !command) io.fallback = () => openBasic({ shell, args: basicArgs, cwd: dir, env });
  } else {
    const shell = process.env.SHELL || which('bash') || '/bin/sh';
    io = openUnix({ caps, argv: command ? [shell, '-lc', command] : [shell, '-l'], cwd: dir, env, cols, rows });
  }
  const id = uuid();
  const term = { id, cwd: slash(dir), io, resizable: io.resizable, buffer: '', exited: null, title: path.basename(dir) || dir, cols, rows, typed: false };
  all.set(id, term);
  const started = Date.now();
  const emit = (data) => {
    if (!data) return;
    term.buffer += data;
    if (term.buffer.length > SCROLLBACK) term.buffer = term.buffer.slice(-SCROLLBACK);
    terminals.emit('data', { id, data });
  };
  const attach = (cur) => {
    cur.ev.on('data', (data) => term.io === cur && emit(data));
    cur.ev.on('exit', (code, signal) => {
      if (term.io !== cur) return;
      // An interactive shell that ends on its own within two seconds means ConPTY did not work on this
      // machine: carry on in basic mode instead of leaving a dead terminal.
      if (cur.fallback && !term.typed && Date.now() - started < 2000) {
        log('ConPTY shell exited at once; switching to the basic terminal');
        term.io = cur.fallback();
        term.resizable = false;
        attach(term.io);
        emit('\r\n[ConPTY did not start here; using a basic terminal]\r\n');
        return;
      }
      term.exited = { code, signal };
      terminals.emit('exit', { id, code, signal });
    });
  };
  attach(io);
  if (IS_WIN && !caps.conpty) emit('[Basic terminal: no ConPTY, so commands run one line at a time]\r\n');
  return { id, cwd: term.cwd, resizable: term.resizable, title: term.title };
}

export function write(id, data) {
  const t = all.get(id);
  if (!t || t.exited) return;
  // Terminal replies to the shell's own queries (cursor position etc.) start with ESC; they are not typing.
  if (!data.startsWith('\x1b')) t.typed = true;
  t.io.write(data);
}

export function resize(id, cols, rows) {
  const t = all.get(id);
  if (!t || t.exited || !t.resizable) return;
  if (t.cols === cols && t.rows === rows) return;
  [t.cols, t.rows] = [cols, rows];
  t.io.resize(cols, rows);
}

export function close(id) {
  const t = all.get(id);
  if (!t) return;
  all.delete(id);
  if (!t.exited) {
    try {
      t.io.kill();
    } catch {
      /* already gone */
    }
  }
}

export function list() {
  return [...all.values()].map((t) => ({ id: t.id, cwd: t.cwd, title: t.title, exited: t.exited, resizable: t.resizable }));
}

export function snapshot(id) {
  const t = all.get(id);
  return t ? { id, buffer: t.buffer, exited: t.exited, cwd: t.cwd, resizable: t.resizable } : null;
}

export function closeAll() {
  for (const id of [...all.keys()]) close(id);
}
