"""K 段收割器 —— 唯一 commit 者。timer 每 30 分跑一次：

  1) git add/commit 收攏新 _pending（distiller 只寫檔、不碰 git → 無併發衝突）
  2) 逐條 pending 問 PR 終態：
       active     → 陪等（無過期機制：PR 開多久我們陪多久）
       abandoned  → 丟稿（從未發生的決策不入帳本）
       completed  → 讀我們那條 thread 分類：
           作者回覆含 reject          → 丟
           作者有回覆（其他）          → explicit：回覆逐字入 Author note
           沉默 + code 未再動         → silent：原稿照單落地
           draft 之後又有新 push      → stale：請求 orchestrator 用最終 diff
                                       重蒸（請求戳 + attempts ≤ 2；關掉
                                       KNOWLEDGE_REDISTILL 就回到直接丟稿）
  3) watermark 掃描：目標分支上「沒經過 bot」就 completed 的 PR → 補一條
     History 帳本行（provenance=unreviewed），release 史零遺漏
  4) 週期收口（release → master 驗收 PR）：
       active     → 貼 digest（首次全列；之後每有新暫存稿補一則 delta——
                    release owner 沒看過的條目不會無聲落地）＋ 收 reject
       completed  → 收口前最後一次收 reject → 批次落地 → _cycles/ 收據
                    （從未出現在任何 digest 的條目 provenance 帶 +unlisted）
  5) 統一 git commit

reject 三種粒度（行首命令式，任何人可回）：
  reject <EntityName|路徑後綴>   整條（既有；撞名時回貼消歧義）
  reject PR12                    整條（不會撞名）
  reject PR12-I1 / PR12-D2       只丟那一行（ID 在蒸餾時定，digest 逐行列出）

digest 裡的「Needs a look」段由 LLM 產生（knowledge_digest.analyze），只是提案：
落地路徑不讀它，失敗就退回純清單。harvester 的**寫入路徑**維持零 LLM。

**重蒸的分工**：收割器只偵測「merge commit ≠ distilled_commit」並打一條
`POST {ORCH_URL}/knowledge/redistill`，蒸餾與寫稿整個留在 orchestrator——
`_land` 維持零 LLM，也不必把半個 orchestrator 搬進這支 oneshot。

用法：python -m orchestrator.knowledge_harvest
env：KNOWLEDGE_ROOT / KNOWLEDGE_BRANCHES（同 orchestrator）+ ADO_PAT（同 ③）
     + ORCH_URL / ORCH_API_KEY（重蒸請求；預設 http://127.0.0.1:8080）
     + KNOWLEDGE_REDISTILL=false 可一鍵回到「stale 直接丟稿」
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import hashlib
import subprocess
import time
from pathlib import Path

from shared.azdo import PrRef, client, get_json

from orchestrator import knowledge_digest as digest
from orchestrator.knowledge import (_branch_patterns, _draft_comment,
                                    _suffix_eq, assign_ids, decision_line,
                                    inv_id, inv_text, invariant_line,
                                    redistill_enabled, split_id,
                                    write_json_atomic)

KMARK = digest.KMARK


def _load_json(path: Path) -> dict | None:
    """讀不出來回 None（寫一半 / 壞檔），由呼叫端決定略過還是保留。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_staged(path: Path) -> dict | None:
    """讀暫存稿並保證 draft 有 ID。升級前就進池的舊稿在此補 ID 並寫回——
    digest 列出的 ID 與之後 reject / 落地用的必須是同一組。"""
    it = _load_json(path)
    if not it or not isinstance(it.get("draft"), dict):
        return None
    before = json.dumps(it["draft"], sort_keys=True, ensure_ascii=False)
    assign_ids(it["draft"], int(it.get("pr_id", 0)))
    if json.dumps(it["draft"], sort_keys=True, ensure_ascii=False) != before:
        write_json_atomic(path, it)
    return it


def _cycle_pat() -> tuple[str, str]:
    return (os.getenv("KNOWLEDGE_CYCLE_SOURCE", "release/*").strip(),
            os.getenv("KNOWLEDGE_CYCLE_TARGET", "master").strip())

_HISTORY_ROW = re.compile(r"^\| PR#(\d+) \|.*\|$", re.M)


# ---------- 可替換的 ADO 薄封裝（測試 stub 點） ----------

def _get_pr(c, ref: PrRef) -> dict:
    return get_json(c, ref.pr_api)


def _get_threads(c, ref: PrRef) -> list[dict]:
    return get_json(c, f"{ref.pr_api}/threads").get("value", [])


def _list_active(c, ref: PrRef) -> list[dict]:
    return get_json(c, f"{ref.repo_api}/pullrequests",
                    **{"searchCriteria.status": "active",
                       "$top": "50"}).get("value", [])


def _post_thread(c, ref: PrRef, content: str):
    r = c.post(f"{ref.pr_api}/threads", params={"api-version": "7.1"},
               json={"comments": [{"parentCommentId": 0, "content": content,
                                   "commentType": 1}],
                     "status": "active"})
    r.raise_for_status()
    try:
        return r.json().get("id")
    except Exception:
        return None


def _list_completed(c, ref: PrRef) -> list[dict]:
    return get_json(c, f"{ref.repo_api}/pullrequests",
                    **{"searchCriteria.status": "completed",
                       "$top": "100"}).get("value", [])


# ---------- 落地寫入 ----------

def _page_path(root: Path, repo: str, entity: str) -> Path:
    ent = Path((entity or "misc").lstrip("/"))
    parent = ent.parent.name or "misc"
    return root / repo / parent / f"{ent.stem or 'misc'}.md"


def _entity_stems(entity: str) -> list[str]:
    """entity 可能是路徑、檔名、類名或 FQN —— 回所有合理的檔名候選（依優先序）。

      src/main/java/.../LockService.java → LockService
      LockService                        → LockService
      com.acme.lock.LockService          → com.acme.lock（Path.stem 的產物，先試）
                                         → LockService（點號尾段，實際會中的）
      LockService.applyLock()            → LockService
    """
    e = (entity or "").strip().lstrip("/")
    if not e:
        return []
    tail = re.split(r"[(#]", e.rsplit("/", 1)[-1], 1)[0].strip()
    out = [Path(tail).stem]
    if "." in tail:
        last = tail.rsplit(".", 1)[-1].strip()
        # 大寫開頭 = 類名；小寫多半是副檔名（.java/.py），不當候選
        if last[:1].isupper():
            out.append(last)
    out.append(tail)
    seen: set[str] = set()
    return [s for s in out if s and not (s in seen or seen.add(s))]


