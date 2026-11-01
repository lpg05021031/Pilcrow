// The inbox: one list of what ran while nobody watched (a scheduled session, a Dispatch job, a lead's
// helpers), newest first, each item read or unread. Its producers push and update items; the clients
// list them, mark them read and remove them.
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { xdg, readJson, jsonFile, uuid } from './util.js';

export const events = new EventEmitter();

const FILE = path.join(xdg('state'), 'inbox.json');
const MAX_ITEMS = 400;

/** @type {{ items: Record<string, any>[] } | null} read on first use: importing must not touch the person's files */
let data = null;
const box = () => (data ??= readJson(FILE, { items: [] }));
// Off the event loop (the file is megabytes at its cap); the windows list it from memory meanwhile.
const file = jsonFile(FILE, box);

function save() {
  file.save();
  events.emit('changed');
}

export function list() {
  const { items } = box();
  return { items, unread: items.filter((i) => !i.read).length };
}

/** @param {string[] | null} ids every item when null */
export function markRead(ids) {
  for (const i of box().items) if (!ids || ids.includes(i.id)) i.read = true;
  save();
  return list().unread;
}

export function remove(id) {
  const b = box();
  b.items = b.items.filter((i) => i.id !== id);
  save();
  return true;
}

/** A new item at the top (unread unless it says otherwise); returns its id. */
export function push(item) {
  const b = box();
  const it = { read: false, ...item, id: item.id || uuid() };
  b.items.unshift(it);
  if (b.items.length > MAX_ITEMS) b.items.length = MAX_ITEMS;
  save();
  return it.id;
}

/** A later change to an item already in the inbox (a report's prose arriving); false when it is gone. */
export function update(id, patch) {
  const it = box().items.find((i) => i.id === id);
  if (!it) return false;
  for (const [k, v] of Object.entries(patch || {})) if (v !== undefined) it[k] = v;
  save();
  return true;
}
