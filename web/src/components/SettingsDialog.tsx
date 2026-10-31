import { useLayoutEffect, useRef, useState } from 'react';
import { Copy, CircleCheck, CircleAlert, RefreshCw } from 'lucide-react';
import { useStore, setDialog, setConfig, toast, call, setTheme } from '../lib/store';
import type { AppInfo, Config } from '../lib/types';
import { tildify } from '../lib/format';
import { Dialog } from './ui/Dialog';
import { Switch, copyText, Mark, Seg } from './ui/bits';
import { Select } from './ui/Select';
import { MODES } from './Composer';
import { ModelSelect } from './ModelSelect';
import { SkillsPage } from './settings/SkillsPage';
import { McpPage } from './settings/McpPage';
import { MemoryPage } from './settings/MemoryPage';
import { ReposPage } from './settings/ReposPage';
import type { PermissionMode, Effort } from '../lib/types';

type Section = 'general' | 'repos' | 'claude' | 'skills' | 'mcp' | 'memory' | 'diagnostics';
const WIDE: Section[] = ['repos', 'skills', 'mcp', 'memory'];

export function SettingsDialog() {
  const config = useStore((s) => s.config)!;
  const info = useStore((s) => s.info);
  const initial = useStore((s) => s.dialogs.settings);
  const [section, setSection] = useState<Section>(typeof initial === 'string' ? (initial as Section) : 'general');
  const set = (p: Partial<Config>) => setConfig(p).catch(() => {});
  // The highlight behind the chosen page glides to the next one.
  const nav = useRef<HTMLElement>(null);
  const [ink, setInk] = useState<{ top: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>('button.on');
    if (el) setInk({ top: el.offsetTop, height: el.offsetHeight });
  }, [section]);

  return (
    <Dialog title="Settings" onClose={() => setDialog('settings', false)} width={WIDE.includes(section) ? 860 : 720} className={`settings${WIDE.includes(section) ? ' settings-wide' : ''}`}>
      <div className="settings-layout">
        <nav className="settings-nav" ref={nav}>
          {ink && <span className="settings-ink" aria-hidden="true" style={{ transform: `translateY(${ink.top}px)`, height: ink.height }} />}
          {(
            [
              ['general', 'General'],
              ['repos', 'Repositories'],
              ['claude', 'Claude Code'],
              ['skills', 'Skills'],
              ['mcp', 'MCP servers'],
              ['memory', 'Memory'],
              ['diagnostics', 'Diagnostics'],
            ] as [Section, string][]
          ).map(([id, label]) => (
            <button key={id} className={section === id ? 'on' : ''} onClick={() => setSection(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="settings-body">
          <div key={section} className="settings-page">
          {section === 'general' && (
            <>
              <Row label="Theme">
                <Seg
                  value={config.theme || 'system'}
                  onChange={(t, e) => setTheme(t, { x: e.clientX, y: e.clientY })}
                  options={[
                    { value: 'system', label: 'System' },
                    { value: 'dark', label: 'Dark' },
                    { value: 'light', label: 'Light' },
                  ]}
                />
              </Row>
              <Row label="Conversation text size">
                <Seg
                  value={String(nearestSize(config.fontSize || 14.5))}
                  onChange={(v) => set({ fontSize: Number(v) })}
                  options={[
                    { value: '13.5', label: 'Small' },
                    { value: '14.5', label: 'Default' },
                    { value: '16', label: 'Large' },
                  ]}
                />
              </Row>
              <Row label="Send messages with">
                <Seg
                  value={config.sendKey === 'mod-enter' ? 'mod-enter' : 'enter'}
                  onChange={(v) => set({ sendKey: v })}
                  options={[
                    { value: 'enter', label: 'Enter' },
                    { value: 'mod-enter', label: 'Ctrl+Enter' },
                  ]}
                />
              </Row>
              <Row label="Desktop notifications" hint="When Claude finishes or needs you while the window is in the background">
                <Switch on={config.notifications} onChange={(v) => set({ notifications: v })} />
              </Row>
              <Row label="Suggest a next message" hint="Claude Code predicts a likely follow-up after each turn">
                <Switch on={config.promptSuggestions} onChange={(v) => set({ promptSuggestions: v })} />
              </Row>
              <Row label="Timeline" hint="Snapshot the folder after every step, so any point can be revisited or restored (side panel → Timeline)">
                <Switch on={config.timeline !== false} onChange={(v) => set({ timeline: v })} />
              </Row>
            </>
          )}

          {section === 'claude' && (
            <>
              <Row label="Claude Code program" hint={claudeHint(info)}>
                <Select
                  value={['auto', 'bundled', 'system'].includes(config.claudeBinary) ? config.claudeBinary : 'custom'}
                  onChange={(v) => set({ claudeBinary: v === 'custom' ? '' : v })}
                  width={300}
                  options={[
                    { value: 'auto', label: 'Automatic', hint: 'The bundled copy, or `claude` on PATH' },
                    { value: 'bundled', label: 'Bundled with Pilcrow', hint: info?.claude.bundled ? `Claude Code ${info.claude.bundled.version}` : 'Not installed' },
                    { value: 'system', label: 'Installed `claude`', hint: info?.claude.system ? `Claude Code ${info.claude.system.version} · ${tildify(info.claude.system.path, info.home)}` : 'Not found on PATH' },
                    { value: 'custom', label: 'Custom path…' },
                  ]}
                />
              </Row>
              {!['auto', 'bundled', 'system'].includes(config.claudeBinary) && (
                <Row label="Custom path">
                  <input className="input mono" defaultValue={config.claudeBinary} placeholder="/usr/local/bin/claude" onBlur={(e) => set({ claudeBinary: e.target.value.trim() || 'auto' })} />
                </Row>
              )}
              <Row label="Default permission mode">
                <Select value={config.defaultMode} onChange={(v) => set({ defaultMode: v as PermissionMode })} width={300} options={MODES.filter((m) => !(m.id === 'bypassPermissions' && info?.isRoot)).map((m) => ({ value: m.id, label: m.label, hint: m.hint, icon: m.icon }))} />
              </Row>
              <Row label="Default model" hint="For new sessions; the models your gateway offers">
                <DefaultModel value={config.defaultModel || ''} save={(v) => set({ defaultModel: v })} />
              </Row>
              <Row label="Default effort">
                <Select
                  value={config.defaultEffort || ''}
                  onChange={(v) => set({ defaultEffort: v as Effort })}
                  options={[
                    { value: '', label: 'Default' },
                    { value: 'low', label: 'Low' },
                    { value: 'medium', label: 'Medium' },
                    { value: 'high', label: 'High' },
                    { value: 'xhigh', label: 'Extra high' },
                    { value: 'max', label: 'Max' },
                  ]}
                />
              </Row>
              <Row label="Stop idle Claude Code processes after" hint="Sessions resume automatically on your next message">
                <Select value={String(config.idleTimeoutMin)} onChange={(v) => set({ idleTimeoutMin: Number(v) })} options={[10, 30, 60, 240, 1440].map((m) => ({ value: String(m), label: m < 60 ? `${m} minutes` : m < 1440 ? `${m / 60} hour${m > 60 ? 's' : ''}` : '1 day' }))} />
              </Row>
              <Row label="Worktree folder" hint="Where isolated worktrees are created. Empty uses ~/.local/share/pilcrow/worktrees">
                <input className="input mono" defaultValue={config.worktreeRoot} placeholder="~/.local/share/pilcrow/worktrees" onBlur={(e) => set({ worktreeRoot: e.target.value.trim() })} />
              </Row>
            </>
          )}

          {section === 'skills' && <SkillsPage />}
          {section === 'mcp' && <McpPage />}
          {section === 'memory' && <MemoryPage />}
          {section === 'repos' && <ReposPage />}
          {section === 'diagnostics' && <Diagnostics info={info} />}
          </div>
        </div>
      </div>
      <div className="settings-about">
        <Mark size={16} />
        <span>
          Pilcrow {info?.version} · Claude Code {info?.claude.version || '—'}
        </span>
      </div>
    </Dialog>
  );
}

function Row({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-label">
        <div>{label}</div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <div className="set-control">{children}</div>
    </div>
  );
}

function Diagnostics({ info }: { info: AppInfo | null }) {
  const [fresh, setFresh] = useState<AppInfo | null>(null);
  const i = fresh || info;
  if (!i) return null;
  const checks: [string, boolean, string][] = [
    ['Claude Code program', Boolean(i.claude.path), i.claude.path ? `${i.claude.version || '?'} · ${i.claude.source} · ${tildify(i.claude.path, i.home)}` : 'Not found — install Claude Code or reinstall Pilcrow'],
    ['API endpoint', Boolean(i.gateway?.baseUrl) || i.gateway?.provider !== 'anthropic', i.gateway?.baseUrl ? `${i.gateway.baseUrl}${i.gateway.source ? ` (from ${i.gateway.source} settings)` : ''}` : 'Default Anthropic API (no ANTHROPIC_BASE_URL set)'],
    ['API credential', Boolean(i.gateway?.hasToken), i.gateway?.hasToken ? 'Found in settings or environment' : 'No token or API key found; Claude Code may ask you to log in'],
    ['Terminal support', i.terminal.pty, i.terminal.python ? `python3 at ${i.terminal.python}` : i.terminal.script ? 'util-linux script (no resizing)' : 'Install python3 for the terminal panel'],
    ['git', i.git, i.git ? 'Available' : 'Not found — the Changes panel and worktrees need git'],
    ['Running as root', !i.isRoot, i.isRoot ? 'Claude Code refuses "bypass permissions" mode as root' : 'No'],
  ];
  const text = [`Pilcrow ${i.version} · ${i.shell} shell · Node ${i.node} · ${i.platform}`, ...checks.map(([k, ok, v]) => `${ok ? '✓' : '✗'} ${k}: ${v}`), `Config: ${i.configDir}`].join('\n');
  return (
    <>
      <div className="diag">
        {checks.map(([k, ok, v]) => (
          <div key={k} className="diag-row">
            {ok ? <CircleCheck size={15} className="st-ok" /> : <CircleAlert size={15} className="st-warn" />}
            <div>
              <div className="diag-k">{k}</div>
              <div className="hint">{v}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="row">
        <button className="btn sm" onClick={() => copyText(text).then(() => toast('Diagnostics copied', 'success'), () => toast('Could not copy to the clipboard', 'error'))}>
          <Copy size={13} /> Copy diagnostics
        </button>
        <button className="btn sm ghost" onClick={() => call<AppInfo>('app.info').then(setFresh)}>
          <RefreshCw size={13} /> Re-check
        </button>
        <div className="grow" />
        <span className="hint">
          {WINDOW_LABEL[i.shell] || i.shell} · Node {i.node}
        </span>
      </div>
      <div className="hint">Logs: ~/.local/state/pilcrow/pilcrow.log · Settings: {tildify(i.configDir, i.home)}</div>
    </>
  );
}

const SIZES = [13.5, 14.5, 16];
function nearestSize(px: number) {
  return SIZES.reduce((best, s) => (Math.abs(s - px) < Math.abs(best - px) ? s : best), SIZES[0]);
}

const WINDOW_LABEL: Record<string, string> = {
  electron: 'Desktop window',
  chrome: 'Chrome app window',
  browser: 'Browser tab',
  none: 'Server only',
};

/** Say which Claude Code runs in words first; the full path is one hover away. */
function claudeHint(info: AppInfo | null) {
  const c = info?.claude;
  if (!c?.path) return 'Not found';
  const v = c.version ? ` ${c.version}` : '';
  const where = c.source === 'bundled' ? `The copy installed with Pilcrow${v}` : c.source === 'system' ? `The claude on your PATH${v}` : `Custom program${v}`;
  return <span title={tildify(c.path, info!.home)}>{where}</span>;
}

/** Saves a picked model at once, and a typed custom ID when the field is left. */
function DefaultModel({ value, save }: { value: string; save: (v: string) => void }) {
  const [v, setV] = useState(value);
  return (
    <ModelSelect
      value={v}
      onChange={(next, typing) => {
        setV(next);
        if (!typing) save(next);
      }}
      onCommit={() => v.trim() !== value && save(v.trim())}
    />
  );
}
