// Formatting helpers.

/** Relative time. `short` gives the compact form used in lists ("now", "4m", "3h", "2d"). */
export function timeAgo(ms?: number, now = Date.now(), short = false): string {
  if (!ms) return '';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 45) return short ? 'now' : 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return short ? `${m}m` : `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return short ? `${h}h` : `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return short ? `${d}d` : `${d}d ago`;
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function duration(ms: number): string {
  if (!ms || ms < 0) return '0s';
  const s = ms / 1000;
  if (s < 9.95) return `${s.toFixed(1)}s`;
  // Round once, then split, so 119.6s reads "2m" rather than "1m 60s".
  const total = Math.round(s);
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const r = total % 60;
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
}

export function tokens(n: number): string {
  if (!n) return '0';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 2 : 1)}M`;
}

export function cost(usd: number): string {
  if (!usd) return '$0.00';
  if (usd < 0.01) return '<$0.01';
  return `$${usd.toFixed(usd < 10 ? 2 : 1)}`;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Windows paths ("C:\\Users\\me", from Claude Code's tools) are shown with forward slashes like the rest. */
const norm = (p: string) => (p.includes('\\') ? p.replace(/\\/g, '/') : p);
const isDrivePath = (p: string) => /^[A-Za-z]:\//.test(p);
/** Same path? Drive paths compare without case, as Windows does. */
const samePath = (a: string, b: string) => (isDrivePath(a) || isDrivePath(b) ? a.toLowerCase() === b.toLowerCase() : a === b);
const inside = (p: string, dir: string) => (isDrivePath(p) ? p.toLowerCase().startsWith(dir.toLowerCase() + '/') : p.startsWith(dir + '/'));

export function basename(p: string): string {
  if (!p) return '';
  const t = norm(p).replace(/\/+$/, '');
  return t.slice(t.lastIndexOf('/') + 1) || t;
}

export function dirname(p: string): string {
  const t = norm(p).replace(/\/+$/, '');
  const i = t.lastIndexOf('/');
  if (isDrivePath(t) && i === 2) return t.slice(0, 3);
  return i <= 0 ? '/' : t.slice(0, i);
}

export function tildify(p: string, home?: string): string {
  if (!p || !home) return p;
  p = norm(p);
  home = norm(home);
  if (samePath(p, home)) return '~';
  return inside(p, home) ? '~' + p.slice(home.length) : p;
}

let HOME = '';
/** Set once the server reports the user's home folder, so paths outside the project show as ~/… */
export function setHome(home: string) {
  HOME = home || '';
}

/** Path relative to a base directory when it lives inside it; otherwise shortened with ~. */
export function relPath(p: string, base?: string): string {
  if (!p) return p;
  p = norm(p);
  if (base) {
    const b = norm(base).replace(/\/+$/, '');
    if (samePath(p, b)) return '.';
    if (inside(p, b)) return p.slice(b.length + 1);
  }
  return tildify(p, HOME);
}

export function truncate(s: string, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function firstLine(s: string): string {
  return (s || '').split('\n')[0];
}

export function plural(n: number, one: string, many = one + 's') {
  return `${n} ${n === 1 ? one : many}`;
}

export function shortModel(id?: string | null): string {
  if (!id) return 'Default';
  const m = id.match(/claude-([a-z]+)-(\d+)(?:-(\d+))?/i);
  if (m) return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] && m[3].length <= 2 ? '.' + m[3] : ''}`;
  return id.charAt(0).toUpperCase() + id.slice(1);
}

export function langFromPath(p: string): string {
  const name = basename(p).toLowerCase();
  if (name === 'dockerfile') return 'dockerfile';
  if (name === 'makefile' || name === 'gnumakefile') return 'make';
  if (name === 'cmakelists.txt') return 'cmake';
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1) : '';
  const map: Record<string, string> = {
    js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx', ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'tsx',
    json: 'json', jsonc: 'jsonc', jsonl: 'json', md: 'markdown', mdx: 'markdown', py: 'python', pyi: 'python', rb: 'ruby', rs: 'rust', go: 'go',
    java: 'java', kt: 'kotlin', kts: 'kotlin', swift: 'swift', c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', cs: 'csharp',
    php: 'php', html: 'html', htm: 'html', vue: 'vue', svelte: 'svelte', css: 'css', scss: 'scss', less: 'less', sql: 'sql', sh: 'bash', bash: 'bash',
    zsh: 'bash', fish: 'fish', ps1: 'powershell', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini', cfg: 'ini', conf: 'ini', xml: 'xml', svg: 'xml',
    v: 'verilog', vh: 'verilog', sv: 'system-verilog', svh: 'system-verilog', vhd: 'vhdl', vhdl: 'vhdl', tcl: 'tcl', sdc: 'tcl', xdc: 'tcl', pl: 'perl', pm: 'perl',
    lua: 'lua', r: 'r', m: 'matlab', scala: 'scala', dart: 'dart', ex: 'elixir', exs: 'elixir', hs: 'haskell', ml: 'ocaml', clj: 'clojure', proto: 'proto',
    graphql: 'graphql', gql: 'graphql', tf: 'hcl', hcl: 'hcl', diff: 'diff', patch: 'diff', mk: 'make', cmake: 'cmake', nix: 'nix', zig: 'zig', jl: 'julia',
    f90: 'fortran-free-form', f: 'fortran-fixed-form', asm: 'asm', s: 'asm', csv: 'csv', tex: 'latex', rst: 'rst', txt: 'text', log: 'log',
  };
  return map[ext] || 'text';
}

/** The message of whatever was thrown: an Error, a string, or something else entirely. */
export function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === 'string') return e;
  // RPC rejections and SDK errors can arrive as plain objects with a message.
  if (e && typeof e === 'object' && typeof (e as { message?: unknown }).message === 'string') return (e as { message: string }).message;
  return String(e);
}
