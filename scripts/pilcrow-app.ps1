# ============================================================================
#  Pilcrow Desktop Host for Windows  (pilcrow-app.ps1)
#
#  以 Windows 內建的 WebView2（Edge 的系統元件）承載 Pilcrow 介面，並拿掉系統標題列，
#  改由外殼在頁面頂端畫一條自己的標題列（圖示、標題、— ☐ ✕），像 VS Code / Claude 桌面版那樣。
#  沒有瀏覽器標題列、沒有分頁、沒有網址列、沒有翻譯提示、沒有 Chrome 的快捷鍵。
#
#  前提：已用 pilcrow-install-windows.ps1 裝好 Pilcrow（Electron 有沒有裝都無所謂）、有 Python 3 + pip。
#  WebView2 SDK 的三個 DLL 從 pip 的 pywebview 套件裡取出（pip download pywebview），不需要 NuGet。
#
#  第一次（建立開始功能表 / 桌面捷徑，之後點捷徑即可）：
#    powershell -NoProfile -ExecutionPolicy Bypass -File .\pilcrow-app.ps1 -Install
#
#  選項：
#    -Dir <path>            Pilcrow 安裝位置（預設 %LOCALAPPDATA%\Programs\Pilcrow）
#    -Node <path>           node.exe（預設從 PATH 找）
#    -Python <path>         python.exe（預設從 PATH / py 啟動器找）
#    -WebView2Package <p>   不用 pip 時：WebView2 SDK 的 .nupkg / .whl / .zip / 資料夾 / 網址
#    -NuGetSource <url>     不用 pip 時：NuGet v3 flat container 根網址（搭配 -WebView2Version）
#    -WebView2Runtime <dir> Fixed Version 的 WebView2 Runtime 資料夾（沒裝 Evergreen 時用）
#    -Install               建立捷徑（直接指向 PilcrowHost.exe）、把本檔複製到 %LOCALAPPDATA%\Pilcrow\host，然後啟動
#    -NoLaunch              搭配 -Install：只建立捷徑，不啟動
#    -NoDesktop             -Install 時不建桌面捷徑
#    -Uninstall             移除本外殼的捷徑與快取（Pilcrow 本體不動）
#    -KeepServer            關視窗後讓 Pilcrow 伺服器留在背景
#    -NativeTitleBar        改用 Windows 原生標題列（不注入自訂標題列）
#    -DevTools              允許 F12 / 瀏覽器快捷鍵（除錯用）
#    -SkipSignatureCheck    不驗證 WebView2 DLL 的 Microsoft 數位簽章（離線環境驗章失敗時才用）
# ============================================================================
[CmdletBinding()]
param(
  [string]$Dir = '',
  [string]$Node = '',
  [string]$Python = '',
  [string]$WebView2Package = '',
  [string]$NuGetSource = '',
  [string]$WebView2Version = '1.0.4258.31',
  [string]$WebView2Runtime = '',
  [switch]$Install,
  [switch]$NoLaunch,
  [switch]$NoDesktop,
  [switch]$Uninstall,
  [switch]$KeepServer,
  [switch]$NativeTitleBar,
  [switch]$DevTools,
  [switch]$SkipSignatureCheck
)

$ErrorActionPreference = 'Stop'
$AppId = 'Pilcrow.Desktop'

# ---------------------------------------------------------------- 一定要在 Windows PowerShell 5.1 + STA 裡跑
# WinForms 與 WebView2 需要 STA；pwsh 7 上 Add-Type 參考 WinForms 很麻煩，所以一律交給內建的 5.1。
$needRelaunch = ($PSVersionTable.PSEdition -ne 'Desktop') -or ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA')
if ($needRelaunch) {
  $ps51 = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  if (-not (Test-Path $ps51)) { throw "找不到 Windows PowerShell 5.1（powershell.exe）" }
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', ('"' + $PSCommandPath + '"'))
  foreach ($k in $PSBoundParameters.Keys) {
    $v = $PSBoundParameters[$k]
    if ($v -is [switch]) { if ($v.IsPresent) { $argv += "-$k" } }
    else { $argv += "-$k"; $argv += ('"' + [string]$v + '"') }
  }
  $child = Start-Process -FilePath $ps51 -ArgumentList $argv -Wait -PassThru
  exit $child.ExitCode
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

# ---------------------------------------------------------------- Win32 小工具（不依賴 SDK，先編）
$preCs = @'
using System;
using System.Runtime.InteropServices;
namespace PilcrowPre {
  public static class Native {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern IntPtr LoadLibraryW(string path);
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
    [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int value);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    public static bool ConsoleVisible() {
      try { IntPtr h = GetConsoleWindow(); return h != IntPtr.Zero && IsWindowVisible(h); } catch { return false; }
    }
    public static void EnableDpi() {
      try { if (SetProcessDpiAwarenessContext((IntPtr)(-4))) return; } catch { }
      try { if (SetProcessDpiAwareness(2) == 0) return; } catch { }
      try { SetProcessDPIAware(); } catch { }
    }
    public static bool Activate(long handle) {
      IntPtr h = (IntPtr)handle;
      if (!IsWindow(h)) return false;
      if (IsIconic(h)) ShowWindow(h, 9);
      return SetForegroundWindow(h);
    }
  }
  [StructLayout(LayoutKind.Sequential)] public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Sequential)] public struct PROPVARIANT { public ushort vt; public ushort r1; public ushort r2; public ushort r3; public IntPtr p; public IntPtr p2; }
  [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    void GetCount(out uint count);
    void GetAt(uint index, out PROPERTYKEY key);
    void GetValue(ref PROPERTYKEY key, out PROPVARIANT value);
    void SetValue(ref PROPERTYKEY key, ref PROPVARIANT value);
    void Commit();
  }
  public static class ShellLink {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern IPropertyStore SHGetPropertyStoreFromParsingName(string path, IntPtr bind, int flags, ref Guid riid);
    [DllImport("ole32.dll")] static extern int PropVariantClear(ref PROPVARIANT v);
    // 讓捷徑帶 AppUserModelID：工作列才會把視窗歸到自己的圖示下，釘選也是釘 Pilcrow 而不是 PowerShell。
    public static void SetAppId(string lnk, string id) {
      Guid iid = typeof(IPropertyStore).GUID;
      IPropertyStore ps = SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid);
      PROPERTYKEY key = new PROPERTYKEY();
      key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
      key.pid = 5;
      PROPVARIANT v = new PROPVARIANT();
      v.vt = 31;
      v.p = Marshal.StringToCoTaskMemUni(id);
      try { ps.SetValue(ref key, ref v); ps.Commit(); }
      finally { PropVariantClear(ref v); Marshal.ReleaseComObject(ps); }
    }
  }
}
'@
if (-not ('PilcrowPre.Native' -as [type])) { Add-Type -TypeDefinition $preCs }

# ---------------------------------------------------------------- 路徑
if (-not $Dir) { $Dir = $env:PILCROW_DIR }
if (-not $Dir) { $Dir = Join-Path $env:LOCALAPPDATA 'Programs\Pilcrow' }
$Dir = [IO.Path]::GetFullPath($Dir)
$StateDir = Join-Path $env:LOCALAPPDATA 'Pilcrow\state'      # Pilcrow 伺服器自己的狀態（instance.json、pilcrow.log）
$HostDir  = Join-Path $env:LOCALAPPDATA 'Pilcrow\host'       # 本外殼的東西（SDK、WebView2 資料、視窗位置）
$SdkDir   = Join-Path $HostDir 'sdk'
$LogFile  = Join-Path $HostDir 'host.log'
$IconPath = Join-Path $Dir 'assets\icon.ico'
$Programs = [Environment]::GetFolderPath('Programs')
$Desktop  = [Environment]::GetFolderPath('Desktop')
$StartMenuLink = Join-Path $Programs 'Pilcrow.lnk'
$DesktopLink   = Join-Path $Desktop 'Pilcrow.lnk'
$Interactive = [PilcrowPre.Native]::ConsoleVisible()   # 從捷徑（-WindowStyle Hidden）啟動時沒有可見主控台，錯誤改用對話框
if (-not $WebView2Runtime) { $WebView2Runtime = $env:PILCROW_WEBVIEW2_RUNTIME }

New-Item -ItemType Directory -Force -Path $HostDir | Out-Null

function Log([string]$Text) {
  $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $Text
  try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
  if ($Interactive) { Write-Host $line }
}
function Fail([string]$Text) {
  Log ('ERROR  ' + $Text)
  if ($Interactive) { Write-Host $Text -ForegroundColor Red }
  else { [void][System.Windows.Forms.MessageBox]::Show($Text, 'Pilcrow', 'OK', 'Error') }
  exit 1
}
trap { Fail ("發生錯誤：" + $_.Exception.Message + "`n`n" + $_.ScriptStackTrace) }

# 執行外部程式：拿回輸出與結束碼，不讓 stderr 變成 PowerShell 錯誤
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = (& $Exe @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $out.Trim() }
  } catch {
    return [pscustomobject]@{ Code = 1; Output = $_.Exception.Message }
  } finally { $ErrorActionPreference = $old }
}

# ---------------------------------------------------------------- 移除
if ($Uninstall) {
  foreach ($l in @($StartMenuLink, $DesktopLink)) { if (Test-Path $l) { Remove-Item -Force $l } }
  if (Test-Path $HostDir) { Remove-Item -Recurse -Force $HostDir }
  Write-Host "已移除 Pilcrow 桌面外殼的捷徑與快取。Pilcrow 本體（$Dir）沒有動；重新執行原本的安裝檔可以恢復原來的捷徑。"
  exit 0
}

