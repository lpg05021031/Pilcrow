const STOPS = [];
const S = (id, chap, t, say, keys, n1, n2, x) => STOPS.push(Object.assign({ id, chap, t, say, keys: keys || [], notes: [n1, n2 || ''] }, x || {}));
const C1 = '緒論', C2 = '圖形介面', C3 = '多 agent 協作', C4 = '開發方法';
S('cover', C1, '標題', '打造 Claude Code 工作台', [],
  '本報告說明如何為 Claude Code 建置工作台：由命令列介面出發，先建立圖形介面，再發展為多個 agent 並行協作的工作方式。該工作台名為 Pilcrow，目前版本為 3.1.0，支援 Linux 與 Windows。',
  '操作方式：→ 下一步，← 上一步，Ctrl K 跳至任一主題，N 顯示講稿；介面上的按鈕皆可直接點選。');
S('path', C1, '背景與動機', '命令列介面的兩項限制：長對話回溯與多 session 狀態管理', [],
  '背景：過去以 pi-web 作為日常的 AI 開發工具；Claude 模型開放使用後，改為直接採用原生的 harness，即 Claude Code。Claude Code 功能完整，惟命令列介面存在兩項限制：長對話不易回溯；多個 session 並行時，各自的狀態難以掌握。',
  '因此先以圖形介面處理瀏覽問題，再以工作台處理多個 agent 並行時的管理問題；最後說明開發所採用的方法。');
S('principle', C1, '設計原則', 'Pilcrow 定位為 Claude Code 的介面，而非獨立的 agent', [],
  '這是整個專案的設計原則。Pilcrow 不是另一個 agent，而是 Claude Code 的介面：相同的工具、權限、CLAUDE.md、skills 與 MCP，經由官方 SDK 驅動，不修改任何行為。',
  '在命令列可用的設定，在 Pilcrow 中同樣有效。');
S('install', C2, '安裝與啟動', '單一封裝檔解壓縮後即可執行，並沿用既有設定', [['tar', 'Linux'], ['zip', 'Windows']],
  'Qt 版以單一封裝檔發布：Linux 為 tar，Windows 為 zip，解壓縮後即可啟動，無須執行安裝腳本。啟動後選擇工作資料夾並輸入需求，即建立第一個 session。',
  '設定沿用 ~/.claude/settings.json，不需另行設定。pilcrow --doctor 可檢查 Claude Code、API endpoint、憑證與 git。');
S('sidebar', C2, 'Session 側欄', '集中呈現所有 session 的狀態', [['Ctrl Alt ↑↓', '切換 session']],
  '側欄是管理多個 session 的主要位置，依專案分組並即時顯示狀態。需要使用者回應的 session 集中於最上方的 Needs you 群組。',
  '口頭補充：上方可建立與搜尋 session；徽章顯示待處理數量；右鍵選單可重新命名、釘選、Fork、封存或刪除；Ctrl+Alt+上下鍵切換 session。');
S('new-session', C2, '建立 Session', '各 session 可配置獨立的 worktree', [['Ctrl N', '建立 session']],
  '獨立 worktree 是多個 session 並行的前提：每個 session 有自己的分支與工作目錄，不會互相覆寫檔案。',
  '口頭補充：對話框另可指定工作資料夾、模型與 Effort；Ctrl+N 以預設值建立，Ctrl+Shift+N 開啟選項後建立。');
S('stream', C2, '對話串流', '執行期間可持續輸入後續訊息', [['Esc', '停止 Claude']],
  '執行期間無須等待：使用者可先行輸入下一則訊息並排入佇列，訊息於目前步驟結束後送出。',
  '口頭補充：標題列與側欄同步顯示 Working；工具呼叫彙整為一行摘要；Ctrl+Shift+Enter 可中斷並立即送出。');
