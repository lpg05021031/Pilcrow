# pr-review-suite

內網自架的 AI code review 生產線：貼一條 Azure DevOps PR 連結，產出一份
證據齊全的英文審查報告；每條進 release 的 PR 順手被蒸餾成團隊決策帳本，
回頭餵給下一場 review。**新增外部依賴：0。全部跑在自己的機器上。**

本文件講「為什麼這樣設計」；操作步驟見 `RUNBOOK.md`。
（最後更新：2026-08-25）

---

## 1. 系統全景

```
Open WebUI (:3001)
   └─ review-orchestrator (:8080)   ← 唯一接 Langfuse 的服務
        ├─ ① ado_pr_tools   (:8094)  唯讀：PR metadata / diff
        ├─ ② repo_reader    (:8095)  唯讀（v2 零呼叫者；可除役或留作擴充面）
        ├─ ③ review_result  (:8096)  唯寫：回貼留言（review 稿 / 知識稿分道）
        ├─ ④ code graph     (:8097)  選配：影響半徑（per-call repo_root 切庫）
        ├─ REPOS_ROOT/               唯讀鏡像庫（repo-sync timer 保鮮）
        ├─ RULES_ROOT/               團隊規則（git、SHA 錨、targeted 載入）
        └─ KNOWLEDGE_ROOT/           決策帳本（本系統的第二個輸出）
Langfuse (:3000)                      trace / prompt 版控 / eval 儀表
```

讀與寫刻意拆成不同服務（①② 唯讀、③ 唯寫），權限治理可以只放行讀。
repo 從 PR URL 現場解析——整套系統 **repo-agnostic**，每顆 repo 的裝備分三檔：
零設定（完整 review、缺 base 上下文）→ +clone（base 上下文，timer 自動保鮮）
→ +graph（影響半徑）。

## 2. 五條承重牆（設計哲學）

**一、能確定性計算的，絕不交給 LLM。**
diff 解剖、行號標定、semgrep/啟發式掃描（信心一律 1.0）、coverage gate、
synthesis 的去重與門檻——全是純函式。LLM 只負責需要理解力的部分，
且輸出全程受 JSON 合約約束、附降級路徑（壞 JSON → 固定四科，不會死）。
確定性層是 recall 的地板：模型心情再差，這一層照樣開火。

**二、變聰明是量出來的，不是感覺出來的。**
30 題 golden set（static 保底題 / LLM 邏輯題 / hard / 乾淨負樣本）押著
三道 CI gate：recall 1.000、noise 0、precision 走勢。任何 prompt、旋鈕、
規則改動，先過考再上線；Langfuse 一鍵 A/B（`?pipeline=v1|v2`）；scorer 另列 `recall_static_share`，headline recall 的 static 地板占比攤在儀表上。
量尺同時抓兩種錯：agent 的錯、和**考卷自己的錯**——eval 驗屍是雙向的。
知識迴圈同樣押著量尺：**5 題知識依賴題**（含對照組 → `knowledge_delta`）與
**10 題蒸餾考卷**（含三題陷阱）——K 段的兩顆 LLM 從此也要先過考再上線。
**先量再調**：任何權重、門檻，沒有量尺就不加。

**三、發現必須站在證據上。**
報告裡每條 finding 有 F 編號、file:line、規則出處、知識條目 id 四級證據；
adversary 要 reject 一條發現，必須引用行號並指明踩了哪一項挑戰，指不出只准
uncertain。知識條目同理：**無連結的句子不得出現**——distiller 產的每條
decision 都要引到「切片裡看得到的行 / 本次 review 的 finding 編號 / 有內容的
PR description」，驗不過的 evidence 剝掉、剝光的整條不入稿，數字進 stats、
痕跡進草稿留言。LLM 的輸出型別是「引用系統給的 ID」，不是自由字串。

**四、不新增服務；重用已驗證的模式。**
知識帳本完整複製 rules repo 的成功模式（本地 git、SHA 錨、targeted
載入）；所有背景工作都是「timer + oneshot + journal」三件套；狀態帳本
就是 git。輪詢、不開 webhook；**每個資料域只有一個寫手**（repos 歸
repo-sync、knowledge 歸 harvester、pending 只寫檔不碰 git），併發衝突
從結構上不存在。

**五、對外的每一次寫入都有雙開關縱深。**
`AUTO_POST`（orchestrator 點火）×`DRY_RUN`（③ 煞車）管 review 回貼；
`KNOWLEDGE_POST` 管小 PR 蒸餾稿；`KNOWLEDGE_DIGEST` 管收口 digest。
出廠全安全側，誤設任何單顆都不會誤發。

## 3. Review pipeline（v2，九站）