def _resolve_page(root: Path, repo: str, entity: str) -> tuple[Path | None, str]:
    """找出 entity 對應的帳本頁。回 (頁面 或 None, 說明)。

    supersedes.entity 是模型自由填的字串，而建頁時用的是 primary_entity 的完整
    路徑——兩者形式不一致就算不出同一個位置。精確路徑不中時，退到「同檔名 stem」
    全域搜；撞名一律不蓋戳——註銷比新增風險高，蓋錯頁等於誤殺一條還有效的規則。
    """
    exact = _page_path(root, repo, entity)
    if exact.exists():
        return exact, ""
    base = root / repo
    if not base.is_dir():
        return None, f"repo 目錄不存在（{repo}）"
    stems = _entity_stems(entity)
    if not stems:
        return None, "entity 空白"
    pages = sorted(base.rglob("*.md"))
    for stem in stems:
        # 同 stem，或 _land 撞名分頁時加的 stem-<md5 前四碼>
        cands = [p for p in pages
                 if p.stem == stem or p.stem.startswith(stem + "-")]
        if len(cands) == 1:
            return cands[0], f"（精確路徑未中，以 {stem} 比對到 {cands[0].relative_to(root)}）"
        if len(cands) > 1:
            names = ", ".join(str(p.relative_to(root)) for p in cands[:4])
            return None, f"{stem} 撞名 {len(cands)} 頁（{names}）— 不蓋戳，避免蓋錯"
    return None, f"查無此頁（試過 {', '.join(stems)}）"


_SEC_RE = re.compile(r"^## (.+)$", re.M)


def _parse_page(text: str) -> tuple[list[str], dict[str, list[str]], str]:
    """(頂端廢除戳 banners, 段名→內容行, entity 行)。只解析我們自己寫的格式。"""
    banners = [ln for ln in text.splitlines()
               if ln.startswith("> ⚠ **Superseded")]
    entity = ""
    m = re.search(r"^> entity: (.+)$", text, re.M)
    if m:
        entity = m.group(1).strip()
    sections: dict[str, list[str]] = {}
    parts = _SEC_RE.split(text)
    for i in range(1, len(parts) - 1, 2):
        name = parts[i].strip()
        body = [ln for ln in parts[i + 1].strip().splitlines() if ln.strip()]
        sections[name] = body
    return banners, sections, entity


def _land(root: Path, repo: str, pr_id: int, data: dict,
          provenance: str, author_note: str,
          rejected_ids: list[str] | None = None) -> tuple[str, Path | None]:
    """累積合併落地（F1）：帳本是接續，不是快照。
    舊頁的 Decisions / Invariants / Supersedes / Author notes 前滾去重，
    Summary 取最新，廢除戳 banner 保留，History 疊行。
    rejected_ids：驗收窗被逐行 reject 掉的 ID，只留痕在 History（內容已不在 draft）。"""
    page = _page_path(root, repo, data.get("primary_entity") or "")
    banners: list[str] = []
    acc: dict[str, list[str]] = {}
    history: list[str] = []
    if page.exists():
        old = page.read_text(encoding="utf-8", errors="replace")
        if re.search(rf"\| PR#{pr_id} \|", old):
            return f"skip（PR#{pr_id} 已在 {page.name} 的 History）", page
        banners, acc, old_entity = _parse_page(old)
        new_entity = (data.get("primary_entity") or "").strip()
        if old_entity and new_entity and old_entity != new_entity:
            # 不同子樹同 parent+stem 的實體：分頁，別把兩個實體攪成一鍋
            tag = hashlib.md5(new_entity.encode()).hexdigest()[:4]
            page = page.with_name(f"{page.stem}-{tag}.md")
            banners, acc, history = [], {}, []
            if page.exists():
                old = page.read_text(encoding="utf-8", errors="replace")
                if re.search(rf"\| PR#{pr_id} \|", old):
                    return f"skip（PR#{pr_id} 已在 {page.name} 的 History）", page
                banners, acc, _ = _parse_page(old)
                history = [m.group(0) for m in _HISTORY_ROW.finditer(old)]
        else:
            history = [m.group(0) for m in _HISTORY_ROW.finditer(old)]

    day = time.strftime("%Y-%m-%d")
    ent = data.get("primary_entity") or "(entity unspecified)"
    assign_ids(data, pr_id)              # 舊稿（無 ID）落地時補齊，格式對齊

    def _body(ln: str) -> str:
        return ln[2:].strip() if ln.startswith("- ") else ln.strip()

    def merge(name: str, new_lines: list[str]) -> list[str]:
        seen = list(acc.get(name, []))
        # 舊格式遷移：單數 Author note 併入複數
        if name == "Author notes" and "Author note" in acc:
            seen = acc["Author note"] + seen
        if name == "Invariants":
            # 不變式以「文字」去重（忽略行尾 ID）：同一條規則被兩條 PR 各講一次
            # 只留先落地的那條；ID 不同不代表規則不同。
            seen_text = {split_id(_body(s))[0] for s in seen}
            fresh = [ln for ln in new_lines
                     if split_id(_body(ln))[0] not in seen_text]
        else:
            fresh = [ln for ln in new_lines if ln not in seen]
        if name in ("Decisions", "Supersedes", "Author notes"):
            return fresh + seen          # 時序段：新在前（注入截斷保住最新）
        return seen + fresh              # Invariants 等無時序段：append 即可

    decs = merge("Decisions", [decision_line(d, pr_id)
                               for d in (data.get("decisions") or [])
                               if isinstance(d, dict)])
    invs = merge("Invariants", [invariant_line(x)
                                for x in (data.get("invariants") or [])
                                if inv_text(x)])
    # 被本次 supersede 點名的不變式：就地標記，不刪除。
    # 主路徑是 ID 精確比對（retires_id）；逐字相等（retires_verbatim）只服務
    # 還沒有 ID 的舊行。對不上就什麼都不做——漏標的代價是兩條並存，
    # 誤標的代價是誤殺一條有效規則。
    retired_ids = {(x.get("retires_id") or "").strip().upper()
                   for x in (data.get("supersedes") or [])
                   if (x.get("retires_id") or "").strip()}
    retired_txt = {(x.get("retires_verbatim") or "").strip()
                   for x in (data.get("supersedes") or [])
                   if (x.get("retires_verbatim") or "").strip()}
    if retired_ids or retired_txt:
        marked: list[str] = []
        for ln in invs:
            body = _body(ln)
            text, iid = split_id(body)
            if text.startswith("~~"):
                marked.append(ln)              # 已標記過
            elif (iid and iid in retired_ids) or (not iid and text in retired_txt):
                marked.append(f"- ~~{text}~~ ⚠ retired by PR#{pr_id}"
                              + (f" `{iid}`" if iid else ""))
            else:
                marked.append(ln)
        invs = marked
    sups = merge("Supersedes", [
        f"- ⚠ `{x.get('entity', '?')}` — {x.get('what', '')} (PR#{pr_id})"
        for x in (data.get("supersedes") or [])])
    notes = merge("Author notes",
                  [f"- (PR#{pr_id}) {author_note.strip()}"] if author_note else [])
    aff = merge("Affected entities",
                [", ".join(f"`{a}`" for a in (data.get("affected") or [])[:8])]
                if data.get("affected") else [])

    lines = [f"# {Path(ent).stem or ent}", ""]
    lines += banners + ([""] if banners else [])
    lines += [f"> entity: {ent}",
              f"> approval: {provenance} · last: PR#{pr_id} · {day}", "",
              "## Summary", (data.get("summary", "") or "(none)").strip(), ""]
    if not data.get("intent_stated", True):
        lines += ["> intent: not stated in the PR — mechanics only.", ""]
    for name, body in (("Decisions", decs), ("Invariants", invs),
                       ("Supersedes", sups), ("Author notes", notes),
                       ("Affected entities", aff)):
        if body:
            lines += [f"## {name}", *body, ""]
    _known = {"Summary", "Decisions", "Invariants", "Supersedes",
              "Author notes", "Author note", "Affected entities", "History"}
    for name, body_lines in acc.items():
        if name not in _known and body_lines:      # 人工補寫的段落：前滾保留
            lines += [f"## {name}", *body_lines, ""]
    row = (f"| PR#{pr_id} | {day} | {data.get('ledger_line', '').strip() or '—'}"
           f" ({data.get('type', 'other')}) · {provenance}"
           + (" · rejected: " + ", ".join(f"`{r}`" for r in rejected_ids)
              if rejected_ids else "") + " |")
    lines += ["## History", "| PR | Date | Entry |", "|---|---|---|", row]
    lines += history + [""]
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("\n".join(lines), encoding="utf-8")
    return f"landed → {page.relative_to(root)}（{provenance}）", page


