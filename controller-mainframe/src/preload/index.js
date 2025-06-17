import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
// Custom APIs for renderer
const api = {
  getModules: () => ipcRenderer.invoke('get-modules'),
  getCharacters: () => ipcRenderer.invoke('get-characters'),
  addCharacter: (character) => ipcRenderer.invoke('add-character', character),
  updateCharacter: (character) => ipcRenderer.invoke('update-character', character),
  deleteCharacter: (characterId) => ipcRenderer.invoke('delete-character', characterId),
  openDialog: (method, params) => {
    return ipcRenderer.invoke('dialog', method, params)
  },
  println: (message) => ipcRenderer.send('println', message),
  addLog: (log_type, message) => ipcRenderer.invoke('println', log_type, message),
  getLogs: async (count) => await ipcRenderer.invoke('get-logs', count)
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  window.electron = electronAPI
  window.api = api
}