S('fold', C2, '步驟摺疊與內嵌 Diff', '預設僅呈現結論，細節可依需求展開', [['Ctrl O', '切換檢視層級']],
  '長對話不易閱讀的主因是工具輸出過多。Pilcrow 將每輪的工具呼叫收合，點選即可展開；檔案編輯直接顯示差異與增刪行數。',
  '檢視層級分為 Normal、Thinking、Verbose 三種，以 Ctrl+O 切換。');
S('permission', C2, '權限核准', '指令執行前須經核准，側欄同步提示', [['1', 'Allow'], ['2', 'Always'], ['3', 'Deny']],
  '權限機制即 Claude Code 原有的機制，Pilcrow 將其呈現為對話中的卡片，並在側欄提示有 session 等待回應。',
  '口頭補充：卡片完整顯示待執行的指令；選項為允許一次、永久允許或拒絕；永久允許前會說明規則涵蓋的範圍。', { act: true, hint: '點選 Allow 或按 1' });
S('handoff', C2, '交接報告', '每輪對話以固定格式的交接報告作結', [],
  '交接報告固定四欄：Asked、Did、Unverified、Decide。使用者無須重讀完整過程，即可掌握四項資訊：原始要求、已完成事項、尚未驗證事項，以及待使用者決定的事項。Unverified 一欄要求 agent 明確說明未驗證的部分。',
  '口頭補充：工作過程收合於上方，每輪結束後顯示耗時。');
S('modes', C2, '權限模式', '提供五種權限模式，可於對話中切換', [['Shift Tab', '切換權限模式']],
  '權限模式沿用 Claude Code 的定義，由逐項詢問到完全略過權限檢查，共五種。可於對話進行中隨時切換，不需重新建立 session。',
  '輸入區常駐顯示目前模式。');
S('overview', C2, '介面總覽', '三區配置：Session 管理、對話、結果檢視', [['Ctrl B', '收合側欄']],
  '整體配置：左側管理 session，中間是對話，右側檢視工作結果。後續依此配置分組說明：左側的 Session、中間的對話、右側的面板，再到鍵盤操作與設定。',
  '口頭補充：輸入區可切換權限模式與模型；Ctrl+B 收合側欄，Ctrl+反斜線收合側邊面板。');
S('changes', C2, 'Changes：變更檢視', '於對話視窗內完成變更審查與提交', [['Alt 1', '開啟 Changes']],
  'Changes 分頁顯示尚未 commit 的變更。審查與提交皆可在此完成，不需切換至其他工具。',
  '口頭補充：標題列顯示增刪行數；差異可切換為並排檢視。');
S('files', C2, 'Files：檔案瀏覽', '直接檢視檔案內容，無須另啟編輯器', [['Alt 2', '開啟 Files']],
  'Files 分頁用於快速確認檔案內容，例如檢視 Claude 剛修改的檔案全貌。',
  '口頭補充：上方顯示檔案路徑與常用操作。');
S('terminal', C2, 'Terminal：內建終端機', '於同一視窗內由使用者自行驗證', [['Alt 3', '開啟 Terminal']],
  '終端機開啟於 session 的工作資料夾，適合由使用者自行執行指令，例如測試與 git，確認 Claude 回報的結果。',
  '口頭補充：可開啟多個終端機分頁。');
S('preview', C2, 'Preview：網頁預覽', '前端修改後，於面板內直接預覽', [['Alt 4', '開啟 Preview']],
  '範例為修正登入頁的轉址問題後，直接於 Preview 分頁確認頁面行為。',
  '口頭補充：網址列輸入本機開發伺服器的網址，亦可改以外部瀏覽器開啟。');
S('tasks', C2, 'Tasks：待辦與背景工作', '彙整待辦進度與背景工作', [['Alt 5', '開啟 Tasks']],
  'Tasks 分頁呈現目前的工作進度：Claude 自行維護的待辦清單，以及仍在執行的背景指令與子 agent。',
  '口頭補充：背景工作可個別停止，完成的工作列於下方。');
