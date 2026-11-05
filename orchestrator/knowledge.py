"""知識迴圈（K 段）—— rules.py 的孿生 + distiller 前段。

定位：決策帳本，不是文件網站。三鐵律：
  檢索優先 —— 第一讀者是我們自己的 reviewer（Stage 04 targeted 注入）
  證據優先 —— 條目每句話掛 PR / file:line，無連結的句子不得出現
  錨在實體 —— 頁面錨 code 實體不錨 PR；同實體新決策接續寫進同一頁

本模組管四件事：
  load_for()            讀取路：變更檔 → 相關條目 → 注入區塊（英文、分級降載）
  gate()                K1 閘門：目標分支 ∈ KNOWLEDGE_BRANCHES、排除 bump/merge-sync
  distill_once()        蒸餾一次：同一份 diff 切片 → LLM → 證據驗證 → 補 ID
  distill_and_stage()   K2+K3 前段：distill_once → 草稿留言貼上 PR → 寫 _pending/
  redistill()           stale 補救：用最終 diff 重蒸一次，覆寫 _pending/
                        （終態裁決與落地在 knowledge_harvest.py，timer 是唯一 commit 者）

證據地板（Phase 1）：distiller 吃的是 reviewer 看過的同一份 numbered slices，
產出的每一條 decision 都要能引到「excerpt 裡看得到的行 / 這次 review 的 finding
編號 / 有內容的 PR description」——驗不過的 evidence 剝掉，剝光的 decision 整條
不入稿，數字進 stats、痕跡進草稿留言 footer。語意判斷在模型、驗證在 code。

設定：settings().knowledge（KNOWLEDGE_ROOT / KNOWLEDGE_BRANCHES / KNOWLEDGE_MAX_CHARS /
DISTILL_DIFF_MAX_CHARS / KNOWLEDGE_REDISTILL …）；KNOWLEDGE_ROOT 未設 = 整個 K 段靜默停用。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from pathlib import Path

from langfuse import observe

from orchestrator import clients
from orchestrator.anatomy import (Anatomy, line_in_view, match_path,
                                  numbered_slice, parse_diff)
from orchestrator.llm import complete, extract_json, prompt_or
from orchestrator.static_pass import DECL_RE, TEST_PATH_RE
from shared.settings import settings

DISTILLER_PROMPT_NAME = "pr-review-distiller"

DEFAULT_DISTILLER_PROMPT = """You are the knowledge distiller of an automated code-review system. A pull request has just been reviewed; your job is to distill it into one decision-ledger entry so future reviewers inherit what was decided here.

Hard rules:
- Every statement must be supported by the material provided (PR description, diff summary, review report). Never invent intent: if the PR text does not state why the change was made, set "intent_stated": false and describe only the mechanics.
- Write for a reviewer who will read this months from now while reviewing a change to the same code: decisions and invariants they must not break, not a narrative of the diff.
- Prefer few, load-bearing statements over completeness. A ledger entry with two real decisions beats ten paraphrased diff lines.
- "evidence" items are short locators and are verified mechanically — an item that cannot be resolved is stripped, and a decision left with no evidence is discarded before anyone reads it. Exactly three forms are accepted:
  - "<file>:<line>" — the line must be one you can actually see in the annotated diff excerpt below (the number in the left-hand column of a "+" or context line). Never cite a line that is not in the excerpt, and never guess a number.
  - "F<n>" — a finding id from the review report below, exactly as numbered there.
  - "PR" — only when the statement rests on the PR description; do not use it when the description is empty.
- If the PR text or review discussion explicitly reverses or retires a previously established rule, record it in "supersedes" (entity + what is being retired). Only when explicitly stated — never infer retirement.
- Lines under "Existing ledger invariants" below start with a bracketed id such as [PR12-I1]. When the retired rule is one of those lines, put that id (without brackets) in "retires_id" — the id is how the system strikes the old entry. If the line is marked [no-id], copy its text into "retires_verbatim" character for character instead. Never invent an id; omit both fields if unsure.
- "Existing ledger invariants" is reference material for supersede detection only. Never restate those lines as this PR's own decisions or invariants.
- If the material mentions alternatives that were considered and rejected, capture that as a decision ("chose X over Y because Z") — rejected paths are knowledge a diff cannot show. Such a decision is normally carried by "PR" evidence; that is what the "PR" form is for.
- "primary_entity" must be copied verbatim from the changed-files list — it is the page this entry is filed under. Pick the file that carries the change's meaning, not a test or a build file. "affected" may name neighbours that this diff does not touch.
- Write all output in clear, professional English.

