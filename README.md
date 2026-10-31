# Pilcrow — Claude Code 桌面版（Linux / Windows）

Pilcrow 是一個跑在 Linux 與 Windows 桌面上的 Claude Code 圖形介面。背後就是真正的 Claude Code
（透過官方 Claude Agent SDK 驅動同一套 harness、工具、權限、CLAUDE.md、skills、MCP），
介面則照 Claude Code Desktop 的工作方式設計：左邊是 session 清單，中間是對話，右邊是
Changes / Files / Terminal / Preview / Tasks / Session 面板。

它沿用你現有的 Claude Code 設定，所以 API endpoint（`ANTHROPIC_BASE_URL` + API key / token）
不用另外設定。

---

## 這個 repo

這裡是 Pilcrow 完整的開發樹，也是安裝檔的來源。根目錄的三個安裝檔是**最新版的發佈產物**，由
`npm run release` 產生，不要手改。

| 路徑 | 內容 |
|---|---|
| `server/` | Node 伺服器：Claude Agent SDK 驅動、session / 歷史 / git / 檔案 / 終端機 / 排程 / 記憶 / 跨 repo 派工（`dispatch_core.js`） |
| `web/src/` | React 介面（Vite + TypeScript）；`web/dist/` 是建置結果，不進 git |
| `electron/`、`bin/pilcrow` | Linux 的 Electron 視窗與啟動器 |
| `qt/pilcrow_qt/` | **原生桌面視窗（PySide6 / Qt Widgets）**：純 CPU 繪圖，沒有 GPU 的機器也快；接同一個伺服器。見 [qt/ARCHITECTURE.md](qt/ARCHITECTURE.md) |
| `scripts/pilcrow-app.sh`、`scripts/pilcrow-app.ps1` | Linux 與 Windows 的桌面外殼。Linux 有 PySide6 時開 Qt 視窗，否則用 WebKitGTK；Windows 用 WebView2，C# 原始碼就寫在 ps1 裡 |
| `scripts/make-installers.py`、`scripts/installer/*.tmpl` | 把工作樹打包成三個安裝檔；模板是安裝檔的固定部分 |
| `scripts/release.mjs` | 改版流程：版本號、檢查、build、打包、驗證、commit、tag |
| `pilcrow-install.sh`、`pilcrow-windows.ps1`、`pilcrow-app.sh` | 發佈產物（見下方「修改介面」與「發佈」） |
| `DISPATCH.md` | 跨 repo 派工（Dispatch）的設計與現況：一份 brief、一組 repo、每個 repo 一個 worktree 裡的 session；先 plan、先跑 canary、一個權限答所有 repo、一次 review、一次收尾 |
| `CHANGELOG.md` | 版本紀錄 |

---

## 需求

| 項目 | 說明 |
|---|---|
| Linux x64 或 arm64 | 有桌面環境（X11 或 Wayland）。沒有桌面也能用 `--server` 模式 |
| Node.js 20 以上 | 建議 22.12 以上（Electron 安裝程式需要） |
| npm 能連到 registry | 自架的 npm mirror 也可以；安裝檔本身不需要連外 |
| git | Changes 面板、worktree、commit 會用到 |
| python3 | 原生 Qt 視窗（加 PySide6）與內建終端機用；沒有的話退回 Electron / Chrome 視窗與 `script` 指令 |
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
ELECTRON_MIRROR=https://你的鏡像/electron/ bash pilcrow-install.sh
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

## Gateway / API 設定

Pilcrow 不碰帳號，它啟動的就是 Claude Code，所以設定方式跟 CLI 一模一樣。建議寫在
`~/.claude/settings.json`：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://你的gateway",
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
`claude`（例如已經裝在系統上的版本），到 **Settings → Claude Code → Claude Code program** 選
*System* 或填路徑。