1. **取件**（code）— ① 拉 metadata 與 diff。
2. **解剖 + 靜態**（code）— anatomy 標定真實新行號、稀疏重建；semgrep
   （Java 規則家族實測校準）＋ ruff ＋ 啟發式；**確定性 MISSING-TEST**：
   diff 有新宣告且零測試檔 → conf 1.0 保底，跟模型無關。只評新增行。
3. **上下文預抓**（code）— base clone、graph 影響半徑（單實例
   per-call `repo_root` 自動切庫）、團隊規則、知識條目。不做 agentic
   tool loop：預抓是確定性的，可重現、可歸因。知識條目依命中強度
   （tier 4 精確路徑 > 3 檔名 > 2 Affected 鄰接 > 1 模組路徑段）排序，
   預算不足時**分級降載**：tier 4 保證入場、其餘退成 Invariants 段、
   真的塞不下才省略並留名。全部是 ordinal，沒有需要校準的分數。
4. **Planner**（LLM）— 讀結構摘要開 2–8 科，每科帶可驗證假說與指派
   規則；四類別鏡片＋PERF 必須全數被認領。
5. **Coverage gate**（code）— 孤兒檔、孤兒規則自動補 Sweep 科：
   「某檔沒人看」在數學上不可能。
6. **平行 Reviewers**（LLM）— 逐假說驗證、在 assessment 交代結局；
   targeted context 只給被指派規則的內文；「不確定就別報」。finding 若
   站在某條帳本不變式上，要引它的 id（`knowledge_ref`）——只准引本次注入
   區塊裡真的出現過的 id，其餘 synthesis 時剝掉。
7. **Adversary**（LLM，可換腦）— 分塊挑戰每條 LLM 發現；rejected 必須
   引行號＋挑戰編號。static 發現不送審。
8. **Synthesis**（code）— 去重（static×LLM 交叉命中 → corroborated）、
   信心乘數（confirmed ×1.25 / rejected ×0.35）、門檻、上限。尺是硬的。
9. **Render**（code）— v3 英文報告：Outcome 三態、F 編號交叉引用、
   Required actions、四張表、graph 證據。零發現照樣完整。

## 4. 知識迴圈（決策帳本）

**定位**：決策帳本，不是文件網站。PR 是「意圖」與「實作」唯一同框的
時刻——PRD 只有 why 且會漂移、code 只有 what、commit message 太瘦；
知識若不在落地那一刻釘下來，就進口耳相傳的墳。

**三元組**（各守一座墳）：
- **reviewer 的理解**（蒸餾原料：review 報告、影響半徑）→ 防空白；
- **作者回饋**（逐字入帳的 Author note ＋ why 捕撈）→ 防「自信的錯誤」
  ——LLM 摘要偏一點點，錯得篤定的文件比沒有更毒；
- **PR 終態**（裁決）→ 防「從未發生的知識」——abandoned 的 PR 蒸出來
  的是反知識，必須丟。

**三鐵律**：檢索優先（第一讀者是自己的 reviewer，人類 wiki 是副產品）、
證據優先（每句掛 PR / file:line）、錨在實體（頁面錨 code 實體不錨 PR，
同實體新決策接續同一頁——像 git blame，但 blame 的是決策）。