Output a single JSON object only — the first character must be "{" and the last "}":
{"summary": "One or two sentences: what this change establishes.",
 "type": "bugfix|feature|refactor|revert|other",
 "intent_stated": true,
 "primary_entity": "src/main/java/.../SessionCache.java",
 "decisions": [{"text": "The decision and its rationale as stated", "evidence": ["PR", "CacheConfig.java:24"]}],
 "invariants": ["A constraint future changes must preserve"],
 "supersedes": [{"entity": "path/or/ClassName", "what": "the rule being retired", "retires_id": "PR12-I1 (the id of the existing invariant this replaces; omit if none)", "retires_verbatim": "only for [no-id] lines: the exact text, copied character for character (omit otherwise)"}],
 "affected": ["other/entities/touched"],
 "ledger_line": "One line for the history table, e.g. 'TTL unit changed to milliseconds; margin fixed at 5000ms'"}"""

# ---------- 條目 ID（蒸餾時即定，暫存→落地一路不變） ----------
#
# 形式：PR<pr_id>-D<n>（decision）/ PR<pr_id>-I<n>（invariant）。PR id + 序號
# 天然全域唯一，不需要中央計數器；digest 的 LLM 分析、`reject <ID>`、supersede
# 的 retires_id 全部認這個字串，落地端只做精確比對——不再靠 stem 猜、不再靠
# 逐字複製。頁面裡以行尾 `PR12-I1`（反引號）標記，wiki 可讀、regex 可解。

ID_RE = re.compile(r"^PR(\d+)-([DI])(\d+)$", re.I)
_ID_TAIL = re.compile(r"\s*`(PR\d+-[DI]\d+)`\s*$", re.I)


def norm_id(tok: str) -> str:
    """'pr12-i1' → 'PR12-I1'；不是 ID 形式回空字串。"""
    m = ID_RE.match((tok or "").strip())
    return f"PR{m.group(1)}-{m.group(2).upper()}{m.group(3)}" if m else ""


def split_id(body: str) -> tuple[str, str]:
    """'text `PR12-I1`' → ('text', 'PR12-I1')；無標記 → (text, '')。"""
    m = _ID_TAIL.search(body or "")
    if not m:
        return (body or "").strip(), ""
    return body[:m.start()].strip(), norm_id(m.group(1))


def inv_text(x) -> str:
    """invariant 可能是舊稿的純字串或新稿的 {id, text}。"""
    return (x.get("text", "") if isinstance(x, dict) else str(x or "")).strip()


def inv_id(x) -> str:
    return (x.get("id", "") if isinstance(x, dict) else "").strip()


def assign_ids(data: dict, pr_id: int) -> dict:
    """就地補 ID（冪等）：已有 id 的不動；純字串 invariant 升格成 {id, text}。
    舊稿（無 id）在 staging 或落地時經過這裡一次即可對齊新格式。"""
    decs = data.get("decisions") or []
    for i, d in enumerate(decs, 1):
        if isinstance(d, dict) and not (d.get("id") or "").strip():
            d["id"] = f"PR{pr_id}-D{i}"
    invs = []
    for i, x in enumerate(data.get("invariants") or [], 1):
        text = inv_text(x)
        if not text:
            continue
        iid = inv_id(x) or f"PR{pr_id}-I{i}"
        invs.append({"id": iid, "text": text})
    data["invariants"] = invs
    return data


def entry_ids(data: dict, pr_id: int) -> set[str]:
    """一條條目可被 reject / 引用的全部 ID：PR<id> 本身 + 各行。"""
    out = {f"PR{pr_id}"}
    out.update(d.get("id", "") for d in (data.get("decisions") or [])
               if isinstance(d, dict) and d.get("id"))
    out.update(inv_id(x) for x in (data.get("invariants") or []) if inv_id(x))
    return out


# ---------- 證據地板（Phase 1.2）：LLM 說的每一句都要指得回材料 ----------
#
# 型別合約：evidence 是「引用系統給的座標」，不是自由字串。三種形式各有一個
# 確定性檢查，驗不過就剝掉；一條 decision 的 evidence 被剝光 = 它沒有任何可
# 回溯的依據 → 整條不入稿（跟「引不出行號的 reject 不配 ×0.35」同一個嚴格度）。
#
# invariants 沒有 evidence 欄位，這裡不驗——它們是規則不是主張，驗證交給
# reviewer 端的引用率（Phase 2）與蒸餾考卷的幻覺不變式陷阱題（Phase 4）。
# 這是明文接受的殘餘風險，不是漏掉。

EV_PR_MIN_CHARS = 40           # description 至少要有這麼多非空白字元才撐得起 "PR"
_EV_FINDING_RE = re.compile(r"^F\d+$", re.I)


def _evidence_ok(tok: str, ana: Anatomy, ids: set[str], desc_chars: int) -> bool:
    t = (tok or "").strip().strip("[]`")
    if not t:
        return False
    if t.upper() == "PR":
        return desc_chars >= EV_PR_MIN_CHARS
    if _EV_FINDING_RE.match(t):
        return t.upper() in ids
    path, sep, line = t.rpartition(":")
    if sep and path.strip() and line.strip().isdigit():
        # line_in_view 內含 match_path：檔案要在這份 diff 裡，行要在切片看得到的
        # 範圍（added ∪ context）±2 —— decision 常常指著被改動那一段的上下文行。
        return line_in_view(ana, path.strip(), int(line))
    return False


def verify_evidence(data: dict, ana: Anatomy, finding_ids: set[str] | None,
                    description: str) -> tuple[dict, list[str]]:
    """就地驗證 decisions 的 evidence。回 (data, 被整條丟掉的 decision 文字)。

    ana 為空（沒有 diff 可比對）時 file:line 一律驗不過——寧可少收，不可假證據。
    """
    desc_chars = len("".join((description or "").split()))
    ids = {str(x).strip().upper() for x in (finding_ids or set())}
    kept: list = []
    dropped: list[str] = []
    for d in data.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        good = [str(e).strip() for e in (d.get("evidence") or [])
                if _evidence_ok(str(e), ana, ids, desc_chars)]
        if not good:
            dropped.append(" ".join((d.get("text") or "(no text)").split())[:200])
            continue
        d["evidence"] = good
        kept.append(d)
    data["decisions"] = kept
    return data, dropped


# ---------- 蒸餾切片（Phase 1.1）：distiller 吃 reviewer 看過的同一份座標系 ----------

def _distill_order(ana: Anatomy, finding_files: list[str] | None) -> list:
    """優先序：① 新增行最多的那個檔 ② 被 review findings 指到的檔
    ③ 含新宣告（DECL_RE）的檔 ④ 其餘。同級以 (新增行數 desc, 路徑) 定序，
    同一份 diff 永遠切出同一份切片。"""
    if not ana.files:
        return []
    refs = [f for f in (finding_files or []) if f]
    top = max(ana.files, key=lambda fc: (len(fc.added), fc.path))

    def rank(fc) -> int:
        if fc.path == top.path:
            return 0
        if any(match_path(fc.path, r) for r in refs):
            return 1
        if any(DECL_RE.match(t) for _, t in fc.added):
            return 2
        return 3

    return sorted(ana.files, key=lambda fc: (rank(fc), -len(fc.added), fc.path))


def distill_slices(ana: Anatomy,
                   finding_files: list[str] | None = None) -> tuple[str, list[str]]:
    """回 (切片文, 超預算被省略的檔案)。省略對模型有註記、對我們進 stats——
    降級可見，不安靜蒸發。"""
    budget = settings().knowledge.distill_diff_max_chars
    parts: list[str] = []
    skipped: list[str] = []
    used = 0
    for fc in _distill_order(ana, finding_files):
        chunk = numbered_slice(fc)
        if parts and used + len(chunk) > budget:
            skipped.append(fc.path)
            continue
        parts.append(chunk)
        used += len(chunk)
    if skipped:
        shown = ", ".join(skipped[:6]) + (", …" if len(skipped) > 6 else "")
        parts.append(f"({len(skipped)} file(s) omitted from the diff excerpt: "
                     f"{shown}) — do not cite line numbers from these files")
    return "\n\n".join(parts), skipped


def pick_primary_entity(ana: Anatomy, paths: list[str]) -> str:
    """頁面錨點的 fallback：非 lockfile、非測試檔中「新增行數最多」的檔。
    （舊版取『路徑最長』——頁面錨點卻用最弱的啟發式。）"""
    def usable(p: str) -> bool:
        name = Path(p).name.lower()
        return name not in _EXCLUDE_BASENAMES \
            and not TEST_PATH_RE.search(p.lstrip("/"))

    cands = [fc for fc in ana.files if usable(fc.path)]
    if cands:
        return max(cands, key=lambda fc: (len(fc.added), fc.path)).path
    if ana.files:                       # 全是測試/lockfile：照樣要有錨
        return max(ana.files, key=lambda fc: (len(fc.added), fc.path)).path
    usable_paths = [p for p in paths if usable(p)]
    return max(usable_paths or paths, key=len) if (usable_paths or paths) else ""


def fix_primary_entity(data: dict, ana: Anatomy, paths: list[str]) -> bool:
    """distiller 自選的 primary_entity 必須是這次真的改到的檔（suffix 語意）；
    驗不過才 fallback。回 True = 被修正過。"""
    ent = (data.get("primary_entity") or "").strip()
    if ent and ana.files and any(match_path(fc.path, ent) for fc in ana.files):
        return False
    if ent and not ana.files and ent in paths:
        return False                     # 沒有 diff 可比對時退回舊語意
    data["primary_entity"] = pick_primary_entity(ana, paths)
    return True


def write_json_atomic(path: Path, obj) -> None:
    """tmp + os.replace：harvester 的 git add / rglob 撞到寫一半的檔也只會看到
    完整舊版或完整新版，不會讀到半截 JSON。（.tmp 不符 pr-*.json 的 glob。）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


