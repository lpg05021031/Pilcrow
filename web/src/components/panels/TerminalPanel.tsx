import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { Plus, X, SquareTerminal } from 'lucide-react';
import { rpc } from '../../lib/rpc';
import { useStore, call, toast, toasted, memo, type SessionMemo } from '../../lib/store';
import type { SessionSummary } from '../../lib/types';
import { basename } from '../../lib/format';
import { errText } from '../../lib/format';
import { Empty } from '../ui/Empty';

type TermRef = SessionMemo['terms'][number];

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function termTheme() {
  return {
    background: cssVar('--chrome'),
    foreground: cssVar('--text'),
    cursor: cssVar('--text-2'),
    cursorAccent: cssVar('--chrome'),
    selectionBackground: cssVar('--selection'),
    black: cssVar('--ansi-black'),
    red: cssVar('--ansi-red'),
    green: cssVar('--ansi-green'),
    yellow: cssVar('--ansi-yellow'),
    blue: cssVar('--ansi-blue'),
    magenta: cssVar('--ansi-magenta'),
    cyan: cssVar('--ansi-cyan'),
    white: cssVar('--ansi-white'),
    brightBlack: cssVar('--ansi-bright-black'),
    brightRed: cssVar('--ansi-bright-red'),
    brightGreen: cssVar('--ansi-bright-green'),
    brightYellow: cssVar('--ansi-bright-yellow'),
    brightBlue: cssVar('--ansi-bright-blue'),
    brightMagenta: cssVar('--ansi-bright-magenta'),
    brightCyan: cssVar('--ansi-bright-cyan'),
    brightWhite: cssVar('--ansi-bright-white'),
  };
}

export function TerminalPanel({ session, cwd, visible }: { session: SessionSummary; cwd: string; visible: boolean }) {
  const info = useStore((s) => s.info);
  // The list lives in the session's memo, so it outlasts the tab and goes with the session.
  const [terms, setTerms] = useState<TermRef[]>(() => memo(session.id).terms);
  const [active, setActive] = useState<string | null>(() => memo(session.id).term);

  const save = (list: TermRef[], act: string | null) => {
    const m = memo(session.id);
    m.terms = list;
    if (act) m.term = act;
    setTerms(list);
    setActive(act);
  };

  const create = async () => {
    try {
      const r = await call<{ id: string; title: string }>('term.open', { cwd, cols: 100, rows: 30 });
      const list = [...memo(session.id).terms, { id: r.id, title: basename(cwd) }];
      save(list, r.id);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  useEffect(() => {
    if (visible && !memo(session.id).terms.length && info?.terminal.pty !== false) create();
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps -- a first terminal opens the first time the tab is shown, not again on each render

  useEffect(
    () =>
      rpc.on('term.exit', ({ id }) => {
        const list = memo(session.id).terms.map((t) => (t.id === id ? { ...t, exited: true } : t));
        memo(session.id).terms = list;
        setTerms(list);
      }),
    [session.id],
  );

  const closeTerm = (id: string) => {
    call('term.close', { id }).catch(toasted);
    const list = memo(session.id).terms.filter((t) => t.id !== id);
    save(list, list[list.length - 1]?.id || null);
  };

  if (info && !info.terminal.pty) {
    return (
      <Empty icon={SquareTerminal} title="The terminal needs python3 (or util-linux `script`) on this machine." />
    );
  }

  return (
    <div className="terms">
      <div className="panel-toolbar">
        {terms.map((t, i) => (
          <div key={t.id} className={`ttab${t.id === active ? ' on' : ''}${t.exited ? ' exited' : ''}`}>
            <button onClick={() => save(terms, t.id)}>
              {t.title}
              {terms.length > 1 ? ` ${i + 1}` : ''}
              {t.exited ? ' (exited)' : ''}
            </button>
            <button className="ttab-x" onClick={() => closeTerm(t.id)} aria-label="Close terminal">
              <X size={11} />
            </button>
          </div>
        ))}
        <button className="icon-btn sm" title="New terminal" aria-label="New terminal" onClick={create}>
          <Plus size={14} />
        </button>
      </div>
      <div className="term-host">
        {terms.map((t) => (
          <XTerm key={t.id} id={t.id} visible={visible && t.id === active} />
        ))}
      </div>
    </div>
  );
}

function XTerm({ id, visible }: { id: string; visible: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const theme = useStore((s) => s.theme);

  useEffect(() => {
    const t = new Terminal({
      fontFamily: cssVar('--font-mono') || 'monospace',
      fontSize: 12.5,
      lineHeight: 1.3,
      cursorBlink: true,
      scrollback: 8000,
      allowProposedApi: true,
      theme: termTheme(),
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.loadAddon(new WebLinksAddon((_e, uri) => rpc.call('shell.openExternal', { url: uri }).catch(() => {}))); // a link that will not open stays text
    t.open(host.current!);
    // Let the app's panel and session shortcuts through instead of sending them to the shell.
    t.attachCustomKeyEventHandler((e) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && /^[1-6]$/.test(e.key)) return false;
      if (e.ctrlKey && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) return false;
      return true;
    });
    term.current = t;
    fit.current = f;
    // Keystrokes to a terminal that has gone: the terminal says so itself when it reconnects or closes.
    const sub = t.onData((data) => rpc.call('term.input', { id, data }).catch(() => {}));
    const off = rpc.on('term.data', (d) => d.id === id && t.write(d.data));
    const offExit = rpc.on('term.exit', (d) => d.id === id && t.write(`\r\n\x1b[2m[process exited${d.code != null ? ` with code ${d.code}` : ''}]\x1b[0m\r\n`));
    rpc.call<{ buffer?: string } | null>('term.snapshot', { id }).then((s) => s?.buffer && t.write(s.buffer), () => {}); // a new terminal has no backlog to show
    const ro = new ResizeObserver(() => {
      if (!host.current?.offsetParent) return;
      try {
        f.fit();
        rpc.call('term.resize', { id, cols: t.cols, rows: t.rows }).catch(() => {}); // the next resize tries again
      } catch {
        /* not visible yet */
      }
    });
    ro.observe(host.current!);
    return () => {
      sub.dispose();
      off();
      offExit();
      ro.disconnect();
      t.dispose();
    };
  }, [id]);

  useEffect(() => {
    if (term.current) term.current.options.theme = termTheme();
  }, [theme]);

  useEffect(() => {
    if (!visible) return;
    requestAnimationFrame(() => {
      try {
        fit.current?.fit();
        const t = term.current;
        if (t) rpc.call('term.resize', { id, cols: t.cols, rows: t.rows }).catch(() => {}); // the next resize tries again
        t?.focus();
      } catch {
        /* ignore */
      }
    });
  }, [visible, id]);

  return <div className="xterm-host" ref={host} style={{ display: visible ? 'block' : 'none' }} />;
}