# ---------------------------------------------------------------- 檢查 Pilcrow 與 Node
$MainJs = Join-Path $Dir 'server\main.js'
if (-not (Test-Path $MainJs)) { Fail "在 $Dir 找不到 Pilcrow（server\main.js）。請先執行 pilcrow-install-windows.ps1，或用 -Dir 指定安裝位置。" }

function Find-Node {
  if ($Node) { return $Node }
  if ($env:PILCROW_NODE -and (Test-Path $env:PILCROW_NODE)) { return $env:PILCROW_NODE }
  $c = Get-Command node.exe -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @((Join-Path $env:ProgramFiles 'nodejs\node.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'))) { if (Test-Path $p) { return $p } }
  return $null
}
$NodeExe = Find-Node
if (-not $NodeExe) { Fail "找不到 node.exe。請安裝 Node.js 20 以上，或用 -Node 指定路徑。" }
$nodeDir = Split-Path $NodeExe -Parent
if (($env:Path -split ';') -notcontains $nodeDir) { $env:Path = $nodeDir + ';' + $env:Path }

# ---------------------------------------------------------------- WebView2 SDK（三個檔：Core.dll、WinForms.dll、WebView2Loader.dll）
$Arch = switch ($env:PROCESSOR_ARCHITECTURE) { 'ARM64' { 'arm64' } 'x86' { 'x86' } default { 'x64' } }
$CoreDll     = Join-Path $SdkDir 'Microsoft.Web.WebView2.Core.dll'
$WinFormsDll = Join-Path $SdkDir 'Microsoft.Web.WebView2.WinForms.dll'
$LoaderDll   = Join-Path $SdkDir 'WebView2Loader.dll'

function Test-Sdk { return (Test-Path $CoreDll) -and (Test-Path $WinFormsDll) -and (Test-Path $LoaderDll) }

function Import-SdkFrom([string]$Source) {
  # $Source：.nupkg / .whl / .zip / 已解壓的資料夾 / http(s) 網址
  $tmp = Join-Path $HostDir ('sdk-tmp-' + [guid]::NewGuid().ToString('n'))
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  try {
    $root = $Source
    if ($Source -match '^https?://') {
      $zip = Join-Path $tmp 'pkg.zip'
      Log "下載 WebView2 SDK：$Source"
      Invoke-WebRequest -Uri $Source -OutFile $zip -UseBasicParsing -TimeoutSec 60
      $root = $tmp
      Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
    } elseif (Test-Path $Source -PathType Leaf) {
      $zip = Join-Path $tmp 'pkg.zip'           # Expand-Archive 只認 .zip 副檔名；.nupkg / .whl 其實就是 zip
      Copy-Item $Source $zip
      $root = $tmp
      Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
    } elseif (-not (Test-Path $Source -PathType Container)) {
      throw "找不到 $Source"
    }
    $core = Get-ChildItem -Path $root -Recurse -Filter 'Microsoft.Web.WebView2.Core.dll' | Sort-Object { if ($_.FullName -match '\\net4\d+\\') { 0 } elseif ($_.FullName -match '\\netcoreapp|\\net\d\.\d') { 2 } else { 1 } } | Select-Object -First 1
    $wf   = Get-ChildItem -Path $root -Recurse -Filter 'Microsoft.Web.WebView2.WinForms.dll' | Sort-Object { if ($_.FullName -match '\\net4\d+\\') { 0 } elseif ($_.FullName -match '\\netcoreapp|\\net\d\.\d') { 2 } else { 1 } } | Select-Object -First 1
    $ld   = Get-ChildItem -Path $root -Recurse -Filter 'WebView2Loader.dll' | Where-Object { $_.FullName -match ('\\win-' + $Arch + '\\') -or $_.FullName -match ('\\' + $Arch + '\\') } | Select-Object -First 1
    if (-not $ld) { $ld = Get-ChildItem -Path $root -Recurse -Filter 'WebView2Loader.dll' | Select-Object -First 1 }
    if (-not ($core -and $wf -and $ld)) { throw "在 $Source 裡找不到完整的 WebView2 SDK（需要 Microsoft.Web.WebView2.Core.dll、Microsoft.Web.WebView2.WinForms.dll、win-$Arch 的 WebView2Loader.dll）" }
    New-Item -ItemType Directory -Force -Path $SdkDir | Out-Null
    Copy-Item $core.FullName $CoreDll -Force
    Copy-Item $wf.FullName $WinFormsDll -Force
    Copy-Item $ld.FullName $LoaderDll -Force
    Log "WebView2 SDK 就緒：$SdkDir（來源 $($core.FullName)）"
  } finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  }
}

function Find-Python {
  # 回傳 @(exe, 前置參數)；跳過 Microsoft Store 的假 python.exe
  $cands = @()
  if ($Python) { $cands += ,@($Python, @()) }
  if ($env:PILCROW_PYTHON) { $cands += ,@($env:PILCROW_PYTHON, @()) }
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) { $cands += ,@($py.Source, @('-3')) }
  foreach ($n in @('python.exe', 'python3.exe')) {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notlike '*\WindowsApps\*') { $cands += ,@($c.Source, @()) }
  }
  foreach ($c in $cands) {
    $r = Invoke-Native $c[0] ([string[]]($c[1] + @('-c', 'import sys;print(sys.version_info[0])')))
    if ($r.Code -eq 0 -and $r.Output -eq '3') { return $c }
  }
  return $null
}

function Import-SdkFromPip {
  $py = Find-Python
  if (-not $py) { throw "找不到 Python 3。" }
  $exe = $py[0]; $pre = [string[]]$py[1]
  # 已經裝了 pywebview 就直接拿它的 lib 資料夾
  $r = Invoke-Native $exe ([string[]]($pre + @('-c', 'import webview,os;print(os.path.join(os.path.dirname(webview.__file__),''lib''))')))
  if ($r.Code -eq 0 -and $r.Output -and (Test-Path (Join-Path $r.Output 'Microsoft.Web.WebView2.Core.dll'))) {
    Log "從已安裝的 pywebview 取得 WebView2 SDK：$($r.Output)"
    Import-SdkFrom $r.Output
    return
  }
  $tmp = Join-Path $HostDir 'pip-tmp'
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  try {
    Log "pip download pywebview（只為了取出 WebView2 的 DLL，用 $exe）…"
    $r = Invoke-Native $exe ([string[]]($pre + @('-m', 'pip', 'download', 'pywebview', '--no-deps', '-d', $tmp, '-q', '--disable-pip-version-check')))
    if ($r.Output) { Log $r.Output }
    $whl = Get-ChildItem -Path $tmp -Filter 'pywebview-*.whl' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $whl) { throw "pip download pywebview 失敗（結束碼 $($r.Code)）。$($r.Output)" }
    Import-SdkFrom $whl.FullName
  } finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
}

if (-not (Test-Sdk)) {
  $errors = @()
  if ($WebView2Package) {
    Import-SdkFrom $WebView2Package
  } else {
    try { Import-SdkFromPip } catch { $errors += "pip：$($_.Exception.Message)" }
    if (-not (Test-Sdk) -and $NuGetSource) {
      $url = $NuGetSource.TrimEnd('/') + "/microsoft.web.webview2/$WebView2Version/microsoft.web.webview2.$WebView2Version.nupkg"
      try { Import-SdkFrom $url } catch { $errors += "NuGet：$($_.Exception.Message)" }
    }
  }
  if (-not (Test-Sdk)) {
    Fail ("拿不到 WebView2 SDK。`n`n" + ($errors -join "`n") + "`n`n" +
          "可以的做法：`n" +
          "  1. 這台裝 Python 3 + pip（內網鏡像能裝 pywebview 即可），再跑一次；`n" +
          "  2. 任一台能 pip 的機器執行 pip download pywebview --no-deps，把 .whl 帶來：-WebView2Package <路徑>；`n" +
          "  3. 有內部 NuGet 鏡像：-NuGetSource <v3 flat container 根網址>。")
  }
}
$SdkVersion = ''
try { $SdkVersion = [Reflection.AssemblyName]::GetAssemblyName($CoreDll).Version.ToString() } catch { }

# 三個 DLL 必須是 Microsoft 簽章的原版（pip 鏡像被動過手腳也擋得住）
if (-not $SkipSignatureCheck) {
  foreach ($dll in @($CoreDll, $WinFormsDll, $LoaderDll)) {
    $sig = Get-AuthenticodeSignature -FilePath $dll
    $subject = ''
    if ($sig.SignerCertificate) { $subject = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne 'Valid' -or $subject -notmatch 'O=Microsoft Corporation') {
      Fail ("WebView2 DLL 的數位簽章不是有效的 Microsoft 簽章：`n$dll`n狀態：$($sig.Status)，簽署者：$subject`n`n" +
            "這通常代表檔案被改過或下載不完整；請刪除 $SdkDir 重新取得。確認來源沒問題但離線驗章失敗時，可加 -SkipSignatureCheck。")
    }
  }
  Log "WebView2 DLL Microsoft 簽章驗證通過"
}

