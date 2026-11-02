# 版本紀錄

最新版在最上面。`npm run release -- X.Y.Z` 會檢查這裡有沒有該版的說明。


- **3.0.6**：Qt 介面三項修正與 control room 重新設計。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **左側刪除 session 不再「刪了又回來」**：刪除時如果 session 的 Claude Code 還在跑，伺服器原本不等它結束就刪掉對話檔，
  行程結束前又把檔案寫回去，session 就重新出現；刪除前剛好發出的清單更新晚到時，也會把它放回側欄。現在伺服器先等行程結束
  （最多 5 秒）再刪，之後再檢查一次有沒有被寫回；刪過的 session 不再列出、也不能重新開啟。側欄按下刪除就立刻移除，晚到的
  清單不會把它帶回來；真的刪除失敗才放回並顯示錯誤。web 版同樣。
  **執行中的指令有流動效果**：指令執行時對話裡看到的通常是收合的群組標題（「Running npm test…」），3.0.4 的流動效果只加在
  展開後的單一指令列上，所以平常看不到。現在群組標題也有；每一格只重畫那一行字，不影響效能。系統設定了「減少動態效果」
  （Windows 的「顯示動畫」關閉、macOS 的「減少動態效果」、GNOME 的 enable-animations）時一律不動。
  **Control room 重新設計**：上方四個大磚塊改成一條細的數字列（Working／Needs you／Finished today／Spent today），任何寬度
  都是一行四格、點了跳到對應區塊；Working 從兩欄卡片改成單欄清單，每一列是標題與耗時・花費、正在做的事、專案與分支，
  縮放視窗時版面不再重排、文字不再被兩欄擠成「…」；Finished today 與 Spend 一律上下排列，沒有內容時只是一行淡字，花費
  長條圖矮一點。重新整理時保留既有的列，不再每次串流更新都把 Finished 的列全部拆掉重建。
  **Needs you 改成安靜的清單**：拿掉卡片左邊的橘色邊條、整張卡的橘色底、標題旁的發光圓點與按鈕上的 1/2/3 鍵帽；
  每個請求是清單裡的一列（標題與專案、等了多久、要做的事、回答按鈕），跟 Working 同一種排法，強調色只留在預設的
  「Allow」按鈕。1/2/3 快捷鍵照舊可用，寫在按鈕的提示裡；工具名稱顯示成「Question」「Plan」而不是內部名稱。

- **3.0.5**：Qt 介面三項修正。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **開 Settings 或切換畫面不再閃過一個小視窗**：幾個元件（設定裡的說明文字、可收合區塊、總覽的圖示、Dispatch 完成列、輸入框
  的模式按鈕、右側面板的空白狀態）在放進版面之前就被設成可見，那一瞬間沒有父元件，Qt 會把它當成獨立視窗顯示一下。現在都先
  放進版面再決定顯示與否；測試會走過所有設定分頁、各頁面、新對話、指令面板與右側面板分頁，確認除了主視窗與 tooltip 之外
  沒有任何視窗出現。
  **送出的訊息與警告文字垂直置中**：Qt 的行高把字放在行框偏下 1–3px（中文的備用字型更低），灰色泡泡裡的字看起來沉在底部。
  現在依實際畫出的字（英文以大寫字高、中文以字形外框）算出位移，讓字落在泡泡正中；警告、錯誤與資訊提示的文字與圖示也一樣
  置中。只在排版時算一次，不影響捲動效能。
  **Settings 每個分頁同樣大小**：所有分頁都是 860px 寬、同樣高度，切換分頁時視窗不再忽大忽小。
  另外：選單會跟著它的按鈕移動（輸入框失去焦點時下沉 1px，選單原本會差 1px）。

- **3.0.4**：Qt 介面的五項改進。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **右上角「…」再點一下就收起**：選單判斷「點在外面」改用實際收到點擊的元件（Wayland 上原本的判斷不可靠，選單會先關掉又
  被重新打開），而且再點一次選單自己的按鈕就是收起；所有選單按鈕都一樣。
  **Dispatch 的 repo 清單捲動不再抽動**：清單能捲時，滾輪與觸控板的捲動（含慣性）都留在清單裡，到頭尾也不外傳；原本會傳給
  外層頁面，頁面一動又把清單移到滑鼠下，兩邊輪流捲。清單短到不用捲時，頁面照常能捲。
  **Thinking 與執行中指令的流動效果**：串流中的「Thinking」和執行中指令的文字上有一道光掃過（web 的 `.shimmer`）；跟著原本
  20 Hz 的時鐘、只重畫那一行文字，減少動態效果時不動。
  **Helper 在其他 repo 工作時看得出來在哪**：helper 列的標籤是「名稱 · repo · 狀態」，hover 有完整路徑與分支；「Started
  helper」卡片顯示 repo、路徑、分支與 Open，不再停在派出當下的「running」；步驟寫成「Started helper web → order FE」。
  **Helper 收到 lead 的指示**：第一次指派顯示「Brief from the lead」，右邊是 repo 與分支；之後的指示顯示「Message from the
  lead」；lead 的內容在淡綠色底上，固定說明收在「Standing instructions」下；lead 端「Told helper」也用同樣底色。

- **3.0.3**：Qt 介面五個修正與一個退出時的穩定性修正。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **右側面板 Finished 不再整段顯示指令或程式碼**：背景指令的標題就是它的描述，常常是整段 heredoc 或多行指令，原本一行一行
  佔滿面板。現在一律收成一行、超出以省略號截斷，完整內容在 tooltip；顯示的文字先截到 400 字，再長的腳本也不會拖慢面板。
  面板裡其他截斷的標題（agent、workflow、檔案路徑）同樣如此。
  **「Latest」只要最下面不在畫面內就出現，點了立刻到底**：原本要等到有新內容進來、且離底部超過半個畫面才出現，在已完成的
  對話往上捲一點就沒有回到底部的按鈕。現在只要正在串流的回覆或最後的結果不在畫面內就顯示；點下去在同一個畫面就到最底下
  （不再慢慢捲），之後持續跟著新內容。web 版同樣。
  **切換主題時 Tasks 面板不再出錯**：任務列的重繪方法與 Qt 的 `update()` 同名，切換深淺色時每一列都丟出例外而沒有重繪。
  **不再跳出小視窗**：3.0.2 的圓角 tooltip 是沒有父視窗的獨立視窗，在 Wayland（與部分 X11 視窗管理器）上會變成一個真正的視窗，
  hover Settings 等按鈕時就冒出一個小視窗、出現在視窗切換列。現在 tooltip 掛在所屬視窗底下（transient），不再另開視窗。
  **退出時不再可能 crash**：全域的事件攔截（tooltip、輸入框的點外面取消 focus）在程式結束與直譯器關閉前就先移除；原本仍掛著時，
  Qt 最後的事件可能在 Python 關閉途中呼叫到它而使行程崩潰。
  **Repositories 的新增與儲存**：選好資料夾後選單立刻收起，新的 repo 不再自動展開設定，只捲到看得見的位置並標示「Added」，
  細節之後再填。別名在離開輸入框時（切頁、點別處、關掉設定）也會儲存，不再只有按 Enter 才存；每次儲存在該列標示「Saved」。
  **打開的 helper 留在 lead 底下**：側欄原本把正在看的 helper 從 lead 底下拉回它資料夾的分組，兩者看起來毫無關係（web 也一樣）。
  現在它留在原處，lead 底下有一條連到各 helper 的細線，選中的 helper 底色從線的右邊開始；helper 標題列的連結改為
  「↖ Lead · 名稱」，點了回到 lead。

- **3.0.2**：Qt 介面的四個修正。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **Repository 面板對齊**：Settings → Repositories 的「add a name you use for it…」欄位緊跟在名稱 chip 後面（原本被推到最右）；
  名稱 chip 有了自己的底色與細框（原本與列同色，看起來像純文字）；展開後 Worktree 開關與標題回到各自的欄位（原本整列被撐開）。
  切換資料夾的選單裡，每列依序是圖示、名稱、路徑（原本名稱浮在列中間），路徑過長以省略號截斷。
  **Tooltip 改為自繪圓角**：Linux 上 Qt 的 tooltip 是方形視窗，樣式表只能把框線畫圓，hover 時看到的是一個長方形框。現在是與
  web 相同的深色圓角 tooltip，有陰影，快捷鍵顯示為鍵帽，出現在按鈕下方；對話區的 tooltip 也一樣。
  **輸入框的 focus 光暈點外面就消失**：原本只有點到會取得 focus 的元件才會消失；現在點輸入框以外的任何地方（對話區、空白處、
  標籤）都會消失，點輸入框本身、它的選單與 `@` / `/` 建議清單則保留。
  **展開的步驟對齊，空的思考不再出現**：展開 Ran commands 等群組時，Thought 列的圖示、文字與高度改成與工具列同一格線，展開的
  思考內容也對齊文字欄；「顯示更多步驟」列同樣對齊。紀錄裡只有簽章、沒有文字的思考區塊不再顯示（web 一直如此），不會再出現點開是
  空的標題；被隱藏的思考（Reasoned privately）保留標題但不能展開。

- **3.0.1**：Qt 視窗與對話間距的四個修正（Linux 的自訂標題列）。設定與安裝方式不變，直接用新的安裝檔覆蓋即可。
  **resize 指標在四邊都正確出現**：邊緣底下壓著側欄的列或對話區時，原本收不到滑鼠移動，指標常常變不過去或留著錯的形狀。
  現在直接監聽整個視窗，指到邊緣就顯示 resize 指標、離開立刻還原，角落的感應範圍加大，按下就交給系統 resize；最大化或
  全螢幕時不啟用。
  **上方兩角改成圓角**（10px），下方維持直角；最大化或全螢幕時恢復直角。
  **標題列不再顯示 session 名稱**：名字已經在對話區上方，標題列只留視窗按鈕。
  **送出的訊息與上一則回覆拉開到 26px**（原本 20px），與 web 版的 `.msg-user` 一致；第一則訊息改在欄位頂端下方 6px，
  不再被看不見的載入佔位區塊多推 26px。

