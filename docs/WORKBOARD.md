# 工作台（Workboard）實作規格

Control room 那一頁的重構：把 session 以「球在誰手上」分成五欄的看板，卡片就是 session，再補上
意圖、等待條件、審核狀態與帳本。Sidebar 與 SessionView 不換，三者讀同一份 session 清單。

互動原型：`pilcrow-desk.html`（對話中發佈的版本），本文件以它為準描述畫面，實作時以本文件為準。

---

## 1. 範圍

**做**

- 新頁面 `board`，取代 `overview`（Control room）。`Ctrl+Shift+O`、`Ctrl+O` 的循環、palette 的「Control room」改指向它。
- Inbox 併入看板：排程產生的 session 是卡片；排程清單搬到「等待中」欄標題的選單。`inbox` 頁與 `InboxPage` 移除，`ScheduleDialog` 保留。
- session 中繼資料新增四個欄位（§3），全部可選，舊資料不需遷移。
- 一個新的讀取 RPC `board.state`，四個寫入 RPC（§5）。
- 交接報告協定：系統提示附帶格式要求，伺服器解析最後一則回覆（§6）。
- 等待條件：時間（沿用 schedules）、CI、相依（§7）。
- 同時執行上限（§8）。
- Qt 客戶端同步實作（§11）。

**不做**

- 多人、權限、指派、通知收件匣。
- 自訂欄位、自訂狀態、泳道、篩選器。
- 跨專案的看板（第一版每個看板就是全部 session，和 Sidebar 一樣，專案用 chip 標示）。
- 新的持久化後端。仍用 `meta.json`。

---

## 2. 名詞

| 名詞 | 定義 |
|---|---|
| 卡片 | 一個未封存的 session。沒有獨立實體。 |
| 意圖 | `meta.intent`，兩三句目標與完成標準。有意圖的 session 才算「工作」；沒有的仍是普通對話，也顯示在看板，但卡片標題用現有的 `title`。 |
| 一輪 | 使用者送出一則訊息到 Claude 結束回覆。對應 `turnEnd`。 |
| 執行 | 看板語境下的一輪或連續多輪，由觸發（開始、退回、回答、喚醒）到下一次需要你。帳本以觸發切分。 |
| 審核 | 一次執行結束後，你看過結果並做了處置（核准、退回、放著）。 |
| 球在誰手上 | 卡片所在的欄：待辦、代理手上、等待中、需要你、完成。 |

---

## 3. 資料：`meta.json` 新欄位

全部存在既有的 `store.setMeta(id, patch)`（`server/config.js`）。`null` 會被 `setMeta` 刪除，所以「清掉」就是寫 `null`。

```ts
interface BoardMeta {
  /** 兩三句目標。開單時寫；從對話升格時補。 */
  intent?: string;
  /** 等待條件。存在且未滿足時卡片在「等待中」欄。 */
  wait?:
    | { kind: 'time'; at: number; prompt?: string }                      // 一次性時間；重複的用 schedules
    | { kind: 'ci'; cwd: string; branch: string; prompt?: string; since: number }
    | { kind: 'session'; id: string; prompt?: string };                   // 另一張卡進「完成」
  /** 最近一次審核處置對應的 turnEnd。等於目前 turnEnd 表示這一輪已處置。 */
  reviewedTurn?: number;
  /** 你的決定，只追加。帳本的人為事件來源。 */
  decisions?: Decision[];
  /** 最近一次解析成功的交接報告（§6）。 */
  report?: Report | null;
  /** 排隊中：超過同時上限時暫存要送的第一則訊息。 */
  queued?: { text: string; at: number } | null;
}

interface Decision {
  at: number;
  /** park = 「先放著」：記 reviewedTurn、不送任何訊息，卡回待辦。 */
  kind: 'approve' | 'reject' | 'answer' | 'park' | 'wait' | 'wake' | 'stop' | 'start' | 'steer';
  /** 退回的評論、回答的內容、插話的內容、等待的條件描述。 */
  note?: string;
  /** 核准時的 commit hash。 */
  commit?: string;
  /** 這個決定針對的 turnEnd。 */
  turn?: number;
}

interface Report {
  asked: string;   // 被要求
  did: string;     // 做了
  files: { path: string; add: number; del: number }[]; // 改了：伺服器從 diff 算，不信代理
  unverified: string; // 沒驗證
  ask: string;     // 要你決定
  turn: number;    // 來自哪一輪
}
```

