// files.js and the path helpers in util.js: what the editor reads and writes, and which paths count
// as inside a folder; and which folders customize.js lets the Skills page remove.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as files from '../../server/files.js';
import * as customize from '../../server/customize.js';
import { absolute, under } from '../../server/util.js';

const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-files-')));
const at = (...p: string[]) => path.join(dir, ...p);

describe('paths', () => {
  it('under() goes by whole segments', () => {
    expect(under('/a/b', '/a/b')).toBe(true);
    expect(under('/a/b', '/a/b/c.txt')).toBe(true);
    expect(under('/a/b/', '/a/b/c')).toBe(true);
    expect(under('/a/b', '/a/bc')).toBe(false);
    expect(under('/a/b', '/a')).toBe(false);
    expect(under('', '/a')).toBe(false);
  });

  it('absolute() expands ~, resolves and drops a trailing slash', () => {
    expect(absolute('~/code/')).toBe(path.join(os.homedir(), 'code'));
    expect(absolute(' /x/y/../z/ ')).toBe('/x/z');
    expect(absolute('/')).toBe('/');
  });
});

describe('writeFile', () => {
  it('writes a new file, folders and all', async () => {
    const r = await files.writeFile(at('new', 'deep', 'a.txt'), 'hello');
    expect(r.ok).toBe(true);
    expect(fs.readFileSync(at('new', 'deep', 'a.txt'), 'utf8')).toBe('hello');
  });

  it('keeps the mode, writes through a link, and leaves no temp file', async () => {
    fs.writeFileSync(at('run.sh'), 'echo 1\n', { mode: 0o755 });
    fs.symlinkSync(at('run.sh'), at('link.sh'));
    await files.writeFile(at('link.sh'), 'echo 2\n');
    expect(fs.lstatSync(at('link.sh')).isSymbolicLink()).toBe(true);
    expect(fs.readFileSync(at('run.sh'), 'utf8')).toBe('echo 2\n');
    expect(fs.statSync(at('run.sh')).mode & 0o777).toBe(0o755);
    expect(fs.readdirSync(dir).filter((f) => f.endsWith('.tmp'))).toEqual([]);
  });

  it('refuses to overwrite a change made since the editor read the file, unless forced', async () => {
    const { mtime } = await files.writeFile(at('c.txt'), 'one');
    fs.writeFileSync(at('c.txt'), 'changed elsewhere');
    fs.utimesSync(at('c.txt'), new Date(), new Date(Date.now() + 5000));
    expect(await files.writeFile(at('c.txt'), 'two', { expectedMtime: mtime })).toMatchObject({ ok: false, conflict: true });
    expect(fs.readFileSync(at('c.txt'), 'utf8')).toBe('changed elsewhere');
    expect((await files.writeFile(at('c.txt'), 'two', { expectedMtime: mtime, force: true })).ok).toBe(true);
  });
});

describe('readFile and listDir', () => {
  it('reads text, says binary for bytes, and sends an image as a data URL', async () => {
    fs.writeFileSync(at('t.md'), '# hi');
    fs.writeFileSync(at('b.dat'), Buffer.from([0x50, 0, 0x51]));
    fs.writeFileSync(at('p.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    expect(await files.readFile(at('t.md'))).toMatchObject({ text: '# hi', size: 4 });
    expect(await files.readFile(at('b.dat'))).toMatchObject({ binary: true });
    expect((await files.readFile(at('p.png'))).dataUrl).toBe('data:image/png;base64,iVBORw==');
    await expect(files.readFile(at('new'))).rejects.toThrow('Not a file');
    await expect(files.readFile(at('nope.txt'))).rejects.toMatchObject({ code: 'ENOENT' });
  });

  it('lists folders first, then names as people sort them, without .git', async () => {
    const d = at('list');
    for (const sub of ['.git', 'zdir', 'Adir']) fs.mkdirSync(path.join(d, sub), { recursive: true });
    for (const f of ['file10.txt', 'file2.txt']) fs.writeFileSync(path.join(d, f), 'x');
    const r = await files.listDir(d);
    expect(r.entries.map((e) => `${e.type}:${e.name}`)).toEqual(['dir:Adir', 'dir:zdir', 'file:file2.txt', 'file:file10.txt']);
    expect((await files.listDir(at('missing'))).error).toMatch(/ENOENT/);
  });

  it('gives content types from one table; /api/raw types only images', () => {
    expect(files.mimeOf('/x/index.HTML')).toBe('text/html; charset=utf-8');
    expect(files.mimeOf('/x/a.pdf')).toBe('application/pdf');
    expect(files.imageMime('/x/a.svg')).toBe('image/svg+xml');
    expect(files.imageMime('/x/a.html')).toBe(null);
  });
});

describe('removing a skill', () => {
  it('removes a folder directly in a skills root, and nothing above, below or beside it', () => {
    const root = customize.skillRoots().user;
    const skill = path.join(root, 'tidy');
    fs.mkdirSync(path.join(skill, 'deeper'), { recursive: true });
    fs.writeFileSync(path.join(skill, 'SKILL.md'), '---\nname: tidy\n---\n');
    fs.mkdirSync(root + '-other/x', { recursive: true });
    for (const dir of [root, path.join(skill, 'deeper'), root + '-other/x', path.join(root, '..', 'skills', '..')]) expect(() => customize.removeSkill({ dir }), dir).toThrow(/Only skills in/);
    expect(customize.removeSkill({ dir: skill })).toBe(true);
    expect(fs.existsSync(skill)).toBe(false);
  });
});