# 載入 SDK：先把原生 loader 載進程序，Core.dll 的 DllImport 就會直接命中；再登記 AssemblyResolve 以防 CLR 找不到。
[void][PilcrowPre.Native]::LoadLibraryW($LoaderDll)
$env:Path = $SdkDir + ';' + $env:Path
$script:SdkDirForResolve = $SdkDir
[AppDomain]::CurrentDomain.add_AssemblyResolve([ResolveEventHandler]{
  param($sender, $e)
  $name = $e.Name.Split(',')[0]
  $p = Join-Path $script:SdkDirForResolve ($name + '.dll')
  if (Test-Path $p) { return [Reflection.Assembly]::LoadFrom($p) }
  return $null
})
Add-Type -Path $CoreDll
Add-Type -Path $WinFormsDll

# ---------------------------------------------------------------- WebView2 Runtime
$rtFolder = $null
if ($WebView2Runtime) { $rtFolder = $WebView2Runtime }
try {
  $rtVer = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString($rtFolder)
  Log "WebView2 Runtime $rtVer，SDK $SdkVersion"
} catch {
  Fail ("這台電腦沒有 Microsoft Edge WebView2 Runtime（Windows 11 內建；Windows 10 多半已隨 Edge/Office 安裝）。`n`n" +
        "請安裝「WebView2 Runtime」的離線（Standalone）安裝檔，或把 Fixed Version 版本解壓成資料夾後用 -WebView2Runtime <資料夾> 指定。`n" +
        "（$($_.Exception.Message)）")
}

# ---------------------------------------------------------------- 安裝捷徑
function New-AppShortcut([string]$Path, [string]$ExePath) {
  # 捷徑直接指向 PilcrowHost.exe：exe 自己會找或啟動伺服器，不經過 PowerShell。
  $sh = New-Object -ComObject WScript.Shell
  $lnk = $sh.CreateShortcut($Path)
  $lnk.TargetPath = $ExePath
  $lnk.Arguments = ''
  $lnk.WorkingDirectory = (Split-Path $ExePath)
  $lnk.IconLocation = $ExePath + ',0'
  $lnk.Description = 'Pilcrow - Claude Code desktop'
  $lnk.WindowStyle = 1
  $lnk.Save()
  try { [PilcrowPre.ShellLink]::SetAppId($Path, $AppId) } catch { Log "捷徑 AppUserModelID 設定失敗（不影響使用）：$($_.Exception.Message)" }
}

$SkipLaunch = $false
if ($Install) {
  $hostScript = Join-Path $HostDir 'pilcrow-app.ps1'
  if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($hostScript)) { Copy-Item $PSCommandPath $hostScript -Force }
  if ($NoLaunch) { $SkipLaunch = $true }
}

# ---------------------------------------------------------------- 單一實例：已經開著就把那個視窗叫到前面
$HostLock = Join-Path $HostDir 'host.json'
if ((-not $SkipLaunch) -and (Test-Path $HostLock)) {
  try {
    $h = Get-Content -Raw $HostLock | ConvertFrom-Json
    if ($h.pid -and (Get-Process -Id $h.pid -ErrorAction SilentlyContinue) -and $h.hwnd) {
      if ([PilcrowPre.Native]::Activate([long]$h.hwnd)) { Log "Pilcrow 視窗已經開著（pid $($h.pid)），切換過去"; exit 0 }
    }
  } catch { }
}

# ---------------------------------------------------------------- Pilcrow 伺服器：已在跑就直接用，否則隱藏啟動一個
function Get-Json([string]$Url) {
  # 用 WebClient 而不是 Invoke-RestMethod：proxy / PAC 設定不一定會略過 127.0.0.1
  $wc = New-Object System.Net.WebClient
  $wc.Proxy = $null
  try { return ($wc.DownloadString($Url) | ConvertFrom-Json) } finally { $wc.Dispose() }
}

function Get-PilcrowServer {
  $lock = Join-Path $StateDir 'instance.json'
  if (-not (Test-Path $lock)) { return $null }
  try {
    $info = Get-Content -Raw $lock | ConvertFrom-Json
    if (-not ($info.pid -and $info.port)) { return $null }
    if (-not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) { return $null }
    $hello = Get-Json "http://127.0.0.1:$($info.port)/api/hello"
    if ($hello.app -eq 'pilcrow' -and $hello.pid -eq $info.pid) {
      return [pscustomobject]@{ pid = [int]$info.pid; port = [int]$info.port; url = "http://127.0.0.1:$($info.port)/"; version = $hello.version; busy = $hello.busy }
    }
  } catch { }
  return $null
}

$server = $null
$spawned = $false
$serverProc = $null
if (-not $SkipLaunch) {
$server = Get-PilcrowServer
# 升級後舊版伺服器可能還在背景：沒在忙就換掉它，視窗才會拿到新版程式
$pkgVersion = ''
try { $pkgVersion = [string](Get-Content -Raw (Join-Path $Dir 'package.json') | ConvertFrom-Json).version } catch { }
if ($server -and $pkgVersion -and $server.version -ne $pkgVersion) {
  if (-not $server.busy -or $server.busy -eq 0) {
    Log "背景的 Pilcrow $($server.version) 比安裝的 $pkgVersion 舊，先關掉它"
    try { Stop-Process -Id $server.pid -Force -ErrorAction Stop; Start-Sleep -Milliseconds 800 } catch { }
    $server = Get-PilcrowServer
  } else {
    Log "背景的 Pilcrow $($server.version) 還有 $($server.busy) 個 session 在忙，暫時沿用（關閉所有視窗再開就會換成 $pkgVersion）"
  }
}
if ($server) {
  Log "沿用已在執行的 Pilcrow $($server.version)（pid $($server.pid)，$($server.url)）"
} else {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $NodeExe
  $psi.Arguments = '"' + $MainJs + '" --shell=none'
  $psi.WorkingDirectory = $Dir
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $serverProc = [System.Diagnostics.Process]::Start($psi)
  $spawned = $true
  Log "啟動 Pilcrow 伺服器（node pid $($serverProc.Id)）"
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    if ($serverProc.HasExited) { break }
    $server = Get-PilcrowServer
    if ($server -and $server.pid -eq $serverProc.Id) { break }
    $server = $null
  }
  if (-not $server) {
    $tail = ''
    $rlog = Join-Path $StateDir 'pilcrow.log'
    if (Test-Path $rlog) { $tail = ((Get-Content $rlog -Tail 15) -join "`n") }
    if (-not $serverProc.HasExited) { try { $serverProc.Kill() } catch { } }
    Fail ("Pilcrow 伺服器沒有在 40 秒內就緒。`n`n記錄檔：$rlog`n$tail")
  }
  Log "伺服器就緒：$($server.url)"
}
}

# ---------------------------------------------------------------- 起始顏色（跟 Pilcrow 的主題設定走，避免白色閃一下）
$BackColor = [System.Drawing.Color]::FromArgb(25, 25, 25)
try {
  $cfgPath = Join-Path $env:APPDATA 'Pilcrow\config.json'
  $theme = 'system'
  if (Test-Path $cfgPath) { $cfg = Get-Content -Raw $cfgPath | ConvertFrom-Json; if ($cfg.theme) { $theme = [string]$cfg.theme } }
  if ($theme -eq 'system') {
    $light = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name AppsUseLightTheme -ErrorAction SilentlyContinue
    $theme = if ($light -and $light.AppsUseLightTheme -eq 1) { 'light' } else { 'dark' }
  }
  if ($theme -eq 'light') { $BackColor = [System.Drawing.Color]::FromArgb(255, 255, 255) }
} catch { }

