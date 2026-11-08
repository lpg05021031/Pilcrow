# 專案記憶 v2：會被驗證的筆記（playbook）

> 狀態：第 1、2 階段已實作（`server/memory.js`、`server/board.js`、`server/sessions.js`、Memory 頁、看板上的筆記卡）。
> 對使用者的日常操作沒有新增任何步驟；差別只在 Memory 頁上多了來源與戰績、新 session 收到的筆記排序不同，
> 以及當你的退回和某條筆記說相反的話時，看板「需要你」多一張卡讓你決定留或刪。

## 1. 為什麼

原本的專案記憶是「每回合結束，小模型讀 transcript，寫幾條筆記」。問題有兩個：

1. 筆記只有模型的印象，沒有被任何結果驗證過。寫錯的筆記會一直被交給新 session。
2. 筆記和 Claude Code 自己的記憶（`CLAUDE.md`、`~/.claude/projects/<key>/memory/`）會重疊，兩邊都在說同一件事，甚至說相反的事。

ACE（Agentic Context Engineering, ICLR 2026）的做法是把 context 當成會長大的 playbook：每一條有來源、有「幫了幾次 / 誤導幾次」的計數，靠真實結果（而不是模型自評）決定去留。工作台（docs/WORKBOARD.md）剛好提供了真實結果：你在卡片上按下的 **核准** 與 **退回**。

## 2. 分工：不碰 Claude Code 的記憶

| 層 | 誰寫 | 誰讀 | Pilcrow 做什麼 |
|---|---|---|---|
| `CLAUDE.md` / `.claude/CLAUDE.md` / `CLAUDE.local.md` | 你 | Claude Code 自己 | 只讀，用來去重；永不寫入 |
| Claude Code auto-memory（`~/.claude/projects/<key>/memory/*.md`） | 模型 | Claude Code 自己 | 只讀，用來去重；永不寫入 |
| Pilcrow 專案記憶（`<data>/memory/<folderKey>.json`） | 回合結束的小模型、你的退回、你的手 | 透過 system prompt append 交給新 session | 全部 |

注入區塊開頭明寫優先序：「CLAUDE.md 與你自己的記憶優先，有衝突以程式碼為準，發現過期就說」。
`<key>` 是 Claude Code 的規則：絕對路徑中非英數字元全部換成 `-`（`memory.claudeProjectDir`）。

## 3. 一條筆記

```ts
{
  id, at, text,
  kind: 'auto' | 'manual',
  source: 'turn' | 'reject' | 'hand',   // 回合結束蒸餾 / 你在卡片上的退回 / 你手寫
  sessionId, sessionTitle, pinned, editedAt?,
  helpful: number,   // 讀過它的 session 被你核准的次數
  harmful: number,   // 退回時被反思判定「誤導了這次工作」的次數
}
```

舊檔案沒有 `source / helpful / harmful` 的筆記照常讀，缺的欄位視為 0 / `turn`。`list()` 另外附上算出來的 `standing`：

| standing | 條件 |
|---|---|
| `confirmed` | 手寫或釘選；或 `helpful ≥ 1` 且 `helpful > harmful` |
| `retired` | `harmful ≥ 2` 且 `harmful > helpful`（不釘選、不手寫） |
| `fresh` | 其餘 |

`retired` 的筆記留在檔案裡、Memory 頁上劃掉顯示，但不再交給新 session；釘選可以救回來。容量到 80 條時先丟 retired，再丟最舊的自動筆記。

## 4. 訊號（Reflector）

| 事件 | 來源 | 做什麼 | 花費 |
|---|---|---|---|
| session 啟動 | `sessions.js start()` | `memory.inject(project)` 回 `{ text, ids }`，`ids` 存進 `meta.playbook` | 無 |
| 回合結束且值得 | `sessions.js`（原有） | `distill()`：最多 5 條，`source:'turn'` | 一次小模型呼叫（原有） |
| 你按 **核准** | `board.decide('approve')` | `memory.credit(session)`：`meta.playbook` 裡每條 `helpful += 1` | 無 |
| 你按 **退回** 並寫了原因 | `board.decide('reject')` | `memory.reflect(session, note)`：小模型讀你的退回、交接報告的 Did/Unverified、這個 session 拿到的筆記（編號），輸出最多 2 條新規則（`source:'reject'`）與 `MISLED: 1, 3`（那些筆記 `harmful += 1`） | 一次小模型呼叫，不等它回來才回應 UI |

