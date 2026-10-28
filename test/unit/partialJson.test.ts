import { describe, expect, it } from 'vitest';
import { parsePartialJson } from '../../web/src/lib/partialJson';

describe('parsePartialJson', () => {
  it('returns {} for empty input', () => {
    expect(parsePartialJson('')).toEqual({});
  });
  it('parses complete JSON as is', () => {
    expect(parsePartialJson('{"a":1,"b":[1,2]}')).toEqual({ a: 1, b: [1, 2] });
  });
  it('closes an open string', () => {
    expect(parsePartialJson('{"command": "ls -l')).toEqual({ command: 'ls -l' });
  });
  it('closes nested arrays and objects', () => {
    expect(parsePartialJson('{"a": {"b": [1, 2')).toEqual({ a: { b: [1, 2] } });
  });
  it('drops a dangling key, with or without its colon', () => {
    expect(parsePartialJson('{"file_path": "/x.ts", "old')).toEqual({ file_path: '/x.ts' });
    expect(parsePartialJson('{"file_path": "/x.ts", "old_string":')).toEqual({ file_path: '/x.ts' });
  });
  it('a lone value-less key becomes null', () => {
    expect(parsePartialJson('{"a":')).toEqual({ a: null });
  });
  it('drops a trailing comma', () => {
    expect(parsePartialJson('{"a": 1,')).toEqual({ a: 1 });
  });
  it('handles an escape cut in half', () => {
    expect(parsePartialJson('{"s": "a\\')).toEqual({ s: 'a' });
    expect(parsePartialJson('{"s": "a\\"b')).toEqual({ s: 'a"b' });
  });
  it('returns null for garbage', () => {
    expect(parsePartialJson('{{{')).toBeNull();
  });
});