`SessionSummary`（`web/src/lib/types.ts`）加同名可選欄位，由 `sessions.list()` 帶出。`qt/pilcrow_qt/lib` 的對應型別同步。

---

## 4. 欄位推導

欄位在客戶端推導，伺服器只提供事實。同一個函式 `laneOf(s, rt)` 放在 `web/src/lib/board.ts`，Qt 移植到 `qt/pilcrow_qt/lib/board.py`，兩邊各自有測試且案例相同。

**沒有意圖的 session 不是卡片。** 它只在正在做事時出現在看板（執行中、等你回答權限或問題）；
靜止時留在 Sidebar，不進任何欄。這樣看板上只有「工作」，對話不會把它塞滿。

| 欄 | 條件（由上往下，第一個符合的） |
|---|---|
| （不在看板） | 已封存；或沒有 `intent` 且不是 running / starting / requires_action |
| `me`（需要你） | `status === 'requires_action'`，或 `pending > 0`，或（`turnEnd` 存在 且 `reviewedTurn !== turnEnd` 且 `status` 不是 running/starting） |
| `agent`（代理手上） | `status` 是 `running` 或 `starting`，或 `queued` 存在，或 `status === 'error'`（卡住） |
| `wait`（等待中） | `wait` 存在 |
| `done`（完成） | `reviewedTurn === turnEnd` 且最近一個 decision 是 `approve`，且當天 |
| `backlog`（待辦） | 其餘：沒有任何一輪、或被停止、或已處置但沒核准 |

細則：

- 卡住：`status === 'error'`，或回覆結束但交接報告的 `ask` 以「無法」「需要」開頭且 `files` 為空。第一版只用 `error`，第二版再加語意判斷。
- 排程啟動的 session（`meta.scheduled`）在觸發前以 `wait: {kind:'time'}` 的形式出現；觸發後和其他卡一樣。schedules.js 在 `nextRun` 時不建立 session，所以「等待中」欄的排程卡是**虛擬卡**：由 `board.state` 從 `schedules.list` 產生，id 為 `sched:<scheduleId>`，不可拖，只能開排程對話框。
- 已封存的 session 不在看板上。核准後的卡在「完成」欄停留到當天結束，隔天自動消失（不封存）。
- `pending` 是現有的「等你回覆的訊息數」，沿用。

---

## 5. RPC

### 讀取

`board.state` 取代 `fleet.state`（保留舊名一版，回傳相同內容加新欄位）：

```ts
{
  live: Record<string, SessionState>;          // 同 fleet.state
  today: { cost: number; sessions: number; done: number };
  limit: { max: number; running: number };     // §8
  scheduled: { id: string; name: string; at: number; cwd: string }[]; // 虛擬卡
}
```

客戶端每 20 秒拉一次，`session.state`、`sessions.changed`、`schedules.changed` 推送時立即重算。不新增推送事件。

### 寫入

| RPC | 參數 | 行為 |
|---|---|---|
| `session.intent` | `{ id, intent }` | 寫 `meta.intent`。空字串寫 `null`。 |
| `session.decide` | `{ id, kind, note?, turn? }` | 追加一筆 `Decision`，並依 `kind` 連動：`approve` 先 `git.commit`（訊息用 `note` 或由 report 的 `did` 第一句組成），成功後寫 `reviewedTurn = turnEnd` 與 `commit`；`reject` 寫 `reviewedTurn` 後呼叫 `session.send` 送出 `note`；`answer` 同 `reject`；`stop` 呼叫 `session.interrupt` 並清 `queued`；`steer` 呼叫 `session.send`（現有的 held/queue 機制會在本輪內送達）；`wake` 清 `wait` 並送出 `wait.prompt` 或預設提示。 |
| `session.wait` | `{ id, wait \| null }` | 寫或清 `meta.wait`。`kind:'time'` 時向 scheduler 登記一次性喚醒；`'ci'` 時啟動輪詢（§7）；`'session'` 時登記相依。 |
| `session.start` | `{ id, text? }` | 待辦卡交給代理：若 `running < limit.max` 則 `session.send({ text: text ?? intentPrompt })`，否則寫 `queued` 並回 `{ queued: true }`。 |

