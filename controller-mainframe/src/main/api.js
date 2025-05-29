import express from 'express'

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
    const missingRequiredModules = Object.entries(modules).filter(([name, module]) => module.required && !module.version)
    if (missingRequiredModules.length > 0) {
        return res.status(400).json({ ready: false, message: 'Missing required modules', missingRequiredModules })
    }

    return res.status(200).json({ ready: true, modules })
});

const API_PORT = 3001;
api.listen(API_PORT, () => {
  console.log(`API server is running at http://localhost:${API_PORT}`)
})

export default api