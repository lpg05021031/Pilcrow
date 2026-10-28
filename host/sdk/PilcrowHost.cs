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