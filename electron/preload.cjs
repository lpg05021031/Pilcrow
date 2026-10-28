// Small bridge between the UI and the Electron shell. The page runs sandboxed; this is all it can reach.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pilcrowNative', {
  shell: 'electron',
  pickFolder: (defaultPath) => ipcRenderer.invoke('pick-folder', defaultPath),
  focus: () => ipcRenderer.send('focus'),
  attention: () => ipcRenderer.send('attention'),
  onMenu: (cb) => {
    const fn = (_e, command) => cb(command);
    ipcRenderer.on('menu', fn);
    return () => ipcRenderer.removeListener('menu', fn);
  },
});
