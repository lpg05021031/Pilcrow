#!/usr/bin/env bash
# ============================================================================
# pr-review-suite —— 知識迴圈 Phase 1–5 + 旋鈕放大（2026-08-25）
#
# 內容：
#   Phase 1 蒸餾證據地板（同一份切片、evidence 機械驗證、primary 錨點）
#   Phase 2 量尺（線上引用率 knowledge_ref / 離線 knowledge_delta + 5 題 fixture）
#   Phase 3 檢索排序 tier + 分級降載（只做序、不做數）
#   Phase 4 蒸餾考卷 10 題（含三題陷阱）＋ run_distill_eval
#   Phase 5 stale 重蒸（/knowledge/redistill、請求戳、③ thread 回覆）
#   旋鈕放大：抓取 300 檔、8 科 / 6 併發、findings 30、蒸餾切片 100k…
#   移除 pr-129 / pr-130（截斷路徑考題，預算放大後前提失效）
#
# 用法：
#   bash pr-review-suite-knowledge-loop.sh /opt/pr-review-suite
#   bash pr-review-suite-knowledge-loop.sh            # 預設寫到目前目錄
#
# 行為：只覆寫下列檔案，覆寫前備份到 <目標>/.kl-backup-<時間戳>/；
#       **不動 env 實檔、不動 systemd、不 restart 任何服務**。
#       部署後的三件事印在腳本結尾。
# ============================================================================
set -euo pipefail
TARGET="${1:-.}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET/.kl-backup-$STAMP"
mkdir -p "$TARGET"

write() {
  local rel="$1" dst="$TARGET/$1"
  if [ -f "$dst" ]; then
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -p "$dst" "$BACKUP/$rel"
  fi
  mkdir -p "$(dirname "$dst")"
  cat > "$dst"
  echo "  寫入 $rel"
}

drop() {
  local rel="$1" dst="$TARGET/$1"
  if [ -f "$dst" ]; then
    mkdir -p "$BACKUP/$(dirname "$rel")"
    mv "$dst" "$BACKUP/$rel"
    echo "  移除 $rel（已備份）"
  fi
}

echo "展開知識迴圈 Phase 1-5 到：$TARGET"

write "README.md" <<'PATCH_EOF'
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
PATCH_EOF

write "RUNBOOK.md" <<'PATCH_EOF'
# pr-review-suite v2 部署 Runbook（細版）

適用：已照原 README 部署過 v1 的主機（`/opt/pr-review-suite` + venv、
`/etc/pr-review-suite/*.env`、四個 systemd 服務、Langfuse :3000）。
每一步都附「預期輸出」與「判讀」。任何一步卡住，先看文末 Troubleshooting。

---

## Phase 0：前置盤點（約 5 分鐘）

**0.1 定義本文件的標準執行姿勢**（每開一個新 shell 貼一次）：

```bash
prs() { sudo -u svc-review bash -c "set -a; source /etc/pr-review-suite/orchestrator.env; set +a; export PATH=/opt/pr-review-suite/venv/bin:\$PATH; cd /opt/pr-review-suite; $*"; }
```

意義：以服務帳號、載入正式 env、**把 venv/bin 排進 PATH**（後面 static pass
找 semgrep/ruff 靠它）、站在專案根目錄執行。之後文件裡 `prs <指令>` 都指這個。
限制：參數別帶引號；帶引號的長指令請自行展開成完整 `sudo -u … bash -c '…'`。

**0.2 Python 版本門檻**（v2 用了 3.10+ 語法，過不了這關全部免談）：

```bash
prs venv/bin/python --version
```

預期 `Python 3.10` 以上。低於 3.10 → 先重建 venv 再回來。

**0.3 現況確認**：

```bash
systemctl is-active review-orchestrator pr-review-ado pr-review-reader pr-review-results
curl -s localhost:8080/healthz
```

預期四個 `active` + `{"ok":true}`。

**0.4 全量快照**（installer 自帶逐檔備份，這是雙保險、一鍵還原用）：

```bash
sudo tar czf /root/pr-review-suite-pre-v2-$(date +%F).tgz /opt/pr-review-suite /etc/pr-review-suite
```

---

## Phase 1：套用檔案

**1.1** 把 `pr-review-suite-v2.sh` 放上主機（scp / 內網檔案伺服器皆可）。
若經過 Windows 剪貼簿，先驗屍（血淚 #2）：

```bash
file pr-review-suite-v2.sh        # 要看到 Bourne-Again shell script
grep -c $'\r' pr-review-suite-v2.sh   # 要是 0；不是 0 → sed -i 's/\r$//' 治療
```

**1.2** 套用 + 還權：

```bash
sudo bash pr-review-suite-v2.sh /opt/pr-review-suite
sudo chown -R svc-review: /opt/pr-review-suite
```

預期：41 行 `寫入 …`，結尾
`完成：41 個檔案。被覆寫的舊版備份在 /opt/pr-review-suite/.v2-backup-<時間戳>`。

**1.3** 匯入健檢（語法/相依一次驗完）：

```bash
prs venv/bin/python -c "import orchestrator.pipeline, orchestrator.core, orchestrator.app, eval.run_eval, eval.preflight; print('import ok')"
```

預期 `import ok`。報 `ModuleNotFoundError: fastapi/fastmcp/...` →
`prs venv/bin/pip install -r requirements.txt` 補齊後重驗。

---

## Phase 2：靜態掃描依賴（semgrep / ruff）

**2.1** 裝進 venv（如需 proxy，在 pip 前加 `HTTPS_PROXY=http://proxy.corp:3128`）：

```bash
prs venv/bin/pip install semgrep ruff
prs semgrep --version
prs ruff --version
```

預期各印一行版本號。裝不動也能繼續（static 段會自動跳過並在 stats 註記），
但強烈建議裝——pr-002 那類注入從「賭模型」變「必中」全靠它。

**2.2 ⚠ 服務端 PATH 地雷**：systemd 跑 `venv/bin/uvicorn` 時 **PATH 不含
venv/bin**，`shutil.which("semgrep")` 會找不到 → 服務裡 static 段永遠跳過。
解法是給 unit 加 PATH override：

```bash
sudo systemctl edit review-orchestrator
```

在打開的檔案寫入：

```ini
[Service]
Environment=PATH=/opt/pr-review-suite/venv/bin:/usr/local/bin:/usr/bin:/bin
```

存檔即可（`daemon-reload` 由 Phase 4 一起做）。CLI 側不受影響——`prs` 已含。

---

## Phase 3：本地 clone 佈局 + sync timer

**3.1** 目錄命名規則先搞對：`/opt/repos/<repo小寫>/`，`<repo>` 必須等於
PR URL 裡 `_git/<這段>` 的**小寫**。拿一條真 PR URL 驗證你要用的名字：

```bash
echo "<PR URL>" | grep -oP '_git/\K[^/]+' | tr 'A-Z' 'a-z'
```

**3.2** 一次性 git 全域設定（svc-review 身分；公司 CA + proxy，血淚 #3）：

```bash
sudo -u svc-review git config --global http.sslCAInfo /etc/ssl/certs/ca-certificates.crt
# 有 proxy 才需要：
sudo -u svc-review git config --global http.proxy http://proxy.corp:3128
```

**3.3** clone（checkout **target branch**，通常 main）。PAT 認證兩選一：

```bash
sudo mkdir -p /opt/repos && sudo chown svc-review: /opt/repos
# 方案 A（簡單，PAT 進 .git/config，僅 svc-review 可讀，內網可接受）：
sudo -u svc-review git clone --branch main \
  "https://anything:<ADO_PAT>@<ADO主機>/<collection>/<project>/_git/<repo>" \
  /opt/repos/<repo小寫>
# 方案 B（PAT 不落地）：credential.helper store，第一次 clone 互動輸入一次
```

**3.4** 關鍵驗證——timer 之後做的就是這件事，先手動確認它不會卡互動：

```bash
sudo -u svc-review git -C /opt/repos/<repo小寫> pull --ff-only
```

預期 `Already up to date.`。跳出要帳密 → 回 3.3；SSL 錯 → 回 3.2。

**3.5** timer 上線：

```bash
sudo cp /opt/pr-review-suite/deploy/systemd/pr-review-repo-sync.service.example /etc/systemd/system/pr-review-repo-sync.service
sudo cp /opt/pr-review-suite/deploy/systemd/pr-review-repo-sync.timer.example  /etc/systemd/system/pr-review-repo-sync.timer
# 若之後裝 code-review-graph 進 venv，這個 service 也要 PATH（現在順手做掉）：
sudo systemctl edit pr-review-repo-sync   # 寫入與 2.2 相同的 [Service] Environment=PATH=…
sudo systemctl daemon-reload
sudo systemctl enable --now pr-review-repo-sync.timer
sudo systemctl start pr-review-repo-sync.service     # 手動觸發一次
journalctl -u pr-review-repo-sync.service -n 20 --no-pager
```

預期每個 repo 一行 `[repo-sync] <名字> -> <sha>`。
`pull 失敗` → 回 3.4 的兩個病因。

---

## Phase 4：env 更新 + 重啟

**4.1** `/etc/pr-review-suite/orchestrator.env` 追加（**註解獨立成行、
等號右邊零尾巴**，血淚 #1）：

```bash
REVIEW_PIPELINE=v1
REPOS_ROOT=/opt/repos
```

沒做過 rules 那輪的話，`RULES_ROOT=/opt/pr-review-rules` 也一併補上。
v2 其他旋鈕（REVIEW_MIN_CONFIDENCE 等）先**全用預設**，不要現在調。

**4.2** `/etc/pr-review-suite/reader.env`：把 `REPO_ROOT=…` 整行換成：

```bash
REPOS_ROOT=/opt/repos
```

（HOST/PORT 不動。）

**4.3** 重啟 + 活體對帳（你們自己的招）：

```bash
sudo systemctl daemon-reload
sudo systemctl restart review-orchestrator pr-review-reader
for s in review-orchestrator pr-review-reader; do
  echo "== $s"; sudo cat /proc/$(systemctl show -p MainPID --value $s)/environ | tr '\0' '\n' | grep -E '^(REVIEW_PIPELINE|REPOS_ROOT|RULES_ROOT|PATH)='
done
curl -s localhost:8080/healthz
```

判讀：orchestrator 要看到 `REVIEW_PIPELINE=v1`、`REPOS_ROOT`、PATH 開頭是
venv/bin；reader 要看到 `REPOS_ROOT`。少哪個 = 哪個 env/override 沒生效
（restart 而非 reload？CRLF？override 檔存了嗎？）。

---

## Phase 5：播種 prompts + preflight + v1 回歸基線

**5.1** 播種（現在會推四份 prompt）：

```bash
prs venv/bin/python -m eval.seed_langfuse
```

預期：

```
prompt 'pr-review-system' 已推上 Langfuse（label: production）
prompt 'pr-review-planner' 已推上 …
prompt 'pr-review-reviewer' 已推上 …
prompt 'pr-review-adversary' 已推上 …
dataset 'pr-review-golden' 寫入 2 條 items —— 去 UI 的 Datasets 確認
```

注意：重跑 seed 是**新增** items 不是覆蓋——dataset 裡出現重複的 pr-002
就去 UI 把舊 shape 那條 archive 掉（scorer 相容，留著也只是灌水題）。

**5.2** Preflight：

```bash
prs venv/bin/python -m eval.preflight
```

預期 `Preflight 通過` + 統計行（v2 幾條/舊版幾條/乾淨負樣本幾條）。
列出問題 → 照訊息去 UI 整形那幾條 item，修完重跑。

**5.3** v1 回歸（證明蓋檔沒動到舊路徑）：

```bash
prs venv/bin/python -m eval.run_eval v1-post-patch
```

預期輸出樣貌：

```
run 'v1-post-patch'：N 條（v2 X／舊 Y／乾淨 Z）
  recall 平均 0.xx｜噪音率 0.xx｜禁語率 0.xx
  配置：prompt v4 × rules <sha> × <模型名>
→ UI：Datasets → pr-review-golden → Runs 看逐條分數與 trace
```

判讀：recall 要跟你們之前的基線同一個量級。差很多 → 先停，
看 UI 裡哪條 item 掉分、點進 trace 查，別急著往下。

---

## Phase 6：v2 先過 eval（完全不碰服務）

eval 走 CLI 自己的環境，所以 A/B 不需要動任何服務——這是設計好的。

**6.1** 連跑兩輪定 v2 基線 + 雜訊底：

```bash
prs REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval v2-a
prs REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval v2-b
```

判讀三件事：
1. **pr-002 的 SECURITY 必中**（static 抓的，confidence 1.0）——沒中去看
   run 輸出有沒有 `static_notes`（十之八九是 semgrep 沒進 PATH，回 2.2）。
2. v2-a vs v2-b 的分差 = 你們的**雜訊底線**，記下來。
3. UI Runs 勾 `v1-post-patch` + `v2-a` 對比：recall 應 ≥ v1；
   noise 若升 → 之後調 `REVIEW_MIN_CONFIDENCE`（0.55→0.65）再跑一輪比。

**6.2** 點開 v2 任一 item 的 trace，瀑布應長這樣：
`run-review → plan → review-dimension ×N（平行）→ adversary → synthesis`，
metadata 有 `dimensions / static_findings / llm_findings / adversary=ok(…)`。
`plan=fallback` 偶發正常（降級=固定四科）；**每條都 fallback** → gateway 吐
不出乾淨 JSON，點開 plan 的 generation 看原始輸出，去 Langfuse UI 改
`pr-review-planner` 的措辭（零改碼）再跑。

**6.3** 離線 eval 裡 `base_available=false`、graph 全跳過是**預期行為**
（fixtures 只有 diff）——base/graph 的效果要等 Phase 7 真 PR 才看得到。

---

## Phase 7：服務切 v2 + 真 PR 驗證

**7.1** `orchestrator.env` 把 `REVIEW_PIPELINE=v1` 改成 `v2` →
`sudo systemctl restart review-orchestrator` → 4.3 的活體對帳再跑一次確認。

**7.2** 真 PR 打一發：

```bash
curl -s -X POST localhost:8080/review -H 'content-type: application/json' \
  -d '{"pr_url":"<PR URL>"}' | head -c 1200
```

判讀 markdown：開頭 `## 🤖 PR Review`、**沒有 JSON 塊**、結尾 footer
`pipeline v2 · 面向 N · static S · llm L · adversary ok(…) · rules <sha>`。

**7.3** 去 Langfuse 看這條 trace 的 metadata，重點三個旗標：
`base_files > 0`（本地 clone 接通了）、`rules_sha` 有值、
`adversary=ok(…)`。`base_available=false` → repo 目錄名對不上：
比對 trace 裡 `fetch_pr_metadata` 輸出的 `repo` 值和 `ls /opt/repos/`。

**7.4** Open WebUI 貼同一條 PR：串流旁白照舊（🔍📋📄🤖），最後吐出的是
v2 的乾淨 markdown。注意 dedupe：TTL 內重貼是重播快取，屬正常。

**7.5** 回滾姿勢背起來：env 改回 `REVIEW_PIPELINE=v1` + restart，十秒完事。

---

## Phase 8：code graph ④（選配，隨時可補）

以下的工具名、URL 路徑、參數樣板都已對 code-review-graph 實機驗證過。
關鍵事實：它的 `serve` 有**原生 `--http` 模式**（MCP over Streamable HTTP），
所以不需要任何橋接——直接就是 clients.py 認得的那種 HTTP MCP server。

**8.1 安裝（裝進同一個 venv；PATH 已在 2.2/3.5 一次解掉）**：

```bash
prs venv/bin/pip install code-review-graph
prs code-review-graph --version
```

注意：它的相依會把 opentelemetry 拉到比 semgrep 要求更新的版本，pip 可能
印相容性警告——實測兩者都能正常執行，警告可忽略；潔癖版是
`pip install semgrep ruff code-review-graph` 三個一起裝讓 resolver 自己喬。

**8.2 首次全量建圖**（graph DB 落在該 repo 的 `.code-review-graph/`）：

```bash
sudo -u <服務user> bash -c 'export PATH=<安裝根>/venv/bin:$PATH; cd <REPOS_ROOT>/<repo小寫> && code-review-graph build'
```

預期最後一行 `Full build: N files, X nodes, Y edges`。之後不用再手動——
3.5 的 timer 每輪 pull 完會自動 `build`（增量，只重解析有改的檔）。
`igraph not available` 的 INFO 行無害（社群偵測退用檔案模式）。

**8.3 起 MCP server（systemd）**：

```bash
sudo cp <安裝根>/deploy/systemd/pr-review-graph.service.example /etc/systemd/system/pr-review-graph.service
sudo vi /etc/systemd/system/pr-review-graph.service   # 換掉三個 <佔位符>：服務user／安裝根／REPOS_ROOT+repo
sudo systemctl daemon-reload
sudo systemctl enable --now pr-review-graph
systemctl is-active pr-review-graph
journalctl -u pr-review-graph -n 5 --no-pager
```

unit 的 ExecStart 骨架（example 檔裡就是這個）：

```
<安裝根>/venv/bin/code-review-graph serve \
    --repo <REPOS_ROOT>/<repo小寫> \
    --http --host 127.0.0.1 --port 8097 \
    --tools get_impact_radius_tool,get_review_context_tool,query_graph_tool
```

`--tools` 是白名單：它總共有 30 個 MCP tools，只放行 pipeline 用得到的。
判讀：journal 出現 uvicorn 的啟動行即可。**用 curl 打 `/mcp` 會回 406，
這是正常的**（MCP over HTTP 要特定 Accept header）——健康檢查請用 8.4
的 list tools，別被 406 騙去 debug。設計取捨：unit 刻意**不開**
`--auto-watch`，讓 3.5 的 timer 當 graph 的唯一寫入者（單一寫手，
避免和 timer 的 build 併發搶 SQLite）。

**8.4 驗收 + 填 env**。先 list tools：

```bash
prs venv/bin/python - <<'EOF'
import asyncio
from fastmcp import Client
async def main():
    async with Client("http://127.0.0.1:8097/mcp") as c:
        for t in await c.list_tools():
            print(t.name)
asyncio.run(main())
EOF
```

預期正好列出白名單那三個。接著在 orchestrator.env 打開（值已實測）：

```bash
GRAPH_TOOLS_URL=http://127.0.0.1:8097/mcp
GRAPH_TOOL_BLAST=get_impact_radius_tool
GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files}'
```

（`$files` 會被代入成變更檔路徑陣列；該工具吃 repo 相對路徑，我們的
anatomy 給的正是這個。想餵更完整的審查上下文可改用
`get_review_context_tool`，換之前先用 list tools 確認它的參數名。）

**多 repo 自動切庫（實測可行）**：不必每 repo 一個 serve——工具支援
per-call `repo_root`，單一實例即可照 PR 自動切到對應 clone：

```bash
GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files, "repo_root": "<REPOS_ROOT>/$repo_dir"}'
```

（`$repo_dir` 代入「repo 名小寫、無引號」；此模式 **GRAPH_REPO 留空**；
serve 啟動時的 `--repo` 只是預設庫，隨便指主力那顆即可。前提：目標 repo
要先 onboard 且 build 過圖——repo-sync timer 對 REPOS_ROOT 下所有 clone
自動建圖，onboard 即涵蓋；未 onboard 的 repo 呼叫會失敗並優雅降級。）
然後 `sudo systemctl restart review-orchestrator` + 4.3 活體對帳看到
三個 GRAPH_ 變數。

**8.5 端到端驗收**：真 PR 再 `curl localhost:8080/review` 一發，
到 Langfuse 看 trace metadata——`graph_used=true`，reviewer 的輸入裡
會多一段 `### code graph（受影響範圍）` 開頭是
`Blast radius for N changed file(s): …`。`graph_used=false` 時看
`graph_note`：`GRAPH_TOOLS_URL 未設定`＝env 沒進服務（回 4.3）；
`graph 呼叫失敗`＝server 沒起或 port 不對（回 8.3 journal）。

**8.6 已知邊界**：目前 env 只有一個 `GRAPH_TOOLS_URL`＝先服務單一主力
repo；要擴多 repo 就每 repo 複製一份 unit（各自 `--repo` 和 port），
orchestrator 端 per-repo 選 URL 屬之後的小改。另外老提醒不變：先往
golden set 加 2–3 條跨檔案例，再跑 `GRAPH_TOOLS_URL` 有／無兩輪 eval，
graph 的貢獻才量得出來（離線 eval 本身查不到 graph，屬預期）。

---

## Phase 9：收尾

1. CI gate 上線姿勢：`prs EVAL_MIN_RECALL=0.7 EVAL_MAX_NOISE=0.2 EVAL_MAX_FORBIDDEN=0 REVIEW_PIPELINE=v2 venv/bin/python -m eval.run_eval ci-$(date +%m%d-%H%M)`（exit 0 過／1 gate 擋／2 preflight 擋）。
2. 從今天起的迭代紀律：一輪只改一件事（prompt 版 / 旋鈕 / 規則），
   run metadata 自動記配置；「改善」要超過 6.1 記下的雜訊底才算贏。
3. 收編習慣照舊：漏抓/誤報必收、乾淨 PR 當負樣本、攢一批一起加、
   加完重跑一輪當新基線。

---

## Troubleshooting（v2 專屬，接在原 README 血淚之後）

1. **服務裡 static_findings 恆為 0，stats 有「semgrep：未啟用或未安裝」**
   → unit 的 PATH override 沒生效。`/proc/<pid>/environ` 看 PATH 是否以
   venv/bin 開頭；沒有 → `systemctl edit` 的 override 存對地方了嗎、
   daemon-reload + restart 了嗎。
2. **plan 每次都 fallback／adversary 常態 degraded** → gateway 對「只輸出
   JSON」的服從度差。看該 span 的 generation 原始輸出；解法順序：
   Langfuse UI 收緊 planner/adversary prompt → 換模型（LLM_MODEL）→
   接受降級（fallback 四科仍優於 v1）。
3. **production trace `base_available=false`** → `REPOS_ROOT` 沒進服務 env，
   或 `/opt/repos/` 目錄名 ≠ PR URL repo 段的小寫。用 3.1 的指令對名字。
4. **repo-sync journal 出現 pull 失敗** → svc-review 的 git 憑證/CA 問題，
   手動重跑 3.4 那條指令看真錯誤。
5. **`run_eval` exit 2** → preflight 列了兇手 item，去 UI 整形；
   **exit 1** → 三道 gate 之一沒過，輸出最後一行寫明是哪道。
6. **v2 比 v1 慢很多** → 正常（1 次呼叫變 4–6 次）。聊天體驗靠串流旁白
   撐；嫌太慢先降 `REVIEW_MAX_DIMENSIONS=3`、確認 `REVIEW_MAX_PARALLEL`
   沒被 gateway 限流卡住（journal 看 429/timeout）。
7. **reader 起不來** → reader.env 改壞（CRLF/尾巴空白）或 REPOS_ROOT
   打錯字；`journalctl -u pr-review-reader -n 30` 一定有一行說人話。

---

## Phase 10 — 知識迴圈（K 段，選配）

**三元組**：reviewer 對 PR 的理解（distiller 輸入）＋ user 回饋（Author note）＋
PR 終態（落地裁決）。新增服務數 0。

### 10.1 初始化 knowledge repo（本地）

```bash
mkdir -p <KNOWLEDGE_ROOT> && cd <KNOWLEDGE_ROOT> && git init
git config user.email bot@local && git config user.name knowledge-bot
# 手寫 3–5 頁種子（照 deploy/knowledge/TEMPLATE.md；模組級放 _module/）
git add -A && git commit -m "seed"
```

### 10.2 env 與 prompt

```bash
# orchestrator.env：
KNOWLEDGE_ROOT=<路徑>
KNOWLEDGE_BRANCHES=release/*,master
# 驗收 PR 的長相（source → target）——符合者不蒸餾、作為週期收口儀式
KNOWLEDGE_CYCLE_SOURCE=release/*
KNOWLEDGE_CYCLE_TARGET=master
# stale 重蒸（draft 之後又有 push → 用最終 diff 重蒸一次）；false = 直接丟稿
KNOWLEDGE_REDISTILL=true
# 重蒸端點的鑰匙與位址（harvester 讀同一份 env）——用重蒸就把 key 設起來，
# /knowledge/redistill 有 LLM 花費與寫檔語意，不是純讀
ORCH_API_KEY=<一組隨機字串>
ORCH_URL=http://127.0.0.1:8080
# results.env（試點開關，先 false = 影子模式）：
KNOWLEDGE_POST=false
```

`restart review-orchestrator pr-review-results` → **re-seed**（`pr-review-distiller`
與 `pr-review-reviewer` 兩份 prompt 都要上 Langfuse；後者帶 `knowledge_ref` 合約）。

⚠ 設了 `ORCH_API_KEY` 之後，聊天室（Open WebUI）與腳本呼叫 `/review`
都要帶 `Authorization: Bearer <key>`；`/eval-webhook` 刻意不驗（見 README）。

### 10.3 harvest timer

```bash
sudo cp deploy/systemd/pr-review-knowledge-harvest.service.example \
     /etc/systemd/system/pr-review-knowledge-harvest.service
sudo cp deploy/systemd/pr-review-knowledge-harvest.timer.example \
     /etc/systemd/system/pr-review-knowledge-harvest.timer
# 改 <你的user> / <安裝根> 佔位符
sudo systemctl daemon-reload && sudo systemctl enable --now pr-review-knowledge-harvest.timer
```

### 10.4 驗證

review 一條會碰到種子頁實體的 PR → Coverage 表出現
`| Knowledge context | N entr… matched |`（預算不足時同一格會標
`M invariants only, K omitted`）；有 finding 引到條目時再多一句
`N finding(s) cite a ledger entry`。手動跑一次
`prs venv/bin/python -m orchestrator.knowledge_harvest` 看 journal 五路分類。
PAT 權限對帳：拿 results.env 的 PAT GET 一次 threads，通了即結案。

新蒸的稿要能自我描述（`_pending/<repo>/pr-<id>.json`）：
`evidence_verified: true`、`distill_dropped: []`（有值代表有 decision 因為
引不出證據被丟掉，草稿留言 footer 會寫出來）、`distill_slices_skipped: []`
（有值代表 diff 大到超過 `DISTILL_DIFF_MAX_CHARS`）。

### 10.4b stale 重蒸的驗證（Phase 5）

```bash
# 端點活著（設了 ORCH_API_KEY 就要帶）
curl -X POST localhost:8080/knowledge/redistill \
     -H 'Authorization: Bearer <key>' -H 'Content-Type: application/json' \
     -d '{"pr_url":"https://<...>/_git/<repo>/pullrequest/<id>"}'
# → 202 + {"ok": true}；蒸餾在背景跑，看 journalctl -u review-orchestrator
```

真實動線：小 PR 貼了草稿之後再 push 一次、然後 complete →
下一輪 timer 的 journal 會出現「請求重蒸（第 1/2 次，commit xxxxxxxx）」→
orchestrator 那邊「重蒸完成 → _pending 覆寫」→ 再下一輪「staged
（silent·redistilled）」。作者先前的回覆逐字帶進重蒸材料，更新稿**回覆進原
thread**（不開新 thread），開頭有 `Draft updated after new commits`。

失敗模式一律有界：POST 打不通或背景蒸餾失敗 → 下一輪再試一次，兩次都不成
就 stale 丟稿（journal 寫「重蒸兩次未成」）；重蒸期間又有新 push → 記世代變更
後丟稿。任何一條 PR 的重蒸 LLM 支出 ≤ 2 次。

### 10.4c 量尺（Phase 2 的兩個數）

```bash
prs venv/bin/python -m eval.run_eval knowledge-baseline
# 輸出末段：
#   knowledge_recall 有知識 0.60（5 題）｜對照組 0.20（5 題）｜**knowledge_delta +0.40**
prs venv/bin/python -m eval.run_distill_eval distill-baseline   # 蒸餾考卷
```

線上那個數在 trace metadata：`knowledge_grounded` / `knowledge_ids_injected`
（引用率 = 上界，只證明 reviewer 讀到了）。離線那個是 `knowledge_delta`
（下界）。**放行條件**：連續兩輪 delta > 0，或單輪 ≥ 2/5——一次僥倖命中不算。
delta = 0 的診斷順序：① 連單頁題都 0 → consumption 問題，先迭代 reviewer
prompt（Knowledge 區塊的位置與措辭）；② 只有 distractor 題 0 → 排序/預算問題；
③ 兩者都修完仍為 0 → K 段進維護模式，把時間還給主線 review。

### 10.5 節奏（週期驗收制）

小 PR 進 release：蒸餾＋貼稿（不催核准）→ completed 後暫存 `_cycle/`
（reject / abandoned 當場淘汰；stale 走重蒸補救，兩次不成才淘汰；作者回覆
逐字入 Author note）。暫存稿同週期即可被後續 review 注入（標 staged）。

release → master 驗收 PR：active 時 harvester 貼一則 **cycle digest**
（驗收窗）；之後每有新暫存稿進池就補一則 **delta**（只列新進）。任何人可回
`reject <EntityName>`（整條）、`reject PR12`（整條、不撞名）、`reject PR12-I1`
（只丟那一行）→ completed 時批次落地（provenance `cycle/explicit|silent`，從未
出現在 digest 的條目帶 `+unlisted`）＋ `_cycles/<日期>-PR<id>.md` 週期收據。
驗收 PR 本身豁免蒸餾；abandoned 則暫存保留等下一輪。

digest 開頭的「🔎 Needs a look」是 LLM 顧問段（`KNOWLEDGE_DIGEST_LLM`，prompt
`pr-review-digest`）：整週期一起看、標矛盾 / 近重複 / 該註銷沒點名、建議
`reject <ID>`。它只是提案——落地不讀它，關掉或失敗都退回純清單。

影子試點：`KNOWLEDGE_POST=false` 時小 PR 不被打擾（稿乾跑），
digest 由 harvester 直發不受此開關管——第一個對外可見物就是收口那一則。

**首見水位（收口路徑）**：「哪些驗收 PR 已經結算過」只記在
`_state/cycle.json`，而它住在 `KNOWLEDGE_ROOT` 底下。帳本重新 clone、`_state`
掉了、或測試時清空 KB，這份記憶就沒了，但 ADO 的歷史一條都沒少——收割器跟
ADO 要的是「最近 100 條 completed PR」，沒有時間條件。因此**首次看到某個
repo 時，當下已經 completed 的驗收 PR 一律標為已收口、不回填落地**，journal
會印「首見，N 條歷史驗收 PR 標為已收口」。暫存稿留在池裡，等下一條**真正
完成**的驗收 PR 才收。這與 watermark 掃描是同一套規則。

代價講明：如果你是在某個 release 週期進行到一半才啟用（或剛好在那條驗收 PR
完成之後才第一次跑），那一輪的收口會被吞掉，該批暫存稿要等下一條驗收 PR。
用 `_state/cycle.json` 手動把某條 PR 的 `closed` 拿掉，就能讓它下一輪照收。

### 10.6 記憶層維運節奏

**規則怎麼被拔掉**：三條路，全部都是「畫掉 + History 留行」，永不刪除。
① distiller 明示註銷（PR description 講明反轉 → `retires_id`）；
② **實作推翻**：review 報了違反（finding 帶 `knowledge_ref`）而 PR 照樣完成 →
digest 的「⚠ Invariants contradicted by merged code」紅區列出 → 收口時沉默即拔，
任何人回 `keep PR12-I1` 否決（旋鈕 `KNOWLEDGE_RETIRE_ON_CONTRADICTION=false`
可整段關掉）；③ 人工直接改 markdown（帳本就是 git repo，人寫的段落會前滾保留）。
已畫掉的行不再進 distiller 的目錄，也不再注入給 reviewer——只留在頁面上給人看血緣。

**Supersede 語意**：條目可宣告廢除既有規則（distiller 只在 PR 明示反轉時才填、
不推論），以 `retires_id`（例 `PR12-I1`）精確點名；沒有 ID 的舊行才用逐字複製。
digest 以「⚠ Rules being retired」獨立紅區呈現——註銷比新增風險高，
掃的人先看這段。落地時被廢頁**頂端蓋戳 + History 留行、內容保留**：
註銷是標記，不是蒸發；不同意照樣 `reject` 廢除方條目即可。

**月度 staleness 抽樣（10 分鐘）**：從 Langfuse traces 撈近月
`knowledge_injected` 名單 → 隨機抽 5 條實際被注入過的條目 → 人工判「還成立嗎」
→ 記衰減率。這是記憶層唯一的健康儀表板；連續兩月衰減率高再啟動
reconciliation 自動化（顯式 confirmed/superseded 裁決），空帳本期不建。

### 附錄 X — 確定性層單元測試

```bash
venv/bin/pip install pytest   # 一次性（dev 依賴，不進 requirements 核心）
venv/bin/python -m pytest -q tests/
```

純函式鎖（anatomy / 邊界比對 / 行號驗證 / 切片預算 / MISSING-TEST
跨語言 / K1 閘門矩陣 / 落地累積+未知段前滾 / reject 邊界與歧義 /
watermark 首見 / digest state merge / 快取 commit 語意 / meta 盾 /
條目 ID 冪等 / ID 註銷與文字去重 / 撞名 skip 回 2-tuple / digest 首次→delta→
+unlisted / reject 三粒度 / digest 驗證與渲染 / 分析降級 / atomic write
/ **evidence 驗證矩陣** / **蒸餾切片優先序** / **primary 錨點 fallback**
/ **注入 id 名單與 knowledge_ref 執法** / **tier 排序與分級降載**
/ **蒸餾考卷計分（含三題陷阱的判法）** / **重蒸請求戳與重試上限**
/ **重蒸覆寫與舊痕跡作廢** / **thread 回覆而非開新 thread**）。
全離線零 LLM；改任何確定性函式，先過這關再跑 eval。
PATCH_EOF

write "CHANGES-2026-08-25-knowledge-loop.md" <<'PATCH_EOF'
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
PATCH_EOF

write "orchestrator/anatomy.py" <<'PATCH_EOF'
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
PATCH_EOF