S('timeline', C2, 'Timeline：變更時間軸', '各步驟的檔案變更皆可檢視與還原', [['Alt 6', '開啟 Timeline']],
  'Timeline 記錄 session 中每一次檔案變更。若某一步驟之後的方向有誤，可將檔案還原至該時間點。',
  '口頭補充：選取任一步驟，可檢視該步驟的差異。');
S('info', C2, 'Session 資訊', '用量、設定與專案記憶彙整於單一頁面', [['Alt 7', '開啟 Session']],
  '此頁用於確認 session 的實際執行狀態，例如 context 是否即將用盡。下方為此專案累積的筆記，新 session 將自動載入。',
  '口頭補充：中段列出模型、權限模式、Effort、版本與工作資料夾。');
S('find', C2, '對話內搜尋', '於長對話中定位關鍵字', [['Ctrl F', '搜尋對話']],
  '命令列介面不易回溯長對話，此為開發 Pilcrow 的動機之一。對話內搜尋可直接定位關鍵字出現的位置，並可前後跳轉。', '');
S('palette', C2, 'Command palette', '所有功能皆可經由鍵盤操作', [['Ctrl K', '開啟 command palette']],
  'Command palette 是鍵盤操作的入口：建立 session、切換面板、開啟工作台與設定頁，皆可由此進入。',
  '口頭補充：輸入 @ 可搜尋檔案；下方可依名稱跳轉至任一 session。本導覽中，Ctrl K 開啟的是導覽本身的目錄。');
S('shortcuts', C2, '鍵盤快捷鍵', '常用操作皆可由鍵盤完成', [['Ctrl /', '開啟總表']],
  '快捷鍵的設計原則為常用操作皆可由鍵盤完成，總表可隨時開啟查閱。',
  '口頭補充：Alt+1 至 Alt+7 對應七個側邊面板分頁；Ctrl+K 開啟 command palette；Shift+Tab 切換權限模式；Esc 停止 Claude。');
S('settings', C2, 'Settings 總覽', '設定集中管理，分為七類', [['Ctrl ,', '開啟 Settings']],
  'Settings 分為七類：General、Repositories、Claude Code、Skills、MCP servers、Memory、Diagnostics。General 包含主題、對話字級、傳送鍵、桌面通知、建議下一則訊息與 Timeline。',
  '後續三項分別說明 Skills、MCP servers 與環境診斷；Memory 於專案記憶一節說明。');
S('skills', C2, 'Skills', '與命令列共用同一組 skills', [],
  'Skills 直接讀取 Claude Code 的 skills 目錄：個人的 ~/.claude/skills、專案的 .claude/skills，以及隨 plugin 安裝的 skills。與命令列共用同一份內容，Pilcrow 不另存副本。',
  '口頭補充：可於介面中建立新的 skill。');
S('mcp', C2, 'MCP servers', '沿用 Claude Code 的 MCP 設定', [],
  'MCP 設定沿用 Claude Code 的設定檔：~/.claude.json 與專案的 .mcp.json，與 CLI 共用。清單區分全域與專案層級；連線狀態於 session 啟動 Claude Code 後顯示。',
  '口頭補充：可於介面中新增 MCP server。');
S('diagnostics', C2, '環境診斷', '發生異常時，先行檢查執行環境', [['pilcrow --doctor', '命令列版本']],
  '診斷頁列出環境檢查的結果：Claude Code、API endpoint、憑證、終端機支援與 git。命令列亦可執行 pilcrow --doctor。',
  '口頭補充：可複製診斷結果以便回報問題；下方顯示 log 與設定的路徑。');
S('board', C3, '工作台', '依工作歸屬分欄', [['Ctrl Shift O', '開啟工作台']],
  '工作台以「工作目前在誰手上」分欄：Backlog、With Claude、Waiting、Needs you、Done。使用者每天只需處理 Needs you 一欄。',
  '口頭補充：上方摘要列顯示待處理、進行中、等待中的數量與當日費用；With Claude 同時最多 3 項；底部列出鍵盤操作。');
