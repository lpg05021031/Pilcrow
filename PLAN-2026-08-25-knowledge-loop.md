# 知識迴圈 — 最終實作計畫（2026-08-25 · v2 定稿）

定案版 v2：在 v1 草案上收掉五個審查洞（附錄 A），方向不變。
順序、合約、完成條件、不做的事，都在這裡；後續不再改方向，只改細節。

原則只有三條，每一項都要能對回去：

1. **語意判斷在模型、驗證與寫入在 code**。LLM 的輸出型別是「引用系統給的
   ID / 行號」，不是自由字串。
2. **先量再調**。任何權重、門檻，都要先有量尺，否則不加。
3. **降級可見**。任何被截、被丟、被剝的東西，在 stats 或註記裡留痕，
   不安靜蒸發。

---

## Phase 0 — 已交付：digest / 條目 ID（部署 + 跑一個完整週期）

內容見 `CHANGES-2026-08-25-digest.md`。這一階段不是「做完了」，是「上線觀察」：

- 部署後 re-seed 兩份 prompt（`pr-review-distiller`、`pr-review-digest`）。
- 影子期跑完**一個完整 release 週期**，確認五件事：首次 digest、delta、
  `reject PR12-I1`、`+unlisted` 沒出現（或出現了但有原因）、`_cycles/` 收據。
- 這個週期的 journal 是 Phase 5 的輸入：數一下有幾條稿被 stale 丟掉。

**Gate → Phase 1**：無條件。Phase 1 不依賴週期結果。

---

## Phase 1 — 蒸餾證據地板（一個 PR，約 1–2 天）

影子期產的稿就是將來第一批落地的東西；證據下限越早立越好。
四件事一起做，因為它們共用同一份輸入。

### 1.0 前置：diff 幾何 predicates 搬家（洞 5：防 circular import）

- `_match_path` / `_line_in_view` / `_line_near` 現居 `pipeline.py`，而
  `pipeline.py` 已經 `import knowledge`；1.2 的驗證器住在 `knowledge.py`，
  反向引用 pipeline 就成環。
- 三個函式是純 diff 幾何，搬進 `anatomy.py` 升格為公開名：
  `match_path` / `line_in_view` / `line_near`。`pipeline.py` 改 import，
  不留 alias；既有測試改 import 路徑（兩處）。
- 語意零改動——這一步只搬位置，先於一切、單獨可驗。

### 1.1 distiller 吃 reviewer 看過的同一份 slices

- `app._handle_review_inner` 手上有 `diff`；`distill_and_stage` 多收
  `diff: str` 與 `finding_ids: set[str]`（見 1.2），內部 `parse_diff` →
  `numbered_slice`。**不另造 diff 表示**：三個角色（reviewer / adversary /
  distiller）對 `file:line` 用同一個座標系。
- 預算 `DISTILL_DIFF_MAX_CHARS`（預設 24000）。優先序（純 code）：
  1. 新增行數最多的檔
  2. 被 review findings 引用的檔
  3. 含 `_DECL` 命中的 hunk（宣告 / 簽名變更是決策密度最高處）
  4. 其餘
- 超預算照 `slices_skipped` 家規：material 末尾註記
  `(N files omitted from the diff excerpt: …)`，stats 記
  `distill_slices_skipped`。
- prompt 補一句：`file:line` evidence 必須指向 excerpt 內看得到的行。

### 1.2 evidence 驗證器（純函式，進 `tests/`）

`verify_evidence(data, ana, finding_ids, description) -> (data, dropped: list[str])`，
在 `assign_ids` 之前跑。`finding_ids` 由呼叫端從 `result.findings` 建
（`{f"F{i}" for i in 1..n}`），**不重 parse markdown**。

| evidence 形式 | 通過條件 | 不過 |
|---|---|---|
| `path:line` | `path` 以 `anatomy.match_path` 語意 ∈ `ana.files`，且 `line` 在該檔可見行 ±2（`anatomy.line_in_view`） | 剝掉這一條 evidence |
| `F<n>` | `F<n>` ∈ `finding_ids` | 剝掉 |
| `PR` | description 去空白後 ≥ 40 字 | 剝掉 |
| 其他字串 | — | 剝掉 |

- decision 的 evidence **全被剝光 → 整條不入稿**，跟「引不出行號的 reject
  不配 ×0.35」同一個嚴格度。
- 被丟的 decision 文字進 `stats["distill_dropped"]` 與 journal；草稿留言
  footer 加一行 `N decision(s) dropped: evidence could not be verified`
  ——作者看得到，可以回 note 補救。
