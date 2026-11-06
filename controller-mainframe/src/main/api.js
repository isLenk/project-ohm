import express from 'express'
import { addLog } from './db.js'
import axios from 'axios'

const api = express()
api.use(express.json())

export const modules = {}

/* Module
    POST /api/modules
    body {
      name: string,
      description: string,
      required: boolean,
    }
*/
api.post('/api/modules', (req, res) => {
  const { name, description, required } = req.body
  // Here you would typically save the module to a database
  if (modules[name]) {
    return res.status(409).json({ message: 'Module already exists' })
  }

  console.log(`Module added: ${name}, ${description}, ${required}`)
  modules[name] = {
    description,
    required
  }
  addLog('info', `New Module - ${name} (req=${required})`)
  res.status(201).json({ message: 'Module added successfully' })
})

api.put('/api/modules/:name', (req, res) => {
  const { name } = req.params
  const { description, required } = req.body
  // Here you would typically update the module in a database
  if (modules[name]) {
    modules[name] = {
      description,
      required
    }
    res.status(200).json({ message: 'Module updated successfully' })
  } else {
    res.status(404).json({ message: 'Module not found' })
  }
})

api.get('/api/modules/', (req, res) => {
  // Here you would typically fetch the modules from a database
  // Check that all required modules are present
  const missingRequiredModules = Object.entries(modules).filter(
    ([name, module]) => module.required && !module.version
  )
  if (missingRequiredModules.length > 0) {
    return res
      .status(400)
      .json({ ready: false, message: 'Missing required modules', missingRequiredModules })
  }

  return res.status(200).json({ ready: true, modules })
})

api.post('/api/send', (req, res) => {
  const { character, input } = req.body
  console.log(`Received input for character ${character}: ${input}`)

  return res.status(200).json({
    message: 'OK'
  })
})

/* Memory service on the NAS (memory_service/). Configure in controller-mainframe/.env:
    MAIN_VITE_MEMORY_API_URL=http://<nas-ip>:8100
    MAIN_VITE_MEMORY_API_KEY=<API_KEY from memory_service/.env>
*/
export const memoryApi = axios.create({
  baseURL: import.meta.env.MAIN_VITE_MEMORY_API_URL,
  headers: { 'X-API-Key': import.meta.env.MAIN_VITE_MEMORY_API_KEY || '' },
  timeout: 5000
})

/* Search memories for a character.
    POST /api/:character/memories?input=...
    The bot talks to the NAS directly now; this route is kept for the UI and older callers.
*/
api.post('/api/:character/memories', async (req, res) => {
  const { character } = req.params
  const { input } = req.query

  if (!character || !input) {
    return res.status(400).json({ message: 'Character and input are required' })
  }
  if (!memoryApi.defaults.baseURL) {
    return res.status(503).json({ message: 'MAIN_VITE_MEMORY_API_URL is not set' })
  }

  try {
    const { data } = await memoryApi.post('/memories/search', { character, text: String(input) })
    return res.status(200).json({ character, data })
  } catch (error) {
    console.error('Error fetching memories from NAS:', error.message)
    return res.status(502).json({ message: 'Memory service unavailable', character, data: [] })
  }
})

const API_PORT = 3001
api.listen(API_PORT, () => {
  console.log(`API server is running at http://localhost:${API_PORT}`)
})

export default api