S('board-new', C3, '建立卡片', '卡片記錄意圖與完成條件，而非執行步驟', [['N', '新增卡片']],
  '建立卡片時只需說明期望的結果與完成條件。可立即交給 Claude 執行，也可先保留於 Backlog，稍後再處理。',
  '口頭補充：可指定獨立 worktree 與 session 設定；Ctrl+Enter 直接交給 Claude。');
S('review', C3, '審核與交接', '對照原始意圖與交接報告後進行核准', [['E', '核准'], ['R', '退回'], ['W', '暫緩']],
  '審核時對照原始意圖與交接報告。交接報告共五欄：Asked、Did、Changed、Unverified、Decide。Did 由 agent 陳述，Changed 則由 diff 量測，兩者來源不同，可互相對照。',
  '口頭補充：Claude 完成後卡片移至 Needs you；審核動作為核准並提交、退回、暫緩，或開啟 session。', { act: true, hint: '點選 Approve and commit 或按 E' });
S('wait', C3, '等待條件', '條件成立時自動喚醒 Claude', [['W', '設定等待條件']],
  '等待條件用於處理當下尚無法進行的工作，例如須待 CI 通過，或須待另一項工作先行核准。條件成立前，卡片停留於 Waiting，使用者無須自行追蹤。',
  '口頭補充：預設條件為 CI 通過、明日 09:00 或一小時後；也可自訂時間，或等待另一張卡片核准。', { act: true, hint: '按 → 模擬 CI 通過' });
S('schedule', C3, '排程', '例行工作依排程執行', [],
  '範例為每日 09:00 執行的相依套件稽核。排程執行時無人值守，無人可回答提問，因此指示需寫明假設與限制，權限範圍也需事先限定。', '');
S('dispatch', C3, 'Dispatch', '單一修改同時派送至多個 repository', [['Ctrl Shift D', '開啟 Dispatch']],
  '範例工作為在三個 repository 的 CI 中固定 Node 22：兩個完成、一個失敗，並列出總費用與耗時。',
  '口頭補充：由側欄或 Ctrl+Shift+D 開啟，以 New job 建立派送工作。');
S('helpers', C3, 'Helpers', '由單一 session 擔任 lead，將工作分派予多個 helper', [],
  '啟動方式為於提示中要求建立 helper，例如 Start two helpers: one for the api and one for the web，由模型呼叫 helpers 工具建立。範例中，lead 將工作拆為 api 與 web 兩部分，各由一個 helper 在自己的資料夾與 worktree 中執行。拆分方式由模型逐輪決定；lead 負責等待、閱讀結果、調整方向與彙整。',
  '口頭補充與 Dispatch 的差異：Dispatch 是同一份指示派送至多個 repository；Helpers 是將一項工作拆成數個部分，各自進行。');
S('helpers-review', C3, 'Helpers 審查與收尾', '各 helper 的變更於同一面板審查並完成收尾', [],
  '按下 Helpers 列的 Review，Changes 面板即依 helper 分段列出各自的變更。可一次提交全部、開啟 pull request，或保留、捨棄。',
  '口頭補充：helper 需要權限時，可在工作台的權限卡片選擇 Allow for all helpers，一次核准。');
S('memory', C3, '專案記憶', '專案記憶自動載入每個新 session', [],
  '專案記憶會帶入該資料夾的每個新 session。畫面中的統計顯示：有筆記的 session 退回比例為 13%，沒有筆記的為 36%。畫面為示範資料。',
  '口頭補充：可手動新增筆記，或由 session 歸納所學；重要筆記可釘選；每則筆記標示來源、讀取次數與發揮作用的次數。方法參考 ACE（Agentic Context Engineering，Zhang et al.，ICLR 2026）：將 context 視為持續累積的 playbook，依實際結果而非模型自評決定去留。');
