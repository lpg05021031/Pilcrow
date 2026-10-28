# Pilcrow — Claude Code 桌面版（Linux / Windows）

Pilcrow 是一個跑在 Linux 與 Windows 桌面上的 Claude Code 圖形介面。背後就是真正的 Claude Code
（透過官方 Claude Agent SDK 驅動同一套 harness、工具、權限、CLAUDE.md、skills、MCP），
介面則照 Claude Code Desktop 的工作方式設計：左邊是 session 清單，中間是對話，右邊是
Changes / Files / Terminal / Preview / Tasks / Session 面板。

它沿用你現有的 Claude Code 設定，所以公司 gateway（`ANTHROPIC_BASE_URL` + API key / token）
不用另外設定。

---

## 需求

| 項目 | 說明 |
|---|---|
| Linux x64 或 arm64 | 有桌面環境（X11 或 Wayland）。沒有桌面也能用 `--server` 模式 |
| Node.js 20 以上 | 建議 22.12 以上（Electron 安裝程式需要） |
| npm 能連到 registry | 公司內部 npm mirror 可以；安裝檔本身不需要連外 |
| git | Changes 面板、worktree、commit 會用到 |
| python3 | 內建終端機（Terminal 面板）用；沒有的話退回 `script` 指令 |
| Chrome / Chromium（選用） | 當 Electron 下載不到時，改用它開成獨立視窗 |

---

## 安裝

安裝檔 `pilcrow-install.sh` 是純文字：原始碼直接寫在裡面，建置好的介面以 base64 文字內附
（約 2 MB），所以你的機器**不需要建置**，也不需要 Vite、rollup、esbuild 這類帶平台執行檔的建置工具。
安裝時只會用 `npm install --omit=dev` 從你設定好的 registry 下載執行需要的套件。

### 方法 A：存成檔案再執行（建議）

1. 用文字編輯器（gedit、VS Code、vim…）開一個新檔，貼上全部內容，存成 `pilcrow-install.sh`
2. 執行：

   ```bash
   bash pilcrow-install.sh
   ```

### 方法 B：直接在終端機貼

```bash
cat > pilcrow-install.sh
# 貼上全部內容，然後按 Ctrl+D
bash pilcrow-install.sh
```

> 不要把內容直接貼進 shell 提示字元執行：互動式 shell 會把 `!`、Tab 等字元當成指令處理，內容會被改掉。
> 一定要先存成檔案。

### 方法 C：剪貼簿太小、一次貼不完

用分段檔 `pilcrow-install.part1.sh` … `partN.sh`。每一段都是獨立可執行的腳本，
照順序各存成檔案、各自執行即可，最後一段會檢查前面每一段都寫對了，再開始安裝：

```bash
bash pilcrow-install.part1.sh
bash pilcrow-install.part2.sh
...
```

安裝檔每寫完一個檔案都會用 sha256 驗證；如果貼的時候少了一截或被改到，會直接告訴你是哪個檔案壞了，
重貼那一段就好。

### 安裝選項

```bash
bash pilcrow-install.sh --dir ~/apps/pilcrow   # 指定安裝位置（預設 ~/pilcrow）
bash pilcrow-install.sh --files-only           # 只寫出檔案與介面，不跑 npm
bash pilcrow-install.sh --skip-electron        # 不下載 Electron，直接用 Chrome 視窗
bash pilcrow-install.sh --no-desktop           # 不建立應用程式選單項目
ELECTRON_MIRROR=https://你的內部鏡像/electron/ bash pilcrow-install.sh
```

---

## 啟動

- 應用程式選單 → **Pilcrow**
- 或終端機：`pilcrow`（安裝檔會放一個連結到 `~/.local/bin/pilcrow`）

