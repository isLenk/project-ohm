import Datastore from 'nedb';

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
  logsDB.insert(logEntry, (err, newDoc) => {
    if (err) {
      console.error('Error adding log:', err)
    } else {
      console.log('Log added:', newDoc)
    }
  })
}

export { charactersDB, logsDB, addLog }
