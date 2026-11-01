// inbox.js: newest first, read and unread, a later change to an item, the cap, and the file: written off
// the event loop, and at once when the server quits (flushSaves).
import { describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as inbox from '../../server/inbox.js';
import { flushSaves } from '../../server/util.js';

const FILE = path.join(process.env.XDG_STATE_HOME!, 'pilcrow', 'inbox.json');
const onDisk = () => JSON.parse(fs.readFileSync(FILE, 'utf8'));

describe('the inbox', () => {
  it('lists the newest first, counts the unread, and says when it changed', () => {
    const changed = vi.fn();
    inbox.events.on('changed', changed);
    const a = inbox.push({ scheduleName: 'nightly', status: 'completed' });
    const b = inbox.push({ scheduleName: 'weekly', status: 'failed', read: true });
    expect(inbox.list().items.map((i) => i.id)).toEqual([b, a]);
    expect(inbox.list().unread).toBe(1);
    expect(changed).toHaveBeenCalledTimes(2);
    inbox.events.off('changed', changed);
  });

  it('updates an item it has, marks some or all read, and removes one', () => {
    const id = inbox.push({ summary: 'first words' });
    expect(inbox.update(id, { summary: 'the report', cost: undefined })).toBe(true);
    expect(inbox.list().items[0]).toMatchObject({ id, summary: 'the report', read: false });
    expect(inbox.update('gone', { summary: 'x' })).toBe(false);
    expect(inbox.markRead([id])).toBe(inbox.list().items.filter((i) => !i.read).length);
    expect(inbox.list().items[0].read).toBe(true);
    expect(inbox.markRead(null)).toBe(0);
    inbox.remove(id);
    expect(inbox.list().items.some((i) => i.id === id)).toBe(false);
  });

  it('keeps the newest 400', () => {
    for (let i = 0; i < 410; i++) inbox.push({ n: i });
    const { items } = inbox.list();
    expect(items).toHaveLength(400);
    expect(items[0].n).toBe(409);
  });

  it('is saved off the event loop, and at once on quit', async () => {
    const id = inbox.push({ summary: 'saved later' });
    expect(fs.existsSync(FILE) && onDisk().items.some((i: { id: string }) => i.id === id)).toBe(false); // not yet: the write is queued
    await vi.waitFor(() => expect(onDisk().items[0].id).toBe(id));
    const last = inbox.push({ summary: 'saved on quit' });
    flushSaves();
    expect(onDisk().items[0].id).toBe(last);
    await new Promise((r) => setTimeout(r, 50)); // no late write lands over it
    expect(onDisk().items[0].id).toBe(last);
  });
});