- **3.0.0**：2.13.0 之後的獨立審查（server、web、Qt 各一份）找到的三個問題修掉，並以 3.0.0 收束 2.x 這一輪：
  Dispatch、Helpers 與 Repositories 註冊表、canvas 對話引擎、五輪品質審查都在這一版裡定下來。設定、對話與安裝方式沒有
  不相容的變更，舊版直接用新的安裝檔覆蓋即可。
  **Qt 執行中的標記不再凍結**：畫面只重畫一部分時（滑鼠移到另一張已完成的卡片、捲動的邊條），canvas 會把不在重畫範圍內的
  執行中區塊從時鐘上拿掉，於是正在跑的卡片的轉圈標記與秒數停住，直到那一列碰巧再被重畫——長指令時可能整段都停著。現在
  畫面上仍在跑的區塊會留在時鐘上；測試讓滑鼠停在另一張卡片上，確認秒數照常往前。
  **Web 的 Find 找得到步驟、錯誤與指令輸出，數到的就是畫出來的**：2.13.0 改成從資料模型計數（因此能搜整段長對話而不必畫出來），
  但只讀你和 Claude 的文字，而且讀的是 Markdown 原始碼：工具的指令與輸出、錯誤通知、slash 指令的輸出都找不到，連結網址或
  粗體的 `**` 會被算成畫不出來的命中。現在每一列照它顯示的樣子被讀（Claude 的文字經過 Markdown 渲染、步驟讀標題、指令與
  輸出），已畫出的列以畫面為準修正計數，所以「第幾個／共幾個」、上下跳與螢光標記一致；跳到折起來的步驟輸出時會打開那張
  卡片；把已畫出的命中捲進畫面時也會停止跟隨最新輸出，串流不再把畫面拉走。
  **Qt 圖示按鈕**：2.13.0 把一批按鈕的顏色傳成 token 名稱，側欄底部的封存與外觀、收合側欄、Linux 標題列的視窗按鈕在滑鼠
  移上去前不畫圖示；修正時又讓沒指定顏色的約四十個按鈕畫成黑色。兩種情況都修好並各有測試。

- **2.13.0**：**五輪「審查→修正→再審查」的品質版**。不加功能：三個 client 的效能、UX 與無障礙、程式品質、測試閘門全面
  整理。每一輪由獨立審查以 file:line 與量測為證據，修正以「最乾淨的做法」為準——根因、最小完整改動、先刪再加、不為小事
  加機制；審查發現的 medium 以上問題全部修掉，low 列在審查報告裡。
  Web 效能：對話分頁（八個 turn 一頁、就近先畫、滑動中不畫、回到原處只畫附近幾頁）；串流時每幀的工作只跟著正在寫的那一塊
  走（清單、表格、段落一塊一塊長，程式碼圍欄接在最後一段）；程式碼上色一條切片隊列、離開頁面就停、grammar 改用預編譯
  （`@shikijs/langs-precompiled`，一個視窗第一個區塊的最長任務 130–150 ms → 60 ms）；模型依大小淘汰、每個快取有位元組
  上限；主題切換是一個 view transition 而不是每個元素的 transition；state 事件保留相同內容的 request 陣列；頁面、編輯器、
  終端機、上色器與每種 grammar 都在第一次用到才載入；`session.open` 分片建模。
  Web UX 與無障礙：對話框 inert、焦點圈、回到開啟處；選單、`/` 與 `@` 列表、命令面板的 roving focus 與 combobox 語法；
  側欄分頁是真的 tab set；每個圖示按鈕與欄位有名字，工具提示不吃掉名字；lit 與 hover 列的次要文字一律 `--text`（含
  時間軸選中的步、控制室、檔案歷史、資料夾挑選器、工具卡頭等十四列）；程式碼註解色兩個主題都達 4.5:1；減少動態跟系統
  設定；淺色主題無遺漏的暗面；每個區塊有 ErrorBoundary 並能重試；CodeMirror 的文字框以檔名命名。
  Web 程式品質：設計 token 一套（間距、字級、圓角、層級；顏色只在 `tokens.css`）、每個屬性只在一個檔宣告、動態只在
  `motion.css`（`scripts/css-overlap.mjs` 隨 lint 守著）；TypeScript 無隱含 any、`exhaustive-deps` 是錯誤、`rpc.on`
  一張事件表讓每個訂閱者的 payload 有型別；重複的拒絕決策、複製 toast、Show sidebar、資料夾瀏覽、收件匣列各合而為一。
  Web 測試：`npm run test:perf` 效能閘門（計數每次強制，時間數字在 `PILCROW_PERF=1` 時強制，與 Qt 同一套切分）、
  `npm run test:ux`（鍵盤流程、無障礙樹的名字、淺色主題、減少動態）、頁面錯誤與畫出的 fault 讓測試失敗、對比測試涵蓋
  半透明填色與每一列 lit 的疊層。
  Server：RPC 參數檢查與錯誤碼；Pilcrow 自己的 JSON 檔非同步原子寫入、離開時立刻寫完；資料夾掃描每 4 ms 讓出事件迴圈；
  每幀一批訊息，state 只送有變的部分；記憶體有上限、git 行程數固定並以測試釘住；時間軸在刪除 session 時回收它的 ref 與
  物件；WebSocket 心跳與落後視窗的處理；離開時的順序、不再起任何行程；`/api/fs` 頁面以 sandbox CSP 與 nosniff 送出；
  `tsc` strictNullChecks 與 ESLint 涵蓋 server 與 Electron shell；每個模組都有測試；CI 跑所有閘門。
  Qt：canvas 捲動用 blit、tick 只重畫標記、hover 與選取只重畫動到的列、自然寬度量一次、改字的區塊 `redraw()` 讓 pixmap
  快取失效、附件縮圖解碼一次、單一時鐘且只在顯示時跑、減少動態讀系統設定；無障礙：列以其文字命名、fold 與卡片是 Button、
  prose 與 Markdown 內的連結是 Tab 可到的 Link、島狀 widget（權限卡、提問、計畫）在對話的無障礙樹裡；效能預算預設跑計數
  與 CPU 時間中位數，`PILCROW_PERF=1` 加 wall-clock；DESIGN、PERF、REVIEW 與程式碼一致。
  依賴：Electron 44.7.2 → 44.10.1、Vite 6.4.5、Vitest 4.1，與 `npm audit` 指出的過渡依賴（body-parser、fast-uri）
  更新到修正版，執行期依賴 0 個已知弱點；開發工具這邊剩 eslint 鏈底下的 minimatch（moderate，上游連最新的 eslint 都
  還沒換到修正版，不隨安裝檔出貨）；`pip-audit` 對 `qt/requirements.txt` 無已知弱點。