- invariants 沒有 evidence 欄位，不動。它們是規則不是主張；規則的驗證在
  Phase 2 的 reviewer 引用與 Phase 4 的考卷（含幻覺不變式陷阱題）。
  這是**明文接受的殘餘風險**，已列入「不做 / 延後」表。
- `PR` 這一形式**必須存在**：「選 X 不選 Y 因為 Z」這類 diff 看不到的知識
  只有 description 能撐，沒有它驗證器會把最值錢的一類決策整批剝掉。

### 1.3 `primary_entity` 修正

- distiller 自選的 `primary_entity` 必須 ∈ `ana.files`（suffix 語意），
  驗不過才 fallback。
- fallback 改成：非 `_EXCLUDE_BASENAMES`、非測試檔中**新增行數最多**的檔
  （現在是「路徑最長」，是頁面錨點卻用最弱的啟發式）。
- `affected` 不驗——它本來就允許指向未變更的鄰居（graph impacted）。

### 測試

- predicates 搬家後 import 與行為不變（既有兩測改路徑後全綠）。
- 驗證器矩陣：四種形式 × 過 / 不過；全剝光整條消失；`PR` 在 description
  太短時被剝。
- slices 優先序與預算註記。
- primary fallback：最長路徑 ≠ 最多新增行的案例。

**完成條件**：新產出的每條 `_pending` 稿帶 `evidence_verified: true` 與
`dropped` 計數；`tests/` 全綠。

**部署**：distiller prompt 有更新（evidence 行號規則）→
re-seed `pr-review-distiller`。

---

## Phase 2 — 量尺（兩個 PR，約 2–3 天）

README 早就寫了成敗指標是「引用率 × review 增益」，但從來沒接儀表。
這一階段決定 K 段後面還投不投。

### 2.1 引用率（線上，trace 裡的上界）

- `load_for` 回傳多一個 `stats["knowledge_ids"]`：本次注入區塊裡出現的
  所有 `PR<n>-I<k>` / `PR<n>-D<k>`。
- reviewer 輸出合約加選填 `knowledge_ref: ["PR12-I1"]`（清單——一條
  finding 可以站在兩條不變式上；與 `rule_ref` 的單值不同是刻意的）。
  prompt：「finding 若依據 Knowledge 區塊裡的某條不變式提出，引它的 id；
  只能引區塊裡出現的 id。」
- `_synthesize` 確定性驗證：`knowledge_ref ⊆ stats["knowledge_ids"]`，
  不在名單的剝掉（不扣分，只剝）。
- stats：`knowledge_grounded`（final findings 中帶有效 ref 的條數）、
  `knowledge_ids_injected`。render 在 finding 上比照 `rule_ref` 顯示
  `knowledge: PR12-I1`。
- Langfuse 指標：`knowledge_grounded / PRs with knowledge_entries > 0`。

這個數只證明 reviewer **讀到了**，不證明 finding **因它而來**——它是上界。
下界在 2.2。

**部署**：reviewer prompt 有更新（knowledge_ref 合約）→
**re-seed `pr-review-reviewer`** + restart orchestrator。

### 2.2 知識依賴題（離線，fixture 的下界）

- fixture 三件套：`diff` + `description` + 種子帳本頁。放法：run_eval 進場
  時 run-level 覆寫一次 `KNOWLEDGE_ROOT=eval/fixtures/knowledge`（可用
  `EVAL_KNOWLEDGE_ROOT` 改指向；實際值記進 run metadata），每題
  `input.repo = "kfix-<n>"` 對應一個子目錄——**不做 per-item env 切換**，
  `load_for` 走現成的 repo 隔離，eval 併發也安全；其餘考題不帶 repo，
  天然拿不到任何頁，零污染。
- **題型配置（洞 3）**，5 題起、兩型分開出分：
  - 3 題「單頁」：kfix repo 只有一頁（不變式住 A 頁、diff 改鄰居 B，
    透過 Affected / 路徑段命中）。注入必完整——量的是 **consumption 純度**。
  - 2 題「distractor」：同 repo 再 seed 3–5 頁其他實體的頁，把注入總量
    推過 `KNOWLEDGE_MAX_CHARS`（6000）——量的是**預算與排序的參與**。
- expected 裡那條 finding 帶 `knowledge_ref`；scorer 加 `knowledge_recall`
  （expected 帶 ref 的題，final 有同 ref 的比例）。
