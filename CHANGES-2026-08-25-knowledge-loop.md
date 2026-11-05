# 變更說明 — 2026-08-25（知識迴圈 Phase 1–5）

依 `PLAN-2026-08-25-knowledge-loop.md`（v2 定稿）實作 Phase 1 到 Phase 5。
Phase 0（digest / 條目 ID）已在 `CHANGES-2026-08-25-digest.md` 交付，這一包
不動它，只在它上面長。

18 個檔案修改、1 支新程式（`eval/run_distill_eval.py`）、29 個新 fixture
（14 頁種子帳本 + 5 條知識依賴題 diff + 10 條蒸餾考卷 diff）。
`tests/` 從 35 條長到 **60 條**，全綠、全離線、零 LLM、零網路。

---

## 對回三條原則

| 原則 | 這一包怎麼對回去 |
|---|---|
| **語意判斷在模型、驗證與寫入在 code** | distiller 的 evidence 從自由字串變成「引用系統給的座標」（切片裡看得到的行 / 本次 review 的 F 編號 / 有內容的 description），驗不過就剝；reviewer 的 `knowledge_ref` 只准引本次注入區塊裡真的出現過的 id，其餘 synthesis 時剝掉 |
| **先量再調** | 新增兩個量尺（線上引用率 = 上界、離線 `knowledge_delta` = 下界）與兩張考卷；排序改 tier **只做序、不做數**——沒有任何新的權重常數 |
| **降級可見** | 蒸餾切片超預算 → 材料留註記 + 稿裡 `distill_slices_skipped`；decision 被丟 → 稿裡 `distill_dropped` + 草稿留言 footer；注入降載 → `knowledge_full / invariants_only / omitted` 進 stats 與報告的 Coverage 表 |

---

## Phase 1 — 蒸餾證據地板

### 1.0 diff 幾何 predicates 搬家（防 circular import）

`_match_path` / `_line_in_view` / `_line_near` 從 `pipeline.py` 搬進
`anatomy.py`，升格為公開名 `match_path` / `line_in_view` / `line_near`。
`pipeline.py` 改 import、不留 alias；既有兩條測試改 import 路徑。
語意零改動——這一步單獨可驗，且讓 `knowledge.py` 不必反向 import
`pipeline`（那會成環）。

同理把 `static_pass._TEST_PATH` / `_DECL` 升格為 `TEST_PATH_RE` / `DECL_RE`：
「哪些檔是測試」「哪一行是新宣告」只能有一個定義。

### 1.1 distiller 吃 reviewer 看過的同一份 slices

- `distill_and_stage()` 多收 `diff` / `finding_ids` / `finding_files`，
  由 `app._handle_review_inner` 直接遞來（**不另造 diff 表示、不重 parse
  markdown**）。三個角色對 `file:line` 用同一個座標系。
- `distill_slices()` 的優先序（純 code）：① 新增行最多的那個檔 ② 被 review
  findings 指到的檔 ③ 含新宣告的檔 ④ 其餘；同級以 (新增行數 desc, 路徑) 定序
  ——同一份 diff 永遠切出同一份切片。預算 `DISTILL_DIFF_MAX_CHARS`（24000）。
- 超預算照 `slices_skipped` 家規：材料尾端
  `(N file(s) omitted from the diff excerpt: …)`，稿裡記
  `distill_slices_skipped`。
- prompt 補上：`file:line` 必須指向 excerpt 裡看得到的行。

### 1.2 evidence 驗證器

`verify_evidence(data, ana, finding_ids, description) -> (data, dropped)`，
在 `assign_ids` 之前跑。`finding_ids` 由呼叫端從 `result.findings` 建。

| evidence 形式 | 通過條件 | 不過 |
|---|---|---|
| `path:line` | `line_in_view`（檔在這份 diff 裡、行在切片可見行 ±2） | 剝掉 |
| `F<n>` | ∈ `finding_ids` | 剝掉 |
| `PR` | description 去空白後 ≥ 40 字 | 剝掉 |
| 其他字串 | — | 剝掉 |

evidence 被剝光的 decision **整條不入稿**——跟「引不出行號的 reject 不配
×0.35」同一個嚴格度。被丟的文字進稿裡的 `distill_dropped` 與 journal，
草稿留言 footer 多一行 `N decision(s) dropped: evidence could not be
verified` ——作者看得到，可以回一句 note 補救。