def _mark_superseded(root: Path, repo: str, sup: dict, pr_id: int,
                     landed: Path | None = None) -> str:
    """在被廢除的頁面頂端蓋戳 + History 留行。內容保留 —— 靜默蒸發是大忌。

    landed 給定時代表「這條 supersede 指向本次剛寫的同一頁」——那種情況整頁蓋戳
    會誤導讀者以為全頁作廢，交給 _land 的逐條刪除線處理即可，這裡不重複標。
    """
    ent = sup.get("entity") or ""
    page, why = None, ""
    rid = (sup.get("retires_id") or "").strip().upper()
    if rid:
        # ID 是精確鍵：哪一頁含 `PR12-I1` 就是哪一頁，不用猜 stem
        hits = [p for p in sorted((root / repo).rglob("*.md"))
                if f"`{rid}`" in p.read_text(encoding="utf-8", errors="replace")] \
            if (root / repo).is_dir() else []
        if len(hits) == 1:
            page, why = hits[0], f"（以 {rid} 定位）"
    if page is None:
        page, why = _resolve_page(root, repo, ent)
    if page is None:
        # 新頁已寫下 Supersedes 宣告、舊頁卻沒蓋到 —— 帳本進入不一致狀態，
        # 這不是無害的略過，措辭要讓人看得出要處理。
        return f"⚠ supersede 未蓋戳（{ent}）：{why}"
    if landed is not None and page.resolve() == landed.resolve():
        return f"supersede 同頁（{page.name}）— 已於條目層級標記，不蓋頁面戳"
    txt = page.read_text(encoding="utf-8", errors="replace")
    if f"Superseded (PR#{pr_id}" in txt:
        return "supersede 已蓋戳（冪等略過）"
    day = time.strftime("%Y-%m-%d")
    banner = (f"> ⚠ **Superseded (PR#{pr_id}, {day})** — "
              f"{sup.get('what', '')}\n")
    nl = txt.find("\n") + 1
    txt = txt[:nl] + "\n" + banner + txt[nl:]
    if "## History" in txt:
        txt = txt.rstrip() + (f"\n| PR#{pr_id} | {day} | ⚠ superseded: "
                              f"{sup.get('what', '')[:80]} |\n")
    page.write_text(txt, encoding="utf-8")
    return f"supersede 蓋戳 → {page.relative_to(root)}{why}"


def _ledger_only(root: Path, repo: str, pr: dict) -> str:
    """未經 bot 的 completed PR：只補一條帳本行（沒有 review 產物可蒸，誠實留痕）。"""
    pr_id = pr.get("pullRequestId")
    title = (pr.get("title") or "").strip()[:100]
    page = root / repo / "_module" / "unreviewed-ledger.md"
    day = time.strftime("%Y-%m-%d")
    row = f"| PR#{pr_id} | {day} | {title or '—'} · unreviewed |"
    if page.exists():
        old = page.read_text(encoding="utf-8", errors="replace")
        if f"PR#{pr_id} " in old:
            return "skip"
        page.write_text(old.rstrip() + "\n" + row + "\n", encoding="utf-8")
    else:
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# Unreviewed release ledger\n\n"
                        "PRs that reached a knowledge branch without passing "
                        "through the review bot.\n\n"
                        "| PR | Date | Entry |\n|---|---|---|\n"
                        + row + "\n", encoding="utf-8")
    return f"ledger 行 → PR#{pr_id}"


