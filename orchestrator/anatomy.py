"""anatomy（v2 第 0 段）—— 純 code 的 diff 解剖，零 LLM。

把 unified diff 解析成結構：每檔的 hunks、新檔行號對照表、統計。
兩個下游吃它：static pass 用「稀疏重建」讓掃描器拿到正確行號；
reviewer 用「行號標註的 diff 切片」讓模型報行號報得準。

容錯：① 產的 diff 夾雜 `# xxx: 檔案過大，略過` 這類註記行，解析器一律跳過。

另外住著三個「diff 幾何 predicates」——match_path / line_in_view / line_near。
它們是純幾何、零業務語意，凡是要驗「模型報的 file:line 是不是真的在這份 diff
裡」的地方（pipeline 的 adversary、knowledge 的 evidence 驗證器）都吃這一份，
以免同一個判斷在兩個模組各長一版然後漂移。放在最底層也讓 knowledge 不必反向
import pipeline（那會成環）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FILE_RE = re.compile(r"^\+\+\+ b(?P<path>/.*)$")
_OLD_RE = re.compile(r"^--- (?:a(?P<path>/.*)|/dev/null)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    # (tag, new_lineno|None, text)；tag ∈ {"+", "-", " "}；"-" 行沒有新行號
    lines: list[tuple[str, int | None, str]] = field(default_factory=list)


@dataclass
class FileChange:
    path: str                      # 新檔路徑（照 diff 原樣，通常以 / 開頭）
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> list[tuple[int, str]]:
        """所有新增行：(新檔行號, 內容)。"""
        return [(n, t) for h in self.hunks for tag, n, t in h.lines
                if tag == "+" and n is not None]

    @property
    def added_linenos(self) -> set[int]:
        return {n for n, _ in self.added}


@dataclass
class Anatomy:
    files: list[FileChange] = field(default_factory=list)
    skipped_notes: list[str] = field(default_factory=list)  # 過大/二進位等註記

    @property
    def stats(self) -> dict:
        return {
            "files": len(self.files),
            "added_lines": sum(len(f.added) for f in self.files),
            "hunks": sum(len(f.hunks) for f in self.files),
            "skipped": len(self.skipped_notes),
        }


def parse_diff(diff: str) -> Anatomy:
    ana = Anatomy()
    cur: FileChange | None = None
    hunk: Hunk | None = None
    new_no = old_no = 0

    for raw in diff.splitlines():
        if raw.startswith(("diff --git ", "index ", "new file mode",
                           "deleted file mode", "similarity index",
                           "rename from", "rename to", "old mode",
                           "new mode", "Binary files ")):
            hunk = None                              # git 頭行：檔案分隔，非內容
            continue
        if raw.startswith("# "):                    # ① 的註記行
            ana.skipped_notes.append(raw[2:].strip())
            hunk = None
            continue
        m = _FILE_RE.match(raw)
        if m:
            cur = FileChange(path=m.group("path"))
            ana.files.append(cur)
            hunk = None
            continue
        if _OLD_RE.match(raw) or raw.startswith("\\ No newline"):
            continue
        m = _HUNK_RE.match(raw)
        if m and cur is not None:
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            hunk = Hunk(old_start=old_no, old_count=int(m.group(2) or 1),
                        new_start=new_no, new_count=int(m.group(4) or 1))
            cur.hunks.append(hunk)
            continue
        if hunk is None or cur is None:
            continue
        if raw.startswith("+"):
            hunk.lines.append(("+", new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            hunk.lines.append(("-", None, raw[1:]))
            old_no += 1
        else:                                        # context（含空行）
            text = raw[1:] if raw.startswith(" ") else raw
            hunk.lines.append((" ", new_no, text))
            new_no += 1
            old_no += 1
    return ana


def sparse_new_file(fc: FileChange) -> str:
    """把「新檔的已知內容」放回正確行號、其餘補空行 —— 掃描器因此回報真行號。"""
    known: dict[int, str] = {n: t for h in fc.hunks
                             for tag, n, t in h.lines
                             if tag in ("+", " ") and n is not None}
    if not known:
        return ""
    top = max(known)
    return "\n".join(known.get(i, "") for i in range(1, top + 1)) + "\n"


def numbered_slice(fc: FileChange, max_chars: int = 6000) -> str:
    """行號標註的 diff 切片（餵 reviewer）：+/context 行前綴新檔行號，- 行不標。"""
    out = [f"### {fc.path}"]
    for h in fc.hunks:
        out.append(f"@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@")
        for tag, n, t in h.lines:
            prefix = f"{n:>5} {tag}" if n is not None else "      -"
            out.append(f"{prefix} {t}")
    text = "\n".join(out)
    return text[:max_chars] + ("\n…（切片截斷）" if len(text) > max_chars else "")


# ---------- diff 幾何 predicates（下游共用：pipeline / knowledge） ----------

def _norm(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


def match_path(a: str, b: str) -> bool:
    """路徑後綴相等，但只在路徑段邊界成立（Cache.java 不算 SessionCache.java）。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na.endswith("/" + nb) or nb.endswith("/" + na)


def line_in_view(ana: Anatomy, file: str, line, tol: int = 2) -> bool:
    """line 落在該檔『切片可見行』（added ∪ context）±tol 內。
    challenge #1（pre-existence）的證據本來就在 context 行，只認 added 會誤傷。"""
    try:
        line = int(line)
    except (TypeError, ValueError):
        return False
    for fc in ana.files:
        if match_path(fc.path, file):
            visible = {n for h in fc.hunks for tag, n, _ in h.lines
                       if n is not None}
            return bool(visible) and any(abs(line - n) <= tol for n in visible)
    return False


def line_near(ana: Anatomy, file: str, line, tol: int = 2) -> bool:
    """行號確定性驗證：line 必須落在該檔『新增行』集合的 ±tol 內。"""
    try:
        line = int(line)
    except (TypeError, ValueError):
        return False
    for fc in ana.files:
        if match_path(fc.path, file):
            added = fc.added_linenos
            return bool(added) and any(abs(line - n) <= tol for n in added)
    return False