`PR` 這一形式**必須存在**：「選 X 不選 Y 因為 Z」這類 diff 看不到的知識只有
description 能撐；沒有它，驗證器會把最值錢的一類決策整批剝掉。

**invariants 沒有 evidence 欄位，不驗**——它們是規則不是主張。這是**明文
接受的殘餘風險**（見不做表），統計兜底是 Phase 4 的幻覺不變式陷阱題。

### 1.3 `primary_entity` 修正

- 模型自選的 `primary_entity` 必須是這次真的改到的檔（suffix 語意），
  驗不過才 fallback。
- fallback 從「路徑最長」改成「非 lockfile、非測試檔中**新增行數最多**」
  ——頁面錨點不該用最弱的啟發式（最長路徑通常是那個 integration test）。
- `affected` 不驗：它本來就允許指向未變更的鄰居。

---

## Phase 2 — 量尺

### 2.1 引用率（線上，trace 裡的上界）

- `load_for()` 多回 `stats["knowledge_ids"]`：**真的被注入的那幾段文字**裡
  出現的所有 `PR<n>-I<k>` / `PR<n>-D<k>`（尾註的格式說明不算數——例子不能
  變成合法引用）。
- `Finding` 多一個 `knowledge_ref: list[str]`（清單而非單值是刻意的：一條
  finding 可以站在兩條不變式上）。reviewer prompt 加上引用合約。
- `_synthesize` 確定性驗證：不在名單的 id 剝掉（不扣分，只剝）；
  `knowledge_grounded`（final findings 中帶有效引用的條數）、
  `knowledge_ids_injected`、`knowledge_refs_stripped` 進 stats。
  去重時若被丟掉的孿生帶著引用，會搬到留下來那條上（去重不該吃掉分子）。
- report 在 finding 上比照 `rule_ref` 顯示 `(knowledge: PR12-I1)`，
  Coverage 表多一句 `N finding(s) cite a ledger entry`。
- Langfuse 指標：`knowledge_grounded / knowledge_ids_injected`。
  **這個數只證明 reviewer 讀到了，不證明 finding 因它而來——它是上界。**

### 2.2 知識依賴題（離線，fixture 的下界）

- 5 題進同一個 `pr-review-golden`，`input.repo = kfix-<n>` 對應
  `eval/fixtures/knowledge/kfix-<n>/` 一個種子帳本；run_eval 進場時
  **run-level** 覆寫一次 `KNOWLEDGE_ROOT`（`EVAL_KNOWLEDGE_ROOT` 可改指，
  實際值記進 run metadata）。不做 per-item env 切換——`load_for` 走現成的
  repo 隔離，eval 併發也安全；其餘 30 題不帶 repo，天然拿不到任何頁。
- 題型配置與量的東西：
  - **3 題單頁**（kfix-1/2/3）：帳本只有一頁，注入必完整 → 量 **consumption
    純度**。三條命中路徑各一：Affected 鄰接、Affected 鄰接、`_module` 路徑段。
  - **2 題 distractor**（kfix-4/5）：同 repo 另有 4–5 頁，注入總量推破
    `KNOWLEDGE_MAX_CHARS` → 量**排序與預算有沒有參與**。kfix-4 的關鍵頁
    (tier 4) 字典序排最後，舊行為會被 `break` 掉；kfix-5 的關鍵頁是鄰接命中
    (tier 2)，只能靠降載成 invariants-only 活下來。
- `knowledge_recall`：expected 帶 `knowledge_ref` 的那條，要「同一條 finding
  命中 **且** 真的引了那個 id」才算 grounded——只引不抓是應付合約，只抓不引
  證明不了這條發現是知識帶來的。
- 對照組：跑完自動對**同一批題**（不是整份考卷）再跑一輪
  `KNOWLEDGE_ROOT=""` → `knowledge_delta`。兩種題型分開出分。
  CI gate `EVAL_MIN_KNOWLEDGE_DELTA` 預設不設 = report-only。

---

## Phase 3 — 檢索排序（結構版，只做序、不做數）

- `_MatchCtx.page_hit` 從布林改回 tier：`4` primary 精確路徑（頁面的
  `> entity:` 就是這次改的檔）> `3` (parent, stem) 檔名 > `2` Affected 鄰接
  > `1` `_module` 路徑段 > `0`。暫存稿用 `entry_tier` 套同一把尺，
  和正式頁一起排（不再無條件排在後面）。