- **2.12.0**：**Helpers：一個 session 帶領其他 session**（設計與現況見 `HELPERS.md`）與 **Repositories 註冊表**（見
  `REPOSITORIES.md`）。Dispatch 是「一份 brief 派給一組 repo」；Helpers 是「一件事拆成幾份，各在自己的 repo 做」，怎麼拆
  由模型在對話裡逐回合決定。
  Helpers：伺服器新增 `server/helpers.js`，每個 session 都拿到一組 in-process MCP 工具（`mcp__helpers__start / wait /
  read / send / stop / list`）。lead 用 `start` 開一個 helper——一個真 session，在自己的資料夾與 worktree 上跑，帶自己的
  brief；資料夾可以用註冊表的名字或別名（`repo`）也可以用路徑（`cwd`）；`after` 讓它等另一個 helper 完成並接上對方的
  handoff。helper 結束時 lead 收到一則 note（兩秒內的多則合併、lead 忙著就先等、被 `wait` 蓋到的不另發、lead 已經關了會被
  喚醒），handoff 從 helper 的最後一則訊息抽出；`wait` 等到 done（needs_action 不算）、`any` 有變就回、超時就回報進度；
  總數、同時數、每個的分鐘數與花費的上限在 `config.helpers`。權限：helpers 自己的工具對 lead 一律放行；`start` 在 lead
  自己的專案與註冊為 allowed 的資料夾放行、其他地方要問、`never` 拒絕，Always allow 會把那個資料夾登記為 allowed；
  同一個權限請求答一次就答所有 helper（規則記在 lead 上）。review 與收尾沿用 Dispatch 的 hunk 分組與 commit / 開 PR
  （`gh`）/ keep / discard；記錄進 inbox 並可匯出 Markdown；伺服器重啟後還在跑的 helper 標記 stopped、lead 收到一則 note；
  刪除 lead 可以一併刪掉它的 helpers 與 worktree。RPC `helpers.*`，測試 `test/unit/helpers.test.ts`。
  Qt 視窗：lead 的標題下多一條 helpers bar（每個 helper 一顆 chip：狀態色點、名字、一句狀態；合計花費；有變更就有
  Review）、六種工具卡、note 畫成便條（可 Open 那個 helper）、helper 的對話頂端有「helper of …」、brief 標成 lead 送的、
  側欄把 helpers 收在 lead 底下（lead 的點取最熱的狀態）、狀態膠囊「Leading」、控制室卡片註明是誰的 helper 並有
  「Allow for all helpers」、Changes 分頁每個 helper 一節（含 diff）加上 Commit all / Open pull requests / Keep / Discard、
  收件匣的記錄卡、選單的 Detach from lead、刪除對話框的連帶刪除；測試 `qt/tests/test_helpers.py`。web client 有同一套
  （bar、卡片、便條、側欄巢狀、控制室、Changes 節、收件匣卡），瀏覽器測試在 `test/e2e/run.mjs`。
  Repositories 註冊表：`server/repos.js`，存在設定檔的 `repos`。每個 repo 有路徑、名字、別名（「order FE」「後端」都行）、
  一句說明、policy（allowed / ask / never）、預設值（mode、worktree、base、prepare、verify、model）、給 agent 的常備 notes、
  依賴，與掃描得到的 facts；名字、別名、資料夾名用同一套正規化比對（NFKC、不分大小寫、空白與 `-`、`_` 不計），找不到時
  給最接近的；摘要附在每個 session 的 system prompt 後面，模型用你的話就找得到 repo。Dispatch 的掃描標出已註冊的、挑選器
  放最前面、notes 自動帶進 brief、plan 擋掉 never、prepare / verify 沒填就用註冊表的預設。Qt 與 web 都有 Settings →
  Repositories（列、就地編輯、掃描資料夾註冊、移除）、composer 的 `@` 列出 repo、session 選單的「Register this
  repository…」、側欄與新 session 對話框用註冊表的名字；RPC `repos.*`，測試 `test/unit/repos.test.ts`、`qt/tests/test_repos.py`。
  效能：helpers bar 沒有 QLayout——chip 由 bar 自己排、bar 自己畫底色，helper 狀態一秒更新十次時只重畫 chip，對話區、側欄、
  composer、標題都不重畫也不重排，一次更新不到 3 ms（`qt/tests/stress/s_helpers.py` 量測，`qt/tests/test_helpers.py` 守著）；
  store 只在 session 清單顯示的欄位變了才重發清單（之前每個 state 事件都重發、側欄都重建一次）；Changes 分頁的 helper 節
  跟著分頁的可見性走，被別的分頁蓋住時不做事；瀏覽器端的對話區不再滑到 helpers bar 底下。
  視覺：helper 在對話裡改用跟工具卡同一套語法——便條是一張卡（狀態色點、「Helper finished」、名字、時間／花費／diffstat、
  滑過出現 Open，handoff 放在工具結果用的那種內嵌底上、由標題列折疊），不再是引言式的灰塊；helper 自己的對話裡 lead 送來
  的 brief 是同一張卡（「From the lead」＋ lead 標題＋Open 回 lead），每個 helper 都拿到的常備指示收在卡片底下；工具卡裡
  helper 的列改成 bar 的順序（色點、名字、狀態字、最後一行、Open）；折疊列的摘要說「Started 2 helpers」「Asked you a
  question」「Proposed a plan」，不再是「connector tools」或工具名。要你回答的三種卡——權限、提問、計畫——統一：同一圈
  accent 外框、標題列同一顆 accent 點、同一組按鈕尺寸、同一種數字鍵小框（提問的選項也有，單一問題時按數字就選、再按
  Enter 送出），答完後都收成同一種安靜的記錄。順手修了兩個舊 bug：Qt 的 composer 有焦點（預設狀態）時數字鍵答不了任何
  卡；helper 對話裡 lead 的 brief 畫成一個空的大灰塊。mock 多了 ExitPlanMode 的腳本，提問的回答也照 Claude Code 的格式回傳，
  兩邊的測試都能把三種卡走一遍（`qt/tests/test_islands.py`、`test/e2e/run.mjs`）。
  **Context 統計**：新的 `server/usage.js` 算 session 的 context——一次回覆讀進去的 input、cache 寫入、cache 讀取三項，
  跟 Claude Code 自己的 `/context` 與狀態列一樣，不再把回覆的 output 也加進去（那是下一次呼叫的 input）；只算 session
  自己的回覆，sub-agent 的訊息（帶 `parent_tool_use_id`）不算；compact 之後舊數字作廢——伺服器收到 `compact_boundary`
  就標成 `afterCompact`，環與 Info 面板寫「Compacted — 下一次回覆才量得到」而不是顯示 compact 前的數字，重新載入歷史時
  若最後一件事是 compact 也一樣；下一次回覆回來就是新的數字。單元測試 `test/unit/usage.test.ts`、Qt 測試
  `qt/tests/test_context.py`（mock 多了 compact 腳本）。
  **Workflow**：伺服器把 `task_progress` 的 state 廣播合併成每 120 ms 最多一次（六十個 agent 回報不再等於每秒六十份完整
  state 丟給每個 client）；Qt 的 Tasks 面板不再每次進度都重建整張 workflow 卡——數字、進度條、每列文字就地更新，新 agent
  就地插入，狀態變了的那一列才重建，phase 的狀態也就地改；對話裡的 Workflow 卡片內文跟著 run 走（之前只在工具結果到時
  才更新）；web 的 Tasks 面板只訂閱 tasks 與對話版本，串流中的回覆不再重畫它。量測見 `qt/PERF.md` §5
  （`qt/tests/stress/s_workflow.py`）；mock 多了 Workflow 腳本（兩個 phase、八個 agent），Qt 與瀏覽器測試都跑一遍。
  **Windows**：Qt 視窗開啟時依主螢幕可用範圍縮放（1366×768 的筆電、125 % 的 1080p 不再超出螢幕、右側被裁到 resize 才對），
  並記住上次的位置與大小（落在螢幕上才還原）；Windows 的原生標題列透過 DWM 跟主題同色（深色模式、標題、邊框、文字）、
  Windows 11 明確要圓角；畫面時鐘與 16 ms flush 計時器改 PreciseTimer（Windows 預設 15.6 ms 的解析度會把 16 ms 變 31）；
  WebView2 外殼也明確要圓角。Windows 安裝檔多了 `-Qt`：PySide6 裝進安裝目錄下的 venv，捷徑與 `pilcrow-qt` 指令
  直接開 Qt 視窗（由 venv 的 pythonw 啟動，不經 PowerShell、不開主控台），伺服器由視窗自己找到或拉起來，輸出寫到
  `%LOCALAPPDATA%\Pilcrow\state\qt-server.log`（之前接 pipe 沒人讀，Linux 上寫滿會把伺服器卡住）。
   **安裝檔資安檢視**：兩個安裝檔逐行檢視過一遍（README「安全性」有整理）。Windows 安裝檔的使用者 PATH 改成照登錄檔原樣
   編輯（REG_EXPAND_SZ、`%VAR%` 不展開，之前 `SetEnvironmentVariable` 會把整條 PATH 展開後寫回成純字串）並廣播
   WM_SETTINGCHANGE；內附的外殼腳本也驗 SHA-256；zip 內的路徑拒絕 `..` 與絕對路徑；關掉舊伺服器前確認該 pid 是 node
   （Linux 同）；桌面捷徑被「受控資料夾存取」擋下時改為警告而非中止；說明裡不再建議 `[scriptblock]::Create` 這種繞過
   執行原則的寫法，改為 `Unblock-File`；WebView2 DLL 的簽章檢查比對完整的 `O=Microsoft Corporation` 欄位；
   `make-installers.py` 會驗證 Windows 安裝檔整份是純 ASCII。
   **Dispatch 檢視**：兩個 client 的 Dispatch 畫面逐一狀態截圖對照（看板、工作頁的等待回答／canary 完成／執行中／
   review／完成、新工作、收件匣卡片、某個 run 自己的對話；深淺兩色），並以四十個 repo、每秒十次回報的工作量測
   （`qt/tests/stress/s_dispatch.py`，數字在 `qt/PERF.md` §5）。修掉的：工作頁表格列在 canvas 上就地更新後，畫面仍是
   舊狀態（重建的 block 沿用快取的 pixmap、也沒要求重繪——現在重建會換代並記下自己的矩形，所有引擎上的清單一體適用）；
   引擎上的清單（run 表、timeline、target picker）不跟著主題切換；看板與工作頁在別的頁面前面時仍逐筆 refresh（現在
   隱藏時標記、顯示時才算）；看板每個 delta 重排所有卡片（現在只有成員或順序變了才重排）；側欄每個 delta 整個重建
   （現在只有工作集合或名稱變了才重建，否則只更新徽章）；新工作表單的三個開關沒有文字；run 自己的對話沒說它屬於哪個
   job（現在有「run of …」麵包屑可以回到工作頁，跟 helper 的「helper of …」一樣）；完成列改成跟控制室一樣有 hover；
   web 端 state 欄跑出表格列、完成列名稱被擠到看不見、收件匣卡片的按鈕溢出，都修了。順手：`Text` 的換行結果依寬度
   快取、一行字寬度沒變就不觸發重排；側欄頁籤的徽章數字沒變就不重繪。
   **Dispatch 重新設計**（兩個 client 同步）：工作頁的狀態改用 session 的 status pill（Running／Needs you／Canary
   done／In review／Paused），標題後面只剩規模、花費、時間；canary 完成與等待回答改成控制室「需要你」的卡片語法（色底、
   左側 rail、標題與副標、well 裡放摘要或指令、按鈕一列），不再是帶色橫幅；表格的動作欄固定寬度、標題列也有，列與列之間
   有細線，欄位對齊；看板上需要你的工作是同一種卡片，請求與 canary 是卡片裡的 well。新工作表單縮成名稱、brief、目標
   三件事：plan 的結論放在 Targets 標籤旁、注意事項只在有話說時出現在清單下方，其餘設定收進「Options」（跟新 session
   對話框同款的展開列，收起時一行讀出目前設定），Plan 按鈕拿掉（打字時就會算）。

