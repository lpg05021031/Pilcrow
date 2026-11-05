# 變更說明 — 2026-11-06（控制台視覺重做）

S2–S4 的控制台「能用但沒有設計」。這一包不改任何路由、資料或測試可見的字串，只把六頁
套上一套設計系統。136 條測試原樣通過。

## 設計系統（`orchestrator/console/static/console.css`，一支檔、零外部資源）

- **版型**：固定左側導覽 232px（品牌、六個入口附 SVG 圖示、登入狀態）+ 內容區。
  每頁一條標題列：標題 / 一句副標 / 右側主要動作（重新檢查、立刻 harvest、PR、trace）。
  通知（開放模式、設定警示、訊息）集中在標題列下方一個區域，不再每頁各自長一條。
- **色彩**：帶藍灰偏向的中性色階（bg / surface / surface-2 / line / fg 三階），一個深青色
  accent；OK / WARN / FAIL / SKIP 四個狀態色與 accent 分開，各有實色與淺底。亮暗兩套
  跟系統 `prefers-color-scheme`。
- **型別**：系統字體 + Noto Sans TC；ID、sha、路徑、環境變數名一律等寬；數字 tabular。
  階層只有四級：頁標題 20、卡片標題 14、內文 13、輔助 11.5–12.5。
- **元件**：卡片（標題列 + 內容，邊框不用陰影）、狀態 pill（前面一顆點）、來源 pill
  （env / file / default）、嚴重度計數（error / warning / info）、統計 tile（左側色條）、
  表格（大寫小字表頭、hover、分組列）、switch 開關、按鈕四種（primary / default / ghost /
  danger）三種尺寸、空狀態、折疊明細。
- **響應式**：≤1100 雙欄變單欄；≤820 側欄變頂欄、只留圖示，長路徑可斷字。

## 各頁

| 頁 | 主要變化 |
|---|---|
| Overview | 四格統計 tile 帶色條；健康檢查表加分組列；餵料 / 最近 review / 排程 / 寫入四張卡等寬排右欄 |
| Settings | 每組一張卡，標題列右側 Apply；欄位名等寬、說明與預設在下方；bool 變 switch；來源 pill；已套用用綠勾 |
| Sources | 三段各一張卡；repos 變真正的表格（branch@sha、最後 commit、上次 fetch）；預覽注入在右半欄 |
| Reviews | 跑一條 review 的表單獨立一張卡；列表加表頭、嚴重度計數用色塊 |
| Review 詳情 | 標題列直接帶 PR 與 trace 按鈕；findings 表 F 編號用色 pill；「吃到了什麼」改 key / value 清單 |
| Knowledge | 待裁決 / 暫存在左、裁決 / 驗收窗 / digest 在右；reject 按鈕用 danger 樣式、keep 用一般樣式 |
| Setup | 每步一張卡，左側色條 + 圓形步驟號 + 狀態 pill；「設定 <group>」變按鈕 |
| Login | 置中卡片，品牌在上 |

## 沒做的

- 沒有圖表（目前沒有時間序列資料；S3 的 SQLite 歷史累積後可加 review 趨勢）。
- 報告 markdown 仍以等寬原文呈現。