**週期驗收制**（配合「小 PR → release/*，release 完成日一條
release→master 驗收 PR」的分支節奏）：

```
小 PR review 完 ──K1 閘門──▶ K2 蒸餾+貼稿(不催) ──completed──▶ _cycle/ 暫存
      ▲                              │                             │
      │                     draft 後又有 push                       │
      │                              ▼                             │
      │                  重蒸（最終 diff，≤2 次）──▶ 覆寫 _pending ─┘
      └────── 暫存稿即可被同週期 review 注入（staged）◀────────────┘
驗收 PR active ──▶ cycle digest（驗收窗：任何人 reject <Entity>）
驗收 PR completed ──▶ 批次落地（cycle/explicit|silent）+ _cycles/ 週期收據
```

- **K1 全確定性**：目標分支 ∈ `KNOWLEDGE_BRANCHES`；version-bump 與
  分支同步 merge 排除；revert 必收；驗收 PR 本身豁免（儀式非知識單元）。
- **核准權不在小 PR**：沉默＝候選；驗收窗一則 digest 讓 release owner
  掃一眼（首次全列，之後每有新暫存稿補一則 delta——沒人看過的條目不會
  無聲落地，真漏了 provenance 會帶 `+unlisted`）。`reject <EntityName>`
  整條淘汰、`reject PR12` 整條且不撞名、`reject PR12-I1` 只丟一行；
  completed 前回都算數（收口時會再讀一次留言）。resolve 與否完全不影響
  判定，與強制 resolve 政策零衝突。
- **條目 ID 蒸餾時即定**：`PR12-D1` / `PR12-I1`（PR id + 序號，天然全域唯一），
  暫存→digest→落地一路不變。supersede 用 `retires_id` 精確比對（逐字複製只
  服務還沒有 ID 的舊行）；digest 的顧問分析、reject 全部認 ID。
- **digest 是提案不是帳本**：收口時 LLM 把整週期暫存稿按實體一起看（distiller
  是逐條 PR 看的，看不到同週期鄰居），標出矛盾 / 近重複 / 該註銷沒點名，
  建議 `reject <ID>`——只引用系統給的 ID，驗證不過的一律丟，失敗退回純清單。
  落地路徑永遠不讀它：LLM 提、人裁、code 執行，與 adversary → synthesis
  同一個模式。harvester 的**寫入路徑**維持零 LLM。
- **Provenance 攤在陽光下**：`cycle/explicit`（作者說過話）、
  `cycle/silent`、`staged`、`unreviewed`（watermark 補的漏網 PR 帳本行，
  release 史零遺漏）。
- **Supersede 是一級操作**：註銷比新增風險高——distiller 只在 PR 明示
  反轉時才填、嚴禁推論；digest 以「⚠ Rules being retired」紅區獨立呈現；
  落地時被廢頁**頂端蓋戳、內容保留**——註銷是標記，不是蒸發。
- **規則會被實作推翻**（帳本不能只進不出）：reviewer 報 finding 時要引它踩到
  的不變式 id，所以「這條規則被違反了」是**確定性**的事實。PR 帶著違反完成
  ＝實作推翻了規則——但這只是**提案**，不是自動執行：進 digest 的
  「⚠ Invariants contradicted by merged code」紅區，收口時沉默即接受（與新條目
  同一套核准語意），任何人回 `keep PR12-I1` 就否決。執行時一樣是畫掉不刪除。
  刻意不做「完成就自動拔」：merge 過一個警告，跟「沒看留言就按 Complete」在
  資料上一樣，自動拔會讓每次 regression 順手刪掉本來會抓到它的那條規則。
  作者若是照 review 修好再 push，stale 重蒸會把矛盾清掉——正確的方向自己會收斂。
- **零種子起步**：PR 分散使實體級種子命中率趨近零；空帳本同時是免費的
  無知識對照組。檢索靠雙鍵：主鍵＝頁面實體、副鍵＝條目的 Affected
  entities（graph 真鄰接）——不變式住在 A、有人改鄰居 B，照樣被提醒。
- **stale 不再等於丟掉**：「reviewer 提意見 → 作者改 → push」是價值最高的
  流量，過去整條被 stale 丟棄。現在 harvester **只偵測**，打一條
  `POST /knowledge/redistill` 給 orchestrator，用最終 diff 重蒸並覆寫
  `_pending`（作者先前的回覆逐字帶進材料、更新稿回覆進原 thread）。
  請求戳 + `attempts ≤ 2` 封死重試迴圈；重蒸期間又 push（世代變更）照舊丟。
  重蒸會作廢舊的「已列 / 已 reject」記錄——沒人看過的新內容不會頂著
  「已列」狀態無聲落地。回滾旋鈕 `KNOWLEDGE_REDISTILL=false`。
- **遙測與量尺**：實際注入的條目名單進 trace（`knowledge_injected`）；
  線上引用率 `knowledge_grounded / knowledge_ids_injected`（**上界**——只證明
  reviewer 讀到了）；離線 `knowledge_delta = 有帳本 − 無帳本`（**下界**，
  fixture 的 5 題分成 3 單頁 + 2 distractor，delta=0 時才判得出是 consumption
  問題還是排序問題）。月度抽 5 條人工判「還成立嗎」記衰減率——記憶層的健康
  儀表板。自動 reconciliation（confirmed/superseded 裁決）掛觸發條件：
  衰減率連兩月偏高才啟動，空帳本期不建。

**work item 刻意整個排除在蒸餾輸入外**：亂 link 的單餵給 distiller 一個
錯的 why，比空白更毒。why 缺席就標 `intent: not stated`，並在稿末向
作者問一行（含否定性決策引導）——在記憶最新鮮、成本最低的時刻捕撈。

## 5. 評測體系

`eval/fixtures/` 30 題三層結構：static 保底（掃描器必中）、LLM 邏輯題
（跨檔單位錯配、@Transactional 自呼叫、N+1…）、乾淨負樣本（誘發誤報的
FP 陷阱）。期望採**類別級比對**（expected 不釘檔案只比 rule）——釘死
「模型任意選擇」的比對維度會讓分數閃爍。webhook 一鍵觸發
（`/eval-webhook?pipeline=v1|v2`），run 名記時間、metadata 認世代；
seed 冪等且每跑必升 prompt 版本（日常調參走 Langfuse UI，seed 留給
部署輪）。

**知識依賴題（5 題，同一個 dataset）**：`input.repo = kfix-<n>` 對應
`eval/fixtures/knowledge/` 底下一個種子帳本；缺陷只有讀過帳本才抓得到，
expected 的那條 finding 帶 `knowledge_ref`。3 題**單頁**（注入必完整，量
consumption 純度）+ 2 題 **distractor**（注入總量推破預算，量排序與降載有沒有
參與）。跑完自動對同一批題再跑一次 `KNOWLEDGE_ROOT=""` 的對照組 →
`knowledge_delta`。整個知識迴圈最重要的就是這個數：>0 才值得繼續蓋，=0 就照
「單頁也 0 → consumption 問題；只有 distractor 0 → 排序問題」的順序診斷。

**蒸餾考卷（10 題，`pr-review-distill-golden`）**：
`python -m eval.run_distill_eval`。K 段的 LLM 過去零考卷——改 distiller prompt
等於裸奔。計分全確定性：`intent_ok`（PR 沒講 why 就別編）、`retires_ok`
（supersede 只認明示、誤殺別條也扣）、`evidence_ok`（產出的 evidence 過不過
Phase 1 驗證器）、`must_mention`、`primary_ok`、`no_hallucination`。三題是陷阱：
why 缺席、supersede 假訊號、幻覺不變式——最後一題是「invariants 不做 evidence
驗證」這個明文殘餘風險的統計兜底。兩張考卷的 gate 都先 report-only。

## 6. 維運速查

| 開關 | 位置 | 管什麼 | 出廠 |
|---|---|---|---|
| `AUTO_POST` | orchestrator.env | review 完是否呼叫 ③ | false |
| `DRY_RUN` | results.env | ③ 收到 review 稿是否真發 | true |
| `KNOWLEDGE_POST` | results.env | 小 PR 蒸餾稿是否真貼 | false |
| `KNOWLEDGE_DIGEST` | orchestrator.env | 收口 digest 是否貼 | true |
| `KNOWLEDGE_DIGEST_LLM` | orchestrator.env | digest 顧問分析（提案，不落地） | true |
| `KNOWLEDGE_ROOT/_BRANCHES/_CYCLE_*` | orchestrator.env | K 段開關與節奏 | 未設=停用 |
| `KNOWLEDGE_REDISTILL` | orchestrator.env | stale 稿用最終 diff 重蒸（≤2 次） | true |
| `ORCH_API_KEY` + `ORCH_URL` | orchestrator.env | 重蒸端點的鑰匙與位址（harvester 共用） | 未設=不驗 |
| `GRAPH_TOOL_BLAST_ARGS`（`$repo_dir`） | orchestrator.env | graph 多 repo 自動切庫 | — |
| `REVIEW_PIPELINE` | orchestrator.env | v1/v2 十秒回滾 | v2 |

服務判讀口訣：**常駐組怕 dead、定時組怕 failed**（oneshot 平時
`inactive (dead)` 是待命不是屍體，看 `list-timers` 的 NEXT/LAST）。
env 是行程啟動時讀的：改完必 restart，活體對帳用
`/proc/<MainPID>/environ`。血淚榜首：installer 跑完必 `chown`；
env 值一律單引號；systemd unit 的 PATH 要含 venv。

## 7. 目錄地圖

```
orchestrator/   pipeline、planner/reviewer/adversary、synthesis、render、
                anatomy.py（diff 解剖＋共用的 diff 幾何 predicates）、
                knowledge.py（K1+K2+loader+條目 ID+證據驗證+重蒸）、
                knowledge_digest.py（digest 渲染＋顧問分析）、
                knowledge_harvest.py（唯一寫手；重蒸只偵測不執行）
tools/          ①②③ FastMCP 服務
shared/         ADO URL 解析與 HTTP client（笨層，無業務邏輯）
eval/           golden fixtures（30 題）、knowledge/ + knowledge_cases/（5 題
                知識依賴題與其種子帳本）、distill/（10 題蒸餾考卷）、
                run_eval、run_distill_eval、seed_langfuse（六份 prompt）
deploy/         env 樣板、systemd 樣板、semgrep starter、knowledge 模板
docs/           三張流程/原型 HTML（主線、知識迴圈、wiki 條目）
RUNBOOK.md      Phase 0–10：從裸機到知識迴圈的逐步部署
```

## 8. 上線節奏（建議）

影子期（全暗或僅 digest 可見）→ 翻 `KNOWLEDGE_POST`（why 捕撈上線，
配一句話公告）→ 翻 `AUTO_POST`+`DRY_RUN=false`（review 真回貼）。
每一步獨立可回退，回退成本都是一顆 env + restart。
