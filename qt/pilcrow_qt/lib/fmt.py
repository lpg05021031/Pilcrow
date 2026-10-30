"""Formatting helpers (port of web/src/lib/format.ts)."""

from __future__ import annotations

import math
import re
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional


def _js_round(x: float) -> int:
    """JavaScript Math.round: halves go up, not to even."""
    return int(math.floor(x + 0.5))


def _to_fixed(x: float, digits: int) -> str:
    """JavaScript Number.toFixed: the exact binary value rounded half away from zero."""
    return str(Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _js_num(n: Any) -> str:
    """A number the way JavaScript prints it: no trailing `.0` on whole floats."""
    if isinstance(n, float) and n.is_integer() and abs(n) < 1e21:
        return str(int(n))
    return str(n)


def time_ago(ms: Optional[float], now: Optional[float] = None, short: bool = False) -> str:
    """Relative time. `short` gives the compact form used in lists ("now", "4m", "3h", "2d")."""
    if not ms:
        return ""
    if now is None:
        now = time.time() * 1000
    s = max(0, _js_round((now - ms) / 1000))
    if s < 45:
        return "now" if short else "just now"
    m = _js_round(s / 60)
    if m < 60:
        return f"{m}m" if short else f"{m}m ago"
    h = _js_round(m / 60)
    if h < 24:
        return f"{h}h" if short else f"{h}h ago"
    d = _js_round(h / 24)
    if d < 7:
        return f"{d}d" if short else f"{d}d ago"
    # toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) in the browser; "Nov 14" here.
    dt = datetime.fromtimestamp(ms / 1000)
    return f"{dt.strftime('%b')} {dt.day}"


def duration(ms: float) -> str:
    if not ms or ms < 0:
        return "0s"
    s = ms / 1000
    if s < 9.95:
        return f"{_to_fixed(s, 1)}s"
    # Round once, then split, so 119.6s reads "2m" rather than "1m 60s".
    total = _js_round(s)
    if total < 60:
        return f"{total}s"
    m = total // 60
    r = total % 60
    if m < 60:
        return f"{m}m {r}s" if r else f"{m}m"
    h = m // 60
    return f"{h}h {m % 60}m" if m % 60 else f"{h}h"


def tokens(n: float) -> str:
    if not n:
        return "0"
    if n < 1000:
        return _js_num(n)
    if n < 1_000_000:
        return f"{_to_fixed(n / 1000, 1 if n < 10_000 else 0)}k"
    return f"{_to_fixed(n / 1_000_000, 2 if n < 10_000_000 else 1)}M"


def cost(usd: float) -> str:
    if not usd:
        return "$0.00"
    if usd < 0.01:
        return "<$0.01"
    return f"${_to_fixed(usd, 2 if usd < 10 else 1)}"


def bytes_(n: float) -> str:
    if n < 1024:
        return f"{_js_num(n)} B"
    if n < 1024 * 1024:
        return f"{_to_fixed(n / 1024, 1)} KB"
    return f"{_to_fixed(n / 1024 / 1024, 1)} MB"


def _norm(p: str) -> str:
    """Windows paths ("C:\\Users\\me", from Claude Code's tools) are shown with forward slashes like the rest."""
    return p.replace("\\", "/") if "\\" in p else p


def _is_drive_path(p: str) -> bool:
    return re.match(r"^[A-Za-z]:/", p) is not None


def _same_path(a: str, b: str) -> bool:
    """Same path? Drive paths compare without case, as Windows does."""
    if _is_drive_path(a) or _is_drive_path(b):
        return a.lower() == b.lower()
    return a == b


def _inside(p: str, d: str) -> bool:
    if _is_drive_path(p):
        return p.lower().startswith(d.lower() + "/")
    return p.startswith(d + "/")


def basename(p: str) -> str:
    if not p:
        return ""
    t = re.sub(r"/+\Z", "", _norm(p))
    return t[t.rfind("/") + 1 :] or t


def dirname(p: str) -> str:
    t = re.sub(r"/+\Z", "", _norm(p))
    i = t.rfind("/")
    if _is_drive_path(t) and i == 2:
        return t[:3]
    return "/" if i <= 0 else t[:i]


def tildify(p: str, home: Optional[str] = None) -> str:
    if not p or not home:
        return p
    p = _norm(p)
    home = _norm(home)
    if _same_path(p, home):
        return "~"
    return "~" + p[len(home) :] if _inside(p, home) else p


_HOME = ""


def set_home(home: Optional[str]) -> None:
    """Set once the server reports the user's home folder, so paths outside the project show as ~/…"""
    global _HOME
    _HOME = home or ""


def rel_path(p: str, base: Optional[str] = None) -> str:
    """Path relative to a base directory when it lives inside it; otherwise shortened with ~."""
    if not p:
        return p
    p = _norm(p)
    if base:
        b = re.sub(r"/+\Z", "", _norm(base))
        if _same_path(p, b):
            return "."
        if _inside(p, b):
            return p[len(b) + 1 :]
    return tildify(p, _HOME)


def truncate(s: Optional[str], n: int) -> str:
    if not s:
        return ""
    return s[: n - 1] + "…" if len(s) > n else s


def first_line(s: Optional[str]) -> str:
    return (s or "").split("\n")[0]


def plural(n: float, one: str, many: Optional[str] = None) -> str:
    if many is None:
        many = one + "s"
    return f"{_js_num(n)} {one if n == 1 else many}"


def short_model(model_id: Optional[str]) -> str:
    if not model_id:
        return "Default"
    m = re.search(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", model_id, re.I)
    if m:
        minor = "." + m.group(3) if m.group(3) and len(m.group(3)) <= 2 else ""
        return f"{m.group(1)[0].upper()}{m.group(1)[1:]} {m.group(2)}{minor}"
    return model_id[:1].upper() + model_id[1:]


_LANGS: Dict[str, str] = {
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "jsx",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "tsx",
    "json": "json",
    "jsonc": "jsonc",
    "jsonl": "json",
    "md": "markdown",
    "mdx": "markdown",
    "py": "python",
    "pyi": "python",
    "rb": "ruby",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "swift": "swift",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "cs": "csharp",
    "php": "php",
    "html": "html",
    "htm": "html",
    "vue": "vue",
    "svelte": "svelte",
    "css": "css",
    "scss": "scss",
    "less": "less",
    "sql": "sql",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "fish": "fish",
    "ps1": "powershell",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "ini": "ini",
    "cfg": "ini",
    "conf": "ini",
    "xml": "xml",
    "svg": "xml",
    "v": "verilog",
    "vh": "verilog",
    "sv": "system-verilog",
    "svh": "system-verilog",
    "vhd": "vhdl",
    "vhdl": "vhdl",
    "tcl": "tcl",
    "sdc": "tcl",
    "xdc": "tcl",
    "pl": "perl",
    "pm": "perl",
    "lua": "lua",
    "r": "r",
    "m": "matlab",
    "scala": "scala",
    "dart": "dart",
    "ex": "elixir",
    "exs": "elixir",
    "hs": "haskell",
    "ml": "ocaml",
    "clj": "clojure",
    "proto": "proto",
    "graphql": "graphql",
    "gql": "graphql",
    "tf": "hcl",
    "hcl": "hcl",
    "diff": "diff",
    "patch": "diff",
    "mk": "make",
    "cmake": "cmake",
    "nix": "nix",
    "zig": "zig",
    "jl": "julia",
    "f90": "fortran-free-form",
    "f": "fortran-fixed-form",
    "asm": "asm",
    "s": "asm",
    "csv": "csv",
    "tex": "latex",
    "rst": "rst",
    "txt": "text",
    "log": "log",
}


def lang_from_path(p: str) -> str:
    name = basename(p).lower()
    if name == "dockerfile":
        return "dockerfile"
    if name in ("makefile", "gnumakefile"):
        return "make"
    if name == "cmakelists.txt":
        return "cmake"
    ext = name[name.rfind(".") + 1 :] if "." in name else ""
    return _LANGS.get(ext, "text")


def err_text(e: Any) -> str:
    """The message of whatever was thrown: an exception, a string, or something else entirely."""
    if isinstance(e, BaseException):
        return str(e)
    if isinstance(e, str):
        return e
    # RPC rejections and SDK errors can arrive as plain objects with a message.
    if isinstance(e, dict) and isinstance(e.get("message"), str):
        return e["message"]
    if e is not None and not isinstance(e, (int, float, bool, list)) and isinstance(getattr(e, "message", None), str):
        return e.message
    return str(e)


def resolve_path(p: str, cwd: str) -> str:
    """A path as a tool or a link names it, made absolute against the working directory."""
    if not p or p.startswith("/") or p.startswith("~/") or re.match(r"^[A-Za-z]:[\\/]", p):
        return p
    base = (cwd or "").rstrip("/")
    return f"{base}/{p[2:] if p.startswith('./') else p}" if base else p