`session.create` 加一個可選參數 `intent`，`startNow` 預設為 `prompt` 是否非空，保持現有行為。

錯誤處理沿用 `rpc.call` 的 toast；`approve` 的 commit 失敗時不寫 `reviewedTurn`，卡片留在「需要你」。

---

## 6. 交接報告協定

### 要求代理

`server/sessions.js` 組 `systemPrompt.append` 的地方（目前只放 `memoryText`）追加一段固定文字，受 `config.handoffReport !== false` 控制：

```
When you finish a piece of work and hand it back, end your reply with a section headed exactly
"## Handoff" containing these five lines, each starting with the label:
Asked: what you were asked to do, one sentence.
Did: what you did, one or two sentences.
Unverified: what you did not test or could not check. Write "nothing" if there is nothing.
Decide: the one question the person must answer, or "none".
Omit the section for ordinary conversation that produces no work.
```

### 解析

一輪結束（`turnEnd` 寫入處）時，伺服器讀最後一則 assistant 文字：

1. 找最後一個 `## Handoff` 標題；沒有則 `report = null`，卡片仍進「需要你」，說明行退回顯示 `lastReply.text` 前 120 字。
2. 逐行以 `Asked:` `Did:` `Unverified:` `Decide:` 取值，缺的欄位為空字串。
3. `files` 由伺服器算：worktree session 用 `git.diff(cwd, { base: worktree.base })`，否則用 `git.diff(cwd)` 的 working tree 差異；只取路徑與增減行數。
4. 寫 `meta.report`。

解析放在 `server/handoff.js`，純函式 `parseHandoff(text): Omit<Report,'files'|'turn'> | null`，有單元測試（`test/unit`）。

卡片說明行的優先順序：`requires_action` 時顯示權限或問題的標題；否則 `report.ask`；否則 `lastReply` 摘要。

---

## 7. 等待條件

`server/schedules.js` 已有 cron、每日、每 N 小時與 `nextRun`。擴充：

- **一次性時間** `wait.kind === 'time'`：scheduler 每分鐘的掃描加一條：所有 `meta.wait.kind === 'time' && at <= now` 的 session，清 `wait`、追加 `Decision{kind:'wake', note:'時間到'}`、送出 `wait.prompt`（預設「等待的時間到了，依照意圖繼續。」）。
- **CI** `wait.kind === 'ci'`：需要 `gh` 在 PATH。每 2 分鐘對 `cwd` 執行 `gh run list --branch <branch> --limit 1 --json status,conclusion,createdAt`，取 `createdAt >= since` 的最新一筆；`status === 'completed'` 時喚醒，提示帶上 `conclusion`。找不到 `gh` 時 `session.wait` 回錯誤，UI 不提供這個選項（`board.state` 回 `caps.ci: boolean`）。
- **相依** `wait.kind === 'session'`：`session.decide` 的 `approve` 成功後，掃描所有 `wait.kind==='session' && wait.id === id` 的 session 並喚醒。

喚醒一律走 `session.start` 的路徑，所以受同時上限約束，超過時進排隊。

---

## 8. 同時執行上限

`config.maxConcurrent`（預設 3，0 為不限）。計數是 `status` 為 `running` 或 `starting` 的 session 數。

- `session.start` 與喚醒超過上限時寫 `queued`，卡片在「代理手上」欄顯示「排隊」。
- 任一 session 的 `turnEnd` 寫入、或被 `interrupt`、或封存時，伺服器遞補：取 `queued.at` 最早的一張，清 `queued`，`session.send(queued.text)`。
- 使用者在 SessionView 手動送訊息不受上限限制（你在看著它）。