對話記錄跟 CLI 共用 `~/.claude/projects/`：CLI 開過的對話會出現在 Pilcrow 的清單裡，
Pilcrow 的對話也可以在終端機用 `claude --resume` 接著做。

---

## 桌面視窗：Qt / Electron / Chrome / 瀏覽器

**原生 Qt 視窗（建議）**：`pilcrow` 會先找系統的 `python3` 有沒有 PySide6，有就開 `qt/pilcrow_qt` 這個原生視窗。
它用 Qt Widgets 在 CPU 上直接繪圖，不經過瀏覽器引擎與 GPU 合成，所以在沒有 GPU 的機器、VM 或遠端桌面上
一樣順。安裝檔會用 `pip install --user -r qt/requirements.txt` 從你設定的 PyPI 來源裝 PySide6 等套件
（約 150 MB；`--skip-qt` 可略過）。系統 Python 標示為 externally managed（Debian 12、Ubuntu 23.04 以後）而拒絕 pip 時，安裝檔會自動改在
`~/pilcrow/qt/.venv` 建一個 venv 裝進去，並把它寫進 `.launch-env`，之後從選單或 `pilcrow` 啟動都會用它
（這需要 `python3-venv`）。手動安裝或重試：

```bash
pip3 install --user -r ~/pilcrow/qt/requirements.txt          # 或
python3 -m venv --system-site-packages ~/pilcrow/qt/.venv && ~/pilcrow/qt/.venv/bin/pip install -r ~/pilcrow/qt/requirements.txt
pilcrow --qt            # 強制用 Qt 視窗；pilcrow --electron / --chrome 則是其他視窗
```

Qt 視窗在 Linux 上畫自己的標題列（和主題同色）；要用視窗管理員的標題列：`pilcrow --qt --native-titlebar`
或環境變數 `PILCROW_TITLEBAR=native`。

Qt 視窗的 Preview 面板沒有內嵌瀏覽器，網頁預覽會改用系統瀏覽器開啟；其他面板（Changes、Files 與編輯器、
Terminal、Tasks、History、Info）都是原生實作。介面的文字、快捷鍵與配色和網頁版相同。


Pilcrow 的視窗優先使用 Electron。Electron 的 npm 套件很小，但執行檔（約 100 MB）是另外從
GitHub 下載的；下載不到的話，安裝檔會提示，並自動改用 Chrome/Chromium 的 app 視窗
（獨立視窗、沒有網址列，用起來差不多）。

事後補裝 Electron：

```bash
cd ~/pilcrow
scripts/electron.sh status
scripts/electron.sh download https://你的鏡像/electron/              # 有鏡像時
scripts/electron.sh from-zip ~/Downloads/electron-v44.7.2-linux-x64.zip  # 自己帶 zip 進來時
```

如果 Electron 因為 Chromium sandbox 無法啟動（有些環境關了 user namespace），
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

> 同一台主機有多個使用者同時登入時（共用伺服器），其他使用者也連得到 `127.0.0.1` 上的服務。
> Pilcrow 沒有為這種情況加上登入驗證，請在自己的機器上使用。

## 修改介面（開發者）

這個 repo 就是開發樹。安裝後的資料夾裡也有同一份原始碼（`web/src`、`server/`）但沒有建置工具；
要改介面，在 repo 裡做：

```bash
npm install --include=dev   # 裝建置工具（Vite、React、TypeScript…）
npm run build               # 輸出到 web/dist
npm run dev                 # 邊改邊建（vite build --watch）
npm run server              # 只開伺服器 http://127.0.0.1:47117/
```

### 原生 Qt 視窗（開發者）