# ---------- 分類 ----------

def _author_reply(threads: list[dict], thread_id, author_id: str,
                  marker: str = "pr-review-knowledge-draft") -> tuple[str, str]:
    """回 (最後一則作者回覆, 'reject'|'reply'|'silent')。"""
    ours = None
    for t in threads:
        if thread_id and t.get("id") == thread_id:
            ours = t
            break
        if not thread_id and any(marker in (c.get("content") or "")
                                 for c in t.get("comments", [])):
            ours = t
            break
    if ours is None:
        return "", "silent"
    replies = [c["content"].strip() for c in ours.get("comments", [])[1:]
               if (c.get("author") or {}).get("id") == author_id
               and (c.get("content") or "").strip()]
    if not replies:
        return "", "silent"
    if re.search(r"(?m)^\s*reject\b", replies[-1], re.I):
        return replies[-1], "reject"
    return "\n\n".join(replies), "reply"      # 全部回覆逐字入帳，不只最後一則


# ---------- stale 重蒸的請求端（偵測在這裡，蒸餾整個在 orchestrator） ----------

def _orch_url() -> str:
    return os.getenv("ORCH_URL", "http://127.0.0.1:8080").strip().rstrip("/")


def _request_redistill(pr_url: str) -> tuple[bool, str]:
    """POST {ORCH}/knowledge/redistill。回 (成功?, 說明)。

    trust_env=False：這是同機 loopback 呼叫，公司的 HTTPS_PROXY 不該接管它
    （ADO 那條才需要 proxy）。端點收到即回 202，所以 timeout 開小即可。
    """
    import httpx
    key = os.getenv("ORCH_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = httpx.post(f"{_orch_url()}/knowledge/redistill",
                       json={"pr_url": pr_url}, headers=headers,
                       timeout=15.0, trust_env=False)
        r.raise_for_status()
        return True, "已請求重蒸"
    except Exception as e:  # noqa: BLE001
        return False, f"請求失敗（{type(e).__name__}: {e}）"


def _handle_stale(pend: Path, info: dict, merged: str,
                  note: str, kind: str) -> str:
    """draft 之後又有 push：請求重蒸，並用「請求戳 + attempts ≤ 2」封死重試迴圈。

        無戳                → 蓋戳(attempts=1) → POST（連線失敗也算一次）
        有戳、commit 相同    → attempts 1 → 2 再試一次；≥2 → 丟稿
        有戳、commit 又變了  → 重蒸期間又 push（世代變更）→ 丟稿

    30 分鐘一輪的 timer 在任何失敗模式下都不可能變成無界的 LLM 重試。
    """
    tail = "（作者回覆一併捨棄）" if kind == "reply" else ""
    if not redistill_enabled():
        pend.unlink()
        return f"completed + draft 後又有 push → stale 丟稿{tail}" \
               "（KNOWLEDGE_REDISTILL=false）"
    stamp = info.get("redistill_requested") or {}
    attempts = int(stamp.get("attempts") or 0)
    if stamp and stamp.get("commit") != merged:
        pend.unlink()
        return ("completed + 重蒸期間又有新 push（世代變更）→ stale 丟稿"
                f"{tail}（戳 {str(stamp.get('commit'))[:8]} → "
                f"現在 {str(merged)[:8]}）")
    if attempts >= 2:
        pend.unlink()
        return f"completed + 重蒸兩次未成 → stale 丟稿{tail}"
    info["redistill_requested"] = {"commit": merged, "attempts": attempts + 1}
    if kind == "reply" and note:
        info["author_note"] = note      # 逐字留著：重蒸的材料要帶作者說過的話
    write_json_atomic(pend, info)       # 先蓋戳再 POST：失敗也算一次 attempt
    ok, why = _request_redistill(info.get("pr_url") or "")
    return (f"completed + draft 後又有 push → 請求重蒸"
            f"（第 {attempts + 1}/2 次，commit {str(merged)[:8]}）：{why}")


def _process_pending(c, root: Path, pend: Path) -> str:
    info = json.loads(pend.read_text(encoding="utf-8"))
    ref = PrRef(base_url=info["base_url"], repo=info["repo"],
                pr_id=int(info["pr_id"]))
    pr = _get_pr(c, ref)
    status = (pr.get("status") or "").lower()
    if status == "active":
        if not info.get("posted") and info.get("post_failed") \
                and not info.get("post_retried"):
            # 只有「真嘗試過且失敗」才補貼。dry_run 稿與無標記的存量舊稿
            # 一律沉默——影子模式的靜默承諾由這行守住（雙開關縱深不繞道）。
            info["post_retried"] = True          # 貼稿失敗補救：只重試一次
            try:
                tid = _post_thread(
                    c, ref, "<!-- pr-review-knowledge-draft -->\n"
                    + _draft_comment(info["draft"], info["pr_id"]))
                info["posted"] = True
                info["thread_id"] = tid
                write_json_atomic(pend, info)
                return "active（貼稿補救成功——harvester 代貼）"
            except Exception as e:
                write_json_atomic(pend, info)
                return f"active（貼稿補救失敗：{e}；不再重試）"
        return "active（陪等）"
    if status == "abandoned":
        pend.unlink()
        return "abandoned → 丟稿"
    if status != "completed":
        return f"未知狀態 {status}（保留）"

    merged = (pr.get("lastMergeSourceCommit") or {}).get("commitId")
    author_id = (pr.get("createdBy") or {}).get("id", "")
    note, kind = _author_reply(_get_threads(c, ref), info.get("thread_id"),
                               author_id)
    if kind == "reject":
        pend.unlink()
        return "completed + 作者 reject → 丟稿"
    if info.get("distilled_commit") and merged \
            and info["distilled_commit"] != merged:
        return _handle_stale(pend, info, merged, note, kind)
    # 進 release ≠ 進帳本：暫存到 _cycle/，等 release→master 驗收 PR 收口
    assign_ids(info["draft"], int(info["pr_id"]))   # 升級前的舊稿在此補 ID
    # 重蒸過的稿裡留著作者對「更早那版草稿」的回覆——thread 讀不到時（例如
    # thread 被刪）仍以它為準，provenance 跟著說實話：作者確實說過話。
    author_note = note if kind == "reply" else info.get("author_note", "")
    prov = "explicit" if author_note else "silent"
    if info.get("redistilled"):
        prov += "·redistilled"          # 這條的內容是用最終 diff 重蒸來的
    write_json_atomic(root / "_cycle" / info["repo"] / pend.name, {
        **{k: info[k] for k in ("pr_url", "pr_id", "repo", "base_url", "draft")},
        "target_branch": info.get("target_branch", ""),
        "author_note": author_note,
        "provenance": prov,
        # 帶著矛盾完成 = 實作推翻了既有規則，留給驗收窗裁決（見 _apply_contradictions）
        "contradicted": list(info.get("contradicted") or []),
    })
    pend.unlink()
    return f"completed → staged（{prov}，等週期驗收）"