- `load_for` 依 `(-tier, 路徑)` 排——同一份 diff 對同一個帳本 SHA 永遠注入
  同一組，可重現。
- 分級降載取代「第一條超預算就 break」：
  1. tier 4 保證入場（塞不下全文就注入 Invariants 段，這一步**優先於預算**）
  2. 其餘依序注入全文；第一次塞不下就切成「只注入 Invariants 段」續排
  3. 連短版都塞不下才停，尾端 `(N more matched entries omitted: …)`
- stats：`knowledge_full` / `knowledge_invariants_only` / `knowledge_omitted`。
- **明確不做**：recency / mass 權重、任何分數常數。要調就先用 fixture 校準。

---

## Phase 4 — 蒸餾考卷（`pr-review-distill-golden`，10 題）

`python -m eval.run_distill_eval`。task 跑的是**真的那支** `distill_once`
（含證據驗證與 primary 修正），計分全部確定性、零 LLM 評審：

`intent_ok`（PR 沒講 why 就別編）、`no_fabrication`（why 缺席題不准冒出
rationale 措辭）、`retires_ok`（集合完全相等——誤殺別條也扣）、`evidence_ok`
（剝 0 個 token、丟 0 條 decision）、`must_mention`、`primary_ok`（要模型
自己選中，靠 fallback 救回不算分）、`no_hallucination`、`distill_score`。

**三題陷阱**：
- d3 **why 缺席**：description 純 what → `intent_stated: true` 或編出
  「because / so that」都扣。
- d4 **supersede 假訊號**：討論聽起來像反轉但沒明示 → 填了 `retires_id` 就扣。
- d5 **幻覺不變式**：材料（純錯字修正）撐不起任何 invariant → 蒸出來就扣。
  這題就是「invariants 不做 evidence 驗證」那個殘餘風險的統計兜底。

`distill_once(..., catalogue=...)` 多一個選填參數：考卷用它直接給種子不變式
目錄（考的是蒸餾不是檢索；檢索有自己的量尺）。production 路徑 `None` = 原行為。

gate `EVAL_MIN_DISTILL_SCORE` 先 report-only。

---

## Phase 5 — stale 重蒸

「reviewer 提意見 → 作者改 → push」是價值最高的流量，過去這段被 stale
整條丟掉。

### 路徑

- **harvester 只偵測**：看到 completed 且 merge commit ≠ `distilled_commit`
  時，蓋請求戳並打 `POST {ORCH_URL}/knowledge/redistill`。
  `_land` 仍然零 LLM，不把半個 orchestrator 搬進收割器。
- 新端點過 `_check_auth`（**部署起 `ORCH_API_KEY`**——這條有 LLM 花費與寫檔
  語意，不再是純讀），收到即回 202、背景執行。
- orchestrator 背景流程：`fetch_pr_diff` → `parse_diff` → 同一份蒸餾合約
  （含 1.2 驗證器）→ 覆寫 `_pending/pr-<id>.json`（新 draft、
  `distilled_commit` = 最終 merge commit、`redistilled: true`、
  `redistill_of` = 舊 commit、清掉請求戳）。
- 回滾旋鈕 `KNOWLEDGE_REDISTILL=false` → 回到 stale 直接丟稿。

### 重蒸的蒸餾合約（材料窄一點換確定性）

`finding_ids = ∅`（舊 review 報告相對最終 diff 已 stale，`F<n>` 一律被剝）；
材料 = 最終 diff 切片 + title / branch / description + 不變式目錄 + 既收的
author replies（逐字，區塊標題註明是對更早那版草稿的回覆）。
**不含舊 review 報告**、graph 給空。

### 請求戳與重試上限

```
無戳                    → 蓋戳 {commit, attempts:1} → POST（連線失敗也算一次）
有戳、commit 相同        → attempts 1 → 2 再試一次；≥2 → 丟稿
有戳、commit 又變了      → 重蒸期間又 push（世代變更）→ 丟稿
重蒸成功（戳已清）       → commit 相等，不再 stale，照常分類 → staging
```

任何一條 PR 的重蒸 LLM 支出 ≤ 2 次；30 分鐘一輪的 timer 在任何失敗模式下
都不可能變成無界重試迴圈。

### ③ 的介面變更