write "orchestrator/app.py" <<'PATCH_EOF'
"""review-orchestrator —— 唯一的大腦、唯一接 Langfuse 的服務。

檔位一（狀態旁白串流）：/v1/chat/completions 在 stream=true 時，
一邊執行 pipeline 一邊把進度旁白即時吐給聊天室（🔍 解析 → 📋 metadata →
📄 diff → 🤖 review），最後串出 markdown —— 使用者看直播，不再盯黑箱。
追蹤結構不變：handle_review 仍是 @observe 的最外層，旁白只是旁路。

啟動：uvicorn orchestrator.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from typing import AsyncIterator, Awaitable, Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langfuse import observe, propagate_attributes
from pydantic import BaseModel

from orchestrator import clients
from orchestrator.core import run_review
from orchestrator.models import ReviewResult
from orchestrator import knowledge, progress
from shared.azdo import find_pr_url

app = FastAPI(title="review-orchestrator")

MODEL_ID = "pr-reviewer"
HELP_TEXT = (
    "把 Azure DevOps 的 PR 連結貼給我（形如 https://.../_git/<repo>/pullrequest/<id>），"
    "我會抓 diff 做 code review。"
)

# ---- 元任務盾 + 去重快取（防 Open WebUI 的 title/tags/follow-up 偷跑 pipeline）----

_META_MARKERS = (
    "### task:", "generate a concise", "3-5 word title", "concise title",
    "generate 1-3 broad tags", "categorizing the main themes", "broad tags",
    "follow-up", "suggest 3", "emoji",
)

def _is_meta_task(body: dict, last_text: str) -> bool:
    """Open WebUI 的元任務（標題/標籤/追問生成）辨識：
    新版會帶 metadata.task 欄位；舊版靠模板字樣。命中就不進 review pipeline。"""
    task = ((body.get("metadata") or {}).get("task")
            or body.get("task") or "")
    if task:
        return True
    low = last_text.lower()
    return any(m in low for m in _META_MARKERS)


_RECENT: dict[str, tuple[float, str]] = {}   # pr_url -> (時間戳, markdown)
_DEDUPE_TTL = float(os.getenv("REVIEW_DEDUPE_TTL", "120"))

def _cache_get(pr_url: str, source_commit: str = "") -> str | None:
    hit = _RECENT.get(pr_url)
    if hit and (time.time() - hit[0]) < _DEDUPE_TTL:
        cached_commit = hit[2] if len(hit) > 2 else ""
        if source_commit and cached_commit and source_commit != cached_commit:
            return None                    # push 了新 commit → 舊 review 作廢
        return hit[1]
    return None

def _cache_put(pr_url: str, markdown: str, source_commit: str = "") -> None:
    _RECENT[pr_url] = (time.time(), markdown, source_commit)
    if len(_RECENT) > 200:   # 粗糙但夠用的清理
        cutoff = time.time() - _DEDUPE_TTL
        for k in [k for k, (t, *_rest) in _RECENT.items() if t < cutoff]:
            _RECENT.pop(k, None)

StatusCb = Optional[Callable[[str], Awaitable[None]]]

_BG_TASKS: set = set()          # F5：event loop 只持弱引用，強引用收在這


def _bg(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


_EVAL_BUSY = {"flag": False}    # eval-webhook 併發鎖（防連點疊考）


def _check_auth(authorization: str | None):
    """設定 ORCH_API_KEY 時才驗；沒設就開放（內網 POC 模式）。"""
    want = os.getenv("ORCH_API_KEY", "")
    if not want:
        return
    got = (authorization or "").removeprefix("Bearer ").strip()
    if got != want:
        raise HTTPException(status_code=401, detail="invalid api key")


@observe(name="pr-review")
async def handle_review(pr_url: str, user: str,
                        on_status: StatusCb = None) -> ReviewResult:
    """一條 PR review 的完整過程 —— trace 的最外層。

    on_status：旁白回呼（SSE 直播用）；不給就是原本的安靜模式（/review 用）。
    """
    async def note(msg: str):
        if on_status:
            await on_status(msg)

    token = progress.set_status_cb(on_status)  # v2 pipeline 各站旁白由此播出
    try:
        return await _handle_review_inner(pr_url, user, note)
    finally:
        progress.reset_status_cb(token)


async def _handle_review_inner(pr_url: str, user: str, note) -> ReviewResult:
    with propagate_attributes(session_id=pr_url, user_id=user, tags=["live"]):
        await note("Resolving pull request URL")
        metadata = await clients.fetch_pr_metadata(pr_url)
        title = (metadata or {}).get("title", "")[:50]
        await note(f"PR located — \"{title}\"; fetching diff")

        diff = await clients.fetch_pr_diff(pr_url)
        n_files = len(re.findall(r"^\+\+\+ b", diff, re.M))
        await note(f"Diff retrieved — {n_files} file(s), {len(diff)} characters; running review pipeline")

        result = await run_review(diff, metadata)

        if os.getenv("AUTO_POST", "false").lower() in {"true", "1", "yes"}:
            await clients.post_review_comment(pr_url, result.markdown)
            await note("Review comment posted to the PR (subject to the results service DRY_RUN setting)")

        # K 段：非同步蒸餾 + 貼稿 + _pending —— 不擋使用者、失敗只進 journal
        try:
            files = [r["path"] for r in (result.stats.get("file_table") or [])]
            # reviewer 看的是這份 diff、報告裡的編號是 F1..Fn（render 的順序）——
            # 蒸餾吃同一份，證據才驗得動；不另造 diff 表示、不重 parse markdown
            finding_ids = {f"F{i}" for i in range(1, len(result.findings) + 1)}
            finding_files = [f.file for f in result.findings if f.file]
            # 矛盾偵測（確定性）：報告裡「踩到既有不變式」的 finding 引了哪些 id。
            # 這批 id 之後會跟著稿走：PR 若帶著矛盾完成，代表實作推翻了規則，
            # 驗收窗要為它做一次裁決（見 knowledge_harvest 的 contradiction 段）。
            contradicted = {ref for f in result.findings
                            if f.severity in ("error", "warning")
                            for ref in (f.knowledge_ref or [])}
            _bg(knowledge.distill_and_stage(
                pr_url, metadata, files, result.markdown,
                list(result.stats.get("graph_impacted") or []),
                diff=diff, finding_ids=finding_ids,
                finding_files=finding_files, contradicted=contradicted))
        except Exception as e:
            print(f"[knowledge] 排程失敗：{e}")

        return result


# ---------- OpenAI 相容層（Open WebUI 用） ----------

@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return {"object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "owned_by": "pr-review-suite"}]}


def _delta(rid: str, **delta) -> str:
    payload = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk",
               "created": int(time.time()), "model": MODEL_ID,
               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finish(rid: str) -> str:
    payload = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk",
               "created": int(time.time()), "model": MODEL_ID,
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"


async def _stream_text(rid: str, text: str,
                       chunk: int = 800) -> AsyncIterator[str]:
    """靜態文字（help / 錯誤）也走 SSE。"""
    yield _delta(rid, role="assistant")
    for i in range(0, len(text), chunk):
        yield _delta(rid, content=text[i:i + chunk])
    yield _finish(rid)


async def _stream_review(rid: str, pr_url: str, user: str,
                         commit_hint: str = "") -> AsyncIterator[str]:
    """檔位一的心臟：queue 直播 —— pipeline 每過一站就吐一行旁白。"""
    q: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    async def on_status(msg: str):
        await q.put(("status", msg))

    async def runner():
        try:
            result = await handle_review(pr_url, user, on_status=on_status)
            _cache_put(pr_url, result.markdown, commit_hint)
            await q.put(("result", result.markdown))
        except Exception as e:  # 串流已開場，不能再回 500 —— 把錯誤演出來
            await q.put(("error",
                         f"❌ review 失敗：{type(e).__name__}: {e}\n"
                         f"（細節見伺服器 journalctl -u review-orchestrator）"))
            raise  # 讓 traceback 照常進 journal
        finally:
            await q.put(None)

    task = asyncio.create_task(runner())
    yield _delta(rid, role="assistant")
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            kind, text = item
            if kind == "status":
                yield _delta(rid, content=text + "\n\n")
            elif kind == "result":
                yield _delta(rid, content="---\n\n")
                for i in range(0, len(text), 800):
                    yield _delta(rid, content=text[i:i + 800])
            else:  # error
                yield _delta(rid, content=text)
    finally:
        task.cancel()  # client 斷線時別讓 runner 變孤兒
    yield _finish(rid)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_openwebui_user_email: str | None = Header(default=None),
    x_openwebui_user_name: str | None = Header(default=None),
):
    _check_auth(authorization)
    body = await request.json()
    user = x_openwebui_user_email or x_openwebui_user_name or "anonymous"

    last_user_msg = ""
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content", "")
            last_user_msg = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break

    pr_url = find_pr_url(last_user_msg)
    rid = uuid.uuid4().hex[:12]
    stream = bool(body.get("stream"))

    def _reply(text: str):
        """靜態文字回覆（盾牌與 help 共用），串流/非串流都吃。"""
        if stream:
            return StreamingResponse(_stream_text(rid, text),
                                     media_type="text/event-stream")
        return {
            "id": f"chatcmpl-{rid}", "object": "chat.completion",
            "created": int(time.time()), "model": MODEL_ID,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
        }

    # 盾一：元任務（title/tags/follow-up）給靜態短答，絕不進 pipeline
    if _is_meta_task(body, last_user_msg):
        return _reply(f"PR Review {pr_url.rsplit('/', 1)[-1]}" if pr_url else "PR Review")

    # 盾二：同一條 PR 在 TTL 內重複觸發 → 重播快取，不再燒一輪
    commit_hint = ""
    if pr_url:
        try:
            commit_hint = (await clients.fetch_pr_metadata(pr_url)) \
                .get("source_commit") or ""
        except Exception:
            pass                            # metadata 失敗 → 照舊 TTL 語意
    if pr_url and (cached := _cache_get(pr_url, commit_hint)) is not None:
        return _reply(cached)

    if not pr_url:
        return _reply(HELP_TEXT)

    if stream:
        return StreamingResponse(_stream_review(rid, pr_url, user,
                                                commit_hint),
                                 media_type="text/event-stream")

    result = await handle_review(pr_url, user)
    _cache_put(pr_url, result.markdown, commit_hint)
    return _reply(result.markdown)


# ---------- 腳本 / CI 用的乾淨介面 ----------

class ReviewRequest(BaseModel):
    pr_url: str
    user: str = "api"


@app.post("/review")
async def review(req: ReviewRequest,
                 authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    result = await handle_review(req.pr_url, req.user)
    return {"pr_url": req.pr_url,
            "markdown": result.markdown,
            "findings": result.findings_summary()}


class RedistillRequest(BaseModel):
    pr_url: str


@app.post("/knowledge/redistill", status_code=202)
async def knowledge_redistill(req: RedistillRequest,
                              authorization: str | None = Header(default=None)):
    """stale 補救：harvester 偵測到「draft 之後又有 push」時打這裡。

    harvester 只偵測，蒸餾這件事整個留在 orchestrator（`_land` 仍然零 LLM，
    不把半個 orchestrator 搬進收割器）。這條端點有 LLM 花費與寫檔語意，
    不是純讀——所以照 `_check_auth` 走；部署時務必把 ORCH_API_KEY 設起來。
    收到即回 202、背景執行；重試上限由 harvester 的請求戳守（≤ 2 次）。
    """
    _check_auth(authorization)
    if not knowledge.redistill_enabled():
        return {"ok": False, "pr_url": req.pr_url,
                "note": "KNOWLEDGE_REDISTILL=false —— 重蒸關閉"}
    _bg(knowledge.redistill(req.pr_url))
    return {"ok": True, "pr_url": req.pr_url,
            "note": "重蒸已在背景開跑；結果覆寫 _pending，看 journal"}


# ---------- Langfuse UI「Custom Experiment」按鈕的落點 ----------

@app.post("/eval-webhook")
async def eval_webhook(request: Request):
    """Langfuse Remote Experiment webhook：收到即回、背景開考。

    Langfuse 只等 10 秒，所以這裡用 subprocess 丟背景跑 run_eval，
    分數由 run_eval 自己回寫成新的 dataset run。注意：此端點刻意不走
    ORCH_API_KEY（Langfuse 觸發時無法帶自訂 Authorization），內網環境
    可接受；要更嚴就在 Langfuse 設定裡開 HMAC 簽章再於此驗證。
    """
    import sys
    from pathlib import Path

    try:
        body = await request.json()
    except Exception:
        body = {}
    cfg = {}
    for key in ("config", "payload"):
        if isinstance(body.get(key), dict):
            cfg.update(body[key])
    q = request.query_params
    # pipeline 可由 query（?pipeline=v2）或 body 指定；預設跟服務 env 走。
    # 在 Langfuse 存兩個 Remote Experiment 設定（v1 / v2 各一）= UI 一鍵 A/B。
    pipeline = (q.get("pipeline") or cfg.get("pipeline")
                or os.getenv("REVIEW_PIPELINE", "v1")).strip().lower()
    if pipeline not in ("v1", "v2"):
        pipeline = "v1"
    run_name = (q.get("run_name") or cfg.get("run_name")
                or f"ui-{pipeline}-{time.strftime('%m%d-%H%M%S')}")

    if _EVAL_BUSY["flag"]:
        raise HTTPException(status_code=409,
                            detail="上一輪 eval 還在跑（併發鎖）；等它收尾再觸發")
    _EVAL_BUSY["flag"] = True
    env = os.environ.copy()
    env["REVIEW_PIPELINE"] = pipeline
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "eval.run_eval", run_name,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )
    async def _reap():
        try:
            await proc.wait()
        finally:
            _EVAL_BUSY["flag"] = False
    _bg(_reap())                       # 收屍 + 釋放併發鎖（強引用防 GC）
    return {"ok": True, "run_name": run_name, "pipeline": pipeline,
            "note": "eval 已在背景開跑，稍後到 Datasets → Runs 看分數"}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
PATCH_EOF

write "orchestrator/clients.py" <<'PATCH_EOF'
"""Orchestrator → 三個 tool set 的呼叫層。

每個 wrapper 都疊 @observe：瀑布圖裡「抓 diff 花多久、讀 repo 花多久、
發評論成不成功」各自成層 —— 這是唯一接 Langfuse 的服務。

環境變數（都有合理預設）：
  ADO_TOOLS_URL      http://127.0.0.1:8091/mcp
  READER_TOOLS_URL   http://127.0.0.1:8092/mcp
  RESULT_TOOLS_URL   http://127.0.0.1:8093/mcp
"""
from __future__ import annotations

import json
import os

from fastmcp import Client
from langfuse import observe


def _url(env: str, default: str) -> str:
    return os.getenv(env, default)


async def _call(server_url: str, tool: str, args: dict):
    """呼叫遠端 MCP tool 並把結果攤平成 Python 物件。"""
    async with Client(server_url) as c:
        res = await c.call_tool(tool, args)
    # fastmcp 2.x：優先用結構化 data；否則取第一個 text content
    data = getattr(res, "data", None)
    if data is not None:
        return data
    content = getattr(res, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return None


# ---------- ① ado-pr-tools ----------

@observe()
async def fetch_pr_metadata(pr_url: str) -> dict:
    return await _call(_url("ADO_TOOLS_URL", "http://127.0.0.1:8091/mcp"),
                       "get_pr_metadata", {"pr_url": pr_url})


@observe()
async def fetch_pr_diff(pr_url: str) -> str:
    out = await _call(_url("ADO_TOOLS_URL", "http://127.0.0.1:8091/mcp"),
                      "get_pr_diff", {"pr_url": pr_url})
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


# ---------- ② repo-reader-tools（多 repo：repo=None 走單 repo 舊行為） ----------

@observe()
async def read_repo_file(path: str, start_line: int = 1,
                         end_line: int | None = None,
                         repo: str | None = None) -> str:
    return await _call(_url("READER_TOOLS_URL", "http://127.0.0.1:8092/mcp"),
                       "read_file",
                       {"path": path, "start_line": start_line,
                        "end_line": end_line, "repo": repo})


@observe()
async def search_repo(pattern: str, glob: str = "**/*", repo: str | None = None):
    return await _call(_url("READER_TOOLS_URL", "http://127.0.0.1:8092/mcp"),
                       "search_code", {"pattern": pattern, "glob": glob,
                                       "repo": repo})


# ---------- ③ review-result-tools ----------

@observe()
async def post_review_comment(pr_url: str, markdown: str) -> dict:
    return await _call(_url("RESULT_TOOLS_URL", "http://127.0.0.1:8093/mcp"),
                       "post_review_comment", {"pr_url": pr_url, "markdown": markdown})

@observe()
async def post_knowledge_draft(pr_url: str, markdown: str,
                               thread_id: int | None = None) -> dict:
    """thread_id 給定 = 回覆進既有 thread（重蒸的更新稿），不開新 thread。

    thread_id 為 None 時**不送這個欄位**：跨服務的版本偏移是常態（orchestrator
    先更新、③ 晚一步 restart），少送一個參數就讓舊版 ③ 照常收稿。這不是潔癖——
    ③ 若因未知參數報錯，harvester 會把它當成「真的貼稿失敗」而代貼，那條路徑
    不看 KNOWLEDGE_POST，影子期會因此誤發。
    """
    args = {"pr_url": pr_url, "markdown": markdown}
    if thread_id is not None:
        args["thread_id"] = thread_id
    return await _call(_url("RESULT_TOOLS_URL", "http://127.0.0.1:8093/mcp"),
                       "post_knowledge_draft", args)


# ---------- ④ code graph（選配；code-review-graph 的 MCP server） ----------
# 裝好後先 list tools 確認名稱與參數，再設 GRAPH_TOOL_BLAST /
# GRAPH_TOOL_BLAST_ARGS（JSON 樣板，$files / $repo 會被代入）。
# GRAPH_TOOLS_URL 未設 = 整個 graph 功能停用，呼叫端全部優雅降級。


@observe()
async def graph_call(tool: str, args: dict):
    url = os.getenv("GRAPH_TOOLS_URL", "").strip()
    if not url:
        return None
    return await _call(url, tool, args)


async def graph_blast(files: list[str], repo: str | None = None):
    """受影響範圍摘要。工具名/參數樣板由 env 決定（不同 graph 工具可換皮）。"""
    tool = os.getenv("GRAPH_TOOL_BLAST", "").strip()
    if not tool:
        return None
    template = os.getenv("GRAPH_TOOL_BLAST_ARGS", '{"files": $files}')
    if "$repo_dir" in template and not (repo or "").strip():
        return None                      # 樣板要 repo、呼叫端沒給 → 靜默跳過
    rendered = (template
                .replace("$files", json.dumps(files))
                .replace("$repo_dir", (repo or "").strip().lower())
                .replace("$repo", json.dumps((repo or "").strip().lower())))
    try:
        args = json.loads(rendered)
    except json.JSONDecodeError:
        return None
    return await graph_call(tool, args)
PATCH_EOF

write "orchestrator/context.py" <<'PATCH_EOF'
"""context（v2 的餵料工程）—— 確定性預抓，不做 agentic tool loop。

刻意的設計決策：reviewer 不拿工具自己逛，而是由 code 先把上下文備好餵進去。
理由：(1) 內部 gateway 的 native tool calling 支援未知（README 已記載這是
LLM-as-judge 的阻礙），確定性預抓不吃這個能力；(2) 可重現 —— 同一份 diff
永遠拿到同一份上下文；(3) 離線 eval 優雅降級（沒有 clone/graph 就少這塊，
不炸）。之後要升級成 agentic loop，再把 ②④ 接給模型即可，介面都備著。

兩個來源：
  A. 本地乾淨 clone（target branch / base 側）：REPOS_ROOT/<repo小寫>/
     —— 取每個 hunk 舊行號附近 ±N 行。分工原則：改了什麼看 diff，
     周邊長什麼樣看 base。
  B. code graph（選配）：GRAPH_TOOLS_URL 設定後，用 clients.graph_blast
     拿受影響範圍摘要。工具名/參數樣板用 env 設定（裝好後 list tools 再填）。

環境變數：REPOS_ROOT、CONTEXT_LINES（±行數，預設 6）、CONTEXT_MAX_CHARS（24000）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from orchestrator.anatomy import Anatomy, FileChange


def _repo_root(repo: str | None) -> Path | None:
    base = os.getenv("REPOS_ROOT", "").strip()
    if not base or not repo:
        return None
    root = (Path(base) / repo.strip().lower()).resolve()
    return root if root.is_dir() else None


def _safe(root: Path, rel: str) -> Path | None:
    p = (root / rel.lstrip("/")).resolve()
    return p if (root == p or root in p.parents) else None


def _excerpt(root: Path, fc: FileChange, ctx: int) -> str | None:
    p = _safe(root, fc.path)
    if p is None or not p.is_file():
        return None
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return None
    wanted: set[int] = set()
    for h in fc.hunks:
        lo = max(1, h.old_start - ctx)
        hi = min(len(lines), h.old_start + max(h.old_count, 1) + ctx)
        wanted.update(range(lo, hi + 1))
    if not wanted:
        return None
    out: list[str] = [f"### base（target branch）{fc.path}"]
    prev = None
    for i in sorted(wanted):
        if prev is not None and i != prev + 1:
            out.append("      …")
        out.append(f"{i:>5}  {lines[i - 1]}")
        prev = i
    return "\n".join(out)


def base_context(repo: str | None, ana: Anatomy) -> tuple[str, dict]:
    """回傳 (組好的上下文文字, 統計)。clone 不在 = 空字串 + 註記，不炸。"""
    root = _repo_root(repo)
    stats = {"base_files": 0, "base_available": root is not None}
    if root is None:
        return "", stats
    ctx = int(os.getenv("CONTEXT_LINES", "6"))
    budget = int(os.getenv("CONTEXT_MAX_CHARS", "24000"))
    parts: list[str] = []
    total = 0
    for fc in ana.files:
        ex = _excerpt(root, fc, ctx)
        if not ex:
            continue
        if total + len(ex) > budget:
            parts.append("（base 上下文達預算上限，其餘省略）")
            break
        parts.append(ex)
        total += len(ex)
        stats["base_files"] += 1
    return "\n\n".join(parts), stats


async def graph_context(repo: str | None, ana: Anatomy) -> tuple[str, dict]:
    """選配：code graph 的 blast radius 摘要。任何失敗 = 空字串 + 註記。"""
    stats = {"graph_used": False, "graph_note": ""}
    if not os.getenv("GRAPH_TOOLS_URL", "").strip():
        stats["graph_note"] = "GRAPH_TOOLS_URL 未設定"
        return "", stats
    bound = os.getenv("GRAPH_REPO", "").strip().lower()
    if bound and (repo or "").strip().lower() != bound:
        # graph server 一次只服務一個 repo；別的 repo 寧可沒有 graph，
        # 也不能拿錯的圖出「無跨檔影響」的假證詞
        stats["graph_note"] = f"graph 綁定 repo '{bound}'，本 repo 未附掛"
        return "", stats
    try:
        from orchestrator import clients
        files = [fc.path.lstrip("/") for fc in ana.files]
        out = await clients.graph_blast(files=files, repo=repo)
    except Exception as e:  # graph 是加分項，不是依賴
        stats["graph_note"] = f"graph 呼叫失敗：{type(e).__name__}"
        return "", stats
    if not out:
        stats["graph_note"] = "graph 無回應或工具名未設定"
        return "", stats
    impacted: list[str] = []
    if isinstance(out, dict):        # 例如 get_impact_radius_tool 的回傳
        summary = str(out.get("summary") or "").strip()
        for key in ("impacted_files", "additional_files", "affected_files",
                    "impacted_nodes", "affected_nodes"):
            for item in out.get(key) or []:
                name = (item.get("qualified_name") or item.get("name")
                        or item.get("file_path")
                        ) if isinstance(item, dict) else str(item)
                if name:
                    impacted.append(str(name))
        detail = json.dumps(out, ensure_ascii=False, default=str)
        text = (summary + "\n" + detail) if summary else detail
        if not summary:
            summary = detail
    else:
        text = out if isinstance(out, str) else str(out)
        summary = text.strip()
    budget = int(os.getenv("CONTEXT_MAX_CHARS", "24000"))
    stats["graph_used"] = True
    stats["graph_summary"] = summary           # 報告：血脈摘要
    stats["graph_impacted"] = list(dict.fromkeys(impacted))[:12]  # 報告：受波及清單
    return "### code graph（受影響範圍）\n" + text[:budget], stats
PATCH_EOF

write "orchestrator/knowledge.py" <<'PATCH_EOF'
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