# ---------------------------------------------------------------- 視窗本體（C#，第一次會編成 PilcrowHost.exe 並快取）
$hostCs = @'
using System;
using System.IO;
using System.Net;
using System.Text;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;
using System.Net.WebSockets;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Pilcrow
{
    public class HostOptions
    {
        public string Url;
        public string Title = "Pilcrow";
        public string IconPath;
        public string UserDataDir;
        public string StateFile;
        public string LockFile;
        public string RuntimeFolder;
        public string LogFile;
        public bool DevTools;
        public bool ServerSpawned;
        public bool KeepServer;
        public bool CustomTitleBar = true;
        public Color BackColor = Color.FromArgb(25, 25, 25);
    }

    public static class Win32
    {
        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ReleaseCapture();
        [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] public static extern IntPtr GetSystemMenu(IntPtr hWnd, bool bRevert);
        [DllImport("user32.dll")] public static extern int TrackPopupMenu(IntPtr hMenu, uint uFlags, int x, int y, int nReserved, IntPtr hWnd, IntPtr prcRect);
        [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT lpPoint);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
        [DllImport("user32.dll")] public static extern int GetSystemMetricsForDpi(int nIndex, uint dpi);
        [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern uint GetDpiForSystem();
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern int SetCurrentProcessExplicitAppUserModelID(string id);
        public static double Scale() { try { uint d = GetDpiForSystem(); if (d > 0) return d / 96.0; } catch { } return 1.0; }
        [DllImport("user32.dll")] public static extern bool FlashWindowEx(ref FLASHWINFO pwfi);
        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
        [StructLayout(LayoutKind.Sequential)]
        public struct FLASHWINFO { public uint cbSize; public IntPtr hwnd; public uint dwFlags; public uint uCount; public uint dwTimeout; }
        public static void Flash(IntPtr h)
        {
            FLASHWINFO f = new FLASHWINFO();
            f.cbSize = (uint)Marshal.SizeOf(typeof(FLASHWINFO));
            f.hwnd = h; f.dwFlags = 3 | 12; f.uCount = 3; f.dwTimeout = 0;   // FLASHW_ALL | FLASHW_TIMERNOFG
            FlashWindowEx(ref f);
        }
        /// <summary>視窗外框厚度（像素，依 DPI）：SM_CYSIZEFRAME + SM_CXPADDEDBORDER。</summary>
        public static int FrameThickness(IntPtr h)
        {
            try
            {
                uint dpi = GetDpiForWindow(h);
                if (dpi > 0) return GetSystemMetricsForDpi(33, dpi) + GetSystemMetricsForDpi(92, dpi);
            }
            catch { }
            return GetSystemMetrics(33) + GetSystemMetrics(92);
        }
    }

    public class HostForm : Form
    {
        const int WM_NCCALCSIZE = 0x0083;
        const int WM_NCLBUTTONDOWN = 0x00A1;
        const int WM_SYSCOMMAND = 0x0112;
        const int HTCAPTION = 2, HTTOP = 12, HTTOPLEFT = 13, HTTOPRIGHT = 14;

        readonly HostOptions o;
        readonly WebView2 web;
        readonly Label splash;
        readonly NotifyIcon tray;
        readonly System.Windows.Forms.Timer retry;
        int retries;
        bool pageUp;
        bool fullscreen;
        bool forceQuit;
        FormBorderStyle savedBorder;
        FormWindowState savedState;
        Rectangle savedBounds;
        Color pageColor;
        string pageTitle;
        CoreWebView2Notification lastNote;

        /// <summary>呼叫端應該把伺服器關掉。</summary>
        public bool QuitServer;
        /// <summary>頁面自己要求關閉（使用者在 Pilcrow 裡按了 Quit，伺服器正在自行結束）。</summary>
        public bool PageQuit;

        static readonly HashSet<string> KeepMenu = new HashSet<string>(new string[] { "cut", "copy", "paste", "pasteAndMatchStyle", "selectAll", "undo", "redo" });

        // 注入頁面（只在最上層 frame）：回報 Pilcrow 的介面底色，讓視窗邊框與啟動畫面跟著主題走。
        const string WatchScript =
            "(function(){" +
            "  if (window.top !== window) return;" +
            "  var last='';" +
            "  function color(el){ try{ var c=getComputedStyle(el).backgroundColor; if(c && c!=='transparent' && !/rgba\\(\\s*\\d+,\\s*\\d+,\\s*\\d+,\\s*0\\s*\\)/.test(c)) return c; }catch(e){} return null; }" +
            "  function tick(){ var c=null; try{ c=getComputedStyle(document.documentElement).getPropertyValue('--chrome').trim(); }catch(e){} if(!c && document.body){ c=color(document.body)||(document.body.firstElementChild&&color(document.body.firstElementChild)); } if(!c&&document.documentElement) c=color(document.documentElement); if(c&&c!==last){ last=c; try{ window.chrome.webview.postMessage('bg:'+c); }catch(e){} } }" +
            "  function watch(){ try{ var mo=new MutationObserver(tick); mo.observe(document.documentElement,{attributes:true}); if(document.body) mo.observe(document.body,{attributes:true}); }catch(e){} tick(); }" +
            "  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', watch); else watch();" +
            "  window.addEventListener('load', tick);" +
            "  setInterval(tick, 1500);" +
            "})();";

        // 注入頁面：自訂標題列。#root 往下推 36px，標題列用 Pilcrow 自己的 CSS 變數上色，
        // 拖曳 / 縮放 / 雙擊 / 右鍵系統選單透過 postMessage 交給外殼做成原生行為。
        const string TitleBarScript = @"
(function(){
  if (window.top !== window) return;
  var H = 36;
  var state = { maximized:false, fullscreen:false, active:true, title:'Pilcrow' };
  var tb, title, maxBtn;
  function post(m){ try { window.chrome.webview.postMessage(m); } catch(e){} }
  function ready(fn){ if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn(); }
  function css(){
    var s = document.createElement('style'); s.id = 'rh-style';
    s.textContent = [
      'html{--rh-tb:' + H + 'px}',
      'html.rh-fs{--rh-tb:0px}',
      '#root{position:fixed!important;top:var(--rh-tb)!important;left:0!important;right:0!important;bottom:0!important;height:auto!important;width:auto!important;margin:0!important}',
      '.app,.boot,.quit-note{height:100%!important;width:100%!important}',
      '#rh-tb{position:fixed;top:0;left:0;right:0;height:var(--rh-tb);z-index:2147483647;display:flex;align-items:center;overflow:hidden;background:var(--chrome,#1f1f1f);color:var(--text-2,#9c9c9c);font:12px/1 \'Segoe UI Variable Text\',\'Segoe UI\',\'Microsoft JhengHei UI\',system-ui,sans-serif;user-select:none;-webkit-user-select:none;cursor:default}',
      'html.rh-fs #rh-tb{display:none}',
      '#rh-tb.rh-inactive{color:var(--text-3,#71717a)}',
      '#rh-strip{position:absolute;top:0;left:0;right:0;height:5px;cursor:n-resize;z-index:1}',
      '#rh-strip:before,#rh-strip:after{content:\'\';position:absolute;top:0;width:10px;height:5px}',
      '#rh-strip:before{left:0;cursor:nw-resize}',
      '#rh-strip:after{right:0;cursor:ne-resize}',
      '#rh-tb.rh-max #rh-strip{display:none}',
      '#rh-icon{width:16px;height:16px;margin:0 8px 0 12px;flex:none;pointer-events:none}',
      '#rh-title{flex:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:50vw;letter-spacing:.01em}',
      '#rh-drag{flex:1;align-self:stretch}',
      '#rh-tb button{all:unset;width:46px;align-self:stretch;display:flex;align-items:center;justify-content:center;font-family:\'Segoe Fluent Icons\',\'Segoe MDL2 Assets\';font-size:10px;color:inherit;cursor:default}',
      '#rh-tb button:hover{background:var(--fill-2,rgba(128,128,128,.22));color:var(--text,inherit)}',
      '#rh-tb button:active{opacity:.7}',
      '#rh-tb button.rh-close:hover{background:#c42b1c;color:#fff}'
    ].join('\n');
    document.head.appendChild(s);
  }
  function el(t,id){ var x=document.createElement(t); x.id=id; return x; }
  function btn(g,msg,tip){ var b=document.createElement('button'); b.textContent=g; b.title=tip; b.addEventListener('click',function(){ post(msg); }); b.addEventListener('mousedown',function(e){ e.preventDefault(); }); return b; }
  function build(){
    if (document.getElementById('rh-tb')) return;
    css();
    tb = el('div','rh-tb');
    var strip = el('div','rh-strip');
    strip.addEventListener('mousedown', function(e){
      if (e.button !== 0) return; e.preventDefault();
      var w = tb.getBoundingClientRect().width;
      post(e.clientX < 10 ? 'resize:topleft' : (e.clientX > w - 10 ? 'resize:topright' : 'resize:top'));
    });
    var icon = document.createElement('img'); icon.id='rh-icon'; icon.src='/favicon.svg'; icon.draggable=false; icon.alt=''; icon.onerror=function(){ icon.style.display='none'; };
    title = el('span','rh-title'); title.textContent = state.title;
    var drag = el('div','rh-drag');
    function dragDown(e){ if (e.button !== 0) return; e.preventDefault(); if (e.detail >= 2) { post('maximize-toggle'); return; } post('drag'); }
    function ctx(e){ e.preventDefault(); post('sysmenu'); }
    [drag, title].forEach(function(x){ x.addEventListener('mousedown', dragDown); x.addEventListener('contextmenu', ctx); });
    var mn = btn('','minimize','最小化');
    maxBtn = btn('','maximize-toggle','最大化');
    var cl = btn('','close','關閉'); cl.className='rh-close';
    tb.appendChild(strip); tb.appendChild(icon); tb.appendChild(title); tb.appendChild(drag); tb.appendChild(mn); tb.appendChild(maxBtn); tb.appendChild(cl);
    document.body.appendChild(tb);
    apply();
  }
  function apply(){
    if (!tb) return;
    tb.classList.toggle('rh-max', !!state.maximized);
    tb.classList.toggle('rh-inactive', !state.active);
    document.documentElement.classList.toggle('rh-fs', !!state.fullscreen);
    maxBtn.textContent = state.maximized ? '' : '';
    maxBtn.title = state.maximized ? '還原' : '最大化';
    title.textContent = state.title || 'Pilcrow';
  }
  try { window.chrome.webview.addEventListener('message', function(ev){ var d = ev.data; if (d && typeof d === 'object') { for (var k in d) state[k] = d[k]; apply(); } }); } catch(e){}
  ready(function(){ build(); post('ready'); });
})();";

        public HostForm(HostOptions opts)
        {
            o = opts;
            pageColor = o.BackColor;
            pageTitle = o.Title;
            Text = o.Title;
            BackColor = o.BackColor;
            double sc = Win32.Scale();
            MinimumSize = new Size((int)(720 * sc), (int)(480 * sc));
            AutoScaleMode = AutoScaleMode.None;   // 程序本身是 Per-Monitor V2（manifest），尺寸自己乘 DPI
            if (!string.IsNullOrEmpty(o.IconPath) && File.Exists(o.IconPath)) { try { Icon = new Icon(o.IconPath); } catch { } }
            LoadBounds();

            splash = new Label();
            splash.Dock = DockStyle.Fill;
            splash.TextAlign = ContentAlignment.MiddleCenter;
            splash.Font = new Font("Segoe UI", 10.5f);
            splash.Text = "Pilcrow\n\n啟動中…";
            Controls.Add(splash);

            web = new WebView2();
            web.Dock = DockStyle.Fill;
            web.Visible = false;
            web.DefaultBackgroundColor = o.BackColor;
            web.KeyDown += OnWebKey;
            Controls.Add(web);
            web.BringToFront();

            tray = new NotifyIcon();
            tray.Icon = Icon;
            tray.Text = "Pilcrow";
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("顯示 Pilcrow", null, delegate { ShowMe(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("結束 Pilcrow", null, delegate { forceQuit = true; Close(); });
            tray.ContextMenuStrip = menu;
            tray.DoubleClick += delegate { ShowMe(); };
            tray.BalloonTipClicked += delegate { ShowMe(); if (lastNote != null) { try { lastNote.ReportClicked(); } catch { } } };
            tray.Visible = true;

            retry = new System.Windows.Forms.Timer();
            retry.Interval = 1500;
            retry.Tick += delegate { retry.Stop(); if (!pageUp && web.CoreWebView2 != null) web.CoreWebView2.Navigate(o.Url); };

            Resize += delegate { PostState(); };
            Activated += delegate { PostState(); };
            Deactivate += delegate { PostState(); };

            ApplyColors(o.BackColor);
            Shown += async delegate { await InitAsync(); };
        }

        // ---- 拿掉系統標題列但保留左右下的外框（縮放邊、Snap、陰影、動畫都還是原生的）
        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero && o.CustomTitleBar && !fullscreen && FormBorderStyle != FormBorderStyle.None)
            {
                Win32.RECT before = (Win32.RECT)Marshal.PtrToStructure(m.LParam, typeof(Win32.RECT));
                base.WndProc(ref m);
                Win32.RECT after = (Win32.RECT)Marshal.PtrToStructure(m.LParam, typeof(Win32.RECT));
                after.Top = before.Top + (Win32.IsZoomed(Handle) ? Win32.FrameThickness(Handle) : 0);
                Marshal.StructureToPtr(after, m.LParam, false);
                m.Result = IntPtr.Zero;
                return;
            }
            base.WndProc(ref m);
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try { Log("window dpi " + Win32.GetDpiForWindow(Handle) + ", system dpi " + Win32.GetDpiForSystem()); } catch { }
            ApplyTitleBar();
            try { File.WriteAllText(o.LockFile, "{\"pid\":" + System.Diagnostics.Process.GetCurrentProcess().Id + ",\"hwnd\":" + Handle.ToInt64() + "}"); } catch { }
        }

        async Task InitAsync()
        {
            try
            {
                CoreWebView2EnvironmentOptions envOpts = new CoreWebView2EnvironmentOptions();
                envOpts.AdditionalBrowserArguments = "--disable-features=msSmartScreenProtection";
                string rt = string.IsNullOrEmpty(o.RuntimeFolder) ? null : o.RuntimeFolder;
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(rt, o.UserDataDir, envOpts);
                await web.EnsureCoreWebView2Async(env);
                CoreWebView2 cw = web.CoreWebView2;
                CoreWebView2Settings s = cw.Settings;
                s.AreDevToolsEnabled = o.DevTools;
                s.IsStatusBarEnabled = false;
                s.IsZoomControlEnabled = false;
                s.AreDefaultContextMenusEnabled = true;            // 只留剪下 / 複製 / 貼上這幾項，見 OnContextMenu
                try
                {
                    s.AreBrowserAcceleratorKeysEnabled = o.DevTools;   // 關掉 F5 / Ctrl+F / Ctrl+P / Ctrl+U … 這類瀏覽器快捷鍵；Pilcrow 自己的快捷鍵不受影響
                    s.IsBuiltInErrorPageEnabled = false;
                    s.IsPinchZoomEnabled = false;
                    s.IsSwipeNavigationEnabled = false;
                    s.IsPasswordAutosaveEnabled = false;
                    s.IsGeneralAutofillEnabled = false;
                }
                catch (Exception ex) { Log("optional settings skipped (old runtime): " + ex.Message); }

                cw.DocumentTitleChanged += delegate { pageTitle = string.IsNullOrEmpty(cw.DocumentTitle) ? o.Title : cw.DocumentTitle; Text = pageTitle; PostState(); };
                cw.NavigationStarting += OnNavigating;
                cw.NavigationCompleted += OnNavigated;
                cw.NewWindowRequested += OnNewWindow;
                cw.WindowCloseRequested += delegate { PageQuit = true; Close(); };
                cw.WebMessageReceived += OnMessage;
                cw.ContextMenuRequested += OnContextMenu;
                cw.PermissionRequested += OnPermission;
                cw.ProcessFailed += OnProcessFailed;
                try { cw.NotificationReceived += OnNotification; } catch (Exception ex) { Log("notifications unavailable: " + ex.Message); }
                await cw.AddScriptToExecuteOnDocumentCreatedAsync(WatchScript);
                if (o.CustomTitleBar) await cw.AddScriptToExecuteOnDocumentCreatedAsync(TitleBarScript);
                cw.Navigate(o.Url);
            }
            catch (Exception ex)
            {
                ShowSplash("無法啟動 WebView2：\n" + ex.Message);
                Log(ex.ToString());
            }
        }

        // ---- 導覽：只讓自己的頁面留在視窗裡，外部連結交給預設瀏覽器
        bool SameOrigin(string uri)
        {
            try { Uri a = new Uri(uri); Uri b = new Uri(o.Url); return a.Scheme == b.Scheme && a.Host == b.Host && a.Port == b.Port; }
            catch { return false; }
        }
        static void OpenExternal(string uri)
        {
            try { if (uri.StartsWith("http://") || uri.StartsWith("https://") || uri.StartsWith("mailto:")) System.Diagnostics.Process.Start(uri); } catch { }
        }
        void OnNavigating(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            if (!SameOrigin(e.Uri)) { e.Cancel = true; OpenExternal(e.Uri); }
        }
        void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            e.Handled = true;
            OpenExternal(e.Uri);
        }
        void OnNavigated(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (e.IsSuccess)
            {
                pageUp = true; retries = 0;
                web.Visible = true; splash.Visible = false;
                web.Focus();
                return;
            }
            pageUp = false; web.Visible = false; retries++;
            if (retries < 40) { ShowSplash("正在等待 Pilcrow 伺服器回應…\n(" + e.WebErrorStatus + ")"); retry.Start(); }
            else ShowSplash("連不上 Pilcrow 伺服器。請關閉視窗後重新開啟。\n" + o.Url);
        }
        void OnProcessFailed(object sender, CoreWebView2ProcessFailedEventArgs e)
        {
            Log("process failed: " + e.ProcessFailedKind);
            if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited)
            {
                ShowSplash("Pilcrow 的顯示引擎已結束，請關閉視窗後重新開啟。");
                web.Visible = false; pageUp = false;
            }
            else { pageUp = false; web.Visible = false; ShowSplash("顯示引擎發生問題（" + e.ProcessFailedKind + "），重新載入中…"); retry.Start(); }
        }

        // ---- 頁面 → 外殼
        void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string msg;
            try { msg = e.TryGetWebMessageAsString(); } catch { return; }
            if (msg == null) return;
            if (msg.StartsWith("bg:")) { Color c; if (TryParseColor(msg.Substring(3), out c)) ApplyColors(c); return; }
            switch (msg)
            {
                case "ready": PostState(); break;
                case "drag": NcDown(HTCAPTION); break;
                case "resize:top": NcDown(HTTOP); break;
                case "resize:topleft": NcDown(HTTOPLEFT); break;
                case "resize:topright": NcDown(HTTOPRIGHT); break;
                case "minimize": WindowState = FormWindowState.Minimized; break;
                case "maximize-toggle": WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized; break;
                case "close": Close(); break;
                case "sysmenu": ShowSystemMenu(); break;
            }
        }
        void NcDown(int hit)
        {
            if (fullscreen) return;
            if (hit != HTCAPTION && WindowState == FormWindowState.Maximized) return;
            Win32.ReleaseCapture();
            Win32.SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)hit, IntPtr.Zero);
        }
        void ShowSystemMenu()
        {
            try
            {
                Win32.POINT pt; Win32.GetCursorPos(out pt);
                IntPtr menu = Win32.GetSystemMenu(Handle, false);
                int cmd = Win32.TrackPopupMenu(menu, 0x0100, pt.X, pt.Y, 0, Handle, IntPtr.Zero);   // TPM_RETURNCMD
                if (cmd != 0) Win32.PostMessage(Handle, WM_SYSCOMMAND, (IntPtr)cmd, IntPtr.Zero);
            }
            catch { }
        }
        static string JsonString(string s)
        {
            StringBuilder b = new StringBuilder("\"");
            foreach (char c in s ?? "")
            {
                if (c == '"' || c == '\\') { b.Append('\\').Append(c); }
                else if (c < ' ') { b.Append("\\u").Append(((int)c).ToString("x4")); }
                else b.Append(c);
            }
            return b.Append('"').ToString();
        }
        void PostState()
        {
            try
            {
                if (web.CoreWebView2 == null) return;
                bool max = WindowState == FormWindowState.Maximized;
                string json = "{\"maximized\":" + (max ? "true" : "false") + ",\"fullscreen\":" + (fullscreen ? "true" : "false") +
                              ",\"active\":" + (Form.ActiveForm == this ? "true" : "false") + ",\"title\":" + JsonString(pageTitle) + "}";
                web.CoreWebView2.PostWebMessageAsJson(json);
            }
            catch { }
        }

        // ---- 外觀：邊框顏色跟著頁面主題
        static bool TryParseColor(string s, out Color c)
        {
            c = Color.Empty;
            Match m = Regex.Match(s, @"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)");
            if (m.Success) { c = Color.FromArgb(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value), int.Parse(m.Groups[3].Value)); return true; }
            m = Regex.Match(s.Trim(), @"^#([0-9a-fA-F]{6})$");
            if (m.Success) { int v = Convert.ToInt32(m.Groups[1].Value, 16); c = Color.FromArgb((v >> 16) & 255, (v >> 8) & 255, v & 255); return true; }
            m = Regex.Match(s.Trim(), @"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$");
            if (m.Success) { c = Color.FromArgb(Convert.ToInt32(m.Groups[1].Value + m.Groups[1].Value, 16), Convert.ToInt32(m.Groups[2].Value + m.Groups[2].Value, 16), Convert.ToInt32(m.Groups[3].Value + m.Groups[3].Value, 16)); return true; }
            return false;
        }
        static int Luma(Color c) { return (int)(0.299 * c.R + 0.587 * c.G + 0.114 * c.B); }
        void ApplyColors(Color c)
        {
            pageColor = c;
            BackColor = c;
            splash.BackColor = c;
            splash.ForeColor = Luma(c) < 128 ? Color.FromArgb(212, 212, 216) : Color.FromArgb(63, 63, 70);
            try { web.DefaultBackgroundColor = c; } catch { }
            ApplyTitleBar();
        }
        void ApplyTitleBar()
        {
            if (!IsHandleCreated) return;
            bool dark = Luma(pageColor) < 128;
            int on = dark ? 1 : 0;
            if (Win32.DwmSetWindowAttribute(Handle, 20, ref on, 4) != 0) Win32.DwmSetWindowAttribute(Handle, 19, ref on, 4);   // DWMWA_USE_IMMERSIVE_DARK_MODE（舊版 Win10 是 19）
            Color fg = dark ? Color.FromArgb(228, 228, 231) : Color.FromArgb(39, 39, 42);
            int cap = pageColor.R | (pageColor.G << 8) | (pageColor.B << 16);
            int txt = fg.R | (fg.G << 8) | (fg.B << 16);
            Win32.DwmSetWindowAttribute(Handle, 35, ref cap, 4);   // DWMWA_CAPTION_COLOR（Win11；用原生標題列時生效）
            Win32.DwmSetWindowAttribute(Handle, 36, ref txt, 4);   // DWMWA_TEXT_COLOR
            Win32.DwmSetWindowAttribute(Handle, 34, ref cap, 4);   // DWMWA_BORDER_COLOR
        }
        void ShowSplash(string text)
        {
            splash.Text = text;
            splash.Visible = true;
            splash.BringToFront();
        }

        // ---- 右鍵選單只留編輯動作；通知與剪貼簿權限直接給
        void OnContextMenu(object sender, CoreWebView2ContextMenuRequestedEventArgs e)
        {
            IList<CoreWebView2ContextMenuItem> items = e.MenuItems;
            for (int i = items.Count - 1; i >= 0; i--)
            {
                CoreWebView2ContextMenuItem it = items[i];
                if (it.Kind == CoreWebView2ContextMenuItemKind.Separator || !KeepMenu.Contains(it.Name)) items.RemoveAt(i);
            }
            if (items.Count == 0) e.Handled = true;
        }
        void OnPermission(object sender, CoreWebView2PermissionRequestedEventArgs e)
        {
            // 只有 Pilcrow 自己的頁面拿得到通知 / 讀剪貼簿權限；預覽面板裡 iframe 進來的其他網站一律拒絕
            if (!SameOrigin(e.Uri)) { e.State = CoreWebView2PermissionState.Deny; return; }
            if (e.PermissionKind == CoreWebView2PermissionKind.Notifications || e.PermissionKind == CoreWebView2PermissionKind.ClipboardRead) e.State = CoreWebView2PermissionState.Allow;
        }
        void OnNotification(object sender, CoreWebView2NotificationReceivedEventArgs e)
        {
            e.Handled = true;
            try { if (!SameOrigin(e.SenderOrigin)) return; } catch { }
            CoreWebView2Notification n = e.Notification;
            lastNote = n;
            string title = string.IsNullOrEmpty(n.Title) ? "Pilcrow" : n.Title;
            string body = string.IsNullOrEmpty(n.Body) ? " " : n.Body;
            try { tray.ShowBalloonTip(6000, title, body, ToolTipIcon.None); } catch { }
            try { n.ReportShown(); } catch { }
            if (Form.ActiveForm != this) Win32.Flash(Handle);
        }

        // ---- 鍵盤：F11 全螢幕
        void OnWebKey(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.F11 && !e.Control && !e.Alt && !e.Shift) { e.Handled = true; ToggleFullscreen(); }
        }
        void ToggleFullscreen()
        {
            if (!fullscreen)
            {
                savedBorder = FormBorderStyle;
                savedState = WindowState;
                savedBounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                fullscreen = true;
                FormBorderStyle = FormBorderStyle.None;
                WindowState = FormWindowState.Normal;
                Bounds = Screen.FromControl(this).Bounds;
            }
            else
            {
                fullscreen = false;
                FormBorderStyle = savedBorder;
                Bounds = savedBounds;
                WindowState = savedState;
            }
            PostState();
        }

        // ---- 視窗位置記憶
        void LoadBounds()
        {
            StartPosition = FormStartPosition.CenterScreen;
            double sc = Win32.Scale();
            Rectangle wa = Screen.PrimaryScreen.WorkingArea;
            Size = new Size(Math.Min((int)(1480 * sc), wa.Width - 40), Math.Min((int)(940 * sc), wa.Height - 40));
            try
            {
                if (!File.Exists(o.StateFile)) return;
                Match m = Regex.Match(File.ReadAllText(o.StateFile), "\"x\":(-?\\d+),\"y\":(-?\\d+),\"w\":(\\d+),\"h\":(\\d+),\"max\":(true|false)");
                if (!m.Success) return;
                Rectangle r = new Rectangle(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value), int.Parse(m.Groups[3].Value), int.Parse(m.Groups[4].Value));
                foreach (Screen scr in Screen.AllScreens)
                {
                    if (scr.WorkingArea.IntersectsWith(r)) { StartPosition = FormStartPosition.Manual; Bounds = r; break; }
                }
                if (m.Groups[5].Value == "true") WindowState = FormWindowState.Maximized;
            }
            catch { }
        }
        void SaveBounds()
        {
            try
            {
                Rectangle r = fullscreen ? savedBounds : (WindowState == FormWindowState.Normal ? Bounds : RestoreBounds);
                bool max = fullscreen ? savedState == FormWindowState.Maximized : WindowState == FormWindowState.Maximized;
                File.WriteAllText(o.StateFile, "{\"x\":" + r.X + ",\"y\":" + r.Y + ",\"w\":" + r.Width + ",\"h\":" + r.Height + ",\"max\":" + (max ? "true" : "false") + "}");
            }
            catch { }
        }

        public void ShowMe()
        {
            Show();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Activate();
            if (Win32.IsIconic(Handle)) Win32.ShowWindow(Handle, 9);
            Win32.SetForegroundWindow(Handle);
        }

        int ServerBusy()
        {
            try
            {
                using (WebClient wc = new WebClient())
                {
                    wc.Proxy = null;
                    string j = wc.DownloadString(o.Url.TrimEnd('/') + "/api/hello");
                    Match m = Regex.Match(j, "\"busy\":(\\d+)");
                    return m.Success ? int.Parse(m.Groups[1].Value) : 0;
                }
            }
            catch { return 0; }
        }

        // ---- 關閉：自己開的伺服器就一起關（Claude 還在忙時先問）；別人開的就留著
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!PageQuit && e.CloseReason == CloseReason.UserClosing)
            {
                bool owned = forceQuit || (o.ServerSpawned && !o.KeepServer);
                if (owned)
                {
                    int busy = ServerBusy();
                    if (busy > 0)
                    {
                        DialogResult r = MessageBox.Show(this,
                            "Claude 還有 " + busy + " 個 session 正在工作。\n\n" +
                            "是：結束 Pilcrow（會中斷它們，對話會保留）\n" +
                            "否：只關視窗，讓 Pilcrow 在背景繼續跑\n" +
                            "取消：回到視窗",
                            "結束 Pilcrow？", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button3);
                        if (r == DialogResult.Cancel) { e.Cancel = true; base.OnFormClosing(e); return; }
                        QuitServer = (r == DialogResult.Yes);
                    }
                    else QuitServer = true;
                }
            }
            SaveBounds();
            tray.Visible = false;
            try { File.Delete(o.LockFile); } catch { }
            base.OnFormClosing(e);
        }

        void Log(string text)
        {
            try { File.AppendAllText(o.LogFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + "\r\n"); } catch { }
        }
    }

    public static class Host
    {
        public static HostForm Run(HostOptions o)
        {
            try { Application.EnableVisualStyles(); } catch { }
            try { Application.SetCompatibleTextRenderingDefault(false); } catch { }
            HostForm f = new HostForm(o);
            Application.Run(f);
            return f;
        }

        /// <summary>用 Pilcrow 自己的 WebSocket 協定送 app.quit，讓伺服器乾淨地收尾。</summary>
        public static bool QuitServer(string url, int timeoutMs)
        {
            try
            {
                Uri u = new Uri(url);
                using (ClientWebSocket ws = new ClientWebSocket())
                {
                    ws.Options.Proxy = null;
                    CancellationTokenSource cts = new CancellationTokenSource(timeoutMs);
                    ws.ConnectAsync(new Uri("ws://" + u.Host + ":" + u.Port + "/ws"), cts.Token).Wait(cts.Token);
                    byte[] bytes = Encoding.UTF8.GetBytes("{\"t\":\"req\",\"id\":1,\"m\":\"app.quit\",\"p\":{}}");
                    ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token).Wait(cts.Token);
                    byte[] buf = new byte[4096];
                    try { ws.ReceiveAsync(new ArraySegment<byte>(buf), cts.Token).Wait(cts.Token); } catch { }
                }
                return true;
            }
            catch { return false; }
        }
    }

    /// <summary>What the shortcut does: find Pilcrow, make sure its server runs, hand the URL to the window.</summary>
    public static class Boot
    {
        static string Field(string json, string key)
        {
            if (json == null) return null;
            var m = System.Text.RegularExpressions.Regex.Match(json, "\"" + System.Text.RegularExpressions.Regex.Escape(key) + "\"\\s*:\\s*(\"((?:[^\"\\\\]|\\\\.)*)\"|[^,}\\s]+)");
            if (!m.Success) return null;
            if (m.Groups[2].Success)
            {
                string v = m.Groups[2].Value;
                v = System.Text.RegularExpressions.Regex.Replace(v, "\\\\u([0-9A-Fa-f]{4})", delegate(System.Text.RegularExpressions.Match x) { return ((char)Convert.ToInt32(x.Groups[1].Value, 16)).ToString(); });
                return v.Replace("\\\\", "\\").Replace("\\/", "/").Replace("\\\"", "\"");
            }
            return m.Groups[1].Value;
        }
        static bool Flag(string json, string key) { return Field(json, key) == "true"; }
        static string ReadAll(string path) { try { return (!string.IsNullOrEmpty(path) && File.Exists(path)) ? File.ReadAllText(path) : null; } catch { return null; } }
        static string HttpGet(string url, int timeoutMs)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Proxy = null; req.Timeout = timeoutMs; req.ReadWriteTimeout = timeoutMs;
                using (WebResponse res = req.GetResponse()) using (StreamReader r = new StreamReader(res.GetResponseStream())) return r.ReadToEnd();
            }
            catch { return null; }
        }
        static bool Alive(int pid) { try { Process.GetProcessById(pid); return true; } catch { return false; } }

        class Server { public int Pid; public int Port; public string Url; public string Version; public int Busy; }
        static Server Find(string stateDir)
        {
            string j = ReadAll(Path.Combine(stateDir, "instance.json"));
            if (j == null) return null;
            int pid, port;
            if (!int.TryParse(Field(j, "pid"), out pid) || !int.TryParse(Field(j, "port"), out port) || !Alive(pid)) return null;
            string hello = HttpGet("http://127.0.0.1:" + port + "/api/hello", 2000);
            if (hello == null || Field(hello, "app") != "pilcrow" || Field(hello, "pid") != pid.ToString()) return null;
            Server s = new Server(); s.Pid = pid; s.Port = port; s.Url = "http://127.0.0.1:" + port + "/"; s.Version = Field(hello, "version");
            int.TryParse(Field(hello, "busy"), out s.Busy);
            return s;
        }

        public static int Run(HostOptions o, out int serverPid)
        {
            serverPid = 0;
            string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            string launch = ReadAll(Path.Combine(exeDir, "launch.json"));
            if (launch == null) { MessageBox.Show("Pilcrow is not set up on this computer yet.\n\nRun pilcrow-windows.ps1 once to install it.", "Pilcrow", MessageBoxButtons.OK, MessageBoxIcon.Warning); return 2; }
            string node = Field(launch, "node"), mainJs = Field(launch, "mainJs"), dir = Field(launch, "dir"), stateDir = Field(launch, "stateDir");
            o.IconPath = Field(launch, "icon"); o.UserDataDir = Field(launch, "userdata"); o.StateFile = Field(launch, "state"); o.LockFile = Field(launch, "lock");
            o.LogFile = Field(launch, "log"); o.RuntimeFolder = Field(launch, "runtime");
            if (o.RuntimeFolder == "") o.RuntimeFolder = null;
            o.DevTools = Flag(launch, "devtools"); o.KeepServer = Flag(launch, "keepServer"); o.CustomTitleBar = !Flag(launch, "nativeTitleBar");
            Log(o.LogFile, "PilcrowHost boot from shortcut");

            // One window: an open Pilcrow comes to the front instead of a second copy.
            string lockJson = ReadAll(o.LockFile);
            if (lockJson != null)
            {
                int lpid; long hwnd;
                if (int.TryParse(Field(lockJson, "pid"), out lpid) && long.TryParse(Field(lockJson, "hwnd"), out hwnd) && lpid != Process.GetCurrentProcess().Id && Alive(lpid))
                {
                    IntPtr h = (IntPtr)hwnd;
                    if (Win32.IsIconic(h)) Win32.ShowWindow(h, 9);
                    if (Win32.SetForegroundWindow(h)) { Log(o.LogFile, "already open (pid " + lpid + "); activated"); return 0; }
                }
            }

            // Start colour follows the saved theme so nothing flashes white.
            try
            {
                string cfg = ReadAll(Field(launch, "config"));
                string theme = cfg != null ? (Field(cfg, "theme") ?? "system") : "system";
                if (theme == "system")
                {
                    object v = Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", null);
                    theme = (v is int && (int)v == 1) ? "light" : "dark";
                }
                o.BackColor = theme == "light" ? Color.FromArgb(255, 255, 255) : Color.FromArgb(25, 25, 25);
            }
            catch { }

            // The server: reuse a running one (unless it is an older, idle build), otherwise start it hidden.
            Server srv = Find(stateDir);
            string pkgVersion = Field(ReadAll(Path.Combine(dir, "package.json")) ?? "", "version");
            if (srv != null && !string.IsNullOrEmpty(pkgVersion) && srv.Version != pkgVersion && srv.Busy == 0)
            {
                Log(o.LogFile, "server " + srv.Version + " is older than " + pkgVersion + "; stopping it");
                try { Process.GetProcessById(srv.Pid).Kill(); Thread.Sleep(800); } catch { }
                srv = Find(stateDir);
            }
            bool spawned = false;
            Process proc = null;
            if (srv == null)
            {
                if (string.IsNullOrEmpty(node) || !File.Exists(node)) { MessageBox.Show("Node.js was not found at\n" + node + "\n\nRun pilcrow-windows.ps1 again to repair the install.", "Pilcrow", MessageBoxButtons.OK, MessageBoxIcon.Error); return 3; }
                if (!File.Exists(mainJs)) { MessageBox.Show("Pilcrow was not found at\n" + dir + "\n\nRun pilcrow-windows.ps1 again to reinstall it.", "Pilcrow", MessageBoxButtons.OK, MessageBoxIcon.Error); return 3; }
                ProcessStartInfo psi = new ProcessStartInfo(node, "\"" + mainJs + "\" --shell=none");
                psi.WorkingDirectory = dir; psi.UseShellExecute = false; psi.CreateNoWindow = true;
                try { proc = Process.Start(psi); } catch (Exception e) { MessageBox.Show("Could not start the Pilcrow server:\n" + e.Message, "Pilcrow", MessageBoxButtons.OK, MessageBoxIcon.Error); return 3; }
                spawned = true;
                Log(o.LogFile, "started server (node pid " + proc.Id + ")");
                DateTime deadline = DateTime.Now.AddSeconds(40);
                while (DateTime.Now < deadline)
                {
                    Thread.Sleep(250);
                    if (proc.HasExited) break;
                    Server s = Find(stateDir);
                    if (s != null && s.Pid == proc.Id) { srv = s; break; }
                }
                if (srv == null)
                {
                    string tail = "";
                    try { string[] lines = File.ReadAllLines(Path.Combine(stateDir, "pilcrow.log")); int from = Math.Max(0, lines.Length - 15); tail = string.Join("\n", lines, from, lines.Length - from); } catch { }
                    if (!proc.HasExited) { try { proc.Kill(); } catch { } }
                    MessageBox.Show("The Pilcrow server did not become ready within 40 seconds.\n\nLog: " + Path.Combine(stateDir, "pilcrow.log") + "\n" + tail, "Pilcrow", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return 4;
                }
            }
            else Log(o.LogFile, "using running server " + srv.Version + " (pid " + srv.Pid + ")");
            o.Url = srv.Url; o.ServerSpawned = spawned; serverPid = srv.Pid;
            return -1; // carry on and open the window
        }

        static void Log(string file, string text)
        {
            try { if (!string.IsNullOrEmpty(file)) File.AppendAllText(file, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + "\r\n"); } catch { }
        }
    }

    public static class Program
    {
        static void Log(string file, string text)
        {
            try { if (!string.IsNullOrEmpty(file)) File.AppendAllText(file, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + "\r\n"); } catch { }
        }

        [STAThread]
        public static int Main(string[] args)
        {
            HostOptions o = new HostOptions();
            int serverPid = 0;
            foreach (string a in args)
            {
                int eq = a.IndexOf('=');
                string k = eq < 0 ? a : a.Substring(0, eq);
                string v = eq < 0 ? "" : a.Substring(eq + 1);
                switch (k)
                {
                    case "--url": o.Url = v; break;
                    case "--icon": o.IconPath = v; break;
                    case "--userdata": o.UserDataDir = v; break;
                    case "--state": o.StateFile = v; break;
                    case "--lock": o.LockFile = v; break;
                    case "--runtime": o.RuntimeFolder = v; break;
                    case "--log": o.LogFile = v; break;
                    case "--serverpid": int.TryParse(v, out serverPid); break;
                    case "--bg":
                        {
                            string[] p = v.Split(',');
                            if (p.Length == 3) o.BackColor = Color.FromArgb(int.Parse(p[0]), int.Parse(p[1]), int.Parse(p[2]));
                            break;
                        }
                    case "--devtools": o.DevTools = true; break;
                    case "--spawned": o.ServerSpawned = true; break;
                    case "--keepserver": o.KeepServer = true; break;
                    case "--nativetitlebar": o.CustomTitleBar = false; break;
                }
            }
            try { Win32.SetCurrentProcessExplicitAppUserModelID("Pilcrow.Desktop"); } catch { }
            if (string.IsNullOrEmpty(o.Url))
            {
                // Started from a shortcut: read launch.json next to the exe, reuse or start the server, then open.
                int rc = Boot.Run(o, out serverPid);
                if (rc >= 0) return rc;
            }
            Log(o.LogFile, "PilcrowHost start " + o.Url);

            HostForm f = Host.Run(o);

            // 視窗關了之後：自己開的伺服器就收掉（頁面 Quit 的話等它自己結束）
            Process srv = null;
            try { if (serverPid > 0) srv = Process.GetProcessById(serverPid); } catch { }
            if (srv != null)
            {
                if (f.PageQuit)
                {
                    Log(o.LogFile, "page quit; waiting for server to exit");
                    if (!srv.WaitForExit(6000)) { try { srv.Kill(); } catch { } }
                }
                else if (f.QuitServer)
                {
                    Log(o.LogFile, "stopping server");
                    Host.QuitServer(o.Url, 3000);
                    if (!srv.WaitForExit(6000)) { Log(o.LogFile, "server did not exit in 6s, killing"); try { srv.Kill(); } catch { } }
                }
                else Log(o.LogFile, "window closed; server left running (pid " + serverPid + ")");
            }
            Log(o.LogFile, "PilcrowHost exit");
            return 0;
        }
    }
}
'@