`post_knowledge_draft(pr_url, markdown, thread_id=None)`：給定 thread_id →
回覆進那條 thread（`/threads/{id}/comments`）而不是開新 thread，內容前綴
`Draft updated after new commits; earlier author notes were kept.`
③ 仍唯寫；`DRY_RUN` × `KNOWLEDGE_POST` 語意不變——影子模式下重蒸稿一樣不真貼。

**版本偏移安全網**：`clients.post_knowledge_draft` 在 `thread_id=None` 時
**不送這個欄位**，所以 orchestrator 先更新、③ 晚一步 restart 也不會出事。
（若 ③ 因未知參數報錯，orchestrator 會記成「真的貼稿失敗」，harvester 就會
代貼——而那條補救路徑不看 `KNOWLEDGE_POST`，影子期會因此誤發。）

### 與 ID 機制的互動

重蒸重新產生 `PR12-*`，內容可能完全不同，所以重蒸時：
- 任何週期 state 的 `listed` 移除 `PR12` → 下一則 delta 重新列它
  （沒人看過的新內容不會頂著「已列」狀態無聲落地）；
- 池裡若已有同一條 PR 的暫存稿（手動補蒸的情境），連同它的 `rejected_ids`
  一起移除——reject 指的是舊行，套到新行是誤殺；
- 同週期其他稿的 `retires_id` 若指向舊 `PR12-*`，落地時當 miss、journal 記
  （`_mark_superseded` 既有的 `⚠ supersede 未蓋戳` 路徑）。

已收到的 `author_note` 保留、帶進重蒸材料。落地 provenance：
`cycle/silent·redistilled` / `cycle/explicit·redistilled`。
每條 PR 最多重蒸一次成功（`redistilled: true` 守門）。

---

## 測試（`tests/` 35 → 60）

新增 25 條，全離線零 LLM：

- **Phase 1**：evidence 四種形式 × 過/不過矩陣、剝光整條消失、`PR` 在
  description 太短時被剝、沒有 diff 時 file:line 一律不過；蒸餾切片優先序與
  預算註記；primary fallback（最長路徑 ≠ 最多新增行）與越界修正。
- **Phase 2**：注入 id 名單（尾註不算數）、`knowledge_ref` 確定性執法與
  grounded 計數、去重保留引用、report 顯示引用、scorer 要求「抓到 + 引了」。
- **Phase 3**：五種 tier 的判定、tier 4 保證入場 + 較弱命中被省略並留名、
  排序不吃 rglob 順序。
- **Phase 4**：滿分卷、三題陷阱各自的判法、evidence/primary 的扣分路徑。
- **Phase 5**：請求戳 → attempts 1 → 2 → 丟稿；世代變更即丟；旋鈕關掉直接
  丟且不打端點；作者回覆被保存進重蒸材料；重蒸覆寫 `_pending` + 清掉舊的
  「已列 / 已 reject」+ 回覆進原 thread + 只重蒸一次 + 稿在重蒸期間被收割器
  判死時不復活；③ 的 thread 回覆語意。

另有一支離線端到端煙霧腳本（罐頭 LLM 回應）驗過
「v2 pipeline → knowledge_ref → 蒸餾證據地板」整條路，不進 repo。

---

## 部署

```bash
sudo cp orchestrator/*.py /opt/pr-review-suite/orchestrator/
sudo cp tools/review_result_tools/server.py \
        /opt/pr-review-suite/tools/review_result_tools/
sudo cp eval/*.py /opt/pr-review-suite/eval/
sudo rsync -a eval/fixtures/ /opt/pr-review-suite/eval/fixtures/
sudo systemctl restart review-orchestrator pr-review-results
# harvester 是 oneshot，下一輪 timer 自動吃新 code
```

### ⚠ 兩份 prompt 必須 re-seed

`prompt_or()` 是 Langfuse 優先——UI 上那份沒更新，code 裡的預設不生效：

```bash
prs venv/bin/python -m eval.seed_langfuse
```

- `pr-review-distiller`：evidence 三形式的機械驗證規則、primary_entity 規則
- `pr-review-reviewer`：`knowledge_ref` 引用合約

seed 同時建 `pr-review-distill-golden`（10 題）並補灌 5 題知識依賴題。

### env（全部有預設，不設 = 行為不變）