- **2.11.0**：**Dispatch：一份 brief、一組 repo、一個 job**（設計與現況見 `DISPATCH.md`）。伺服器新增 `server/dispatch_core.js`
  與 `server/dispatch.js`：掃描資料夾找 repo（分支、未提交、遠端、大小、CLAUDE.md，以及 package.json / go.mod /
  Cargo.toml 之間的本地依賴）；開跑前先 plan（不是 repo、分支不存在、brief 空了或太長 → blocked；主 checkout 有未提交、
  另一個 job 還開著 → warning；依賴排成層級、最壞情況的分鐘數與花費）；每個 target 是一個真 session，在自己的
  worktree 與分支 `pilcrow/<job>` 上跑；先跑 canary，讀過再放其餘（其餘的 brief 可帶上 canary 的摘要，被依賴的 repo
  先跑、依賴者拿到它的摘要）；同一個權限請求（工具＋正規化輸入）答一次就答所有 repo，規則記在 job 上；每 run 分鐘上限、
  平行數、花費上限、API rate limit 整個 job 退避；agent 開始前可跑 prepare 指令（並從主 checkout 複製 `.env` 等檔案）、
  結束後跑 verify 指令（失敗可送回 agent 一次）；review 把跨 repo 相同的 hunk 分組；收尾可 commit（分支保留、worktree
  目錄移除）、開 PR（`gh`）、discard 或 keep；報告進 inbox 並可匯出 Markdown；伺服器重啟後活著的 session 接回、其餘標記
  stopped。Qt 視窗新增側欄的 Dispatch 入口（徽章是等人處理的數量）、看板（需要你的 job 在最上面，請求與 canary 卡
  就地回答）、job 畫面（每個 repo 一列，canvas 引擎、順序固定、只重畫變了的列；Allow / Deny、Retry / Open、Stop、Skip
  都在列上；brief 可在主波開跑前改）、新 job 的 composer（target 挑選器＋即時 plan）、控制室卡片上的「Allow for all」、
  收件匣的報告卡；job 的 session 在側欄收在 job 底下。
  第三期：**playbook**（brief＋設定＋一組 repo，從 composer 或 job 存、在 composer 載入、Run again 用它再開一個 job）；
  **排程可以派工**（排程對話框多了「A dispatch playbook」：到點用 playbook 開一個無人值守的 job——沒有 canary、權限一律
  拒絕——報告進 inbox）；**agent 寫的報告**（收尾設定選「table, with prose written by an agent」，job 結束後多一個 session
  讀各 repo 的摘要寫三段話，放在 job 畫面、inbox 卡片與 Markdown 匯出裡）；Qt 的 target 挑選器改成 canvas 引擎的列，
  幾百個 repo 也是一個畫面；**web client** 有同一套畫面（看板、job 畫面、composer、側欄入口、inbox 卡片、排程對話框）。
  測試：`test/unit/dispatch.test.ts`（假 session 與真 git repo）、`qt/tests/test_dispatch.py`（對 mock server 完整跑一個 job、
  playbook、排程、報告）。

- **2.10.0**：Qt 對話區整個換成自繪的 canvas 引擎（`qt/pilcrow_qt/canvas/`，設計見 `qt/DESIGN.md`，量測見
  `qt/PERF.md`）。對話不再是一棵 QWidget／QLayout 樹，而是一個 viewport 上的場景：每一列是一個 block，位置由
  Fenwick 樹算、折疊是一個數字（reveal）、每次變動都先讀錨點、改完再把錨點放回原位——所以展開／收起指令與思考過程、
  streaming、遠處高度修正、字寬重排都不會讓眼前的內容位移或閃爍；畫面只重畫真正變了的那一段，靜止的列快取成 pixmap，
  看不到的列不畫也不量（估高 → 快量 → 精量三級，背景預取、遠處回收），長對話開啟時只建模型不建 widget，
  切 session 會保留場景與畫面。串流時只把新字塞進段落的文件、不重排整段；fold 展開前先在 8 ms 預算內精量子項，
  動畫跑在真實高度上。量測（`qt/tests/bench`）見 `qt/PERF.md` 的對照表。
  `widgets/transcript/` 只留權限卡、提問／計畫表單與程式碼 diff 的工具函式；純函式搬到 `lib/folds.py`。
  Bench 的 flicker 量法改為「同一段內容消失一格又回到原位」（內容滑過空隙不算），並多記錄 canvas 的 placeholder
  次數；scrolled-up 串流情境改成送出後再往上捲（送出本來就會跳到自己的訊息）。
  對話區的操作：Tab／Shift+Tab 在畫面上的 fold、卡片、按鈕、連結之間移動焦點（有焦點環），Enter 展開，Esc 清除；
  連點兩下選字、三下選整段；Ctrl+A 全選；右鍵選單有複製、複製這則訊息、開啟／複製連結、開啟檔案／複製路徑；
  Ctrl+=／Ctrl+-／Ctrl+0 縮放對話文字（會記住）；對話區對無障礙工具呈現為一個清單（每列一個項目）。
  視窗其餘部分：Timeline 改成引擎上的清單（300 步從 1.7 s 的卡頓變成幾毫秒）；隱藏的右側分頁完全不工作、
  顯示時才補；Tasks 分頁就地更新列；側欄只搬動真的移動的列、不再每次全部重新掛上；Changes 的 diff 只量一次行寬、
  調整寬度不重排、檔案卡依路徑保留；Collapse 改走共用時鐘並以實際寬度量高；composer／標題列／側欄列不再在沒變時
  重設樣式；開啟 session 時快照分批（每個事件迴圈 300 則）進模型，不再卡住視窗；`qt/tests/test_perf_budget.py`
  在 `PILCROW_PERF=1` 時以短 bench 檢查 `qt/PERF.md` 的目標（`npm run release` 會跑）。
  主題改走調色盤：`theme.py` 把 token 對到 QPalette 的角色，`theme.tint()` 給單一 widget 自己的顏色（換主題時自動重套），
  應用程式樣式表改成靜態、顏色只寫 `palette(role)`、只設一次；按鈕（`ui/button.py`）、卡片、藥丸、細線、彈窗、
  toast、搜尋列與對話框底板都自繪。切換主題變成換調色盤加重畫：600 回合對話＋Changes 面板打開時 124–146 ms
  （原本 3.5–4 s）。
  介面走了一遍（`qt/UX.md`，原則是「讓版面自己說明」）：空狀態不再說兩次——控制室沒有工作中的 session 時不再放
  一個「Working 0」的說明框；收件匣沒有排程時只留主視覺與一顆按鈕；Tasks 分頁只畫有內容的區段，什麼都沒有時是一個
  空狀態；Skills／MCP 設定頁在導覽卡出現時工具列不再多一顆同名按鈕、群組提示不重複；Terminal 不可用的提示放到分頁
  頂端；Timeline 關閉時直接給「到設定開啟」的按鈕；Files 的樹是空的會說原因。修掉幾處被切掉的東西：新 session 的範例
  提示第二排、換行的 toast（也修了剛關掉對話框後 toast 不出現的問題）、窄視窗下的標題（改成省略號）與路徑列
  （560px 以下收起）、composer 的權限按鈕（420px 以下只留圖示）、Changes 檔案卡的資料夾顯示成 `web/src/lib/.`。
  側欄切到封存清單時頂端有「← Archived」；新 session 對話框只留一個 Browse，系統選擇器搬進瀏覽器的工具列。
  對話區排版再走一遍（`qt/UX.md` 第 5 節）：表格的欄寬照瀏覽器的 auto layout 由每格最寬的字與最寬的一行算出來，放得下就照內容寬（至少佔欄寬六成），放不下也不會把程式碼或中文從中間切斷、標題不換行；程式碼區塊一行一段，太長的行折到自己縮排再進兩格，角落多了 Copy；Read 與 diff 的行號欄照最寬的號碼算寬（原本兩位數會被折成兩行）；工具卡片的內容從動詞底下開始（原本被多縮排一次、右邊被切掉）；思考寫完後以 Markdown 排（清單、行內程式碼、段落），串流中維持純文字；行內程式碼的灰底框縮到和網頁一樣的 0.38em（原本每邊多出約 10px）；任務清單在標記的位置畫方框；清單標記換層換形、顏色變淡；斜體註解裡的中文保持正體；按下任何文字就能拖曳選取，行頂的留白不再選到結尾。
  對抗式審查（`qt/AUDIT.md`，壓力腳本在 `qt/tests/stress/`）找到的前五項已修：串流中的程式碼區塊、清單、表格不再每次刷新整塊重排，而是就地長大（`canvas/grow.py`：一行、一項、一列各自接進既有文件，1000 行的程式碼每個 token 0.6–0.9 ms，原本 360 ms；1000 項的清單 1–1.6 ms，原本 222 ms；500 列的表格 2–3 ms，原本 181 ms），寫完時直接保留長出來的文件（高度與重排相同），畫面不動也不再重排；超長段落切成固定位置的續段，串流與寫完切在同一處；開啟長對話時骨架撐到載完才一次跳到底，不再先閃出開頭；側面板第一次開啟不再卡 240 ms（lexer 快取、檔案卡分批建、面板在閒置時先建好）；composer 貼上幾千行後每個按鍵不再要 54 ms。
  審查剩下的七項也修完：比欄寬還寬的圖片縮到所在那行的空間（引言、清單內也算）；引言或清單裡的程式碼區塊
  不再超出欄寬（Qt 把 `width="100%"` 算成整個 frame 的寬，現在排版後補成實際剩餘的寬度，`paint.fit_doc`）；
  所有文件的 tab 停駐點都是 4 個等寬字元（原本是 Qt 預設的 80 px，約 11 格），tab 縮排的行換行後接在正確的位置；
  指令輸出先照終端機的方式顯示再讀顏色（`lib/ansi.py overwrite`）：`\r` 回到行首覆寫、`ESC[K`／`ESC[2K` 清除、
  `ESC[nA` 上移重寫，進度條只剩最後一個狀態、每個字的顏色保留，沒有這些控制碼的文字原樣回傳；
  細線在 1.25×／1.5× 的螢幕上不再糊成兩排灰（`canvas/pixels.py` 把線與框邊貼齊實體像素，表格線、引言線、
  程式碼框、卡片、分隔線、側欄與對話框的細線全部改走）；塞不下的表格每欄至少保留 Qt 本來就不會再縮的寬度
  （最寬的字加內距）再按比例擠，所以表格停在欄寬上，而被擠的欄位裡的行內程式碼允許在斜線、句點處換行；
  語法上色快取改成以原始字數（4 MB）而非筆數計；3000 行的程式碼區塊串流到最後每個 token 26 ms 的問題
  （每次重畫都掃過整份文件找框的位置）也修了，現在 5.0 → 5.7 ms。Changes 分頭：檔案卡改成每個事件迴圈
  建 4 ms（原本數量制），清單高度釘在卡片總高（`SetMinAndMaxSize`，原本每加一張卡整份清單重排兩次，
  一百個檔案 5 萬個幾何事件），面板在閒置時先離屏排版畫一次、session 編輯過的語言的 lexer 一回合編一個，
  開啟面板 5 ms、一百張卡填入時每回合 11–13 ms。測試在 `qt/tests/test_pixels.py`、`test_markdown.py`、
  `test_lib_ansi.py`、`test_loading.py`。
  最後以純 CPU 的效能標準審查整體演算法與資料結構（`qt/REVIEW.md`，每項都附量測）：骨幹（Fenwick 高度、錨點、三級量高、
  pixmap 快取、grower、會停的時鐘）站得住；要修的是三個會隨規模長大的地方——工具輸入串流時每個 delta 都從頭重解
  partial JSON（114 KB 的 Write 要花主執行緒 9.7 s，二次方）、每次 flush 都做整段對話長度的清單／字典重建
  （6000 回合時每個 token 2.8 ms）、以及文件經 HTML 往返建構（富文字區塊 3.2 ms，直接用 QTextCursor 建約可省一半）。
  三項都修了：工具輸入串流時的 partial JSON 掃描改成帶狀態、逐段推進，只在畫面 flush 時解析一次
  （114 KB 的 Write 整段串流從主執行緒 9.7 s 降到 0.16 s，單個 delta 幾微秒）；每次 flush 只碰尾端，已定的前綴
  連同鍵與索引跨 flush 保留（6000 回合時 scene.sync 從 3.46 ms 降到 0.25 ms，一個 token 2.83 → 0.62 ms）；
  對話區的文件改由 markdown-it 的 token 直接用 QTextCursor 建（`canvas/build.py`），不再經 HTML 字串、
  regex 後處理與 Qt 的 HTML 解析器，四十種結構與原本的渲染逐像素相同（`tests/test_build.py`），並順手修了
  三個舊錯（行內程式碼裡的路徑又能點了、縮排程式碼區塊是井、清單裡的程式碼不再變暗）；公平量測（兩邊都排版完）
  快 13–30%，不是審查估的兩倍——剩下的大半是 Qt 的排版本身。另外量了順暢度（`tests/stress/s_smooth.py`）：
  1500 回合甩動 61 幀中位數 16.8 ms、最差 19.6、無遲到幀；fold 展開／收起每幀 16 ms、錨點不動。

