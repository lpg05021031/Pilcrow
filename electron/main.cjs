// Pilcrow desktop shell: runs the Pilcrow server in-process and shows it in a native window.
const { app, BrowserWindow, shell, Menu, nativeTheme, dialog, ipcMain, screen, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');

const ROOT = path.join(__dirname, '..');
// Same folder the server uses for its state (server/util.js xdg('state')).
const STATE_DIR =
  process.platform === 'win32'
    ? path.join(process.env.LOCALAPPDATA || path.join(app.getPath('home'), 'AppData', 'Local'), 'Pilcrow', 'state')
    : path.join(process.env.XDG_STATE_HOME || path.join(app.getPath('home'), '.local', 'state'), 'pilcrow');

app.setName('Pilcrow');
if (process.platform === 'linux') {
  // Match the launcher entry (pilcrow.desktop, StartupWMClass=pilcrow) so docks group the window correctly.
  process.env.CHROME_DESKTOP = 'pilcrow.desktop';
  app.commandLine.appendSwitch('class', 'Pilcrow');
  app.commandLine.appendSwitch('enable-features', 'WaylandWindowDecorations');
}
if (process.env.PILCROW_DISABLE_GPU === '1') app.disableHardwareAcceleration();

let win = null;
let server = null;
let store = null;
let quitting = false;

// After an update the previous version may still be running; replace it (when idle) instead of focusing it.
(async () => {
  try {
    const mod = await import(pathToFileURL(path.join(ROOT, 'server', 'main.js')).href);
    const existing = await mod.existingInstance();
    if (existing) await mod.retireOutdated(existing);
  } catch {
    /* nothing to replace */
  }
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });
  app.whenReady().then(boot).catch((e) => {
    dialog.showErrorBox('Pilcrow could not start', String(e && e.stack ? e.stack : e));
    app.exit(1);
  });
})();

async function boot() {
  const mod = await import(pathToFileURL(path.join(ROOT, 'server', 'main.js')).href);
  store = (await import(pathToFileURL(path.join(ROOT, 'server', 'config.js')).href)).store;
  server = await mod.start({ shell: 'electron', quiet: true, onQuit: () => app.quit() });
  Menu.setApplicationMenu(buildMenu());
  createWindow();
}

function boundsFromConfig() {
  const saved = store?.getConfig().window;
  const area = screen.getPrimaryDisplay().workAreaSize;
  const width = Math.min(saved?.width || 1480, area.width);
  const height = Math.min(saved?.height || 940, area.height);
  const visible =
    saved &&
    Number.isFinite(saved.x) &&
    screen.getAllDisplays().some((d) => saved.x >= d.bounds.x - 40 && saved.y >= d.bounds.y - 40 && saved.x < d.bounds.x + d.bounds.width - 100 && saved.y < d.bounds.y + d.bounds.height - 100);
  return { width, height, ...(visible ? { x: saved.x, y: saved.y } : {}), maximized: Boolean(saved?.maximized) };
}

function themeBackground() {
  const t = store?.getConfig().theme;
  const dark = t === 'dark' || (t !== 'light' && nativeTheme.shouldUseDarkColors);
  return dark ? '#15181d' : '#fafafb';
}