`reflect` 與 `distill` 同受「Take notes automatically」開關控制，用同一個 note-taking 模型。回答、先放著、停止、喚醒不產生訊號。

### 4.1 矛盾 → 需要你的卡片

被 `MISLED` 點名的筆記除了 `harmful += 1`，還會記上 `disputed = { at, sessionId, sessionTitle, correction, replacement }`
（`replacement` 是同一次反思寫出的第一條新規則的 id，可能沒有）。

- `memory.conflicts()` 掃所有專案的記憶檔，列出帶 `disputed` 的筆記；`board.state()` 以 `conflicts[]` 回傳。
- 兩端的看板把每條做成一張虛擬卡（id 前綴 `note:`，如同排程的 `sched:`），固定在「需要你」欄，chip 寫 **Memory**，不能拖曳、沒有對話可開。
  面板顯示：這條筆記、你的退回原文（與是哪張卡）、「改寫成」的新筆記、以及一行說明。
- 動作只有兩個：**Forget the note**（↵，從這個專案的記憶移除）與 **Keep it**（e，清掉旗標；harmful 計數留著，新 session 會再拿到它）。
  RPC 是 `memory.resolve { cwd, id, keep }`。
- 在 Memory 頁改寫或釘選一條被點名的筆記，也算「留著」。
- Memory 頁上同一條筆記會有一行「Contradicted by your correction on …: “…”」與同樣的 Keep / Forget。

`k` 在看板上是向上移動，所以 Keep 用 `e`（與核准同鍵，語意也是「接受」）。

## 5. 去重與注入

- `similar(a, b)`：取兩句的實詞（小寫、≥3 字、去 stop words），較短那句有 ≥ 75% 的詞出現在另一句即視為同一件事；短句（< 3 詞）要完全相同。
- 新筆記進來（蒸餾或反思）時，和既有筆記、`CLAUDE.md` 三個檔案、Claude Code auto-memory 的每一行比對，重複就不收。
- 注入時再比一次（CLAUDE.md 之後可能才加上同一句），重複的跳過。
- 注入順序：釘選 → `confirmed` → `fresh`，同級依 `helpful − harmful`，再依時間；`retired` 永不注入；總量仍以 2600 字元為上限。

## 6. 介面

- Web Memory 頁：每條筆記的來源行多出「From your correction on <session>」、「helped n, misled n」、「confirmed」或「retired」；retired 劃掉、淡色，滑過有說明。
  `confirmed` 的筆記多一個「Copy as a CLAUDE.md line」按鈕：把 `- <text>` 放進剪貼簿，由你自己貼進 CLAUDE.md；Pilcrow 不寫那個檔案。
- Qt 設定 › Memory：同一行文字（`note_meta`）、同一個複製按鈕、同一列 Keep / Forget。沒有新設定。
- 看板：只在有矛盾時多一張 Memory 卡（§4.1）；其餘卡片沒有變化。

## 7. 測試

`test/unit/memory.test.ts`：standing 與排序、注入回 ids 並略過 retired 與 CLAUDE.md / auto-memory 已說的、`credit` 加分、`reflect` 新增規則並標記 MISLED、舊格式的檔案照讀、`conflicts()` 跨專案列出並由 `resolve` / 改寫 / 釘選清掉。既有的 distill / promptFor 測試不變。

看板：`test/unit/board.test.ts` 與 `qt/tests/test_lib_board.py` 各有一條 conflict card 的案例；mock server 種了一張（`note:note-cents`），`test/e2e/run.mjs` 從面板按 Forget、`qt/tests/test_board.py` 按 Keep，卡都要離開看板。

另外修了 `test/unit/setup.ts`：Windows 上測試原本把筆記寫進真實的 `%LOCALAPPDATA%\Pilcrow\data\memory`，現在 `APPDATA` / `LOCALAPPDATA` 也指到測試用的 home。

## 8. 還沒做（下一階段）

1. **量測**：記錄每條筆記被注入的次數與核准 / 退回的比例，看 playbook 有沒有降低退回率；沒有就拿掉反思那一步。
2. **衝突卡的第三個選項**：現在只有留 / 刪；「用新規則取代」目前等於刪舊的（新規則已經在），之後若新規則常常也錯，再考慮讓卡上能直接改寫。
