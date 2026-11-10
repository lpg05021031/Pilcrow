# 互動導覽

`pilcrow-tour.html` 是一份單一檔案的互動導覽，用來向沒用過 Pilcrow 的人介紹它：畫面就是 Pilcrow 的介面，按一步、介面上發生一件事。
檔案不需要伺服器，也不連外，直接用瀏覽器開啟即可。

## 內容

共 52 站，分成四段：

| 段落 | 內容 |
|---|---|
| 緒論 | 標題、背景與動機、設計原則、報告大綱 |
| 第一部分：圖形介面 | 介面總覽；啟動與 Session、對話、側邊面板（七個分頁）、搜尋與鍵盤操作、設定 |
| 第二部分：多 agent 協作 | 工作台、排程、Repositories 註冊表、Dispatch、Helpers、Workflow、專案記憶 |
| 第三部分：開發方法 | 三項方法與 Q&A |

每一組功能前有一頁導引，說明這組功能在視窗的哪一區、接下來有哪幾項。

## 操作

| 按鍵 | 作用 |
|---|---|
| `→`、空白鍵、`Enter` | 下一步；動畫播放中再按一次，直接跳到該步的結果 |
| `←` | 上一步 |
| `Ctrl K`、`/` | 開啟目錄，跳到任一站 |
| `N` | 顯示或隱藏講稿 |
| `Home` | 回到開頭 |

介面上的控制項也能點：側邊面板的分頁、Settings 左側的分類、側欄的 Workboard 與 Dispatch 會跳到對應的站；
權限卡的 Allow 與審核面板的 Approve 會推進該站。側欄左下角的外觀按鈕切換深色與淺色。

## 做法

導覽不是重畫的介面，而是把真實介面的畫面「錄」下來重播：

1. `capture.mjs` 用 Playwright 開啟建置好的介面（`web/dist`），接上假資料伺服器（`test/mock`），
   依一段預先安排的工作過程操作介面，在每個狀態存下 `.app` 的 DOM、portal 到 `<body>` 的對話框與選單，以及一張截圖。
2. `build.mjs` 把介面自己的樣式表、這些畫面與 `src/` 的導覽層組成單一 HTML。
3. 播放時，導覽層把目前的 DOM 變形成下一個畫面，並補上位移與展開的動畫、滑鼠指標、重點外框與底部的導覽列。

因此介面改版後，重新擷取、重新建置，導覽就會跟上；每一站要框出的元素由 `src/c-engine.js` 以選擇器指定，
介面的類別名稱改了，這裡要跟著改。

## 重新產生

```bash
npm run build                     # 介面的 bundle（web/dist）
node docs/tour/capture.mjs        # 擷取畫面到 test/out/tour（約五分鐘）
node docs/tour/build.mjs          # 產生 docs/tour/pilcrow-tour.html
```

`build.mjs` 可以指定擷取資料夾與輸出檔：`node docs/tour/build.mjs <captureDir> <outFile>`；
加上 `--artifact <file>` 會另外輸出不含文件外殼的版本，供自帶外殼的平台使用。

擷取完成後，`test/out/tour/` 裡每個畫面都有一張 `.png`，可以先用眼睛確認畫面正確再建置。

## 檔案

| 路徑 | 內容 |
|---|---|
| `pilcrow-tour.html` | 建置結果，可直接開啟或分享 |
| `capture.mjs` | 擷取腳本：範例資料、操作步驟與畫面的存檔 |
| `build.mjs` | 建置腳本 |
| `src/0-stops.js` | 每一站的標題、主張與講稿，以及分組 |
| `src/c-engine.js` | 每一站使用的畫面與要框出的元素 |
| `src/d-play.js`、`src/d-cursor.js` | 每一站的動作編排，以及滑鼠指標與按鍵提示 |
| `src/e-cards.js` | 文字卡片、各組導引頁與參考出處 |
| `src/a-core.js`、`src/b-view.js` | DOM 變形與畫面切換的動畫 |
| `src/c-move.js`、`src/e-ui.js`、`src/f-input.js` | 換站、底部導覽列、講稿、目錄與輸入處理 |
| `src/tour-*.css`、`src/body.html` | 導覽層的樣式與骨架（類別一律以 `tx-` 開頭，不影響介面的樣式表） |

## 已知限制

- 畫面是重播，不是可操作的介面：沒有收錄的按鈕按了只會提示「示範版未收錄此功能」。
- Helpers、Dispatch 與 Workflow 三站的對話內容來自假資料伺服器內建的英文劇本；觸發它們的提示也因此是英文。
- Preview 分頁的 iframe 無法放進單一檔案，頁面內容是靜態重繪的。
- 版面以桌面寬度為準；手機寬度可以瀏覽，但不顯示滑鼠指標，側欄也會隱藏。
