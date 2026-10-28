// Rasterize the Pilcrow icon (the paragraph mark) to PNG without any image libraries (signed-distance shapes + zlib).
// Usage: node scripts/make-icon.mjs [size ...]   -> assets/icon-<size>.png
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'assets');
const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const BG = hex('#1f1f1f');
const INK = hex('#fafafa');
const CLAY = hex('#d97757');

// Signed distance functions in the 256-unit design space.
const roundBox = (x, y, cx, cy, hw, hh, r) => {
  const qx = Math.abs(x - cx) - (hw - r);
  const qy = Math.abs(y - cy) - (hh - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
};
const ring = (x, y, cx, cy, r, w) => Math.abs(Math.hypot(x - cx, y - cy) - r) - w / 2;
const capsule = (x, y, ax, ay, bx, by, w) => {
  const pax = x - ax, pay = y - ay, bax = bx - ax, bay = by - ay;
  const h = Math.max(0, Math.min(1, (pax * bax + pay * bay) / (bax * bax + bay * bay)));
  return Math.hypot(pax - bax * h, pay - bay * h) - w / 2;
};
const disc = (x, y, cx, cy, r) => Math.hypot(x - cx, y - cy) - r;

function render(size) {
  const s = 256 / size;
  const px = Buffer.alloc(size * size * 4);
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      const x = (i + 0.5) * s;
      const y = (j + 0.5) * s;
      const cov = (d) => Math.max(0, Math.min(1, 0.5 - d / s));
      let r = 0, g = 0, b = 0, a = 0;
      const over = (col, alpha) => {
        if (alpha <= 0) return;
        const na = alpha + a * (1 - alpha);
        r = (col[0] * alpha + r * a * (1 - alpha)) / na;
        g = (col[1] * alpha + g * a * (1 - alpha)) / na;
        b = (col[2] * alpha + b * a * (1 - alpha)) / na;
        a = na;
      };
      // The pilcrow: an orange bowl, then the top bar and two stems in paper white.
      const body = roundBox(x, y, 128, 128, 128, 128, 60);
      over(BG, cov(body));
      over(CLAY, cov(disc(x, y, 105, 98, 43.5)));
      const stems = Math.min(roundBox(x, y, 139.5, 65.25, 34.5, 10.25, 4.5), roundBox(x, y, 133.75, 128, 10.25, 73, 4.5), roundBox(x, y, 163.25, 128, 10.25, 73, 4.5));
      over(INK, cov(stems));
      const o = (j * size + i) * 4;
      px[o] = Math.round(r);
      px[o + 1] = Math.round(g);
      px[o + 2] = Math.round(b);
      px[o + 3] = Math.round(a * 255);
    }
  }
  return px;
}

const CRC = new Int32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});
function crc32(buf) {
  let c = -1;
  for (const byte of buf) c = CRC[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size) {
  const px = render(size);
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) px.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

/** Windows icon: an ICO whose entries are PNG images (supported since Vista), 16 to 256 px. */
function ico(sizes) {
  const images = sizes.map((s) => png(s));
  const head = Buffer.alloc(6 + 16 * sizes.length);
  head.writeUInt16LE(0, 0);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(sizes.length, 4);
  let offset = head.length;
  sizes.forEach((s, i) => {
    const e = 6 + 16 * i;
    head[e] = s >= 256 ? 0 : s;
    head[e + 1] = s >= 256 ? 0 : s;
    head.writeUInt16LE(1, e + 4);
    head.writeUInt16LE(32, e + 6);
    head.writeUInt32LE(images[i].length, e + 8);
    head.writeUInt32LE(offset, e + 12);
    offset += images[i].length;
  });
  return Buffer.concat([head, ...images]);
}

const sizes = process.argv.slice(2).map(Number).filter(Boolean);
fs.mkdirSync(OUT, { recursive: true });
for (const size of sizes.length ? sizes : [64, 128, 256, 512]) {
  fs.writeFileSync(path.join(OUT, `icon-${size}.png`), png(size));
  console.log(`assets/icon-${size}.png`);
}
if (!sizes.length) {
  fs.writeFileSync(path.join(OUT, 'icon.ico'), ico([16, 24, 32, 48, 64, 256]));
  console.log('assets/icon.ico');
}