- 對照組：同一套題 `KNOWLEDGE_ROOT=""` 再跑一輪 →
  `knowledge_delta = recall_with − recall_without`。
- CI gate `EVAL_MIN_KNOWLEDGE_DELTA`：先 report-only（門檻 0），
  基線穩定兩輪後再升成硬門。

**完成條件**：儀表上看得到上界（線上引用率）與下界（fixture delta）兩個數，
且 delta 能按題型拆開讀。

**Gate → Phase 3–5（洞 4：樣本量）**：

- 放行條件：**連續兩輪 delta > 0，或單輪 ≥ 2/5**。
  一次僥倖命中（1/5）不放行。
- delta = 0 的診斷順序（洞 3 的題型配置讓這一步可判）：
  1. **單頁題也 0** → consumption 問題：迭代 reviewer prompt（Knowledge
     區塊的位置 / 措辭），Phase 3 救不了，先不做。
  2. **只有 distractor 題 0** → 排序 / 預算問題：做 Phase 3，重量。
  3. 兩者修完仍為 0 → K 段進維護模式，Phase 4 / 5 不投。

---

## Phase 3 — 檢索排序（結構版，一個 PR，約 1 天）

只做序、不做數。所有排序都是 ordinal，不需要校準。

### 3.1 tier 取代布林

- `_MatchCtx.page_hit` 改回傳 tier：`4` primary 精確路徑 > `3`
  (parent, stem) 檔名 > `2` Affected 鄰接 > `1` `_module` 路徑段 >
  `0` 未命中。
- `load_for` 依 `(-tier, path)` 排；同 tier 路徑字典序——同一份 diff 對
  同一個帳本 SHA 永遠注入同一組（可重現）。
- `_cycle/` 暫存稿套同一套 tier，和正式頁一起排，不再無條件排在後面。

### 3.2 分級降載取代「第一條超預算就 break」

1. tier 4 的頁**保證入場**；若單頁就超 cap，注入它的 Invariants 段。
2. 其餘依序注入全文；下一頁塞不下 → 切換成「只注入 Invariants 段」模式續排。
3. Invariants 段也塞不下 → 停，尾端註記
   `(N more matched entries omitted: PR12, PR15, …)`。
- stats：`knowledge_full`、`knowledge_invariants_only`、`knowledge_omitted`。

### 明確不做

- recency / mass 權重、任何分數常數。等 Phase 2 的兩個數穩定後，
  若要調，用 fixture 校準。

**完成條件**：測試涵蓋 tier 序、primary 保證入場、降載註記；
Phase 2 的 fixture 重跑一次（特別看 distractor 題）delta 有沒有動。

---

## Phase 4 — 蒸餾考卷（約 2 天，Phase 2 之後）

review pipeline 有 32 題押著，K 段的 LLM 輸出零考卷——方法論上最明顯的
不一致。

- 10 題：`diff` + `description` + 種子不變式目錄（帶 ID）。
- **題型至少含三題陷阱**：
  - 1 題 **why 缺席**：description 純 what——編出 rationale 或
    `intent_stated: true` 都扣。
  - 1 題 **supersede 假訊號**：討論聽起來像反轉但非明示——填了
    `retires_id` 就扣（量「嚴禁推論」守不守得住）。
  - 1 題 **幻覺不變式**：材料撐不起任何 invariant——蒸出 invariant 就扣
    （invariants 不做 evidence 驗證的統計兜底，見不做表）。
- expected（全部確定性 scorer）：
  - `intent_stated` 判對（description 沒講 why 的題，編出 rationale 就扣）
  - `retires_id` 精確命中種子目錄裡指定的 ID（誤殺其他 ID 也扣）
  - evidence 全數通過 Phase 1 驗證器
  - 必抓決策關鍵詞（`must_mention`）
  - `primary_entity` ∈ 變更檔
- `python -m eval.run_distill_eval`，dataset `pr-review-distill-golden`，
  seed 腳本註冊。
- gate 先 report-only。從此改 distiller prompt 跟改 reviewer prompt 一樣
  要先過考。

---

## Phase 5 — stale 重蒸（先設計後做，約 3–4 天，Phase 1 + 2 之後）

價值最高的流量是「reviewer 提意見 → 作者改 → push」，現在這段被 stale
整條丟掉。它不是順手解鎖，是一個要設計的功能，以下定案。

### 路徑

- harvester **只偵測**，orchestrator 做全部。`_land` 仍然零 LLM；
  **不把半個 orchestrator 搬進 harvester**。