---

## 9. Web 介面

### 9.1 檔案

```
web/src/components/Board.tsx          頁面：strip、andon、五欄
web/src/components/board/Card.tsx     卡片
web/src/components/board/Lane.tsx     欄，含拖放目標
web/src/components/board/CardPanel.tsx 右側板：意圖、報告、帳本、動作
web/src/components/board/NewCard.tsx  待辦欄內的就地開單
web/src/lib/board.ts                  laneOf、dropTargets、ledgerOf、快捷鍵表
web/src/styles/board.css
```

`Overview.tsx`、`Inbox.tsx` 刪除；`panels.css` 中 `.ov-*`、`.inbox-*` 規則搬到 `board.css` 或刪除。

### 9.2 版面

```
┌ titlebar ──────────────────────────────────────────────────────────────┐
│ sidebar │ strip：日期 · N 件等你 · N 件代理手上 · N 件等待 · N 件完成 · $ │ panel │
│         │ andon（只在有卡住時）                                          │       │
│         │ 待辦 │ 代理手上 n/N │ 等待中 │ 需要你 │ 完成                    │       │
└─────────┴──────────────────────────────────────────────────────────────┴───────┘
```

- 欄寬 `minmax(228px, 1fr)`，看板橫向捲動，每欄獨立縱向捲動，欄標題 sticky。
- 右側板沿用 `ui.panel` / `ui.panelW` 與 `Resizer`，但在 board 頁內容是 `CardPanel`。沒有選取時側板收起。
- 視窗寬度小於 860px 時側板改為覆蓋層，和原型一致。

### 9.3 卡片

四層：`id + 狀態 chip` / 標題（最多兩行）/ 說明行（最多兩行，§6 的優先順序）/ 底列（模型縮寫與頭像、右側是進度與時間或增減行數與花費）。左側 3px 色條：待審核橘、執行中藍、提問黃、卡住紅；其他沒有。

顏色只用 `tokens.css` 既有 token：橘色只出現在「需要你」欄標題、待審核色條、主要按鈕。

### 9.4 動作與快捷鍵

動作集由 `laneOf` 與 `status` 決定，按鈕與快捷鍵同一張表（`web/src/lib/board.ts` 的 `actionsFor`）：

| 卡片狀態 | Enter | e | r | w | s | i |
|---|---|---|---|---|---|---|
| 待審核 | 開啟 session（Changes 面板） | 核准並 commit | 退回（文字框） | 先放著（`park`：寫 `reviewedTurn`，卡回待辦） | | |
| 提問 / 權限 | 開啟 session | | 自己寫答案 | | | |
| 執行中 | 開啟 session | | | | 停止 | 插話 |
| 排隊 | 開啟 session | | | | 移出排隊 | |
| 等待中 | 現在就喚醒 | | | 改條件 | 取消等待 | |
| 卡住 | 開啟 session | | 給它需要的東西 | | 停止 | |
| 待辦 | 交給代理 | | | 設條件再開始 | | |
| 完成 | 開啟 session | | | | | |

走位：`j` `k` 欄內上下、`h` `l` 換欄（保留同一個索引）、`1`–`5` 跳到欄首、`n` 開單、`Esc` 關側板或取消文字框、`Ctrl+Enter` 送出文字框。提問卡的 `1` `2` `3` 選 AskUserQuestion 的選項（優先於跳欄）。

權限請求（`requires_action` 且 `pending` 來自工具）的側板直接嵌入現有 `Permission` 卡片的 Allow / Always / Deny，鍵盤 `1` `2` `3` 與 SessionView 一致。

### 9.5 拖放

HTML5 drag。拖起時非法目標欄加 `.no`（淡化），合法目標 hover 加 `.over`。放下等於做動作：

| 放到 | 等於 |
|---|---|
| 代理手上 | `start`；卡住的卡改為開「退回」文字框 |
| 等待中 | 開條件選單 |
| 完成 | `approve`（只有待審核能放） |
| 待辦 | `stop` 或取消等待 |
| 需要你 | 永不合法 |