env：
  KNOWLEDGE_ROOT        本地 knowledge repo 路徑；未設 = 整個 K 段停用（全部靜默）
  KNOWLEDGE_BRANCHES    逗號分隔、支援萬用字元（例：master,release/*）；預設 master,main
  KNOWLEDGE_MAX_CHARS   注入區塊總上限（預設 18000）
  DISTILL_DIFF_MAX_CHARS 蒸餾看到的 diff 切片預算（預設 100000）
  KNOWLEDGE_REDISTILL   stale 重蒸開關（預設 true；false = 回到 stale 丟稿）
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


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


def _flag_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw not in {"false", "0", "no"}


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
    budget = _int_env("DISTILL_DIFF_MAX_CHARS", 100000)
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
    r = os.getenv("KNOWLEDGE_ROOT", "").strip()
    return Path(r) if r else None


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
    cap_total = _int_env("KNOWLEDGE_MAX_CHARS", 18000)
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
    raw = os.getenv("KNOWLEDGE_BRANCHES", "master,main")
    return [b.strip() for b in raw.split(",") if b.strip()]


def gate(meta: dict, changed_paths: list[str]) -> tuple[bool, str]:
    """(要不要蒸餾, 原因)。原因進 journal，不進使用者面。"""
    if _root() is None:
        return False, "KNOWLEDGE_ROOT 未設定"
    target = (meta.get("target_branch") or "").replace("refs/heads/", "").strip()
    if not target or not any(fnmatch.fnmatch(target, pat)
                             for pat in _branch_patterns()):
        return False, f"目標分支 {target or '?'} 不在 KNOWLEDGE_BRANCHES"
    src = (meta.get("source_branch") or "").replace("refs/heads/", "").strip()
    cyc_src = os.getenv("KNOWLEDGE_CYCLE_SOURCE", "release/*").strip()
    cyc_tgt = os.getenv("KNOWLEDGE_CYCLE_TARGET", "master").strip()
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
    return _flag_env("KNOWLEDGE_REDISTILL", True)


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
PATCH_EOF

write "orchestrator/knowledge_harvest.py" <<'PATCH_EOF'
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
PATCH_EOF

write "orchestrator/models.py" <<'PATCH_EOF'
"""輸出合約（雙軌）：markdown 給人看，findings 給機器評分。從第一天就定死。

v2 起 Finding 多兩個欄位（都有預設值，向後相容）：
  source      "llm" | "static" | "graph" —— 誰產的（eval 可 per-source 切片）
  confidence  0.0–1.0 —— static 恆為 1.0；LLM 從基礎值起跳，被 adversary
              確認加權、推翻打折；synthesis 依門檻過濾
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Finding(BaseModel):
    rule: str = Field(description="規則代號，例如 BUG / MISSING-TEST / NAMING / SECURITY / STYLE / PERF")
    file: str = ""
    line: int | None = None
    severity: str = "info"  # info | warning | error
    note: str = ""
    # 依據的團隊規則檔路徑（例如 rules/common/naming.md）；一般發現留空。
    rule_ref: str = ""
    # 依據的知識條目 id（例如 ["PR12-I1"]）。清單而非單值是刻意的：一條 finding
    # 可以同時站在兩條不變式上。只有「本次注入區塊裡真的出現過」的 id 留得下來
    # （synthesis 確定性驗證），所以這個欄位是引用率的分子，不是模型的自由欄。
    knowledge_ref: list[str] = Field(default_factory=list)
    # 具體修法建議（一兩句，可含短碼）；報告會以「↳ 建議：」呈現，選填
    suggestion: str = ""
    source: str = "llm"      # llm | static | graph
    # 產出這條 finding 的審查面向名（報告的 Dimensions 表交叉引用用；static 留空）
    dimension: str = ""
    confidence: float = 0.7


class ReviewResult(BaseModel):
    markdown: str
    findings: list[Finding] = []
    stats: dict = {}         # pipeline 各階段的統計（進 trace metadata / footer）

    def findings_summary(self) -> list[dict]:
        return [f.model_dump() for f in self.findings]
PATCH_EOF

write "orchestrator/pipeline.py" <<'PATCH_EOF'
"""v2 pipeline —— plan → coverage gate → parallel reviewers → adversary → synthesis。

分工鐵律（PR-AF 的核心洞見）：能確定性計算的絕不給 LLM。
  anatomy / static / coverage gate / dedupe / 乘數 / 門檻 / render = code
  規劃面向與假說 / 找問題 / 挑戰問題                              = LLM
每個 LLM 階段都是「JSON + 驗證 + fallback」：任何故障是降級、不是炸掉。

本版三個升級（U1–U3）：
  U1 厚 brief：planner 對每個面向產「待驗證假說 + 指派規則」，reviewer 收到
     的是任務書（假說逐條驗、只帶自己該管的規則內容 = targeted context）。
  U2 覆蓋保證（純 code）：plan 後檢查「每個變更檔 ≥ 一個面向」「每條 md 規則
     ≥ 一個面向」，孤兒自動補一個 Coverage Sweep 面向；stats["coverage"] 記帳。
  U3 adversary 精緻化：分塊審（≤ ADVERSARY_CHUNK 條/次，防注意力稀釋）、判決
     必附行號證據與挑戰編號、可用 LLM_MODEL_ADVERSARY 換一顆腦去自我同意偏差。

旋鈕（全進 env、全進 trace metadata）：
  REVIEW_MAX_DIMENSIONS=8    planner 最多開幾科（sweep 可額外 +1，保證優先於上限）
  REVIEW_MAX_PARALLEL=6      reviewer 併發
  REVIEW_LLM_BASE_CONF=0.7   LLM finding 的基礎信心
  REVIEW_MIN_CONFIDENCE=0.55 低於此分的 finding 被過濾
  REVIEW_MAX_FINDINGS=30     輸出上限
  REVIEW_ADVERSARY=true      關掉 = 跳過對抗段（A/B 用）
  ADVERSARY_CHUNK=6          adversary 每次呼叫最多審幾條
  LLM_MODEL_ADVERSARY        （選配）adversary 專用模型；未設 = 同 LLM_MODEL
  REVIEWER_RULES_MAX_CHARS=20000  每個 reviewer 收到的規則文字上限
  REVIEWER_SLICES_MAX_CHARS=30000 每一科的切片總預算（超過的檔降級 static-only）
  REVIEWER_FILE_SLICE_MAX_CHARS=6000 單一檔案的切片上限（超過就檔內截斷）
"""
from __future__ import annotations

import asyncio
import os

from langfuse import observe
from pydantic import BaseModel

from orchestrator import context as ctx
from orchestrator import knowledge
from orchestrator.anatomy import (Anatomy, line_in_view, line_near,
                                 match_path, numbered_slice, parse_diff)
from orchestrator.llm import complete, extract_json, prompt_or
from orchestrator.models import Finding, ReviewResult
from orchestrator.progress import note
from orchestrator.render import render_markdown, sort_key
from orchestrator.rules import RulesBundle
from orchestrator.static_pass import run_static

# ---------- 受版控 prompts（Langfuse 優先，內建為後備；seed_langfuse 會推上去） ----------

PLANNER_PROMPT_NAME = "pr-review-planner"
REVIEWER_PROMPT_NAME = "pr-review-reviewer"
ADVERSARY_PROMPT_NAME = "pr-review-adversary"

DEFAULT_PLANNER_PROMPT = """You are the review planner of an automated pull-request review system. Your output is the investigation brief that individual reviewers will execute, so be specific: a vague plan produces a vague review.

Given the structural summary of a pull request, decide which review dimensions this specific PR warrants. Derive the dimensions from the actual risk profile of the change — for example: database access warrants injection and transactional-integrity scrutiny; public API changes warrant input-validation and backward-compatibility scrutiny; pure refactors warrant behavior-preservation and consistency scrutiny. Do not apply a generic checklist.

Requirements:
- Define 2 to {max_dims} dimensions.
- Taken together, the dimensions must give every category an owner: behavioral correctness (BUG), security (SECURITY), test adequacy (MISSING-TEST), naming/readability (NAMING/STYLE), and performance/resource efficiency (PERF). Fold these lenses into related dimensions or add one — do not leave a category unowned.
- For each dimension provide:
  - "focus": precisely what to investigate and why it matters for this PR.
  - "hypotheses": 2 to 4 concrete, testable suspicions about what could be wrong in THIS change (e.g. "deleteByName builds SQL from the raw name argument", "the new expiry check may treat the boundary instant incorrectly"). Reviewers will verify each one.
  - "files": target files, using the paths that appear in the diff.
  - "rules": the team-rule file paths (from the provided rule index) this dimension is responsible for checking. Every rule plausibly relevant to this change must be assigned to at least one dimension.

Output JSON only — no prose, no code fences:
{"summary": "One or two sentences describing what this PR does, for the report header.",
 "dimensions": [{"name": "Dimension name", "focus": "...", "hypotheses": ["...", "..."], "files": ["/src/x.py"], "rules": ["rules/common/x.md"]}]}"""

DEFAULT_REVIEWER_PROMPT = """You are a senior software engineer performing a focused code review. You are responsible for exactly one review dimension and you have been handed an investigation brief; execute it exhaustively and ignore concerns outside it.

Ground rules:
- Work through every listed hypothesis explicitly. Your "assessment" must state the outcome of each hypothesis (confirmed as a finding, or refuted and why). Hypotheses are leads, not limits — also report any defect within your dimension that no hypothesis anticipated.
- Review only the changed lines shown in the annotated diff. Base-branch context is provided solely for understanding; do not raise issues about pre-existing code.
- Report the line number shown in the left-hand annotation of the diff slice (these are true new-file line numbers).
- Use uppercase category codes: BUG / SECURITY / MISSING-TEST / NAMING / STYLE / PERF.
- Severity calibration: "error" = would cause incorrect behavior, data loss, or a security exposure — merging should be blocked; "warning" = a real defect or risk that should be addressed but need not block the merge; "info" = advisory.
- MISSING-TEST is decided within the diff: if this diff adds or changes behavior (new or modified methods, endpoints, or logic) and no test file is touched in the same diff, report MISSING-TEST (severity: warning). Attach it to the changed file carrying the main new behavior; one finding is enough — do not repeat it per file. Do not require proof that no tests exist elsewhere. Pure refactors, renames, or formatting-only changes with no behavior change do not warrant it.
- Category boundaries: NAMING is for identifiers whose name misstates their type, polarity, or content (e.g. a Map named accountList, a double-negative boolean method); STYLE is for formatting and layout only.
- Be precise and factual. Every finding must be supported by the code shown. If you are not confident a finding is real, omit it — false positives erode trust in the review.
- Where practical, include a concrete remediation in "suggestion" (one or two sentences; short code is acceptable).
- Team rules: the rules assigned to this dimension are attached. For each assigned rule, take an explicit position. If the change violates it, raise a finding referencing it and add a "violation" entry to rule_checks. If you verified compliance, add a "pass" entry with one sentence of evidence (what you checked and why it is satisfied).
- Team knowledge: a "Knowledge" block may be attached, carrying decisions and invariants distilled from earlier pull requests. Each line ends with an id such as `PR12-I1`. If a finding rests on one of those invariants, list that id in "knowledge_ref" (a list — a finding may stand on two). Cite only ids that appear in the attached block; an id that is not there is dropped. Knowledge entries are leads, not law: verify each against the diff before you rely on it.
- Write all output in clear, professional English.

Output a single JSON object only. The first character of your reply must be "{" and the last must be "}". Never output a bare array. When there are no findings, output "findings": [] and still provide "assessment".
{"assessment": "Hypothesis-by-hypothesis outcome and overall conclusion", "findings": [{"rule": "BUG", "file": "/src/x.py", "line": 12, "severity": "error|warning|info", "note": "The defect and its consequence", "suggestion": "Concrete fix", "rule_ref": "", "knowledge_ref": []}], "rule_checks": [{"rule_ref": "rules/common/x.md", "verdict": "pass|violation", "evidence": "One sentence of evidence"}]}"""

DEFAULT_ADVERSARY_PROMPT = """You are the adversarial validator of a code-review pipeline. Other reviewers proposed the findings below against the annotated diff. Attack every finding and keep only what survives scrutiny.

Challenge each finding on:
1. Pre-existence — is the issue visible only in base context rather than in the added lines? If so, it is out of scope.
2. Convention — is this an established project pattern rather than a defect?
3. Severity inflation — does the stated severity match the actual consequence?
4. Substantiation — does the cited code actually support the claim?

Verdicts: "confirmed" only when the finding is clearly real and correctly scoped; "rejected" when it fails any challenge above; otherwise "uncertain". Be strict — confirming a false positive is worse than marking it uncertain — but every "rejected" must cite the specific annotated line your reason relies on and name which challenge (1-4) it failed; if you cannot point to a line, use "uncertain" instead.

Output a JSON array only — no prose. "id" must match the input numbering.
[{"id": 1, "verdict": "confirmed|uncertain|rejected", "challenge": 2, "line": 15, "reason": "One sentence grounded in the cited line"}]"""


class _Dim(BaseModel):
    name: str
    focus: str = ""
    hypotheses: list[str] = []
    files: list[str] = []
    rules: list[str] = []


def _int(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, str(default)))
    except ValueError:
        return default


def _float(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except ValueError:
        return default


def _norm(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


# ---------- U1：targeted rules —— 只給每個 reviewer 它該管的規則 ----------

def _rules_for_dim(rules: RulesBundle, dim: _Dim) -> str:
    if not rules.loaded:
        return ""
    cap = _int("REVIEWER_RULES_MAX_CHARS", 20000)
    picked: list[tuple[str, str]] = []
    for want in dim.rules:
        for rel, body in rules.sections.items():
            if match_path(rel, want):
                picked.append((rel, body))
                break
    if picked:
        body = "\n\n".join(f"## {rel}\n{b}" for rel, b in picked)[:cap]
        head = (f"\n\n# Assigned Team Rules (version {rules.sha}; this dimension "
                "is responsible for these — take an explicit pass/violation "
                "position on each in rule_checks; reference the file path in "
                "rule_ref for rule-based findings)\n\n")
    else:  # planner 沒指派（或 fallback）→ 全量，行為同前版
        body = rules.text[:cap]
        head = (f"\n\n# Team Rules (version {rules.sha}; repo-specific overrides "
                "common on conflict; reference the rule file path in rule_ref, "
                "and record pass/violation positions in rule_checks for rules "
                "relevant to your dimension)\n\n")
    return head + body + ("\n(Note: rules truncated at the length limit)"
                          if len(body) >= cap else "")


# ---------- 階段 2：plan（U1 厚 brief） ----------

@observe(name="plan")
async def _plan(ana: Anatomy, meta: dict, rules: RulesBundle,
                graph_text: str, stats: dict) -> tuple[list[_Dim], str]:
    max_dims = _int("REVIEW_MAX_DIMENSIONS", 8)
    files = [fc.path for fc in ana.files]
    fallback = [
        _Dim(name="Correctness",
             focus="Logic errors, boundary conditions, behavior contradicting intent (BUG)",
             hypotheses=["A changed branch or boundary condition contradicts the stated intent",
                         "A changed expression produces a wrong value on edge inputs"],
             files=files),
        _Dim(name="Security",
             focus="Injection, credential exposure, unsafe defaults (SECURITY)",
             hypotheses=["Changed code builds a query or command from unvalidated input",
                         "Changed code embeds or weakens a credential or trust setting"],
             files=files),
        _Dim(name="Test Coverage",
             focus="New or changed behavior lacking corresponding tests (MISSING-TEST)",
             hypotheses=["This diff adds or changes behavior without touching any test file"],
             files=files),
        _Dim(name="Readability",
             focus="Misleading names, inconsistent style (NAMING/STYLE)",
             hypotheses=["A new identifier misstates its type, polarity, or content"],
             files=files),
    ][:max_dims]

    summary = [f"{ana.stats['files']} file(s) changed, "
               f"{ana.stats['added_lines']} line(s) added"]
    if meta.get("title"):
        summary.append(f"PR title: {meta['title']}")
    if meta.get("description"):
        summary.append(f"Description: {meta['description']}")
    summary.append("Files: " + ", ".join(files[:20]))
    for fc in ana.files[:20]:
        heads = [t.strip() for _, n, t in
                 (fc.hunks[0].lines if fc.hunks else []) if n][:3]
        summary.append(f"- {fc.path} ({len(fc.added)} added lines), e.g.: "
                       + " / ".join(heads))
    if rules.files:
        summary.append("Team rule index: " + ", ".join(rules.files[:30]))
    if graph_text:
        summary.append(graph_text[:1500])

    system = prompt_or(PLANNER_PROMPT_NAME,
                       DEFAULT_PLANNER_PROMPT).replace("{max_dims}", str(max_dims))
    pr_summary = ""
    try:
        raw = await complete(system, "\n".join(summary))
        data = extract_json(raw) or {}
        pr_summary = str(data.get("summary", "")).strip()
        dims = [_Dim(**d) for d in data.get("dimensions", [])
                if isinstance(d, dict)]
        dims = [d for d in dims if d.name.strip()][:max_dims]
    except Exception:
        dims = []
    if len(dims) < 2:
        stats["plan"] = "fallback"
        return fallback, pr_summary
    stats["plan"] = "ok"
    return dims, pr_summary


# ---------- U2：coverage gate（純 code 的覆蓋保證） ----------

def _coverage_gate(dims: list[_Dim], ana: Anatomy,
                   rules: RulesBundle, stats: dict) -> list[_Dim]:
    """保證：每個變更檔、每條 md 規則，至少落在一個面向。孤兒 → Coverage Sweep。
    files 為空的面向視為涵蓋全部檔案（planner 明示的全 PR 面向）。"""
    all_files_dim = any(not d.files for d in dims)
    orphan_files = [] if all_files_dim else [
        fc.path for fc in ana.files
        if not any(match_path(fc.path, w) for d in dims for w in d.files)]
    all_rules_dim = any(not d.rules for d in dims)   # 空 rules = 收全量規則
    assigned_rules = {w for d in dims for w in d.rules}
    orphan_rules = [] if all_rules_dim else [
        rel for rel in rules.files
        if not any(match_path(rel, w) for w in assigned_rules)]

    stats["coverage"] = {
        "files": "full" if not orphan_files else "patched",
        "rules": "full" if (not rules.files or not orphan_rules) else "patched",
        "orphan_files": orphan_files,
        "orphan_rules": orphan_rules,
    }
    if not orphan_files and not orphan_rules:
        return dims

    sweep = _Dim(
        name="Coverage Sweep",
        focus=("Deterministic coverage backstop: no planned dimension claimed "
               "the files and/or team rules listed below. Review them with "
               "the same rigor — nothing merges unexamined."),
        hypotheses=["An unclaimed file contains a defect no dimension looked at",
                    "An unassigned team rule is violated by this change"],
        files=orphan_files or [fc.path for fc in ana.files],
        rules=orphan_rules,
    )
    return dims + [sweep]   # 覆蓋保證優先於 REVIEW_MAX_DIMENSIONS（最多 +1）


# ---------- 階段 3：平行 reviewer ----------

def _slices_for(dim: _Dim, ana: Anatomy) -> tuple[str, list[str]]:
    """回 (切片文, 被預算省略的檔案清單)。省略對模型有註記、對人類進 stats——
    「nothing merges unexamined」的誠實版本是：超預算檔案降級為 static-only。"""
    wanted = {_norm(f) for f in dim.files}
    picked = [fc for fc in ana.files
              if not wanted or any(match_path(fc.path, w) for w in wanted)]
    if not picked:
        picked = ana.files
    budget = _int("REVIEWER_SLICES_MAX_CHARS", 30000)
    per_file = _int("REVIEWER_FILE_SLICE_MAX_CHARS", 6000)
    parts, used, skipped = [], 0, []
    for fc in picked:
        chunk = numbered_slice(fc, max_chars=per_file)
        if used + len(chunk) > budget and parts:
            skipped.append(fc.path)
            continue
        parts.append(chunk)
        used += len(chunk)
    if skipped:
        parts.append(f"({len(skipped)} more changed file(s) omitted — this "
                     f"dimension's context budget was reached)")
    return "\n\n".join(parts), skipped


@observe(name="review-dimension")
async def _review_one(dim: _Dim, ana: Anatomy, rules: RulesBundle,
                      base_text: str, graph_text: str):
    system = prompt_or(REVIEWER_PROMPT_NAME, DEFAULT_REVIEWER_PROMPT) \
        + _rules_for_dim(rules, dim)
    brief = [f"# Review dimension: {dim.name}",
             f"Investigative focus: {dim.focus}"]
    if dim.hypotheses:
        brief.append("Hypotheses to test (address each in your assessment):")
        brief += [f"  {i}. {h}" for i, h in enumerate(dim.hypotheses, 1)]
    slices_text, slices_skipped = _slices_for(dim, ana)
    user_parts = ["\n".join(brief),
                  "# Changes (line numbers annotated)", slices_text]
    if base_text:
        user_parts += ["# Base-branch context (for understanding only — "
                       "do not comment on it)", base_text]
    if graph_text:
        user_parts.append(graph_text)
    raw = await complete(system, "\n\n".join(user_parts))
    data = extract_json(raw)
    assessment = ""
    rule_checks: list[dict] = []
    if isinstance(data, dict):                 # 新格式
        assessment = str(data.get("assessment", "")).strip()
        items = data.get("findings", [])
        rule_checks = [c for c in data.get("rule_checks", [])
                       if isinstance(c, dict) and c.get("rule_ref")
                       and c.get("verdict") in ("pass", "violation")]
    elif isinstance(data, list):               # 舊格式（裸陣列）相容
        items = data
    else:
        return "(unparseable reviewer output)", [], [], \
            {"location_dropped": 0, "location_flagged": 0,
             "slices_skipped": slices_skipped}
    base_conf = _float("REVIEW_LLM_BASE_CONF", 0.7)
    out: list[Finding] = []
    loc = {"location_dropped": 0, "location_flagged": 0,
           "slices_skipped": slices_skipped}
    for f in items:
        if not isinstance(f, dict):
            continue
        kr = f.get("knowledge_ref")                   # 型別收斂在建模前做：
        if isinstance(kr, str):                       # 引用格式寫歪不該讓整條
            f["knowledge_ref"] = [kr] if kr else []    # finding 被 pydantic 丟掉
        elif isinstance(kr, list):
            f["knowledge_ref"] = [str(x).strip() for x in kr if str(x).strip()]
        elif kr is not None:
            f.pop("knowledge_ref", None)
        try:
            fin = Finding(**f)
        except Exception:
            continue
        fin.source = "llm"
        fin.dimension = dim.name
        fin.confidence = base_conf
        fin.rule = (fin.rule or "BUG").strip().upper()
        if not any(match_path(fc.path, fin.file) for fc in ana.files):
            loc["location_dropped"] += 1        # 幻覺檔案：明確 drop，不進報告
            continue
        if not line_near(ana, fin.file, fin.line):
            fin.confidence = round(fin.confidence * 0.8, 3)   # 0.7→0.56：過線、帶標記
            fin.note = "[unverified location: line not in added set] " + fin.note
            loc["location_flagged"] += 1
        if fin.note:
            out.append(fin)
    return assessment, out, rule_checks, loc


async def _review_all(dims: list[_Dim], ana: Anatomy, rules: RulesBundle,
                      base_text: str, graph_text: str,
                      stats: dict) -> list[Finding]:
    sem = asyncio.Semaphore(_int("REVIEW_MAX_PARALLEL", 6))

    async def one(d: _Dim):
        async with sem:
            try:
                assessment, out, checks, loc = await _review_one(
                    d, ana, rules, base_text, graph_text)
                for k, v in loc.items():
                    if k == "slices_skipped":
                        merged = set(stats.get("context_omitted") or []) | set(v)
                        stats["context_omitted"] = sorted(merged)   # JSON 友善
                    else:
                        stats[k] = stats.get(k, 0) + v
                await note(f"Reviewing [{d.name}] — done, {len(out)} finding(s)")
                return out, {"name": d.name, "assessment": assessment,
                             "count": len(out)}, checks
            except Exception:
                stats.setdefault("reviewer_errors", 0)
                stats["reviewer_errors"] += 1
                await note(f"Reviewing [{d.name}] — failed, skipped")
                return [], {"name": d.name, "assessment": "Failed to execute",
                            "count": 0}, []

    results = await asyncio.gather(*(one(d) for d in dims))
    stats["assessments"] = [a for _, a, _ in results]     # 依 dims 原順序
    checks: dict[str, dict] = {}                          # violation 蓋過 pass
    for _, _, cs in results:
        for c in cs:
            ref = str(c["rule_ref"]).strip()
            if ref not in checks or c["verdict"] == "violation":
                checks[ref] = {"verdict": c["verdict"],
                               "evidence": str(c.get("evidence", ""))}
    stats["llm_rule_checks"] = checks
    return [f for fs, _, _ in results for f in fs]


# ---------- 階段 4：adversary（U3 分塊 + 行號證據 + 可換腦） ----------

@observe(name="adversary")
async def _adversary(llm_findings: list[Finding], ana: Anatomy,
                     base_text: str, rules: RulesBundle, stats: dict) -> None:
    """就地調整 llm findings 的 confidence。static（確定性）不送審、不動。"""
    if os.getenv("REVIEW_ADVERSARY", "true").strip().lower() in {"false", "0", "no"}:
        stats["adversary"] = "off"
        return
    if not llm_findings:
        stats["adversary"] = "n/a"
        return
    fallback_diff = "\n\n".join(numbered_slice(fc, max_chars=3000)
                                for fc in ana.files)[:12000]
    base_block = ("\n\n# Base-branch context\n" + base_text[:4000]) if base_text else ""

    def _context_for(batch: list[Finding]) -> str:
        # 檢察官至少要看得到被挑戰發現所在的檔 —— 大 PR 不能靠全域截斷碰運氣
        picked, seen = [], set()
        for f in batch:
            for fc in ana.files:
                if fc.path not in seen and match_path(fc.path, f.file):
                    seen.add(fc.path)
                    picked.append(numbered_slice(fc, max_chars=3000))
        text = "\n\n".join(picked)[:15000]
        rule_refs = sorted({f.rule_ref for f in batch if f.rule_ref})
        excerpts = []
        for ref in rule_refs:
            for rel, body in rules.sections.items():
                if (rel == ref or ref.endswith("/" + rel)
                        or rel.endswith("/" + ref)):
                    excerpts.append(f"## {rel}\n{body[:1500]}")
                    break
        if excerpts:
            text += ("\n\n# Referenced team rules (verbatim — a finding that "
                     "cites one of these is grounded in the rule itself; do not "
                     "reject it as 'project convention' without reading it)\n"
                     + "\n\n".join(excerpts)[:4500])
        return text or fallback_diff
    system = prompt_or(ADVERSARY_PROMPT_NAME, DEFAULT_ADVERSARY_PROMPT)
    model = os.getenv("LLM_MODEL_ADVERSARY", "").strip() or None
    chunk = max(1, _int("ADVERSARY_CHUNK", 6))
    chunks = [llm_findings[i:i + chunk]
              for i in range(0, len(llm_findings), chunk)]

    async def judge(batch: list[Finding]) -> list[dict] | None:
        listing = "\n".join(
            f'{i + 1}. [{f.rule}] {f.file}:{f.line} ({f.severity}) {f.note}'
            for i, f in enumerate(batch))
        user = ("# Diff (line numbers annotated)\n" + _context_for(batch)
                + base_block + "\n\n# Findings under challenge\n" + listing)
        try:
            raw = await complete(system, user, model=model)
            data = extract_json(raw)
            assert isinstance(data, list)
        except Exception:
            return None
        slots: list[dict] = [{} for _ in batch]
        for v in data:
            if not isinstance(v, dict):
                continue
            try:
                idx = int(v.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                slots[idx] = v
        return slots

    sem = asyncio.Semaphore(_int("REVIEW_MAX_PARALLEL", 6))

    async def _bounded(b):
        async with sem:                      # 檢察官同樣受併發上限管
            return await judge(b)

    results = await asyncio.gather(*(_bounded(b) for b in chunks))
    confirmed = rejected = degraded = demoted = 0
    for batch, slots in zip(chunks, results):
        if slots is None:
            degraded += 1
            continue                       # 該塊全維持 uncertain（中立）
        for f, v in zip(batch, slots):
            verdict = str(v.get("verdict", "uncertain")).lower()
            if verdict == "confirmed":
                f.confidence = min(0.95, f.confidence * 1.25)
                confirmed += 1
            elif verdict == "rejected":
                # 確定性執法：prompt 說「必引行號+挑戰編號」，這裡驗真。
                # 引不出合法行號的 reject 不配 ×0.35 —— 降為 uncertain。
                ch = str(v.get("challenge", "")).strip()
                line_ok = (line_in_view(ana, f.file, v.get("line"))
                           if ch == "1" else
                           line_near(ana, f.file, v.get("line")))
                if ch in {"1", "2", "3", "4"} and line_ok:
                    f.confidence = round(f.confidence * 0.35, 3)
                    rejected += 1
                else:
                    demoted += 1
    stats["adversary"] = (f"ok({confirmed} confirmed / {rejected} rejected"
                          f" / {len(llm_findings)}; {len(chunks)} chunk(s)"
                          + (f", {demoted} unproven-reject demoted" if demoted else "")
                          + (f", {degraded} degraded" if degraded else "")
                          + (f", model={model}" if model else "") + ")")


# ---------- 階段 5：synthesis（純 code） ----------

def _dedupe(findings: list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: -x.confidence):
        dup = None
        for k in kept:
            if k.rule != f.rule:
                continue
            if k.rule == "MISSING-TEST":       # 全域一條就夠（掛哪個檔不重要）
                dup = k
                break
            if _norm(k.file) != _norm(f.file):
                continue
            if k.line is None or f.line is None:
                if k.line == f.line:
                    dup = k
                    break
            elif abs(k.line - f.line) <= 3:
                dup = k
                break
        if dup is None:
            kept.append(f)
            continue
        if f.knowledge_ref and not dup.knowledge_ref:
            dup.knowledge_ref = f.knowledge_ref   # 去重不該吃掉引用（引用率分子）
        if {dup.source, f.source} == {"static", "llm"}:
            dup.confidence = 1.0
            dup.note = dup.note + " (corroborated: static + LLM)"
            dup.rule_ref = dup.rule_ref or f.rule_ref
    return kept


def _verify_knowledge_refs(findings: list[Finding], stats: dict) -> None:
    """引用率的確定性執法：knowledge_ref ⊆ 本次注入區塊裡真的出現過的 id。

    不在名單的剝掉（不扣分——引錯不代表 finding 是錯的，只代表這條引用不算數）。
    幻覺 id 若留在報告裡，日後沒有人能追回它指的是什麼。
    """
    known = {str(i).upper() for i in (stats.get("knowledge_ids") or [])}
    stats["knowledge_ids_injected"] = len(known)
    stripped = 0
    for f in findings:
        if not f.knowledge_ref:
            continue
        keep, seen = [], set()
        for ref in f.knowledge_ref:
            r = str(ref).strip().upper()
            if r in known and r not in seen:
                seen.add(r)
                keep.append(r)
            else:
                stripped += 1
        f.knowledge_ref = keep
    if stripped:
        stats["knowledge_refs_stripped"] = stripped


@observe(name="synthesis")
def _synthesize(findings: list[Finding], stats: dict) -> list[Finding]:
    merged = _dedupe(findings)
    _verify_knowledge_refs(merged, stats)
    threshold = _float("REVIEW_MIN_CONFIDENCE", 0.55)
    passed = [f for f in merged if f.confidence >= threshold]
    stats["dropped_low_conf"] = len(merged) - len(passed)
    cap = _int("REVIEW_MAX_FINDINGS", 30)
    passed.sort(key=sort_key)
    stats["dropped_over_cap"] = max(0, len(passed) - cap)
    final = passed[:cap]
    # 引用率的分子：最終留在報告裡、且引用有效的條數（線上量尺的上界——
    # 它只證明 reviewer 讀到了知識，不證明 finding 因它而來；下界在 eval 的
    # knowledge_delta）
    stats["knowledge_grounded"] = sum(1 for f in final if f.knowledge_ref)
    return final


def _rule_audit(rules: RulesBundle, findings: list[Finding],
                stats: dict) -> list[dict]:
    """規則稽核表：每條載入的團隊規則一列 —— 違反/通過/未檢核。
    證據優先序：最終發現的違反 > semgrep 機械掃描(0 命中=通過) > LLM 的 pass 表態。"""
    def norm(x: str) -> str:
        return (x or "").strip().lstrip("/").lower()

    violated: dict[str, str] = {}
    for f in findings:
        if f.rule_ref:
            violated.setdefault(norm(f.rule_ref),
                                f"See finding: {f.file}:{f.line} {f.note}")
    llm = {norm(k): v for k, v in (stats.get("llm_rule_checks") or {}).items()}
    rows: list[dict] = []
    for ref in list(stats.get("static_rule_refs") or []) + list(rules.files):
        key = norm(ref)
        if any(r["ref"] == ref for r in rows):
            continue
        if key in violated:
            rows.append({"ref": ref, "verdict": "violation",
                         "evidence": violated[key]})
        elif ref in (stats.get("static_rule_refs") or []):
            rows.append({"ref": ref, "verdict": "pass",
                         "evidence": "Static analysis (semgrep): 0 matches"})
        elif key in llm and llm[key]["verdict"] == "pass":
            rows.append({"ref": ref, "verdict": "pass",
                         "evidence": llm[key]["evidence"]
                         or "Verified by review dimension"})
        else:
            rows.append({"ref": ref, "verdict": "unchecked", "evidence": ""})
    return rows


# ---------- 入口 ----------

async def run_pipeline(diff: str, meta: dict,
                       rules: RulesBundle) -> ReviewResult:
    stats: dict = {"pipeline": "v2", "rules_sha": rules.sha or None}

    ana = parse_diff(diff)
    stats.update(ana.stats)
    stats["skipped_notes"] = ana.skipped_notes
    await note(f"Parsing diff — {ana.stats['files']} file(s), "
               f"{ana.stats['added_lines']} added line(s)"
               + (f", {len(ana.skipped_notes)} skipped"
                  if ana.skipped_notes else ""))

    static_finds, static_notes, static_refs = run_static(ana)
    stats["static_rule_refs"] = static_refs
    stats["static_findings"] = len(static_finds)
    if static_notes:
        stats["static_notes"] = static_notes
    await note(f"Static analysis — {len(static_finds)} finding(s)"
               + (f"; {static_notes[0]}"
                  if static_notes and not static_finds else ""))

    base_text, base_stats = ctx.base_context(meta.get("repo"), ana)
    graph_text, graph_stats = await ctx.graph_context(meta.get("repo"), ana)
    know_text, know_stats = knowledge.load_for(
        meta.get("repo"), [fc.path for fc in ana.files])
    stats.update(base_stats)
    stats.update(graph_stats)
    stats.update(know_stats)
    if know_text:
        graph_text = (graph_text + "\n\n" + know_text) if graph_text else know_text
    await note("Context prefetch — base: "
               + (f"{base_stats['base_files']} file(s)"
                  if base_stats.get("base_available")
                  else "unavailable (no local clone)")
               + ("; code graph: attached"
                  if graph_stats.get("graph_used") else "")
               + (f"; knowledge: {know_stats['knowledge_entries']} entr"
                  + ("y" if know_stats["knowledge_entries"] == 1 else "ies")
                  if know_stats.get("knowledge_entries") else ""))

    dims, pr_summary = await _plan(ana, meta, rules, graph_text, stats)
    dims = _coverage_gate(dims, ana, rules, stats)
    stats["pr_summary"] = pr_summary or (meta.get("title") or "")
    stats["file_table"] = [
        {"path": fc.path, "added": len(fc.added), "hunks": len(fc.hunks)}
        for fc in ana.files]
    stats["dimensions"] = len(dims)
    stats["dimension_names"] = [d.name for d in dims]
    cov = stats["coverage"]
    await note(("Planning" if stats.get("plan") == "ok"
                else "Planning (degraded to fixed dimensions)")
               + f" — {len(dims)} dimensions: "
               + ", ".join(d.name for d in dims))
    await note(f"Coverage gate — files: {cov['files']}; rules: {cov['rules']}"
               + (" (+Coverage Sweep dimension)"
                  if cov["files"] == "patched" or cov["rules"] == "patched"
                  else ""))

    llm_finds = await _review_all(dims, ana, rules, base_text, graph_text, stats)
    stats["llm_findings"] = len(llm_finds)

    await _adversary(llm_finds, ana, base_text, rules, stats)
    await note(f"Adversarial validation — {stats.get('adversary', 'n/a')}")

    final = _synthesize(static_finds + llm_finds, stats)
    stats["final_findings"] = len(final)
    stats["rule_audit"] = _rule_audit(rules, final, stats)
    await note(f"Synthesis — {len(final)} finding(s) retained"
               + (f", {stats['dropped_low_conf']} filtered below confidence"
                  " threshold" if stats.get("dropped_low_conf") else ""))

    return ReviewResult(markdown=render_markdown(final, stats),
                        findings=final, stats=stats)
PATCH_EOF

write "orchestrator/render.py" <<'PATCH_EOF'
"""render（v2 第 6 段）—— 專業英文標準報告 v3，markdown 由 code 產、格式恆定。

v3 升級：finding 編號（F1..Fn）全報告交叉引用；At-a-glance 統計行 +
Required actions（blocking 的行動清單）；Review Dimensions 改表格
（Dimension / Verdict / Findings / Assessment，含 Static analysis 合成列）；
Rule Compliance 的違反依據直接引 F 編號；Review Coverage 改成表格。
不變的底線：零發現照樣完整、無變更可解析明講不代表通過、全程無 emoji。
"""
from __future__ import annotations

from orchestrator.models import Finding

_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEV_HDR = [("error", "Blocking — must fix"),
            ("warning", "Non-blocking — should fix"),
            ("info", "Informational")]
_SRC = {"static": "static", "graph": "graph"}
_VERDICT_MARK = {"pass": "Pass", "violation": "Violation",
                 "unchecked": "Not assessed"}


def sort_key(f: Finding) -> tuple:
    return (_ORDER.get(f.severity, 2), -f.confidence)


def _norm(p: str) -> str:
    return (p or "").strip().lstrip("/").lower()


def _cell(text: str, limit: int | None = None) -> str:
    """表格儲存格：拔掉會拆表的字元（裸 | 與換行會拆掉 markdown 表格）。

    limit 預設 None＝不截斷——表格寬度由渲染端自動換行處理，不該在這裡砍內容。
    只有「本來就該短」的欄位（例如科名）才明確傳 limit。
    """
    s = (text or "").replace("|", "/").replace("\n", " ").strip()
    return s[:limit] if limit else s


def _partial(stats: dict) -> bool:
    return bool(stats.get("context_omitted") or stats.get("skipped_notes"))


def _verdict(findings: list[Finding], stats: dict | None = None) -> str:
    st = stats or {}
    if not findings and st.get("reviewer_errors") \
            and not st.get("static_findings"):
        n = st.get("reviewer_errors")
        return (f"**REVIEW INCOMPLETE** — {n} reviewer pass(es) failed and "
                "static analysis reported nothing; this is NOT an approval. "
                "Re-run the review.")
    if any(f.severity == "error" for f in findings):
        return ("**REQUIRES CHANGES** — blocking issues must be resolved "
                "before merge")
    if any(f.severity == "warning" for f in findings):
        return "**APPROVED WITH SUGGESTIONS** — non-blocking issues noted below"
    return "**APPROVED** — no issues requiring action were identified"


def _counts_line(findings: list[Finding]) -> str:
    n = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        n[f.severity] = n.get(f.severity, 0) + 1
    return (f"Blocking {n['error']} · Non-blocking {n['warning']} · "
            f"Informational {n['info']}")


def _required_actions(ordered: list[Finding], ids: dict[int, str]) -> list[str]:
    blocking = [f for f in ordered if f.severity == "error"]
    if not blocking:
        return []
    out = ["**Required actions before merge**:", ""]
    for i, f in enumerate(blocking, 1):
        loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
        action = f.suggestion or f.note
        out.append(f"{i}. {ids[id(f)]} {loc} — {_cell(action)}")
    out.append("")
    return out


def _file_table(stats: dict) -> list[str]:
    table = stats.get("file_table") or []
    if not table:
        return []
    out = ["### Changed Files", "",
           "| File | Lines added | Hunks |", "|---|---:|---:|"]
    for row in table[:15]:
        out.append(f"| `{row['path']}` | {row['added']} | {row['hunks']} |")
    if len(table) > 15:
        out.append(f"| … {len(table) - 15} more file(s) | | |")
    for n in (stats.get("skipped_notes") or [])[:4]:
        out.append(f"\n> Not reviewed: {n}")
    out.append("")
    return out


def _graph_section(stats: dict) -> list[str]:
    if not stats.get("graph_summary"):
        return []
    out = ["### Impact Analysis (Code Graph)", ""]
    out += [f"> {ln}" for ln in stats["graph_summary"].strip().splitlines()[:4]]
    impacted = stats.get("graph_impacted") or []
    if impacted:
        out += ["", "Affected outside this diff:"]
        out += [f"- `{name}`" for name in impacted]
    else:
        out += ["", "**No cross-file impact detected** — the dependency graph "
                "shows no other callers or files affected by these changes."]
    out.append("")
    return out


def _findings_section(ordered: list[Finding], ids: dict[int, str]) -> list[str]:
    out = [f"### Findings ({len(ordered)})", ""]
    for sev, hdr in _SEV_HDR:
        group = [f for f in ordered if f.severity == sev]
        if not group:
            continue
        out.append(f"#### {hdr} ({len(group)})")
        for f in group:
            loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
            tag = f" `[{_SRC[f.source]}]`" if f.source in _SRC else ""
            ref = f" (per {f.rule_ref})" if f.rule_ref else ""
            # 知識引用比照 rule_ref：讀者看得到這條發現站在哪一條既有決策上
            if f.knowledge_ref:
                ref += " (knowledge: " + ", ".join(f.knowledge_ref) + ")"
            note = " ".join(f.note.split())          # 換行/控制字元收斂
            out.append(f"- **{ids[id(f)]} · {f.rule}** {loc}{tag} — "
                       f"{note}{ref}")
            if f.suggestion:
                out.append(f"  Recommendation: {f.suggestion}")
        out.append("")
    return out


def _dimension_table(stats: dict, ordered: list[Finding],
                     ids: dict[int, str]) -> list[str]:
    rows = stats.get("assessments") or []
    if not rows and not stats.get("static_findings"):
        return []
    out = ["### Review Dimensions", "",
           "| Dimension | Verdict | Findings | Assessment |",
           "|---|---|---|---|"]
    static_ids = [ids[id(f)] for f in ordered if f.source == "static"]
    if stats.get("static_findings") is not None:
        verdict = "Issues found" if static_ids else "Clear"
        out.append("| Static analysis (deterministic) | " + verdict + " | "
                   + (", ".join(static_ids) or "—")
                   + " | semgrep / ruff / heuristic checks; findings carry "
                     "confidence 1.0 |")
    for a in rows:
        fids = [ids[id(f)] for f in ordered
                if f.source == "llm" and f.dimension == a["name"]]
        if a.get("assessment") == "Failed to execute":
            verdict = "Failed"
        elif fids or a.get("count"):
            verdict = "Issues found"
        else:
            verdict = "Clear"
        assessment = _cell(a.get("assessment") or "(no assessment provided)")
        out.append(f"| {_cell(a['name'], 80)} | {verdict} | "
                   f"{', '.join(fids) or '—'} | {assessment} |")
    out.append("")
    return out


def _rule_audit_section(stats: dict, ordered: list[Finding],
                        ids: dict[int, str]) -> list[str]:
    rows = stats.get("rule_audit") or []
    if not rows:
        return []
    sha = stats.get("rules_sha") or "?"
    out = [f"### Rule Compliance (rules {sha})", "",
           "| Rule | Status | Basis |", "|---|---|---|"]
    shown = [r for r in rows if r["verdict"] != "unchecked"][:14]
    for r in shown:
        basis = r["evidence"] or "—"
        if r["verdict"] == "violation":
            hit = next((f for f in ordered
                        if _norm(f.rule_ref) == _norm(r["ref"])), None)
            if hit is not None:
                basis = f"{ids[id(hit)]} — {basis}"
        out.append(f"| `{r['ref']}` | {_VERDICT_MARK[r['verdict']]} "
                   f"| {_cell(basis)} |")
    unchecked = sum(1 for r in rows if r["verdict"] == "unchecked")
    if unchecked:
        out.append(f"| … {unchecked} more | Not assessed | Out of scope for "
                   "this change or not covered by a dimension |")
    out.append("")
    return out


def _coverage_table(stats: dict) -> list[str]:
    rows: list[tuple[str, str]] = []
    if stats.get("files"):
        rows.append(("Scope", f"{stats['files']} file(s), "
                              f"{stats.get('added_lines', 0)} added line(s)"))
    static = f"{stats.get('static_findings', 0)} match(es)"
    if stats.get("static_findings", 0) == 0 and stats.get("static_notes"):
        static += " — " + "; ".join(stats["static_notes"][:2])
    rows.append(("Static analysis", static))
    if stats.get("base_available"):
        rows.append(("Base context", f"{stats.get('base_files', 0)} file(s)"))
    rows.append(("Code graph", "enabled" if stats.get("graph_used")
                 else "not attached"))
    if stats.get("knowledge_entries"):
        detail = (f"{stats['knowledge_entries']} entr"
                  + ("y" if stats["knowledge_entries"] == 1 else "ies")
                  + " matched")
        if stats.get("knowledge_invariants_only") or stats.get("knowledge_omitted"):
            # 降級可見：被縮成 invariants-only 或整條沒進去的，攤在報告上
            detail += (f" ({stats.get('knowledge_full', 0)} full, "
                       f"{stats.get('knowledge_invariants_only', 0)} invariants "
                       f"only, {stats.get('knowledge_omitted', 0)} omitted "
                       "— context budget)")
        if stats.get("knowledge_grounded"):
            detail += (f"; {stats['knowledge_grounded']} finding(s) cite a "
                       "ledger entry")
        rows.append(("Knowledge context", detail))
    omitted = stats.get("context_omitted") or []
    if omitted:
        rows.append(("Context budget",
                     f"{len(omitted)} file(s) exceeded reviewer context — "
                     "covered by static analysis only"))
    if stats.get("adversary"):
        rows.append(("Adversarial validation", stats["adversary"]))
    cov = stats.get("coverage") or {}
    if cov:
        rows.append(("Dimension coverage",
                     f"files: {cov.get('files')}; rules: {cov.get('rules')}"))
    if stats.get("dropped_low_conf"):
        rows.append(("Filtered", f"{stats['dropped_low_conf']} low-confidence "
                                 "finding(s) (REVIEW_MIN_CONFIDENCE)"))
    if stats.get("dropped_over_cap"):
        rows.append(("Truncated", f"{stats['dropped_over_cap']} finding(s) "
                                  "over the output cap"))
    if stats.get("reviewer_errors"):
        rows.append(("Warnings", f"{stats['reviewer_errors']} review "
                                 "dimension(s) failed to execute"))
    out = ["### Review Coverage", "", "| Check | Result |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows]
    out.append("")
    return out


def _footer(stats: dict) -> str:
    return ("\n---\n<sub>pipeline v2"
            + (f" · rules {stats['rules_sha']}" if stats.get("rules_sha") else "")
            + "</sub>")


def render_markdown(findings: list[Finding], stats: dict) -> str:
    out: list[str] = ["## Pull Request Review"]

    if not stats.get("files"):
        out.append("\n**No reviewable changes could be extracted from this "
                   "pull request** (empty diff, or all files were skipped). "
                   "This report does **not** constitute approval — "
                   "investigate the retrieval layer first.")
        for n in (stats.get("skipped_notes") or [])[:6]:
            out.append(f"- Skipped: {n}")
        out.append("")
        out += _coverage_table(stats)
        out.append(_footer(stats))
        return "\n".join(out)

    ordered = sorted(findings, key=sort_key)
    ids = {id(f): f"F{i}" for i, f in enumerate(ordered, 1)}

    if stats.get("pr_summary"):
        out.append(f"\n**Summary of Changes**: {stats['pr_summary']}")
    out.append(f"\n**Review Outcome**: {_verdict(ordered, stats)}")
    if _partial(stats):
        out.append("_Partial coverage: some files exceeded the reviewer "
                   "context budget or were skipped at diff level — see the "
                   "Coverage Check table. Static analysis covered all files._")
    if ordered:
        out.append(f"\n{_counts_line(ordered)}")
    out.append("")
    out += _required_actions(ordered, ids)
    out += _file_table(stats)
    out += _graph_section(stats)
    if ordered:
        out += _findings_section(ordered, ids)
    out += _dimension_table(stats, ordered, ids)
    out += _rule_audit_section(stats, ordered, ids)
    out += _coverage_table(stats)
    out.append(_footer(stats))
    return "\n".join(out)
PATCH_EOF

write "orchestrator/static_pass.py" <<'PATCH_EOF'
"""static pass（v2 第 1 段）—— 確定性掃描，零 LLM、零幻覺、行號精準。

做法：對每個變更檔做「稀疏重建」（anatomy.sparse_new_file，行號保真）寫進
暫存目錄 → semgrep / ruff 掃 → 只保留落在「新增行」上的命中（不對未變更
的程式說教）→ 產出 source="static"、confidence=1.0 的 findings。

規則來源：
  1) 專案內建 deploy/semgrep/starter.yml
  2) rules repo 裡的 semgrep 規則（RULES_ROOT 底下 **/*.semgrep.yml 與
     semgrep/**/*.yml）—— 機械可查的團隊規則寫成 semgrep，掛在這層，
     命中自動以該規則檔為 rule_ref（確定性執法，rule_ref 也是確定的）。

環境變數：STATIC_SEMGREP / STATIC_RUFF（預設 auto：裝了就跑，沒裝就跳過並註記）
production 和離線 eval 吃的都是同一份 diff —— 這層在兩邊行為一模一樣。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from orchestrator.anatomy import Anatomy, sparse_new_file
from orchestrator.models import Finding

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
STARTER = _PROJECT_ROOT / "deploy" / "semgrep" / "starter.yml"

_SEV = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}


def _enabled(env: str, binary: str) -> bool:
    v = os.getenv(env, "auto").strip().lower()
    if v in {"false", "0", "no", "off"}:
        return False
    has = shutil.which(binary) is not None
    if v in {"true", "1", "yes", "on"}:
        return has  # 明開但沒裝 → 還是只能跳過（會註記）
    return has      # auto


def _rules_semgrep_configs() -> list[tuple[Path, str]]:
    """(規則檔路徑, rule_ref) —— 來自 rules repo 的 semgrep 規則。"""
    root = os.getenv("RULES_ROOT", "").strip()
    if not root:
        return []
    r = Path(root)
    if not r.is_dir():
        return []
    seen: dict[Path, str] = {}
    for pat in ("**/*.semgrep.yml", "**/*.semgrep.yaml",
                "semgrep/**/*.yml", "semgrep/**/*.yaml"):
        for f in sorted(r.glob(pat)):
            seen.setdefault(f, str(f.relative_to(r)))
    return list(seen.items())


def _materialize(ana: Anatomy, tmp: Path) -> dict[Path, str]:
    """稀疏重建寫進暫存目錄。回傳 暫存路徑 → 原始 diff 路徑 的對照。"""
    mapping: dict[Path, str] = {}
    for fc in ana.files:
        body = sparse_new_file(fc)
        if not body.strip():
            continue
        rel = fc.path.lstrip("/")
        dst = (tmp / rel).resolve()
        if tmp not in dst.parents:            # 防路徑穿越（../../ 之類）
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body)
        mapping[dst] = fc.path
    return mapping


def _timeout() -> int:
    try:
        return int(os.getenv("STATIC_TIMEOUT", "300"))
    except ValueError:
        return 90


def _run(cmd: list[str], env: dict | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_timeout(), env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out (STATIC_TIMEOUT={_timeout()}s)"
    except OSError as e:
        return -1, "", str(e)


_RULE_ID = re.compile(r"^\s*-\s*id:\s*([\w.\-]+)", re.M)


def _id_map(configs: list[tuple[Path, str]]) -> dict[str, str]:
    """規則 id → 來源規則檔（rule_ref 用）。id 撞名時先到先贏。"""
    out: dict[str, str] = {}
    for cfg, ref in configs:
        try:
            text = cfg.read_text(errors="replace")
        except OSError:
            continue
        for rid in _RULE_ID.findall(text):
            out.setdefault(rid, ref)
    return out


def _semgrep(tmp: Path, mapping: dict[Path, str], added: dict[str, set[int]],
             notes: list[str]) -> list[Finding]:
    configs: list[tuple[Path, str]] = []
    if STARTER.is_file():
        configs.append((STARTER, ""))
    configs += _rules_semgrep_configs()
    if not configs:
        notes.append("semgrep: no rule configs available, skipped")
        return [], []
    checked_refs = sorted({ref for _, ref in configs if ref})
    ids = _id_map(configs)
    cmd = ["semgrep", "scan", "--json", "--quiet", "--metrics=off",
           "--timeout", "30", "--no-git-ignore"]
    for cfg, _ in configs:
        cmd += ["--config", str(cfg)]
    cmd.append(str(tmp))
    # 內網重點：關掉啟動時的版本檢查（連不上 semgrep.dev 會吊到逾時）
    env = dict(os.environ, SEMGREP_ENABLE_VERSION_CHECK="0")
    code, out, err = _run(cmd, env=env)
    if code < 0 or not out.strip():
        notes.append(f"semgrep failed: {err[:120]}")
        return [], []
    try:
        results = json.loads(out).get("results", [])
    except json.JSONDecodeError:
        notes.append("semgrep: non-JSON output, skipped")
        return [], []
    finds: list[Finding] = []
    for r in results:
        p = Path(r.get("path", "")).resolve()
        orig = mapping.get(p)
        if not orig:
            continue
        line = int((r.get("start") or {}).get("line") or 0)
        if line not in added.get(orig, set()):
            continue                       # 只評新增行，不對未變更程式說教
        extra = r.get("extra") or {}
        meta = extra.get("metadata") or {}
        check = str(r.get("check_id", ""))
        ref = str(meta.get("rule_ref", ""))
        if not ref:                        # check_id 可能帶路徑前綴，取尾段對 id 表
            ref = ids.get(check) or ids.get(check.rsplit(".", 1)[-1], "")
        finds.append(Finding(
            rule=str(meta.get("review_rule", "SECURITY")).upper(),
            file=orig, line=line,
            severity=_SEV.get(str(extra.get("severity", "")).upper(), "warning"),
            note=(extra.get("message") or check),
            rule_ref=ref,
            source="static", confidence=1.0,
        ))
    return finds, checked_refs


def _ruff(mapping: dict[Path, str], added: dict[str, set[int]],
          notes: list[str]) -> list[Finding]:
    py = [p for p in mapping if p.suffix == ".py"]
    if not py:
        return []
    cmd = ["ruff", "check", "--select", "F821,F811,F601,E711,E712",
           "--output-format", "json", "--no-cache", "--isolated",
           *map(str, py)]
    code, out, err = _run(cmd)
    if not out.strip():
        if code not in (0, 1):
            notes.append(f"ruff failed: {err[:160]}")
        return []
    try:
        results = json.loads(out)
    except json.JSONDecodeError:
        notes.append("ruff: non-JSON output, skipped")
        return []
    finds: list[Finding] = []
    for r in results:
        p = Path(r.get("filename", "")).resolve()
        orig = mapping.get(p)
        line = int((r.get("location") or {}).get("row") or 0)
        if not orig or line not in added.get(orig, set()):
            continue
        finds.append(Finding(
            rule="BUG", file=orig, line=line, severity="warning",
            note=f"[{r.get('code')}] {r.get('message', '')}",
            source="static", confidence=1.0,
        ))
    return finds


# 公開名：knowledge 的蒸餾切片排序與 primary_entity fallback 吃同一份判斷——
# 「哪些檔是測試」「哪一行是新宣告」只能有一個定義，否則兩邊會慢慢漂移。
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?)(/|$)|(^|/)(test_[^/]+|[^/]+_test)\.py$"
    r"|(^|/)[A-Za-z0-9_]*Tests?\.java$|(^|/)[A-Za-z0-9_]*Spec\.[a-z]+$"
    r"|\.(spec|test)\.[jt]sx?$", re.I)
