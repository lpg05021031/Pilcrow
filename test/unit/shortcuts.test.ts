import { describe, expect, it } from 'vitest';
import { SHORTCUTS, matches, type Shortcut } from '../../web/src/lib/shortcuts';

const all = Object.values(SHORTCUTS) as Shortcut[];
const key = (combo: string) => {
  const parts = combo.split(/\+(?!$)/);
  const k = parts.pop()!;
  return { key: k, ctrlKey: parts.includes('Ctrl'), metaKey: false, shiftKey: parts.includes('Shift'), altKey: parts.includes('Alt') };
};

describe('keyboard shortcuts', () => {
  it('every entry has keys and a label', () => {
    for (const s of all) {
      expect(s.keys.trim()).not.toBe('');
      expect(s.label.trim()).not.toBe('');
    }
  });

  it('no two bound shortcuts answer to the same key press', () => {
    const bound = all.filter((s) => s.run).flatMap((s) => s.combos || [s.keys]);
    for (const a of bound) expect(bound.filter((b) => matches(b, key(a))), a).toEqual([a]);
  });

  it('matches modifiers exactly, letters in either case, ⌘ as Ctrl', () => {
    expect(matches('Ctrl+Shift+O', { key: 'O', ctrlKey: true, metaKey: false, shiftKey: true, altKey: false })).toBe(true);
    expect(matches('Ctrl+O', { key: 'o', ctrlKey: false, metaKey: true, shiftKey: false, altKey: false })).toBe(true);
    expect(matches('Ctrl+O', { key: 'o', ctrlKey: true, metaKey: false, shiftKey: true, altKey: false })).toBe(false);
    expect(matches('Ctrl+\\', { key: '\\', ctrlKey: true, metaKey: false, shiftKey: false, altKey: false })).toBe(true);
    expect(matches('Alt+3', { key: '3', ctrlKey: false, metaKey: false, shiftKey: false, altKey: true })).toBe(true);
  });
});