- **2.9.8**：Linux 自繪標題列的標題對齊對話區：從對話卡片的文字欄開始（與 session 標題同一條線），側欄收起時移到最左、
  拖寬側欄時跟著走。

- **2.9.7**：Qt 視窗：卡片上方的更新／斷線提示列置中（和網頁一樣），太長時以省略號收尾而不是被切掉或擠成多行；
  Linux 自繪標題列不再放 ¶ 圖示，只留標題（側欄的品牌列已經有圖示）。

- **2.9.6**：Qt 視窗的外觀打磨到與網頁版一致（以 `test/e2e/shots.mjs` 的網頁截圖逐格對照 `qt/tests/snap.py`、
  `panel_probe.py` 的 Qt 截圖，量到像素）。外殼：對話區與右側面板是和網頁一樣內縮、圓角、帶髮線的卡片，標題列在卡片裡、
  沒有整條底線；Linux 自繪標題列與側欄同一個面。側欄：搜尋是和「New session」一樣的一列（不再是輸入框）、下方髮線、
  快捷鍵與群組的 chevron/+ 只在 hover 出現、選中列是內縮的圓角底、時間在 hover 時換成 ⋯；側欄與右側面板都可拖邊緣調寬
  （200–440 / 340–62%），寬度會記住。對話：行高 24px（14.5px × 1.65）、行內 code 畫成圓角小藥丸且不再在中間斷行、
  程式碼區塊有語言列與底色（之前因為正規式沒對上而畫成純文字）、使用者泡泡、fold 標題、工具列、權限卡、workflow 列、
  Latest、浮動的搜尋列全部照網頁。Composer：髮線外框、焦點光暈與陰影、送出鈕的彈簧、@ 與 / 清單、模式與模型選單、
  附件與排隊訊息、下方的快捷鍵提示列。右側面板：Changes 在非 git 資料夾改顯示「本次 session 的編輯」與 diff、Tasks 的
  待辦/進行中/完成區與 workflow 詳細檢視、Session、Timeline、Files 與編輯器、Terminal、Preview 都對齊網頁。頁面與對話框：
  Welcome 改成網頁的樣子（發光的圖示、問候語、大輸入框、資料夾與快捷鍵）、Control room 單欄版面與標題字重、Inbox、
  設定的每一頁（下拉選單改成網頁的 select + popover，開啟、hover、勾選都一致）、命令面板（空查詢列出動作與 session）、
  新 session、排程、快捷鍵、確認框、選單、toast。
  底層：`setProperty("size", …)` 從來沒生效（QWidget 本來就有 `size` 屬性），字級/字重的樣式規則因此全部沒套用，
  改名為 `fs`；測試之間重設版面並清掉上一個測試留下的頂層視窗（側欄 hover 測試在整套跑時才會壞的原因）。
  mock server 多了 `mock.answer`、`mock.patch`、`mock.messages`，讓面板各種狀態能被截圖。

- **2.9.5**：Qt 視窗不再閃退。送出訊息、打開右側面板、展開工具卡都會跑彈簧動畫，動畫曲線原本是把彈簧取樣成
  16 段貝茲曲線交給 Qt；Qt 的 `BezierSpline` 內部只預留 10 段的空間又不會長大，第 11 段起就寫到別人的記憶體，
  之後在毫不相干的地方（量泡泡大小、回收物件）崩掉——這就是 Linux 上「送出訊息、右邊功能列一點就閃退」。現在取 8 段。
  測試套件與 Linux 容器都能以 `PILCROW_MOTION=1` 把動畫打開跑一遍（之前一律關閉，所以沒抓到）。
  順手修到的：搜尋（Ctrl+F）跳到的那一列，在剛畫出來的頁面長到真實高度時不再被推出畫面；搜尋展開的 fold 直接開、不做動畫。

- **2.9.4**：Qt 視窗在 Linux 上的字型修正，用 Ubuntu 24.04 容器（`test/linux/`：真的跑安裝檔、Xvfb 上開視窗逐頁操作）
  查出來的：(1) 元件自己的 stylesheet 一有 `font-size`/`font-weight`，Qt 會從應用程式預設字型重解析，Linux 預設是 DejaVu，
  所以很多字變寬變擠——現在應用程式預設字型就是 Inter；(2) 可變字型在 FreeType 下不會合成字重，改內附 400/500/600/700
  的固定實例；(3) latin 與 latin-ext 子集是兩個檔都叫「Inter Regular」，fontconfig 挑到只有擴充字元的那個，ASCII 逐字退回
  DejaVu——現在每個字重合併成一個檔。Inbox 空狀態的說明文字不再被切掉最後一行。

- **2.9.3**：Qt 視窗：字型改以像素定大小（不再受 Linux 的 Xft.dpi 影響）、一律用 Fusion 樣式並補齊下拉選單的樣式、
  Linux 上畫自己的標題列（`--native-titlebar` 可關）、原生崩潰會留 Python 追蹤在 `~/.local/state/pilcrow-host/qt-crash.log`。
  安裝檔：系統 Python 拒絕 pip（PEP 668）時改建 `qt/.venv` 並寫進 `.launch-env`，啟動器優先用它。
  介面原始碼裡的零寬空白改成跳脫寫法。

- **2.9.2**：**原生 Qt 視窗**（`qt/pilcrow_qt`，PySide6 / Qt Widgets）：給沒有 GPU 的 Linux 機器的桌面視窗，純 CPU 繪圖，
  不經過瀏覽器引擎。接同一個伺服器與 RPC；側欄、對話、工具卡、權限卡、Composer、Control room、Inbox、設定、
  各面板（Changes、Files 與編輯器、Terminal、Tasks、History、Info）都是原生實作，Preview 改用系統瀏覽器開啟。
  `pilcrow` 有 PySide6 就開 Qt 視窗（`--qt` / `--electron` / `--chrome` 可指定）；`pilcrow-app.sh` 同樣優先 Qt，
  `--webkit` 回到 WebKitGTK。安裝檔多一步 `pip install --user -r qt/requirements.txt`（`--skip-qt` 略過），
  字型以 base64 bundle 內附。測試：`python -m pytest qt/tests`（139 個：純邏輯移植 + 離線視窗測試，接 mock server）。
  量測（`python qt/tests/perf.py`，offscreen，400 回合）：開啟到列出現 0.38 秒、第一次繪製 22 ms、串流時每格重畫
  約 7 ms、閒置 CPU 約 5%。