### 9.6 開單

待辦欄 + 或 `n`：欄頂長出 `NewCard`：標題（必填）、意圖（textarea，可空）、專案 chip（預設最近一個，點選換，`…` 開 `FolderPicker`）、選項列（worktree 開關預設開、模型）。`Enter` 建立待辦（`session.create` 帶 `intent`、不帶 prompt），`Ctrl+Enter` 建立並 `session.start`。意圖留空時以標題為一行意圖，卡片才會在看板上。

`NewSessionDialog` 的 prompt 欄位標籤改為「意圖（也是第一則訊息）」，送出時同時寫 `intent`。

SessionView 標題列選單加「寫成卡片」：開一個只有意圖欄的小對話框，寫入 `intent`。

### 9.7 SessionView 的改動

- 從看板開啟時 `ui.returnTo = 'board'`，標題列顯示「← 工作台」，`Esc`（非輸入狀態且沒在執行）回看板並保持選取。
- 待審核卡開啟時 `setUi({ panel: true, panelTab: 'changes' })`。
- `Info` 面板新增「意圖」與「帳本」兩段，帳本與 `CardPanel` 共用 `Ledger` 元件。

### 9.8 Sidebar

不改結構。`Needs you` 區塊改用 `laneOf(s) === 'me'`，確保與看板一致。排程相關的 Inbox 入口移除，改為 Sidebar 底部的「工作台」按鈕（原 Control room 按鈕）。

### 9.9 帳本

`ledgerOf(s, rt, history?)` 由以下來源合成並依時間排序：

| 事件 | 來源 |
|---|---|
| 建立意圖 | `createdAt` 與 `intent` |
| 執行 #n | 每次 `start`/`reject`/`answer`/`wake` decision 之後的第一輪，到下一個 decision 為止；顯示模型、耗時（`turnStartedAt`→`turnEnd`）、費用差 |
| 你的決定 | `decisions[]` |
| 交接報告 | `report.turn` |
| commit | `decisions[].commit` |

側板第一版只需要 `meta` 與 `rt`，不載入 transcript。

---

## 10. 移除與相容

- `page` 型別：`'session' | 'board'`。讀到舊的 `'overview'`/`'inbox'` 一律當 `'board'`。
- `fleet.state` 保留一版作為 `board.state` 的別名，供舊的 Qt 客戶端使用；CHANGELOG 註明下一版移除。
- `inbox.*` RPC 保留，`InboxItem` 的資料不再產生新項目（排程跑完的結果就是卡片與報告）。既有 `inbox.json` 不刪。
- `.ov-*`、`.inbox-*` 樣式與 `test/e2e/shots.mjs` 的 overview、inbox 場景改為 board 場景。

---

## 11. Qt 客戶端

依 `qt/ARCHITECTURE.md` 的規則移植，React 為規格：

| Web | Qt |
|---|---|
| `Board.tsx` | `widgets/board.py`，取代 `overview.py` 與 `inbox.py` |
| `Card.tsx` / `Lane.tsx` | 同檔內的 `Card(QFrame)`、`Lane(QScrollArea)`；拖放用 `QDrag` 與 `dragEnterEvent` |
| `CardPanel.tsx` | `widgets/panels/card_panel.py`，掛在既有右側板容器 |
| `NewCard.tsx` | `widgets/board.py` 內 `NewCard(QFrame)` |
| `lib/board.ts` | `lib/board.py`，純函式，測試案例與 web 共用一份 JSON（`test/unit/board-cases.json`） |

快捷鍵、顏色、字級以 web 為準，`snap.py` 加 `board` 場景與 web 截圖比對。

---

## 12. 測試

