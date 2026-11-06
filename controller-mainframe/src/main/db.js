import Datastore from 'nedb';
import axios from 'axios';

// Logs are also mirrored to the memory service on the NAS when it's configured.
const nasLogs = import.meta.env.MAIN_VITE_MEMORY_API_URL
  ? axios.create({
      baseURL: import.meta.env.MAIN_VITE_MEMORY_API_URL,
      headers: { 'X-API-Key': import.meta.env.MAIN_VITE_MEMORY_API_KEY || '' },
      timeout: 3000
    })
  : null

const charactersDB = new Datastore({
    filename: 'characters.db',
    autoload: true
});

const logsDB = new Datastore({
  filename: 'logs.db',
  autoload: true
})

function addLog(log_type, message) {
  console.log(`Adding log: ${log_type} - ${message}`)
  const logEntry = {
    log_type,
    message,
    timestamp: new Date()
  }
  nasLogs
    ?.post('/logs', { level: log_type, source: 'controller', message: String(message) })
    .catch((err) => console.error('Could not mirror log to NAS:', err.message))
  logsDB.insert(logEntry, (err, newDoc) => {
    if (err) {
      console.error('Error adding log:', err)
    } else {
      console.log('Log added:', newDoc)
    }
  })
}

export { charactersDB, logsDB, addLog }
