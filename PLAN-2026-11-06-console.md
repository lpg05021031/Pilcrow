# pr-review 重構計畫：一個行程、一份設定、一個控制台（2026-11-06 草案）

> 進度：S1 併行程 ✅（PR#5，`CHANGES-2026-11-06-s1.md`）· S2 設定載體 + Settings 頁 ✅
> （`CHANGES-2026-11-06-s2.md`）· S3 Overview/Sources/Reviews ⏳ · S4 Knowledge 頁 + 安裝精靈 ⏳

## 0. 問題不是「缺一個 UI」

今天的維運痛點是**形狀**的問題：一套給一兩個人維運、跑在一台 Linux 機上的系統，
長成了 5 個行程、6 個 port、10 個 systemd unit、4 份 env 檔、64 個變數、
一本 10 個 Phase 的 RUNBOOK。昨天把變數收成一個 schema（PR#1）、把判讀收成
doctor（PR#3），是在這個形狀上補洞。這份計畫改形狀。

目標只有一句：**打開一個網頁，30 秒內知道系統現在怎樣、要改什麼在哪裡改。**
UI 是結果，不是手段；手段是先把系統縮到「一個網頁裝得下」的大小。

## 1. 目標形狀

```
pr-review（一個行程，:8080，一個 systemd unit）
├─ /v1/chat/completions   Open WebUI 的入口（不變）
├─ /review  /healthz      腳本與 CI 的入口（不變）
├─ /console/*             控制台（新）
├─ core/                  anatomy → static → plan → reviewers → adversary → synthesis → render（不變）
├─ adapters/              ado（讀＋寫，原 ①③ 合一）、llm、langfuse、graph（選配、仍是外部行程）
├─ sources/               repos / rules / knowledge：三個同形的 Source 物件
│                         （path、git sha、last_sync、sync()、health()）
├─ jobs/                  行程內排程（asyncio）：repo-sync、knowledge-harvest，同一把鎖
└─ config/                /etc/pr-review/config.toml（可由 UI 編輯）+ secrets.env（只進不出）

外部只剩：Langfuse（:3000，trace / prompt / eval）、Open WebUI（:3001，聊天入口）、
code graph（:8097，選配）。這三個本來就是外部的。
```

| | 現在 | 目標 |
|---|---|---|
| 行程 | 5（orchestrator、①、②、③、harvester oneshot）+ 2 timer | 1 |
| port | 8080、8094、8095、8096（+8097）| 8080（+8097）|
| systemd unit | 10 | 1 |
| 設定檔 | 4 份 env（PR#1 後 1 份）| config.toml + secrets.env |
| 設定入口 | 文字編輯器 + restart | 控制台表單 + Apply（熱重載）|
| 部署文件 | RUNBOOK 10 Phase | `pr-review install` + 首次啟動精靈 |
| 看現況 | journalctl × 5 + curl + /proc | 控制台首頁 |

## 2. 為什麼可以砍成一個行程

- ①②③ 三個 MCP server **唯一的呼叫者是 orchestrator，而且在同一台機器**。
  中間那層 `clients.py` 只有 5 個函式、被 3 個檔呼叫。把它們變成行程內的
  普通函式，簽名不變，pipeline 一行不改。
- ② repo_reader 已經零呼叫者，直接刪。
- 「讀寫分權給未來 gateway 治理」是為一個不存在的東西付的持續成本。改以
  `ado.post_enabled` 一顆開關 + 控制台的發文稽核紀錄取代；今天的 AUTO_POST × DRY_RUN
  雙保險語意保留，只是住在同一處。
- harvester「唯一 commit 者」的不變式，改成行程內單一排程迴圈持同一把 asyncio 鎖，
  保證比兩個 oneshot timer 更強（同一個 event loop 不可能並發）。
- repo-sync.sh 的 git pull 搬進 Source.sync()，失敗原因第一次能被 UI 看見。
- 單一行程倒了全倒：今天 orchestrator 倒了本來就全倒，沒有變差。

## 3. 設定：schema 不變、載體換、入口換

- schema 就是昨天的 `shared/settings.py`（12 組），不重寫。
- 載體：`config.toml`（分組與 settings 一致）+ `secrets.env`（ADO_PAT、LLM_API_KEY、
  LANGFUSE_*、ADMIN_TOKEN；600 權限；UI 只顯示「已設 / 未設」，永不回顯）。
  pydantic-settings 原生支援 toml source；**環境變數仍可覆寫**（env > toml > 預設），
  所以 91 條測試與 run_eval 的對照組手法原樣可用。
