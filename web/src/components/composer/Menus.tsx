import { useState } from 'react';
import { ChevronDown, ShieldCheck, FilePen, Map as MapIcon, Zap, ShieldOff, RefreshCw, type LucideIcon } from 'lucide-react';
import { useStore, setMode, setModel, setEffort, loadGatewayModels, toasted } from '../../lib/store';
import { ALIASES, useGatewayModels, modelLabel } from '../../lib/models';
import type { PermissionMode, Effort, ModelInfo } from '../../lib/types';
import { Popover, Menu, type MenuItem } from '../ui/Popover';

export const MODES: { id: PermissionMode; label: string; hint: string; icon: LucideIcon; tone: string }[] = [
  { id: 'default', label: 'Ask before edits', hint: 'Asks before changing files or running commands', icon: ShieldCheck, tone: '' },
  { id: 'acceptEdits', label: 'Edit automatically', hint: 'File edits are applied without asking', icon: FilePen, tone: 'accent' },
  { id: 'plan', label: 'Plan mode', hint: 'Explores and proposes a plan; changes nothing', icon: MapIcon, tone: 'plan' },
  { id: 'auto', label: 'Auto', hint: 'Approves routine actions, asks about risky ones', icon: Zap, tone: 'info' },
  { id: 'bypassPermissions', label: 'Bypass permissions', hint: 'Runs everything without asking — sandboxes only', icon: ShieldOff, tone: 'err' },
];

/** The permission mode chip and its menu. */
export function ModeMenu({ sessionId, mode }: { sessionId: string; mode: PermissionMode }) {
  const isRoot = useStore((s) => s.info?.isRoot);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const info = MODES.find((m) => m.id === mode) || MODES[0];
  const items: MenuItem[] = MODES.filter((m) => !(m.id === 'bypassPermissions' && isRoot)).map((m) => ({ label: m.label, hint: m.hint, icon: m.icon, checked: m.id === mode, danger: m.id === 'bypassPermissions', onSelect: () => setMode(sessionId, m.id).catch(toasted) }));
  return (
    <>
      <button ref={setAnchor} className={`cbtn mode ${mode !== 'default' ? info.tone || 'set' : ''}`} onClick={() => setOpen(!open)} title="Permission mode (Shift+Tab to switch)" aria-haspopup="menu" aria-expanded={open}>
        <info.icon size={14} aria-hidden="true" />
        <span className="cbtn-label">{info.label}</span>
        <ChevronDown size={12} className="cbtn-chev" aria-hidden="true" />
      </button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} placement="top-start" width={372}>
        <Menu label="Permission mode" items={items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}

const effortName = (l: string) => (l === 'xhigh' ? 'Extra high' : l[0].toUpperCase() + l.slice(1));

const hostOf = (url: string) => {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};

/** The model chip (with the effort, when set) and its menu: the gateway's models, then the effort. */
export function ModelMenu({ sessionId }: { sessionId: string }) {
  const own = useStore((s) => s.rt[sessionId]?.models);
  const current = useStore((s) => s.rt[sessionId]?.model) || '';
  const active = useStore((s) => s.rt[sessionId]?.activeModel);
  const effort = useStore((s) => s.rt[sessionId]?.effort);
  // Before this session's first reply Claude Code has not listed its models yet; borrow the list (and
  // the default's name) from another session so the menu is never a dead end and the label never jumps.
  const borrowed = useStore((s) => Object.values(s.rt).find((r) => r?.models?.length)?.models);
  const defaultSeen = useStore((s) => Object.values(s.rt).find((r) => r?.activeModel && !r?.model)?.activeModel);
  const gw = useGatewayModels();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const models: ModelInfo[] = own?.length ? own : borrowed?.length ? borrowed : ALIASES;
  const info = models.find((m) => m.value === current) || models.find((m) => m.value === 'default');
  const levels = (info?.supportedEffortLevels || (info?.supportsEffort ? ['low', 'medium', 'high', 'xhigh', 'max'] : [])) as Effort[];
  const shown = current || active || defaultSeen || '';
  const items: MenuItem[] = [
    { heading: 'Model' },
    { label: 'Default', hint: 'Whatever Claude Code is configured to use', checked: !current, onSelect: () => setModel(sessionId, '').catch(toasted) },
    // The gateway's own models. Claude Code's built-in aliases (Opus/Sonnet/Haiku) are not offered:
    // everything goes through the gateway, which may not know them.
    ...(gw.models.length || gw.loading || gw.error
      ? [
          { heading: gw.base ? `On ${hostOf(gw.base)}` : 'On your gateway' },
          ...(gw.loading ? [{ label: 'Asking the gateway…', disabled: true }] : []),
          ...gw.models.map((m) => ({ label: m.displayName ? m.displayName.replace(/^Claude\s+/i, '') : m.id, hint: m.displayName ? m.id : undefined, mono: true, checked: current === m.id, onSelect: () => setModel(sessionId, m.id).catch(toasted) })),
          ...(gw.error ? [{ label: "Couldn't list the models", hint: gw.error, disabled: true }] : []),
          ...(!gw.loading ? [{ label: gw.error ? 'Try again' : 'Refresh the list', icon: RefreshCw, onSelect: () => loadGatewayModels(true) }] : []),
        ]
      : []),
    ...(levels.length ? [{ separator: true }, { heading: 'Effort' }, { label: 'Default', checked: !effort, onSelect: () => setEffort(sessionId, '').catch(toasted) }, ...levels.map((l) => ({ label: effortName(l), checked: effort === l, onSelect: () => setEffort(sessionId, l).catch(toasted) }))] : []),
  ];
  return (
    <>
      <button
        ref={setAnchor}
        className="cbtn"
        onClick={() => {
          if (!open) loadGatewayModels();
          setOpen(!open);
        }}
        title="Model and effort"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span>{shown ? modelLabel(shown, gw.models, models) : 'Default model'}</span>
        {effort && <span className="cbtn-sub">· {effortName(effort)}</span>}
        <ChevronDown size={12} className="cbtn-chev" aria-hidden="true" />
      </button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} placement="top-end" width={320}>
        <Menu label="Model and effort" items={items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}