```bash
pip install -r qt/requirements.txt pytest pytest-qt
node scripts/extract-icons.mjs        # Lucide 圖示 → qt/pilcrow_qt/assets/icons（已產生並進版）
python scripts/make-qt-fonts.py       # Inter / JetBrains Mono → qt/pilcrow_qt/assets/fonts（已產生並進版）
node test/mock/mock-server.mjs 5300   # 假伺服器
python -m pilcrow_qt --url http://127.0.0.1:5300/     # PYTHONPATH=qt
python -m pytest qt/tests -q          # 純邏輯移植的單元測試 + 離線（offscreen）的視窗測試，接 mock server
python qt/tests/snap.py --scenario session            # 離線截圖到 test/out/qt-*.png
python qt/tests/panel_probe.py                        # 右側面板逐頁截圖 test/out/qt-tab-*.png
python qt/tests/perf.py --turns 400                   # 開啟時間、重畫成本、閒置 CPU
node test/e2e/shots.mjs                               # 網頁版同樣場景的截圖 test/out/web-*.png，拿來對照
```

測試與截圖預設關閉動畫；`PILCROW_MOTION=1` 把彈簧動畫打開跑一遍（送訊息、開面板、展開工具卡都會動），
發佈前兩種都要綠。

沒有 Linux 機器時，`test/linux/` 用 Docker 跑一台 Ubuntu 24.04：真的執行安裝檔（含 PEP 668 的 venv 路徑）、
在 Xvfb 上開 Qt 視窗逐頁操作，截圖與記錄回寫到 `test/out/linux-*`：

```bash
docker build -t pilcrow-linux test/linux
docker run --rm -v pilcrow-home:/home/tester -v "$PWD/out:/in:ro" -v "$PWD:/repo:ro" -v "$PWD/test/out:/out" pilcrow-linux
docker run --rm -e QUICK=1 -v pilcrow-home:/home/tester -v "$PWD/out:/in:ro" -v "$PWD:/repo:ro" -v "$PWD/test/out:/out" pilcrow-linux   # 沿用安裝，跑 repo 的 qt/
```

架構與規範在 [qt/ARCHITECTURE.md](qt/ARCHITECTURE.md)；`web/src` 的 React 元件就是每個 Qt 元件的規格。

### 檢查與測試

```bash
npm run check        # TypeScript（tsc --noEmit）
npm run lint         # ESLint：typescript-eslint recommended + react-hooks，禁止 any
npm run test:unit    # vitest：web/src/lib 的純邏輯（transcript 模型、分段、工具摘要、格式化、partial JSON、workflow）
npm run test:e2e     # Playwright + headless Chromium：真的開介面、點、捲、打字，對 DOM 與座標做斷言
npm run test:webkit  # WebKitGTK（Linux 外殼用的引擎）：真的畫出來再量；沒有 rig 就跳過
npm test             # 以上全部
npm run mock         # 只開假伺服器 http://127.0.0.1:5300/（MOCK_LONG=400 會多一個 400 回合的長對話）
```

瀏覽器測試跑在 `test/mock/mock-server.mjs` 這個假伺服器上：它提供和真伺服器一樣的 RPC（同一個
WebSocket JSON-RPC、同樣的事件），並有一個腳本化的 Claude——依提示裡的字眼決定要串流文字、讀檔、
搜尋、編輯、要權限（`permission`／`run a command`）或慢慢回（`slow`）——所以不需要 Claude Code
也不需要 API key。測試透過同一條 socket 的 `mock.*` 方法控制它（重設、調整串流速度、碰一下 session）。

`test/e2e/run.mjs` 涵蓋的行為：Normal view 打開只有 root fold、進行中的 fold 與 working mark、回答留在
頁面上而後面的工作另外摺起、權限卡（1 允許、Deny 告知 Claude）、archive 不重畫其他列、側欄順序不自己跑、
送出後移到最上、品牌與列圖示對齊、400 回合一秒內開啟且只畫附近的頁並落到底部、Latest 在新內容到達時出現、
點了就消失、從歷史送出會回到底並跟著回覆走、打字時對話相對 composer 零位移、Find 畫出全部並逐一跳、
切走再切回落在同一處、Control room 的格子不溢出不被切、等待中的卡片可以直接批准。
失敗時截圖在 `test/out/`。`--grep 文字` 只跑名稱符合的；`--headed` 看得到視窗。

