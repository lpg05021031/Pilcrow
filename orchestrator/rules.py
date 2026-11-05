"""團隊規則載入器 —— 把 rules repo 的本機 clone 組進 review context。

對目錄結構的假設只有兩個名字（都可用環境變數改，等於沒有硬依賴）：

  <RULES_ROOT>/
    SKILL.md            ← 本 loader 不碰（它是 system prompt 的事，見 seed_langfuse）
    rules/              ← RULES_SUBDIR，預設 "rules"
      common/           ← RULES_COMMON_DIR，預設 "common"；每次 review 必載
      <repo 名小寫>/     ← 與 PR 所屬 repo 同名的目錄，存在才載
      其他目錄/          ← 一律忽略（別的 repo 的規則不該混進來）

紀律：唯讀、不碰網路、失敗絕不炸 review —— RULES_ROOT 沒設或路徑不存在時
回空 bundle，行為與沒有規則功能的舊版完全相同。

設定：settings().rules（RULES_ROOT / RULES_SUBDIR / RULES_COMMON_DIR / RULES_MAX_CHARS）。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from shared.settings import settings


@dataclass(frozen=True)
class RulesBundle:
    """一次 review 實際載入的規則快照。"""

    text: str = ""            # 組好的規則全文（含路徑標頭）；空字串 = 什麼都沒載到
    sha: str = ""             # rules repo 的 git commit —— 可重現性的錨點
    files: list[str] = field(default_factory=list)  # 實際載入的檔案（相對 RULES_ROOT）
    truncated: bool = False   # 超過 RULES_MAX_CHARS 被截斷
    # 逐檔內容（rel path → body）—— planner 把規則指派到面向後，
    # reviewer 只收到自己該管的那幾份（targeted context）
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def loaded(self) -> bool:
        return bool(self.text)


def _root() -> Path | None:
    return settings().rules.root


def _git_sha(root: Path) -> str:
    """規則的版本錨：取「最後一次動到 RULES_ROOT 子樹」的 commit short SHA。

    RULES_ROOT 可以指向共用 repo 裡的子資料夾（git 會自己往上找 .git）；
    用子樹範圍的 log 而非整庫 HEAD，同 repo 其他目錄的 commit 不會
    汙染這個錨。不是 git repo 或 git 不在時回 'no-git'（不炸）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h", "--", "."],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "no-git"
    except Exception:
        return "no-git"


def _iter_md(dirs: list[Path]):
    for d in dirs:
        if not d.is_dir():
            continue
        yield from sorted(d.rglob("*.md"))


def load_rules(repo: str | None = None) -> RulesBundle:
    """common 必載；repo 有給且 rules/<repo小寫>/ 存在就疊上去。

    載入順序 = common 先、repo 專屬後（優先序聲明在 rules_context 的標頭裡）。
    """
    root = _root()
    if root is None or not root.is_dir():
        return RulesBundle()

    cfg = settings().rules
    subdir = root / cfg.subdir
    dirs = [subdir / cfg.common_dir]
    if repo:
        dirs.append(subdir / repo.strip().lower())

    max_chars = cfg.max_chars
    parts: list[str] = []
    files: list[str] = []
    sections: dict[str, str] = {}
    total = 0
    truncated = False
    for f in _iter_md(dirs):
        try:
            body = f.read_text(errors="replace").strip()
        except OSError:
            continue
        if not body:
            continue
        rel = str(f.relative_to(root))
        block = f"## {rel}\n{body}"
        if total + len(block) > max_chars:
            truncated = True
            break
        parts.append(block)
        files.append(rel)
        sections[rel] = body
        total += len(block)

    return RulesBundle(
        text="\n\n".join(parts),
        sha=_git_sha(root),
        files=files,
        truncated=truncated,
        sections=sections,
    )


def rules_context(bundle: RulesBundle) -> str:
    """把 bundle 包成可直接 append 在 system prompt 後面的區塊；沒載到就回空字串。

    rule_ref 的指示放在這裡而不是 Langfuse 上的 prompt —— 這樣不管線上用的是
    哪一版受版控 prompt，只要規則有載入，引用規範就一定跟著。
    """
    if not bundle.loaded:
        return ""
    head = (
        f"\n\n# Team Rules (review basis, version {bundle.sha})\n"
        "The team rule documents follow; each begins with `## <file path>`.\n"
        "Precedence: repo-specific rules override common rules on conflict.\n"
        "For any finding based on a rule, set the finding's \"rule_ref\" "
        "field to the rule file path (e.g. \"rules/common/naming.md\"); "
        "leave rule_ref empty for general findings.\n"
    )
    tail = ("\n\n(Note: rule documents exceeded the length limit; only the "
            "leading portion was loaded)" if bundle.truncated else "")
    return head + "\n" + bundle.text + tail