- 入口：控制台 Settings 頁由 schema 自動生成表單——型別決定控件、description 是說明、
  範圍是驗證、來源（預設 / 檔案 / 環境變數）標在旁邊。Apply = 寫 toml（先備份）→
  `settings.reset()` 熱重載 → 該組 doctor 檢查立刻重跑。只有 host/port 需要 restart，
  UI 會說。
- `pr-review config migrate`：把現有 suite.env 轉成 toml + secrets.env，一次性。

## 4. 控制台：五頁，不多

| 頁 | 回答的問題 | 內容 |
|---|---|---|
| **Overview** | 系統現在怎樣？ | doctor 那張表（即時）、三個 Source 的 sha / 上次同步 / 錯誤、最近 10 條 review（PR、finding 數、耗時、trace 連結）、哪些功能開著（K 段、graph、rules、auto-post）|
| **Settings** | 要改什麼在哪？ | 12 組表單、來源標示、Apply 熱重載、改動紀錄（誰、何時、哪顆、舊→新）|
| **Sources** | 餵給 reviewer 的東西新不新？ | repos：每顆 clone 的分支 / sha / 上次 sync / 錯誤、Sync now、新增 repo；rules：sha、檔案樹、**「對 repo X 預覽注入」**（實際跑 load_rules，看哪些檔進 prompt、有沒有截斷）；knowledge：sha、頁數、pending 數、同樣可預覽注入 |
| **Reviews** | 剛才那條 review 發生了什麼？ | 列表 + 詳情（報告、stats、注入了哪些 rules / knowledge、adversary 結果、trace 連結）；「跑一條 review」表單（沿用現有 SSE 直播）——不開 Open WebUI 也能測 |
| **Knowledge** | 帳本在等誰？ | pending 稿、digest 預覽、reject / keep 按鈕 |

技術選擇，全部為了「內網、零 build、一個人維護」：FastAPI + Jinja2 伺服端渲染、
htmx（一個 14 KB 檔，vendor 進 repo，內網沒有 CDN）、一支自寫 CSS。review 歷史
用 SQLite（標準庫），放 `/var/lib/pr-review/`。認證：一個 ADMIN_TOKEN 換 cookie。
不引入 node、不引入資料庫服務——「新增外部依賴：0」繼續成立。

## 5. 分段上線（每段都能單獨部署，生產不中斷）

**S1 併行程**（砍最多的一步）
①③ 的函式搬進 `adapters/ado.py`，`clients.py` 變成直接呼叫；② 刪；repo-sync 與
harvest 變成 `jobs/` 的排程迴圈（保留 `pr-review harvest --once` 讓舊 timer 過渡期仍可用）。
systemd 10 → 1。tools/、deploy/systemd 大半、兩支 .sh 單檔包退場。
完成條件：doctor 的 services 組只剩 orchestrator 一列 OK；91 條測試不動。

**S2 設定載體 + Settings 頁**
toml source、secrets.env、`config migrate`、Settings 頁（schema 生成表單、Apply 熱重載、
改動紀錄）。完成條件：不開編輯器、不 restart，從 UI 把 REVIEW_PIPELINE 切 v1↔v2 並在
下一條 review 的 trace 看到。

**S3 Overview + Sources + Reviews**
Source 物件、SQLite review 歷史、三頁 UI、「跑一條 review」。完成條件：一個沒看過
RUNBOOK 的人打開首頁能說出系統狀態；Sync now 失敗時原因顯示在頁面而不是 journal。

**S4 Knowledge 頁 + 安裝精靈**
pending / digest / reject / keep；`pr-review install`（寫 unit、建目錄、空 config）；
首次啟動精靈逐步帶：LLM gateway → Langfuse → ADO PAT → repos → rules → knowledge，
每步就是 doctor 對應的檢查。RUNBOOK 縮成一頁。

## 6. 不變的東西（刻意）

- review pipeline 的九站、確定性分工、Finding / ReviewResult 合約。
- Langfuse 作為 trace / prompt 版控 / eval 儀表；eval 體系與 45 題考卷。
- rules 與 knowledge 都是 git repo、SHA 錨、targeted 注入；harvester 的裁決邏輯。
- Open WebUI 作為終端使用者的聊天入口。

## 7. 要先講清楚的取捨

- MCP 包裝曾是「日後 agentic tool loop」的接縫。adapters 保持純函式，要再包成
  MCP 是一個薄殼的事；現在不為可能性付五個行程的租金。
- Settings 頁可寫 = UI 能改生產行為。用 ADMIN_TOKEN、改動紀錄、secrets 永不回顯、
  危險旋鈕（AUTO_POST、DRY_RUN、KNOWLEDGE_POST）二次確認來守。
- 這是一次把 tools/ 與 deploy/ 大半刪掉的重構；每段獨立 PR，S1 完成前生產不需要動。