Playwright 的 Chromium 需要 `npx playwright install chromium`；下載不到時會改用機器上的 Chrome / Edge，
或用 `PILCROW_CHROME=/path/to/chrome` 指定。

`test:webkit`（`test/webkit/run.mjs` + `driver.py`）需要 Linux、`python3-gi`、`gir1.2-webkit2-4.1`、`python3-gi-cairo`，
沒有顯示器時用 `xvfb-run`；找不到就印一行 skipped 並視為通過（CI 要強制時設 `PILCROW_REQUIRE_WEBKIT=1`）。
它檢查 Chromium 代替不了的部分：Control room 的上緣有畫出來、捲動長對話時每一格都有內容、串流時視角跟著回覆。

### 發佈

```bash
npm run release -- 2.9.1 [--skip-tests] [--skip-browser] [--no-commit] [--dry-run]
```

一個指令把工作樹變成三個安裝檔：確認 `CHANGELOG.md` 最上面有這一版的說明 → 改 `package.json` 與
`package-lock.json` 的版本 → `tsc`、`eslint`、單元測試、瀏覽器測試 → `vite build` → `scripts/make-installers.py` 產生
`out/pilcrow-install.sh`、`out/pilcrow-windows.ps1`、`out/pilcrow-app.sh` → 把 Linux 安裝檔解回來，
和工作樹、`web/dist` 逐檔比對 → 複製到 repo 根目錄 → `git commit` 並打 `vX.Y.Z` tag。任何一步失敗就停在那裡。

安裝檔的格式：`scripts/installer/linux.sh.tmpl` 與 `windows.ps1.tmpl` 是固定部分（檢查環境、npm、
Electron、捷徑…），`make-installers.py` 只填入版本號、檔案清單、每個檔案的 heredoc 與 sha256、
介面的 base64 tgz（Linux）或整包 zip（Windows）、以及 Windows 外殼腳本的 base64。輸出是決定性的：
同一棵樹產生的安裝檔逐位元組相同。要改安裝流程本身，改模板。

```bash
python scripts/make-installers.py            # 只打包到 out/（需要先 npm run build）
python scripts/make-installers.py --verify   # 打包後把 Linux 安裝檔解回來逐檔比對
```

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
| Node.js 20 以上 | 建議 22.12 以上；npm 能連到 registry |
| Python 3 + pip | 只用來抓 `pywebview` wheel 裡的 WebView2 SDK；pip 能連到 PyPI（或鏡像）即可 |
| Git for Windows（選用） | 有的話 Claude Code 用 Git Bash 執行指令；沒有的話改用 PowerShell，一樣可以用。Changes 面板與 worktree 需要 git |

**安裝 / 更新**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pilcrow-windows.ps1
```

如果執行原則封鎖 PowerShell 腳本檔，改用這行（直接執行內容，效果一樣）：

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

- Gateway 一樣寫在 `%USERPROFILE%\.claude\settings.json` 的 `env`
- 設定在 `%APPDATA%\Pilcrow`，狀態與記錄在 `%LOCALAPPDATA%\Pilcrow\state`
- 終端機面板預設是 PowerShell（透過 ConPTY，元件是預先編好的 node-pty，不需要編譯）。
  想改用 `cmd.exe` 或 Git Bash，設定環境變數 `PILCROW_SHELL`（例如 `C:\Program Files\Git\bin\bash.exe`）。
  裝不到 node-pty 或 ConPTY 無法啟動時，自動改用簡易模式：可以下指令，但全螢幕程式不能用
- 新增 session 的資料夾選擇器最上層是「This PC」，列出所有磁碟機
- 介面中文字型使用微軟正黑體

---

---

## 版本紀錄

見 [CHANGELOG.md](CHANGELOG.md)。
