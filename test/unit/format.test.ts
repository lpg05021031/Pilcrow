import { describe, expect, it } from 'vitest';
import { basename, bytes, cost, dirname, duration, errText, langFromPath, relPath, setHome, shortModel, tildify, timeAgo, tokens, truncate } from '../../web/src/lib/format';

describe('timeAgo', () => {
  const now = 1_700_000_000_000;
  it('short and long forms', () => {
    expect(timeAgo(now - 10_000, now)).toBe('just now');
    expect(timeAgo(now - 10_000, now, true)).toBe('now');
    expect(timeAgo(now - 5 * 60_000, now)).toBe('5m ago');
    expect(timeAgo(now - 5 * 60_000, now, true)).toBe('5m');
    expect(timeAgo(now - 3 * 3600_000, now, true)).toBe('3h');
    expect(timeAgo(now - 2 * 86400_000, now, true)).toBe('2d');
  });
  it('empty for no time', () => {
    expect(timeAgo(undefined, now)).toBe('');
  });
});

describe('duration', () => {
  it('rounds once so 119.6s is 2m, not 1m 60s', () => {
    expect(duration(119_600)).toBe('2m');
    expect(duration(0)).toBe('0s');
    expect(duration(1234)).toBe('1.2s');
    expect(duration(45_000)).toBe('45s');
    expect(duration(61_000)).toBe('1m 1s');
    expect(duration(3600_000)).toBe('1h');
    expect(duration(3660_000)).toBe('1h 1m');
  });
});

describe('tokens / cost / bytes', () => {
  it('tokens', () => {
    expect(tokens(0)).toBe('0');
    expect(tokens(999)).toBe('999');
    expect(tokens(1500)).toBe('1.5k');
    expect(tokens(25_000)).toBe('25k');
    expect(tokens(1_500_000)).toBe('1.50M');
    expect(tokens(12_000_000)).toBe('12.0M');
  });
  it('cost', () => {
    expect(cost(0)).toBe('$0.00');
    expect(cost(0.001)).toBe('<$0.01');
    expect(cost(1.234)).toBe('$1.23');
    expect(cost(12.34)).toBe('$12.3');
  });
  it('bytes', () => {
    expect(bytes(10)).toBe('10 B');
    expect(bytes(2048)).toBe('2.0 KB');
    expect(bytes(3 * 1024 * 1024)).toBe('3.0 MB');
  });
});

describe('paths', () => {
  it('basename and dirname, posix and Windows', () => {
    expect(basename('/a/b/c.ts')).toBe('c.ts');
    expect(basename('C:\\Users\\me\\x.txt')).toBe('x.txt');
    expect(basename('/a/b/')).toBe('b');
    expect(dirname('/a/b/c.ts')).toBe('/a/b');
    expect(dirname('C:/x.txt')).toBe('C:/');
    expect(dirname('file')).toBe('/');
  });
  it('tildify compares drive paths without case', () => {
    expect(tildify('/home/me/p/x', '/home/me')).toBe('~/p/x');
    expect(tildify('/home/me', '/home/me')).toBe('~');
    expect(tildify('c:/users/me/p', 'C:\\Users\\me')).toBe('~/p');
    expect(tildify('/other', '/home/me')).toBe('/other');
  });
  it('relPath inside a base, else ~', () => {
    setHome('/home/me');
    expect(relPath('/home/me/proj/src/a.ts', '/home/me/proj')).toBe('src/a.ts');
    expect(relPath('/home/me/proj', '/home/me/proj')).toBe('.');
    expect(relPath('/home/me/other/a.ts', '/home/me/proj')).toBe('~/other/a.ts');
    setHome('');
  });
});

describe('text helpers', () => {
  it('truncate with an ellipsis', () => {
    expect(truncate('hello world', 6)).toBe('hello…');
    expect(truncate('hi', 6)).toBe('hi');
  });
  it('shortModel', () => {
    expect(shortModel('claude-opus-4-1-20250805')).toBe('Opus 4.1');
    expect(shortModel('claude-sonnet-4-20250514')).toBe('Sonnet 4');
    expect(shortModel('gpt-x')).toBe('Gpt-x');
    expect(shortModel(null)).toBe('Default');
  });
  it('langFromPath', () => {
    expect(langFromPath('a/b/Main.TSX')).toBe('tsx');
    expect(langFromPath('Dockerfile')).toBe('dockerfile');
    expect(langFromPath('top.sv')).toBe('system-verilog');
    expect(langFromPath('noext')).toBe('text');
  });
  it('errText reads any thrown shape', () => {
    expect(errText(new Error('boom'))).toBe('boom');
    expect(errText('plain')).toBe('plain');
    expect(errText({ message: 'obj' })).toBe('obj');
    expect(errText(42)).toBe('42');
  });
});