# ---------- watermark 掃描 ----------

def _watermark_scan(c, root: Path) -> list[str]:
    state = root / "_state"
    repos_f = state / "repos.json"
    if not repos_f.exists():
        return []
    wm_f = state / "watermark.json"
    wm = json.loads(wm_f.read_text()) if wm_f.exists() else {}
    pats = _branch_patterns()
    seen_pending: set[str] = set()
    if (root / "_pending").is_dir():
        for p in (root / "_pending").rglob("pr-*.json"):
            info = _load_json(p)           # 寫一半的稿：這輪略過，不拖垮整個掃描
            if info and info.get("pr_id") is not None:
                seen_pending.add(f"{p.parent.name}#{info['pr_id']}")
    lines: list[str] = []
    for repo, base in json.loads(repos_f.read_text()).items():
        key = f"{base}|{repo}"
        ref = PrRef(base_url=base, repo=repo, pr_id=0)
        if key not in wm:                    # F6：首見即定水位，不灌歷史
            wm[key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            lines.append(f"{repo}: 首見，watermark 定於現在（歷史 PR 不回填）")
            continue
        newest = wm.get(key, "")
        for pr in _list_completed(c, ref):
            closed = pr.get("closedDate") or ""
            target = (pr.get("targetRefName") or "").replace("refs/heads/", "")
            if closed <= wm.get(key, "") or not any(
                    fnmatch.fnmatch(target, p) for p in pats):
                continue
            src = (pr.get("sourceRefName") or "").replace("refs/heads/", "")
            if fnmatch.fnmatch(src, _cycle_pat()[0]):
                newest = max(newest, closed)
                continue          # 驗收儀式本身不是「漏網 PR」
            if f"{repo}#{pr.get('pullRequestId')}" in seen_pending:
                newest = max(newest, closed)
                continue
            lines.append(f"{repo}: {_ledger_only(root, repo, pr)}")
            newest = max(newest, closed)
        if newest:
            wm[key] = newest
    write_json_atomic(wm_f, wm)
    return lines


# ---------- 週期收口（release → master 驗收 PR） ----------

def _collect_rejects(threads: list[dict]) -> set[str]:
    """行首命令式：`reject <Entity或路徑後綴>`；『don't reject X』不算。"""
    tokens: set[str] = set()
    for t in threads:
        for cm in t.get("comments", []):
            for m in re.finditer(r"(?m)^\s*reject\s+([\w./$-]+)",
                                 cm.get("content") or "", re.I):
                tokens.add(m.group(1).lower())
    return tokens


# ---------- 矛盾裁決（實作推翻規則 → 驗收窗決定要不要拔掉那條規則） ----------
#
# 為什麼不是「PR 完成就自動拔」：merge 過一個警告，跟「作者根本沒看那條留言
# 就按 Complete」在資料上長得一樣。自動拔的話，每一次真的 regression 都會順手
# 刪掉那條本來會抓到它的規則——錯誤自我抹除，而且無聲。
#
# 為什麼也不是「不能拔」：帳本只進不出就會爛。加一條規則只要沉默 merge，拔一條
# 卻要作者用剛好的句式明示——這個不對稱是反的，錯誤的規則會永久製造誤報。
#
# 所以：偵測是確定性的（finding 的 knowledge_ref 指名了是哪一條），提案進 digest
# 的紅區讓人看得到，**沉默即接受**（跟「新條目沉默即落地」同一套核准語意），
# 任何人回 `keep PR12-I1` 就否決這次拔除。與 reject 同一個模式：人裁、code 執行。

def _retire_on_contradiction() -> bool:
    return os.getenv("KNOWLEDGE_RETIRE_ON_CONTRADICTION", "true").strip().lower() \
        not in {"false", "0", "no"}


def _collect_keeps(threads: list[dict]) -> set[str]:
    """行首命令式 `keep PR12-I1`：否決本週期對該條規則的拔除。"""
    out: set[str] = set()
    for t in threads:
        for cm in t.get("comments", []):
            for m in re.finditer(r"(?m)^\s*keep\s+(PR\d+-[DI]\d+)",
                                 cm.get("content") or "", re.I):
                out.add(m.group(1).upper())
    return out


def _retire_by_id(root: Path, repo: str, iid: str, pr_id: int) -> str:
    """在已落地的頁上把某條不變式畫掉（不刪除），並在 History 留行。

    定位方式與 _mark_superseded 相同：哪一頁含 `PR12-I1` 就是哪一頁——ID 是
    精確鍵，不用猜 stem。找不到 / 撞多頁一律不動，只留 journal。
    """
    base = root / repo
    if not base.is_dir():
        return f"⚠ 拔除 {iid} 失敗：repo 目錄不存在"
    hits = [p for p in sorted(base.rglob("*.md"))
            if f"`{iid}`" in p.read_text(encoding="utf-8", errors="replace")]
    if len(hits) != 1:
        return (f"⚠ 拔除 {iid} 未執行：{'查無此 ID' if not hits else f'撞到 {len(hits)} 頁'}")
    page = hits[0]
    txt = page.read_text(encoding="utf-8", errors="replace")
    out, done = [], False
    for ln in txt.splitlines():
        body = ln[2:].strip() if ln.startswith("- ") else ""
        text, line_id = split_id(body)
        if not done and line_id.upper() == iid and not text.startswith("~~"):
            out.append(f"- ~~{text}~~ ⚠ retired by PR#{pr_id} "
                       f"(contradicted by merged code) `{line_id}`")
            done = True
        else:
            out.append(ln)
    if not done:
        return f"⚠ 拔除 {iid} 未執行：該行已畫掉或格式不符"
    day = time.strftime("%Y-%m-%d")
    txt = "\n".join(out).rstrip() + (
        f"\n| PR#{pr_id} | {day} | ⚠ retired `{iid}`: contradicted by merged "
        f"code, accepted at cycle close |\n")
    page.write_text(txt, encoding="utf-8")
    return f"拔除 {iid} → {page.relative_to(root).as_posix()}（實作推翻，驗收窗未否決）"


def _apply_contradictions(root: Path, repo: str, items: list[tuple],
                          keeps: set[str]) -> list[str]:
    """收口時執行：每條暫存稿帶來的矛盾 id，沒被 keep 否決的就畫掉。"""
    if not _retire_on_contradiction():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for _x, it in items:
        for iid in (it.get("contradicted") or []):
            iid = str(iid).upper()
            if iid in seen:
                continue
            seen.add(iid)
            if iid in keeps:
                out.append(f"矛盾 {iid} 保留（有人回 keep，本週期不拔）")
                continue
            out.append(_retire_by_id(root, repo, iid, int(it.get("pr_id", 0))))
    return out


def _reject_line(x: Path, iid: str) -> bool:
    """從暫存稿拿掉一行（decision 或 invariant），留痕在 rejected_ids。
    命中回 True。條目本身保留——摘要與 History 行仍會落地。"""
    info = _load_json(x)
    if not info:
        return False
    d = info.get("draft") or {}
    assign_ids(d, int(info.get("pr_id", 0)))
    before = (len(d.get("decisions") or []), len(d.get("invariants") or []))
    d["decisions"] = [q for q in (d.get("decisions") or [])
                      if not (isinstance(q, dict)
                              and (q.get("id") or "").upper() == iid)]
    d["invariants"] = [q for q in (d.get("invariants") or [])
                       if inv_id(q).upper() != iid]
    if before == (len(d["decisions"]), len(d["invariants"])):
        return False
    info["rejected_ids"] = sorted(set(info.get("rejected_ids") or []) | {iid})
    write_json_atomic(x, info)
    return True


def _apply_rejects(tokens: set[str], staged: list[Path],
                   where: str) -> tuple[list[str], dict[str, list[str]]]:
    """回 (journal 行, 歧義 token → 候選路徑清單)。歧義的一律保留不執行。

    三種 token：實體名 / 路徑後綴（撞名→歧義）、PR12（整條，精確）、
    PR12-I1（單行，精確）。ID 類永遠不會歧義。"""
    out: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for tok in sorted(tokens):
        kind, key = digest.parse_reject_token(tok)
        if kind == "line":
            pr_key = key.split("-", 1)[0]
            hit = False
            for x in staged:
                info = _load_json(x) if x.exists() else None
                if info and f"PR{info.get('pr_id')}" == pr_key:
                    hit = _reject_line(x, key) or hit
            out.append(f"reject {key} {where} → "
                       + ("該行已移除（條目保留）" if hit else "查無此 ID（略過）"))
            continue
        if kind == "entry":
            hit = False
            for x in list(staged):
                info = _load_json(x)
                if info and f"PR{info.get('pr_id')}" == key and x.exists():
                    x.unlink()
                    hit = True
            out.append(f"reject {key} {where} → " + ("整條丟" if hit else "查無此 PR（略過）"))
            continue
        hits, ents = [], []
        for x in list(staged):
            info = _load_json(x)
            if not info:
                continue
            ent = (info.get("draft") or {}).get("primary_entity", "")
            stem = Path(ent).stem.lower()
            if ("/" in tok and _suffix_eq(ent.lower(), tok)) \
                    or ("/" not in tok and stem == tok):
                hits.append(x)
                ents.append(ent)
        if len(hits) > 1:
            ambiguous[tok] = ents
            out.append(f"reject '{tok}' 對到 {len(hits)} 條（同名 stem）——"
                       "已回貼請求消歧義，本輪全數保留")
            continue
        for x in hits:
            if x.exists():
                x.unlink()
                out.append(f"staged {tok} {where} reject → 丟")
    return out, ambiguous


def _post_ambiguity(c, ref: PrRef, state: dict, key: str,
                    ambiguous: dict[str, list[str]]) -> list[str]:
    """對 release owner 說人話：哪個 token 撞名、候選有哪些、該怎麼打。
    state 記帳（每 token 只貼一次），30 分鐘一輪的 timer 不會變成騷擾。"""
    done = set(state.get(key, {}).get("ambig", []))
    fresh = {t: e for t, e in ambiguous.items() if t not in done}
    out: list[str] = []
    if not fresh:
        return out
    lines = ["<!-- pr-review-knowledge-digest -->",
             "**⚠ Reject ambiguity — no entry was dropped**", ""]
    for tok, ents in fresh.items():
        lines.append(f"- `reject {tok}` matches {len(ents)} entries: "
                     + ", ".join(f"`{e}`" for e in ents[:4])
                     + " — reply with the PR id shown in the digest "
                     + "(e.g. `reject PR12`), or a path suffix, e.g. "
                     + f"`reject {ents[0].split('/', 1)[-1] if '/' in ents[0] else ents[0]}`")
    try:
        _post_thread(c, ref, "\n".join(lines))
        st = state.setdefault(key, {})
        st["ambig"] = sorted(done | set(fresh))
        out.append(f"歧義回貼 → PR#{ref.pr_id}（{len(fresh)} 個 token）")
    except Exception as e:
        out.append(f"歧義回貼失敗（{e}）——僅 journal 留痕")
    return out


def _cycle_first_sight(repo: str, state: dict) -> bool:
    """這個 repo 的週期 state 是不是全新的（= 收割器第一次看到它）。

    「哪些驗收 PR 已經結算過」只記在 `_state/cycle.json`，而那個檔住在
    KNOWLEDGE_ROOT 底下；帳本被重新 clone、_state 掉了、或測試時清空 KB，
    記憶就沒了——但 ADO 那邊的歷史一條都沒少。少了這個判斷，收割器會把
    「上個月早就結算完的驗收 PR」當成「剛完成、等結算」，把當下暫存的稿
    立刻落地，而那批稿沒有任何人在任何 digest 上看過。

    對策與 _watermark_scan 同一套：首見即定水位，歷史不回填。
    """
    return not any(k.startswith(f"{repo}#") for k in state)


def _pending_for(root: Path, repo: str, branch: str) -> int:
    """還在 _pending（小 PR 未 completed）且目標分支就是本週期 src 的稿數。
    驗收 PR 完成時若池子是空的但這裡 > 0，不能標 closed——否則那幾條稿
    completed 後進池就永遠沒有驗收 PR 來收（原「孤兒」問題）。"""
    d = root / "_pending" / repo
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("pr-*.json"):
        info = _load_json(f)
        if info and (info.get("target_branch") or "") == branch:
            n += 1
    return n


def _staged(root: Path, repo: str, branch: str | None = None) -> list[Path]:
    """branch 給定時，只回「目標分支 == branch」的暫存稿（無標記視為萬用）。
    多條 release 分支並行時，各週期只收自己那批——不跨週期污染。"""
    d = root / "_cycle" / repo
    files = sorted(d.glob("pr-*.json")) if d.is_dir() else []
    if branch is None:
        return files
    out = []
    for f in files:
        try:
            tb = json.loads(f.read_text()).get("target_branch", "")
        except Exception:
            tb = ""
        if not tb or tb == branch:
            out.append(f)
    return out


def _listed(st: dict, staged_prs: list[str]) -> set[str]:
    """已出現在某則 digest 裡的 PR 集合。

    升級相容：舊 state 只有 digest:true 沒有 listed —— 那則舊 digest 列的就是
    當時所有暫存稿，這裡把現在池裡的視為已列（寧可少貼一則 delta，也不把
    整份 digest 重貼一次）。"""
    if "listed" in st:
        return set(st["listed"])
    return set(staged_prs) if st.get("digest") else set()


def _post_digest(c, ref: PrRef, repo: str, items: list[dict],
                 listed: set[str], log: list[str]) -> set[str]:
    """首次全列、之後只補新進的（delta）。回更新後的 listed；貼失敗回原集合
    （下一輪再試——條目沒被人看過之前不算「已列」）。"""
    all_prs = {f"PR{it.get('pr_id')}" for it in items}
    new_prs = all_prs - listed
    if not new_prs:
        return listed
    analysis = digest.analyze(repo, items, log=lambda m: log.append(f"{repo}: {m}"))
    first = not listed
    md = digest.render(items, None if first else new_prs, analysis,
                       total=len(items))
    try:
        _post_thread(c, ref, md)
    except Exception as e:
        log.append(f"{repo}: digest 貼失敗（{e}）—— 下一輪重試")
        return listed
    n_flags = len(analysis["flags"]) if analysis else 0
    log.append(f"{repo}: digest{'' if first else '（delta）'} → 驗收 PR#{ref.pr_id}"
               f"（{len(new_prs)} 條{'開窗' if first else '新進'}"
               + (f"，{n_flags} 個顧問旗標" if analysis else "，無顧問分析") + "）")
    return listed | new_prs


def _cycle_close(c, root: Path) -> list[str]:
    """對每個已知 repo：active 驗收 PR → 貼 digest（首次 / delta）+ 收 reject；
    completed 驗收 PR → 收口前最後一次收 reject → 批次落地 + 週期收據。"""
    state_f = root / "_state" / "cycle.json"
    state = (_load_json(state_f) or {}) if state_f.exists() else {}
    repos_f = root / "_state" / "repos.json"
    if not repos_f.exists():
        return []
    src_pat, tgt_pat = _cycle_pat()
    digest_on = os.getenv("KNOWLEDGE_DIGEST", "true").strip().lower() \
        not in {"false", "0", "no"}
    out: list[str] = []

    def _is_cycle_pr(pr: dict) -> tuple[bool, str]:
        src = (pr.get("sourceRefName") or "").replace("refs/heads/", "")
        tgt = (pr.get("targetRefName") or "").replace("refs/heads/", "")
        return (fnmatch.fnmatch(src, src_pat)
                and fnmatch.fnmatch(tgt, tgt_pat)), src

    for repo, base in (_load_json(repos_f) or {}).items():
        ref0 = PrRef(base_url=base, repo=repo, pr_id=0)
        # 在動任何 state 之前先問：這個 repo 是不是首見？（下面的 active 迴圈
        # 會 setdefault 出 key，問晚了答案就被自己汙染了）
        first_sight = _cycle_first_sight(repo, state)
        for pr in _list_active(c, ref0):
            ok, src = _is_cycle_pr(pr)
            if not ok:
                continue
            pid = pr.get("pullRequestId")
            ref = PrRef(base_url=base, repo=repo, pr_id=pid)
            key = f"{repo}#{pid}"
            st = state.setdefault(key, {})
            staged = _staged(root, repo, src)      # 只認本週期（src 分支）那批
            items = [it for it in (_load_staged(x) for x in staged) if it]
            listed = _listed(st, [f"PR{it.get('pr_id')}" for it in items])
            if digest_on and items:
                listed = _post_digest(c, ref, repo, items, listed, out)
            if listed or st.get("digest"):
                st["listed"] = sorted(listed)
                st["digest"] = True
            # 驗收窗內收 reject（行首命令式；實體名 / PR12 / PR12-I1）
            msgs, ambiguous = _apply_rejects(
                _collect_rejects(_get_threads(c, ref)), staged, "驗收窗")
            for line in msgs:
                out.append(f"{repo}: {line}")
            for line in _post_ambiguity(c, ref, state, key, ambiguous):
                out.append(f"{repo}: {line}")
        # completed 驗收 PR → 收口
        swallowed = 0
        for pr in _list_completed(c, ref0):
            ok, src = _is_cycle_pr(pr)
            pid = pr.get("pullRequestId")
            key = f"{repo}#{pid}"
            if not ok or state.get(key, {}).get("closed"):
                continue
            st = state.setdefault(key, {})
            if first_sight:
                # 首見水位：ADO 回的是「最近 100 條 completed」，沒有時間條件，
                # 所以這些多半是我們開始看之前就結算完的。標 closed、不落地。
                st["closed"] = True
                st["note"] = "首見水位：本輪之前就已完成，不回填收口"
                swallowed += 1
                continue
            staged = _staged(root, repo, src)      # 本週期那批
            if not staged:
                n_wait = _pending_for(root, repo, src)
                if n_wait:
                    out.append(f"{repo}: 驗收 PR#{pid} 已完成但仍有 {n_wait} 條 "
                               f"_pending 指向 {src} —— 暫不收口，等它們裁決")
                    continue
                st["closed"] = True
                continue
            # 收口前最後一次收 reject / keep（防「回完馬上按 Complete」的時間差漏看）
            ref = PrRef(base_url=base, repo=repo, pr_id=pid)
            keeps: set[str] = set()
            try:
                threads = _get_threads(c, ref)
                tokens = _collect_rejects(threads)
                keeps = _collect_keeps(threads)
            except Exception:
                tokens = set()
            msgs, ambiguous = _apply_rejects(tokens, staged, "收口前")
            for line in msgs:
                out.append(f"{repo}: {line}")
            for line in _post_ambiguity(c, ref, state, key, ambiguous):
                out.append(f"{repo}: {line}")
            staged = _staged(root, repo, src)
            if not staged:
                st["closed"] = True
                continue
            items = [(x, it) for x in staged for it in [_load_staged(x)] if it]
            listed = _listed(st, [f"PR{it.get('pr_id')}" for _, it in items])
            day = time.strftime("%Y-%m-%d")
            rows = []
            unlisted = 0
            for x, it in items:
                prov = f"cycle/{it.get('provenance', 'silent')}"
                if f"PR{it.get('pr_id')}" not in listed:
                    prov += "+unlisted"       # 沒人看過就落地：攤在 provenance 上
                    unlisted += 1
                res, landed = _land(root, repo, it["pr_id"], it["draft"], prov,
                                    author_note=it.get("author_note", ""),
                                    rejected_ids=it.get("rejected_ids") or [])
                for sup in it["draft"].get("supersedes") or []:
                    out.append(f"{repo}: "
                               f"{_mark_superseded(root, repo, sup, it['pr_id'], landed)}")
                rows.append(f"| PR#{it['pr_id']} | "
                            f"{Path(it['draft'].get('primary_entity', '')).stem}"
                            f" | {prov} |")
                x.unlink()
            # 落地之後才處理矛盾：新條目已經寫進頁面，這時再把被推翻的舊行畫掉，
            # 順序與人的直覺一致（先記下這次決定了什麼，再劃掉它取代的東西）。
            retired_lines = _apply_contradictions(root, repo, items, keeps)
            for line in retired_lines:
                out.append(f"{repo}: {line}")
            cyc_page = root / repo / "_cycles" / f"{day}-PR{pid}.md"
            cyc_page.parent.mkdir(parents=True, exist_ok=True)
            cyc_page.write_text(
                f"# Cycle acceptance — PR#{pid} ({day})\n\n"
                "| Source PR | Entity | Provenance |\n|---|---|---|\n"
                + "\n".join(rows) + "\n"
                + ("\n## Invariants retired this cycle\n"
                   "（被合併的實作推翻，驗收窗未否決）\n\n"
                   + "\n".join(f"- {ln}" for ln in retired_lines) + "\n"
                   if retired_lines else ""), encoding="utf-8")
            st["closed"] = True
            out.append(f"{repo}: 驗收 PR#{pid} 收口 → 落地 {len(rows)} 條 + 週期頁"
                       + (f"（{unlisted} 條從未出現在 digest，provenance 已標 +unlisted）"
                          if unlisted else ""))
        if swallowed:
            out.append(f"{repo}: 首見，{swallowed} 條歷史驗收 PR 標為已收口"
                       "（不回填落地；暫存稿留著等下一條真正完成的驗收 PR）")
    write_json_atomic(state_f, state)
    return out


# ---------- git（唯一寫手） ----------

def _git(root: Path, *args) -> None:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if r.returncode != 0 and "commit" in args:
        msg = (r.stderr or r.stdout or "").strip()
        if "nothing to commit" not in msg and "nothing added" not in msg:
            print(f"[harvest] git commit 失敗（{msg.splitlines()[-1] if msg else '?'}）"
                  "—— 檔案已寫入但無版本紀錄；多半是 user.name/email 未設")


def main() -> None:
    raw = os.getenv("KNOWLEDGE_ROOT", "").strip()
    if not raw:
        print("KNOWLEDGE_ROOT 未設定，收割器不跑")
        return
    root = Path(raw)
    if not root.is_dir():
        print(f"KNOWLEDGE_ROOT 不存在：{root}")
        return
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "knowledge: stage pendings")

    pendings = sorted((root / "_pending").rglob("pr-*.json")) \
        if (root / "_pending").is_dir() else []
    print(f"[harvest] pending {len(pendings)} 條")
    with client() as c:
        for pend in pendings:
            try:
                print(f"  PR {pend.stem[3:]}（{pend.parent.name}）："
                      f"{_process_pending(c, root, pend)}")
            except Exception as e:
                print(f"  {pend.name} 失敗：{e}（保留待下輪）")
        for ln in _watermark_scan(c, root):
            print(f"  {ln}")
        for ln in _cycle_close(c, root):
            print(f"  {ln}")

    _git(root, "add", "-A")
    _git(root, "commit", "-m",
         f"knowledge: harvest {time.strftime('%F %T')}")
    print("[harvest] 完成")


if __name__ == "__main__":
    main()
