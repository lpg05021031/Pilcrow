import { describe, expect, it } from 'vitest';
// The server's parser is plain JavaScript; the test runs in Node like the server does.
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import { parseHandoff } from '../../server/handoff.js';

describe('parseHandoff', () => {
  it('reads the four lines under the heading', () => {
    const r = parseHandoff('All done.\n\n## Handoff\nAsked: Fix the redirect.\nDid: Carried `next` through the form.\nUnverified: Wayland.\nDecide: Merge without Wayland?\n');
    expect(r).toEqual({ asked: 'Fix the redirect.', did: 'Carried `next` through the form.', unverified: 'Wayland.', ask: 'Merge without Wayland?' });
  });
  it('is null for a reply without one', () => {
    expect(parseHandoff('Just a chat reply.')).toBeNull();
    expect(parseHandoff('')).toBeNull();
    expect(parseHandoff(undefined as unknown as string)).toBeNull();
  });
  it('takes the last section, tolerates bold labels and lower case, and joins continuation lines', () => {
    const text = '## Handoff\nAsked: old\n\nLater text.\n\n### handoff\n- **Asked**: Rename the file.\n- **did**: Renamed it\n  and fixed the imports.\n- Unverified: nothing\n- Decide: none\n';
    expect(parseHandoff(text)).toEqual({ asked: 'Rename the file.', did: 'Renamed it and fixed the imports.', unverified: '', ask: '' });
  });
  it('stops at the next heading and ignores a heading with no labels', () => {
    expect(parseHandoff('## Handoff\nAsked: a\nDid: b\n## Notes\nDecide: not part of it\n')?.ask).toBe('');
    expect(parseHandoff('## Handoff\nnothing structured here\n')).toBeNull();
  });
  it('accepts a full-width colon', () => {
    expect(parseHandoff('## Handoff\nAsked：修標題列\nDid：改了對齊\nUnverified：無\nDecide：要不要也改 Windows？')?.ask).toBe('要不要也改 Windows？');
  });
});