- **2.9.1**：測試套件重建：ESLint 設定（typescript-eslint recommended + react-hooks，禁止 any）、vitest 單元測試
  （`test/unit`，68 個：transcript 模型、分段、工具摘要、格式化、partial JSON、workflow、ANSI、fuzzy）、
  假伺服器 `test/mock/mock-server.mjs`（同一套 RPC、腳本化的 Claude）、Playwright 瀏覽器測試
  `test/e2e/run.mjs`（15 個）、WebKitGTK 檢查 `test/webkit`（沒有 rig 就跳過）。`npm run release` 會跑全部。
  兩個修正：剛開啟 Pilcrow 的半秒內打開一段很長的對話（例如自動恢復上次的 session）時，遠處的頁換成真實高度
  會被當成「讀者往上捲了」，對話停在離底部兩百多像素的地方，打字時也不跟著調整。瀏覽器測試抓到的。
  Workflow 卡片讀 `export const meta = { … }` 時，`phases: [{ title }]` 這種物件形式的 phase 會讓
  整段 meta 在第一個 `}` 就被截斷，標題與欄位讀不到；改成數括號找真正的結尾。

- **2.9.0**：工程品質這一版，介面行為不變。(1) 型別：前端 102 個 `any` 全部換成真正的型別——SDK 訊息（`RawMsg`、`ContentBlock`、`StreamEvent`）、工具的結構化結果（`StructuredResult`、`PatchHunk`）、工具輸入（`ToolInput`）、權限回覆（`PermissionDecision`）、MCP 狀態、原生橋（`window.pilcrowNative`）都有定義；`tsc` 與新加的 ESLint（typescript-eslint recommended + react-hooks，`no-explicit-any` 為 error）都是零錯誤零警告。順手修到的：`errText` 現在也讀純物件的 `message`（RPC 的拒絕有時長這樣）；設定裡的 `panelTab`/`diffMode`/`view` 讀回來時會驗證，壞掉的值不再進狀態。(2) 測試：`test/unit`（vitest）蓋 transcript 模型（歷史載入、串流中的文字與工具、中斷、只結算未結算項目而不重掃 2000 回合）、回合分段（`groupTurns` 從 Transcript.tsx 抽到 `lib/units.ts`）、工具摘要、格式化、partial JSON；`test/e2e`（Playwright、headless Chromium）17 個測試把這幾版的 bug 都變成斷言：root fold、live fold、回答不被收起、archive 不閃、順序不跑、對齊、送出回底、Latest、打字零抖動、長對話開啟時間與分頁、Find、捲動記憶、Control room 版面；`test/webkit` 用真的 WebKitGTK 畫出來逐格量像素，確認 Control room 上緣、捲動與串流每一格都有內容，沒有 rig 就跳過。假伺服器 `test/mock/mock-server.mjs` 進了 repo。(3) 發佈：`npm run release -- x.y.z` 一個指令從工作樹到三個安裝檔（版本、檢查、測試、建置、打包、解開比對、commit），打包器 `scripts/make-installers.py` 與安裝檔模板也進了 repo，不再依賴外部資料夾。
- **2.8.13**：這次在容器裡裝了和你一樣的 WebKitGTK 2.52.6，用它逐格截圖才看到三件 Chromium 上看不出來的事。(1) 換頁時 2.8.6 會把離開的那一頁整個複製一份、釘在上面淡出；WebKitGTK 合成得慢，那份複本會蓋在 Control room 的標題和統計磚上好一會兒——這就是「上面的格子被擋住」。頁面層級的離場複本拿掉了（選單、對話框、toast 的小型離場保留），只留新頁面升起。(2) WebKitGTK 對捲動容器內 `content-visibility: auto` 的「是否在畫面上」判斷又慢又錯，捲動時整頁一下空白一下出現；WebKit 上改為不用它，交給自己的分頁機制（遠的頁空、近的頁畫）。(3) 每一列（段落、思考、工具）掛著進場淡入動畫，歷史頁在捲動中被畫出來時整頁段落一起從透明淡入，看起來就是閃爍；現在只有進行中的頁才有進場動畫。側欄順序：改成完全只看「你最後一次送訊息」，transcript 檔案被寫到（Claude Code 的背景摘要、行程結束時的收尾）一律不再移動列；之前沒記錄過的 session 第一次被看到時就固定在當時的位置。Control room 區塊進場的延遲縮短（320/70ms→220/40ms）。Linux 外殼：更新後若舊版伺服器還在背景且沒有工作在跑，啟動時會自動換成新版，不再出現「background process is still x.y.z」。
- **2.8.12**：在輸入框打多行文字時，上面的對話不再抽動。兩個原因：輸入框自動調高時會先以 height:auto 量一次（那一瞬間整頁重新排版、對話區被夾到較小的捲動位置，等輸入框回到真實高度就差了幾個像素），現在量完會把對話放回原本的底端；對話區本身變矮（輸入框變高、面板開合）時改為同一幀內直接對齊，不再用緩動追——緩動追的本身就是抽動。實測 6 行輸入與刪除，最後一列到視窗底的距離 200 幀內零變化。
- **2.8.11**：Control room 的內容區上方多留 12px（6→18px），統計磚不再貼著標題列。
- **2.8.10**：往上看歷史時送出新訊息，對話會自己回到最底並跟著回覆走（以前停在原處）。
- **2.8.9**：兩個 bug。Control room 的 Working 卡片整張擠成一行：2.8.6 給卡片加的 `working` 類別撞到對話區「正在跑」那一行的 `.working { display: flex }`，改名避開。「明明在最底卻跳出 Latest、也捲不下去」：根因是「讀者捲上去了」的判斷只看位置有沒有比上次設定的小——而遠處的頁換成真實高度時位置也會變小，於是被當成讀者離開、停止跟隨；現在只有滾輪、鍵盤、捲軸、觸控這些讀者自己的動作才算離開。順便：打開長對話時會一直落到底，直到高度穩定（遠處的頁先以估計高度占位，不再依賴瀏覽器何時判定它離開畫面）；視窗或輸入框變高、側欄面板開合讓對話區變矮時也會跟上；新訊息來時若其實已在底部就不顯示 Latest。
- **2.8.8**：長對話效能做到底。對話分成「頁」（每頁 8 個單元），離視窗遠的頁是一個空盒子、只留估計高度（捲軸不變），靠近時（提前 1600px、每幀畫一頁、快速甩動時先不畫）才真的畫出來；畫過的頁留著，而且每個對話記得哪些頁畫過，所以切回來時高度一樣、位置精準（還原時多做兩幀校正）。Ctrl+F 會先把整段對話畫出來再搜。從 Timeline／檔案歷史跳到某一步，會先把那一頁畫出、把摺疊打開再捲過去（以前在收起的摺疊裡會找不到）。另外抓到四個 O(n) 或 O(n²) 的熱點：一輪結束時 transcript model 掃全部項目（2000 輪重播時是 32M 次迭代）改為只看未結束的；跟隨串流的捲動不再每幀讀 layout；側欄的 FLIP 量測改在畫完之後、且只在列可能移動時做；同樣的 session 清單不再換新陣列。React 列表包進自己的容器，避免 React 在大量插入時走 getHostSibling 的二次方路徑。實測（Chromium，mock）：400 輪／3,200 則——打開 4.9s→0.3s、DOM 節點 35k→2.7k、串流一輪 long task 2.06s→0.2s、快速捲動 p95 幀 31→27ms；2000 輪／16,000 則——打開 0.3s、串流 0.2s、p95 46ms。伺服器端讀 12MB／2000 輪的 transcript 是 0.66s（之後快取 0.2s），不是瓶頸。
- **2.8.7**：2.8.6 併入主線，加上審查時抓到的修正。長對話捲動：2.8.6 的捲動處理為了記住位置，每個捲動事件都掃過全部單元讀 offsetTop（O(n) 且每次強制 layout），400 輪的對話快速捲動時 p95 幀時間 70ms、三成的幀超過 50ms；改成二分搜尋後 p95 約 40ms、幾乎沒有卡幀，比 2.8.5 還順。側欄用 Ctrl+B 關了再開會變寬（268→323px）：`Slot` 動畫結束時把 `<aside>` 的 inline width 清成空字串，抹掉了 React 設的可調寬度；現在存起原值再還原。對話末端常駐渲染的單元改以最後四個 child 計算（working line 也在其中）。摺疊展開的彈簧上限從 560ms 壓到 420ms。實測 400 輪對話：打開 4.9s→0.3–1.5s、串流一輪 long task 2.06s→0.69s、heap 66→53MB。
- **2.8.6**：介面的動態與材質整個重做，目標是 iOS 等級的手感。真正的彈簧：以阻尼彈簧物理取樣成 CSS `linear()` 曲線（soft / snappy / bouncy），所有會動的東西都用它們。離場動畫：對話框、選單、Toast、搜尋列、@ 建議、佇列、附件、通知列、「Latest」膠囊關閉時留一個靜止殘影播放退場，不再瞬間消失（`useExitGhost`）。側欄與右側面板以寬度滑開／滑合（`Slot`），主畫面邊距同步跟著；側欄的選取高亮用 CSS anchor positioning 釘在該列上，切換時在列之間滑動。浮層改為半透明毛玻璃，對話框背後的遮罩加深模糊；深色模式的卡片加上頂邊受光線。標題列改成毛玻璃、對話內容從底下滑過；輸入框上方是漸進式模糊。送出訊息時箭頭射出、輸入框以彈簧縮回、氣泡從輸入框飛進對話串；串流中的回覆末端有橘色游標。所有可按元件統一「按下縮、放開彈回」；開關按住時旋鈕拉長；分段控制有會滑動的拇指；chevron 用彈簧旋轉；設定頁高亮滑動、頁面淡入；面板分頁內容淡入；選單逐列展開。Session 面板的 context 改成會填滿的環形儀表，費用與 token 數會滾動（Control room 亦同）。diff 逐行寫出、程式碼上色淡入；Plan / Bypass 模式會為輸入框染色；頁面切換交叉淡出；圖片改為應用內燈箱。另外：index.html 的主題啟動腳本原本被 CSP 擋掉（淺色主題載入會先閃深色），改為外部的 `public/theme.js`。`scripts/make-installers.mjs` 可從這棵樹重新產生兩個安裝檔。
- **2.8.5**：Claude 說的話一律留在頁面上。以前一輪裡只有「最後一段」算回答，中途輸出的文字一旦後面又接著思考或工具，就會被收進摺疊裡——看起來像閃一下就不見。現在一輪拆成多段：每一段文字都留著（它們都是回答的一部分），每兩段文字之間的工作（思考、讀檔、指令、編輯）各自摺成一行；進行中的那一段是 live 的。只有思考、沒有步驟的一段不再多包一層摺疊，直接是「Thought for Ns」那一列。串流時文字不會再跳進摺疊裡。
- **2.8.4**：側欄頂端對齊：¶ 商標改用裁到墨跡的版本，佔與下方列圖示相同的 15px 欄位，「Pilcrow」與「New session」「Search」「Control room」的文字同一條左緣（x=42）、圖示同一條左緣（x=18）。
- **2.8.3**：Archive / Delete 時那一列自己退場：先淡出、再把高度收起來，底下的列跟著版面平順地合上，清單真正更新時已經沒有東西要動，所以其他列不再閃。另外兩個造成整欄抖動的根因一併處理：列的滑動（FLIP）以前是量 getBoundingClientRect——一列還在滑動時帶著 transform，下一次量到的是半路上的位置，兩段動畫疊在一起就是一閃；現在量版面位置（offsetTop），而且只有列的集合或順序真的變了才滑動，單純重畫不會重啟動畫。
- **2.8.2**：側欄的順序不再自己亂跑。以前每一列的位置來自 transcript 檔案的 mtime，而 Claude Code 在一輪結束後還會自己往檔案裡寫東西（摘要、快照、簿記），加上 Pilcrow 自己又拿「最後有訊息進來的時間」蓋過去、閒置回收行程時再換回檔案時間——所以你沒碰的 session 會突然上移或下移。現在 Pilcrow 開過的 session 一律以「你最後一次送訊息」排序（建立、送出、fork 會更新），檔案時間只在 transcript 於我們最後一輪結束十分鐘之後還被寫到（也就是你從終端機用了它）才算數。Archive 也不再整欄刷一下：重新整理清單時沒變的列保留原物件、列本身 memo 化，只有真的變了的列才重畫。
- **2.8.1**：Normal view 打開任何對話都只剩每一輪的 root 那一行——包括正在跑的那一輪：進行中的工作也折成一行，行首是呼吸的 ¶、文字是此刻在做的事（Editing Transcript.tsx… / Running a command…），右側是步數與已花時間；要看過程就點開，輪次結束時 ¶ 往左收走、文字落回答案的左緣。展開後章節一律只看到標題（Normal view），失敗的章節也不再自動展開；等你批准的步驟仍會自己浮出來。重開對話時所有折疊都回到預設，不記得上次展開了什麼。另外修了一個 2.7.0 起的 bug：一輪進行中串流的文字與工具列要等整輪結束才會出現。
- **2.8.0**：展開一輪的過程時改以「章節」呈現：Claude 中途說的每一段話（「我先看一下 X，接著…」）本來就是在說明後面那些步驟的目的，所以它成為章節標題，後面的步驟縮排在底下，右側一個小統計（3 edits · 5 commands · 1 failed · 40s）；緊接在說明前的思考併入該標題下。章節預設收起，只有失敗、等你批准、正在跑的章節預設展開；左側一條 rail 串起章節，正在跑的節點會呼吸。Verbose 模式全部展開。
- **2.7.1**：摺疊的過程摘要行與下方回答左緣對齊，箭頭移到文字後面（同 Claude 桌面版）。
- **2.7.0**：對話改成「一輪一摺」：每一輪結束後，你的訊息和最後的回答之間所有的過程——讀檔、搜尋、指令、編輯、中途的思考與說明——摺成一行摘要（例如「Edited 3 files, ran 13 commands, read 4 files, searched 6 times · 1 failed」，右側標步驟數），點開才展開整個過程（中途的說明以較淡的字呈現），回答永遠單獨立在下面。執行中的那一輪維持展開讓你看得到進度，結束時自動收起；有等你批准的步驟時不會收起；Thinking／Verbose 檢視模式預設展開；展開與否會記住。
- **2.6.7**：對話欄寬度改用黃金分割：欄寬＝可用寬度的 61.8%，兩側留白合計 38.2%，任何視窗大小都是同一個比例；欄寬最少 640px（留白先讓到 24px），最多 1080px（再寬的螢幕由留白吸收）。這也是 Claude 桌面版實際呈現的比例。
- **2.6.6**：對話欄兩側留白加寬到接近 Claude 桌面版：每側約 16%（24–420px），欄寬最少 680px、最多 1150px。
- **2.6.5**：修正 2.6.2／2.6.4 安裝包裡的介面版本號比伺服器舊（打包前沒有重新 build），導致視窗一直自動重載。現在每個伺服器版本只會自動重載一次，不可能再迴圈；打包工具也會拒絕版本不一致的 bundle。
- **2.6.4**：提示訊息（toast）改到右下角、composer 上方，從右側滑入，不再擋在對話中間；同一則訊息重複出現時合併成一則並顯示 ×N；滑鼠停在上面時不會消失；已知的錯誤改用人話說明並附上出路（例如 OAuth 過期 → 「Claude Code 已登出，請在終端機執行 claude login，或到 Settings → Claude Code 設定 gateway 金鑰」＋ Settings 按鈕）。
- **2.6.3**：裝新版時若舊視窗還開著，以前會出現「Pilcrow x is installed. Reload this window to use it.」要你手動重載；現在沒有 session 在跑時會在 1.2 秒後自動重載接上新版，有 session 在跑時才留一行提示（並說明背景的 session 不會中斷）。
- **2.6.2**：開啟動畫重做。第一幀就是正確的主題底色（index.html 內建 splash，沒有白閃）；¶ 的橘色碗部先以彈簧曲線落下，橫槓與兩根字幹依序「放下」，名字在下方以字距收攏的方式浮現；等待時碗部緩慢呼吸；5 秒後提示「第一次啟動需要一點時間」、16 秒後提示重開；連上後整個畫面放大淡出，app 在底下升起。
- **2.6.1**：HTML 也能直接預覽。.html 打開時預設是渲染後的頁面（在沙箱 iframe 裡，從檔案所在的資料夾提供，所以相對路徑的 CSS／JS／圖片都會載入，頁面自己的 script 也會跑）；同一組「預覽／編輯／並排」切換；預覽顯示的是存檔後的內容，有未存的修改會出現「Save to refresh」；另有重新載入與「在瀏覽器打開」。
- **2.6.0**：Files 面板可以直接讀 Markdown。.md／.markdown／.mdx 打開時預設是排好版的頁面（標題、清單、表格、程式碼、引用、相對路徑的圖片都會正確顯示），工具列多了「閱讀／編輯／並排」三段切換；並排時左邊改右邊即時更新，檔案樹會自動收起來讓出空間。
- **2.5.2**：執行中的對話不再閃。主因是「安靜的步驟」（Read／Grep／Bash）每完成一個就立刻被折進「Explored」一行，列表尾端跟著跳；現在只要這一輪還在跑，尾端的步驟就維持一列一列，等後面有別的內容或這輪結束才折疊。另外：每個新步驟淡入滑入、狀態圖示完成時交叉淡出、右側的秒數與 +/- 淡入、思考預覽的跑馬字以 160ms 節流滑動、新的文字段落與思考區塊淡入。
- **2.5.1**：執行中那一行的圖示從舊的 Reticle 準星換成會呼吸的 ¶（橘色碗部明暗起伏、字幹不動），動詞也從半導體製程（Aligning、Etching…）換成排版室的詞（Reading、Drafting、Composing、Proofing、Kerning…）。
- **2.5.0**：全面審查後的修整，不限新頁面。側面板分頁在窄面板改成「只有選中的分頁顯示名稱」的分段式樣式，名稱滑入、墨線跟隨；面板內所有區段標題（Context window、Claude Code、Project memory、MCP servers、To-do list…）統一為 15px、去掉前置圖示；標題列的狀態（Working／Needs you）改為點＋文字，不再是膠囊；MCP 通知列變薄；對話裡權限卡的快捷鍵提示移到按鈕外；快捷鍵對話框的「/」改為按鍵樣式。
- **2.4.0**：質感修整。頁面有了明確的字級層次（頁標題／區段／內文）；拿掉重複的狀態圖示與多餘膠囊（快捷鍵提示移到按鈕外）；橘色只留給主要動作與「等你」；淺色主題的卡片改用柔和陰影而不是髮線；時間軸的 scrubber 改按真實時間分佈（長暫停壓縮並標示），拖動時有回饋，「現在」用 ¶ 標記；控制室批准後有確認狀態；卡片與列表有進場動畫；文案統一（Run now／Dismiss／Forget）。
- **2.3.0**：四個 Pilcrow 獨有的功能。**時光機**（側欄 → Timeline）：每個會改到檔案的步驟（編輯、指令、你送出的訊息）都在影子 git 倉庫留一份快照，上方 scrubber 可拖、下方列表可點，看每一步改了什麼、從那一步到現在又改了什麼，可整個資料夾或單一檔案還原（還原前自動留安全點，可 Undo）。**控制室**（側欄 → Control room，Ctrl+Shift+O）：所有 session 一頁看完——等你的在最上面可以直接 Allow / Always allow / Deny（1/2/3），執行中的卡片顯示正在做什麼、workflow 進度、耗時、花費、context；今天完成的清單；最近一小時花費與今天最貴的 session。**排程 + 收件匣**（側欄 → Inbox）：把一段提示排在某個資料夾定時跑（每天／平日／每週／每 N 小時／cron），無人值守時無法批准的動作會被告知跳過；每次結果進收件匣（摘要、改了哪些檔、花費、時間），可一鍵打開完整 session 接手或再跑一次。**專案記憶**（Settings → Memory）：每輪結束後用一個小型 run 把決定、死路、慣例蒸餾成筆記，新 session 自動帶著這些筆記開始；可手動新增、置頂、編輯；Changes／Files 面板的檔案旁會標示「哪些 session 改過這個檔」，點開可跳回那一步。
- **2.2.0**：Workflow 改版——對話區只留一列安靜的工具列（名稱、進度、Open in Tasks），完整流程改在 Tasks 面板：一條直的 rail 串起各 phase（編號 / ✓ / ✕ 節點）、底下縮排列出每個代理（狀態、模型、時間、最近動作、可展開 prompt 與結果），代理 spawn 的 sub-agent 以肘形連線掛在它下面；標題列的 workflow 名稱可點開 Tasks。對話欄寬度隨視窗動態調整（兩側約 8% 留白，最寬 1480px）。
- **2.1.0**：Workflow 圖示化——流程卡（phase 欄位、代理節點、狀態、log、script、結果）、Tasks 面板的 workflow 列、標題列顯示執行中的 workflow。伺服器端合併 task_progress 裡的 workflow_progress（代理 / phase / log）到 task 狀態。
- **2.0.2**：捷徑改為直接指向 `PilcrowHost.exe`（exe 內建尋找 / 啟動伺服器、單一視窗、主題底色；圖示也編進 exe），不再經過 PowerShell。外殼腳本 `pilcrow-app.ps1` 改回純 UTF-8 文字（有 BOM），不再用字元碼拼字串——那種寫法會被 Defender 的 AMSI 當成混淆腳本擋下來。
- **2.0.1**：歡迎頁與新對話的空白頁改用應用程式圖示本身（72 / 56px 的 ¶ 方塊，橘碗落定一次的入場動畫），移除舊的放射狀示意圖。
- **2.0.0**：改名 **Pilcrow**（¶，段落符號）。新圖示：橘色的碗加兩道白色直筆。所有資料夾、指令、捷徑、AppUserModelID、desktop entry 與環境變數（`PILCROW_*`，舊的 `RETICLE_*` 仍可用）跟著改名；第一次啟動會把 `~/.config/reticle`、`~/.local/state|share/reticle`（Windows：`%APPDATA%\Reticle`、`%LOCALAPPDATA%\Reticle`）自動搬到新名字，設定與對話紀錄都保留。安裝檔改名 `pilcrow-install.sh` / `pilcrow-windows.ps1`，會順手移除舊名稱的安裝與捷徑。
- **1.5.0**：Skills 與 MCP servers 管理頁（Settings），對照 Claude 桌面版的 Skills / Connectors 設計；MCP 需要登入或失敗時在 session 內直接處理（標題列提示、工具錯誤下的修復按鈕、OAuth 登入對話框）。新增 RPC：`skills.*`、`mcp.*`、`session.mcp.*`、`session.reloadSkills`；新檔 `server/customize.js`。
- **1.4.0**：權限確認改成一張卡——工具列、指令與「Allow / Always allow / Deny」同在一張面板上，沒有橘色外框；「Always allow」實際會放行的規則寫在按鈕下方一行。檢視模式（Ctrl+O 循環，亦在對話選單）：Normal（步驟摘要、思考折疊）、Thinking（思考展開）、Verbose（所有步驟與輸出展開、不合併搜尋步驟）。每一輪結尾改為橘色 ✱ 加耗時，滑過顯示 token 與費用；上下文用量環常駐在送出鈕旁。右側面板分頁加上文字標籤（寬度不足時自動只剩圖示），預設面板寬度 560；diff 色票改用 Claude Code 的 git 色。內嵌程式碼只有在副檔名是真的副檔名時才當檔案連結（`Math.round` 不再變藍）。
- **1.3.5**：色票改為 Claude Code 桌面版實際的 cds 中性色系（從 claude.ai/code 的樣式表讀出）：淺色頁面 #FFFFFF、側欄 #FAFAFA、文字為黑色 90/60/40%、髮絲線黑色 8%；深色頁面 #191919、側欄 #1F1F1F、浮層 #303030、文字為白色 93/57/40%。錯誤 #A42528/#F17778、成功 #006C10/#53C556、思考紫 #5437B8/#A89AF0。Windows 主程式的視窗底色同步。
- **1.3.4**：對話區改為浮在側欄底色上的圓角面板（12px 圓角、髮絲邊、8px 留白），右側面板同樣處理；側欄收合按鈕改為常駐在「Pilcrow」標題列右側（Ctrl+B 亦可），收起後主畫面標題列左側會出現展開鈕。
- **1.3.3**：改用 Claude 桌面版實際色票——淺色主畫面純白、側欄 #F5F4ED、墨色 #141413；深色 #262624 / #1F1E1D / #30302E、文字 #FAF9F5。工具失敗不再有紅框與紅色側線，只留一行原因，其餘收進「Details」；工具步驟拿掉底色外框，只保留左側細軌。
- **1.3.2**：配色改成 Claude 桌面版那種暖色系（炭灰與米白、Claude 橘作為唯一強調色，思考用淡紫），
  側欄與主畫面之間不再有常駐的分隔線（拖曳把手只在游標靠近時出現）；側欄可以收起（Ctrl+B 或品牌列右側的
  按鈕，收起後標題列左側有按鈕打開）；捲軸改成細的覆蓋式、沒有箭頭、只在游標在上面時出現。對話裡三種聲音
  分開：Claude 說的話是全對比的文字，動作收進淺色區塊，思考是一行淡紫火花。失敗的步驟先給一行人話，
  原始錯誤文字收在「Show details」，輸入參數折疊；MCP 工具不再顯示伺服器 id，改顯示工具名與動作。