- 新 endpoint `POST {ORCH}/knowledge/redistill {"pr_url": ...}`，過
  `_check_auth`（**部署起 `ORCH_API_KEY`**——這個 endpoint 有 LLM 花費
  與寫檔語意，不再是純讀）。收到即回 202、背景執行（`_bg` 家規）。
- orchestrator 背景流程：`clients.fetch_pr_diff`（走 ①，同一份 diff 語意）
  → `parse_diff` → 同一份 distiller 合約（含 1.2 驗證器）→ 覆寫
  `_pending/pr-<id>.json`：新 draft、`distilled_commit` = 最終 merge
  commit、`redistilled: true`、`redistill_of: <舊 commit>`、清掉
  `redistill_requested` 戳。
- 回滾旋鈕：`KNOWLEDGE_REDISTILL`（orchestrator.env，預設 true）；
  false = harvester 不發請求，回到 stale 丟稿。

### 重蒸的蒸餾合約（洞 2 後半）

- `finding_ids` 傳**空集合**：舊 review 報告相對最終 diff 已 stale，
  `F<n>` evidence 一律被驗證器剝掉；decision 只准 ground 在最終 diff 與
  description。
- 材料 = 最終 diff slices + title / branch / description + inv catalogue
  + 既收的 author replies（逐字，區塊標題註明 "author's reply to an
  earlier draft"）。**不含舊 review 報告**、graph 給空——重蒸是補救路徑，
  材料窄一點換確定性。

### 請求戳與重試上限（洞 1）

harvester 在 `_process_pending` 看到 completed 且 merge commit ≠
`distilled_commit` 時：

```
無 redistill_requested 戳
    → 蓋戳 {commit: <merge commit>, attempts: 1}（write_json_atomic）
    → POST /knowledge/redistill；journal "redistill requested"
    （POST 連線失敗也算一次 attempt，戳照蓋）
有戳、戳的 commit == 目前 merge commit（上次請求後沒有成功重蒸）
    attempts == 1 → attempts=2、再 POST 一次（transient 失敗的補救）
    attempts >= 2 → 丟稿；journal "redistill failed twice → stale 丟稿"
有戳、但 merge commit 又變了（重蒸期間又 push）
    → 丟稿；journal 記世代變更
重蒸成功（orchestrator 已覆寫 pending、清戳）
    → commit 相等，不再 stale，照常走分類 → staging
```

- 上限硬編：任何一條 PR 的重蒸 LLM 支出 ≤ 2 次嘗試；30 分鐘 timer
  在任何失敗模式下都不可能變成無界重試迴圈。

### ③ 的介面變更（洞 2 前半）

- `post_knowledge_draft` 加選填 `thread_id`：給定 → 回覆進該 thread
  （不開新 thread），內容前綴
  `Draft updated after new commits; earlier author notes were kept.`
- ③ 仍唯寫；`DRY_RUN` × `KNOWLEDGE_POST` 語意不變——影子模式下重蒸稿
  一樣不真貼，靜默承諾不繞道。
- 這是對 ③ 的程式變更 + restart，列入部署清單。

### 與 ID 機制的互動（必須做，否則跟 Phase 0 打架）

- 重蒸重新產生 `PR12-*`，內容可能完全不同。重蒸時：
  - 任何 active 週期 state 的 `listed` 移除 `PR12` → 下一則 delta 重新
    列它（沒人看過的新內容不會頂著「已列」狀態無聲落地）；
  - 清掉舊的 `rejected_ids`（reject 指的是舊行）；
  - 同週期其他稿 `retires_id` 指向舊 `PR12-*` → 落地時當 miss，journal 記。
- 已收到的 `author_note` 保留、累加。

### 邊界

- 每條 PR 最多重蒸一次成功（`redistilled: true` 守門）；重蒸後再有 push
  → 照舊 stale 丟，journal 說明。
- 落地 provenance：`cycle/silent·redistilled` / `cycle/explicit·redistilled`。

**完成條件**：Phase 0 那個週期 journal 裡「stale 丟稿」的條數，在下一個
週期降到接近 0；且 journal 裡沒有任何 PR 出現 3 次以上 redistill 字樣
（重試上限被驗證守住）。

---

## 不做 / 延後（明文，避免之後又拿出來討論）