| 變數 | 位置 | 預設 |
|---|---|---|
| `DISTILL_DIFF_MAX_CHARS` | orchestrator.env | 24000 |
| `KNOWLEDGE_REDISTILL` | orchestrator.env | true |
| `ORCH_URL` | orchestrator.env（harvester 共用） | http://127.0.0.1:8080 |
| `ORCH_API_KEY` | orchestrator.env | 未設 = 不驗（**用重蒸就設起來**） |
| `EVAL_KNOWLEDGE_ROOT` / `EVAL_KNOWLEDGE_AB` | eval 專用 | fixtures / true |
| `EVAL_MIN_KNOWLEDGE_DELTA` / `EVAL_MIN_DISTILL_SCORE` | eval 專用 | 未設 = report-only |

設 `ORCH_API_KEY` 之後，Open WebUI 與 `/review` 的呼叫方都要帶 Bearer；
`/eval-webhook` 刻意不驗（Langfuse 觸發時帶不了自訂 header）。

---

## 驗收

```bash
venv/bin/python -m pytest -q tests/           # 60 條全綠
venv/bin/python -m eval.preflight             # 知識題的種子帳本目錄也會被驗
venv/bin/python -m eval.run_eval knowledge-baseline
venv/bin/python -m eval.run_distill_eval distill-baseline
```

影子期看四件事：
1. 新蒸的 `_pending` 稿帶 `evidence_verified: true`、`distill_dropped`、
   `distill_slices_skipped`；
2. review 報告的 Coverage 表出現 `N entr… matched`（降載時標
   `M invariants only, K omitted`），有引用時多一句 `cite a ledger entry`；
3. `knowledge_delta` 兩型分開的數字（放行條件：連續兩輪 > 0，或單輪 ≥ 2/5）；
4. 一條「貼稿後又 push」的 PR：journal 走完
   請求重蒸 → 重蒸完成 → `staged（silent·redistilled）`。

**Phase 5 的完成條件**：上個週期 journal 裡「stale 丟稿」的條數，在下一個
週期降到接近 0；且沒有任何 PR 出現 3 次以上 redistill 字樣（重試上限被守住）。

---

## 仍待處理（沿用 PLAN 的「不做 / 延後」表）

- **invariants 的 evidence 驗證**：不做（明文接受的殘餘風險）。schema 無此欄；
  目前靠 Phase 4 的幻覺不變式陷阱題（統計兜底）＋驗收窗人裁（個案兜底）。
  Phase 4 量出失真率偏高再議 schema 加欄。
- 倒排索引 / BM25、Embedding 檢索：不做（`rglob` 全量讀撐十年；lexical 還沒
  被量出來有漏）。
- 排序權重（recency / mass）：等 Phase 2 兩個數穩定後，用 fixture 校準再議。
- 實體頁「current rules」view、帳本改 JSON 為源：Phase 2 gate 過了再做。
- LLM 直接改寫頁面：永不。
- 蒸餾**首次**失敗仍然沒有稿、且無痕（只 print）。`/knowledge/redistill` 這條
  端點救不了它——它從既有的 `_pending` 稿出發（沒稿就 return），所以能手動
  或定時重蒸「已經有稿」的 PR，不能無中生有。要補這個洞，得讓
  `distill_and_stage` 失敗時也留一份最小骨架稿；留給下一輪。
- `_list_completed` 的 `$top: 100` 無分頁（5–10 人團隊碰不到，未動）。

---

## 附錄 B — 同批次的旋鈕放大與考題調整

同一輪部署裡另外做了兩件事，跟 Phase 1–5 無關，但會一起上線。

### B1. 放大預設值（改 code 的 default，env 不必動）

分類的依據只有一個問題：**這個數會不會讓「送進模型的單一 prompt」變長**。
會的，放大就是在跟長 context 的注意力衰減對賭；不會的，放大只花錢和時間。

| 旋鈕 | 舊 → 新 | 進不進 prompt |
|---|---|---|
| `ADO_DIFF_MAX_FILES` | 100 → **300** | 不進（抓取層；順帶讓 static 掃得到更多檔） |
| `ADO_DIFF_MAX_FILE_BYTES` | 200000 → **600000** | 不進（單檔超過就整檔略過，這是覆蓋度不是長度） |
| `STATIC_TIMEOUT` | 90 → **300** | 不進（檔案變多，掃描時間跟著長） |
| `REVIEW_MAX_FINDINGS` | 10 → **30** | 不進（純輸出上限） |
| `DISTILL_DIFF_MAX_CHARS` | 24000 → **100000** | 進，但只進 distiller 那**一次**呼叫，沒有併發 |
| `REVIEW_MAX_DIMENSIONS` | 4 → **8** | 不讓 prompt 變長——**買覆蓋的正確方式是開科** |
| `REVIEW_MAX_PARALLEL` | 3 → **6** | 不進（併發度；gateway 有連線限制就往回調） |
| `CONTEXT_MAX_CHARS` | 8000 → **24000（3x）** | **會進每一科**，所以不是 5x |
| `KNOWLEDGE_MAX_CHARS` | 6000 → **18000（3x）** | **會進每一科**，同上 |