S('memory-conflict', C3, '記憶矛盾處理', '記憶內容出現矛盾時，由使用者裁決', [['Enter', '刪除'], ['E', '保留']],
  '使用者在某個 session 中的修正若與既有筆記矛盾，系統不自行判斷何者正確，也不自動覆寫，而是在 Needs you 產生一張卡片，交由使用者決定。',
  '口頭補充：面板對照原筆記、使用者的修正與改寫後的內容；可刪除筆記，或保留並記錄此次矛盾。');
S('how-outcome', C4, '以結果界定任務', '以預期結果與完成條件界定任務，而非指定實作方式', [],
  '交付任務時，說明預期結果與完成條件，而非實作方式。以「新增一個看板」為指示，產出僅為一個看板；改以結果描述後，agent 具備判斷完成與否的標準，並由此發展出五欄看板、審核關卡與交接報告。',
  'Qt 原生視窗採用相同做法：任務並非表述為「提升視窗流暢度」，而是「於無 GPU 的機器上同樣流暢，且外觀與網頁版一致」。本節觀點參考 Felix Rieseberg 的文章 How to be more ambitious（2026 年 10 月）。');
S('how-bar', C4, '設定高標準', '設定具挑戰性且可量測的標準', [],
  '標準刻意設定於初期看似難以達成的水準，且必須為可量測的數值。標準設定較高時，agent 傾向尋求根本的解法，而非止於表面可行的結果。',
  '第二個重點是誰來判定：先訂數字，再交給 agent 達成，是否完成由測試、diff 與截圖比對決定。交接報告中「做了什麼」由 agent 陳述，「改了哪些檔案」由 diff 量測，兩者互相對照。');
S('how-borrow', C4, '援引既有解法', '流程問題優先參考其他領域的既有解法', [],
  '多數流程問題於其他領域已有經過驗證的解法，可交由 agent 查找並套用。工作台同時執行的工作上限源自豐田生產方式；Waiting 欄位源自 GTD；固定格式的交接報告源自醫院的臨床交班。',
  '另外兩個例子：收件匣的三個分類參考郵件用戶端；全介面只有一個強調色，且只用在 Needs you，來自 Dieter Rams 的精簡原則。這三件事與使用何種工具無關，適用於任何交付給 agent 的工作。');
S('end', C4, 'Q &amp; A', 'Q &amp; A', [],
  'Q &amp; A。安裝方式：Linux 解壓縮 tar，Windows 解壓縮 zip，即可啟動。', '');
S('repos', C3, 'Repositories 註冊表', '登錄可供分派的 repository：名稱、別名與權限政策', [['Ctrl ,', '開啟 Settings']],
  'Repositories 為使用者維護的 repository 註冊表，記錄每個 repository 的名稱與別名、權限政策、預設值，以及提供給 agent 的常備說明。Dispatch 與 Helpers 皆讀取此清單，因此提示中以慣用名稱（例如 api、order FE）即可指向對應的資料夾。',
  '口頭補充：政策分為 Allowed、Ask、Never，決定分派至該 repository 前是否須先詢問；可掃描資料夾批次登錄，或手動新增。');
S('workflow', C3, 'Workflow', '由提示啟動多 agent 流程，並於 Tasks 面板呈現各階段進度', [],
  'Claude 以 Workflow 工具啟動多 agent 流程時，對話中會出現流程摘要，顯示階段數與 agent 的完成進度；完整的流程卡位於 Tasks 面板，依 phase 分段列出各 agent 的狀態、模型與結果預覽。範例提示為要求以 workflow 審查登入修正：先由三個 agent 分別審查，再逐項驗證。',
  '口頭補充：啟動方式為於提示中要求使用 workflow，例如 Use a workflow to review the login fix。流程卡上方為整體進度，下方可展開 log，並可停止執行中的 workflow。此功能由 Claude Code 的設定 enableWorkflows 決定。畫面為示範資料。');