| 項目 | 決定 | 理由 |
|---|---|---|
| invariants 的 evidence 驗證 | 不做（明文接受的殘餘風險） | schema 無 evidence 欄。幻覺不變式比幻覺 decision 毒（會被注入未來 review 當 leads），目前靠 Phase 4 幻覺不變式陷阱題（統計兜底）＋驗收窗人裁（個案兜底）。Phase 4 量出失真率偏高再議 schema 加欄，不在本輪 |
| 倒排索引 / BM25 | 不做 | 5–10 人的帳本，`rglob` 全量讀撐十年 |
| Embedding 檢索 | 不做 | lexical 還沒被量出來有漏 |
| 排序權重（recency / mass） | Phase 2 兩個數穩定後再議 | 沒有量尺就是另一組 0.35 |
| 實體頁「current rules」view（解 Summary 取最新） | Phase 2 gate 過了再做 | 帳本近乎空時沒東西可摘 |
| 帳本改 JSON 為源、markdown 只 render | 同上 | ID 已埋好，屆時不需再遷移 |
| LLM 直接改寫頁面 | 永不 | 違反「靜默蒸發是大忌」與冪等 |
| 蒸餾失敗重試 | 併入 Phase 5 | 同一條 endpoint 可以手動 / 定時補蒸 |

---

## 部署清單（跨 phase 彙總）

| Phase | 動作 |
|---|---|
| 0 | re-seed `pr-review-distiller`、`pr-review-digest` |
| 1 | distiller prompt 更新 → re-seed `pr-review-distiller`；restart orchestrator |
| 2 | reviewer prompt 更新 → **re-seed `pr-review-reviewer`**；restart orchestrator |
| 3 | restart orchestrator（純 code） |
| 4 | seed `pr-review-distill-golden` dataset |
| 5 | ③ 更新（thread_id 回覆）→ restart results；orchestrator 新 endpoint → restart；ado.env 之外**設起 `ORCH_API_KEY`** 並同步給 harvester env |

新增 env（全部有預設、不設不變行為）：
`DISTILL_DIFF_MAX_CHARS=24000` · `EVAL_KNOWLEDGE_ROOT`（eval 專用）·
`EVAL_MIN_KNOWLEDGE_DELTA`（report-only 起步）· `KNOWLEDGE_REDISTILL=true`
（Phase 5 回滾旋鈕）。

---

## 順序與工時（單人，粗估）

```
Phase 0  部署 + 一個週期觀察                        （日曆時間，非工時）
Phase 1  predicates 搬家 + 證據地板三件套            1–2 天   ← 現在就做
Phase 2  引用率 + 知識依賴題（3 單頁 + 2 distractor） 2–3 天   ← 決定後面投不投
Phase 3  排序結構版                                  1 天
Phase 4  蒸餾考卷（含三題陷阱）                      2 天
Phase 5  stale 重蒸（endpoint + 請求戳 + ③ thread 回覆） 3–4 天 ← 先過設計再動手
```

Phase 1 和 Phase 2 之間沒有 gate；Phase 2 之後有（連續兩輪 delta > 0，
或單輪 ≥ 2/5）。整個計畫最重要的一個數是 `knowledge_delta`——它 > 0
這個迴圈才值得繼續蓋，它 = 0 就照診斷順序修到底，修完還是 0 就停在
維護模式，把時間還給主線 review。

---

## 附錄 A — v1 → v2 審查修訂記錄

| # | 洞 | 修在哪 |
|---|---|---|
| 1 | 重蒸「POST 被收下、背景失敗」→ 30 分鐘一次的無界 LLM 重試 | Phase 5 請求戳 + attempts ≤ 2（連線失敗也計次） |
| 2 | ③ 需支援 thread 回覆未列入計畫；重蒸會引用 stale 的 F 編號 | Phase 5「③ 介面變更」段；重蒸合約 `finding_ids = ∅` |
| 3 | 「delta=0 → 做 Phase 3」逃生門與單頁 fixture 對不上（單頁題注入必完整，排序不參與） | Phase 2 題型配置 3 單頁 + 2 distractor、分開出分、三步診斷順序 |
| 4 | funding gate 單輪 5 題，一次僥倖命中（1/5）就放行 | 連續兩輪 delta > 0，或單輪 ≥ 2/5 |
| 5 | `_match_path` / `_line_in_view` 住 pipeline，knowledge 反向 import 成環 | Phase 1.0 搬進 `anatomy.py` 升格公開名 |

另兩條註記併入：reviewer prompt re-seed 進部署清單（Phase 2）；
「invariants 不驗 evidence」明文進不做表，並在 Phase 4 加幻覺不變式
陷阱題作統計兜底。