- **1.3.1**：一鍵新對話——側欄的「New session」與 Ctrl+N 直接在目前資料夾用預設值建立，不再先開對話框
  （Shift+點擊或 Ctrl+Shift+N 才開完整選項，選項預設收合成一行摘要）；還沒送出第一句之前，標題列的資料夾可以
  直接換。Claude 工作中照樣可以打字：Enter 排進佇列，等這一步結束後依序送出，佇列裡的訊息可以編輯、移除、
  或「Now」插隊（會中斷目前步驟）；↑ 可把最後一則排隊訊息拉回輸入框。Windows 的字型渲染微調、中日文段落行高。
- **1.3.0**：新增原生 Windows 版（PowerShell 安裝檔、開始功能表與桌面捷徑、ConPTY 終端機、
  Windows 路徑與磁碟機、微軟正黑體、Claude Code 的 PowerShell 工具與 Bash 一樣顯示）；
  終端機在 ConPTY 無法啟動時自動改用簡易模式；npm 安裝不再顯示更新提示
- **1.2.4**：修正壓縮（`/compact` 或自動壓縮）過的 session 重新開啟後，只看得到壓縮之後的內容、
  並露出 Claude Code 內部摘要的問題；現在重開後跟當下看到的一樣：完整對話、壓縮分隔線與前後 token 數