/* parts and groups: every group of stops opens with a page that says where we are and what comes next */
const PARTS = [
  { n: '第一部分', t: '圖形介面', sub: '將 Claude Code 的操作由命令列整合至單一視窗' },
  { n: '第二部分', t: '多 agent 協作', sub: '多個 agent 並行作業時的交付、等待與審核機制' },
  { n: '第三部分', t: '開發方法', sub: '本工作台開發過程所採用的方法' },
];
const GROUPS = [
  { id: 'g-start', part: 0, t: '啟動與 Session', map: 'side', where: '左側：Session 側欄', sub: '自安裝至建立第一個 session 的流程', stops: ['install', 'sidebar', 'new-session'] },
  { id: 'g-chat', part: 0, t: '對話', map: 'main', where: '中央：對話區', sub: '單輪對話自送出需求至交接報告的流程', stops: ['stream', 'fold', 'permission', 'handoff', 'modes'] },
  { id: 'g-panel', part: 0, t: '側邊面板', map: 'panel', where: '右側：側邊面板（七個分頁）', sub: '於對話視窗內檢視工作結果', stops: ['changes', 'files', 'terminal', 'preview', 'tasks', 'timeline', 'info'] },
  { id: 'g-keys', part: 0, t: '搜尋與鍵盤操作', map: 'all', where: '整個視窗', sub: '長對話內容的定位，以及全功能的鍵盤操作', stops: ['find', 'palette', 'shortcuts'] },
  { id: 'g-set', part: 0, t: '設定', map: 'dialog', where: 'Settings 對話框', sub: '與命令列共用同一組設定檔', stops: ['settings', 'skills', 'mcp', 'diagnostics'] },
  { id: 'g-board', part: 1, t: '工作台', map: 'page', where: 'Workboard 頁面（由側欄進入）', sub: '依工作歸屬管理多個 session', stops: ['board', 'board-new', 'review', 'wait'] },
  { id: 'g-fan', part: 1, t: '排程與分派', map: 'page', where: '工作台、Settings、Dispatch 頁面與對話', sub: '例行工作的排程執行，以及單一工作的多 agent 分派與流程', stops: ['schedule', 'repos', 'dispatch', 'helpers', 'helpers-review', 'workflow'] },
  { id: 'g-mem', part: 1, t: '專案記憶', map: 'dialog', where: 'Settings 的 Memory 頁面與工作台', sub: '跨 session 累積的專案筆記及其矛盾處理機制', stops: ['memory', 'memory-conflict'] },
  { id: 'g-how', part: 2, t: '三項方法', map: '', where: '', sub: '不依賴特定工具，適用於各類交付 agent 執行的工作', stops: ['how-outcome', 'how-bar', 'how-borrow'] },
];
const byId = {};
STOPS.forEach((s) => { byId[s.id] = s; });
S('agenda', C1, '報告大綱', '本報告分為三個部分', [],
  '本報告分為三個部分：第一部分說明圖形介面，第二部分說明多個 agent 並行作業時的工作台，第三部分說明開發所採用的方法。',
  '各組功能之前設有導引頁，說明其於介面中的位置與涵蓋項目。');
GROUPS.forEach((g) => {
  const part = PARTS[g.part];
  S(g.id, part.t, g.t, g.sub, [], '本組共 ' + g.stops.length + ' 項：' + g.stops.map((id) => byId[id].t).join('、') + '。', g.where ? '位置：' + g.where + '。' : '', { group: g, lead: true });
  g.stops.forEach((id) => { byId[id].group = g; byId[id].chap = part.t; });
});
STOPS.forEach((s) => { byId[s.id] = s; });
byId.overview.chap = PARTS[0].t;
const ORDER = ['cover', 'path', 'principle', 'agenda', 'overview'];
GROUPS.forEach((g) => { ORDER.push(g.id); g.stops.forEach((id) => ORDER.push(id)); });
ORDER.push('end');
if (ORDER.length !== STOPS.length) throw new Error('stop order ' + ORDER.length + ' / ' + STOPS.length);
STOPS.length = 0;
ORDER.forEach((id) => STOPS.push(byId[id]));
const IDX = {};
STOPS.forEach((s, i) => { IDX[s.id] = i; });
const at = (id) => STOPS[cur].id === id;
