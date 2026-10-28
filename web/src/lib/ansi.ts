// Minimal ANSI SGR → styled segments (for command output). Other escape sequences are dropped.

export interface Seg {
  text: string;
  cls?: string;
  style?: Record<string, string>;
}

const BASIC = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'];

function xterm256(n: number): string {
  if (n < 16) return `var(--ansi-${n < 8 ? BASIC[n] : 'bright-' + BASIC[n - 8]})`;
  if (n >= 232) {
    const v = 8 + (n - 232) * 10;
    return `rgb(${v},${v},${v})`;
  }
  const i = n - 16;
  const c = (x: number) => (x === 0 ? 0 : 55 + x * 40);
  return `rgb(${c(Math.floor(i / 36))},${c(Math.floor(i / 6) % 6)},${c(i % 6)})`;
}

 
const ESC_RE = /\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]|\r(?!\n)/g;

export function parseAnsi(input: string): Seg[] {
  const out: Seg[] = [];
  let fg: string | null = null;
  let bg: string | null = null;
  let bold = false;
  let dim = false;
  let italic = false;
  let underline = false;
  let last = 0;
  const push = (text: string) => {
    if (!text) return;
    const cls = [bold && 'a-b', dim && 'a-d', italic && 'a-i', underline && 'a-u'].filter(Boolean).join(' ');
    const style: Record<string, string> = {};
    if (fg) style.color = fg;
    if (bg) style.background = bg;
    out.push({ text, cls: cls || undefined, style: fg || bg ? style : undefined });
  };
  let m: RegExpExecArray | null;
  ESC_RE.lastIndex = 0;
  while ((m = ESC_RE.exec(input))) {
    push(input.slice(last, m.index));
    last = ESC_RE.lastIndex;
    if (m[2] !== 'm') continue;
    const codes = (m[1] || '0').split(';').map((x) => Number(x || 0));
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) [fg, bg, bold, dim, italic, underline] = [null, null, false, false, false, false];
      else if (c === 1) bold = true;
      else if (c === 2) dim = true;
      else if (c === 3) italic = true;
      else if (c === 4) underline = true;
      else if (c === 22) [bold, dim] = [false, false];
      else if (c === 23) italic = false;
      else if (c === 24) underline = false;
      else if (c >= 30 && c <= 37) fg = `var(--ansi-${BASIC[c - 30]})`;
      else if (c === 39) fg = null;
      else if (c >= 40 && c <= 47) bg = `var(--ansi-${BASIC[c - 40]})`;
      else if (c === 49) bg = null;
      else if (c >= 90 && c <= 97) fg = `var(--ansi-bright-${BASIC[c - 90]})`;
      else if (c >= 100 && c <= 107) bg = `var(--ansi-bright-${BASIC[c - 100]})`;
      else if ((c === 38 || c === 48) && codes[i + 1] === 5) {
        const col = xterm256(codes[i + 2] || 0);
        if (c === 38) fg = col;
        else bg = col;
        i += 2;
      } else if ((c === 38 || c === 48) && codes[i + 1] === 2) {
        const col = `rgb(${codes[i + 2] || 0},${codes[i + 3] || 0},${codes[i + 4] || 0})`;
        if (c === 38) fg = col;
        else bg = col;
        i += 4;
      }
    }
  }
  push(input.slice(last));
  return out;
}

export function stripAnsi(s: string) {
  return s.replace(ESC_RE, '');
}