function createWindow() {
  const b = boundsFromConfig();
  win = new BrowserWindow({
    width: b.width,
    height: b.height,
    x: b.x,
    y: b.y,
    minWidth: 900,
    minHeight: 560,
    title: 'Pilcrow',
    icon: path.join(ROOT, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon-256.png'),
    backgroundColor: themeBackground(),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });
  if (b.maximized) win.maximize();
  win.once('ready-to-show', () => win.show());

  const origin = new URL(server.url).origin;
  const wc = win.webContents;
  wc.setWindowOpenHandler(({ url }) => {
    if (/^(https?|mailto):/i.test(url) && !url.startsWith(origin)) shell.openExternal(url);
    return { action: 'deny' };
  });
  wc.on('will-navigate', (e, url) => {
    if (!url.startsWith(origin)) {
      e.preventDefault();
      if (/^(https?|mailto):/i.test(url)) shell.openExternal(url);
    }
  });
  wc.on('render-process-gone', (_e, details) => {
    if (details.reason !== 'clean-exit' && win && !win.isDestroyed()) setTimeout(() => win?.reload(), 500);
  });
  wc.on('context-menu', (_e, params) => contextMenu(params));

  const save = () => {
    if (!win || win.isDestroyed()) return;
    const maximized = win.isMaximized();
    const bounds = maximized ? win.getNormalBounds() : win.getBounds();
    store.setConfig({ window: { ...bounds, maximized } });
  };
  win.on('resize', debounce(save, 400));
  win.on('move', debounce(save, 400));
  win.on('focus', () => win?.flashFrame(false));
  win.on('close', (e) => {
    save();
    if (!quitting && !confirmQuit()) e.preventDefault();
  });
  win.on('closed', () => {
    win = null;
  });
  win.loadURL(server.url);
}

/** Ask before quitting while Claude is still working. Returns true when it is fine to quit. */
function confirmQuit() {
  const busy = server?.busy() || [];
  if (!busy.length || !win) return (quitting = true);
  const n = busy.length;
  const choice = dialog.showMessageBoxSync(win, {
    type: 'question',
    buttons: ['Quit', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    title: 'Quit Pilcrow?',
    message: `Claude is still working in ${n} session${n > 1 ? 's' : ''}.`,
    detail: `Quitting stops ${n > 1 ? 'them' : 'it'}. The conversations are saved and can be resumed later.`,
  });
  if (choice === 0) quitting = true;
  return quitting;
}

function contextMenu(params) {
  const items = [];
  const { editFlags, isEditable, selectionText, linkURL } = params;
  if (linkURL && /^(https?|mailto):/i.test(linkURL)) {
    items.push({ label: 'Open link', click: () => shell.openExternal(linkURL) }, { label: 'Copy link address', click: () => clipboard.writeText(linkURL) }, { type: 'separator' });
  }
  if (isEditable) {
    items.push({ role: 'undo', enabled: editFlags.canUndo }, { role: 'redo', enabled: editFlags.canRedo }, { type: 'separator' });
    items.push({ role: 'cut', enabled: editFlags.canCut }, { role: 'copy', enabled: editFlags.canCopy }, { role: 'paste', enabled: editFlags.canPaste }, { type: 'separator' }, { role: 'selectAll' });
  } else if (selectionText && selectionText.trim()) {
    items.push({ role: 'copy' });
  }
  while (items.length && items[items.length - 1].type === 'separator') items.pop();
  if (items.length && win) Menu.buildFromTemplate(items).popup({ window: win });
}

ipcMain.handle('pick-folder', async (_e, defaultPath) => {
  const r = await dialog.showOpenDialog(win, { properties: ['openDirectory', 'createDirectory'], defaultPath: defaultPath || app.getPath('home') });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.on('focus', () => {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
});
ipcMain.on('attention', () => {
  if (win && !win.isFocused()) win.flashFrame(true);
});

// Shortcuts the page handles itself are shown in the menu but not registered, so the page still gets the key.
const shown = (accelerator) => ({ accelerator, registerAccelerator: false });
const sendMenu = (command) => () => win?.webContents.send('menu', command);

function buildMenu() {
  return Menu.buildFromTemplate([
    {
      label: '&File',
      submenu: [
        { label: 'New Session…', ...shown('CmdOrCtrl+N'), click: sendMenu('new-session') },
        { label: 'Command Palette…', ...shown('CmdOrCtrl+K'), click: sendMenu('palette') },
        { type: 'separator' },
        { label: 'Settings…', ...shown('CmdOrCtrl+,'), click: sendMenu('settings') },
        { type: 'separator' },
        { label: 'Quit Pilcrow', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() },
      ],
    },
    {
      label: '&Edit',
      submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'pasteAndMatchStyle' }, { role: 'selectAll' }],
    },
    {
      label: '&View',
      submenu: [
        { label: 'Toggle Sidebar', ...shown('CmdOrCtrl+B'), click: sendMenu('toggle-sidebar') },
        { label: 'Toggle Side Panel', ...shown('CmdOrCtrl+\\'), click: sendMenu('toggle-panel') },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
      ],
    },
    { label: '&Window', submenu: [{ role: 'minimize' }, { label: 'Maximize', click: () => (win?.isMaximized() ? win.unmaximize() : win?.maximize()) }, { role: 'close' }] },
    {
      label: '&Help',
      submenu: [
        { label: 'Keyboard Shortcuts', ...shown('CmdOrCtrl+/'), click: sendMenu('shortcuts') },
        { label: 'Diagnostics', click: sendMenu('diagnostics') },
        { label: 'Open Logs Folder', click: () => shell.openPath(fs.existsSync(STATE_DIR) ? STATE_DIR : app.getPath('home')) },
        { type: 'separator' },
        {
          label: 'About Pilcrow',
          click: () => {
            const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
            dialog.showMessageBox(win, {
              type: 'info',
              title: 'About Pilcrow',
              icon: path.join(ROOT, 'assets', 'icon-128.png'),
              message: `Pilcrow ${pkg.version}`,
              detail: `A desktop workbench for Claude Code.\n\nElectron ${process.versions.electron}\nChromium ${process.versions.chrome}\nNode ${process.versions.node}\n\n${ROOT}`,
            });
          },
        },
      ],
    },
  ]);
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

app.on('before-quit', (e) => {
  if (!quitting && !confirmQuit()) e.preventDefault();
});
app.on('window-all-closed', () => {
  server?.stop();
  app.quit();
});
app.on('will-quit', () => server?.stop());