刻意**沒有**動的兩顆，理由是它們放大會反向：

- `REVIEWER_SLICES_MAX_CHARS`（30000/科）：這是每一科的切片預算。要讓模型看
  得完，開多科比把單科拉長好——後者讓單一 prompt 過長，注意力在中段掉。
- `ADVERSARY_CHUNK`（6）：它存在的唯一理由就是防注意力稀釋，放大等於關掉它。

順手把單檔切片上限升格成 env：`REVIEWER_FILE_SLICE_MAX_CHARS`（預設 6000，
與原本寫死的值相同 = 行為不變）。它跟 `REVIEWER_SLICES_MAX_CHARS` 是兩層：
一個管「單一檔最多給模型看多少」，一個管「一科總共多少」。

**這些是 code 的 default**，所以既有部署不改 env 就會吃到新值；要壓回舊值，
在 orchestrator.env 明寫該顆即可。改完務必重跑一輪 eval 對照基線——這一包
本身就是「先量再調」的反例練習：我們是先調了，所以更要立刻量。

### B2. 移除 pr-129 / pr-130

這兩題（41 檔 / 46 檔）的設計前提是「切片預算一定會破」，靠這個考截斷路徑
的行為。預算與科數放大之後前提不再成立，題目會安靜地退化成「跟別題考一樣的
東西」——留著比拿掉更糟，因為它看起來還在守著那條路徑。

- 刪 `eval/fixtures/pr-129-large-truncation.diff`、`pr-130-omitted-defect.diff`
- `CASES` 移除對應兩條（golden set 32 → **30 題**），註解寫明為什麼
- `preflight._GEOMETRY` 清空但**機制留著**：日後要再考截斷路徑，照當時的預算
  重新生 fixture、在這裡登記斷言即可
- README / env 樣板 / 測試裡的數字同步（測試那條「文件跟考卷同一個數」的
  斷言還在，所以三個地方對不上就會紅）

⚠ **Langfuse 上的 dataset 不會自己少兩題**：seed 是「逐條冪等」只新增不刪除。
去 UI 的 Datasets → `pr-review-golden` 把 pr-129 / pr-130 兩條 **archive**
掉，否則下一輪 eval 還是會跑它們，而 fixture 已經不在磁碟上——那兩題的
`input.diff` 存在 Langfuse 裡，所以它們會照跑、只是考的東西已經沒有意義。

### B3. 修：收口路徑補上首見水位

**症狀**（實測抓到）：清空 knowledge repo 重測 → 開第一條小 PR 進 `release/*`
並完成 → 手動跑一次 harvest → 條目**直接落地**，journal 出現「驗收 PR#xxx
收口」，而那條 PR 是上一輪早就完成的。

**根因**：`_cycle_close` 問 ADO 的是「最近 100 條 completed PR」，沒有時間
條件；「這條驗收 PR 已經結算過」唯一的記錄是 `_state/cycle.json`，而它住在
`KNOWLEDGE_ROOT` 底下。KB 一清，記憶沒了、世界還在 → 每一條歷史驗收 PR 都
被當成「已完成、待結算」，把當下暫存的稿立刻收走。`_watermark_scan` 對同一個
不對稱有「首見即定水位、不回填」的保護，`_cycle_close` 漏了。

**修法**：`_cycle_first_sight(repo, state)` —— 該 repo 在 state 裡一個 key
都沒有 = 首見。首見時把當下所有 completed 的驗收 PR 直接標 `closed`（附
`note` 說明），不落地、不寫收據，journal 印一行。判斷刻意放在 active 迴圈
**之前**算，否則 `setdefault` 出來的 key 會汙染自己的答案。

**副作用（明文）**：週期進行到一半才啟用時，那一輪的收口會被吞掉，暫存稿
等下一條驗收 PR。要強制收，把 `_state/cycle.json` 裡那條的 `closed` 拿掉。

