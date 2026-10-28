# Pilcrow Desktop Host for Windows

以 Windows 內建的 WebView2（Edge 的系統元件）承載 Pilcrow 介面，並拿掉系統標題列，
改由外殼在頁面頂端畫自己的標題列（圖示、標題、— ☐ ✕），像 VS Code / Claude 桌面版那樣。
沒有瀏覽器標題列、沒有分頁、沒有網址列、沒有翻譯提示、沒有 Chrome 的快捷鍵。

`pilcrow-windows.ps1` 安裝時會自動處理這一切；這裡的檔案是給想單獨重裝外殼、或要改外殼的人看的。

## 檔案

| 檔案 | 說明 |
|---|---|
| `pilcrow-app.ps1` | 外殼腳本。取得 WebView2 SDK（從 pip 的 `pywebview` wheel 抽出三個 DLL，或用 `-WebView2Package` / `-NuGetSource` 指定）、驗證 Microsoft 簽章、用系統內建的 C# 編譯器把 `PilcrowHost.cs` 編成 `PilcrowHost.exe`、寫 `launch.json`、建立捷徑 |
| `sdk/PilcrowHost.cs` | 外殼本體（WinForms + WebView2）。自訂標題列、DPI 感知、單一實例、找到或啟動背景伺服器、關視窗時依 `keepServer` 決定是否關伺服器 |
| `sdk/PilcrowHost.exe` | 編好的外殼。捷徑直接指向它，啟動不經過 PowerShell，也不受執行原則影響 |
| `sdk/PilcrowHost.hash` | 編譯時的原始碼 hash，原始碼變了才重新編譯 |
| `sdk/PilcrowHost.manifest` | PerMonitorV2 DPI、長路徑支援、asInvoker |
| `sdk/Microsoft.Web.WebView2.Core.dll`<br>`sdk/Microsoft.Web.WebView2.WinForms.dll`<br>`sdk/WebView2Loader.dll` | WebView2 SDK（1.0.4258.31） |
| `sdk/launch.json` | 外殼啟動時讀的設定：node 路徑、`server/main.js`、state / userdata / log 位置、`devtools`、`keepServer`、`nativeTitleBar`。**由安裝腳本依本機路徑產生**，repo 裡這份是範例 |

執行期產生、不在 repo 裡：`webview2/`（WebView2 使用者資料，含登入 session）、`window.json`（視窗位置）、
`host.json`（單一實例鎖）、`host.log`。

## 單獨安裝 / 重裝外殼

前提：已用 `pilcrow-windows.ps1` 裝好 Pilcrow、有 Python 3 + pip。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pilcrow-app.ps1 -Install
```

選項：

| 選項 | 說明 |
|---|---|
| `-Dir <path>` | Pilcrow 安裝位置（預設 `%LOCALAPPDATA%\Programs\Pilcrow`） |
| `-Node <path>` / `-Python <path>` | 指定 node.exe / python.exe（預設從 PATH 找） |
| `-WebView2Package <p>` | 不用 pip 時：SDK 的 .nupkg / .whl / .zip / 資料夾 / 網址 |
| `-NuGetSource <url>` + `-WebView2Version` | 不用 pip 時：NuGet v3 flat container 根網址 |
| `-WebView2Runtime <dir>` | Fixed Version 的 WebView2 Runtime 資料夾（沒裝 Evergreen 時） |
| `-Install` / `-NoLaunch` / `-NoDesktop` | 建立捷徑、只建不啟動、不建桌面捷徑 |
| `-Uninstall` | 移除外殼的捷徑與快取（Pilcrow 本體不動） |
| `-KeepServer` | 關視窗後讓 Pilcrow 伺服器留在背景 |
| `-NativeTitleBar` | 改用 Windows 原生標題列 |
| `-DevTools` | 允許 F12 與瀏覽器快捷鍵（除錯用） |
| `-SkipSignatureCheck` | 不驗證 DLL 的 Microsoft 簽章（離線環境驗章失敗時才用） |

腳本一律在 Windows PowerShell 5.1 + STA 裡執行（WinForms 與 WebView2 需要），從 pwsh 7 啟動會自動轉交。

## 改外殼

改 `sdk/PilcrowHost.cs` 後重跑 `pilcrow-app.ps1 -Install`，hash 不同就會重新編譯。
