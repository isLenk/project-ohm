import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { modules } from './api.js'
import { logsDB, addLog } from './db.js'
import { listCharacters, addCharacter, updateCharacter, deleteCharacter } from './characters.js'
import { dialog } from 'electron/main'

function createWindow() {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
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
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

  ipcMain.handle('get-modules', async (event) => modules)

  // Characters live on the NAS when MAIN_VITE_MEMORY_API_URL is set (see characters.js).
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
