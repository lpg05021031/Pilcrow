import { useStore } from './store';
import type { GatewayModel } from './types';
import { shortModel } from './format';

/** Aliases Claude Code always understands; used until a session has reported its own list. */
export const ALIASES = [
  { value: 'opus', displayName: 'Opus', description: 'Most capable' },
  { value: 'sonnet', displayName: 'Sonnet', description: 'Fast and capable' },
  { value: 'haiku', displayName: 'Haiku', description: 'Fastest' },
];

export interface GatewayModelState {
  models: GatewayModel[];
  loading: boolean;
  /** Something went wrong that is worth showing (not "this endpoint has no list"). */
  error: string | null;
  base: string | null;
}

/** The endpoint's model list as the UI should show it. */
export function useGatewayModels(): GatewayModelState {
  const g = useStore((s) => s.gatewayModels);
  if (g === 'loading') return { models: [], loading: true, error: null, base: null };
  if (!g) return { models: [], loading: false, error: null, base: null };
  return {
    models: g.ok ? g.models || [] : [],
    loading: false,
    error: g.ok || g.noCredential ? null : g.error || 'Could not list the models',
    base: g.base || null,
  };
}

/** "Claude Opus 5.5 (1M context)" → "Opus 5.5 (1M context)": the chip has little room and every model here is Claude. */
const trimClaude = (s: string) => s.replace(/^Claude\s+/i, '');

/** A short, human name for a model value: the endpoint's display name, Claude Code's alias name, or a tidied ID. */
export function modelLabel(value: string, gateway: GatewayModel[], aliases: { value: string; displayName?: string }[] = ALIASES): string {
  const g = gateway.find((m) => m.id === value);
  if (g?.displayName) return trimClaude(g.displayName);
  const a = aliases.find((m) => m.value === value);
  if (a?.displayName) return a.displayName;
  return /claude-/i.test(value) ? shortModel(value) : value;
}

/**
 * Options for model pickers: Default, then the gateway's own models. Claude Code's built-in aliases
 * (Opus/Sonnet/Haiku) are not offered: everything here goes through the gateway.
 */
export function modelOptions(gateway: GatewayModel[]) {
  return [
    { value: '', label: 'Default', hint: "Claude Code's configured model" },
    ...gateway.map((m) => ({ value: m.id, label: m.displayName ? trimClaude(m.displayName) : m.id, hint: m.displayName ? m.id : undefined, mono: true })),
  ];
}
