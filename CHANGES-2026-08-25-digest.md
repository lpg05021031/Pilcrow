# 變更說明 — 2026-08-25（digest / 條目 ID）

本包在 `CHANGES-2026-08-25.md` 之上的改動。共 8 個檔案（1 新增）。
知識迴圈相關測試 18 條全數通過（原 7 條不動 + 新增 11 條），全離線零 LLM。

---

## 設計取向（為什麼是這樣改，而不是讓 LLM 改寫頁面）

落地層的 corner case 來源不是「deterministic 不好」，而是三件本質上是
**語意**的工作在用字串工具做：這是哪個實體？這條跟既有那條是不是同一條？
它廢掉的是哪一條？`_entity_stems` 四種形式、md5 分頁、`retires_verbatim`
逐字相等，都是為此長出來的補丁。

這批改動不讓 LLM 碰帳本，而是**改 LLM 輸出的型別**：從自由字串改成
**引用系統給的 ID**。語意判斷留在模型（distiller 判斷哪條被廢、digest 分析
判斷哪兩條矛盾），驗證與寫入留在 code，而且 code 只做 ID 精確比對。

三條線：

| 線 | 內容 | LLM 參與 |
|---|---|---|
| 條目 ID | 蒸餾時即定 `PR12-D1` / `PR12-I1`，暫存→digest→落地不變 | distiller 輸出 `retires_id` |
| digest 流程 | 首次全列 → delta → 收口時 `+unlisted`；reject 三粒度 | 無 |
| digest 顧問段 | 整週期按實體看，標矛盾 / 近重複 / 該註銷沒點名 | **只提案**，落地不讀 |

harvester 的**寫入路徑**（`_land` / `_mark_superseded`）維持零 LLM。程序層
多了一個 LLM 呼叫（顧問分析），失敗退回純清單。

---

## 一、條目 ID（`orchestrator/knowledge.py`）

- `assign_ids(data, pr_id)`：decision 補 `id`；invariant 由純字串升格為
  `{"id", "text"}`。冪等，已有 id 不動。在 `distill_and_stage` 抽出 JSON 後
  立即呼叫——**作者在稿裡看到的 ID 就是落地的 ID**。
- 舊稿（升級前的 `_pending` / `_cycle` / 頁面）在三個地方被動補齊：
  `_process_pending` 進池時、`_load_staged` 首次讀到時（寫回）、`_land`
  落地時。不需要手動遷移。
- 頁面格式：行尾反引號標記 `- rule text \`PR12-I1\``；decision 以 ID 取代
  原本的 `(PR#12)` 血緣標記（ID 含 PR 號，blame 不丟）。無 ID 的舊行維持原樣。
- `existing_invariants()` 回傳 `[(頁, [(ID, 原文)])]`；`catalogue_text()`
  排成 `- [PR12-I1] text` / `- [no-id] text` 給模型看。
- distiller 合約：`supersedes[].retires_id`（主路徑）；`retires_verbatim`
  只服務 `[no-id]` 舊行。**prompt 必須 re-seed**（見部署）。
- `write_json_atomic()`：tmp + `os.replace`。`_pending` / `_cycle` / `_state`
  全部改走這條——harvester 的 `git add -A` 與 `rglob` 不會再讀到半截 JSON。

## 二、落地（`orchestrator/knowledge_harvest.py`）

- **修 bug**：`_land` 撞名分頁的 skip 路徑回三個值，呼叫端 `res, landed =`
  unpack 直接炸；因為暫存稿逐條 unlink，炸在中途 = 半落地且 state 未 closed，
  下一輪重跑撞同一條再炸——那個週期會卡死到手動介入。已改回 2-tuple 並加測試。
- 註銷改 ID 精確比對；`retires_verbatim` 保留為舊行退路。畫掉的行保留 ID：
  `- ~~text~~ ⚠ retired by PR#13 \`PR12-I1\``。
- Invariants 以**文字**去重（忽略行尾 ID）：兩條 PR 各講一次同一條規則只留
  先落地的。Decisions 維持逐行（含 ID）不去重，時序段本來就是 blame。
- `_mark_superseded` 先用 `retires_id` 定位頁面（哪頁含 `` `PR12-I1` ``
  就是哪頁），`_resolve_page` 的 stem 猜法退為 fallback。entity 亂填也蓋對頁。
- `_watermark_scan` 逐檔 try：一條壞稿不再拖垮整輪掃描。
- History 行帶 `· rejected: \`PR12-I1\``——驗收窗被逐行 reject 的痕跡留在帳本。

## 三、digest 流程（`_cycle_close`）

原本 digest 只貼一次（`state.digest = true`）。驗收 PR 開了之後才 completed
的小 PR 進池，不會出現在任何 digest，收口時卻照樣落地——release owner
從沒看過它。

- state 改記 `listed: [PR70, PR71, …]`（已出現在某則 digest 的 PR）。
  有新進就補一則 **delta**（只列新進；顧問旗標只留牽涉新進 PR 的）。
  貼失敗不記 listed，下一輪重試。
- 收口時 `PR` 不在 `listed` → provenance `cycle/silent+unlisted`，
  `_cycles/` 收據與 journal 都點名。真漏了也攤在陽光下。