```bash
pilcrow              # 開桌面視窗（Electron → Chrome app 視窗 → 預設瀏覽器，依序嘗試）
pilcrow --chrome     # 強制用 Chrome/Chromium 視窗
pilcrow --server     # 不開視窗，只印出網址 http://127.0.0.1:47117/（SSH 遠端用，見下方）
pilcrow --doctor     # 健康檢查：Node、Claude Code、API endpoint、桌面環境
```

---

## 公司 gateway / API 設定

Pilcrow 不碰帳號，它啟動的就是 Claude Code，所以設定方式跟 CLI 一模一樣。建議寫在
`~/.claude/settings.json`：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://你的公司gateway",
    "ANTHROPIC_AUTH_TOKEN": "你的key",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
```

（用 `ANTHROPIC_API_KEY` 或 `apiKeyHelper` 也可以。）

如果你是在 `~/.bashrc` / `~/.profile` 用 `export` 設定的：從應用程式選單啟動時，Pilcrow 會先讀一次
你的登入 shell 環境（跟 VS Code 一樣的做法），所以也會生效。

### 選用 gateway 上的模型

Pilcrow 會向你的 endpoint 查詢它提供哪些模型（`GET /v1/models`），列在三個地方：

- 輸入框右下角的模型選單（Claude Code 內建的 Opus / Sonnet / Haiku 不再列出，一律用 gateway 的模型）
- 新增 session 視窗的 **Model**
- **Settings → Claude Code → Default model**

選了之後，Claude Code 送給 gateway 的就是那個模型 ID。查詢用的設定跟 Claude Code 完全一樣
（endpoint、key 或 `apiKeyHelper`、`ANTHROPIC_CUSTOM_HEADERS`、`HTTPS_PROXY` / `NO_PROXY`、
`NODE_EXTRA_CA_CERTS`）。Anthropic 格式和 OpenAI 格式（如 LiteLLM）的清單都看得懂；清單會快取 10 分鐘，
選單最下方可以重新整理。gateway 沒有提供清單時，仍可選 **Custom model ID…** 自己輸入。

確認方式：

- `pilcrow --doctor` 會列出實際使用的 endpoint、設定來源、gateway 上的模型，並試連一次
- App 裡 **Settings → Diagnostics**，或右側 **Session** 面板最下方的 *API endpoint*

---

## Claude Code 程式

安裝時 npm 會一起裝好 SDK 內附的 Claude Code（版本跟 SDK 綁定）。如果你想用系統上自己的
`claude`（例如公司統一安裝的版本），到 **Settings → Claude Code → Claude Code program** 選
*System* 或填路徑。

對話記錄跟 CLI 共用 `~/.claude/projects/`：CLI 開過的對話會出現在 Pilcrow 的清單裡，
Pilcrow 的對話也可以在終端機用 `claude --resume` 接著做。

---

## 桌面視窗：Electron / Chrome / 瀏覽器

Pilcrow 的視窗優先使用 Electron。Electron 的 npm 套件很小，但執行檔（約 100 MB）是另外從
GitHub 下載的；公司網路擋住的話，安裝檔會提示，並自動改用 Chrome/Chromium 的 app 視窗
（獨立視窗、沒有網址列，用起來差不多）。

事後補裝 Electron：

```bash
cd ~/pilcrow
scripts/electron.sh status
scripts/electron.sh download https://你的內部鏡像/electron/          # 有內部鏡像時
scripts/electron.sh from-zip ~/Downloads/electron-v44.7.2-linux-x64.zip  # 自己帶 zip 進來時
```

如果 Electron 因為 Chromium sandbox 無法啟動（有些公司環境關了 user namespace），
launcher 會自動改用 `--no-sandbox` 並記住，不用手動處理。

### 遠端主機（SSH）

```bash
# 在遠端主機
pilcrow --server --port=47117
# 在自己的電腦
ssh -L 47117:127.0.0.1:47117 遠端主機
# 然後用自己電腦的瀏覽器開 http://127.0.0.1:47117/
```

---

## 功能

- **Sessions**：依專案分組、置頂、封存、改名、刪除；*Needs you* 區塊集中顯示等你回覆的 session；
  多個 session 可以同時跑
- **Worktree**：開新 session 時勾選，就會在獨立的 git worktree / branch 裡工作，平行任務互不干擾
- **權限模式**：Ask before edits / Edit automatically / Plan mode / Auto / Bypass（Shift+Tab 切換）
- **權限確認**：工具卡片上直接 Allow / 本 session 一律允許 / Deny（鍵盤 1 / 2 / 3）；
  或按 *Suggest something else* 直接告訴 Claude 改做什麼
- **Plan mode**：計畫用卡片呈現，核准後自動切換模式；或寫下修改意見繼續規劃
- **AskUserQuestion**：Claude 的選擇題用表單回答
- **步驟摺疊**：讀檔、搜尋、唯讀指令等探索步驟自動收成一行（例如「Ran 3 commands, read a file」），
  點開才看細節；修改檔案、失敗、等你確認的步驟永遠單獨顯示
- **Diff**：Claude 提出修改時直接展開差異（逐字標示、語法上色），完成後收成一行並顯示 +N −M；
  Changes 面板看整個 working tree，可切換 unified / side-by-side（面板窄時自動換行），
  在行上留評論再一次送給 Claude，commit，或請 Claude 開 PR
- **還原檔案**：任何一則訊息上的 ⟲ 按鈕，可以把檔案還原到那則訊息之前的狀態（同 `/rewind`）
- **Edit & resend / Fork**：從任何一則訊息分岔出新的對話
- **Subagents**：子代理的工作過程巢狀顯示在 Agent 卡片裡
- **Files**：檔案樹 + 編輯器（CodeMirror，支援 Verilog / SystemVerilog / VHDL / Tcl / Python / C++…）
- **Terminal**：每個 session 可以開多個終端機，跟 session 同一個資料夾
- **Preview**：開發中的網頁直接在旁邊預覽，會自動抓 Claude 啟動的 localhost 網址
- **Tasks**：Todo 清單與背景工作，可以停止背景工作
- **Session**：context 使用量、成本、模型、MCP server 狀態（需要登入或連不上時直接 Sign in / Retry）
- **Skills**（Settings → Skills，或 command palette 的「Skills…」）：列出個人（`~/.claude/skills`）、專案（`.claude/skills`）
  與 plugin 的 skills；新增（填名稱和一句「什麼時候用」，直接在 Files 面板編輯 SKILL.md）、匯入資料夾、複製到另一個範圍、
  刪除；變更會通知所有執行中的 session 重新載入
- **MCP servers**（Settings → MCP servers，或 palette 的「MCP servers…」）：讀 Claude Code 自己的設定
  （`~/.claude.json` 與專案的 `.mcp.json`），新增（HTTP / SSE / 指令，或貼 JSON；範圍：所有專案 / 本專案 / 本專案只有你）
  與移除都透過 `claude mcp` 寫入，格式跟 CLI 一致。有 session 在跑時每一列顯示即時狀態：Connected（可看工具清單）、
  Needs sign-in、Failed（附錯誤）、Disabled；可重連、停用、簽入 / 簽出、批准或拒絕專案的 .mcp.json server
- **Workflows**：Claude 用 Workflow 工具開一個多代理流程時，對話裡會出現一張流程卡：每個 phase 一欄、欄裡是該階段的代理，
  每個代理有自己的狀態（排隊 / 執行中 / 完成 / 失敗 / 重用快取 / 被擋）、模型、耗時、最後一步或結果預覽，點開看 prompt 與結果；
  卡片上方是整體進度（完成 / 執行中 / 失敗），底部可展開 log 與 script；標題列的狀態會寫出正在跑的 workflow 名稱；
  Tasks 面板列出所有 workflow，點一下跳到卡片；可以從卡片上停止 workflow。
  Workflow 功能由 Claude Code 自己的設定決定（`~/.claude/settings.json` 的 `enableWorkflows`，依方案開放）。
- **MCP 登入與故障**：server 需要重新登入或連不上時，session 標題列下方會出現一行提示，一鍵 Sign in / Retry；
  MCP 工具因為授權或連線失敗時，錯誤下方直接給出「Sign in to …」或「Reconnect …」。登入走 Claude Code 自己的 OAuth
  流程：Pilcrow 開啟登入頁，瀏覽器跳回 localhost 時自動完成；跳不回來時可把網址貼進對話框完成
- **對話內搜尋**：Ctrl+F 在對話裡找字，所有符合處同時標出，Enter / Shift+Enter 逐一跳轉
- **中文輸入**：注音、倉頡等輸入法選字時按 Enter / Esc 只會交給輸入法，不會誤送訊息或中斷 Claude；中英文之間自動留出細縫
- **其他**：`@` 引用檔案、`/` 指令、貼上圖片、command palette、桌面通知、深淺色主題、session 右鍵選單

### 快捷鍵

| 快捷鍵 | 動作 |
|---|---|
| Ctrl+K | Command palette（輸入 `@` 搜尋檔案） |
| Ctrl+N | 新 session（對話框裡 Ctrl+Enter 直接開始） |
| Ctrl+F | 在對話中搜尋（Enter / Shift+Enter 跳下一個 / 上一個） |
| Ctrl+B / Ctrl+\\ | 切換左側欄 / 右側面板 |
| Alt+1…6 | Changes / Files / Terminal / Preview / Tasks / Session |
| Ctrl+Alt+↑ / ↓ | 上一個 / 下一個 session |
| Shift+Tab | 切換權限模式 |
| Esc | 停止 Claude |
| Ctrl+, | 設定 |
| Ctrl+/ | 所有快捷鍵 |

---

## 檔案位置

| 位置 | 內容 |
|---|---|
| `~/pilcrow/` | 程式本體（可用 `--dir` 改） |
| `~/.config/pilcrow/` | Pilcrow 設定、session 標題等 |
| `~/.local/state/pilcrow/` | log（`pilcrow.log`、`launcher.log`） |
| `~/.local/share/pilcrow/worktrees/` | Pilcrow 建立的 worktree |
| `~/.claude/` | Claude Code 自己的設定與對話（與 CLI 共用） |

## 更新與移除

- **更新**：執行新版的安裝檔即可（會保留 `node_modules`，舊版多出來的檔案會被清掉）
- **移除**：

  ```bash
  ~/pilcrow/scripts/install-desktop.sh --uninstall
  rm -rf ~/pilcrow
  rm -rf ~/.config/pilcrow ~/.local/state/pilcrow   # 連設定一起刪（選用）
  ```

---

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 不知道哪裡有問題 | `pilcrow --doctor` |
| 開了沒有視窗 | 看 `~/.local/state/pilcrow/launcher.log`；試 `pilcrow --chrome` |
| 「Authentication failed」 | API key / token 不對，或 gateway 沒收到；`pilcrow --doctor` 看實際用的 endpoint |
| 「Retrying request … HTTP 5xx」 | gateway 暫時有問題，Claude Code 會自動重試 |
| 終端機面板不能用 | 安裝 `python3` |
| Electron 下載不到 | 用 Chrome 視窗即可，或照上面「事後補裝 Electron」 |
| 畫面空白／GPU 錯誤（VNC、遠端桌面） | `PILCROW_DISABLE_GPU=1 pilcrow` |

## 安全性

Pilcrow 是設計給「一人一台」的開發機：

- 背景程式只聽 `127.0.0.1`，別台電腦連不進來
- 只接受 Pilcrow 自己頁面發出的請求（檢查 Host / Origin / Sec-Fetch-Site，並禁止被別的網頁嵌入），
  所以你瀏覽器裡開的其他網站、或本機其他 dev server，都沒辦法偷偷操作它
- Pilcrow 本身不收集、不上傳任何資料；網路流量只有 Claude Code 連到你設定的 API endpoint
- 請依公司資訊安全規範使用（例如哪些專案可以交給 AI 處理）

> 同一台主機有多個使用者同時登入時（共用伺服器），其他使用者也連得到 `127.0.0.1` 上的服務。
> Pilcrow 沒有為這種情況加上登入驗證，請在自己的機器上使用。

## 修改介面（開發者）

安裝後的資料夾裡有完整的介面原始碼（`web/src`），但沒有建置工具。要修改介面，需要一台 registry 有
Vite 與 rollup 平台套件的機器：

```bash
npm install --include=dev   # 裝建置工具（Vite、React、TypeScript…）
npm run build               # 輸出到 web/dist
```

### 檢查與測試

```bash
npm run check        # TypeScript（tsc --noEmit）
npm run lint         # ESLint：typescript-eslint recommended + react-hooks，禁止 any
npm run test:unit    # vitest：web/src/lib 的純邏輯（transcript 模型、分段、工具摘要、格式化、partial JSON）
npm run test:e2e     # Playwright + headless Chromium：真的開介面、點、捲、打字，對 DOM 與座標做斷言
npm run test:webkit  # WebKitGTK（Linux 外殼用的引擎）：真的畫出來再逐格量像素，沒有 rig 就跳過
npm test             # 以上全部
npm run mock         # 只開假伺服器 http://127.0.0.1:5300/（MOCK_LONG=400 會多一個 400 回合的長對話）
```

瀏覽器測試跑在 `test/mock/mock-server.mjs` 這個假伺服器上：它提供和真伺服器一樣的 RPC，並有一個
會串流文字、用工具、要權限、結束回合的腳本化 Claude，所以不需要 Claude Code 也不需要 API key。
測試涵蓋的是這幾版修過的行為：Normal view 打開只有 root fold、進行中的 fold 與 working mark、
回答留在頁面上而後面的工作另外摺起、archive 不重畫其他列、側欄順序不自己跑、品牌與列對齊、
送出後回到底部、Latest 的出現與消失、打字時對話區零抖動、400 回合一秒內開啟且只畫附近的頁、
Find 畫出全部、切走再切回落在同一處、Control room 的格子不溢出不被切。WebKit 的部分另外確認
Control room 的上緣有畫出來、捲動與串流的每一格都有內容。失敗時截圖在 `test/out/`。

`test:webkit` 需要 `python3-gi`、`gir1.2-webkit2-4.1`、`python3-gi-cairo`，沒有顯示器時用 `xvfb-run`；
找不到就印一行 skipped 並視為通過（CI 要強制時設 `PILCROW_REQUIRE_WEBKIT=1`）。

### 發佈

```bash
npm run release -- 2.9.1 [--skip-tests] [--skip-webkit] [--no-commit] [--trailer "Key: value"]
```

一個指令把工作樹變成三個安裝檔：確認 README 的版本紀錄最上面有這一版的說明 → 改 package.json 與
package-lock.json 的版本 → tsc、eslint、單元測試 → `vite build` → Chromium 與 WebKitGTK 瀏覽器測試 →
`scripts/make-installers.py` 產生 `out/pilcrow-install.sh`、`out/pilcrow-windows.ps1`、`out/pilcrow-app.sh` →
把 Linux 安裝檔解開到暫存資料夾，和 `web/dist`、`server/` 逐檔比對 → `git commit`。任何一步失敗就停在那裡。

---

## Windows

Windows 版跟 Linux 版是同一套程式與介面。安裝檔只有一個：`pilcrow-windows.ps1`（純 ASCII 文字，
裡面內附經過驗證的程式、建置好的介面，以及桌面外殼腳本）。跑一次就把三件事做完：
安裝 Pilcrow（npm 只抓需要的套件）、準備 WebView2 桌面外殼（SDK 從 pip 的 `pywebview` 取出並編譯）、
建立開始功能表與桌面的 **Pilcrow** 捷徑，然後把視窗打開。之後要更新，拿新版的同一個檔案再跑一次。

**需求**

| 項目 | 說明 |
|---|---|
| Windows 10 1809 以上 / Windows 11 | 需要 WebView2 Runtime（Windows 11 與 Edge 都內含） |
| Node.js 20 以上 | 建議 22.12 以上；npm 能連到公司 registry |
| Python 3 + pip | 只用來抓 `pywebview` wheel 裡的 WebView2 SDK；pip 能連到公司 PyPI 鏡像即可 |
| Git for Windows（選用） | 有的話 Claude Code 用 Git Bash 執行指令；沒有的話改用 PowerShell，一樣可以用。Changes 面板與 worktree 需要 git |

**安裝 / 更新**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pilcrow-windows.ps1
```

