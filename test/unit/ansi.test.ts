import { describe, expect, it } from 'vitest';
import { parseAnsi, stripAnsi } from '../../web/src/lib/ansi';

describe('parseAnsi', () => {
  it('plain text is one segment', () => {
    expect(parseAnsi('hello')).toEqual([{ text: 'hello', cls: undefined, style: undefined }]);
  });
  it('bold and a basic colour', () => {
    const segs = parseAnsi('\x1b[1;31merr\x1b[0m ok');
    expect(segs).toEqual([
      { text: 'err', cls: 'a-b', style: { color: 'var(--ansi-red)' } },
      { text: ' ok', cls: undefined, style: undefined },
    ]);
  });
  it('256-colour and truecolour', () => {
    expect(parseAnsi('\x1b[38;5;196mx')[0].style).toEqual({ color: 'rgb(255,0,0)' });
    expect(parseAnsi('\x1b[48;2;1;2;3mx')[0].style).toEqual({ background: 'rgb(1,2,3)' });
    expect(parseAnsi('\x1b[38;5;240mx')[0].style).toEqual({ color: 'rgb(88,88,88)' });
  });
  it('drops cursor movement and OSC sequences', () => {
    const segs = parseAnsi('a\x1b[2Kb\x1b]0;title\x07c');
    expect(segs.map((s) => s.text).join('')).toBe('abc');
    expect(segs.every((s) => !s.cls && !s.style)).toBe(true);
  });
  it('a bare carriage return is dropped, CRLF keeps its newline', () => {
    expect(stripAnsi('a\rb\r\nc')).toBe('ab\r\nc'.replace('\r\n', '\r\n'));
    expect(stripAnsi('x\ry')).toBe('xy');
  });
});
