import Datastore from 'nedb'
import { nas } from './nas.js'

const charactersDB = new Datastore({
  filename: 'characters.db',
  autoload: true
})

const logsDB = new Datastore({
  filename: 'logs.db',
  autoload: true
})

// What the engine and each module did and when ({ kind, name, event, at }), for uptime and restarts.
const eventsDB = new Datastore({
  filename: 'events.db',
  autoload: true
})
eventsDB.ensureIndex({ fieldName: 'at' })

// Every web search Ohm made, posted by the engine (utils/WebSearch.py), for the dashboard's Web search tab.
// { character, said, query, results: [{ title, body, href }], error, ms, at }
const searchesDB = new Datastore({
  filename: 'searches.db',
  autoload: true
})
searchesDB.ensureIndex({ fieldName: 'at' })
const SEARCH_DAYS = 30 // older searches are dropped when the app starts
searchesDB.remove({ at: { $lt: new Date(Date.now() - SEARCH_DAYS * 86400000) } }, { multi: true })

function addLog(log_type, message) {
  console.log(`Adding log: ${log_type} - ${message}`)
  const logEntry = {
    log_type,
    message,
    timestamp: new Date()
  }
  // Logs are also mirrored to the memory service on the NAS when it's configured.
  nas(3000)
    ?.post('/logs', { level: log_type, source: 'controller', message: String(message) })
    .catch((err) => console.error('Could not mirror log to NAS:', err.message))
  logsDB.insert(logEntry, (err) => {
    if (err) console.error('Error adding log:', err)
  })
}

/** kind: 'engine' | 'module'. event: start | stop | crash | restart | up | down | off */
function addEvent(kind, name, event) {
  eventsDB.insert({ kind, name, event, at: new Date() }, (err) => {
    if (err) console.error('Error adding event:', err)
  })
}

const find = (db, query, sort = { at: 1 }) =>
  new Promise((resolve, reject) =>
    db
      .find(query)
      .sort(sort)
      .exec((err, docs) => (err ? reject(err) : resolve(docs)))
  )

export { charactersDB, logsDB, eventsDB, searchesDB, addLog, addEvent, find }