- 升級相容：舊 state 只有 `digest:true` → 把當下池裡的視為已列（寧可少一則
  delta，不把整份重貼）。
- **孤兒週期修正**（原已知問題）：驗收 PR completed、池子空、但 `_pending`
  還有指向本週期分支的稿 → 不標 `closed`，journal 說明等它們裁決。

## 四、reject 三粒度（`_apply_rejects`）

```
reject <EntityName|路徑後綴>   整條（既有；撞名 → 回貼消歧義，歧義提示改建議用 PR id）
reject PR12                    整條，永不撞名
reject PR12-I1 / PR12-D2       只丟那一行；條目、摘要、History 行仍落地
```

行級 reject 寫回暫存稿並記 `rejected_ids`。ID 類 token 找不到 → journal
「查無此 ID」，不做任何事。

## 五、顧問分析（新檔 `orchestrator/knowledge_digest.py`）

- `analyze(repo, items)`：把整週期暫存稿（逐行帶 ID）＋ 同實體正式頁上還有效
  的不變式（帶 ID）餵給 `pr-review-digest` prompt。輸出合約：

  ```json
  {"headline": "…",
   "flags": [{"kind": "conflict|duplicate|retire_check|unclear",
              "ids": ["PR70-I1", "PR75-I1"], "note": "…", "suggest": "reject PR70-I1"}]}
  ```
- `validate()`：kind 正規化；`ids` 只留系統認得的（未知 ID 即幻覺，靜默丟）；
  一個都不剩的 flag 整個丟；`suggest` 必須是 `reject <已知 ID>` 否則清空；
  排序 conflict → retire_check → duplicate → unclear；上限
  `KNOWLEDGE_DIGEST_MAX_FLAGS`（預設 12）。
- `render()`：旗標與「⚠ Rules being retired」排在條目清單**前面**——掃的人
  先看要處理的。條目逐行列 ID，reject 才有東西可指。
- 任何失敗 → `None` → 純清單。`KNOWLEDGE_DIGEST_LLM=false` 直接關。
- `orchestrator/llm.py` 新增 `complete_sync()`：oneshot 程序用，每次新 loop +
  新 client（模組層 client 的連線池綁第一個 loop，跨 `asyncio.run` 會炸）。
  harvester 的 unit 本來就載 `orchestrator.env`，同一個 venv，不加服務。

## 六、其他

- `eval/seed_langfuse.py` 多推 `pr-review-digest`。
- `deploy/env/orchestrator.env.example`：`KNOWLEDGE_DIGEST_LLM`、
  `LLM_MODEL_DIGEST`、`KNOWLEDGE_DIGEST_MAX_FLAGS`（全註解、預設安全）。
- README §4 / §6、RUNBOOK §10.5–10.6、附錄 X 對應更新。
- `tests/test_core.py`：`kroot` fixture 設 `KNOWLEDGE_DIGEST_LLM=false`
  （測試零 LLM 的憲法）；新增 11 條。

---

## 部署

```bash
sudo cp orchestrator/knowledge.py orchestrator/knowledge_digest.py \
        orchestrator/knowledge_harvest.py orchestrator/llm.py \
        /opt/pr-review-suite/orchestrator/
sudo cp eval/seed_langfuse.py /opt/pr-review-suite/eval/
sudo systemctl restart review-orchestrator      # distiller 合約與 atomic write 在這裡
# harvester 是 oneshot，下一輪 timer 自動吃新 code
```

### ⚠ prompt 必須 re-seed（兩份）

distiller 合約新增 `retires_id`、digest 新增 `pr-review-digest`。
`prompt_or()` 是 Langfuse 優先——UI 上那份沒更新，code 裡的預設不生效：

```bash
prs venv/bin/python -m eval.seed_langfuse
```

## 驗收

```bash
venv/bin/python -m pytest -q tests/                      # 全綠
# 影子期驗收（KNOWLEDGE_POST=false 照舊）：
#  1. 看一條新蒸餾稿的 _pending JSON：invariants 應為 {id, text}
#  2. 開一條 release→master 驗收 PR，等一輪 timer：digest 逐行帶 `PRnn-Ix`，
#     頂端有 🔎 段（或 journal 出現「退回純清單」+ 原因）
#  3. 再合一條小 PR 進 release，等一輪：應多一則「update: 1 new entry」
#  4. 回 `reject PRnn-I1`，等一輪：journal「該行已移除（條目保留）」
#  5. Complete 驗收 PR：頁面該行不在、History 行含 rejected:；_cycles 收據無 +unlisted
```

## 仍待處理

- 蒸餾失敗只 print 不重試，該 PR 沒有知識且無痕（未動）。
- `_list_completed` `$top: 100` 無分頁（5–10 人團隊實務上碰不到，未動）。
- 帳本 source of truth 仍是 markdown（`_parse_page` regex 回讀）。ID 已經埋好，
  哪天要換成 JSON 為源、markdown 只 render，不需要再遷移 ID。
- 實體頁 Summary 仍「取最新 PR」——「current rules」view 重生成留到跑完第一個
  完整週期、trace 裡真的看到 `knowledge_entries > 0` 再做。
