// util.log: pilcrow.log moves to pilcrow.log.1 once it reaches LOG_MAX_BYTES, when the server starts
// with a full one and while it runs, so a server that runs for weeks keeps at most two files.
import { expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { LOG_MAX_BYTES, log, xdg } from '../../server/util.js';

const file = path.join(xdg('state'), 'pilcrow.log');
const size = (f: string) => (fs.existsSync(f) ? fs.statSync(f).size : 0);
const settled = () => new Promise((r) => setTimeout(r, 100)); // the log is written through a stream

it('rotates a full log at the first line, and again past the cap while running', async () => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'x'.repeat(LOG_MAX_BYTES));
  log('first line');
  await settled();
  expect(size(file + '.1')).toBe(LOG_MAX_BYTES);
  expect(fs.readFileSync(file, 'utf8')).toMatch(/first line\n$/);

  const chunk = 'y'.repeat(1024 * 1024);
  for (let i = 0; i < 5; i++) log(chunk);
  await settled();
  expect(size(file + '.1')).toBeGreaterThanOrEqual(LOG_MAX_BYTES);
  expect(fs.readFileSync(file + '.1', 'utf8')).toContain('first line');
  expect(size(file)).toBeLessThan(2 * 1024 * 1024);
});