_BEHAVIOR_EXT = {".java", ".py", ".kt", ".cs", ".go", ".ts", ".js", ".scala"}
DECL_RE = re.compile(
    r"^\s*(def |class |function |func |fun |"
    r"(public|protected|private)\b[^;=]*\(|"
    r"(static|final)\b[^;=]*\([^)]*\)\s*\{)")


def _missing_test(ana: Anatomy) -> list[Finding]:
    """確定性 MISSING-TEST：新增了可呼叫行為、同 diff 卻沒碰任何測試檔。
    刻意保守：只認「新增宣告」為行為訊號 —— 純重構（改常數/搬呼叫）不觸發。
    STATIC_MISSING_TEST=false 可整段關閉。"""
    if os.getenv("STATIC_MISSING_TEST", "true").strip().lower() in {"false", "0", "no"}:
        return []
    if any(TEST_PATH_RE.search(fc.path.lstrip("/")) for fc in ana.files):
        return []                                   # diff 有帶測試 → 不吭聲
    behavior: list[tuple[int, str, int]] = []       # (新增宣告數, path, 首行)
    for fc in ana.files:
        if Path(fc.path).suffix.lower() not in _BEHAVIOR_EXT:
            continue
        decls = [ln for ln, text in fc.added if DECL_RE.match(text)]
        if decls:
            behavior.append((len(decls), fc.path, decls[0]))
    if not behavior:
        return []
    behavior.sort(key=lambda x: (-x[0], x[1]))
    _, primary, line = behavior[0]
    names = ", ".join(b[1] for b in behavior[:3])
    return [Finding(
        rule="MISSING-TEST", file=primary, line=line, severity="warning",
        note=(f"This diff adds or changes behavior in {len(behavior)} file(s) "
              f"({names}) but touches no test files"),
        suggestion="Add or update tests covering the new/changed behavior "
                   "in the same pull request.",
        source="static", confidence=1.0,
    )]


def run_static(ana: Anatomy) -> tuple[list[Finding], list[str], list[str]]:
    """回傳 (findings, notes, checked_rule_refs)。
    checked_rule_refs = 實際掃描過的「團隊 semgrep 規則檔」——0 命中即為
    該規則的機械通過證據。掃描器缺席／故障 = 註記並跳過，絕不炸 review。"""
    notes: list[str] = []
    checked: list[str] = []
    added = {fc.path: fc.added_linenos for fc in ana.files}
    if not any(added.values()):
        return [], notes, checked
    tmp = Path(tempfile.mkdtemp(prefix="crg-static-"))
    try:
        mapping = _materialize(ana, tmp)
        finds: list[Finding] = []
        if _enabled("STATIC_SEMGREP", "semgrep"):
            sg, checked = _semgrep(tmp, mapping, added, notes)
            finds += sg
        else:
            notes.append("semgrep: disabled or not installed")
        if _enabled("STATIC_RUFF", "ruff"):
            finds += _ruff(mapping, added, notes)
        else:
            notes.append("ruff: disabled or not installed")
        finds += _missing_test(ana)
        return finds, notes, checked
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
PATCH_EOF

write "tools/ado_pr_tools/server.py" <<'PATCH_EOF'
"""① ado-pr-tools —— 抓 Azure DevOps PR 的工具組（FastMCP）。

紀律：無狀態、唯讀、不 import langfuse。PAT/proxy 只活在本服務的環境變數裡，
永遠不會流進 orchestrator 或 trace。

啟動：python -m tools.ado_pr_tools.server   （PORT 預設 8091）
"""
from __future__ import annotations

import difflib
import os
from concurrent.futures import ThreadPoolExecutor

from fastmcp import FastMCP

from shared.azdo import PrRef, client, get_json, get_text, parse_pr_url

mcp = FastMCP("ado-pr-tools", mask_error_details=False)

MAX_FILES = int(os.getenv("ADO_DIFF_MAX_FILES", "300"))  # 先於一切層（含 static）的最外圈上限
MAX_FILE_BYTES = int(os.getenv("ADO_DIFF_MAX_FILE_BYTES", "600000"))
# 每個檔案要抓 base/head 兩份內容；序列化跑百檔會拖到分鐘級，用執行緒併發。
# httpx.Client 是 thread-safe 的，共用同一顆 client 即可（連線池也一起共用）。
FETCH_CONCURRENCY = max(1, int(os.getenv("ADO_DIFF_FETCH_CONCURRENCY", "8")))


PAGE = 200          # ADO 單頁上限；靠 $skip 迴圈把全部撈完


def _latest_iteration(c, ref: PrRef) -> dict | None:
    """最新一次 iteration 的完整物件。

    裡面有三個 commit，選對哪一個決定了 diff 對不對得上 PR 頁面：
      commonRefCommit  merge base —— PR UI 左欄就是它（三點 diff 的基準）
      sourceRefCommit  來源分支 tip —— PR UI 右欄
      targetRefCommit  目標分支 tip —— **不要拿來當 base**，master 一動它就變
    """
    iterations = get_json(c, f"{ref.pr_api}/iterations")["value"]
    return iterations[-1] if iterations else None


def _iteration_changes(c, ref: PrRef, iteration_id: int) -> list[dict]:
    """該 iteration 相對 merge base 的完整變更清單（$skip 分頁，不截斷）。"""
    entries: list[dict] = []
    skip = 0
    while True:
        page = get_json(
            c, f"{ref.pr_api}/iterations/{iteration_id}/changes",
            **{"$top": PAGE, "$skip": skip},
        ).get("changeEntries", [])
        entries += page
        if len(page) < PAGE:
            return entries
        skip += PAGE


def _is_folder(entry: dict) -> bool:
    item = entry.get("item") or {}
    return bool(item.get("isFolder")) or item.get("gitObjectType") == "tree"


def _latest_iteration_changes(c, ref: PrRef) -> list[dict]:
    it = _latest_iteration(c, ref)
    if it is None:
        return []
    return [e for e in _iteration_changes(c, ref, it["id"]) if not _is_folder(e)]


@mcp.tool()
def get_pr_metadata(pr_url: str) -> dict:
    """取得 PR 的標題、描述、分支與 head/base commit。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        pr = get_json(c, ref.pr_api)
        it = _latest_iteration(c, ref) or {}
    # source_commit 取自 iteration 而非 lastMergeSourceCommit：兩者平時相同，
    # 但前者才是這份 diff 真正的右欄，快取鍵要跟 diff 綁在一起才不會失準。
    source_commit = ((it.get("sourceRefCommit") or {}).get("commitId")
                     or (pr.get("lastMergeSourceCommit") or {}).get("commitId"))
    return {
        "repo": ref.repo,  # 讓 orchestrator 能載入 per-repo 規則（rules/<repo小寫>/）
        "title": pr.get("title", ""),
        "description": pr.get("description", ""),
        "source_branch": pr.get("sourceRefName", ""),
        "target_branch": pr.get("targetRefName", ""),
        "author": (pr.get("createdBy") or {}).get("displayName", ""),
        "status": pr.get("status", ""),
        "source_commit": source_commit,
        "merge_base": (it.get("commonRefCommit") or {}).get("commitId"),
        "target_commit": (pr.get("lastMergeTargetCommit") or {}).get("commitId"),
    }


@mcp.tool()
def list_pr_files(pr_url: str) -> list[dict]:
    """列出這條 PR 變更的檔案（路徑 + 變更型態）。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        entries = _latest_iteration_changes(c, ref)
    return [
        {
            "path": (e.get("item") or {}).get("path", ""),
            "change_type": e.get("changeType", ""),
        }
        for e in entries
        if (e.get("item") or {}).get("path")
    ]


@mcp.tool()
def get_pr_diff(pr_url: str) -> str:
    """組出這條 PR 的 unified diff（純文字）。

    做法：取最新 iteration 的變更清單，逐檔抓 base/head 兩個 commit 的內容，
    用 difflib 產生 unified diff。過大或疑似二進位的檔案會標註略過。
    （若你們既有 daemon 已有更完整的 diff 邏輯，可把內部實作換成它，
    對外簽名 get_pr_diff(pr_url) -> str 維持不變即可。）
    """
    ref = parse_pr_url(pr_url)
    out: list[str] = []
    with client() as c:
        it = _latest_iteration(c, ref)
        if it is None:
            return "# 這條 PR 沒有 iteration，無法取得變更"

        # 三點 diff：merge base ↔ 來源分支 tip，與 PR 頁面左右兩欄一致。
        # 用 targetRefCommit 當 base 會把目標分支上別人的改動反向算進來，
        # 而且目標分支一往前推，同一條沒動過的 PR 就會跑出不同的 diff。
        base = (it.get("commonRefCommit") or {}).get("commitId")
        head = (it.get("sourceRefCommit") or {}).get("commitId")

        entries = [e for e in _iteration_changes(c, ref, it["id"])
                   if not _is_folder(e)]

        total = len(entries)
        if total > MAX_FILES:
            out.append(f"# 注意：共 {total} 個檔案，僅呈現前 {MAX_FILES} 個\n")
            entries = entries[:MAX_FILES]

        item_url = f"{ref.repo_api}/items"

        def _content(commit_id: str | None, p: str) -> str | None:
            if not commit_id:
                return None
            return get_text(
                c,
                item_url,
                path=p,
                **{
                    "versionDescriptor.version": commit_id,
                    "versionDescriptor.versionType": "commit",
                },
            )

        # (新路徑, 舊路徑)；rename 時 base 側要用舊路徑，否則整檔會被誤判成新增
        targets: list[tuple[str, str]] = []
        for e in entries:
            path = (e.get("item") or {}).get("path")
            if path:
                targets.append((path, e.get("sourceServerItem") or path))

        def _pair(t: tuple[str, str]) -> tuple[str | None, str | None]:
            path, old_path = t
            return _content(base, old_path), _content(head, path)

        # executor.map 依輸入順序回傳 —— 併發抓取，輸出仍是 deterministic 的
        with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as pool:
            fetched = list(pool.map(_pair, targets))

        for (path, old_path), (old, new) in zip(targets, fetched):
            if old_path != path:
                out.append(f"# {old_path} → {path}: renamed\n")
            if (old and len(old) > MAX_FILE_BYTES) or (new and len(new) > MAX_FILE_BYTES):
                out.append(f"# {path}: file too large — diff skipped\n")
                continue
            if (old and "\x00" in old[:1024]) or (new and "\x00" in new[:1024]):
                out.append(f"# {path}: binary file suspected — skipped\n")
                continue

            diff = difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"a{old_path}",
                tofile=f"b{path}",
            )
            out.append("".join(diff))

    return "\n".join(s for s in out if s.strip()) or "# 這條 PR 沒有可呈現的文字變更"


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8091")),
    )
PATCH_EOF

write "tools/review_result_tools/server.py" <<'PATCH_EOF'
"""③ review-result-tools —— 把 review 結果寫回 / 查回 Azure DevOps（FastMCP）。

刻意跟 ①（唯讀）分開：讀與寫的權限等級不同，未來 gateway 治理可以只放行讀、
寫要審批。預設 DRY_RUN=true —— 內測期間絕不誤發真評論。

環境變數：ADO_PAT（必填）、DRY_RUN（預設 true）、HTTPS_PROXY / ADO_VERIFY_SSL 同 ①
啟動：python -m tools.review_result_tools.server   （PORT 預設 8093）
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from shared.azdo import client, get_json, parse_pr_url

mcp = FastMCP("review-result-tools", mask_error_details=False)

MARKER = "<!-- pr-review-bot -->"  # 隱形浮水印：讓我們認得自己發過的評論


def _dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").strip().lower() not in {"false", "0", "no"}


@mcp.tool()
def post_review_comment(pr_url: str, markdown: str) -> dict:
    """把 review markdown 發成 PR 上的一條 comment thread。

    DRY_RUN=true（預設）時不打 API，只回傳預覽 —— 內測安全氣囊。
    """
    ref = parse_pr_url(pr_url)
    content = f"{MARKER}\n{markdown}"
    if _dry_run():
        return {
            "posted": False,
            "dry_run": True,
            "would_post_to": ref.pr_api + "/threads",
            "preview": content[:500],
        }
    with client() as c:
        r = c.post(
            f"{ref.pr_api}/threads",
            params={"api-version": "7.1"},
            json={
                "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
                "status": "active",
            },
        )
        r.raise_for_status()
        thread = r.json()
    return {"posted": True, "dry_run": False, "thread_id": thread.get("id")}


KNOWLEDGE_MARKER = "<!-- pr-review-knowledge-draft -->"


def _knowledge_post() -> bool:
    # 跟 review 的 DRY_RUN 刻意分家：知識稿可先行試點真貼，review 留言繼續乾跑
    return os.getenv("KNOWLEDGE_POST", "false").strip().lower() in {"true", "1", "yes"}


REDISTILL_PREFIX = ("_Draft updated after new commits; earlier author notes "
                    "were kept._")


@mcp.tool()
def post_knowledge_draft(pr_url: str, markdown: str,
                         thread_id: int | None = None) -> dict:
    """把知識蒸餾稿發成 PR 上的一條 thread（KNOWLEDGE_POST=true 才真貼）。

    thread_id 給定時改成「回覆進那條既有 thread」而不是開新的——重蒸（stale
    後用最終 diff 重蒸）的更新稿要接在作者回過話的那串下面，不然同一條 PR
    會出現兩份互相矛盾的草稿，作者也不知道該回哪一份。
    """
    ref = parse_pr_url(pr_url)
    reply = thread_id is not None
    body = f"{REDISTILL_PREFIX}\n\n{markdown}" if reply else markdown
    content = f"{KNOWLEDGE_MARKER}\n{body}"
    target = (f"{ref.pr_api}/threads/{thread_id}/comments" if reply
              else f"{ref.pr_api}/threads")
    if not _knowledge_post():
        return {"posted": False, "dry_run": True, "reply": reply,
                "thread_id": thread_id, "would_post_to": target,
                "preview": content[:500]}
    with client() as c:
        if reply:
            r = c.post(target, params={"api-version": "7.1"},
                       json={"content": content, "commentType": 1})
            r.raise_for_status()
            return {"posted": True, "dry_run": False, "reply": True,
                    "thread_id": thread_id}
        r = c.post(
            target,
            params={"api-version": "7.1"},
            json={"comments": [{"parentCommentId": 0, "content": content,
                                "commentType": 1}],
                  "status": "active"},
        )
        r.raise_for_status()
        thread = r.json()
    return {"posted": True, "dry_run": False, "reply": False,
            "thread_id": thread.get("id")}


@mcp.tool()
def get_past_reviews(pr_url: str) -> list[str]:
    """撈出這條 PR 上「本 bot 發過」的歷史評論（靠隱形浮水印過濾）。"""
    ref = parse_pr_url(pr_url)
    with client() as c:
        threads = get_json(c, f"{ref.pr_api}/threads")["value"]
    out: list[str] = []
    for t in threads:
        for cm in t.get("comments", []):
            body = cm.get("content") or ""
            if MARKER in body:
                out.append(body.replace(MARKER, "").strip())
    return out


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8093")),
    )
PATCH_EOF

write "eval/preflight.py" <<'PATCH_EOF'
"""Preflight：開跑 experiment 前逐條驗 dataset item 的 shape。

目的：UI 收編忘了「整形」的 item（例如 input 缺 diff、expected_output 缺
可計分的期望）應該死在開跑前三秒，而不是燒完半輪 LLM 之後噴 ERROR: 'diff'。

單獨跑：  python -m eval.preflight
被引用：  run_eval 開跑前自動呼叫（驗不過 exit 2、列出兇手、不開跑）

一條合格的 item：
  input            dict，必含非空字串 "diff"；選配 "repo"（str）
  expected_output  dict，必含 "expected"（v2 結構清單，乾淨 PR 給 []）
                   或 "must_catch"（舊版關鍵字清單）兩者之一
  v2 的 expected 每條必含 rule；file 選配（不填=類別級、只比 rule）；選配 line / line_tolerance / rule_ref
"""
from __future__ import annotations

import sys

from langfuse import get_client

DATASET = "pr-review-golden"

# 幾何前提考題：fixture 名 → 斷言（重放預算，驗證前提真的發生）
# 目前是空的：pr-129 / pr-130 隨著預算與科數調大而失效，已移除。機制留著——
# 日後再加「切片預算會破」這類考題時，在這裡登記斷言，才不會有題目安靜地
# 退化成「跟別題考一樣的東西」而沒人發現。
_GEOMETRY: dict[str, str] = {}


def check_environment() -> list[str]:
    """考卷的隱性環境依賴明示化：static 地板題沒有 semgrep = recall gate
    會以「模型漏抓」的誤導姿勢失敗——用真實原因在開跑前擋下。"""
    import shutil
    probs: list[str] = []
    if not shutil.which("semgrep"):
        probs.append("semgrep 不在 PATH —— 多題 SECURITY 期望靠 static 地板"
                     "保底，缺席會讓 recall gate 誤導性失敗。裝 semgrep 再跑。")
    return probs


def check_fixture_geometry() -> list[str]:
    """執行才抓得到的考卷錯：重放 _slices_for 的預算，驗證每題的幾何前提
    （例如「這題的切片預算真的會破」）確實成立。_GEOMETRY 空 = 無題可驗。"""
    from pathlib import Path as _P

    from orchestrator.anatomy import parse_diff
    from orchestrator.pipeline import _slices_for

    class _All:
        files: list = []
        name = "geometry"
        focus = ""
        hypotheses: list = []

    probs: list[str] = []
    fixtures = _P(__file__).parent / "fixtures"
    for fname, expect in _GEOMETRY.items():
        f = fixtures / fname
        if not f.exists():
            continue
        _, skipped = _slices_for(_All(), parse_diff(f.read_text()))
        if expect == "tripped" and not skipped:
            probs.append(f"{fname}: 預算沒被踩到（截斷路徑沒被考）")
        if expect == "dao-omitted" and not any("UserQueryDao" in x
                                               for x in skipped):
            probs.append(f"{fname}: UserQueryDao 未落在省略區——考題退化")
    return probs


def _is_active(item) -> bool:
    status = str(getattr(item, "status", "") or "").upper()
    return "ARCHIVED" not in status


def is_knowledge_item(item) -> bool:
    """知識依賴題（kfix-*）：input.repo 指到一個種子帳本目錄。
    run_eval 的對照組只重跑這一批。"""
    meta = getattr(item, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get("kind") == "knowledge":
        return True
    inp = getattr(item, "input", None) or {}
    return isinstance(inp, dict) and str(inp.get("repo") or "").startswith("kfix-")


def check_knowledge_fixtures(items) -> list[str]:
    """知識題的種子帳本必須真的在磁碟上——少一個目錄，那題會安靜地退化成
    「沒有知識的對照組」，delta 於是天生為 0 而且看不出原因。"""
    from pathlib import Path as _P
    import os

    root = _P(os.getenv("EVAL_KNOWLEDGE_ROOT", "").strip()
              or (_P(__file__).parent / "fixtures" / "knowledge"))
    probs: list[str] = []
    for item in items:
        if not _is_active(item) or not is_knowledge_item(item):
            continue
        repo = str((item.input or {}).get("repo") or "")
        if repo and not (root / repo).is_dir():
            probs.append(f"知識題 repo '{repo}' 在 {root} 底下沒有帳本目錄"
                         "—— 這題會退化成無知識對照組")
        exp = item.expected_output if isinstance(item.expected_output, dict) else {}
        if not any(e.get("knowledge_ref") for e in (exp.get("expected") or [])
                   if isinstance(e, dict)):
            probs.append(f"知識題 repo '{repo}' 的 expected 沒有任何 "
                         "knowledge_ref —— 量不到 knowledge_recall")
    return probs


def check_item(item) -> list[str]:
    """回傳這條 item 的所有問題（空清單 = 合格）。"""
    probs: list[str] = []
    inp = item.input
    if not isinstance(inp, dict):
        return [f"input 不是 dict（實際是 {type(inp).__name__}）"]
    diff = inp.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        probs.append('input 缺非空字串 "diff"（收編後要整形，見 README）')
    repo = inp.get("repo")
    if repo is not None and not isinstance(repo, str):
        probs.append('"repo" 要是字串')

    exp = item.expected_output
    if not isinstance(exp, dict):
        return probs + [
            "expected_output 不是 dict —— 沒有期望的 item 會無條件滿分，是死重"
        ]

    has_v2 = "expected" in exp
    has_legacy = isinstance(exp.get("must_catch"), list)
    if not has_v2 and not has_legacy:
        probs.append('expected_output 缺 "expected"（v2）或 "must_catch"（舊版）'
                     "—— 乾淨 PR 也要明寫 \"expected\": []")

    if has_v2:
        items = exp["expected"]
        if not isinstance(items, list):
            probs.append('"expected" 要是清單')
        else:
            for i, e in enumerate(items):
                if not isinstance(e, dict):
                    probs.append(f"expected[{i}] 不是 dict")
                    continue
                for key in ("rule",):        # file 選配：類別級期望可不填
                    if not isinstance(e.get(key), str) or not e[key].strip():
                        probs.append(f"expected[{i}] 缺非空字串 \"{key}\"")
                if "line" in e and e["line"] is not None \
                        and not isinstance(e["line"], int):
                    probs.append(f"expected[{i}] 的 \"line\" 要是整數")
                kref = e.get("knowledge_ref")
                if kref is not None and not isinstance(kref, str):
                    probs.append(f"expected[{i}] 的 \"knowledge_ref\" 要是字串"
                                 "（單一條目 id，例如 PR41-I1）")

    if "allowed_extra" in exp and (
            not isinstance(exp["allowed_extra"], int) or exp["allowed_extra"] < 0):
        probs.append('"allowed_extra" 要是 >=0 的整數')
    if "forbidden" in exp and not isinstance(exp["forbidden"], list):
        probs.append('"forbidden" 要是字串清單')
    if "max_findings" in exp and not isinstance(exp["max_findings"], int):
        probs.append('"max_findings" 要是整數')
    return probs


def check_items(items) -> tuple[list[str], dict]:
    """驗一批 items。回傳 (問題清單, 統計)。"""
    problems: list[str] = []
    stats = {"total": 0, "v2": 0, "legacy": 0, "clean": 0, "knowledge": 0}
    for item in items:
        if not _is_active(item):
            continue
        stats["total"] += 1
        if is_knowledge_item(item):
            stats["knowledge"] += 1
        exp = item.expected_output if isinstance(item.expected_output, dict) else {}
        if "expected" in exp:
            stats["v2"] += 1
            if exp["expected"] == []:
                stats["clean"] += 1
        elif isinstance(exp.get("must_catch"), list):
            stats["legacy"] += 1
        short = str(item.id)[:8]
        problems.extend(f"item {short}…: {p}" for p in check_item(item))
    return problems, stats


def main() -> None:
    dataset = get_client().get_dataset(DATASET)
    items = list(dataset.items)
    problems, stats = check_items(items)
    problems = (check_environment() + check_fixture_geometry()
                + check_knowledge_fixtures(items) + problems)
    print(f"dataset '{DATASET}'：{stats['total']} 條有效 item"
          f"（v2 結構 {stats['v2']}、舊版關鍵字 {stats['legacy']}、"
          f"乾淨負樣本 {stats['clean']}、知識依賴 {stats['knowledge']}）")
    if stats["clean"] == 0:
        print("提醒：沒有乾淨 PR 負樣本（expected: []）——只獎不罰，agent 會學會灌水")
    if problems:
        print(f"\nPreflight 不通過，{len(problems)} 個問題：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)
    print("Preflight 通過")


if __name__ == "__main__":
    main()
PATCH_EOF

write "eval/run_eval.py" <<'PATCH_EOF'
"""跑一輪 eval：golden dataset → run_review（離線、可重現）→ 分數回 Langfuse。

計分是 deterministic 的：scorer 是純函式，沒有 LLM 參與評分；run 間的
分數浮動全部來自被評的 agent 本身（所以定基線時同一 config 跑兩輪量雜訊底）。

expected_output 支援兩種 shape（可混用，preflight 會驗）：

v2 結構比對（建議）——比 findings 的 rule + file（可選行號容差、rule_ref）：
  {
    "expected": [
      {"rule": "BUG", "file": "src/calculator.py"},
      {"rule": "SECURITY", "file": "src/api.py",
       "rule_ref": "rules/common/security.md", "line": 16, "line_tolerance": 5}
    ],
    "allowed_extra": 2,          # 容忍幾條合理的額外 finding，超過算噪音
    "forbidden": ["LGTM"]
  }
  乾淨 PR 負樣本："expected": [] + "allowed_extra": 0 —— 量 false positive。

舊版關鍵字（相容）：{"must_catch": [...], "forbidden": [...], "max_findings": N}

指標：must_catch_recall（兩種 shape 同名，CI gate 讀這個）、precision（僅 v2）、
noise_flag、forbidden_hit。run 的 metadata 會記 rules_sha × prompt_version ×
llm_model —— 每輪自我描述，分數變動才對得回配置。

知識依賴題（kfix-*，Phase 2.2）：expected 的某條 finding 帶 "knowledge_ref"，
量 reviewer 有沒有真的**站在**帳本條目上提出這條發現 → knowledge_recall。
開跑時 run-level 把 KNOWLEDGE_ROOT 覆寫到 eval/fixtures/knowledge（可用
EVAL_KNOWLEDGE_ROOT 改指），每題 input.repo = kfix-<n> 對應一個子目錄；其餘
考題不帶 repo，天然拿不到任何頁，零污染。跑完再對同一批題跑一次
KNOWLEDGE_ROOT="" 的對照組 → knowledge_delta = 有知識 − 無知識。
delta 是整個知識迴圈唯一重要的數：>0 才值得繼續蓋。

用法：
  python -m eval.run_eval v2-structured
  EVAL_MIN_RECALL=0.7 EVAL_MAX_NOISE=0.2 EVAL_MAX_FORBIDDEN=0 \
      python -m eval.run_eval v2-gated        # 三道門檻 = CI gate
  EVAL_KNOWLEDGE_AB=false python -m eval.run_eval quick   # 跳過對照組
退出碼：0 通過；1 gate 擋下；2 preflight 不通過（根本沒開跑）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from langfuse import Evaluation, get_client

from eval.preflight import (check_environment, check_fixture_geometry,
                            check_items, check_knowledge_fixtures,
                            is_knowledge_item)
from orchestrator.core import PROMPT_NAME, run_review
from orchestrator.rules import load_rules

DATASET = "pr-review-golden"
KNOWLEDGE_FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"

_recalls: list[float] = []
_noises: list[float] = []
_forbiddens: list[float] = []
# 知識分數按「這一輪是有知識還是對照組」分開收，delta 才算得出來；再按題型
# （單頁 / distractor）分桶，delta=0 時才判得出是 consumption 問題還是排序問題
_PHASE = {"name": "with"}
_k_recalls: dict[str, list[tuple[str, float]]] = {"with": [], "without": []}
_KTYPES = ("single-page", "distractor")


async def task(*, item, **kwargs):
    """對每條 dataset item 跑一次 review（框架支援 async task）。"""
    inp = item.input if isinstance(item.input, dict) else {}
    meta = {k: inp[k] for k in ("repo", "title", "description")
            if inp.get(k)}
    result = await run_review(inp["diff"], meta or None)
    return {"markdown": result.markdown, "findings": result.findings_summary()}


# ---------- v2 結構比對（純函式、確定性） ----------

def _norm_path(p: str) -> str:
    p = (p or "").strip().lstrip("/")
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p.lower()


def _match(exp: dict, f: dict) -> bool:
    if (exp.get("rule", "")).strip().upper() != (f.get("rule") or "").strip().upper():
        return False
    e, g = _norm_path(exp.get("file", "")), _norm_path(f.get("file") or "")
    if e:                                   # 沒填 file = 類別級期望，只比 rule
        if not (g and (g.endswith(e) or e.endswith(g))):
            return False
    if exp.get("line") is not None:
        if f.get("line") is None:
            return False
        if abs(int(f["line"]) - int(exp["line"])) > int(exp.get("line_tolerance", 5)):
            return False
    ref = exp.get("rule_ref")
    if ref:
        got = _norm_path(f.get("rule_ref") or "")
        want = _norm_path(ref)
        if not (got and (got == want or got.endswith(want))):
            return False
    return True


def _score_v2(expected: list[dict], findings: list[dict],
              allowed_extra: int | None) -> tuple[float, float, float, str]:
    """回傳 (recall, precision, noise_flag, comment)。greedy 一對一配對。"""
    used: set[int] = set()
    missed: list[str] = []
    by_src = {"static": 0, "llm": 0}
    for exp in expected:
        hit = next((i for i, f in enumerate(findings)
                    if i not in used and _match(exp, f)), None)
        if hit is None:
            missed.append(f"{exp.get('rule')}@{exp.get('file')}")
        else:
            used.add(hit)
            src = (findings[hit].get("source") or "llm").lower()
            by_src["static" if src == "static" else "llm"] += 1
    matched = len(expected) - len(missed)
    recall = matched / len(expected) if expected else 1.0
    precision = 1.0 if not findings else matched / len(findings)
    extras = len(findings) - len(used)
    noise = float(allowed_extra is not None and extras > allowed_extra)
    comment = ("all caught" if not missed else "missed: " + ", ".join(missed)) \
        + f"; extra findings: {extras}" \
        + (f" (allowed {allowed_extra})" if allowed_extra is not None else "") \
        + f"; matched by source: static {by_src['static']} / llm {by_src['llm']}"
    return recall, precision, noise, comment, by_src


def _score_knowledge(expected: list[dict],
                     findings: list[dict]) -> tuple[float, str] | None:
    """knowledge_recall：expected 裡帶 knowledge_ref 的期望，有幾條被「同一條
    finding 命中 + 真的引了那個 id」滿足。None = 這題不是知識依賴題。

    要求兩件事同時成立是刻意的：只引不抓、或只抓不引，都不算 grounded ——
    前者是應付合約，後者證明不了這條發現是知識帶來的。
    """
    wanted = [e for e in expected if e.get("knowledge_ref")]
    if not wanted:
        return None
    used: set[int] = set()
    hits, missed = 0, []
    for exp in wanted:
        ref = str(exp["knowledge_ref"]).strip().upper()
        idx = next((i for i, f in enumerate(findings)
                    if i not in used and _match(exp, f)
                    and ref in {str(r).strip().upper()
                                for r in (f.get("knowledge_ref") or [])}), None)
        if idx is None:
            missed.append(ref)
        else:
            used.add(idx)
            hits += 1
    comment = (f"grounded {hits}/{len(wanted)}"
               + ("; not grounded: " + ", ".join(missed) if missed else ""))
    return hits / len(wanted), comment


def scorer(*, input, output, expected_output, metadata=None, **kwargs):
    md = (output or {}).get("markdown", "")
    findings = (output or {}).get("findings", [])
    low = md.lower()
    exp = expected_output or {}

    evs: list[Evaluation] = []
    if "expected" in exp:  # v2 結構比對
        recall, precision, noise, comment, by_src = _score_v2(
            exp["expected"], findings, exp.get("allowed_extra"))
        evs.append(Evaluation(name="must_catch_recall", value=recall, comment=comment))
        evs.append(Evaluation(name="precision", value=precision))
        matched = by_src["static"] + by_src["llm"]
        evs.append(Evaluation(                     # headline 的 static 地板占比
            name="recall_static_share",
            value=(by_src["static"] / matched) if matched else 0.0))
    else:                  # 舊版關鍵字（相容）
        must = exp.get("must_catch", [])
        recall = sum(1 for k in must if k.lower() in low) / len(must) if must else 1.0
        noise = float(bool(exp.get("max_findings"))
                      and len(findings) > exp["max_findings"])
        evs.append(Evaluation(name="must_catch_recall", value=recall))

    forbidden = float(any(f.lower() in low for f in exp.get("forbidden", [])))
    evs.append(Evaluation(name="noise_flag", value=noise))
    evs.append(Evaluation(name="forbidden_hit", value=forbidden))

    k = _score_knowledge(exp.get("expected") or [], findings)
    if k is not None:
        evs.append(Evaluation(name="knowledge_recall", value=k[0], comment=k[1]))
        ktype = str((metadata or {}).get("ktype") or "unspecified")
        _k_recalls[_PHASE["name"]].append((ktype, k[0]))

    if _PHASE["name"] == "with":      # 對照組只貢獻 knowledge_recall，不動主指標
        _recalls.append(recall)
        _noises.append(noise)
        _forbiddens.append(forbidden)
    return evs


# ---------- 三道 CI gate（都是選配，設了才生效） ----------

def _gates(recall_avg: float, noise_rate: float, forbidden_rate: float) -> list[str]:
    fails: list[str] = []
    min_recall = float(os.getenv("EVAL_MIN_RECALL", "0"))
    if min_recall and recall_avg < min_recall:
        fails.append(f"recall {recall_avg:.2f} < 門檻 {min_recall}")
    if os.getenv("EVAL_MAX_NOISE", "") != "":
        mx = float(os.environ["EVAL_MAX_NOISE"])
        if noise_rate > mx:
            fails.append(f"噪音率 {noise_rate:.2f} > 上限 {mx}")
    if os.getenv("EVAL_MAX_FORBIDDEN", "") != "":
        mx = float(os.environ["EVAL_MAX_FORBIDDEN"])
        if forbidden_rate > mx:
            fails.append(f"禁語率 {forbidden_rate:.2f} > 上限 {mx}")
    return fails


def _knowledge_gate(delta: float) -> list[str]:
    """knowledge_delta 的門檻。刻意預設「沒設 = 不擋」：基線穩定兩輪之前
    這個數只 report，用一個沒有量尺的門把 CI 擋紅是 0.35 那類錯誤的重演。"""
    raw = os.getenv("EVAL_MIN_KNOWLEDGE_DELTA", "").strip()
    if not raw or not _k_recalls["without"]:
        return []
    try:
        want = float(raw)
    except ValueError:
        return []
    return ([] if delta >= want else
            [f"knowledge_delta {delta:+.2f} < 門檻 {want:+.2f}"])


def _prompt_version() -> int | None:
    try:
        return get_client().get_prompt(PROMPT_NAME).version
    except Exception:
        return None


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _knowledge_ab(lf, dataset, run_name: str, run_meta: dict) -> None:
    """對照組：同一批知識依賴題、KNOWLEDGE_ROOT="" 再跑一輪。

    只跑那幾題（不是整份考卷）—— delta 要的是同一批題的差，其餘題目沒有 repo，
    有沒有帳本對它們完全一樣，重跑只是燒錢。
    """
    k_items = [it for it in dataset.items if is_knowledge_item(it)]
    if not k_items:
        print("  知識依賴題：0 條（跳過對照組）")
        return
    if os.getenv("EVAL_KNOWLEDGE_AB", "true").strip().lower() in {"false", "0", "no"}:
        print("  對照組已關閉（EVAL_KNOWLEDGE_AB=false）—— 只有上界，沒有 delta")
        return
    keep = os.environ.get("KNOWLEDGE_ROOT", "")
    _PHASE["name"] = "without"
    os.environ["KNOWLEDGE_ROOT"] = ""          # 這一輪的 agent 拿不到任何條目
    try:
        lf.run_experiment(
            name=f"{run_name}-noknowledge",
            run_name=f"{run_name}-noknowledge",
            data=k_items,
            task=task,
            evaluators=[scorer],
            max_concurrency=2,
            metadata={**run_meta, "knowledge_root": "", "arm": "control"},
        )
        lf.flush()
    finally:
        _PHASE["name"] = "with"
        os.environ["KNOWLEDGE_ROOT"] = keep


