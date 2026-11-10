import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
// Custom APIs for renderer
const api = {
  getModules: () => ipcRenderer.invoke('get-modules'),
  getCharacters: () => ipcRenderer.invoke('get-characters'),
  addCharacter: (character) => ipcRenderer.invoke('add-character', character),
  updateCharacter: (character) => ipcRenderer.invoke('update-character', character),
  deleteCharacter: (characterId) => ipcRenderer.invoke('delete-character', characterId),
  listVoices: () => ipcRenderer.invoke('list-voices'),
  importVoice: (name, bytes) => ipcRenderer.invoke('import-voice', name, bytes),
  loadVoice: (voice) => ipcRenderer.invoke('load-voice', voice),
  openDialog: (method, params) => {
    return ipcRenderer.invoke('dialog', method, params)
  },
  println: (message) => ipcRenderer.send('println', message),
  addLog: (log_type, message) => ipcRenderer.invoke('println', log_type, message),
  getLogs: async (count) => await ipcRenderer.invoke('get-logs', count),
  getSearches: (character, count) => ipcRenderer.invoke('get-searches', character, count),
  curatorConfig: () => ipcRenderer.invoke('curator-config'),

  listModules: () => ipcRenderer.invoke('list-modules'),
  setModuleEnabled: (name, on) => ipcRenderer.invoke('set-module-enabled', name, on),
  restartModule: (name) => ipcRenderer.invoke('restart-module', name),

  engineStatus: () => ipcRenderer.invoke('engine-status'),
  startEngine: () => ipcRenderer.invoke('engine-start'),
  stopEngine: () => ipcRenderer.invoke('engine-stop'),
  restartEngine: () => ipcRenderer.invoke('engine-restart'),
  engineOutput: () => ipcRenderer.invoke('engine-output'),
  onEngineChange: (fn) => {
    const listener = (event, status) => fn(status)
    ipcRenderer.on('engine-changed', listener)
    return () => ipcRenderer.removeListener('engine-changed', listener)
  },

  health: (days) => ipcRenderer.invoke('health', days),
  nas: (method, path, body) => ipcRenderer.invoke('nas', method, path, body),
  getSettings: () => ipcRenderer.invoke('get-settings'),
  saveSettings: (values) => ipcRenderer.invoke('save-settings', values),

  brainStatus: () => ipcRenderer.invoke('brain-status'),
  installTool: (name) => ipcRenderer.invoke('brain-install', name),
  openSignIn: (name) => ipcRenderer.invoke('brain-sign-in', name),
  onInstallOutput: (fn) => {
    const listener = (event, name, line) => fn(name, line)
    ipcRenderer.on('brain-install-output', listener)
    return () => ipcRenderer.removeListener('brain-install-output', listener)
  }
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