如果公司政策封鎖 PowerShell 腳本檔，改用這行（直接執行內容，效果一樣）：

```powershell
& ([scriptblock]::Create((Get-Content -Raw .\pilcrow-windows.ps1)))
```

選項：`-Dir <路徑>`（預設 `%LOCALAPPDATA%\Programs\Pilcrow`）、`-NoDesktop`、`-NoLaunch`（裝完不開視窗）、
`-FilesOnly`、`-Uninstall`（移除程式與外殼，設定與對話紀錄保留）、`-Electron`（額外嘗試下載 Electron，通常不需要）；
外殼相關：`-Node`、`-Python`、`-WebView2Package`、`-NuGetSource`、`-WebView2Runtime`、`-KeepServer`、
`-NativeTitleBar`、`-DevTools`、`-SkipSignatureCheck`，意思同 `pilcrow-app.ps1` 的說明。

安裝完會建立開始功能表與桌面的 **Pilcrow** 捷徑，直接指向 `%LOCALAPPDATA%\Pilcrow\host\sdk\PilcrowHost.exe`
（exe 自己會找到或啟動背景伺服器，啟動不經過 PowerShell，也不受執行原則影響），並把 `pilcrow` 指令加到 PATH
（`pilcrow --doctor` 健康檢查、`pilcrow --server` 只開背景程式）。