$Manifest = @'
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="Pilcrow.Desktop.Host" type="win32"/>
  <description>Pilcrow desktop host</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v2">
    <security><requestedPrivileges xmlns="urn:schemas-microsoft-com:asm.v3"><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security>
  </trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application><supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/></application>
  </compatibility>
</assembly>
'@
$ExeConfig = @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.7.2"/></startup>
  <System.Windows.Forms.ApplicationConfigurationSection>
    <add key="DpiAwareness" value="PerMonitorV2"/>
  </System.Windows.Forms.ApplicationConfigurationSection>
</configuration>
'@

# 視窗程式編成真正的 PilcrowHost.exe（放在 SDK 資料夾旁，DLL 直接找得到）。
# 一定要是獨立 exe 而不是在 powershell.exe 裡開視窗：Per-Monitor V2 的 DPI 宣告只有寫在 exe 的 manifest 才完全可靠，
# 否則 WebView2 會以 96 DPI 繪製再被 Windows 放大，字會糊。
$sha = [System.Security.Cryptography.SHA256]::Create()
$iconBytes = if (Test-Path $IconPath) { [IO.File]::ReadAllBytes($IconPath) } else { [byte[]]@() }
$hash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($hostCs + '|' + $Manifest + '|' + $ExeConfig + '|' + $SdkVersion + '|' + [Convert]::ToBase64String($sha.ComputeHash($iconBytes))))) -replace '-', '').Substring(0, 12).ToLower()
$HostExe = Join-Path $SdkDir 'PilcrowHost.exe'
$HashFile = Join-Path $SdkDir 'PilcrowHost.hash'
$haveExe = (Test-Path $HostExe) -and (Test-Path $HashFile) -and ((Get-Content -Raw $HashFile).Trim() -eq $hash)
if (-not $haveExe) {
  Log "編譯視窗程式（只有第一次，或 SDK / 本檔更新後）…"
  $manifestPath = Join-Path $SdkDir 'PilcrowHost.manifest'
  [IO.File]::WriteAllText($manifestPath, $Manifest, (New-Object Text.UTF8Encoding $false))
  [IO.File]::WriteAllText(($HostExe + '.config'), $ExeConfig, (New-Object Text.UTF8Encoding $false))
  if (Test-Path $HostExe) {
    try { Remove-Item -Force $HostExe } catch { Fail "PilcrowHost.exe 正在使用中，請先關閉 Pilcrow 視窗再執行一次。" }
  }
  # 直接呼叫 .NET Framework 內建的 csc.exe（Windows PowerShell 5.1 的 Add-Type 沒有 -CompilerOptions，塞不進 manifest）
  $csc = Join-Path ([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) 'csc.exe'
  if (-not (Test-Path $csc)) { Fail "找不到 C# 編譯器（$csc）。需要 .NET Framework 4.x（Windows 內建）。" }
  $srcPath = Join-Path $SdkDir 'PilcrowHost.cs'
  [IO.File]::WriteAllText($srcPath, $hostCs, (New-Object Text.UTF8Encoding $true))
  $platform = switch ($Arch) { 'x64' { 'x64' } 'x86' { 'x86' } default { 'anycpu' } }
  $cscArgs = @('/nologo', '/target:winexe', '/optimize+', ('/platform:' + $platform), ('/out:' + $HostExe), ('/win32manifest:' + $manifestPath))
  if (Test-Path $IconPath) { $cscArgs += ('/win32icon:' + $IconPath) }
  $cscArgs += @('/r:System.dll', '/r:System.Core.dll', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', ('/r:' + $CoreDll), ('/r:' + $WinFormsDll), $srcPath)
  $r = Invoke-Native $csc $cscArgs
  if ($r.Code -ne 0 -or -not (Test-Path $HostExe)) { Fail ("視窗程式編譯失敗（csc 結束碼 $($r.Code)）：`n" + $r.Output) }
  if (-not (Test-Path $HostExe)) { Fail "視窗程式編譯失敗（沒有產生 PilcrowHost.exe）。" }
  [IO.File]::WriteAllText($HashFile, $hash)
}

# ---------------------------------------------------------------- 捷徑要用的設定：exe 從這裡知道 node、Pilcrow 與狀態在哪
$launch = [ordered]@{
  node = $NodeExe; mainJs = $MainJs; dir = $Dir; stateDir = $StateDir
  icon = $IconPath; userdata = (Join-Path $HostDir 'webview2'); state = (Join-Path $HostDir 'window.json'); lock = $HostLock; log = $LogFile
  runtime = $(if ($rtFolder) { [string]$rtFolder } else { '' }); config = (Join-Path $env:APPDATA 'Pilcrow\config.json')
  devtools = [bool]$DevTools; keepServer = [bool]$KeepServer; nativeTitleBar = [bool]$NativeTitleBar
}
[IO.File]::WriteAllText((Join-Path $SdkDir 'launch.json'), ($launch | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))

if ($Install) {
  New-AppShortcut $StartMenuLink $HostExe
  if (-not $NoDesktop) { New-AppShortcut $DesktopLink $HostExe }
  Write-Host "已建立捷徑：開始功能表「Pilcrow」$(if (-not $NoDesktop) { '、桌面「Pilcrow」' })，直接指向 $HostExe" -ForegroundColor Green
}
if ($SkipLaunch) { exit 0 }

# ---------------------------------------------------------------- 開視窗（獨立程序；本腳本到此結束）
function Q([string]$s) { return '"' + $s + '"' }
$hostArgs = @(
  (Q ('--url=' + $server.url)),
  (Q ('--icon=' + $IconPath)),
  (Q ('--userdata=' + (Join-Path $HostDir 'webview2'))),
  (Q ('--state=' + (Join-Path $HostDir 'window.json'))),
  (Q ('--lock=' + $HostLock)),
  (Q ('--log=' + $LogFile)),
  ('--serverpid=' + $server.pid),
  ('--bg=' + $BackColor.R + ',' + $BackColor.G + ',' + $BackColor.B)
)
if ($rtFolder) { $hostArgs += (Q ('--runtime=' + $rtFolder)) }
if ($DevTools) { $hostArgs += '--devtools' }
if ($spawned) { $hostArgs += '--spawned' }
if ($KeepServer) { $hostArgs += '--keepserver' }
if ($NativeTitleBar) { $hostArgs += '--nativetitlebar' }

Log "開啟視窗：$($server.url)"
Start-Process -FilePath $HostExe -ArgumentList ($hostArgs -join ' ') -WorkingDirectory $SdkDir | Out-Null
exit 0
