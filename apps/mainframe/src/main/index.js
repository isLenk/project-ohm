import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { modules, listModules, setModuleEnabled, restartModule } from './api.js'
import { logsDB, addLog } from './db.js'
import { listCharacters, addCharacter, updateCharacter, deleteCharacter } from './characters.js'
import { dialog } from 'electron/main'
import { startCurator, stopCurator, curatorConfig } from './curator.js'
import {
  engineStatus,
  engineOutput,
  onEngineChange,
  restartEngine,
  startEngine,
  stopEngine
} from './engine.js'
import { health } from './health.js'
import { nasCall } from './nas.js'
import { getSettings, saveSettings } from './settings.js'

function createWindow() {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    // No title bar: the page headers are the drag area (.app-titlebar in main.css) and the
    // system's minimise/maximise/close buttons are drawn over the top right, in the page's colours.
    titleBarStyle: 'hidden',
    ...(process.platform === 'darwin'
      ? {}
      : { titleBarOverlay: { color: '#111214', symbolColor: '#a0a3ab', height: 40 } }),
    backgroundColor: '#111214',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    },
    icon: __dirname + '/../../resources/icon.png'
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  // Windows groups taskbar buttons by this id. It matches appId in electron-builder.yml and the
  // id on the Ohm launcher shortcut, so the window joins the pinned launcher. Set directly rather
  // than through electronApp.setAppUserModelId, which swaps in electron.exe's path in dev.
  if (process.platform === 'win32') app.setAppUserModelId('com.ohm.app')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

  ipcMain.handle('get-modules', async (event) => modules)
  ipcMain.handle('list-modules', () => listModules())
  ipcMain.handle('set-module-enabled', (event, name, on) => setModuleEnabled(name, on))
  ipcMain.handle('restart-module', (event, name) => restartModule(name))

  // The Python side (main.py). The Engine panel also gets every change pushed to it.
  ipcMain.handle('engine-status', () => engineStatus())
  ipcMain.handle('engine-start', () => startEngine())
  ipcMain.handle('engine-stop', () => stopEngine())
  ipcMain.handle('engine-restart', () => restartEngine())
  ipcMain.handle('engine-output', () => engineOutput())
  onEngineChange((status) => {
    for (const window of BrowserWindow.getAllWindows())
      window.webContents.send('engine-changed', status)
  })
  app.on('will-quit', () => {
    // Leave a main.py that was started by hand alone
    if (!engineStatus().external) stopEngine()
  })

  ipcMain.handle('health', (event, days) => health(days))
  ipcMain.handle('nas', (event, method, path, body) => nasCall(method, path, body))
  saveSettings({}) // encrypts secrets saved in plain text by older versions
  ipcMain.handle('get-settings', () => getSettings())
  ipcMain.handle('save-settings', (event, values) => saveSettings(values))

  // Characters live on the NAS when its address is set (see characters.js).
  ipcMain.handle('get-characters', () => listCharacters())
  ipcMain.handle('add-character', (event, character) => addCharacter(character))
  ipcMain.handle('update-character', (event, character) => updateCharacter(character))
  ipcMain.handle('delete-character', (event, characterId) => deleteCharacter(characterId))

  ipcMain.handle('dialog', async (event, method, params) => {
    return dialog[method](params)
  })

  ipcMain.handle('println', (event, message) => {
    console.log(message)
    logsDB.insert({ message, timestamp: new Date() })
  })

  // Conversation Curator backend (apps/curator)
  startCurator()
  ipcMain.handle('curator-config', () => curatorConfig())
  app.on('will-quit', stopCurator)

  ipcMain.handle('add-log', (event, log_type, message) => {
    addLog(log_type, message)
  })

  ipcMain.handle('get-logs', async (event, count = 20) => {
    return new Promise((resolve, reject) => {
      logsDB
        .find({})
        .sort({ timestamp: -1 })
        .limit(count)
        .exec((err, docs) => {
          if (err) {
            reject(err)
          } else {
            resolve(docs)
          }
        })
    })
  })

  createWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