**和 Linux 版的差別**

- 公司 gateway 一樣寫在 `%USERPROFILE%\.claude\settings.json` 的 `env`
- 設定在 `%APPDATA%\Pilcrow`，狀態與記錄在 `%LOCALAPPDATA%\Pilcrow\state`
- 終端機面板預設是 PowerShell（透過 ConPTY，元件是預先編好的 node-pty，不需要編譯）。
  想改用 `cmd.exe` 或 Git Bash，設定環境變數 `PILCROW_SHELL`（例如 `C:\Program Files\Git\bin\bash.exe`）。
  裝不到 node-pty 或 ConPTY 無法啟動時，自動改用簡易模式：可以下指令，但全螢幕程式不能用
- 新增 session 的資料夾選擇器最上層是「This PC」，列出所有磁碟機
- 介面中文字型使用微軟正黑體

---

## 版本紀錄

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
  支援分頁、Anthropic 與 OpenAI 格式、proxy 與公司 CA）；健康檢查也會列出 gateway 上的模型
- **1.2.1**：安裝檔改為內附建置好的介面，安裝時只裝執行需要的套件，不再需要 Vite / rollup 等建置工具
  （修正內部 registry 沒有 `@rollup/rollup-linux-x64-gnu` 時建置失敗的問題）；
  只內附拉丁字母的字型子集（中文本來就用系統字型）
- **1.2.0**：精修手感與中文使用。回覆逐字平順地出現（公司 gateway 一段一段送來的內容也不會跳動），
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
