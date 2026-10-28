# Pilcrow ¶

**Claude Code 的桌面版圖形介面（Linux / Windows）。**

Pilcrow 背後就是真正的 Claude Code：透過官方 Claude Agent SDK 驅動同一套 harness、工具、權限、
`CLAUDE.md`、skills 與 MCP。介面照 Claude Code Desktop 的工作方式設計：左邊是 session 清單，
中間是對話，右邊是 Changes / Files / Terminal / Preview / Tasks / Session 面板。
它沿用你現有的 `~/.claude` 設定，所以公司 gateway（`ANTHROPIC_BASE_URL` + API key / token）不用另外設定。

目前版本：**2.9.0**（見 [CHANGELOG.md](CHANGELOG.md)）。

---

## 這個 repo 放什麼

Pilcrow 以「單一純文字安裝檔」發佈：伺服器原始碼直接寫在安裝檔裡，建置好的前端以 base64 內附，
安裝時只用 `npm install --omit=dev` 從你設定的 registry 抓執行需要的套件，機器上不需要任何建置工具。
這個 repo 收的就是這些發佈產物，以及兩個原生桌面外殼：

| 路徑 | 內容 |
|---|---|
| `pilcrow-install.sh` | **Linux 安裝檔**。寫出程式、介面與啟動器，建立應用程式選單項目與 `~/.local/bin/pilcrow` |
| `pilcrow-windows.ps1` | **Windows 安裝檔**。裝 Pilcrow、準備 WebView2 桌面外殼、建立開始功能表與桌面捷徑，然後開視窗。重跑同一個檔案即可更新 |
| `pilcrow-app.sh` | Linux 桌面外殼：用 WebKitGTK（找不到時退回 PyQt6 WebEngine）把 Pilcrow 包成 GNOME 風格的原生視窗 |
| `host/pilcrow-app.ps1` | Windows 桌面外殼腳本：取得 WebView2 SDK、編譯 `PilcrowHost.exe`、建立捷徑 |
| `host/sdk/` | Windows 外殼本體：`PilcrowHost.cs` 原始碼、編好的 `PilcrowHost.exe`、WebView2 SDK 的三個 DLL、`launch.json`。詳見 [host/README.md](host/README.md) |
| `docs/user-guide.md` | 完整使用手冊（安裝後也會寫到 `~/pilcrow/README.md`） |
| `CHANGELOG.md` | 版本紀錄 |

執行期資料不在 repo 裡（已用 `.gitignore` 排除）：瀏覽器 profile（`data/`、`host/webview2/`）、
log、視窗狀態。這些含有登入 session，請不要加進來。

---

## 安裝

### Linux

需求：Linux x64 / arm64 有桌面環境、Node.js 20 以上（建議 22.12+）、npm 能連到 registry；
git 與 python3 建議安裝。

```bash
bash pilcrow-install.sh
```

```bash
bash pilcrow-install.sh --help
```

常用選項：`--dir PATH`（預設 `~/pilcrow`）、`--files-only`、`--skip-electron`（改用 Chrome 視窗）、
`--no-desktop`；`ELECTRON_MIRROR=URL` 指定 Electron 下載鏡像。

> 請先把安裝檔**存成檔案**再用 bash 執行，不要直接貼進 shell 提示字元。
> 安裝檔每寫完一個檔案都會做 sha256 驗證，貼壞了會直接指出是哪個檔案。

裝好後從應用程式選單開 **Pilcrow**，或在終端機執行 `pilcrow`。
想要沒有網址列與瀏覽器快捷鍵的原生視窗，再跑一次：

```bash
bash pilcrow-app.sh --install
```

### Windows

需求：Windows 10 1809 以上、Node.js 20 以上、Python 3 + pip（只用來取 WebView2 SDK）、
WebView2 Runtime（Windows 11 與 Edge 內含）；Git for Windows 建議安裝。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pilcrow-windows.ps1
```

公司政策封鎖腳本檔時，改成直接執行內容：

```powershell
& ([scriptblock]::Create((Get-Content -Raw .\pilcrow-windows.ps1)))
```

常用選項：`-Dir <path>`（預設 `%LOCALAPPDATA%\Programs\Pilcrow`）、`-NoDesktop`、`-NoLaunch`、
`-FilesOnly`、`-Uninstall`（設定與對話保留）。外殼相關選項（`-Node`、`-Python`、`-WebView2Package`、
`-NuGetSource`、`-WebView2Runtime`、`-KeepServer`、`-NativeTitleBar`、`-DevTools`、`-SkipSignatureCheck`）
意思同 `host/pilcrow-app.ps1` 檔頭說明。

裝完會建立開始功能表與桌面的 **Pilcrow** 捷徑，直接指向 `%LOCALAPPDATA%\Pilcrow\host\sdk\PilcrowHost.exe`，
並把 `pilcrow` 指令加到 PATH（`pilcrow --doctor` 健康檢查、`pilcrow --server` 只開背景程式）。

---

## 功能一覽

- **Sessions**：依專案分組、置頂、封存；*Needs you* 區塊集中顯示等你回覆的 session；多個 session 同時跑
- **Worktree**：開新 session 時勾選，就在獨立的 git worktree / branch 裡工作
- **權限模式與確認**：Ask / Edit automatically / Plan / Auto / Bypass；工具卡片上直接 Allow / Always / Deny
- **Diff 與 Changes 面板**：逐字差異、unified / side-by-side、在行上留評論再送回給 Claude、commit、開 PR
- **時光機（Timeline）**：每個改檔步驟留快照，可看每一步改了什麼、整個資料夾或單一檔案還原
- **控制室（Control room）**：所有 session 一頁看完，等你的直接在這裡批准；今日花費與耗時
- **排程 + 收件匣**：把提示排在某個資料夾定時跑，結果進收件匣，可一鍵接手
- **專案記憶**：每輪結束後把決定、死路、慣例蒸餾成筆記，新 session 自動帶著開始
- **Files / Terminal / Preview / Tasks**：檔案樹與 CodeMirror 編輯器、多終端機、localhost 預覽、背景工作
- **Skills 與 MCP servers 管理**：讀寫 Claude Code 自己的設定，格式與 CLI 一致
- **Workflows**：多代理流程以流程卡呈現，每個代理有狀態、耗時與結果預覽

完整說明（快捷鍵、gateway 設定、遠端 SSH 用法、檔案位置、疑難排解）見 [docs/user-guide.md](docs/user-guide.md)。

---

## 安全性

Pilcrow 是設計給「一人一台」的開發機：

- 背景程式只聽 `127.0.0.1`，別台電腦連不進來
- 只接受 Pilcrow 自己頁面發出的請求（檢查 Host / Origin / Sec-Fetch-Site，並禁止被別的網頁嵌入）
- Pilcrow 本身不收集、不上傳任何資料；網路流量只有 Claude Code 連到你設定的 API endpoint

> 共用伺服器上其他登入使用者也連得到 `127.0.0.1`。Pilcrow 沒有為這種情況加登入驗證，請在自己的機器上使用。

---

## 開發

前端與伺服器的原始碼樹不在這個 repo，這裡只有發佈出來的安裝檔。
要改介面或重新打包，請參考 [docs/user-guide.md](docs/user-guide.md) 的「修改介面（開發者）」與「發佈」兩節。

Windows 外殼（`host/sdk/PilcrowHost.cs`）可以直接改；`host/pilcrow-app.ps1` 會用系統內建的 C# 編譯器重新編譯。

---

## 授權

[MIT](LICENSE)。內附的 WebView2 SDK DLL 屬 Microsoft，依其自身條款散佈。