測試：`test_cycle_close_first_sight_does_not_backfill`（首見不回填、稿留在池
裡、state 標 closed；之後新完成的驗收 PR 照常收口）。既有兩條穩態測試
（分支隔離、等待未裁決小 PR）改成先寫一筆 state 宣告「非首見」，才走得到
它們原本要測的路徑。

### B4. 修：已註銷的規則不再被注入給 reviewer

**症狀**（實測抓到）：某條 invariant 已經被 supersede 畫掉（頁面上是
`- ~~規則~~ ⚠ retired by PR#11`），下一次 review 仍然拿它報 violation。

**根因**：兩條讀取路徑對「畫掉」的處理不一致——`existing_invariants()`
（給 distiller 的目錄）早就排除畫掉的行，但 `load_for()`（注入給 reviewer）
是把整頁 markdown 原樣塞進去的，畫掉的行照樣進 prompt，連它的 id 都還留在
`knowledge_ids` 裡（等於允許 reviewer 拿一條死規則當 grounding）。

**修法**：`strip_retired(body)` —— 注入前把 `- ~~…` 開頭的行拿掉，數量記進
`stats["knowledge_retired_hidden"]`。**頁面不動**（註銷是標記不是蒸發，人要
看得到血緣），變的只有餵給模型的那一份。

**為什麼不是「留著讓模型自己讀懂 retired 標記」**：那是把一個確定性判斷交給
模型去猜，而猜錯的方向是誤報——照這套系統自己的原則（false positive 比漏報
更快燒掉信任），這種判斷該留在 code。

測試：`test_retired_invariants_stay_on_the_page_but_leave_the_injection`。

### B5. 新增：規則會被實作推翻（帳本不再只進不出）

**問題**：立下的規則實務上拔不掉。加一條只要沉默 merge，拔一條卻要作者在
description 用剛好的句式明示、還要 distiller 判定算「明示反轉」——**加容易、
拔困難**，方向剛好是反的：錯誤的規則會永久製造誤報，而誤報是這套系統最怕的。
真實流程（bot 擋 → 作者說「需求改了」→ merge）系統完全沒有回應。

**為什麼不是「PR 完成就自動拔」**：merge 過一個警告，跟「作者沒讀留言就按
Complete」在資料上長得一樣。自動拔 = 每次真的 regression 都順手刪掉那條本來
會抓到它的規則，錯誤自我抹除且無聲。

**做法**：把矛盾變成一個**必須被裁決的事件**，用 Phase 2 已經建好的機制偵測。

1. **偵測（確定性）**：reviewer 的 finding 帶 `knowledge_ref`（且已被 synthesis
   驗證過只能引真的注入過的 id）。severity 為 error/warning 的 finding 引到的
   id，就是「這次實作踩到的規則」→ 寫進 `_pending` 的 `contradicted`。
2. **提案（可見）**：digest 新增紅區「⚠ Invariants contradicted by merged
   code」，列出 id、是哪條 PR 推翻的，並附上否決指令。
3. **裁決（沉默即接受）**：收口時沒被 `keep <ID>` 否決的，就在已落地的頁上
   **畫掉那一行**（`~~text~~ ⚠ retired by PR#nn (contradicted by merged code)`）
   ＋ History 留行 ＋ 週期收據多一段。與新條目同一套核准語意。
4. **自我收斂**：作者若是照 review 把 code 改回來才 push → stale 重蒸 → 重蒸
   沒有 review 報告可驗，`contradicted` 一律清空。修好的案子不會誤拔規則。

新指令 `keep PR12-I1`（行首命令式，與 `reject` 同一個模式：人裁、code 執行、
零 LLM、只認精確 ID）。旋鈕 `KNOWLEDGE_RETIRE_ON_CONTRADICTION=false` 整段關掉。

**明文的殘餘風險**：如果那條 finding 本身是誤報，而作者因為知道是誤報所以照
merge，這條規則會被提案拔除。擋它的是驗收窗（digest 紅區 + `keep`）——這正是
驗收窗存在的理由。`_retire_by_id` 另外守兩件事：ID 查無此頁、或撞到多頁，
一律不動只留 journal。

測試 5 條：收口拔除 + 兩條讀取路徑同步、`keep` 否決、旋鈕關閉、ID 歧義/查無、
digest 紅區渲染。另補一條 app→distiller 的接線測試（diff / F 編號 /
finding_files / contradicted 四個參數釘住，那段斷掉只會 print 一行 journal）。