def main() -> None:
    run_name = sys.argv[1] if len(sys.argv) > 1 else "v1-baseline"
    lf = get_client()
    dataset = lf.get_dataset(DATASET)

    items = list(dataset.items)
    problems, stats = check_items(items)           # preflight：驗不過不開跑
    problems = (check_environment() + check_fixture_geometry()
                + check_knowledge_fixtures(items) + problems)
    if problems:
        print(f"Preflight 不通過（{len(problems)} 個問題），不開跑：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)

    # run-level 覆寫（不做 per-item env 切換：load_for 走現成的 repo 隔離，
    # eval 併發也安全）。實際值記進 run metadata，分數才對得回配置。
    kroot = os.getenv("EVAL_KNOWLEDGE_ROOT", "").strip() or str(KNOWLEDGE_FIXTURES)
    os.environ["KNOWLEDGE_ROOT"] = kroot

    run_meta = {
        "pipeline": os.getenv("REVIEW_PIPELINE", "v1"),
        "rules_sha": load_rules(None).sha or None,
        "prompt_version": _prompt_version(),
        "llm_model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "knowledge_root": kroot,
        "arm": "knowledge",
    }
    dataset.run_experiment(
        name=run_name,
        run_name=run_name,
        task=task,
        evaluators=[scorer],
        max_concurrency=2,   # 對內部 gateway 溫柔一點
        metadata=run_meta,
    )
    lf.flush()
    _knowledge_ab(lf, dataset, run_name, run_meta)

    n = len(_recalls)
    recall_avg = _mean(_recalls)
    noise_rate = _mean(_noises)
    forbidden_rate = _mean(_forbiddens)
    print(f"run '{run_name}'：{n} 條（v2 {stats['v2']}／舊 {stats['legacy']}／"
          f"乾淨 {stats['clean']}／知識 {stats['knowledge']}）")
    print(f"  recall 平均 {recall_avg:.2f}｜噪音率 {noise_rate:.2f}｜"
          f"禁語率 {forbidden_rate:.2f}")
    k_with = _mean([v for _, v in _k_recalls["with"]])
    k_without = _mean([v for _, v in _k_recalls["without"]])
    delta = k_with - k_without
    if _k_recalls["with"]:
        print(f"  knowledge_recall 有知識 {k_with:.2f}"
              f"（{len(_k_recalls['with'])} 題）｜對照組 {k_without:.2f}"
              f"（{len(_k_recalls['without'])} 題）｜"
              f"**knowledge_delta {delta:+.2f}**")
        for kt in _KTYPES:                     # 兩型分開讀 —— 診斷順序靠這兩行
            w = [v for t, v in _k_recalls["with"] if t == kt]
            wo = [v for t, v in _k_recalls["without"] if t == kt]
            if w:
                print(f"    · {kt:<12} 有知識 {_mean(w):.2f}（{len(w)} 題）"
                      f"｜對照組 {_mean(wo):.2f}｜delta {_mean(w) - _mean(wo):+.2f}")
    print(f"  配置：prompt v{run_meta['prompt_version']} × "
          f"rules {run_meta['rules_sha'] or '未啟用'} × {run_meta['llm_model']}"
          f" × knowledge {kroot or '（無）'}")
    print("→ UI：Datasets → pr-review-golden → Runs 看逐條分數與 trace")

    fails = _gates(recall_avg, noise_rate, forbidden_rate)
    fails += _knowledge_gate(delta)
    if fails:
        print("CI gate 擋下：" + "；".join(fails))
        sys.exit(1)


if __name__ == "__main__":
    main()
PATCH_EOF

write "eval/run_distill_eval.py" <<'PATCH_EOF'
"""蒸餾考卷 —— 跑一輪 distiller 的離線評測（Phase 4）。

為什麼要有這張考卷：review pipeline 有 32 題押著，K 段的 LLM 輸出過去零考卷
——改 `pr-review-distiller` 的 prompt 等於裸奔。從此改它跟改 reviewer prompt
一樣要先過考。

計分全部確定性（零 LLM 評審）：

| 分數 | 量什麼 | 怎麼判 |
|---|---|---|
| intent_ok        | 「PR 沒講 why 就不要編」守不守得住 | intent_stated == expected |
| no_fabrication   | why 缺席題有沒有冒出 rationale 措辭 | forbidden_mention 不出現 |
| retires_ok       | supersede 的 retires_id 精準度 | 集合完全相等（誤殺別條也扣） |
| evidence_ok      | 產出的 evidence 過不過 Phase 1 驗證器 | 剝掉 0 個 token、丟 0 條 decision |
| must_mention     | 決策關鍵詞有沒有被寫進條目 | 關鍵詞命中率 |
| primary_ok       | primary_entity 選得對不對 | 模型自己就選中變更檔內的正確檔 |
| no_hallucination | 材料撐不起 invariant 時有沒有硬蒸 | invariants 為空 |
| distill_score    | 以上適用項目的平均 | — |

用法：
  python -m eval.run_distill_eval distill-baseline
  EVAL_MIN_DISTILL_SCORE=0.8 python -m eval.run_distill_eval distill-gated
退出碼：0 通過；1 gate 擋下。門檻未設 = 只報數不擋（先蹲基線再升硬門）。
"""
from __future__ import annotations

import os
import sys

from langfuse import Evaluation, get_client

from orchestrator.anatomy import match_path, parse_diff
from orchestrator.knowledge import DISTILLER_PROMPT_NAME, distill_once

DATASET = "pr-review-distill-golden"

_scores: list[float] = []


async def task(*, item, **kwargs):
    """跑一次真正的蒸餾（同一支 distill_once，含證據驗證與 primary 修正）。

    finding_ids 給空集合：考卷沒有 review 報告，`F<n>` 形式的 evidence 本來就
    不該過——這也順帶考「沒有報告時模型會不會硬掰 finding 編號」。
    """
    inp = item.input if isinstance(item.input, dict) else {}
    diff = inp.get("diff") or ""
    ana = parse_diff(diff)
    meta = {"title": inp.get("title", ""), "description": inp.get("description", ""),
            "target_branch": "release/eval"}
    data, info = await distill_once(
        pr_id=1, repo="eval", meta=meta, ana=ana,
        ana_files=[fc.path for fc in ana.files],
        finding_ids=set(), finding_files=[],
        catalogue=inp.get("catalogue") or "")
    return {"draft": data, "info": {k: v for k, v in info.items()
                                    if k != "slices_skipped"}}


# ---------- 純函式計分 ----------

def _entry_text(d: dict) -> str:
    parts = [str(d.get("summary") or ""), str(d.get("ledger_line") or "")]
    for x in d.get("decisions") or []:
        if isinstance(x, dict):
            parts.append(str(x.get("text") or ""))
    for x in d.get("invariants") or []:
        parts.append(x.get("text", "") if isinstance(x, dict) else str(x))
    return " ".join(parts).lower()


def _retires(d: dict) -> set[str]:
    return {str(s.get("retires_id") or "").strip().upper()
            for s in (d.get("supersedes") or []) if isinstance(s, dict)
            and str(s.get("retires_id") or "").strip()}


def score_distill(expected: dict, draft: dict, info: dict,
                  changed: list[str]) -> tuple[list[tuple[str, float, str]], float]:
    """回 ([(名稱, 分數, 說明)], 綜合分)。純函式 —— pytest 直接吃它。"""
    out: list[tuple[str, float, str]] = []

    want_intent = bool(expected.get("intent_stated", True))
    got_intent = bool(draft.get("intent_stated", True))
    out.append(("intent_ok", float(got_intent == want_intent),
                f"expected {want_intent}, got {got_intent}"))

    forbidden = [w.lower() for w in (expected.get("forbidden_mention") or [])]
    if forbidden:
        text = _entry_text(draft)
        hit = [w for w in forbidden if w in text]
        out.append(("no_fabrication", float(not hit),
                    "fabricated rationale: " + ", ".join(hit) if hit else "clean"))

    want_ret = {str(expected["retires_id"]).upper()} if expected.get("retires_id") \
        else set()
    got_ret = _retires(draft)
    out.append(("retires_ok", float(got_ret == want_ret),
                f"expected {sorted(want_ret) or 'none'}, got {sorted(got_ret) or 'none'}"))

    stripped = int(info.get("evidence_stripped") or 0)
    dropped = len(info.get("dropped") or [])
    n_dec = len(draft.get("decisions") or [])
    out.append(("evidence_ok", float(stripped == 0 and dropped == 0),
                f"{n_dec} decision(s) kept; {stripped} evidence item(s) stripped, "
                f"{dropped} decision(s) dropped"))

    words = [w.lower() for w in (expected.get("must_mention") or [])]
    if words:
        text = _entry_text(draft)
        hits = [w for w in words if w in text]
        out.append(("must_mention", len(hits) / len(words),
                    f"{len(hits)}/{len(words)}: missing "
                    + (", ".join(w for w in words if w not in hits) or "none")))

    ent = str(draft.get("primary_entity") or "")
    want_ent = expected.get("primary_entity")
    ok = not info.get("primary_fixed") and bool(ent)
    if ok and want_ent:
        ok = match_path(ent, want_ent)
    out.append(("primary_ok", float(ok),
                f"got {ent or '(none)'}"
                + ("（fallback 介入）" if info.get("primary_fixed") else "")))

    if expected.get("forbid_invariants"):
        n_inv = len(draft.get("invariants") or [])
        out.append(("no_hallucination", float(n_inv == 0),
                    f"{n_inv} invariant(s) distilled from material that "
                    "supports none"))

    composite = sum(v for _, v, _ in out) / len(out)
    return out, composite


def scorer(*, input, output, expected_output, **kwargs):
    draft = (output or {}).get("draft") or {}
    info = (output or {}).get("info") or {}
    ana = parse_diff((input or {}).get("diff") or "")
    rows, composite = score_distill(expected_output or {}, draft, info,
                                    [fc.path for fc in ana.files])
    _scores.append(composite)
    return [Evaluation(name=n, value=v, comment=c) for n, v, c in rows] \
        + [Evaluation(name="distill_score", value=composite)]


def main() -> None:
    run_name = sys.argv[1] if len(sys.argv) > 1 else "distill-baseline"
    lf = get_client()
    dataset = lf.get_dataset(DATASET)
    items = list(dataset.items)
    if not items:
        print(f"dataset '{DATASET}' 是空的 —— 先跑 python -m eval.seed_langfuse")
        sys.exit(2)
    try:
        prompt_version = lf.get_prompt(DISTILLER_PROMPT_NAME).version
    except Exception:
        prompt_version = None

    dataset.run_experiment(
        name=run_name, run_name=run_name, task=task, evaluators=[scorer],
        max_concurrency=2,
        metadata={"prompt": DISTILLER_PROMPT_NAME,
                  "prompt_version": prompt_version,
                  "llm_model": os.getenv("LLM_MODEL", "gpt-4o-mini")},
    )
    lf.flush()

    avg = sum(_scores) / len(_scores) if _scores else 0.0
    print(f"run '{run_name}'：{len(_scores)} 題｜distill_score 平均 {avg:.2f}")
    print(f"  配置：prompt {DISTILLER_PROMPT_NAME} v{prompt_version} × "
          f"{os.getenv('LLM_MODEL', 'gpt-4o-mini')}")
    print(f"→ UI：Datasets → {DATASET} → Runs 看逐題分數與 trace")

    want = os.getenv("EVAL_MIN_DISTILL_SCORE", "").strip()
    if want:
        try:
            if avg < float(want):
                print(f"CI gate 擋下：distill_score {avg:.2f} < 門檻 {want}")
                sys.exit(1)
        except ValueError:
            pass


if __name__ == "__main__":
    main()
PATCH_EOF

write "eval/seed_langfuse.py" <<'PATCH_EOF'
"""播種：把六份 system prompt 推上 Langfuse 受版控 + 建/擴三套考題。

三套考題（兩個 dataset）：
  pr-review-golden          30 題 review 金題 + 5 題知識依賴題（kfix-*）
  pr-review-distill-golden  10 題蒸餾考卷（含三題陷阱）

用法：python -m eval.seed_langfuse
（LANGFUSE_HOST / PUBLIC_KEY / SECRET_KEY 由環境變數提供）

冪等規則：
- prompts 每跑必推新版本（掛 production label）—— 只在「code 內建 prompt 有
  更新的部署輪」跑；日常迭代走 Langfuse UI，跑 seed 會把 UI 修改蓋掉。
- dataset items 以 metadata.fixture 為鍵「逐條冪等」：已存在的跳過、只補新的
  —— fixtures 目錄加了新題，重跑 seed 就只灌新題。

金題設計（Java / JPA / SQL / Spring Boot）：
  static 保底題（101–103）  semgrep 必中 → recall 地板
  LLM 邏輯題（104,106,107） 語意缺陷，static 看不見 → 量 reviewer 腦力
  已知難題（105,108,111）   MISSING-TEST / NAMING / 跨檔推理 → 進步空間的量尺
  乾淨負樣本（109,110）     expected 為空 → 量誤報；110 專防「重構被嫌沒測試」
日常養資料動線：Traces → Add to dataset → 整形成 v2 shape（見 run_eval.py）。
"""
from __future__ import annotations

from pathlib import Path

from langfuse import get_client

from orchestrator.core import DEFAULT_SYSTEM_PROMPT, PROMPT_NAME
from orchestrator.knowledge import DEFAULT_DISTILLER_PROMPT, DISTILLER_PROMPT_NAME
from orchestrator.knowledge_digest import DEFAULT_DIGEST_PROMPT, DIGEST_PROMPT_NAME
from orchestrator.pipeline import (
    ADVERSARY_PROMPT_NAME, DEFAULT_ADVERSARY_PROMPT,
    DEFAULT_PLANNER_PROMPT, DEFAULT_REVIEWER_PROMPT,
    PLANNER_PROMPT_NAME, REVIEWER_PROMPT_NAME,
)

DATASET = "pr-review-golden"
FIXTURES = Path(__file__).parent / "fixtures"

_J = "src/main/java/com/acme"
_FORBID = ["LGTM"]


def _case(fname, expected, allowed_extra, kind, focus, hard=False):
    exp = {"expected": expected, "allowed_extra": allowed_extra,
           "forbidden": _FORBID}
    meta = {"fixture": fname, "kind": kind, "focus": focus}
    if hard:
        meta["hard"] = True
    if fname.startswith("pr-1"):
        meta["lang"] = "java"
    return fname, exp, meta


CASES = [
    # 註：原本的 pr-129 / pr-130（41 / 46 檔的截斷路徑考題）已移除——切片預算與
    # 科數調大之後，它們不再踩得到預算，考的東西名存實亡。日後要再考截斷路徑，
    # 得照新的預算重新生 fixture，並在 preflight._GEOMETRY 重新登記斷言。
    # ---- 既有 python 種題 ----
    _case("pr-002.diff",
          [{"rule": "BUG", "file": "src/calculator.py"},
           {"rule": "SECURITY", "file": "src/api.py"},
           {"rule": "MISSING-TEST"}],   # 類別級：掛哪個檔都算對
          2, "planted", "python-baseline"),
    _case("pr-003-clean.diff", [], 0, "clean", "python-clean"),
    # ---- static 保底（semgrep 必中 = recall 地板） ----
    _case("pr-101-jdbc-injection.diff",
          [{"rule": "SECURITY", "file": f"{_J}/user/UserRepository.java"},
           {"rule": "SECURITY", "file": f"{_J}/user/UserRepository.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "jdbctemplate-concat+format"),
    _case("pr-102-mybatis-dollar.diff",
          [{"rule": "SECURITY", "file": f"{_J}/order/OrderMapper.java"}],
          2, "planted", "mybatis-dollar-substitution"),
    _case("pr-103-catch-secret.diff",
          [{"rule": "SECURITY", "file": f"{_J}/payment/PaymentClient.java"},
           {"rule": "BUG", "file": f"{_J}/payment/PaymentClient.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-secret+empty-catch"),
    # ---- LLM 邏輯題（static 看不見） ----
    _case("pr-104-logic-boundary.diff",
          [{"rule": "BUG", "file": f"{_J}/pricing/PricingService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "off-by-one-vs-javadoc"),
    _case("pr-106-npe-optional.diff",
          [{"rule": "BUG", "file": f"{_J}/user/UserService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "optional-get-vs-null-contract"),
    _case("pr-107-transaction.diff",
          [{"rule": "BUG", "file": f"{_J}/transfer/TransferService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "spring-missing-transactional"),
    # ---- 已知難題（現階段 recall 缺口的量尺，不是 bug） ----
    _case("pr-105-missing-test.diff",
          [{"rule": "MISSING-TEST", "file": f"{_J}/auth/TokenService.java"}],
          2, "planted", "new-behavior-no-test", hard=True),
    _case("pr-108-naming.diff",
          [{"rule": "NAMING", "file": f"{_J}/account/AccountService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "map-named-list+double-negative", hard=True),
    _case("pr-111-crossfile-units.diff",
          [{"rule": "BUG", "file": f"{_J}/cache/SessionCache.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "cross-file-unit-mismatch", hard=True),
    # ---- 乾淨負樣本（量誤報；110 專防「重構被嫌沒測試」） ----
    _case("pr-109-clean-endpoint.diff", [], 0, "clean",
          "endpoint-with-test"),
    _case("pr-110-clean-refactor.diff", [], 0, "clean",
          "pure-refactor-no-behavior-change"),
    # ---- 敏感 log（112/113 有 static 保底；114 純 LLM/合規語境） ----
    _case("pr-112-log-password.diff",
          [{"rule": "SECURITY", "file": f"{_J}/auth/LoginService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "password-in-log"),
    _case("pr-113-log-auth-header.diff",
          [{"rule": "SECURITY", "file": f"{_J}/web/RequestLogFilter.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "authorization-header-in-log"),
    _case("pr-114-log-pii.diff",
          [{"rule": "SECURITY", "file": f"{_J}/user/ProfileService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "pii-in-log-vs-compliance-note"),
    _case("pr-115-clean-masked-log.diff", [], 0, "clean",
          "masked-logging-with-test"),
    # ---- hardcode（118 有 static 保底；116/117 對照 javadoc 的意圖矛盾） ----
    _case("pr-116-hardcoded-config.diff",
          [{"rule": "BUG", "file": f"{_J}/sync/SyncScheduler.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-interval-vs-configurable-contract"),
    _case("pr-117-hardcoded-path.diff",
          [{"rule": "BUG", "file": f"{_J}/export/ReportExporter.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "hardcoded-path-vs-readonly-container"),
    _case("pr-118-ecb-cipher.diff",
          [{"rule": "SECURITY", "file": f"{_J}/crypto/FieldEncryptor.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "ecb-cipher-mode"),
    # ---- 簡單題（120 有 static 保底） ----
    _case("pr-119-obvious-npe.diff",
          [{"rule": "BUG", "file": f"{_J}/config/FeatureFlags.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "npe-on-absent-property"),
    _case("pr-120-string-eq.diff",
          [{"rule": "BUG", "file": f"{_J}/order/OrderStateMachine.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "string-reference-equality"),
    _case("pr-121-interrupted.diff",
          [{"rule": "BUG", "file": f"{_J}/worker/RetryHelper.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "swallowed-interrupt-no-reinterrupt"),
    _case("pr-122-resource-leak.diff",
          [{"rule": "BUG", "file": f"{_J}/imports/CsvLoader.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "reader-not-closed"),
    _case("pr-123-clean-equals.diff", [], 0, "clean",
          "equals-hashcode-with-test"),
    # ---- 更多情況（併發 / Spring 暗坑 / PERF / API 契約；後三題標 hard） ----
    _case("pr-124-shared-dateformat.diff",
          [{"rule": "BUG", "file": f"{_J}/report/DateFormats.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "shared-simpledateformat-threads"),
    _case("pr-125-tx-self-invocation.diff",
          [{"rule": "BUG", "file": f"{_J}/billing/InvoiceService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "transactional-self-invocation", hard=True),
    _case("pr-126-n-plus-one.diff",
          [{"rule": "PERF", "file": f"{_J}/user/BulkLookupService.java"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "n-plus-one-lookup", hard=True),
    _case("pr-127-api-contract.diff",
          [{"rule": "BUG"},
           {"rule": "MISSING-TEST"}],
          2, "planted", "frozen-api-field-rename", hard=True),
    _case("pr-128-clean-fp-trap.diff", [], 0, "clean",
          "password-words-but-nothing-sensitive"),
]

# ---- 知識依賴題（Phase 2.2）：沒讀帳本就抓不到的缺陷 ----
#
# 三題「單頁」（kfix-1..3）：帳本只有一頁，注入必完整 —— 量的是 consumption
# 純度（reviewer 有沒有真的用上它、有沒有引 id）。
# 兩題「distractor」（kfix-4/5）：同 repo 另有 4–5 頁其他實體的頁，注入總量
# 推過 KNOWLEDGE_MAX_CHARS —— 量的是排序與預算有沒有參與（tier 4 保證入場、
# 降載成 invariants-only 而不是整條蒸發）。
# 兩型分開出分：delta=0 時才判得出是 consumption 問題還是排序問題。
KNOWLEDGE_DIR = FIXTURES / "knowledge_cases"


def _kcase(name, repo, title, description, expected, allowed_extra, kind, focus):
    return (name, repo, title, description,
            {"expected": expected, "allowed_extra": allowed_extra,
             "forbidden": _FORBID},
            {"fixture": name, "kind": "knowledge", "ktype": kind, "focus": focus,
             "lang": "java"})


KNOWLEDGE_CASES = [
    _kcase("kfix-1.diff", "kfix-1",
           "Apply the configured session timeout at startup",
           "The session TTL has been hard-coded since the edge rewrite. This "
           "change reads session.ttl.seconds from the properties file and "
           "pushes it into the cache when the loader starts, so operations can "
           "tune it per environment without a rebuild.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/session/SessionConfigLoader.java",
             "knowledge_ref": "PR41-I1"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "seconds passed into a millisecond boundary — only the ledger says so"),
    _kcase("kfix-2.diff", "kfix-2",
           "Retry transient charge failures",
           "Transient provider errors currently surface to the customer as a "
           "failed checkout. This adds a bounded retry with exponential backoff "
           "around the charge call.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/retry/RetryExecutor.java",
             "knowledge_ref": "PR52-I1"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "fresh idempotency key per attempt — double charge; ledger knows"),
    _kcase("kfix-3.diff", "kfix-3",
           "Move the cancellation audit write off the request thread",
           "Cancellation latency is dominated by the audit insert. The audit "
           "table is not on the hot path, so the write is now dispatched "
           "asynchronously.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/orders/OrderService.java",
             "knowledge_ref": "PR63-I2"},
            {"rule": "MISSING-TEST"}],
           2, "single-page",
           "audit escapes the mutating transaction — module page carries the rule"),
    _kcase("kfix-4.diff", "kfix-4",
           "Report the protocol name from each handler and route on it",
           "Each frame handler now reports the protocol it speaks so the router "
           "can prefer protocol-scoped zone entries over the plain zone entry.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/routing/ZoneRouter.java",
             "knowledge_ref": "PR75-I1"},
            {"rule": "MISSING-TEST"}],
           4, "distractor",
           "string reference equality; 6 matched pages blow the budget and the "
           "load-bearing page sorts last (tier-4 guarantee must save it)"),
    _kcase("kfix-5.diff", "kfix-5",
           "Add the tiered fee schedule",
           "This release introduces tiered fees. The calculator gains a tiered "
           "rate path, the writer appends the fee entry alongside its parent "
           "transaction, and statements render the tier label.",
           [{"rule": "BUG",
             "file": "src/main/java/com/acme/ledger/FeeCalculator.java",
             "knowledge_ref": "PR85-I3"},
            {"rule": "MISSING-TEST"}],
           4, "distractor",
           "double round-trip on money; the rule lives on a neighbour page that "
           "only survives the budget as invariants-only"),
]


# ---- 蒸餾考卷（Phase 4）：K 段的 LLM 輸出從此也押著考題 ----
#
# review pipeline 有 30 題押著，distiller 過去零考卷——改它的 prompt 等於裸奔。
# 三題陷阱刻意佔三成：why 缺席（編 rationale 就扣）、supersede 假訊號（推論就
# 扣）、幻覺不變式（材料撐不起 invariant 還蒸出來就扣）。invariants 不做
# evidence 驗證是明文接受的殘餘風險，第三題就是它的統計兜底。

DISTILL_DATASET = "pr-review-distill-golden"
DISTILL_DIR = FIXTURES / "distill"


def _dcase(fname, title, description, expected, focus, catalogue=None):
    return (fname, title, description, catalogue or [], expected,
            {"fixture": fname, "kind": "distill", "focus": focus})


DISTILL_CASES = [
    _dcase("d1-ttl-unit.diff",
           "Make the cache TTL boundary explicitly millisecond-based",
           "setTtl took an int of seconds while the field it wrote was already "
           "milliseconds, so every configured TTL expired a thousand times too "
           "early. We considered an overloaded setTtl(value, unit) instead, but "
           "rejected it: autoboxing picked the wrong overload at two call sites "
           "during the 2026.04 incident. The boundary is milliseconds, full "
           "stop, and it now rejects values below the floor.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["millisecond"],
            "primary_entity": "src/main/java/com/acme/cache/SessionCache.java"},
           "ordinary entry with a stated why and a rejected alternative"),
    _dcase("d2-retry-revert.diff",
           "Stop retrying writes in WriteClient",
           "This retires the write-retry policy introduced in PR#31. Retrying a "
           "non-idempotent write is what turned last month's partial outage into "
           "duplicated orders, so writes are now sent exactly once and the "
           "caller decides what a failure means. The rule that writes are "
           "retried up to five times no longer holds.",
           {"intent_stated": True, "retires_id": "PR31-I1",
            "must_mention": ["retr"],
            "primary_entity": "src/main/java/com/acme/http/WriteClient.java"},
           "explicit supersede — retires_id must hit the seeded id exactly",
           catalogue=[("acme/http/WriteClient.md", [
               ("PR31-I1", "Writes are retried up to five times with jitter; "
                           "callers must not implement their own retry loop."),
               ("PR31-I2", "The client never blocks the caller thread for more "
                           "than the configured request timeout.")])]),
    _dcase("d3-batch-size.diff",
           "Raise the import batch size to 500",
           "Bump BATCH_SIZE from 100 to 500.",
           {"intent_stated": False, "retires_id": None,
            "must_mention": ["batch"],
            "forbidden_mention": ["because", "in order to", "rationale",
                                  "so that"],
            "primary_entity":
                "src/main/java/com/acme/importer/BatchLoader.java"},
           "TRAP why-absent — description is pure what; inventing a why is a miss"),
    _dcase("d4-flag-cache.diff",
           "Batch feature-flag reads per request",
           "Reading flags one by one costs a store round-trip each. readAll "
           "fetches the whole set a request needs in one call. We talked about "
           "caching flags per instance instead, which would be faster still, "
           "but that is not what this change does — flags are still read from "
           "the store on every request.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["flag"],
            "primary_entity":
                "src/main/java/com/acme/config/FeatureFlags.java"},
           "TRAP supersede false signal — sounds like a reversal, never states one",
           catalogue=[("acme/config/FeatureFlags.md", [
               ("PR44-I1", "Feature flags are read per request, never cached "
                           "for the lifetime of an instance."),
               ("PR44-I2", "A flag that cannot be read fails closed.")])]),
    _dcase("d5-typo-only.diff",
           "Fix spelling in ReportBuilder",
           "Typos in a javadoc block and a TODO comment.",
           {"intent_stated": False, "retires_id": None, "must_mention": [],
            "forbid_invariants": True,
            "primary_entity":
                "src/main/java/com/acme/report/ReportBuilder.java"},
           "TRAP hallucinated invariant — nothing here constrains future change"),
    _dcase("d6-cache-library.diff",
           "Move LookupCache from Guava to Caffeine",
           "Guava's cache has no hit-rate instrumentation we can export, and we "
           "have been guessing at the sizing for two quarters. We chose "
           "Caffeine over both Guava and a hand-rolled map because it gives us "
           "recordStats() for free and keeps the same eviction semantics, so "
           "this is a drop-in with metrics rather than a behaviour change.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["caffeine", "guava"],
            "primary_entity": "src/main/java/com/acme/cache/LookupCache.java"},
           "rejected alternatives named in the description — PR evidence carries it"),
    _dcase("d7-sql-parameterize.diff",
           "Parameterize findByName",
           "The name argument was concatenated into the SQL string. It is now a "
           "bound parameter. Reported by the review bot on the previous PR; the "
           "same pattern exists nowhere else in this repository, which we "
           "checked before merging.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["parameter"],
            "primary_entity":
                "src/main/java/com/acme/user/UserRepository.java"},
           "security fix — evidence must resolve to lines in the excerpt"),
    _dcase("d8-api-compat.diff",
           "Expose available_balance and deprecate balance",
           "Partners read balance as if it were spendable, which it is not once "
           "holds are counted. available_balance is the new field. balance "
           "stays in the payload until 2027-01-01 because two partner "
           "integrations cannot ship a client change before then; removing it "
           "earlier is a breaking change we have already promised not to make.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["2027"],
            "primary_entity":
                "src/main/java/com/acme/api/AccountResponse.java"},
           "compatibility window — a date a diff alone cannot show"),
    _dcase("d9-primary-entity.diff",
           "Acquire both lock keys in a fixed order",
           "Two callers acquiring the same pair of keys in opposite orders "
           "deadlocked in production. acquireBoth sorts the keys first, which "
           "makes the order global. The integration test reproduces the old "
           "deadlock under contention.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["order"],
            "primary_entity": "src/main/java/com/acme/lock/LockService.java"},
           "the longest path is the test file — the page must anchor on the service"),
    _dcase("d10-two-decisions.diff",
           "Stream CSV export instead of building it in memory",
           "Month-end exports for the largest accounts exceeded the heap. The "
           "exporter now writes into a caller-supplied stream and flushes every "
           "FLUSH_EVERY rows. We deliberately do not close the sink: the caller "
           "owns it, and closing a servlet output stream here truncated the "
           "response during the first attempt.",
           {"intent_stated": True, "retires_id": None,
            "must_mention": ["stream", "flush"],
            "primary_entity": "src/main/java/com/acme/export/CsvExporter.java"},
           "two load-bearing decisions, one of them a negative decision"),
]


def seed_distill_dataset(lf) -> None:
    """蒸餾考卷 —— 與 golden set 同樣的逐條冪等規則（metadata.fixture 為鍵）。"""
    from orchestrator.knowledge import catalogue_text

    lf.create_dataset(name=DISTILL_DATASET)
    existing: set[str] = set()
    try:
        for it in lf.get_dataset(DISTILL_DATASET).items:
            if "ARCHIVED" in str(getattr(it, "status", "") or "").upper():
                continue
            m = it.metadata or {}
            if isinstance(m, dict) and m.get("fixture"):
                existing.add(str(m["fixture"]))
    except Exception:
        pass

    added = skipped = missing = 0
    for fname, title, desc, catalogue, expected, meta in DISTILL_CASES:
        if fname in existing:
            skipped += 1
            continue
        path = DISTILL_DIR / fname
        if not path.exists():
            print(f"略過 {fname}（fixtures/distill 裡沒有這個檔）")
            missing += 1
            continue
        # 目錄用 production 的同一支 renderer 排版：考卷看到的格式 = 真的那份
        cat = catalogue_text(
            [(rel, pairs) for rel, pairs in catalogue],
            "# Existing ledger invariants for the entities this PR touches\n"
            "# (supersede detection only — do NOT restate these as your own "
            "decisions; put the bracketed id in retires_id if one is retired)\n")
        lf.create_dataset_item(
            dataset_name=DISTILL_DATASET,
            input={"diff": path.read_text(encoding="utf-8"), "title": title,
                   "description": desc, "catalogue": cat},
            expected_output=expected,
            metadata=meta,
        )
        added += 1
    lf.flush()
    print(f"dataset '{DISTILL_DATASET}'：新增 {added} 條、已存在跳過 {skipped} 條"
          + (f"、缺檔 {missing} 條" if missing else ""))


def main() -> None:
    lf = get_client()

    # 1) prompts 資產化（UI 改版、code 零改動；每跑必升版）
    prompts = {
        PROMPT_NAME: DEFAULT_SYSTEM_PROMPT,             # v1 單呼叫
        PLANNER_PROMPT_NAME: DEFAULT_PLANNER_PROMPT,    # v2 規劃者
        REVIEWER_PROMPT_NAME: DEFAULT_REVIEWER_PROMPT,  # v2 通用 reviewer
        ADVERSARY_PROMPT_NAME: DEFAULT_ADVERSARY_PROMPT,  # v2 對抗者
        DISTILLER_PROMPT_NAME: DEFAULT_DISTILLER_PROMPT,  # K2 知識蒸餾
        DIGEST_PROMPT_NAME: DEFAULT_DIGEST_PROMPT,        # 週期 digest 顧問分析
    }
    for name, text in prompts.items():
        lf.create_prompt(name=name, type="text", prompt=text,
                         labels=["production"])
        print(f"prompt '{name}' 已推上 Langfuse（label: production）")

    # 2) golden dataset —— 逐條冪等（以 metadata.fixture 為鍵）
    lf.create_dataset(name=DATASET)
    existing: set[str] = set()
    try:
        for it in lf.get_dataset(DATASET).items:
            if "ARCHIVED" in str(getattr(it, "status", "") or "").upper():
                continue          # archive 過的不算存在 —— 允許重灌
            m = it.metadata or {}
            if isinstance(m, dict) and m.get("fixture"):
                existing.add(str(m["fixture"]))
    except Exception:
        pass

    added = skipped = missing = 0
    for fname, expected, meta in CASES:
        if fname in existing:
            skipped += 1
            continue
        path = FIXTURES / fname
        if not path.exists():
            print(f"略過 {fname}（fixtures 裡沒有這個檔）")
            missing += 1
            continue
        lf.create_dataset_item(
            dataset_name=DATASET,
            input={"diff": path.read_text(encoding="utf-8")},
            expected_output=expected,
            metadata=meta,
        )
        added += 1

    # 知識依賴題：同一個 dataset，但 input 多帶 repo（= 種子帳本目錄）與
    # description（沒有 description 就沒有「意圖 vs 實作」的張力可考）
    kadded = 0
    for fname, repo, title, desc, expected, meta in KNOWLEDGE_CASES:
        if fname in existing:
            skipped += 1
            continue
        path = KNOWLEDGE_DIR / fname
        if not path.exists():
            print(f"略過 {fname}（knowledge_cases 裡沒有這個檔）")
            missing += 1
            continue
        lf.create_dataset_item(
            dataset_name=DATASET,
            input={"diff": path.read_text(encoding="utf-8"), "repo": repo,
                   "title": title, "description": desc},
            expected_output=expected,
            metadata=meta,
        )
        kadded += 1
    lf.flush()
    print(f"dataset '{DATASET}'：新增 {added} 條 review 題 + {kadded} 條知識依賴題、"
          f"已存在跳過 {skipped} 條"
          + (f"、缺檔 {missing} 條" if missing else "")
          + " —— 去 UI 的 Datasets 確認")
    seed_distill_dataset(lf)


if __name__ == "__main__":
    main()
PATCH_EOF

write "tests/test_core.py" <<'PATCH_EOF'
"""確定性層的 pytest 鎖 —— 承重牆不能只靠系統級 eval 保護。
全部離線、零 LLM、零網路；harvest 的 ADO 薄封裝天生是 stub 點。
跑法：venv/bin/python -m pytest -q tests/
"""
import contextlib
import json
import pathlib
import tempfile

import asyncio
import types

import pytest

from orchestrator.anatomy import parse_diff


# ---------- anatomy ----------

def test_anatomy_line_numbering_and_added_set():
    diff = ("--- a/src/A.java\n+++ b/src/A.java\n@@ -10,4 +10,5 @@\n"
            " ctx\n ctx\n+added\n ctx\n ctx\n")
    ana = parse_diff(diff)
    fc = ana.files[0]
    assert fc.added_linenos == {12}
    assert [(t, n) for h in fc.hunks for t, n, _ in h.lines][2] == ("+", 12)


# ---------- diff 幾何 predicates（住 anatomy，pipeline / knowledge 共用） ----------

def test_match_path_segment_boundary():
    from orchestrator.anatomy import match_path
    assert match_path("src/cache/SessionCache.java", "cache/SessionCache.java")
    assert not match_path("src/cache/SessionCache.java", "Cache.java")


def test_line_near_vs_line_in_view():
    from orchestrator.anatomy import line_in_view, line_near
    diff = ("--- a/src/A.java\n+++ b/src/A.java\n@@ -10,7 +10,8 @@\n"
            " c10\n c11\n+a12\n c13\n c14\n c15\n c16\n c17\n")
    ana = parse_diff(diff)
    assert line_near(ana, "src/A.java", 13)           # added±2
    assert not line_near(ana, "src/A.java", 16)
    assert line_in_view(ana, "src/A.java", 16)         # context 行在視野


def test_slices_budget_reports_skipped(monkeypatch):
    from orchestrator import pipeline as pl
    monkeypatch.setenv("REVIEWER_SLICES_MAX_CHARS", "800")
    diff = "\n".join(
        f"--- /dev/null\n+++ b/src/f{i}.java\n@@ -0,0 +1,6 @@\n"
        + "\n".join(f"+line{k} of file {i} with some padding text" for k in range(6))
        for i in range(6)) + "\n"

    class D:
        files: list = []
        name = "X"
        focus = ""
        hypotheses: list = []

    text, skipped = pl._slices_for(D(), parse_diff(diff))
    assert skipped and "omitted" in text


# ---------- static：MISSING-TEST 保底跨語言 ----------

@pytest.mark.parametrize("fname,decl", [
    ("src/pkg/handler.go", "func HandlePing(w http.ResponseWriter) {"),
    ("src/app/Service.kt", "fun computeTotal(x: Int): Int {"),
    ("src/app/Svc.java", "public int run(int x) {"),
])
def test_missing_test_fires_per_language(fname, decl, monkeypatch):
    monkeypatch.setenv("STATIC_MISSING_TEST", "true")
    from orchestrator.static_pass import _missing_test
    diff = (f"--- /dev/null\n+++ b/{fname}\n@@ -0,0 +1,3 @@\n"
            f"+package x\n+{decl}\n+}}\n")
    finds = _missing_test(parse_diff(diff))
    assert finds and finds[0].rule == "MISSING-TEST"


# ---------- knowledge：K1 閘門 ----------

@pytest.fixture()
def kroot(monkeypatch):
    root = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_BRANCHES", "release/*,master")
    monkeypatch.setenv("KNOWLEDGE_CYCLE_SOURCE", "release/*")
    monkeypatch.setenv("KNOWLEDGE_CYCLE_TARGET", "master")
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "false")   # 測試零 LLM：顧問分析關
    return root


def test_gate_matrix(kroot):
    from orchestrator.knowledge import gate
    ok, _ = gate({"target_branch": "refs/heads/release/2026.09",
                  "source_branch": "refs/heads/feature/x",
                  "title": "fix NPE"}, ["/src/A.java"])
    assert ok
    assert not gate({"target_branch": "refs/heads/feature/y",
                     "source_branch": "refs/heads/z", "title": "t"}, ["/a"])[0]
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/x",
                     "title": "t"}, ["/pom.xml"])[0]
    assert gate({"target_branch": "refs/heads/master",
                 "source_branch": "refs/heads/x",
                 "title": 'Revert "x"'}, ["/pom.xml"])[0]
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/release/2026.09",
                     "title": "Release"}, ["/src/A.java"])[0]   # 驗收 PR 豁免
    assert not gate({"target_branch": "refs/heads/master",
                     "source_branch": "refs/heads/x",
                     "title": "Merge master into release/1.2"},
                    ["/src/A.java"])[0]                          # ADO merge-sync


# ---------- harvest：落地累積、未知段前滾、reject 邊界、watermark ----------

def _draft(entity, **kw):
    d = {"summary": "s", "type": "bugfix", "intent_stated": True,
         "primary_entity": entity, "decisions": [], "invariants": [],
         "affected": [], "supersedes": [], "ledger_line": "l"}
    d.update(kw)
    return d


def test_land_accumulates_and_keeps_unknown_sections(kroot):
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 55,
            _draft("src/a/cache/S.java", invariants=["inv-A"]),
            "cycle/silent", "")
    page = kroot / "repo-a" / "cache" / "S.md"
    page.write_text(page.read_text() + "\n## Ops runbook\n- 手工補的一段\n")
    H._land(kroot, "repo-a", 56,
            _draft("src/a/cache/S.java", invariants=["inv-B"]),
            "cycle/silent", "")
    body = page.read_text()
    assert "inv-A" in body and "inv-B" in body
    assert "Ops runbook" in body and "手工補的一段" in body
    assert "| PR#55 |" in body and "| PR#56 |" in body


def test_apply_rejects_boundary_and_ambiguity(kroot):
    from orchestrator import knowledge_harvest as H
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    for pid, ent in ((70, "src/x/FeeCalc.java"), (71, "src/y/FeeCalc.java")):
        (cyc / f"pr-{pid}.json").write_text(
            json.dumps({"draft": {"primary_entity": ent}}))
    staged = sorted(cyc.glob("*.json"))
    msgs, amb = H._apply_rejects({"eecalc.java"}, staged, "t")
    assert not msgs and not amb                       # 段邊界：不吸子字串
    msgs, amb = H._apply_rejects({"feecalc"}, staged, "t")
    assert amb and len(list(cyc.glob("*.json"))) == 2  # 歧義：全保留
    msgs, _ = H._apply_rejects({"x/feecalc.java"}, staged, "t")
    assert any("丟" in m for m in msgs)


def test_watermark_first_sight_no_backfill(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 90, "title": "ancient",
        "sourceRefName": "refs/heads/f", "targetRefName": "refs/heads/master",
        "closedDate": "2020-01-01T00:00:00Z"}])
    lines = H._watermark_scan(object(), kroot)
    assert any("首見" in x for x in lines)
    assert not (kroot / "repo-a" / "_module" / "unreviewed-ledger.md").exists()
    assert H._watermark_scan(object(), kroot) == []


def test_digest_state_merge_keeps_ambig(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    (cyc / "pr-70.json").write_text(json.dumps({
        "pr_url": "u", "pr_id": 70, "repo": "repo-a",
        "base_url": "https://x/C/P",
        "draft": _draft("src/x/F.java"), "author_note": "",
        "provenance": "silent"}))
    posted = []
    monkeypatch.setattr(H, "client",
                        lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: posted.append(t))
    monkeypatch.setattr(H, "_list_active", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master"}])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    state_f = kroot / "_state" / "cycle.json"
    state_f.write_text(json.dumps({"repo-a#500": {"ambig": ["feecalc"]}}))
    H._cycle_close(object(), kroot)
    st = json.loads(state_f.read_text())["repo-a#500"]
    assert st.get("digest") is True and st.get("ambig") == ["feecalc"]


# ---------- app：快取 commit 語意、meta 盾 ----------

def test_cache_commit_invalidation():
    from orchestrator import app as A
    A._RECENT.clear()
    A._cache_put("u1", "old-review", "sha-A")
    assert A._cache_get("u1", "sha-A") == "old-review"
    assert A._cache_get("u1", "sha-B") is None         # push 新 commit → 失效
    assert A._cache_get("u1", "") == "old-review"      # 無 hint → TTL 語意


def test_meta_shield_not_tripped_by_json_format():
    from orchestrator import app as A
    assert not A._is_meta_task({}, "please give me the json format of X")
    assert A._is_meta_task({"metadata": {"task": "title_generation"}}, "x")


# ---------- 考卷帳目：檔數 ≡ CASES ≡ 文件宣稱（防人腦算術） ----------

def test_fixture_count_matches_cases_and_readme():
    from eval.seed_langfuse import CASES, DISTILL_CASES, KNOWLEDGE_CASES
    fixtures = pathlib.Path(__file__).parent.parent / "eval" / "fixtures"
    diffs = sorted(f.name for f in fixtures.glob("*.diff"))
    assert len(diffs) == len(CASES) == 30
    case_files = {c[0] for c in CASES}
    assert case_files == set(diffs)          # 每題有檔、每檔有題
    readme = (pathlib.Path(__file__).parent.parent / "README.md").read_text(
        encoding="utf-8")
    assert "30 題 golden set" in readme      # 文件跟考卷同一個數

    # 知識依賴題：每題有 diff、有種子帳本目錄（少一個就退化成無知識對照組）
    kdiffs = sorted(f.name for f in (fixtures / "knowledge_cases").glob("*.diff"))
    assert len(kdiffs) == len(KNOWLEDGE_CASES) == 5
    assert {c[0] for c in KNOWLEDGE_CASES} == set(kdiffs)
    for _fname, repo, *_rest in KNOWLEDGE_CASES:
        assert (fixtures / "knowledge" / repo).is_dir()
    assert sum(1 for c in KNOWLEDGE_CASES if c[5]["ktype"] == "single-page") == 3
    assert sum(1 for c in KNOWLEDGE_CASES if c[5]["ktype"] == "distractor") == 2
    assert "5 題知識依賴題" in readme

    # 蒸餾考卷：10 題，其中至少三題是陷阱
    ddiffs = sorted(f.name for f in (fixtures / "distill").glob("*.diff"))
    assert len(ddiffs) == len(DISTILL_CASES) == 10
    assert {c[0] for c in DISTILL_CASES} == set(ddiffs)
    traps = [c for c in DISTILL_CASES if c[5]["focus"].startswith("TRAP")]
    assert len(traps) >= 3
    assert "10 題蒸餾考卷" in readme


def test_cycle_isolation_between_parallel_release_branches(kroot, monkeypatch):
    """release/N 收口只落地自己那批；release/N+1 的暫存稿留在池裡等自己的
    驗收 PR；無 target_branch 標記的舊稿視為萬用（升級不擱淺）。"""
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見（見水位測試）
        json.dumps({"repo-a#1": {"closed": True}}))
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True)
    for pid, ent, tb in ((70, "src/a/A.java", "release/2026.09"),
                         (71, "src/b/B.java", "release/2026.10"),
                         (72, "src/c/C.java", "")):          # legacy 無標記
        (cyc / f"pr-{pid}.json").write_text(json.dumps({
            "pr_url": "u", "pr_id": pid, "repo": "repo-a",
            "base_url": "https://x/C/P", "target_branch": tb,
            "draft": _draft(ent), "author_note": "", "provenance": "silent"}))
    monkeypatch.setattr(H, "client",
                        lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t2: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500,
        "sourceRefName": "refs/heads/release/2026.09",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])
    H._cycle_close(object(), kroot)
    assert (kroot / "repo-a" / "a" / "A.md").exists()        # 本週期 → 落地
    assert (kroot / "repo-a" / "c" / "C.md").exists()        # 萬用 → 落地
    assert not (kroot / "repo-a" / "b" / "B.md").exists()    # 下週期 → 留池
    assert [f.name for f in cyc.glob("*.json")] == ["pr-71.json"]


# ---------- 串流路徑端到端（同族 NameError 兩次得逞的共同根因＝這裡沒測） ----------

def test_stream_review_delivers_markdown_and_caches_commit(monkeypatch):
    from orchestrator import app as A
    A._RECENT.clear()

    async def f_meta(u):
        return {"repo": "r", "title": "t", "source_commit": "sha-X",
                "target_branch": "refs/heads/feature/x"}

    async def f_diff(u):
        return "--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"

    async def f_run(diff, md):
        return types.SimpleNamespace(markdown="## Pull Request Review OK",
                                     stats={"file_table": [],
                                            "graph_impacted": []})

    async def f_distill(*a, **k):
        return None

    monkeypatch.setattr(A.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(A.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(A, "run_review", f_run)
    monkeypatch.setattr(A.knowledge, "distill_and_stage", f_distill)
    monkeypatch.setenv("AUTO_POST", "false")

    async def consume():
        chunks = []
        async for c in A._stream_review("rid", "https://x/C/P/_git/R/pullrequest/9",
                                        "u", commit_hint="sha-X"):
            chunks.append(c)
        return "".join(chunks)

    out = asyncio.run(consume())
    assert "Pull Request Review OK" in out
    assert "❌" not in out                    # 全程無錯誤演出
    hit = A._RECENT["https://x/C/P/_git/R/pullrequest/9"]
    assert len(hit) == 3 and hit[2] == "sha-X"


# ---------- F 批：synthesis / coverage gate / verdict / scorer / cache 清理 ----------

def _fnd(rule="BUG", file="src/A.java", line=12, sev="warning",
         src="llm", conf=0.7, note="n"):
    from orchestrator.models import Finding
    f = Finding(rule=rule, file=file, line=line, severity=sev, note=note)
    f.source = src
    f.confidence = conf
    return f


def test_dedupe_corroborates_cross_source():
    from orchestrator.pipeline import _dedupe
    out = _dedupe([_fnd(src="static", conf=1.0), _fnd(src="llm", conf=0.7)])
    assert len(out) == 1
    assert out[0].confidence >= 1.0 - 1e-9        # 交叉命中不降信心


def test_synthesize_threshold_and_cap(monkeypatch):
    from orchestrator.pipeline import _synthesize
    monkeypatch.setenv("REVIEW_MAX_FINDINGS", "2")
    stats = {}
    out = _synthesize([_fnd(conf=0.9, file="a.java", note="keep1"),
                       _fnd(conf=0.8, file="b.java", note="keep2"),
                       _fnd(conf=0.7, file="c.java", note="capped"),
                       _fnd(conf=0.2, file="d.java", note="low")], stats)
    assert len(out) == 2 and stats["dropped_low_conf"] == 1


def test_coverage_gate_sweeps_orphans():
    from orchestrator.pipeline import _Dim, _coverage_gate
    from orchestrator.rules import RulesBundle
    diff = ("--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"
            "--- a/y.java\n+++ b/y.java\n@@ -1,1 +1,2 @@\n a\n+c\n")
    ana = parse_diff(diff)
    dims = [_Dim(name="Only-X", focus="f", hypotheses=[],
                 files=["x.java"], rules=[])]
    out = _coverage_gate(dims, ana, RulesBundle(), {})
    swept = [d for d in out if d is not dims[0]]
    assert swept and any("y.java" in f for d in swept for f in d.files)


def test_verdict_incomplete_when_all_reviewers_failed():
    from orchestrator.render import _verdict
    assert "REVIEW INCOMPLETE" in _verdict(
        [], {"reviewer_errors": 4, "static_findings": 0})
    assert "APPROVED" in _verdict([], {"reviewer_errors": 0,
                                       "static_findings": 3})


def test_scorer_reports_per_source_recall():
    from eval.run_eval import _score_v2
    expected = [{"rule": "SECURITY"}, {"rule": "MISSING-TEST"}]
    findings = [{"rule": "SECURITY", "source": "llm", "file": "a"},
                {"rule": "MISSING-TEST", "source": "static", "file": ""}]
    recall, _, _, comment, by_src = _score_v2(expected, findings, 2)
    assert recall == 1.0 and by_src == {"static": 1, "llm": 1}
    assert "static 1 / llm 1" in comment


def test_cache_cleanup_survives_three_tuples():
    from orchestrator import app as A
    A._RECENT.clear()
    for i in range(205):                       # F2：>200 觸發清理路徑
        A._cache_put(f"u{i}", "md", f"sha{i}")
    assert len(A._RECENT) >= 200               # 未過期不清、但絕不 ValueError


def test_anatomy_ignores_git_header_lines():
    diff = ("diff --git a/x.java b/x.java\n"
            "index 111..222 100644\n"
            "--- a/x.java\n+++ b/x.java\n@@ -1,2 +1,3 @@\n"
            " a\n+b\n c\n"
            "diff --git a/y.java b/y.java\n"
            "new file mode 100644\n"
            "--- /dev/null\n+++ b/y.java\n@@ -0,0 +1,1 @@\n+z\n")
    ana = parse_diff(diff)
    xs = [fc for fc in ana.files if fc.path.endswith("x.java")][0]
    nums = [n for h in xs.hunks for _, n, _ in h.lines if n is not None]
    assert max(nums) == 3                      # 不再出現幽靈第 4/5 行


def test_harvester_never_posts_shadow_or_legacy_drafts(kroot, monkeypatch):
    """影子模式（dry_run）與無標記存量稿：active PR + posted:false，
    harvester 必須陪等、絕不代貼；post_failed 的真失敗稿才補貼、且只一次。"""
    from orchestrator import knowledge_harvest as H
    posted = []
    monkeypatch.setattr(H, "_get_pr", lambda c, r: {"status": "active"})
    monkeypatch.setattr(H, "_post_thread",
                        lambda c, r, t2: posted.append(t2) or 77)
    pend_dir = kroot / "_pending" / "repo-a"
    pend_dir.mkdir(parents=True)

    def mk(pid, **extra):
        f = pend_dir / f"pr-{pid}.json"
        f.write_text(json.dumps({
            "pr_url": "u", "pr_id": pid, "repo": "repo-a",
            "base_url": "https://x/C/P", "distilled_commit": "sha",
            "author": "a", "thread_id": None, "posted": False,
            "draft": _draft("src/x/F.java"), "created": "t", **extra}))
        return f

    shadow = mk(70, dry_run=True, post_failed=False)
    legacy = mk(71)                                   # F7 修正前的舊稿：無標記
    failed = mk(72, dry_run=False, post_failed=True)
    for f in (shadow, legacy):
        out = H._process_pending(object(), kroot, f)
        assert "陪等" in out and not posted
        assert json.loads(f.read_text())["posted"] is False
    out = H._process_pending(object(), kroot, failed)
    assert "補救成功" in out and len(posted) == 1
    info = json.loads(failed.read_text())
    assert info["posted"] is True and info["thread_id"] == 77
    assert "陪等" in H._process_pending(object(), kroot, failed)  # 不重貼


# ---------- digest / 條目 ID：蒸餾時定、暫存→落地不變、reject 三粒度 ----------

def test_assign_ids_idempotent_and_legacy_shapes():
    from orchestrator.knowledge import assign_ids, entry_ids, norm_id, split_id
    d = {"decisions": [{"text": "a"}, {"id": "PR9-D7", "text": "b"}],
         "invariants": ["x", {"id": "PR9-I3", "text": "y"}, "", {"text": "z"}]}
    assign_ids(d, 12)
    assert [q["id"] for q in d["decisions"]] == ["PR12-D1", "PR9-D7"]
    assert d["invariants"] == [{"id": "PR12-I1", "text": "x"},
                               {"id": "PR9-I3", "text": "y"},
                               {"id": "PR12-I4", "text": "z"}]
    snap = json.dumps(d)
    assign_ids(d, 99)                              # 冪等：已有 id 一律不動
    assert json.dumps(d) == snap
    assert entry_ids(d, 12) == {"PR12", "PR12-D1", "PR9-D7",
                                "PR12-I1", "PR9-I3", "PR12-I4"}
    assert norm_id("pr12-i1") == "PR12-I1" and norm_id("PR12") == ""
    assert split_id("rule text `PR12-I1`") == ("rule text", "PR12-I1")
    assert split_id("legacy line") == ("legacy line", "")


def test_land_ids_retire_by_id_and_dedupe_by_text(kroot):
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants
    H._land(kroot, "repo-a", 55,
            _draft("src/a/cache/S.java", invariants=["inv-A", "inv-B"],
                   decisions=[{"text": "dec", "evidence": ["PR"]}]),
            "cycle/silent", "")
    page = kroot / "repo-a" / "cache" / "S.md"
    body = page.read_text()
    assert "- inv-A `PR55-I1`" in body and "- dec [PR] `PR55-D1`" in body
    cat = existing_invariants("repo-a", ["src/a/cache/S.java"])
    assert cat == [("repo-a/cache/S.md", [("PR55-I1", "inv-A"),
                                          ("PR55-I2", "inv-B")])]
    # PR#56：用 ID 註銷 inv-A、重述 inv-B（文字相同 → 不重複落）、新增 inv-C
    H._land(kroot, "repo-a", 56,
            _draft("src/a/cache/S.java", invariants=["inv-B", "inv-C"],
                   supersedes=[{"entity": "S", "what": "A gone",
                                "retires_id": "pr55-i1"}]),
            "cycle/explicit", "", rejected_ids=["PR56-D9"])
    body = page.read_text()
    assert "- ~~inv-A~~ ⚠ retired by PR#56 `PR55-I1`" in body
    assert body.count("inv-B") == 1 and "inv-C `PR56-I2`" in body
    assert "rejected: `PR56-D9`" in body
    # 已註銷的不再列入 catalogue；ID 跟著行走
    cat = existing_invariants("repo-a", ["src/a/cache/S.java"])
    assert cat[0][1] == [("PR55-I2", "inv-B"), ("PR56-I2", "inv-C")]
    # 舊格式（無 ID）行仍可用逐字相等註銷
    page.write_text(body.replace("- inv-C `PR56-I2`", "- legacy-rule"))
    H._land(kroot, "repo-a", 57, _draft(
        "src/a/cache/S.java",
        supersedes=[{"entity": "S", "what": "x", "retires_verbatim": "legacy-rule"}]),
        "cycle/silent", "")
    assert "~~legacy-rule~~ ⚠ retired by PR#57" in page.read_text()


def test_land_stem_collision_skip_returns_pair(kroot):
    """同 parent+stem、不同實體 → 分頁；重複落地走 skip 路徑必須回 2-tuple
    （原本回三個值，呼叫端 unpack 會炸、整個週期卡死）。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 60, _draft("src/a/cache/S.java"), "cycle/silent", "")
    msg, page = H._land(kroot, "repo-a", 61, _draft("src/b/cache/S.java"),
                        "cycle/silent", "")
    assert page.name.startswith("S-") and page.exists()
    msg2, page2 = H._land(kroot, "repo-a", 61, _draft("src/b/cache/S.java"),
                          "cycle/silent", "")
    assert msg2.startswith("skip") and page2 == page


def _stage(kroot, pid, ent, **kw):
    cyc = kroot / "_cycle" / "repo-a"
    cyc.mkdir(parents=True, exist_ok=True)
    (cyc / f"pr-{pid}.json").write_text(json.dumps({
        "pr_url": "u", "pr_id": pid, "repo": "repo-a",
        "base_url": "https://x/C/P", "target_branch": "release/x",
        "draft": _draft(ent, invariants=[f"rule-{pid}"], **kw),
        "author_note": "", "provenance": "silent"}))


def test_digest_first_delta_then_unlisted_on_close(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    posted, active, completed = [], [], []
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: posted.append(t))
    monkeypatch.setattr(H, "_list_active", lambda c, r: active)
    monkeypatch.setattr(H, "_list_completed", lambda c, r: completed)
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    acc = {"pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
           "targetRefName": "refs/heads/master", "closedDate": "z"}
    state_f = kroot / "_state" / "cycle.json"

    _stage(kroot, 70, "src/x/A.java")
    active.append(acc)
    H._cycle_close(object(), kroot)                       # 首次 digest
    assert len(posted) == 1 and "pending acceptance" in posted[0]
    assert "PR#70" in posted[0] and "`PR70-I1`" in posted[0]
    assert json.loads(state_f.read_text())["repo-a#500"]["listed"] == ["PR70"]

    H._cycle_close(object(), kroot)                       # 沒新稿：不重貼
    assert len(posted) == 1

    _stage(kroot, 71, "src/y/B.java")
    H._cycle_close(object(), kroot)                       # delta：只列新進
    assert len(posted) == 2 and "update: 1 new entry" in posted[1]
    assert "PR#71" in posted[1] and "`PR70-I1`" not in posted[1]
    assert json.loads(state_f.read_text())["repo-a#500"]["listed"] == ["PR70", "PR71"]

    _stage(kroot, 72, "src/z/C.java")                     # 驗收 PR 已完成才進池
    active.clear()
    completed.append(acc)
    lines = H._cycle_close(object(), kroot)
    assert len(posted) == 2                               # completed 不再貼 digest
    for stem in ("x/A", "y/B", "z/C"):
        assert (kroot / "repo-a" / f"{stem}.md").exists()
    receipt = next((kroot / "repo-a" / "_cycles").glob("*-PR500.md")).read_text()
    assert "| PR#72 | C | cycle/silent+unlisted |" in receipt
    assert "| PR#70 | A | cycle/silent |" in receipt
    assert "cycle/silent+unlisted" in (kroot / "repo-a" / "z" / "C.md").read_text()
    assert any("1 條從未出現在 digest" in ln for ln in lines)
    assert json.loads(state_f.read_text())["repo-a#500"]["closed"] is True


def test_reject_by_id_line_and_entry(kroot):
    from orchestrator import knowledge_harvest as H
    _stage(kroot, 70, "src/x/A.java",
           decisions=[{"text": "d1"}, {"text": "d2"}])
    _stage(kroot, 71, "src/y/B.java")
    cyc = kroot / "_cycle" / "repo-a"
    staged = sorted(cyc.glob("*.json"))
    msgs, amb = H._apply_rejects({"pr70-d2", "pr70-i1", "pr99-i1"}, staged, "t")
    assert not amb
    info = json.loads((cyc / "pr-70.json").read_text())
    assert [d["id"] for d in info["draft"]["decisions"]] == ["PR70-D1"]
    assert info["draft"]["invariants"] == []            # 行拿掉、條目還在
    assert info["rejected_ids"] == ["PR70-D2", "PR70-I1"]
    assert any("PR99-I1" in m and "查無" in m for m in msgs)
    msgs, _ = H._apply_rejects({"pr71"}, sorted(cyc.glob("*.json")), "t")
    assert any("整條丟" in m for m in msgs)
    assert [f.name for f in cyc.glob("*.json")] == ["pr-70.json"]


def test_digest_validate_and_render_offline():
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 70, "provenance": "silent", "author_note": "keep it",
              "draft": _draft("src/x/A.java",
                              invariants=[{"id": "PR70-I1", "text": "r70"}],
                              decisions=[{"id": "PR70-D1", "text": "d70",
                                          "evidence": ["PR"]}])},
             {"pr_id": 71, "provenance": "explicit", "author_note": "",
              "draft": _draft("src/y/B.java",
                              invariants=[{"id": "PR71-I1", "text": "r71"}],
                              supersedes=[{"entity": "A", "what": "w",
                                           "retires_id": "PR1-I1"}])}]
    known = D.known_ids(items, [("repo-a/x/A.md", [("PR1-I1", "old")])])
    assert {"PR70", "PR70-I1", "PR70-D1", "PR71", "PR71-I1", "PR1-I1"} <= known
    raw = {"headline": "  cycle  summary ",
           "flags": [{"kind": "Conflict", "ids": ["pr70-i1", "PR71-I1", "PR5-I9"],
                      "note": "a\nb", "suggest": "reject PR70-I1"},
                     {"kind": "duplicate", "ids": ["PR404-I1"], "note": "n"},
                     {"kind": "praise", "ids": ["PR70-I1"], "note": "n"},
                     {"kind": "unclear", "ids": ["PR71-I1"], "note": "n",
                      "suggest": "reject PR9-I9 now"},
                     "junk"]}
    out = D.validate(raw, known)
    assert out["headline"] == "cycle summary"
    assert [f["kind"] for f in out["flags"]] == ["conflict", "unclear"]
    assert out["flags"][0]["ids"] == ["PR70-I1", "PR71-I1"]     # 未知 ID 被濾
    assert out["flags"][0]["note"] == "a b"
    assert out["flags"][1]["suggest"] == ""                      # 非法建議只留 note
    assert D.validate(raw, known, max_flags=1)["flags"][0]["kind"] == "conflict"
    assert D.validate("nope", known) is None

    md = D.render(items, None, out)
    assert md.startswith(D.KMARK) and "pending acceptance** (2 entries)" in md
    assert "Needs a look" in md and "`PR70-I1` ↔ `PR71-I1`" in md
    assert "→ `reject PR70-I1`" in md and "_cycle summary_" in md
    assert "Rules being retired" in md and "retires `PR1-I1`" in md
    assert "- d70 [PR] `PR70-D1`" in md and "💬 author: keep it" in md
    assert "`reject PR12-I1`" in md                              # 指令說明
    md_delta = D.render(items, {"PR71"}, out, total=2)
    assert "update: 1 new entry" in md_delta and "PR#70 ·" not in md_delta
    assert "`PR70-I1` ↔ `PR71-I1`" in md_delta                   # 牽涉新進 PR → 留
    assert "**unclear**" in md_delta and "_cycle summary_" not in md_delta
    md_plain = D.render(items, None, None)
    assert "Needs a look" not in md_plain and "PR#70" in md_plain
    assert D.parse_reject_token("pr70-i1") == ("line", "PR70-I1")
    assert D.parse_reject_token("PR70") == ("entry", "PR70")
    assert D.parse_reject_token("x/A.java") == ("name", "x/A.java")


def test_analyze_degrades_to_none_and_respects_switch(monkeypatch):
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 1, "draft": _draft("a/B.java", invariants=["r"])}]
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "false")
    assert D.analyze("repo-a", items) is None
    monkeypatch.setenv("KNOWLEDGE_DIGEST_LLM", "true")
    monkeypatch.setattr(D, "complete_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    log = []
    assert D.analyze("repo-a", items, log=log.append) is None
    assert log and "分析失敗" in log[0]
    monkeypatch.setattr(D, "complete_sync", lambda *a, **k:
                        '{"headline":"h","flags":[{"kind":"unclear","ids":["PR1-I1"],"note":"n"}]}')
    out = D.analyze("repo-a", items)
    assert out["flags"][0]["ids"] == ["PR1-I1"]


def test_write_json_atomic_leaves_no_tmp(tmp_path=None):
    from orchestrator.knowledge import write_json_atomic
    d = pathlib.Path(tempfile.mkdtemp()) / "nested"
    write_json_atomic(d / "pr-1.json", {"a": 1})
    write_json_atomic(d / "pr-1.json", {"a": 2})
    assert json.loads((d / "pr-1.json").read_text()) == {"a": 2}
    assert [p.name for p in d.iterdir()] == ["pr-1.json"]


def test_mark_superseded_locates_page_by_id(kroot):
    """retires_id 命中時不走 stem 猜頁：entity 亂填也蓋對頁。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 40, _draft("src/a/lock/LockService.java",
                                        invariants=["both keys"]), "cycle/silent", "")
    msg = H._mark_superseded(kroot, "repo-a",
                             {"entity": "com.acme.Whatever", "what": "w",
                              "retires_id": "PR40-I1"}, 41)
    assert "PR40-I1 定位" in msg
    body = (kroot / "repo-a" / "lock" / "LockService.md").read_text()
    assert "Superseded (PR#41" in body


def test_cycle_close_waits_for_pending_of_same_branch(kroot, monkeypatch):
    """驗收 PR 完成、池子空、但還有小 PR 未終態且指向本週期分支 → 不標 closed，
    等它進池後照常落地（原本會永久孤兒）。"""
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見（見水位測試）
        json.dumps({"repo-a#1": {"closed": True}}))
    pend = kroot / "_pending" / "repo-a"
    pend.mkdir(parents=True)
    (pend / "pr-80.json").write_text(json.dumps({
        "pr_id": 80, "repo": "repo-a", "target_branch": "release/x",
        "draft": _draft("src/q/Q.java")}))
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])
    lines = H._cycle_close(object(), kroot)
    st = json.loads((kroot / "_state" / "cycle.json").read_text())
    assert not st.get("repo-a#500", {}).get("closed")
    assert any("暫不收口" in ln for ln in lines)
    (pend / "pr-80.json").unlink()                     # 小 PR 終態 → 進池
    _stage(kroot, 80, "src/q/Q.java")
    H._cycle_close(object(), kroot)
    assert (kroot / "repo-a" / "q" / "Q.md").exists()
    assert json.loads((kroot / "_state" / "cycle.json").read_text())["repo-a#500"]["closed"]


# ========== Phase 1：蒸餾證據地板（切片 / evidence 驗證 / primary 錨點） ==========

_EV_DIFF = ("--- a/src/main/java/com/acme/cache/SessionCache.java\n"
            "+++ b/src/main/java/com/acme/cache/SessionCache.java\n"
            "@@ -10,4 +10,6 @@\n"
            " ctx10\n ctx11\n+added12\n+added13\n ctx14\n ctx15\n")


def test_verify_evidence_matrix():
    """四種 evidence 形式 × 過/不過；剝光的 decision 整條不入稿。"""
    from orchestrator.anatomy import Anatomy
    from orchestrator.knowledge import verify_evidence
    ana = parse_diff(_EV_DIFF)
    long_desc = "x" * 60
    data = {"decisions": [
        {"text": "keeps a real line", "evidence": ["SessionCache.java:12"]},
        {"text": "keeps a finding id", "evidence": ["F2"]},
        {"text": "keeps a PR-backed rationale", "evidence": ["PR"]},
        {"text": "mixed: one good one bogus",
         "evidence": ["Nope.java:12", "SessionCache.java:14"]},
        {"text": "line outside the excerpt", "evidence": ["SessionCache.java:99"]},
        {"text": "file not in this diff", "evidence": ["Other.java:12"]},
        {"text": "finding id we never issued", "evidence": ["F9"]},
        {"text": "no evidence at all", "evidence": []},
        {"text": "free-form prose", "evidence": ["see the diff"]},
    ]}
    out, dropped = verify_evidence(data, ana, {"F1", "F2"}, long_desc)
    kept = [d["text"] for d in out["decisions"]]
    assert kept == ["keeps a real line", "keeps a finding id",
                    "keeps a PR-backed rationale", "mixed: one good one bogus"]
    assert out["decisions"][3]["evidence"] == ["SessionCache.java:14"]  # 壞的被剝
    assert len(dropped) == 5 and "no evidence at all" in dropped

    # "PR" 需要有內容的 description（去空白後 >= 40 字）
    thin, _ = verify_evidence({"decisions": [{"text": "t", "evidence": ["PR"]}]},
                              ana, set(), "too short")
    assert thin["decisions"] == []
    # 沒有 diff 可比對時 file:line 一律不過（寧可少收，不可假證據）
    blind, _ = verify_evidence(
        {"decisions": [{"text": "t", "evidence": ["SessionCache.java:12"]}]},
        Anatomy(), set(), long_desc)
    assert blind["decisions"] == []


def test_distill_slices_priority_and_budget(monkeypatch):
    """優先序：新增最多 → finding 引到的 → 有新宣告的 → 其餘；超預算留註記。"""
    from orchestrator.knowledge import _distill_order, distill_slices

    def f(path, adds, decl=False):
        body = "\n".join((f"+    public void m{i}() {{" if decl else f"+ line{i}")
                         for i in range(adds))
        return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{adds} @@\n{body}\n"

    ana = parse_diff(f("src/Big.java", 12) + f("src/Cited.java", 3)
                     + f("src/Decl.java", 3, decl=True) + f("src/Plain.java", 3))
    order = [fc.path for fc in _distill_order(ana, ["src/Cited.java"])]
    assert order == ["/src/Big.java", "/src/Cited.java", "/src/Decl.java",
                     "/src/Plain.java"]
    monkeypatch.setenv("DISTILL_DIFF_MAX_CHARS", "400")
    text, skipped = distill_slices(ana, ["src/Cited.java"])
    assert skipped and "omitted from the diff excerpt" in text
    assert "/src/Big.java" not in skipped          # 最高優先序不會被省掉


def test_primary_entity_prefers_added_lines_over_path_length():
    """舊 fallback 取『路徑最長』——那通常是測試檔。改取新增行數最多的原始碼檔。"""
    from orchestrator.knowledge import fix_primary_entity, pick_primary_entity
    diff = ("--- /dev/null\n+++ b/src/main/java/com/acme/lock/LockService.java\n"
            "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
            "--- /dev/null\n"
            "+++ b/src/test/java/com/acme/lock/integration/LockServiceIT.java\n"
            "@@ -0,0 +1,8 @@\n" + "\n".join(f"+t{i}" for i in range(8)) + "\n"
            "--- /dev/null\n+++ b/pom.xml\n@@ -0,0 +1,9 @@\n"
            + "\n".join(f"+<dep{i}/>" for i in range(9)) + "\n")
    ana = parse_diff(diff)
    assert pick_primary_entity(ana, []).endswith("LockService.java")
    d = {"primary_entity": "src/main/java/com/acme/lock/LockService.java"}
    assert fix_primary_entity(d, ana, []) is False        # 在變更檔內 → 不動
    d = {"primary_entity": "com.acme.SomewhereElse"}
    assert fix_primary_entity(d, ana, []) is True         # 不在 → fallback
    assert d["primary_entity"].endswith("LockService.java")


# ========== Phase 2：引用率（注入 id 名單 → knowledge_ref → 確定性執法） ==========

def _seed_page(kroot, repo, rel, entity, invariants, affected=(), pad=0):
    p = kroot / repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {p.stem}", "", f"> entity: {entity}",
            "> approval: seed · last: PR#9 · 2026-08-01", "",
            "## Summary", "s" * pad if pad else "seed page", "", "## Invariants"]
    body += [f"- {t} `{i}`" for i, t in invariants]
    if affected:
        body += ["", "## Affected entities", ", ".join(f"`{a}`" for a in affected)]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def test_load_for_reports_only_injected_ids(kroot):
    from orchestrator.knowledge import load_for
    _seed_page(kroot, "repo-a", "cache/SessionCache.md",
               "src/main/java/com/acme/cache/SessionCache.java",
               [("PR41-I1", "ttl is millis")])
    text, stats = load_for("repo-a",
                           ["src/main/java/com/acme/cache/SessionCache.java"])
    assert stats["knowledge_ids"] == ["PR41-I1"]      # 尾註的說明文字不算數
    assert stats["knowledge_full"] == 1 and stats["knowledge_omitted"] == 0
    assert "knowledge_ref" in text                    # 引用合約有講給模型聽
    assert load_for("repo-a",
                    ["src/other/Unrelated.java"])[1]["knowledge_entries"] == 0


def test_synthesize_verifies_knowledge_refs():
    from orchestrator.pipeline import _synthesize
    good = _fnd(file="a.java", note="grounded")
    good.knowledge_ref = ["pr41-i1", "PR99-I9"]       # 後者沒被注入過 → 剝掉
    bare = _fnd(file="b.java", note="ungrounded")
    stats = {"knowledge_ids": ["PR41-I1"]}
    out = _synthesize([good, bare], stats)
    assert good.knowledge_ref == ["PR41-I1"]
    assert stats["knowledge_grounded"] == 1 and stats["knowledge_refs_stripped"] == 1
    assert stats["knowledge_ids_injected"] == 1
    assert len(out) == 2


def test_dedupe_keeps_knowledge_ref_of_the_dropped_twin():
    from orchestrator.pipeline import _dedupe
    keep = _fnd(conf=0.9, note="kept")
    twin = _fnd(conf=0.7, note="dup with a citation")
    twin.knowledge_ref = ["PR41-I1"]
    out = _dedupe([keep, twin])
    assert len(out) == 1 and out[0].knowledge_ref == ["PR41-I1"]


def test_render_shows_knowledge_citation():
    from orchestrator.render import render_markdown
    f = _fnd(note="ttl unit is wrong", sev="error")
    f.knowledge_ref = ["PR41-I1"]
    md = render_markdown([f], {"files": 1, "added_lines": 3,
                               "knowledge_entries": 1, "knowledge_grounded": 1})
    assert "(knowledge: PR41-I1)" in md
    assert "1 finding(s) cite a ledger entry" in md


def test_score_knowledge_requires_both_catch_and_citation():
    from eval.run_eval import _score_knowledge
    expected = [{"rule": "BUG", "file": "src/A.java", "knowledge_ref": "PR41-I1"}]
    caught_and_cited = [{"rule": "BUG", "file": "src/A.java",
                         "knowledge_ref": ["PR41-I1"]}]
    caught_only = [{"rule": "BUG", "file": "src/A.java", "knowledge_ref": []}]
    cited_elsewhere = [{"rule": "NAMING", "file": "src/A.java",
                        "knowledge_ref": ["PR41-I1"]}]
    assert _score_knowledge(expected, caught_and_cited)[0] == 1.0
    assert _score_knowledge(expected, caught_only)[0] == 0.0
    assert _score_knowledge(expected, cited_elsewhere)[0] == 0.0
    assert _score_knowledge([{"rule": "BUG"}], caught_only) is None   # 非知識題


# ========== Phase 3：檢索排序（tier）與分級降載 ==========

def test_page_tier_ordering(kroot):
    from orchestrator.knowledge import _MatchCtx
    changed = ["src/main/java/com/acme/cache/SessionCache.java",
               "src/main/java/com/acme/pricing/PricingService.java"]
    ctx = _MatchCtx(changed)
    exact = _seed_page(kroot, "r", "cache/SessionCache.md", changed[0],
                       [("PR1-I1", "x")])
    # 同 (parent, stem) 但住在另一棵子樹的實體：檔名命中，不是精確路徑命中
    other = _seed_page(kroot, "r", "pricing/PricingService.md",
                       "src/legacy/pricing/PricingService.java", [("PR2-I1", "y")])
    neigh = _seed_page(kroot, "r", "cfg/CacheConfig.md",
                       "src/main/java/com/acme/cfg/CacheConfig.java",
                       [("PR3-I1", "z")], affected=["SessionCache.java"])
    mod = _seed_page(kroot, "r", "_module/cache.md", "(module)", [("PR4-I1", "w")])
    miss = _seed_page(kroot, "r", "far/Unrelated.md",
                      "src/main/java/com/acme/far/Unrelated.java", [("PR5-I1", "v")])

    def tier(page):
        return ctx.page_hit(page, page.relative_to(kroot / "r"),
                            page.read_text(encoding="utf-8"))

    assert tier(exact) == 4
    assert tier(other) == 3          # 同 (parent, stem)，但 entity 路徑不同
    assert tier(neigh) == 2          # Affected entities 鄰接
    assert tier(mod) == 1            # _module 路徑段
    assert tier(miss) == 0
    assert ctx.entry_tier(changed[0], []) == 4
    assert ctx.entry_tier("src/legacy/pricing/PricingService.java", []) == 3
    assert ctx.entry_tier("src/x/SessionCache.java", []) == 3
    assert ctx.entry_tier("src/x/Nope.java", ["SessionCache.java"]) == 2
    assert ctx.entry_tier("src/x/Nope.java", []) == 0


def test_load_for_guarantees_primary_and_degrades_the_rest(kroot, monkeypatch):
    """預算極小時：tier 4 仍保證入場（降為 Invariants 段），較弱的命中留註記。"""
    from orchestrator.knowledge import load_for
    changed = ["src/main/java/com/acme/cache/SessionCache.java"]
    _seed_page(kroot, "repo-a", "cache/SessionCache.md", changed[0],
               [("PR41-I1", "ttl is millis")], pad=4000)
    # 鄰接命中的那頁連「只注入 Invariants 段」都塞不下 → 它才是被省略的那個
    _seed_page(kroot, "repo-a", "cfg/CacheConfig.md",
               "src/main/java/com/acme/cfg/CacheConfig.java",
               [("PR42-I1", "config is seconds " + "y" * 1200)],
               affected=["SessionCache.java"], pad=4000)
    monkeypatch.setenv("KNOWLEDGE_MAX_CHARS", "900")
    text, stats = load_for("repo-a", changed)
    assert stats["knowledge_full"] == 0
    assert stats["knowledge_invariants_only"] == 1        # tier 4 保證入場
    assert stats["knowledge_omitted"] == 1
    assert "PR41-I1" in stats["knowledge_ids"]
    assert "PR42-I1" not in stats["knowledge_ids"]
    assert "invariants only" in text and "more matched entries omitted" in text
    assert "CacheConfig" in text.split("more matched entries omitted")[1]


def test_load_for_orders_by_tier_not_by_walk_order(kroot):
    from orchestrator.knowledge import load_for
    changed = ["src/main/java/com/acme/zone/ZoneRouter.java"]
    _seed_page(kroot, "repo-a", "aaa/Neighbour.md",
               "src/main/java/com/acme/aaa/Neighbour.java",
               [("PR10-I1", "neighbour rule")], affected=["ZoneRouter.java"])
    _seed_page(kroot, "repo-a", "zone/ZoneRouter.md", changed[0],
               [("PR20-I1", "compare with equals")])
    text, _ = load_for("repo-a", changed)
    assert text.index("ZoneRouter") < text.index("Neighbour")   # 精確命中在前


# ========== Phase 4：蒸餾考卷的確定性計分（含三題陷阱的判法） ==========

def _distill_expect(**kw):
    exp = {"intent_stated": True, "retires_id": None, "must_mention": [],
           "primary_entity": "src/main/java/com/acme/cache/SessionCache.java"}
    exp.update(kw)
    return exp


def _distill_draft(**kw):
    d = {"summary": "ttl boundary is milliseconds", "type": "bugfix",
         "intent_stated": True,
         "primary_entity": "src/main/java/com/acme/cache/SessionCache.java",
         "decisions": [{"text": "chose millisecond boundary",
                        "evidence": ["SessionCache.java:12"]}],
         "invariants": [], "supersedes": [], "affected": [],
         "ledger_line": "ttl unit fixed"}
    d.update(kw)
    return d


def _clean_info(**kw):
    info = {"dropped": [], "evidence_stripped": 0, "primary_fixed": False}
    info.update(kw)
    return info


def test_score_distill_perfect_paper():
    from eval.run_distill_eval import score_distill
    rows, score = score_distill(_distill_expect(must_mention=["millisecond"]),
                                _distill_draft(), _clean_info(), [])
    assert score == 1.0
    assert {n for n, _, _ in rows} == {"intent_ok", "retires_ok", "evidence_ok",
                                       "must_mention", "primary_ok"}


def test_score_distill_traps():
    """三題陷阱各自的判法：編 rationale / 推論 supersede / 幻覺不變式。"""
    from eval.run_distill_eval import score_distill

    def val(rows, name):
        return next(v for n, v, _ in rows if n == name)

    # ① why 缺席：intent_stated 說了謊，且條目裡冒出 rationale 措辭
    rows, _ = score_distill(
        _distill_expect(intent_stated=False,
                        forbidden_mention=["because", "so that"]),
        _distill_draft(intent_stated=True,
                       decisions=[{"text": "raised the batch size because "
                                           "throughput was low",
                                   "evidence": ["SessionCache.java:12"]}]),
        _clean_info(), [])
    assert val(rows, "intent_ok") == 0.0 and val(rows, "no_fabrication") == 0.0

    # ② supersede 假訊號：沒有明示反轉卻填了 retires_id（誤殺一條還有效的規則）
    rows, _ = score_distill(
        _distill_expect(),
        _distill_draft(supersedes=[{"entity": "FeatureFlags",
                                    "what": "per-request reads",
                                    "retires_id": "PR44-I1"}]),
        _clean_info(), [])
    assert val(rows, "retires_ok") == 0.0
    # 反過來：該點名卻點錯 ID —— 一樣扣（誤殺別條比漏標更貴）
    rows, _ = score_distill(
        _distill_expect(retires_id="PR31-I1"),
        _distill_draft(supersedes=[{"entity": "WriteClient", "what": "retries",
                                    "retires_id": "PR31-I2"}]),
        _clean_info(), [])
    assert val(rows, "retires_ok") == 0.0

    # ③ 幻覺不變式：材料撐不起任何 invariant，蒸出來就扣
    rows, _ = score_distill(
        _distill_expect(forbid_invariants=True),
        _distill_draft(invariants=[{"id": "PR1-I1", "text": "javadoc must be "
                                                            "spelled correctly"}]),
        _clean_info(), [])
    assert val(rows, "no_hallucination") == 0.0


def test_score_distill_evidence_and_primary():
    from eval.run_distill_eval import score_distill

    def val(rows, name):
        return next(v for n, v, _ in rows if n == name)

    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(evidence_stripped=1), [])
    assert val(rows, "evidence_ok") == 0.0            # 有 evidence 被剝 = 沒過關
    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(dropped=["a decision with no proof"]), [])
    assert val(rows, "evidence_ok") == 0.0
    rows, _ = score_distill(_distill_expect(), _distill_draft(),
                            _clean_info(primary_fixed=True), [])
    assert val(rows, "primary_ok") == 0.0             # 靠 fallback 救回不算分
    rows, _ = score_distill(
        _distill_expect(primary_entity="src/main/java/com/acme/other/X.java"),
        _distill_draft(), _clean_info(), [])
    assert val(rows, "primary_ok") == 0.0             # 錨錯實體
    rows, _ = score_distill(_distill_expect(must_mention=["millisecond", "floor"]),
                            _distill_draft(), _clean_info(), [])
    assert val(rows, "must_mention") == 0.5


# ========== Phase 5：stale 重蒸（請求戳 / 重試上限 / 覆寫 / provenance） ==========

def _pending(kroot, pid=90, **extra):
    from orchestrator.knowledge import write_json_atomic
    f = kroot / "_pending" / "repo-a" / f"pr-{pid}.json"
    write_json_atomic(f, {
        "pr_url": f"https://x/C/P/_git/repo-a/pullrequest/{pid}",
        "pr_id": pid, "repo": "repo-a", "base_url": "https://x/C/P",
        "target_branch": "release/x", "distilled_commit": "sha-OLD",
        "author": "a", "thread_id": 7, "posted": True, "dry_run": False,
        "draft": _draft("src/x/F.java"), "created": "t", **extra})
    return f


def _completed_pr(merge="sha-NEW"):
    return {"status": "completed", "lastMergeSourceCommit": {"commitId": merge},
            "createdBy": {"id": "author-1"}}


def test_stale_requests_redistill_then_gives_up(kroot, monkeypatch):
    """戳 + attempts ≤ 2：30 分鐘一輪的 timer 不會變成無界的 LLM 重試迴圈。"""
    from orchestrator import knowledge_harvest as H
    calls = []
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill",
                        lambda url: (calls.append(url) or (True, "已請求重蒸")))
    pend = _pending(kroot)

    out = H._process_pending(object(), kroot, pend)
    assert "請求重蒸（第 1/2 次" in out and len(calls) == 1
    stamp = json.loads(pend.read_text(encoding="utf-8"))["redistill_requested"]
    assert stamp == {"commit": "sha-NEW", "attempts": 1}
    assert pend.exists()                              # 稿留著等重蒸

    out = H._process_pending(object(), kroot, pend)   # 背景失敗了 → 再一次
    assert "第 2/2 次" in out and len(calls) == 2
    assert json.loads(pend.read_text(encoding="utf-8"))["redistill_requested"] \
        == {"commit": "sha-NEW", "attempts": 2}

    out = H._process_pending(object(), kroot, pend)   # 兩次都沒成 → 丟稿
    assert "重蒸兩次未成" in out and not pend.exists() and len(calls) == 2


def test_stale_gives_up_when_a_newer_push_lands_mid_redistill(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill", lambda url: (True, "ok"))
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr("sha-NEWER"))
    pend = _pending(kroot, redistill_requested={"commit": "sha-NEW", "attempts": 1})
    out = H._process_pending(object(), kroot, pend)
    assert "世代變更" in out and not pend.exists()


def test_stale_falls_back_to_dropping_when_redistill_is_off(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setenv("KNOWLEDGE_REDISTILL", "false")
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_request_redistill",
                        lambda url: pytest.fail("關掉了還打 endpoint"))
    pend = _pending(kroot)
    out = H._process_pending(object(), kroot, pend)
    assert "stale 丟稿" in out and "KNOWLEDGE_REDISTILL=false" in out
    assert not pend.exists()


def test_stale_keeps_author_reply_for_the_redistill_material(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr())
    monkeypatch.setattr(H, "_request_redistill", lambda url: (True, "ok"))
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [{
        "id": 7, "comments": [
            {"author": {"id": "bot"}, "content": "draft"},
            {"author": {"id": "author-1"}, "content": "we chose X over Y"}]}])
    pend = _pending(kroot)
    H._process_pending(object(), kroot, pend)
    assert json.loads(pend.read_text(encoding="utf-8"))["author_note"] \
        == "we chose X over Y"


def test_redistilled_draft_stages_with_visible_provenance(kroot, monkeypatch):
    """重蒸過的稿落地時 provenance 攤開說：內容是重蒸來的、作者有沒有說過話。"""
    from orchestrator import knowledge_harvest as H
    monkeypatch.setattr(H, "_get_pr", lambda c, r: _completed_pr("sha-NEW"))
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])   # thread 讀不到
    pend = _pending(kroot, distilled_commit="sha-NEW", redistilled=True,
                    author_note="kept from the earlier draft")
    out = H._process_pending(object(), kroot, pend)
    assert "staged（explicit·redistilled" in out
    staged = json.loads((kroot / "_cycle" / "repo-a" / "pr-90.json")
                        .read_text(encoding="utf-8"))
    assert staged["provenance"] == "explicit·redistilled"
    assert staged["author_note"] == "kept from the earlier draft"

    pend2 = _pending(kroot, pid=91, distilled_commit="sha-NEW", redistilled=True)
    H._process_pending(object(), kroot, pend2)
    staged2 = json.loads((kroot / "_cycle" / "repo-a" / "pr-91.json")
                         .read_text(encoding="utf-8"))
    assert staged2["provenance"] == "silent·redistilled"      # 沒人說過話


def test_redistill_overwrites_pending_and_clears_stale_traces(kroot, monkeypatch):
    """重蒸：最終 diff 重蒸 → 覆寫 _pending、回覆進原 thread、清掉舊的『已列』。"""
    from orchestrator import knowledge as K
    pend = _pending(kroot, author_note="the author said this earlier",
                    redistill_requested={"commit": "sha-NEW", "attempts": 1})
    # 舊的暫存稿 + 已列狀態（手動補蒸的情境）：都必須被作廢
    K.write_json_atomic(kroot / "_cycle" / "repo-a" / "pr-90.json",
                        {"pr_id": 90, "rejected_ids": ["PR90-I1"],
                         "draft": _draft("src/x/F.java")})
    K.write_json_atomic(kroot / "_state" / "cycle.json",
                        {"repo-a#500": {"listed": ["PR70", "PR90"],
                                        "digest": True}})
    diff = ("--- a/src/x/F.java\n+++ b/src/x/F.java\n@@ -10,3 +10,4 @@\n"
            " c10\n+a11\n c12\n c13\n")
    seen = {}

    async def f_meta(u):
        return {"title": "t", "description": "d" * 60, "source_commit": "sha-NEW",
                "target_branch": "release/x"}

    async def f_diff(u):
        return diff

    async def f_post(pr_url, markdown, thread_id=None):
        seen["thread_id"] = thread_id
        seen["markdown"] = markdown
        return {"posted": True, "dry_run": False, "reply": True,
                "thread_id": thread_id}

    async def f_complete(system, user, **kw):
        seen["user"] = user
        return json.dumps({
            "summary": "redistilled from the final diff", "type": "bugfix",
            "intent_stated": True, "primary_entity": "src/x/F.java",
            "decisions": [{"text": "kept the guard", "evidence": ["F.java:11"]},
                          {"text": "no proof at all", "evidence": ["F2"]}],
            "invariants": ["the guard stays"], "supersedes": [], "affected": [],
            "ledger_line": "guard kept"})

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(K.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(K.clients, "post_knowledge_draft", f_post)
    monkeypatch.setattr(K, "prompt_or", lambda name, default: "SYSTEM")
    monkeypatch.setattr(K, "complete", f_complete)

    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))

    info = json.loads(pend.read_text(encoding="utf-8"))
    assert info["redistilled"] is True and info["redistill_of"] == "sha-OLD"
    assert info["distilled_commit"] == "sha-NEW"
    assert "redistill_requested" not in info                  # 戳被消化掉了
    assert info["draft"]["summary"] == "redistilled from the final diff"
    assert [d["id"] for d in info["draft"]["decisions"]] == ["PR90-D1"]
    assert info["distill_dropped"] == ["no proof at all"]      # F 編號已 stale
    assert info["evidence_verified"] is True
    # 舊痕跡作廢：暫存稿移除、listed 移除 PR90（下一則 delta 會重新列它）
    assert not (kroot / "_cycle" / "repo-a" / "pr-90.json").exists()
    state = json.loads((kroot / "_state" / "cycle.json").read_text(encoding="utf-8"))
    assert state["repo-a#500"]["listed"] == ["PR70"]
    # 材料：帶作者原話、不含舊 review 報告
    assert "the author said this earlier" in seen["user"]
    assert "Review report" not in seen["user"]
    assert seen["thread_id"] == 7                              # 回覆進原 thread
    assert "1 decision(s) dropped" in seen["markdown"]


def test_redistill_is_once_per_pr(kroot, monkeypatch):
    from orchestrator import knowledge as K
    pend = _pending(kroot, redistilled=True)
    before = pend.read_text(encoding="utf-8")

    async def boom(*a, **k):
        raise AssertionError("已重蒸過的稿不該再打 ADO")

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", boom)
    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))
    assert pend.read_text(encoding="utf-8") == before


def test_knowledge_draft_thread_reply_is_a_reply_not_a_new_thread(monkeypatch):
    from tools.review_result_tools import server as S
    monkeypatch.setenv("KNOWLEDGE_POST", "false")
    url = "https://x/C/P/_git/repo-a/pullrequest/90"
    call = getattr(S.post_knowledge_draft, "fn", S.post_knowledge_draft)
    fresh = call(url, "body")
    reply = call(url, "body", thread_id=7)
    assert fresh["would_post_to"].endswith("/threads")
    assert reply["would_post_to"].endswith("/threads/7/comments")
    assert reply["reply"] is True and reply["dry_run"] is True
    assert "Draft updated after new commits" in reply["preview"]


def test_reviewer_knowledge_ref_shape_is_coerced_not_fatal(monkeypatch):
    """引用欄位寫歪（單值 / 混型別 / 亂形狀）不該讓整條 finding 被 pydantic 丟掉。"""
    from orchestrator import pipeline as P
    from orchestrator.rules import RulesBundle
    ana = parse_diff("--- a/src/A.java\n+++ b/src/A.java\n@@ -1,1 +1,2 @@\n a\n+b\n")

    async def fake(system, user, **kw):
        return json.dumps({"assessment": "a", "findings": [
            {"rule": "BUG", "file": "src/A.java", "line": 2,
             "severity": "warning", "note": "n1", "knowledge_ref": "PR1-I1"},
            {"rule": "PERF", "file": "src/A.java", "line": 2, "severity": "info",
             "note": "n2", "knowledge_ref": [12, "PR1-I2", ""]},
            {"rule": "NAMING", "file": "src/A.java", "line": 2, "severity": "info",
             "note": "n3", "knowledge_ref": {"bad": "shape"}}]})

    monkeypatch.setattr(P, "complete", fake)
    monkeypatch.setattr(P, "prompt_or", lambda name, default: default)
    dim = P._Dim(name="X", focus="f", hypotheses=[], files=[], rules=[])
    _assessment, finds, _checks, _loc = asyncio.run(
        P._review_one(dim, ana, RulesBundle(), "", ""))
    assert [f.knowledge_ref for f in finds] == [["PR1-I1"], ["12", "PR1-I2"], []]


def test_redistill_does_not_resurrect_a_dropped_draft(kroot, monkeypatch):
    """收割器在重蒸期間判死這條稿（重試用盡 / 世代變更）→ 重蒸不得把它寫回來。"""
    from orchestrator import knowledge as K
    pend = _pending(kroot)
    diff = ("--- a/src/x/F.java\n+++ b/src/x/F.java\n@@ -10,3 +10,4 @@\n"
            " c10\n+a11\n c12\n c13\n")

    async def f_meta(u):
        return {"title": "t", "description": "d" * 60, "source_commit": "sha-NEW",
                "target_branch": "release/x"}

    async def f_diff(u):
        pend.unlink()                 # 蒸到一半，收割器把稿丟了
        return diff

    async def f_post(pr_url, markdown, thread_id=None):
        return {"posted": True, "dry_run": False, "thread_id": thread_id}

    async def f_complete(system, user, **kw):
        return json.dumps({"summary": "s", "type": "bugfix",
                           "intent_stated": True, "primary_entity": "src/x/F.java",
                           "decisions": [{"text": "d", "evidence": ["F.java:11"]}],
                           "invariants": [], "supersedes": [], "affected": [],
                           "ledger_line": "l"})

    monkeypatch.setattr(K.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(K.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(K.clients, "post_knowledge_draft", f_post)
    monkeypatch.setattr(K, "prompt_or", lambda name, default: "SYSTEM")
    monkeypatch.setattr(K, "complete", f_complete)

    asyncio.run(K.redistill("https://x/C/P/_git/repo-a/pullrequest/90"))
    assert not pend.exists()


def test_post_knowledge_draft_omits_thread_id_for_old_result_servers(monkeypatch):
    """版本偏移安全網：thread_id 為 None 時不送這個欄位，舊版 ③ 照常收稿。

    ③ 若因未知參數報錯，會被當成「真的貼稿失敗」→ harvester 代貼，而那條路徑
    不看 KNOWLEDGE_POST —— 影子期會因此誤發。
    """
    from orchestrator import clients
    sent = []

    async def fake_call(url, tool, args):
        sent.append((tool, args))
        return {"posted": True}

    monkeypatch.setattr(clients, "_call", fake_call)
    asyncio.run(clients.post_knowledge_draft("u", "md"))
    asyncio.run(clients.post_knowledge_draft("u", "md", thread_id=7))
    assert sent[0][1] == {"pr_url": "u", "markdown": "md"}      # 舊介面原樣
    assert sent[1][1]["thread_id"] == 7


def test_cycle_close_first_sight_does_not_backfill(kroot, monkeypatch):
    """首見水位：歷史上早就 completed 的驗收 PR 不得收口當下暫存的稿。

    「哪些驗收 PR 結算過」只記在 _state/cycle.json，而它住在 KNOWLEDGE_ROOT——
    帳本重新 clone、_state 掉了、或清空 KB 重測，記憶就沒了，但 ADO 的歷史
    一條都沒少。少了這個判斷，上個月的驗收 PR 會把今天剛暫存的稿立刻落地，
    而那批稿沒有任何人在任何 digest 上看過。
    """
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir()
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    completed = [{"pullRequestId": 400, "sourceRefName": "refs/heads/release/x",
                  "targetRefName": "refs/heads/master",
                  "closedDate": "2026-01-01T00:00:00Z"}]        # 上一輪的
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: [])
    monkeypatch.setattr(H, "_list_completed", lambda c, r: completed)

    _stage(kroot, 70, "src/x/A.java")                # 今天剛暫存的稿
    lines = H._cycle_close(object(), kroot)
    assert any("首見" in ln and "不回填" in ln for ln in lines)
    assert not (kroot / "repo-a" / "x" / "A.md").exists()        # 沒有被落地
    assert (kroot / "_cycle" / "repo-a" / "pr-70.json").exists()  # 稿還在池裡
    st = json.loads((kroot / "_state" / "cycle.json").read_text())
    assert st["repo-a#400"]["closed"] is True                   # 但不會再問一次

    # 之後真的有一條驗收 PR 完成 → 照常收口
    completed.append({"pullRequestId": 401,
                      "sourceRefName": "refs/heads/release/x",
                      "targetRefName": "refs/heads/master", "closedDate": "z"})
    lines = H._cycle_close(object(), kroot)
    assert any("驗收 PR#401 收口" in ln for ln in lines)
    assert (kroot / "repo-a" / "x" / "A.md").exists()
    assert not (kroot / "_cycle" / "repo-a" / "pr-70.json").exists()


def test_retired_invariants_stay_on_the_page_but_leave_the_injection(kroot):
    """註銷是標記不是蒸發——頁面留著，但注入給 reviewer 的區塊不帶它。

    帶著它，模型會照樣拿一條死掉的規則報 violation（作者明明已經說那個
    business 需求取消了），而誤報比漏報更快把信任燒光。
    """
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants, load_for
    H._land(kroot, "repo-a", 10,
            _draft("src/a/Fee.java", invariants=["Fee must be capped at 5%",
                                                 "Fee is always BigDecimal"]),
            "cycle/silent", "")
    H._land(kroot, "repo-a", 11,
            _draft("src/a/Fee.java",
                   supersedes=[{"entity": "Fee", "what": "cap removed",
                                "retires_id": "PR10-I1"}]),
            "cycle/explicit", "business requirement dropped")

    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "~~Fee must be capped at 5%~~ ⚠ retired by PR#11" in page   # 血緣留著
    assert existing_invariants("repo-a", ["src/a/Fee.java"])[0][1] == [
        ("PR10-I2", "Fee is always BigDecimal")]       # 目錄早就不列它

    text, stats = load_for("repo-a", ["src/a/Fee.java"])
    assert "Fee must be capped" not in text            # 注入不帶死規則
    assert "Fee is always BigDecimal" in text          # 活的照常
    assert stats["knowledge_ids"] == ["PR10-I2"]       # 也不能拿它當 grounding
    assert stats["knowledge_retired_hidden"] == 1      # 擋掉幾條，攤在 stats
    assert "business requirement dropped" in text      # 作者原話仍在（是脈絡）


# ========== 矛盾裁決：實作推翻規則 → 驗收窗決定拔不拔 ==========

def _seed_rule(kroot, entity="src/a/Fee.java", text="Fee must be capped at 5%"):
    """先讓一條規則落地，回它的 id。"""
    from orchestrator import knowledge_harvest as H
    H._land(kroot, "repo-a", 10, _draft(entity, invariants=[text]),
            "cycle/silent", "")
    return "PR10-I1"


def _stage_contradicting(kroot, pid, iid, ent="src/a/Fee.java"):
    from orchestrator.knowledge import write_json_atomic
    write_json_atomic(kroot / "_cycle" / "repo-a" / f"pr-{pid}.json", {
        "pr_url": "u", "pr_id": pid, "repo": "repo-a",
        "base_url": "https://x/C/P", "target_branch": "release/x",
        "draft": _draft(ent, invariants=[f"new rule from PR{pid}"]),
        "author_note": "the business requirement was dropped",
        "provenance": "explicit", "contradicted": [iid]})


def _cycle_env(kroot, monkeypatch, threads=()):
    from orchestrator import knowledge_harvest as H
    (kroot / "_state").mkdir(exist_ok=True)
    (kroot / "_state" / "repos.json").write_text(
        json.dumps({"repo-a": "https://x/C/P"}))
    (kroot / "_state" / "cycle.json").write_text(          # 非首見
        json.dumps({"repo-a#1": {"closed": True}}))
    monkeypatch.setattr(H, "client", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(H, "_post_thread", lambda c, r, t: None)
    monkeypatch.setattr(H, "_list_active", lambda c, r: [])
    monkeypatch.setattr(H, "_get_threads", lambda c, r: list(threads))
    monkeypatch.setattr(H, "_list_completed", lambda c, r: [{
        "pullRequestId": 500, "sourceRefName": "refs/heads/release/x",
        "targetRefName": "refs/heads/master", "closedDate": "z"}])


def test_contradicted_invariant_is_retired_at_cycle_close(kroot, monkeypatch):
    """帶著矛盾完成的 PR = 實作推翻了規則；沉默即接受 → 收口時畫掉那條。"""
    from orchestrator import knowledge_harvest as H
    from orchestrator.knowledge import existing_invariants, load_for
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 70, iid)
    _cycle_env(kroot, monkeypatch)

    lines = H._cycle_close(object(), kroot)
    assert any(f"拔除 {iid}" in ln for ln in lines)
    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "~~Fee must be capped at 5%~~ ⚠ retired by PR#70" in page   # 畫掉不刪除
    assert "retired `PR10-I1`: contradicted by merged code" in page    # History 留行
    assert "new rule from PR70" in page                                # 新條目照樣落地
    # 之後兩條讀取路徑都不再帶它
    assert all(i != iid for _, pairs in existing_invariants(
        "repo-a", ["src/a/Fee.java"]) for i, _ in pairs)
    assert "Fee must be capped" not in load_for("repo-a", ["src/a/Fee.java"])[0]
    receipt = next((kroot / "repo-a" / "_cycles").glob("*-PR500.md")).read_text(
        encoding="utf-8")
    assert "Invariants retired this cycle" in receipt


def test_keep_command_vetoes_the_retirement(kroot, monkeypatch):
    """任何人回 `keep PR10-I1` → 本週期不拔，規則原封不動。"""
    from orchestrator import knowledge_harvest as H
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 71, iid)
    _cycle_env(kroot, monkeypatch, threads=[{"id": 9, "comments": [
        {"content": "keep PR10-I1\n這條還要，只是這支例外"}]}])

    lines = H._cycle_close(object(), kroot)
    assert any(f"矛盾 {iid} 保留" in ln for ln in lines)
    page = (kroot / "repo-a" / "a" / "Fee.md").read_text(encoding="utf-8")
    assert "- Fee must be capped at 5% `PR10-I1`" in page      # 沒被動過
    assert "~~" not in page


def test_contradiction_retirement_can_be_switched_off(kroot, monkeypatch):
    from orchestrator import knowledge_harvest as H
    monkeypatch.setenv("KNOWLEDGE_RETIRE_ON_CONTRADICTION", "false")
    iid = _seed_rule(kroot)
    _stage_contradicting(kroot, 72, iid)
    _cycle_env(kroot, monkeypatch)
    lines = H._cycle_close(object(), kroot)
    assert not any("拔除" in ln for ln in lines)
    assert "~~" not in (kroot / "repo-a" / "a" / "Fee.md").read_text(
        encoding="utf-8")


def test_retire_by_id_refuses_when_the_id_is_ambiguous_or_absent(kroot):
    from orchestrator import knowledge_harvest as H
    _seed_rule(kroot)
    assert "查無此 ID" in H._retire_by_id(kroot, "repo-a", "PR99-I9", 70)
    # 同一個 ID 出現在兩頁（不該發生，但真發生時寧可不動）
    H._land(kroot, "repo-a", 11, _draft("src/b/Other.java",
                                        invariants=["dup"]), "cycle/silent", "")
    p = kroot / "repo-a" / "b" / "Other.md"
    p.write_text(p.read_text(encoding="utf-8").replace("`PR11-I1`", "`PR10-I1`"),
                 encoding="utf-8")
    assert "撞到 2 頁" in H._retire_by_id(kroot, "repo-a", "PR10-I1", 70)


def test_digest_shows_contradictions_as_a_proposal(kroot):
    from orchestrator import knowledge_digest as D
    items = [{"pr_id": 70, "provenance": "explicit", "author_note": "req dropped",
              "contradicted": ["PR10-I1"],
              "draft": _draft("src/a/Fee.java", invariants=["new rule"])}]
    md = D.render(items, None, None)
    assert "Invariants contradicted by merged code" in md
    assert "`PR10-I1`" in md and "keep PR10-I1" in md


def test_review_hands_the_distiller_its_diff_ids_and_contradictions(monkeypatch):
    """app → distiller 的接線：同一份 diff、F 編號、被踩到的不變式 id。

    這段是純接線，最容易在改動裡安靜斷掉（斷了也只會 print 一行 journal），
    所以用一條測試把四個參數釘住。
    """
    from orchestrator import app as A
    from orchestrator.models import Finding
    A._RECENT.clear()
    captured = {}

    async def f_meta(u):
        return {"repo": "r", "title": "t", "source_commit": "sha-X",
                "target_branch": "refs/heads/release/x"}

    async def f_diff(u):
        return "--- a/x.java\n+++ b/x.java\n@@ -1,1 +1,2 @@\n a\n+b\n"

    async def f_run(diff, md):
        blocking = Finding(rule="BUG", file="x.java", line=2, severity="error",
                           note="violates the cap rule")
        blocking.knowledge_ref = ["PR41-I1"]
        advisory = Finding(rule="STYLE", file="x.java", line=2, severity="info",
                           note="nit")
        advisory.knowledge_ref = ["PR41-I2"]        # info 不算矛盾
        return types.SimpleNamespace(
            markdown="## Pull Request Review OK", findings=[blocking, advisory],
            stats={"file_table": [{"path": "x.java"}], "graph_impacted": []})

    async def f_distill(*a, **k):
        captured.update(k)
        captured["files"] = a[2]

    monkeypatch.setattr(A.clients, "fetch_pr_metadata", f_meta)
    monkeypatch.setattr(A.clients, "fetch_pr_diff", f_diff)
    monkeypatch.setattr(A, "run_review", f_run)
    monkeypatch.setattr(A.knowledge, "distill_and_stage", f_distill)
    monkeypatch.setenv("AUTO_POST", "false")

    async def go():
        await A.handle_review("https://x/C/P/_git/R/pullrequest/9", "u")
        await asyncio.sleep(0)          # 讓 _bg 排的那條真的跑到

    asyncio.run(go())
    assert captured["finding_ids"] == {"F1", "F2"}
    assert captured["finding_files"] == ["x.java", "x.java"]
    assert captured["diff"].startswith("--- a/x.java")
    assert captured["contradicted"] == {"PR41-I1"}    # 只有 error/warning 算
PATCH_EOF

write "deploy/env/orchestrator.env.example" <<'PATCH_EOF'
# 複製為 /etc/pr-review-suite/orchestrator.env（chmod 600）
# 大腦（8080）—— 唯一接 Langfuse 的服務

LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-changeme
LANGFUSE_SECRET_KEY=sk-lf-changeme

# OpenAI 相容端點：填到「/chat/completions 的上一層」（帶不帶 /v1 以實測為準）
# 再次提醒：值後面不可以有空白或註解
LLM_BASE_URL=http://llm-gateway.internal
LLM_API_KEY=not-needed
LLM_MODEL=changeme

ADO_TOOLS_URL=http://127.0.0.1:8094/mcp
READER_TOOLS_URL=http://127.0.0.1:8095/mcp
RESULT_TOOLS_URL=http://127.0.0.1:8096/mcp

# review 完自動發回 PR（需同時 results 的 DRY_RUN=false 才會真發，雙保險）
AUTO_POST=false

# 同一條 PR 在幾秒內重複觸發時，重播快取而不重跑
REVIEW_DEDUPE_TTL=120

# 團隊規則 repo 的本機 clone（沒設 = 規則功能整個停用，行為同舊版）
# 結構假設：<RULES_ROOT>/rules/common/ 必載；<RULES_ROOT>/rules/<repo小寫>/ 命中才載
# 記得配一個 systemd timer 定期 git pull；每次 review 用到的 commit SHA 會進 trace
# RULES_ROOT=/opt/pr-review-rules
# 目錄名不一樣才需要下面兩個（例如你們的 common 其實叫 commons）
# RULES_SUBDIR=rules
# RULES_COMMON_DIR=common
# 組進 prompt 的規則總量上限（字元）
# RULES_MAX_CHARS=60000

# ---- v2 pipeline（anatomy→static→plan→平行reviewer→adversary→synthesis）----
# 預設 v1（原始單呼叫）。切 v2 前先跑過 eval 基線；出事改回 v1 即回滾
REVIEW_PIPELINE=v1
# 本地乾淨 clone 的根：REPOS_ROOT/<repo小寫>/ 一 repo 一顆（target branch）
# REPOS_ROOT=/opt/repos
# v2 旋鈕（以下皆預設值，要調再打開；每顆旋鈕都會進 trace/run metadata）
# 科數與併發（2026-08-25 起放大：買覆蓋而不是把單一 prompt 拉長）
# REVIEW_MAX_DIMENSIONS=8
# REVIEW_MAX_PARALLEL=6
# REVIEW_LLM_BASE_CONF=0.7
# REVIEW_MIN_CONFIDENCE=0.55
# 純輸出上限，不影響任何 prompt 長度
# REVIEW_MAX_FINDINGS=30
# REVIEW_ADVERSARY=true
# adversary 每次呼叫最多審幾條（分塊防注意力稀釋）
# ADVERSARY_CHUNK=6
# adversary 專用模型（換一顆腦去自我同意偏差；未設 = 同 LLM_MODEL）
# LLM_MODEL_ADVERSARY=
# REVIEWER_RULES_MAX_CHARS=20000
# CONTEXT_LINES=6
# base 上下文與 graph 摘要的預算。注意這一份會進「每一科」的 reviewer prompt，
# 所以放大是 3x 不是 5x —— 它跟切片共用同一個注意力預算。
# CONTEXT_MAX_CHARS=24000
# static pass：auto = 裝了就跑（venv 裝 semgrep/ruff 即啟用）
# STATIC_SEMGREP=auto
# STATIC_RUFF=auto
# 掃描逾時秒數（檔案上限拉到 300 之後預設已改 300；冷啟動慢的機器再往上調）
# STATIC_TIMEOUT=300
# 確定性 MISSING-TEST（diff 有新宣告卻零測試檔 → conf 1.0 保底）；false 關閉
# STATIC_MISSING_TEST=true

# ---- 大 PR（ADO_DIFF_MAX_FILES=300）配套 ----
# 檔案變多時掃描會變久；journal 若出現 timed out (STATIC_TIMEOUT=300s) 就往上調。
#
# REVIEWER_SLICES_MAX_CHARS 是「每一科」的切片預算，不是全域的。
# 超預算的檔案降級為 static-only，切片尾巴會留 "(N more changed file(s) omitted)"，
# 且進 stats 的 slices_skipped。想讓 LLM 也看得完，**開多科比把單科預算拉大好**
# ——後者會讓單一 prompt 過長，模型在長 context 中段的注意力會掉。這也是為什麼
# 這一輪放大的是 REVIEW_MAX_DIMENSIONS，而不是下面這個數。
# REVIEWER_SLICES_MAX_CHARS=30000
#
# 單一檔案的切片上限（超過就檔內截斷）。跟上面那顆是兩層預算：這顆管「一個檔
# 最多給模型看多少」，上面那顆管「一科總共給多少」。
# REVIEWER_FILE_SLICE_MAX_CHARS=6000
#
# ⚠ 改這幾個數會改變 golden set 的語意。原本的 pr-129 / pr-130 就是靠
# 「切片預算會破」在考截斷路徑，預算調大之後名存實亡，已經移除。日後要再考
# 那條路徑，得照當時的預算重新生 fixture，並在 preflight._GEOMETRY 登記斷言。

# ---- ④ code graph（選配；code-review-graph serve --http，原生 MCP over HTTP）----
# 下面三個值已對 code-review-graph 實測驗證過；不設 = 功能停用、pipeline 優雅降級
# graph server 綁定的 repo 名（小寫）；設了之後其他 repo 的 review 自動跳過
# graph（避免拿 A repo 的圖回答 B repo）。單 repo 環境可不設。
# GRAPH_REPO=
# GRAPH_TOOLS_URL=http://127.0.0.1:8097/mcp
# GRAPH_TOOL_BLAST=get_impact_radius_tool
# 注意單引號：這個值含空白與 $，沒引號時 systemd 沒事、但被 shell source 會爆
# GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files}'
# 多 repo 自動切庫（實測可行）：單一 serve 實例、per-call 用 repo_root 指到
# 對應 clone；$repo_dir 會代入成「repo 名小寫、無引號」。此模式 GRAPH_REPO 留空。
# GRAPH_TOOL_BLAST_ARGS='{"changed_files": $files, "repo_root": "<REPOS_ROOT>/$repo_dir"}'
# 想改餵更完整的審查上下文可換 get_review_context_tool（參數名先 list tools 確認）

# 打開後聊天室/腳本要帶 Bearer；/eval-webhook 刻意不驗此 key（見 README）
# 用到 K 段的 stale 重蒸就「必須」設起來：/knowledge/redistill 有 LLM 花費與
# 寫檔語意，不是純讀。harvester 也讀這份 env，同一個值即可對上。
# ORCH_API_KEY=changeme

# harvester 打重蒸請求的位址（同機 loopback；不走 proxy）
# ORCH_URL=http://127.0.0.1:8080

# ---------- 知識迴圈（K 段，選配）----------
# 本地 knowledge repo 路徑；未設 = K 段整個停用（注入與蒸餾皆靜默）
# KNOWLEDGE_ROOT=
# 進帳本的目標分支（逗號分隔、支援萬用字元）
# KNOWLEDGE_BRANCHES=master,release/*
# Stage 04 注入區塊總字數上限（同樣會進 reviewer prompt，所以放大是 3x）
# KNOWLEDGE_MAX_CHARS=18000
# 週期驗收 PR 的長相（source → target）；符合者不蒸餾、作為收口儀式
# KNOWLEDGE_CYCLE_SOURCE=release/*
# KNOWLEDGE_CYCLE_TARGET=master
# 收口 digest 要不要貼到驗收 PR（false = 全暗影子模式：照樣落地、不發任何留言）
# 首次全列，之後每有新暫存稿補一則 delta；從未出現在 digest 的條目落地時
# provenance 帶 +unlisted
# KNOWLEDGE_DIGEST=true
# digest 裡的「🔎 Needs a look」顧問段（LLM）：按實體看整週期、標矛盾 / 近重複 /
# 該註銷沒點名。只是提案——落地路徑不讀它；失敗退回純清單。false = 純清單
# KNOWLEDGE_DIGEST_LLM=true
# （選配）顧問分析專用模型；未設 = LLM_MODEL。prompt 名 pr-review-digest（Langfuse 版控）
# LLM_MODEL_DIGEST=
# 顧問旗標上限（防洗版）
# KNOWLEDGE_DIGEST_MAX_FLAGS=12
# 蒸餾看得到的 diff 切片預算（distiller 與 reviewer 吃同一份座標系；超預算的
# 檔會在材料尾端留 "(N file(s) omitted from the diff excerpt: …)" 並進稿裡的
# distill_slices_skipped —— 被截掉的東西不安靜蒸發）
# DISTILL_DIFF_MAX_CHARS=100000
# 實作推翻規則時，收口是否拔掉那條不變式（review 報了違反、PR 仍完成 →
# digest 紅區提案 → 沉默即拔；回 `keep <ID>` 否決）。false = 只提案不執行，
# 規則永遠只能靠 distiller 明示註銷或人工改頁
# KNOWLEDGE_RETIRE_ON_CONTRADICTION=true
# stale 重蒸（draft 之後又有 push → 用最終 diff 重蒸一次，覆寫 _pending）。
# false = 回到「stale 直接丟稿」的舊行為；重試上限由 harvester 的請求戳守（≤2 次）
# KNOWLEDGE_REDISTILL=true

# ---------- eval 專用（run_eval / run_distill_eval 讀；webhook 觸發會繼承）----------
# 知識依賴題的種子帳本根目錄；未設 = eval/fixtures/knowledge
# EVAL_KNOWLEDGE_ROOT=
# 對照組（同一批知識題再跑一次 KNOWLEDGE_ROOT=""）；關掉就只剩上界、沒有 delta
# EVAL_KNOWLEDGE_AB=true
# knowledge_delta 的 CI 門檻。**先 report-only（不要設）**，基線穩定兩輪再升硬門
# EVAL_MIN_KNOWLEDGE_DELTA=
# 蒸餾考卷的 CI 門檻，同樣先 report-only
# EVAL_MIN_DISTILL_SCORE=
PATCH_EOF

write "deploy/systemd/pr-review-knowledge-harvest.service.example" <<'PATCH_EOF'
[Unit]
Description=pr-review knowledge harvest (PR 終態裁決 + 落地 knowledge repo)
# stale 重蒸會打 ORCH_URL（同機 orchestrator）；orchestrator 沒起來時請求會失敗，
# 那條 PR 的稿留著、下一輪再試一次（attempts ≤ 2），不會遺失也不會無界重試。
After=network.target review-orchestrator.service
Wants=review-orchestrator.service

[Service]
Type=oneshot
User=<你的user>
WorkingDirectory=<安裝根>
EnvironmentFile=/etc/pr-review-suite/orchestrator.env
EnvironmentFile=/etc/pr-review-suite/results.env
Environment=PATH=<安裝根>/venv/bin:/usr/bin:/bin
ExecStart=/bin/bash -lc '<安裝根>/venv/bin/python -m orchestrator.knowledge_harvest'
PATCH_EOF

write "eval/fixtures/knowledge/kfix-1/cache/SessionCache.md" <<'PATCH_EOF'
# SessionCache

> entity: src/main/java/com/acme/cache/SessionCache.java
> approval: seed · last: PR#41 · 2026-06-11

## Summary
Holds authenticated sessions for the edge tier. The one thing newcomers get
wrong is the time unit: the cache speaks milliseconds everywhere, while the
properties file that feeds it is written in seconds.

## Decisions
- Kept a single millisecond-based TTL on the cache boundary instead of an
  overloaded setTtl(value, unit), because the overload was silently picked by
  autoboxing at two call sites during the 2026.04 incident. [PR] [SessionCache.java:58] `PR41-D1`

## Invariants
- Every TTL crossing the SessionCache boundary is in milliseconds. A caller
  reading a value configured in seconds must multiply by 1000 before calling
  setTtl; passing the raw seconds value expires sessions a thousand times too
  early and shows up as random logouts, not as an error. `PR41-I1`

## Affected entities
`SessionConfigLoader.java`, `EdgeAuthFilter.java`

## History
| PR | Date | Entry |
|---|---|---|
| PR#41 | 2026-06-11 | TTL unit fixed at milliseconds on the cache boundary (bugfix) · cycle/explicit |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-2/payment/PaymentClient.md" <<'PATCH_EOF'
# PaymentClient

> entity: src/main/java/com/acme/payment/PaymentClient.java
> approval: seed · last: PR#52 · 2026-07-02

## Summary
The only outbound path to the payment provider. Charges are not naturally
idempotent on the provider side; the client makes them idempotent by key, and
everything that retries a charge inherits that contract.

## Decisions
- Chose per-attempt keys generated by the caller over provider-side dedup
  windows, because the provider's window is 60 seconds and our retry backoff
  exceeds it. [PR] [PaymentClient.java:73] `PR52-D1`

## Invariants
- A retry of POST /charge must send the same Idempotency-Key as the first
  attempt. Generating a fresh key per attempt turns a retry into a second
  real charge — the provider accepts both and the customer is billed twice.
  The key belongs to the charge, not to the attempt. `PR52-I1`

## Affected entities
`RetryExecutor.java`, `CheckoutService.java`

## History
| PR | Date | Entry |
|---|---|---|
| PR#52 | 2026-07-02 | Idempotency-Key bound to the charge, not the attempt (bugfix) · cycle/explicit |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-3/_module/orders.md" <<'PATCH_EOF'
# orders

> module seed · approval: seed

## Summary
Order lifecycle and its audit trail. The module's whole reason for having a
hand-written page is the audit contract below: it is invisible in the code and
every reviewer who has not been burned by it reads the async version as an
optimisation.

## Invariants
- Audit rows are written inside the same transaction as the mutation they
  describe. An audit write that runs after commit — @Async, an event listener
  bound to AFTER_COMMIT, or a second transaction — survives a rollback of the
  mutation and produces an audit trail describing state that never existed.
  Compliance reads this table as ground truth. `PR63-I2`
- Order state transitions go through OrderStateMachine; no service sets
  `order.status` directly. `PR63-I3`

## History
| PR | Date | Entry |
|---|---|---|
| PR#63 | 2026-07-19 | Audit writes pinned to the mutating transaction (bugfix) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/AlphaHandler.md" <<'PATCH_EOF'
# AlphaHandler

> entity: src/main/java/com/acme/routing/AlphaHandler.java
> approval: seed · last: PR#70 · 2026-07-28

## Summary
Handles the alpha protocol frames that the legacy edge boxes still speak.
Kept alive only for the two datacentres that have not been migrated; every
change here is expected to be defensive rather than clever.

## Decisions
- Kept the hand-rolled frame parser instead of moving to the shared codec, because the legacy boxes emit a non-standard length prefix that the codec rejects outright. [PR] [AlphaHandler.java:41] `PR70-D1`
- Kept the retired 2019 opcode table in the parser rather than deleting it, because two customer appliances still negotiate down to it and removing the table turns their reconnect into a silent protocol error. [PR] [AlphaHandler.java:96] `PR66-D1`
- Chose to cap alpha connections per datacentre at 200 instead of scaling with the pool, since the legacy boxes leak sockets on their side and the cap is what makes the leak survivable until the migration finishes. [PR] [AlphaHandler.java:118] `PR66-D2`

## Invariants
- Alpha frames are parsed with an explicit length check before the payload is read; the legacy boxes occasionally emit a truncated frame after a link flap and an unchecked read blocks the handler thread until the socket timeout fires. `PR70-I1`
- The handler never mutates the shared RoutingTable; it returns a decision object and the router applies it. `PR70-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#70 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#66 | 2026-06-30 | Legacy opcode table retained for two appliances (other) · cycle/explicit |
| PR#58 | 2026-06-04 | Per-datacentre connection cap introduced (bugfix) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/BetaHandler.md" <<'PATCH_EOF'
# BetaHandler

> entity: src/main/java/com/acme/routing/BetaHandler.java
> approval: seed · last: PR#71 · 2026-07-28

## Summary
Beta protocol handler — the current default for edge traffic. Carries the
backpressure contract for the whole routing package: when the downstream
queue is full the handler must refuse, not buffer.

## Decisions
- Chose bounded refusal over an unbounded in-memory queue after the 2026.03 outage, where buffering turned a five-minute downstream stall into a forty-minute heap exhaustion. [PR] [BetaHandler.java:88] `PR71-D1`
- Chose a fixed 8 KiB read buffer over an adaptive one because the adaptive sizing interacted badly with the refusal path: a large buffer made the handler look healthy while the downstream queue was already saturated. [PR] [BetaHandler.java:131] `PR67-D1`
- Kept metrics emission synchronous inside the handler so that refusals are counted even when the dispatch executor is the component that is stuck. [PR] [BetaHandler.java:146] `PR67-D2`

## Invariants
- When the downstream queue reports full, the handler answers with a retryable refusal and drops the frame; it must not buffer frames on the heap. `PR71-I1`
- Handler threads are never blocked on I/O; anything blocking goes to the dispatch executor. `PR71-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#71 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#64 | 2026-06-24 | Fixed read buffer; adaptive sizing reverted (revert) · cycle/explicit |
| PR#59 | 2026-06-06 | Refusal metrics made synchronous (bugfix) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/DeltaHandler.md" <<'PATCH_EOF'
# DeltaHandler

> entity: src/main/java/com/acme/routing/DeltaHandler.java
> approval: seed · last: PR#73 · 2026-07-28

## Summary
Delta protocol handler for the internal control plane. Low traffic, high
blast radius: it carries the drain and failover commands.

## Decisions
- Kept delta on its own listener port so that a flood of edge traffic cannot starve control-plane commands. [PR] [DeltaHandler.java:33] `PR73-D1`
- Chose to reject rather than queue commands that arrive while a drain is in progress; queueing produced command storms after the drain finished during the 2026.05 failover drill. [PR] [DeltaHandler.java:61] `PR69-D1`
- Kept the control-plane listener on a dedicated thread pool of two threads, sized deliberately small so that a runaway operator script cannot consume the shared executor. [PR] [DeltaHandler.java:77] `PR69-D2`

## Invariants
- Control-plane commands are idempotent and carry a monotonic epoch; an out-of-order drain command must be ignored, not replayed. `PR73-I1`
- The handler logs every accepted command with its epoch. `PR73-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#73 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#65 | 2026-06-26 | Commands rejected during drain instead of queued (bugfix) · cycle/explicit |
| PR#57 | 2026-06-02 | Dedicated two-thread control-plane pool (feature) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/EpsilonHandler.md" <<'PATCH_EOF'
# EpsilonHandler

> entity: src/main/java/com/acme/routing/EpsilonHandler.java
> approval: seed · last: PR#74 · 2026-07-28

## Summary
Epsilon protocol handler — the experimental path behind a feature flag.
Present in production but disabled by default; kept in the routing package
so that the shared decision object stays honest about all five protocols.

## Decisions
- Chose a compile-time-registered handler over dynamic discovery so that an unflagged build cannot accidentally expose the experiment. [PR] [EpsilonHandler.java:24] `PR74-D1`
- Chose to mirror epsilon traffic into the beta path for comparison instead of routing it live, so that an experiment defect can never affect a real customer response. [PR] [EpsilonHandler.java:52] `PR70-D1`
- Kept the experiment's metrics under a separate prefix so that dashboards built on the routing package do not silently mix experimental traffic into production baselines. [PR] [EpsilonHandler.java:69] `PR70-D2`

## Invariants
- The epsilon path checks the feature flag on every frame, not once at startup; the flag is flipped during incidents and a cached value would keep the experiment live. `PR74-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#74 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#68 | 2026-07-05 | Epsilon mirrored, never routed live (feature) · cycle/silent |
| PR#62 | 2026-06-15 | Experiment metrics moved to their own prefix (other) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/GammaHandler.md" <<'PATCH_EOF'
# GammaHandler

> entity: src/main/java/com/acme/routing/GammaHandler.java
> approval: seed · last: PR#72 · 2026-07-28

## Summary
Gamma protocol handler, used by the partner integrations. Partner traffic is
authenticated per frame, which makes it the only handler that touches the
credential cache on the hot path.

## Decisions
- Chose a per-frame signature check over a per-connection handshake because partners multiplex several tenants over one connection. [PR] [GammaHandler.java:57] `PR72-D1`
- Chose to cache partner certificates for five minutes and not longer, because partner rotations are announced with a ten-minute window and a longer cache turns a routine rotation into an outage. [PR] [GammaHandler.java:88] `PR68-D1`
- Kept per-tenant rate limits inside the handler rather than at the gateway, since a single partner connection multiplexes tenants and the gateway cannot see the tenant id before signature verification. [PR] [GammaHandler.java:104] `PR68-D2`

## Invariants
- Signature verification happens before any partner-supplied field is used for routing; a forged zone id would otherwise steer traffic into another tenant's pool. `PR72-I1`
- Credential cache misses fail closed. `PR72-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#72 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#67 | 2026-07-01 | Certificate cache capped at five minutes (bugfix) · cycle/explicit |
| PR#61 | 2026-06-12 | Per-tenant rate limits moved into the handler (feature) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-4/routing/ZoneRouter.md" <<'PATCH_EOF'
# ZoneRouter

> entity: src/main/java/com/acme/routing/ZoneRouter.java
> approval: seed · last: PR#75 · 2026-07-30

## Summary
Picks the destination pool for a frame from its zone id. The zone ids reaching
this class come from two sources with different lifetimes, which is the whole
reason this page exists.

## Decisions
- Kept zone ids as plain strings rather than interning them or wrapping them in
  a ZoneId value type, because the partner-supplied ids are parsed at runtime
  and any wrapper would have to be created on the hot path anyway. [PR]
  [ZoneRouter.java:44] `PR75-D1`

## Invariants
- Zone ids must be compared with equals(). The ids in the static routing table
  are compile-time constants and therefore interned, while the ids arriving on
  partner frames are parsed at runtime and are not; reference comparison happens
  to work for the constants and silently fails for parsed ids, sending partner
  traffic to the default pool without any error. `PR75-I1`

## History
| PR | Date | Entry |
|---|---|---|
| PR#75 | 2026-07-30 | Zone ids kept as plain strings; equals() mandated (bugfix) · cycle/explicit |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-5/ledger/BalanceReader.md" <<'PATCH_EOF'
# BalanceReader

> entity: src/main/java/com/acme/ledger/BalanceReader.java
> approval: seed · last: PR#80 · 2026-08-08

## Summary
Read side of the ledger. Serves balances to the app tier from the projection
table, never from the entry log, because replaying the log per request was
what made the 2026.02 month-end unusable.

## Decisions
- Chose a materialised projection over on-demand replay of the entry log, since month-end concentrates reads on the largest accounts and replay cost grows with account age rather than with traffic. [PR] [BalanceReader.java:37] `PR80-D1`
- Kept the projection eventually consistent with an explicit staleness header instead of reading through the writer's transaction, because a read-through put month-end reporting behind the write lock. [PR] [BalanceReader.java:62] `PR80-D2`

## Invariants
- Balances are read from the projection, never by replaying the entry log on the request path. `PR80-I1`
- Every balance response carries the projection watermark; callers that cannot tolerate staleness compare it themselves. `PR80-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#80 | 2026-08-01 | Projection-based reads with an explicit watermark (feature) · cycle/explicit |
| PR#74 | 2026-07-21 | Fail closed beyond five minutes of lag (bugfix) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-5/ledger/EntryWriter.md" <<'PATCH_EOF'
# EntryWriter

> entity: src/main/java/com/acme/ledger/EntryWriter.java
> approval: seed · last: PR#81 · 2026-08-08

## Summary
Write side of the ledger: appends double-entry rows and nudges the projection.
The append is the system of record; everything else in this package is a
derived view and can be rebuilt from it.

## Decisions
- Chose append-only rows with a reversal convention over in-place correction, because auditors require the original entry to remain readable after a correction. [PR] [EntryWriter.java:45] `PR81-D1`
- Kept the projection update inside the same transaction as the append so that a crash cannot leave a balance that no entry explains. [PR] [EntryWriter.java:71] `PR81-D2`

## Invariants
- Entries are append-only. Corrections are new reversing entries; no row is ever updated or deleted. `PR81-I1`
- The projection update commits with the append, in one transaction. `PR81-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#81 | 2026-08-03 | Append-only with reversal convention (feature) · cycle/explicit |
| PR#76 | 2026-07-24 | Per-account sequence replaces the global one (perf) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-5/ledger/FeeCalculator.md" <<'PATCH_EOF'
# FeeCalculator

> entity: src/main/java/com/acme/ledger/FeeCalculator.java
> approval: seed · last: PR#82 · 2026-08-08

## Summary
Computes transaction fees for the ledger. Small class, disproportionate blast
radius: its output is written as a ledger entry and cannot be corrected in
place afterwards.

## Decisions
- Chose to resolve the fee schedule at entry time and store the resolved rate on the entry, because schedules change and a recomputed fee would not match the entry that was written. [PR] [FeeCalculator.java:29] `PR82-D1`
- Kept rounding at the fee level rather than at the statement level, so that the sum of the entries always equals the statement total. [PR] [FeeCalculator.java:54] `PR82-D2`

## Invariants
- The resolved rate is stored on the entry; fees are never recomputed from the current schedule when reprinting an old statement. `PR82-I1`
- Rounding is HALF_EVEN at the fee level. `PR82-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#82 | 2026-08-05 | Resolved rate stored on the entry (bugfix) · cycle/explicit |
| PR#77 | 2026-07-26 | HALF_EVEN aligned with the settlement partner (bugfix) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-5/ledger/StatementBuilder.md" <<'PATCH_EOF'
# StatementBuilder

> entity: src/main/java/com/acme/ledger/StatementBuilder.java
> approval: seed · last: PR#83 · 2026-08-08

## Summary
Renders monthly statements from entries. Pure assembly: it must not compute
anything that an entry already carries, or two sources of truth appear.

## Decisions
- Chose to render from stored entries only, with no recomputation, after a statement reprint disagreed with the original by four cents. [PR] [StatementBuilder.java:41] `PR83-D1`
- Kept statement generation idempotent by keying on (account, period) so that a retried job overwrites rather than duplicates. [PR] [StatementBuilder.java:63] `PR83-D2`

## Invariants
- Statements are assembled from stored entries; no figure is recomputed at render time. `PR83-I1`
- Generation is idempotent per (account, period). `PR83-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#83 | 2026-08-07 | Render-from-entries only, no recomputation (bugfix) · cycle/explicit |
| PR#78 | 2026-07-27 | Streaming render for large accounts (perf) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge/kfix-5/money/MoneyFormat.md" <<'PATCH_EOF'
# MoneyFormat

> entity: src/main/java/com/acme/money/MoneyFormat.java
> approval: seed · last: PR#85 · 2026-08-12

## Summary
The money type boundary for the whole service: parsing, scaling and formatting
of monetary amounts. Nothing in this codebase is allowed to invent its own
arithmetic for money, and the ledger package is the one that keeps trying,
because its inputs arrive as rates and percentages rather than as amounts.

## Decisions
- Chose BigDecimal with an explicit scale over long-cents after the 2025.11
  migration, because several currencies in the partner set use three decimal
  places and the long-cents helpers silently truncated them. [PR]
  [MoneyFormat.java:31] `PR85-D1`
- Kept parsing and formatting in one class so that the scale used to parse an
  amount is provably the scale used to print it; the earlier split produced
  statements whose totals did not match their rows. [PR] [MoneyFormat.java:58]
  `PR85-D2`

## Invariants
- Money is BigDecimal end to end. No intermediate step converts an amount to
  double or float — not for a percentage, not for a fee rate, not "just for the
  multiplication". Binary floating point cannot represent the decimal rates used
  by the fee schedules, so a double round-trip loses fractions of a cent per row
  and reconciliation breaks at month-end scale, long after the change shipped.
  `PR85-I3`
- Scale is set once at the boundary and carried; downstream code must not call
  setScale to "tidy" a value. `PR85-I4`

## Affected entities
`FeeCalculator.java`, `StatementBuilder.java`, `SettlementExport.java`

## History
| PR | Date | Entry |
|---|---|---|
| PR#85 | 2026-08-12 | BigDecimal end to end; no double round-trips (bugfix) · cycle/explicit |
| PR#79 | 2026-07-29 | Parse and format kept in one class (refactor) · cycle/silent |
PATCH_EOF

write "eval/fixtures/knowledge_cases/kfix-1.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/session/SessionConfigLoader.java
+++ b/src/main/java/com/acme/session/SessionConfigLoader.java
@@ -14,6 +14,16 @@ public class SessionConfigLoader {
     private final Properties props;
     private final SessionCache cache;

+    /**
+     * Push the configured session timeout into the cache at startup.
+     */
+    public void applyTimeouts() {
+        int ttlSeconds = Integer.parseInt(
+                props.getProperty("session.ttl.seconds", "1800"));
+        cache.setTtl(ttlSeconds);
+        log.info("session ttl applied: {}s", ttlSeconds);
+    }
+
     public SessionConfigLoader(Properties props, SessionCache cache) {
         this.props = props;
         this.cache = cache;
PATCH_EOF

write "eval/fixtures/knowledge_cases/kfix-2.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/retry/RetryExecutor.java
+++ b/src/main/java/com/acme/retry/RetryExecutor.java
@@ -21,6 +21,22 @@ public class RetryExecutor {
     private final PaymentClient client;
     private final Backoff backoff;

+    /**
+     * Retry a charge up to maxAttempts times with exponential backoff.
+     */
+    public ChargeResult chargeWithRetry(ChargeRequest request, int maxAttempts) {
+        RuntimeException last = null;
+        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
+            String idempotencyKey = UUID.randomUUID().toString();
+            try {
+                return client.charge(request, idempotencyKey);
+            } catch (TransientPaymentException e) {
+                last = e;
+                backoff.sleep(attempt);
+            }
+        }
+        throw last;
+    }
+
     public RetryExecutor(PaymentClient client, Backoff backoff) {
         this.client = client;
         this.backoff = backoff;
PATCH_EOF

write "eval/fixtures/knowledge_cases/kfix-3.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/orders/OrderService.java
+++ b/src/main/java/com/acme/orders/OrderService.java
@@ -38,12 +38,20 @@ public class OrderService {
     @Transactional
     public Order cancel(long orderId, String reason) {
         Order order = repository.findById(orderId).orElseThrow();
         stateMachine.apply(order, Event.CANCEL);
         repository.save(order);
-        auditLog.write(new AuditEntry(orderId, "CANCEL", reason));
+        publishAudit(orderId, reason);
         return order;
     }

+    /**
+     * Write the audit row off the request thread so cancellation returns
+     * faster; the audit table is not on the hot path.
+     */
+    @Async
+    public void publishAudit(long orderId, String reason) {
+        auditLog.write(new AuditEntry(orderId, "CANCEL", reason));
+    }
+
     public Order get(long orderId) {
         return repository.findById(orderId).orElseThrow();
     }
PATCH_EOF

write "eval/fixtures/knowledge_cases/kfix-4.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/routing/AlphaHandler.java
+++ b/src/main/java/com/acme/routing/AlphaHandler.java
@@ -30,6 +30,10 @@ public class AlphaHandler implements FrameHandler {
     private final RoutingMetrics metrics;

+    @Override
+    public String protocolName() {
+        return "alpha";
+    }
+
     @Override
     public Decision handle(Frame frame) {
         metrics.counter("alpha.frames").increment();
--- a/src/main/java/com/acme/routing/BetaHandler.java
+++ b/src/main/java/com/acme/routing/BetaHandler.java
@@ -44,6 +44,10 @@ public class BetaHandler implements FrameHandler {
     private final DownstreamQueue queue;

+    @Override
+    public String protocolName() {
+        return "beta";
+    }
+
     @Override
     public Decision handle(Frame frame) {
         if (queue.isFull()) {
--- a/src/main/java/com/acme/routing/DeltaHandler.java
+++ b/src/main/java/com/acme/routing/DeltaHandler.java
@@ -25,6 +25,10 @@ public class DeltaHandler implements FrameHandler {
     private final ControlPlane controlPlane;

+    @Override
+    public String protocolName() {
+        return "delta";
+    }
+
     @Override
     public Decision handle(Frame frame) {
         Command command = Command.parse(frame);
--- a/src/main/java/com/acme/routing/EpsilonHandler.java
+++ b/src/main/java/com/acme/routing/EpsilonHandler.java
@@ -18,6 +18,10 @@ public class EpsilonHandler implements FrameHandler {
     private final FeatureFlags flags;

+    @Override
+    public String protocolName() {
+        return "epsilon";
+    }
+
     @Override
     public Decision handle(Frame frame) {
         if (!flags.enabled("routing.epsilon")) {
--- a/src/main/java/com/acme/routing/GammaHandler.java
+++ b/src/main/java/com/acme/routing/GammaHandler.java
@@ -36,6 +36,10 @@ public class GammaHandler implements FrameHandler {
     private final CredentialCache credentials;

+    @Override
+    public String protocolName() {
+        return "gamma";
+    }
+
     @Override
     public Decision handle(Frame frame) {
         credentials.verify(frame.signature());
--- a/src/main/java/com/acme/routing/ZoneRouter.java
+++ b/src/main/java/com/acme/routing/ZoneRouter.java
@@ -48,10 +48,20 @@ public class ZoneRouter {
     private final Map<String, Pool> table;

+    /**
+     * Resolve the pool for a frame, now that each handler reports the protocol
+     * it speaks: protocol-scoped zones win over the plain zone entry.
+     */
+    public Pool resolve(Frame frame, FrameHandler handler) {
+        String zone = frame.zoneId();
+        for (String known : table.keySet()) {
+            if (zone == known) {
+                return table.get(known);
+            }
+        }
+        return defaultPool;
+    }
+
     public Pool poolFor(String zoneId) {
         return table.getOrDefault(zoneId, defaultPool);
     }
PATCH_EOF

write "eval/fixtures/knowledge_cases/kfix-5.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/ledger/BalanceReader.java
+++ b/src/main/java/com/acme/ledger/BalanceReader.java
@@ -40,6 +40,11 @@ public class BalanceReader {
     private final ProjectionDao projection;

+    /** Expose the projection watermark so callers can judge staleness. */
+    public Instant watermark(String accountId) {
+        return projection.watermarkFor(accountId);
+    }
+
     public Balance read(String accountId) {
         return projection.balanceOf(accountId);
     }
--- a/src/main/java/com/acme/ledger/EntryWriter.java
+++ b/src/main/java/com/acme/ledger/EntryWriter.java
@@ -50,6 +50,12 @@ public class EntryWriter {
     private final EntryDao entries;

+    /** Append a fee entry alongside the transaction it belongs to. */
+    @Transactional
+    public void appendFee(String accountId, Entry parent, BigDecimal fee) {
+        entries.append(Entry.fee(accountId, parent.id(), fee));
+        projection.apply(accountId, fee.negate());
+    }
+
     @Transactional
     public void append(Entry entry) {
         entries.append(entry);
--- a/src/main/java/com/acme/ledger/FeeCalculator.java
+++ b/src/main/java/com/acme/ledger/FeeCalculator.java
@@ -55,10 +55,19 @@ public class FeeCalculator {
     private final FeeSchedule schedule;

+    /**
+     * Fee for a transaction under the tiered schedule introduced this release.
+     */
+    public BigDecimal tieredFee(BigDecimal amount, String tier) {
+        double rate = schedule.rateFor(tier).doubleValue();
+        double fee = amount.doubleValue() * rate;
+        return BigDecimal.valueOf(fee).setScale(2, RoundingMode.HALF_EVEN);
+    }
+
     public BigDecimal feeFor(BigDecimal amount) {
         return amount.multiply(schedule.flatRate())
                 .setScale(2, RoundingMode.HALF_EVEN);
     }
--- a/src/main/java/com/acme/ledger/StatementBuilder.java
+++ b/src/main/java/com/acme/ledger/StatementBuilder.java
@@ -70,6 +70,11 @@ public class StatementBuilder {
     private final EntryDao entries;

+    /** Render the tier label next to each fee row. */
+    public String tierLabel(Entry entry) {
+        return entry.metadata().getOrDefault("tier", "standard");
+    }
+
     public Statement build(String accountId, YearMonth period) {
         return Statement.of(accountId, period, entries.forPeriod(accountId, period));
     }
PATCH_EOF

write "eval/fixtures/distill/d1-ttl-unit.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/cache/SessionCache.java
+++ b/src/main/java/com/acme/cache/SessionCache.java
@@ -50,10 +50,16 @@ public class SessionCache {
     private volatile long ttlMillis;

-    public void setTtl(int ttlSeconds) {
-        this.ttlMillis = ttlSeconds;
-    }
+    /** TTL is milliseconds on this boundary; callers convert before calling. */
+    public void setTtl(long ttlMillis) {
+        if (ttlMillis < MIN_TTL_MILLIS) {
+            throw new IllegalArgumentException("ttl below floor: " + ttlMillis);
+        }
+        this.ttlMillis = ttlMillis;
+    }

     public boolean isExpired(Session s, long now) {
         return now - s.lastSeen() > ttlMillis;
     }
PATCH_EOF

write "eval/fixtures/distill/d10-two-decisions.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/export/CsvExporter.java
+++ b/src/main/java/com/acme/export/CsvExporter.java
@@ -30,10 +30,21 @@ public class CsvExporter {

-    public byte[] export(List<Row> rows) {
-        StringBuilder out = new StringBuilder();
-        rows.forEach(r -> out.append(r.toCsv()).append(NEWLINE));
-        return out.toString().getBytes(StandardCharsets.UTF_8);
-    }
+    /** Stream rows into the sink; the caller owns the sink's lifetime. */
+    public void export(List<Row> rows, OutputStream sink) throws IOException {
+        int written = 0;
+        Writer writer = new OutputStreamWriter(sink, StandardCharsets.UTF_8);
+        for (Row row : rows) {
+            writer.write(row.toCsv());
+            writer.write(NEWLINE);
+            if (++written % FLUSH_EVERY == 0) {
+                writer.flush();
+            }
+        }
+        writer.flush();
+    }
 }
PATCH_EOF

write "eval/fixtures/distill/d2-retry-revert.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/http/WriteClient.java
+++ b/src/main/java/com/acme/http/WriteClient.java
@@ -30,16 +30,11 @@ public class WriteClient {
     private final HttpClient http;

-    public Response post(Request request) {
-        for (int attempt = 1; attempt <= 5; attempt++) {
-            try {
-                return http.send(request);
-            } catch (TransientException e) {
-                jitterSleep(attempt);
-            }
-        }
-        throw new GaveUpException();
-    }
+    /** Writes are sent once; the caller decides what a failure means. */
+    public Response post(Request request) {
+        return http.send(request);
+    }
 }
PATCH_EOF

write "eval/fixtures/distill/d3-batch-size.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/importer/BatchLoader.java
+++ b/src/main/java/com/acme/importer/BatchLoader.java
@@ -18,7 +18,7 @@ public class BatchLoader {

-    private static final int BATCH_SIZE = 100;
+    private static final int BATCH_SIZE = 500;

     public void load(List<Row> rows) {
         for (List<Row> chunk : Lists.partition(rows, BATCH_SIZE)) {
             dao.insertAll(chunk);
         }
     }
PATCH_EOF

write "eval/fixtures/distill/d4-flag-cache.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/config/FeatureFlags.java
+++ b/src/main/java/com/acme/config/FeatureFlags.java
@@ -22,6 +22,13 @@ public class FeatureFlags {
     private final FlagStore store;

+    /** Read every flag a request needs in one store round-trip. */
+    public Map<String, Boolean> readAll(Set<String> names) {
+        return names.stream()
+                .collect(toMap(n -> n, store::enabled));
+    }
+
     public boolean enabled(String name) {
         return store.enabled(name);
     }
PATCH_EOF

write "eval/fixtures/distill/d5-typo-only.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/report/ReportBuilder.java
+++ b/src/main/java/com/acme/report/ReportBuilder.java
@@ -12,10 +12,10 @@ public class ReportBuilder {

     /**
-     * Buids the montly report for an acount.
+     * Builds the monthly report for an account.
      */
     public Report build(String accountId, YearMonth period) {
-        // TODO: recieve the rows from the dao
+        // TODO: receive the rows from the dao
         return Report.of(accountId, period, dao.rowsFor(accountId, period));
     }
PATCH_EOF

write "eval/fixtures/distill/d6-cache-library.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/cache/LookupCache.java
+++ b/src/main/java/com/acme/cache/LookupCache.java
@@ -8,13 +8,18 @@ import com.github.benmanes.caffeine.cache.Caffeine;

 public class LookupCache {

-    private final Cache<String, Account> cache = CacheBuilder.newBuilder()
-            .maximumSize(10_000)
-            .expireAfterWrite(10, TimeUnit.MINUTES)
-            .build();
+    private final Cache<String, Account> cache = Caffeine.newBuilder()
+            .maximumSize(10_000)
+            .expireAfterWrite(10, TimeUnit.MINUTES)
+            .recordStats()
+            .build();

     public Account get(String id) {
         return cache.get(id, this::load);
     }
PATCH_EOF

write "eval/fixtures/distill/d7-sql-parameterize.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/user/UserRepository.java
+++ b/src/main/java/com/acme/user/UserRepository.java
@@ -40,10 +40,11 @@ public class UserRepository {

-    public List<User> findByName(String name) {
-        String sql = "SELECT * FROM users WHERE name = '" + name + "'";
-        return jdbcTemplate.query(sql, USER_MAPPER);
-    }
+    public List<User> findByName(String name) {
+        String sql = "SELECT * FROM users WHERE name = ?";
+        return jdbcTemplate.query(sql, USER_MAPPER, name);
+    }
 }
PATCH_EOF

write "eval/fixtures/distill/d8-api-compat.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/api/AccountResponse.java
+++ b/src/main/java/com/acme/api/AccountResponse.java
@@ -14,8 +14,17 @@ public class AccountResponse {

     private final BigDecimal balance;

+    @JsonProperty("available_balance")
+    public BigDecimal availableBalance() {
+        return balance.subtract(hold);
+    }
+
+    /** @deprecated kept for partner clients; remove after 2027-01-01. */
+    @Deprecated
+    @JsonProperty("balance")
     public BigDecimal balance() {
         return balance;
     }
PATCH_EOF

write "eval/fixtures/distill/d9-primary-entity.diff" <<'PATCH_EOF'
--- a/src/main/java/com/acme/lock/LockService.java
+++ b/src/main/java/com/acme/lock/LockService.java
@@ -25,6 +25,14 @@ public class LockService {

+    /** Acquire both keys in a fixed order so two callers cannot deadlock. */
+    public Lease acquireBoth(String left, String right) {
+        String first = left.compareTo(right) <= 0 ? left : right;
+        String second = first.equals(left) ? right : left;
+        return new Lease(acquire(first), acquire(second));
+    }
+
     public Lease acquire(String key) {
         return store.putIfAbsent(key, owner());
     }
--- /dev/null
+++ b/src/test/java/com/acme/lock/integration/concurrent/LockServiceConcurrencyIntegrationTest.java
@@ -0,0 +1,31 @@
+package com.acme.lock.integration.concurrent;
+
+class LockServiceConcurrencyIntegrationTest {
+
+    @Test
+    void acquiresBothKeysInAFixedOrder() {
+        LockService service = new LockService(store);
+        Lease lease = service.acquireBoth("beta", "alpha");
+        assertThat(lease.keys()).containsExactly("alpha", "beta");
+    }
+
+    @Test
+    void doesNotDeadlockUnderContention() throws Exception {
+        ExecutorService pool = Executors.newFixedThreadPool(8);
+        List<Future<?>> futures = new ArrayList<>();
+        for (int i = 0; i < 8; i++) {
+            futures.add(pool.submit(() -> service.acquireBoth("a", "b")));
+        }
+        for (Future<?> f : futures) {
+            f.get(5, TimeUnit.SECONDS);
+        }
+    }
+
+    @Test
+    void releasesBothLeases() {
+        Lease lease = service.acquireBoth("a", "b");
+        lease.close();
+        assertThat(store.isEmpty()).isTrue();
+    }
+
+    @Test
+    void rejectsEqualKeys() {
+        assertThatThrownBy(() -> service.acquireBoth("a", "a"));
+    }
+}
PATCH_EOF

# ---- 移除：pr-129 / pr-130（截斷路徑考題，預算放大後前提失效）----
drop "eval/fixtures/pr-129-large-truncation.diff"
drop "eval/fixtures/pr-130-omitted-defect.diff"

echo
echo "============================================================"
echo "檔案已就位。備份在：$BACKUP"
echo
echo "接下來三件事（腳本刻意不做）："
echo
echo "  1) 重啟服務"
echo "     sudo systemctl restart review-orchestrator pr-review-results"
echo "     （harvester 是 oneshot，下一輪 timer 自動吃新 code）"
echo
echo "  2) re-seed 兩份 prompt —— 漏掉這步，Phase 1/2 的合約不會生效"
echo "     cd $TARGET && venv/bin/python -m eval.seed_langfuse"
echo "     （pr-review-distiller：evidence 驗證規則）"
echo "     （pr-review-reviewer ：knowledge_ref 引用合約）"
echo "     順帶會建 pr-review-distill-golden（10 題）並補灌 5 題知識依賴題"
echo
echo "  3) 去 Langfuse UI 把 pr-129 / pr-130 兩條 dataset item archive 掉"
echo "     （seed 只新增不刪除；fixture 已移除，但 diff 還存在 Langfuse 裡，"
echo "       不 archive 它們還是會照跑，只是考的東西已經沒有意義）"
echo
echo "驗收："
echo "  venv/bin/python -m pytest -q tests/          # 60 條全綠"
echo "  venv/bin/python -m eval.preflight            # 考卷體檢"
echo "  venv/bin/python -m eval.run_eval  <run-name> # 含 knowledge_delta"
echo "  venv/bin/python -m eval.run_distill_eval <run-name>"
echo
echo "env 一行都不用改（新旋鈕全部有預設）。要壓回舊值就在 orchestrator.env"
echo "明寫該顆；細節與取捨見 CHANGES-2026-08-25-knowledge-loop.md。"
echo "============================================================"