| 層 | 內容 |
|---|---|
| 單元（web、Qt 共用案例） | `laneOf`、`dropTargets`、`actionsFor`、`ledgerOf`、`parseHandoff` |
| 伺服器 | `session.decide` 各 kind 的連動；排隊與遞補；三種等待條件的喚醒；`approve` commit 失敗不寫 `reviewedTurn` |
| mock server | `board.state` 回五欄都有卡的固定資料；`session.decide` 等寫入會改 mock 狀態並推 `session.state` |
| e2e | 開單→交給代理→完成→核准 的一條路徑；拖放一次；鍵盤走一圈 |
| 截圖 | web 與 Qt 的 board 場景、側板三種狀態（待審核、提問、執行中） |

---

## 13. 分階段

1. **資料與推導**：§3、§4、`board.state`、`session.intent`、`session.decide` 的 `approve`/`reject`/`stop`；Board 頁五欄唯讀加側板加核准退回；Sidebar 的 Needs you 改用 `laneOf`。此時 Overview 可以下線。
2. **開單與交接報告**：§6、§9.6、`NewSessionDialog` 的 intent；卡片說明行用 `report.ask`。
3. **等待與上限**：§7、§8、Inbox 併入、排程虛擬卡。
4. **拖放、帳本、Qt**：§9.5、§9.9、§11、截圖比對。

每一階段獨立可發佈，CHANGELOG 各記一條。

---

## 14. 已決與待決

第 1 階段動工時定案：

- 「先放著」是 `park`：寫 `reviewedTurn`、不送訊息，卡回待辦。
- `approve` 的 commit 訊息：退回文字框沒填時用 session 標題第一行（120 字內）；第 2 階段有 report 後改為 `did` 第一句。
- commit 範圍：第 1 階段一律 `git add -A`（整個 working tree，worktree session 本來就是獨立目錄）。
  非 worktree session 限縮到本輪 touched 檔案留到第 2 階段，與交接報告的 `files` 一起做。
- 「沒有東西可 commit」視為核准成功（例如代理只回答問題）；其他 commit 失敗不寫 `reviewedTurn`。
- `stop` 之後 session 送回的 turn end 由 `Session.autoReview` 直接標為已審，不再進「需要你」。

仍待決：

- CI 等待是否也支援 GitLab（`glab`）。第一版只做 `gh`。
- Sidebar 的 Needs you 現在包含「做完還沒審」的卡，數量多時是否要分成兩段（提問在上、待審在下）。

## 15. 進度

- 第 3 階段（2026-11-08）：`session.wait` 與三種條件（時間、CI 用 `gh run list` 每 2 分鐘輪詢、相依在核准時喚醒）；
  `board.tick` 每 30 秒跑；`maxConcurrent` 上限與排隊（`meta.queued`，turn end 與 tick 遞補；手動送訊息不受限）；
  排程觸發前以虛擬卡出現在「等待中」，排程清單與新增移到該欄標題的選單；排程啟動的 session 帶 `intent`，跑完進「需要你」。
  Inbox 頁與其 CSS 移除，`inbox.*` RPC 與 `inbox.json` 保留。側板的等待選單：CI（沒有 gh 時停用）、明天 09:00、一小時後、
  自選時間、等另一張卡。

- 第 2 階段（2026-11-08）：`server/handoff.js`（`HANDOFF_PROMPT` 附在系統提示、`parseHandoff`、`filesChanged` 由 diff 量）；
  一輪結束時寫 `meta.report`；`approve` 的 commit 訊息用 report 的 `did` 第一句，非 worktree session 只 add report 的檔案；
  待辦欄就地開單（`NewCard`：標題、意圖、專案 chip、worktree、模型，Enter / Ctrl+Enter）；New session 對話框的第一則訊息
  即意圖；session 選單「Make it a card / Edit the intent」；卡片說明行與側板顯示交接報告。`config.handoffReport=false` 可關。

- 第 1 階段（2026-11-08）：`server/board.js`、`board.state` 與三個寫入 RPC、`web/src/lib/board.ts` 與單元測試、
  `Board.tsx` 五欄＋側板＋核准/退回/先放著/停止/交給代理、權限在側板直接回答、Sidebar 的 Needs you 改用 `laneOf`、
  Control room 下線。mock server 與 e2e 有對應案例。Qt 仍走 `fleet.state` 別名，顯示舊的 overview。