_EXCLUDE_BASENAMES = {"pom.xml", "build.gradle", "build.gradle.kts", "package.json",
                      "package-lock.json", "yarn.lock", "changelog.md", "version.txt",
                      "gradle.properties"}
_SKIP_DIRS = {"_pending", "_state", "_overview"}
_AFFECTED_RE = re.compile(r"## Affected entities\n(.*?)(\n#|\Z)", re.S)


def _suffix_eq(a: str, b: str) -> bool:
    """路徑後綴相等，但只在路徑段邊界成立。"""
    return bool(a) and bool(b) and (
        a == b or a.endswith("/" + b) or b.endswith("/" + a))


def _affected_block(body: str) -> str:
    m = _AFFECTED_RE.search(body)
    return m.group(1) if m else ""


def _root() -> Path | None:
    return settings().knowledge.root


def _norm(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


# ---------- 頁面比對（檢索路與 supersede 目錄共用，避免兩份邏輯漂移） ----------
#
# 命中強度是 tier 不是布林（Phase 3）：注入預算有限時，「這一頁就是講你正在改
# 的那個檔」必須排在「你改的檔的鄰居提過一句」前面。全部是 ordinal——沒有分數
# 常數、不需要校準，同一份 diff 對同一個帳本 SHA 永遠注入同一組。
#
#   4  primary 精確路徑（頁面的 `> entity:` 就是這次變更的檔）
#   3  (parent, stem) 檔名命中
#   2  Affected entities 鄰接（graph 給的真鄰居）
#   1  _module 人類手寫頁的路徑段命中
#   0  未命中

TIER_PRIMARY, TIER_FILENAME, TIER_AFFECTED, TIER_MODULE = 4, 3, 2, 1

_ENTITY_LINE = re.compile(r"^> entity: (.+)$", re.M)


class _MatchCtx:
    """把「這次變更的檔案」預處理成比對用的鍵。"""

    def __init__(self, changed_paths: list[str]):
        self.segs: set[str] = set()
        self.fname_keys: set[tuple[str, str]] = set()   # (parent_dir, stem)
        for p in changed_paths:
            parts = _norm(p).split("/")
            self.segs.update(parts)
            if parts:
                stem = parts[-1].rsplit(".", 1)[0]
                parent = parts[-2] if len(parts) >= 2 else ""
                self.fname_keys.add((parent, stem))
        self.changed_norm = [_norm(p) for p in changed_paths]

    def entry_tier(self, primary: str, affected: list[str]) -> int:
        """暫存稿（_cycle/）的命中強度 —— 與正式頁同一把尺，一起排序。"""
        pn = _norm(primary)
        if pn and any(_suffix_eq(c, pn) for c in self.changed_norm):
            return TIER_PRIMARY
        stem = pn.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem and any(s2 == stem for _, s2 in self.fname_keys):
            return TIER_FILENAME
        if any(self._aff_hit(aff) for aff in affected):
            return TIER_AFFECTED
        return 0

    def _aff_hit(self, aff: str) -> bool:
        a = _norm(aff)
        if "/" in a:
            return any(_suffix_eq(c, a) for c in self.changed_norm)
        token = re.split(r"[.(]", a, 1)[0].strip()
        return bool(token) and any(s2 == token for _, s2 in self.fname_keys)

    def page_hit(self, page: Path, rel: Path, body: str) -> int:
        """回命中強度 tier（0 = 沒中）。"""
        stem = page.stem.lower()
        if rel.parts and rel.parts[0] == "_module":
            # 人類手寫的模組頁：路徑段命中，強度最低（整個模組級的泛談）
            return TIER_MODULE if stem in self.segs else 0
        m = _ENTITY_LINE.search(body)
        ent = _norm(m.group(1)) if m else ""
        if ent and any(_suffix_eq(c, ent) for c in self.changed_norm):
            return TIER_PRIMARY            # 這一頁講的就是你正在改的那個檔
        parent = page.parent.name.lower()
        if any(stem == s and (not parent or parent == pa or parent in self.segs)
               for pa, s in self.fname_keys) or (parent, stem) in self.fname_keys:
            return TIER_FILENAME
        # 副鍵：條目的 Affected entities（graph 給的真鄰接）——
        # 不變式住在 A、開發者改的是鄰居 B，照樣要被提醒。
        if any(self._aff_hit(a)
               for a in re.findall(r"`([^`]+)`", _affected_block(body))):
            return TIER_AFFECTED
        return 0


def _matching_pages(base: Path, ctx: _MatchCtx):
    """走訪 repo 底下的帳本頁，yield 命中的 (page, body, tier)。"""
    if not base.is_dir():
        return
    for page in sorted(base.rglob("*.md")):
        rel = page.relative_to(base)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        body = page.read_text(encoding="utf-8", errors="replace")
        tier = ctx.page_hit(page, rel, body)
        if tier:
            yield page, body, tier


_INVARIANT_BLOCK = re.compile(r"^## Invariants\n(.*?)(?=\n## |\Z)", re.M | re.S)


def existing_invariants(repo: str | None, changed_paths: list[str]
                        ) -> list[tuple[str, list[tuple[str, str]]]]:
    """回 [(頁面相對路徑, [(ID, 不變式原文)])]，給 distiller 與 digest 分析用。

    只回「還有效」的條目——已經被畫掉的（`~~…~~`）不再列入，否則模型會被
    引導去重複註銷同一條。ID 來自行尾 `PR12-I1` 標記；舊格式的行 ID 為空，
    仍以逐字原文方式（retires_verbatim）可被註銷。
    """
    root = _root()
    if root is None or not repo or not changed_paths:
        return []
    base = root / repo.strip().lower()
    ctx = _MatchCtx(changed_paths)
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for page, body, _tier in _matching_pages(base, ctx):
        m = _INVARIANT_BLOCK.search(body)
        if not m:
            continue
        pairs: list[tuple[str, str]] = []
        for ln in m.group(1).splitlines():
            ln = ln.strip()
            if not ln.startswith("- "):
                continue
            text, iid = split_id(ln[2:].strip())
            if text and not text.startswith("~~"):
                pairs.append((iid, text))
        if pairs:
            out.append((page.relative_to(root).as_posix(), pairs))
    return out


def catalogue_text(cat: list[tuple[str, list[tuple[str, str]]]], header: str) -> str:
    """把 existing_invariants 的結果排成給模型看的清單：`- [PR12-I1] text`。"""
    if not cat:
        return ""
    blocks = []
    for rel, pairs in cat:
        blocks.append("## " + rel + "\n" + "\n".join(
            f"- [{iid or 'no-id'}] {text}" for iid, text in pairs))
    return header + "\n\n".join(blocks)


# ---------- 讀取路：Stage 04 注入 ----------

_ID_ANY = re.compile(r"\bPR\d+-[DI]\d+\b", re.I)
_RETIRED_LINE = re.compile(r"^- ~~", re.M)


def strip_retired(body: str) -> tuple[str, int]:
    """把已註銷的行（`- ~~text~~ ⚠ retired by PR#nn`）從**注入用**的文字裡拿掉。

    頁面上保留（註銷是標記不是蒸發，人要看得到血緣）；但餵給 reviewer 的區塊
    不該帶著它——模型會照樣拿它報 violation，而誤報比漏報更快把信任燒光。
    連帶效果：它的 id 也就不在 knowledge_ids 裡，reviewer 不能拿一條死掉的
    規則當 grounding。
    """
    keep, n = [], 0
    for ln in body.splitlines():
        if _RETIRED_LINE.match(ln):
            n += 1
            continue
        keep.append(ln)
    return ("\n".join(keep), n) if n else (body, 0)


def _short_form(body: str) -> str:
    """降載版本：只留 Invariants 段（沒有就退 Summary）——條目最像規則的那部分。"""
    m = _INVARIANT_BLOCK.search(body)
    if m and m.group(1).strip():
        return "## Invariants\n" + m.group(1).strip()
    m = re.search(r"^## Summary\n(.*?)(?=\n## |\Z)", body, re.M | re.S)
    return ("## Summary\n" + m.group(1).strip()[:1200]) if m else body.strip()[:1200]


def load_for(repo: str | None, changed_paths: list[str]) -> tuple[str, dict]:
    """回 (注入區塊, stats)。無 root / 無 repo / 無命中 → ("", {entries:0})。

    排序與降載（Phase 3）：
      · 依 (-tier, 路徑) 排 —— 精確命中的頁排在鄰接命中的前面，可重現。
      · tier 4（primary 精確路徑）保證入場：塞不下全文就注入 Invariants 段。
      · 其餘依序注入全文；第一次塞不下就切成「只注入 Invariants 段」續排；
        連短版都塞不下才停，尾端列出被省略的條目名（降級可見）。
    """
    stats = {"knowledge_entries": 0}
    root = _root()
    if root is None or not repo or not changed_paths:
        return "", stats
    base = root / repo.strip().lower()
    cyc_dir = root / "_cycle" / repo.strip().lower()
    if not base.is_dir() and not cyc_dir.is_dir():
        return "", stats

    ctx = _MatchCtx(changed_paths)
    retired = 0
    picked: list[tuple[int, str, str, str]] = []      # (tier, sort_key, title, body)
    for page, body, tier in _matching_pages(base, ctx):
        body, n = strip_retired(body)                 # 已註銷的行不進 reviewer
        retired += n
        picked.append((tier, page.relative_to(base).as_posix(), page.stem, body))

    if cyc_dir.is_dir():
        for f in sorted(cyc_dir.glob("pr-*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            draft = d.get("draft") or {}
            tier = ctx.entry_tier(draft.get("primary_entity", ""),
                                  list(draft.get("affected") or []))
            if tier:
                # 暫存稿與正式頁套同一把尺一起排：它可能正是最相關的那一條
                picked.append((
                    tier, f.name,
                    f"{Path(draft.get('primary_entity', 'entry')).stem} "
                    f"(staged, pending cycle acceptance)",
                    render_entry(draft, int(d.get("pr_id", 0)), "staged",
                                 author_note=d.get("author_note", ""))))

    if not picked:
        return "", stats
    picked.sort(key=lambda x: (-x[0], x[1]))
    cap_total = settings().knowledge.max_chars
    parts_out = [f"# Knowledge ({len(picked)} entr"
                 f"{'y' if len(picked) == 1 else 'ies'} relevant to this change)"]
    used = 0
    injected: list[str] = []
    full = short = 0
    omitted: list[str] = []
    degraded = False                     # 一旦降載就不再回頭（順序即優先序）
    ids: set[str] = set()
    for tier, _key, title, body in picked:
        if omitted:                      # 已經停了：其餘全部列進省略名單
            omitted.append(title)
            continue
        chunk = f"\n## {title}\n{body.strip()}"
        if degraded or used + len(chunk) > cap_total:
            degraded = True
            chunk = (f"\n## {title} (invariants only — context budget)\n"
                     f"{_short_form(body)}")
            if used + len(chunk) > cap_total and tier != TIER_PRIMARY:
                omitted.append(title)
                continue
            # tier 4 的保證入場優先於預算：頁面講的就是這次改的檔，
            # 少注入它等於這次 review 白帶了知識層。
            short += 1
        else:
            full += 1
        parts_out.append(chunk)
        used += len(chunk)
        injected.append(title)
        # 引用率的分母（Phase 2.1）：只認「真的被注入的那一段文字」裡的 id——
        # 尾註要舉例說明格式，例子不能因此變成合法引用。
        ids.update(m.upper() for m in _ID_ANY.findall(chunk))
    if omitted:
        shown = ", ".join(omitted[:8]) + (", …" if len(omitted) > 8 else "")
        parts_out.append(f"\n({len(omitted)} more matched entries omitted "
                         f"(context budget): {shown})")
    stats["knowledge_ids"] = sorted(ids)
    parts_out.append("\n(evidence-linked team knowledge; treat as leads, not "
                     "limits — verify against the diff. When a finding rests on "
                     "one of the invariants above, cite its id — the backticked "
                     "marker at the end of the line — in knowledge_ref.)")
    text = "\n".join(parts_out)
    stats["knowledge_entries"] = len(picked)
    stats["knowledge_injected"] = injected      # 月度 staleness 抽樣的分母
    stats["knowledge_full"] = full
    stats["knowledge_invariants_only"] = short
    stats["knowledge_omitted"] = len(omitted)
    if retired:
        stats["knowledge_retired_hidden"] = retired   # 降級可見：被擋掉的死規則
    return text, stats


# ---------- K1 閘門 ----------

def _branch_patterns() -> list[str]:
    return list(settings().knowledge.branches)


def gate(meta: dict, changed_paths: list[str]) -> tuple[bool, str]:
    """(要不要蒸餾, 原因)。原因進 journal，不進使用者面。"""
    if _root() is None:
        return False, "KNOWLEDGE_ROOT 未設定"
    target = (meta.get("target_branch") or "").replace("refs/heads/", "").strip()
    if not target or not any(fnmatch.fnmatch(target, pat)
                             for pat in _branch_patterns()):
        return False, f"目標分支 {target or '?'} 不在 KNOWLEDGE_BRANCHES"
    src = (meta.get("source_branch") or "").replace("refs/heads/", "").strip()
    cyc_src = settings().knowledge.cycle_source
    cyc_tgt = settings().knowledge.cycle_target
    if cyc_src and fnmatch.fnmatch(src, cyc_src) \
            and fnmatch.fnmatch(target, cyc_tgt):
        return False, "cycle checkpoint PR（驗收儀式，不蒸餾）"
    title = (meta.get("title") or "").strip()
    if re.match(r"^Merge (branch|remote-tracking)", title) \
            or re.match(r"^Merge .+ (into|to) .+", title, re.I):
        return False, "分支同步 merge PR"
    if title.lower().startswith("revert"):
        return True, "revert（必收）"
    paths = [_norm(p) for p in changed_paths]
    if paths and all(p.rsplit("/", 1)[-1] in _EXCLUDE_BASENAMES for p in paths):
        return False, "純 version-bump / lockfile"
    return True, "release-bound"


# ---------- 條目渲染（distiller 與 harvester 共用） ----------

def decision_line(d: dict, pr_id: int) -> str:
    """`- text [ev] \\`PR12-D1\\``；無 id 的舊稿退回 `(PR#12)` 血緣標記。"""
    ev = " ".join(f"[{e}]" for e in (d.get("evidence") or []))
    body = f"{(d.get('text') or '').strip()} {ev}".rstrip()
    did = (d.get("id") or "").strip()
    return f"- {body} `{did}`" if did else f"- {body} (PR#{pr_id})"


def invariant_line(x) -> str:
    text, iid = inv_text(x), inv_id(x)
    return f"- {text} `{iid}`" if iid else f"- {text}"


def render_entry(data: dict, pr_id: int, provenance: str,
                 author_note: str = "", history_rows: list[str] | None = None) -> str:
    day = time.strftime("%Y-%m-%d")
    ent = data.get("primary_entity") or "(entity unspecified)"
    lines = [f"# {Path(ent).stem or ent}", "",
             f"> entity: {ent}",
             f"> approval: {provenance} · last: PR#{pr_id} · {day}", "",
             "## Summary", data.get("summary", "").strip() or "(none)", ""]
    if not data.get("intent_stated", True):
        lines += ["> intent: not stated in the PR — mechanics only.", ""]
    decs = data.get("decisions") or []
    if decs:
        lines.append("## Decisions")
        lines += [decision_line(d, pr_id) for d in decs if isinstance(d, dict)]
        lines.append("")
    inv = data.get("invariants") or []
    if inv:
        lines.append("## Invariants")
        lines += [invariant_line(x) for x in inv if inv_text(x)]
        lines.append("")
    if author_note:
        lines += ["## Author note", f"> {author_note.strip()}", ""]
    sup = data.get("supersedes") or []
    if sup:
        lines.append("## Supersedes")
        lines += [f"- ⚠ `{x.get('entity', '?')}` — {x.get('what', '')}" for x in sup]
        lines.append("")
    aff = data.get("affected") or []
    if aff:
        lines += ["## Affected entities", ", ".join(f"`{a}`" for a in aff[:8]), ""]
    row = (f"| PR#{pr_id} | {day} | {data.get('ledger_line', '').strip() or '—'}"
           f" ({data.get('type', 'other')}) · {provenance} |")
    lines += ["## History", "| PR | Date | Entry |", "|---|---|---|", row]
    lines += (history_rows or [])
    lines.append("")
    return "\n".join(lines)


# ---------- K2 + 貼稿 + _pending ----------

def _draft_comment(data: dict, pr_id: int, dropped: int = 0) -> str:
    body = render_entry(data, pr_id, provenance="draft")
    ask_why = ("" if data.get("intent_stated", True) else
               "\n**The PR text does not state why this change was made** — "
               "reply with one line of intent — especially any alternative you "
               "considered and rejected — and it will be recorded verbatim.\n")
    # 被丟掉的 decision 也要看得見：作者知道少了什麼，才有機會用一行 note 補回來
    dropped_note = (f"\n_{dropped} decision(s) dropped: evidence could not be "
                    "verified against this diff._\n") if dropped else ""
    return ("**Knowledge draft — decision ledger entry**\n\n"
            "This will be committed to the team knowledge base when the PR "
            "completes.\n" + ask_why + "\n---\n" + body + "\n---\n"
            + dropped_note +
            "Reply to add an author note (recorded verbatim) · reply `reject` "
            "to discard · merging silently records it as-is.")


def _catalogue_for_paths(repo: str, paths: list[str]) -> str:
    return catalogue_text(
        existing_invariants(repo, paths),
        "# Existing ledger invariants for the entities this PR touches\n"
        "# (supersede detection only — do NOT restate these as your own "
        "decisions; put the bracketed id in retires_id if one is retired)\n")


@observe(name="knowledge-distill-llm")
async def distill_once(pr_id: int, repo: str, meta: dict, ana: Anatomy,
                       ana_files: list[str], review_markdown: str = "",
                       graph_impacted: list[str] | None = None,
                       finding_ids: set[str] | None = None,
                       finding_files: list[str] | None = None,
                       extra_blocks: list[str] | None = None,
                       catalogue: str | None = None
                       ) -> tuple[dict, dict]:
    """蒸餾一次：組材料 → LLM → 證據驗證 → primary 修正 → 補 ID。

    回 (data, info)。info 帶 slices_skipped / dropped / evidence_stripped /
    primary_fixed —— 全是「被降級或被修正掉的東西」，呼叫端負責寫進 _pending
    與 journal（蒸餾考卷也讀這幾個數）。失敗直接 raise，呼叫端決定降級。

    catalogue 給定時用它取代「從 KNOWLEDGE_ROOT 讀既有不變式」——只有離線考卷
    會用（考的是蒸餾，不是檢索；檢索有自己的量尺）。
    """
    slices_text, slices_skipped = distill_slices(ana, finding_files)
    user = "\n\n".join(filter(None, [
        f"# PR #{pr_id}: {meta.get('title', '')}",
        f"Target branch: {meta.get('target_branch', '')}",
        ("Description:\n" + (meta.get("description") or ""))
        if meta.get("description") else "Description: (empty)",
        "Changed files:\n" + "\n".join(f"- {p}" for p in ana_files),
        ("Graph impact outside this diff:\n"
         + "\n".join(f"- {g}" for g in (graph_impacted or [])))
        if graph_impacted else "",
        # reviewer 看過的同一份座標系：file:line evidence 必須指得到這裡的行
        ("# Diff excerpt (line numbers annotated — every file:line you cite "
         "must be visible here)\n" + slices_text) if slices_text else "",
        catalogue if catalogue is not None
        else _catalogue_for_paths(repo, ana_files),
        *(extra_blocks or []),
        # 報告完整餵入：蒸餾的原料就是「已經掛好證據」的 review 結果，
        # 從中間切斷會讓最後幾條 finding 整個消失、且無痕。
        ("# Review report (already evidence-linked)\n" + review_markdown)
        if review_markdown else "",
    ]))
    raw = await complete(prompt_or(DISTILLER_PROMPT_NAME,
                                   DEFAULT_DISTILLER_PROMPT), user)
    data = extract_json(raw)
    if not (isinstance(data, dict) and data.get("summary")):
        raise ValueError("distiller 輸出不是合約 JSON（缺 summary）")
    def _ev_count(d: dict) -> int:
        return sum(len(x.get("evidence") or []) for x in (d.get("decisions") or [])
                   if isinstance(x, dict))

    before = _ev_count(data)
    data, dropped = verify_evidence(data, ana, finding_ids,
                                    meta.get("description") or "")
    primary_fixed = fix_primary_entity(data, ana, ana_files)
    assign_ids(data, pr_id)          # 貼稿前就定 ID：作者看到的和落地的是同一組
    return data, {"slices_skipped": slices_skipped, "dropped": dropped,
                  "evidence_stripped": before - _ev_count(data),
                  "primary_fixed": primary_fixed}


@observe(name="knowledge-distill")
async def distill_and_stage(pr_url: str, meta: dict, ana_files: list[str],
                            review_markdown: str, graph_impacted: list[str],
                            diff: str = "",
                            finding_ids: set[str] | None = None,
                            finding_files: list[str] | None = None,
                            contradicted: set[str] | None = None) -> None:
    """review 完非同步跑；任何失敗只進 journal（print），絕不影響使用者。

    diff / finding_ids / finding_files 由 app 直接遞來（reviewer 手上那一份）——
    不另造 diff 表示、不重 parse markdown。省略時退化成無切片的舊行為。
    """
    ok, why = gate(meta, ana_files)
    root = _root()
    if not ok or root is None:
        print(f"[knowledge] 跳過蒸餾：{why}")
        return
    from shared.azdo import parse_pr_url
    ref = parse_pr_url(pr_url)

    ana = parse_diff(diff) if diff else Anatomy()
    try:
        data, info = await distill_once(
            ref.pr_id, ref.repo, meta, ana, ana_files,
            review_markdown=review_markdown, graph_impacted=graph_impacted,
            finding_ids=finding_ids, finding_files=finding_files)
    except Exception as e:
        print(f"[knowledge] 蒸餾失敗（{e}）—— 本條 PR 不留稿")
        return
    _log_distill(ref.pr_id, info)

    thread_id = None
    posted = False
    dry_run = False
    post_failed = False
    try:
        out = await clients.post_knowledge_draft(
            pr_url, _draft_comment(data, ref.pr_id, len(info["dropped"])))
        posted = bool(out and out.get("posted"))
        dry_run = bool(out and out.get("dry_run"))
        thread_id = (out or {}).get("thread_id")
        if dry_run:
            print(f"[knowledge] PR#{ref.pr_id} 影子模式（KNOWLEDGE_POST=false）"
                  "—— 稿入 _pending，不會被任何人補貼")
    except Exception as e:
        post_failed = True                    # 真嘗試、真失敗 → harvest 才補貼
        print(f"[knowledge] 貼稿失敗（{e}）—— 仍寫 _pending，harvest 會重試一次")

    pend_dir = root / "_pending" / ref.repo.lower()
    write_json_atomic(pend_dir / f"pr-{ref.pr_id}.json", {
        "pr_url": pr_url, "pr_id": ref.pr_id, "repo": ref.repo.lower(),
        "target_branch": (meta.get("target_branch") or "")
        .replace("refs/heads/", "").strip(),
        "base_url": ref.base_url, "distilled_commit": meta.get("source_commit"),
        "author": meta.get("author", ""), "thread_id": thread_id,
        "posted": posted, "dry_run": dry_run, "post_failed": post_failed,
        "draft": data, "created": time.strftime("%FT%T"),
        # 這次 review 裡「被實作踩到」的既有不變式（確定性，來自 knowledge_ref）
        "contradicted": sorted({str(x).upper() for x in (contradicted or set())}),
        **_provenance_fields(info),
    })
    repos_f = root / "_state" / "repos.json"
    repos = json.loads(repos_f.read_text()) if repos_f.exists() else {}
    repos[ref.repo.lower()] = ref.base_url
    write_json_atomic(repos_f, repos)
    print(f"[knowledge] PR#{ref.pr_id} 蒸餾稿已入 _pending（posted={posted}）")


def _provenance_fields(info: dict) -> dict:
    """稿裡自帶「這一稿被驗過、被丟過什麼」——影子期驗收看這幾個欄位。"""
    return {"evidence_verified": True,
            "distill_dropped": info.get("dropped") or [],
            "distill_slices_skipped": info.get("slices_skipped") or []}


def _log_distill(pr_id: int, info: dict) -> None:
    if info.get("dropped"):
        print(f"[knowledge] PR#{pr_id} 證據驗證：丟掉 {len(info['dropped'])} 條 "
              f"decision —— " + " | ".join(info["dropped"][:3]))
    if info.get("slices_skipped"):
        print(f"[knowledge] PR#{pr_id} 蒸餾切片超預算，{len(info['slices_skipped'])} "
              f"檔未進 excerpt：{', '.join(info['slices_skipped'][:5])}")
    if info.get("primary_fixed"):
        print(f"[knowledge] PR#{pr_id} primary_entity 不在變更檔內 —— 已改用"
              "新增行數最多的檔")


# ---------- Phase 5：stale 重蒸（harvester 只偵測，這裡做全部） ----------

def redistill_enabled() -> bool:
    return settings().knowledge.redistill


def _clear_cycle_traces(root: Path, repo: str, pr_id: int) -> list[str]:
    """重蒸會產生同 ID、不同內容的行——舊的「已列 / 已 reject」記錄一律作廢。

    · 任何週期 state 的 listed 移除 PR<n> → 下一則 delta 會重新列它
      （沒人看過的新內容不會頂著「已列」狀態無聲落地）
    · 池裡若已有同一條 PR 的暫存稿（手動補蒸的情境）連同它的 rejected_ids
      一起移除——reject 指的是舊行，套到新行是誤殺
    """
    out: list[str] = []
    staged = root / "_cycle" / repo / f"pr-{pr_id}.json"
    if staged.exists():
        staged.unlink()
        out.append(f"移除舊暫存稿 _cycle/{repo}/pr-{pr_id}.json（含 rejected_ids）")
    state_f = root / "_state" / "cycle.json"
    if state_f.exists():
        try:
            state = json.loads(state_f.read_text(encoding="utf-8"))
        except Exception:
            return out
        key = f"PR{pr_id}"
        touched = False
        for ck, st in state.items():
            if key in (st.get("listed") or []):
                st["listed"] = [x for x in st["listed"] if x != key]
                touched = True
                out.append(f"{ck} 的 listed 移除 {key}（下一則 digest 重新列）")
        if touched:
            write_json_atomic(state_f, state)
    return out


@observe(name="knowledge-redistill")
async def redistill(pr_url: str) -> None:
    """用最終 diff 重蒸一次、覆寫 _pending/。

    材料刻意比首蒸窄：最終 diff 切片 + title/branch/description + 不變式目錄
    + 既收的 author replies（逐字）。**不含舊 review 報告**、graph 給空、
    finding_ids 給空集合——那份報告是對舊 diff 寫的，F 編號已經 stale，
    容許它進來只會讓 evidence 指向不存在的東西。窄一點換確定性。
    """
    root = _root()
    if root is None:
        print("[knowledge] 重蒸跳過：KNOWLEDGE_ROOT 未設定")
        return
    if not redistill_enabled():
        print("[knowledge] 重蒸跳過：KNOWLEDGE_REDISTILL=false")
        return
    from shared.azdo import parse_pr_url
    ref = parse_pr_url(pr_url)
    pend = root / "_pending" / ref.repo.lower() / f"pr-{ref.pr_id}.json"
    try:
        info = json.loads(pend.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[knowledge] 重蒸跳過：讀不到 _pending 稿（{e}）")
        return
    if info.get("redistilled"):
        print(f"[knowledge] PR#{ref.pr_id} 已重蒸過一次，不再重蒸")
        return

    try:
        meta = await clients.fetch_pr_metadata(pr_url)
        diff = await clients.fetch_pr_diff(pr_url)
    except Exception as e:
        print(f"[knowledge] PR#{ref.pr_id} 重蒸取件失敗（{e}）—— 稿保留，"
              "harvester 下一輪決定要不要再試")
        return
    ana = parse_diff(diff)
    ana_files = [fc.path for fc in ana.files]
    replies = (info.get("author_note") or "").strip()
    extra = ([f"# Author's reply to an earlier draft of this entry (verbatim)"
              f"\n{replies}"] if replies else [])
    try:
        data, dinfo = await distill_once(
            ref.pr_id, ref.repo, meta, ana, ana_files,
            review_markdown="", graph_impacted=[],
            finding_ids=set(), finding_files=[], extra_blocks=extra)
    except Exception as e:
        print(f"[knowledge] PR#{ref.pr_id} 重蒸失敗（{e}）—— 稿保留（stale），"
              "harvester 下一輪按重試上限處理")
        return
    _log_distill(ref.pr_id, dinfo)
    merged = (info.get("redistill_requested") or {}).get("commit") \
        or meta.get("source_commit")
    posted, dry_run, post_failed = info.get("posted", False), False, False
    try:
        out = await clients.post_knowledge_draft(
            pr_url, _draft_comment(data, ref.pr_id, len(dinfo["dropped"])),
            thread_id=info.get("thread_id"))
        posted = bool(out and out.get("posted")) or posted
        dry_run = bool(out and out.get("dry_run"))
    except Exception as e:
        post_failed = True
        print(f"[knowledge] PR#{ref.pr_id} 重蒸稿回貼失敗（{e}）—— 稿照樣更新")

    if not pend.exists():
        # 我們蒸的期間 harvester 已經把這條判死（重試上限用盡 / 世代變更）——
        # 這裡再寫回去等於讓一份已經被裁決掉的稿復活。放棄，只留 journal。
        print(f"[knowledge] PR#{ref.pr_id} 重蒸完成但稿已被收割器丟棄 —— 不復活")
        return
    # 舊痕跡的作廢緊貼著寫入做：確定要覆寫了才動別人的狀態
    for line in _clear_cycle_traces(root, ref.repo.lower(), ref.pr_id):
        print(f"[knowledge] PR#{ref.pr_id} 重蒸：{line}")
    info.update({
        "draft": data, "distilled_commit": merged,
        # 重蒸沒有 review 報告可讀，驗不了「那些矛盾是不是還在」——驗不了就不提案。
        # 作者若是照 review 把 code 改回來才 push，矛盾本來就該消失，這是正確方向。
        "contradicted": [],
        "redistilled": True, "redistill_of": info.get("distilled_commit"),
        "posted": posted, "dry_run": dry_run, "post_failed": post_failed,
        "redistilled_at": time.strftime("%FT%T"),
        **_provenance_fields(dinfo),
    })
    info.pop("redistill_requested", None)     # 戳清掉 = 這一輪請求已經被消化
    write_json_atomic(pend, info)
    print(f"[knowledge] PR#{ref.pr_id} 重蒸完成 → _pending 覆寫"
          f"（commit {str(merged)[:8]}）")
