import { useEffect, useState } from 'react';
import { loadGatewayModels } from '../lib/store';
import { modelOptions, useGatewayModels } from '../lib/models';
import { Select } from './ui/Select';

const CUSTOM = '__custom';

/**
 * Model picker for new sessions and settings: Default, Claude Code's aliases, every model the API endpoint
 * lists, and a free-form ID for anything else. `typing` is true while the person types a custom ID, so
 * callers that save on every change can wait for `onCommit` instead.
 */
export function ModelSelect({ value, onChange, onCommit }: { value: string; onChange: (v: string, typing: boolean) => void; onCommit?: () => void }) {
  const gw = useGatewayModels();
  useEffect(() => {
    loadGatewayModels();
  }, []);
  const options = modelOptions(gw.models);
  const known = options.some((o) => o.value === value);
  const [custom, setCustom] = useState(!known);
  // The list can arrive after the dialog opened; a value it contains is no longer "custom".
  useEffect(() => {
    if (known && value) setCustom(false);
  }, [known, value]);

  const note = gw.loading ? 'Asking the gateway for its models…' : gw.error ? `Couldn't list the gateway's models: ${gw.error}` : 'Any model ID the endpoint accepts';
  return (
    <div className="model-select">
      <Select
        value={custom ? CUSTOM : value}
        width={320}
        onChange={(v) => {
          if (v === CUSTOM) return setCustom(true);
          setCustom(false);
          onChange(v, false);
        }}
        options={[...options, { value: CUSTOM, label: 'Custom model ID…', hint: note }]}
      />
      {custom && (
        <input
          className="input mono"
          value={value}
          autoFocus
          spellCheck={false}
          placeholder="claude-opus-5-5"
          onChange={(e) => onChange(e.target.value, true)}
          onBlur={() => onCommit?.()}
          onKeyDown={(e) => e.key === 'Enter' && onCommit?.()}
        />
      )}
    </div>
  );
}