- **1.2.3**：模型選單只列 gateway 的模型（拿掉 Claude Code 內建的 Opus / Sonnet / Haiku）；升級後若舊版
  Pilcrow 還在背景執行，安裝檔會詢問是否關閉它，下次開啟也會自動換成新版（修正升級後出現
  `Unknown method` 的問題）；找模型清單時也會試 `<base>/models` 與主機根目錄的 `/v1/models`
- **1.2.2**：模型選單、新增 session 與預設模型都會列出 gateway 提供的模型（讀取 `/v1/models`，
  支援分頁、Anthropic 與 OpenAI 格式、proxy 與自訂 CA）；健康檢查也會列出 gateway 上的模型
- **1.2.1**：安裝檔改為內附建置好的介面，安裝時只裝執行需要的套件，不再需要 Vite / rollup 等建置工具
  （修正內部 registry 沒有 `@rollup/rollup-linux-x64-gnu` 時建置失敗的問題）；
  只內附拉丁字母的字型子集（中文本來就用系統字型）
- **1.2.0**：精修手感與中文使用。回覆逐字平順地出現（gateway 一段一段送來的內容也不會跳動），
  完成的段落與程式碼在串流時不再重繪；展開 / 收合都有動畫；主題切換從按鈕位置展開新主題；
  session 重新排序時平滑移動；自訂提示框（顯示快捷鍵）；Ctrl+F 對話內搜尋；切換 session 會記住
  捲動位置；session 右鍵選單；第一次使用且尚未設定 API 時，首頁直接說明要填什麼、填在哪；
  修正注音輸入選字時按 Enter / Esc 可能觸發送出或中斷、視窗變窄時側邊面板超出畫面、
  「1m 60s」這類時間顯示、複製失敗時沒有提示等問題；失敗的編輯不再顯示 +/− 行數，工具錯誤
  不再露出 `<tool_use_error>` 標籤；分段安裝時 npm 失敗後只要重跑最後一段即可
- **1.1.0**：介面全面重新設計。新的字體（Inter / JetBrains Mono）與深淺色配色，琥珀色只用在
  「需要你」與主要按鈕；工具步驟改成對齊內文的單行、探索步驟自動摺疊；權限 / 提問 / 計畫卡片
  重做；新增平滑捲動跟隨、新內容淡入、對話框與面板的進出場動畫；自訂下拉選單取代系統下拉；
  修正對話框打字時游標會跳回第一欄的問題，以及背景 agent 完成通知在歷史紀錄裡顯示成原始 XML 的問題
- **1.0.0**：第一版
