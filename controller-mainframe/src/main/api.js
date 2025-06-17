import express from 'express'
import { QdrantClient } from '@qdrant/js-client-rest'
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

const bearer = '0608da5d28eb10cea2914f3de0f3ddba'
const endpoint = 'http://localhost:5000/v1/chat/completions'
const client = new QdrantClient({ host: '127.0.0.1', port: 6333 })

async function openai_fetch(body) {
  const response = await axios.post(endpoint, body, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${bearer}`
    }
  })
  if (response.status !== 200) {
    throw new Error(`Error: ${response.status} - ${response.statusText}`)
  }
  if (!response.data || !response.data.choices || response.data.choices.length === 0) {
    throw new Error('No choices found in response')
  }
  return response.data.choices[0].message.content
}

async function llm_call_get_memory_importance(input) {
  const body = {
    model: 'gpt-3.5-turbo',
    messages: [
      {
        role: 'system',
        content:
          "disable verbose mode. Determine if the following sentence is a memory/fact/something they like or not. Return only either 'yes' or 'no'."
      },
      {
        role: 'user',
        content: 'I like apples and i hate oranges'
      },
      {
        role: 'assistant',
        content: 'yes'
      },
      {
        role: 'user',
        content: 'Personally not a fan of rats, what about you?'
      },
      {
        role: 'assistant',
        content: 'no'
      },
      {
        role: 'user',
        content: input
      }
    ],
    temperature: 0.5,
    stream: false,
    max_tokens: 3
  }

  try {
    const result = await openai_fetch(body)
    return result
  } catch (error) {
    console.error('Error calling LLM:', error)
    throw new Error('Failed to call LLM')
  }
}

async function qdrant_get_collection(collectionName, vectorsize) {
  /* Fetches a collection from qdrant by name.
    If the collection does not exist, it will create it.
  */
  if (!collectionName) {
    throw new Error('Collection name is required')
  }
  try {
    const response = await client.getCollections()
    const collection = response.collections.find((collection) => collection.name === collectionName)
    if (!collection) {
      console.log(`Collection ${collectionName} does not exist, creating it...`)
      await client.createCollection(collectionName, {
        vectors: {
          size: vectorsize, // Adjust based on your embedding model
          distance: 'Cosine'
        },
        timeout: 6
      })
    }
    return collection
  } catch (error) {
    console.error('Error checking collection:', error)
    throw new Error('Failed to check collection existence')
  }
}

async function qdrant_get_relevant_memories(character, encoding, vectorsize) {
  try {
    const character_collection = `memories_${character}`
    const collection = await qdrant_get_collection(character_collection, vectorsize)
    if (!collection) {
      throw new Error(`Collection ${character_collection} does not exist`)
    }

    // Perform a semantic search in the collection
    // get the query vector for the input
    console.log('Encoding for input:', encoding)
    const response = await client.search(character_collection, {
      vector: encoding,
      limit: 4
    })

    return response
  } catch (error) {
    console.error('Error fetching relevant memories:', error)
    throw new Error('Failed to fetch relevant memories')
  }
}

async function qdrant_create_memory(character, input, encoding, vectorsize) {
  try {
    const character_collection = `memories_${character}`
    // pre: assert that the collection exists

    // Create a memory in the collection
    const memory = {
      value: input
    }

    await client.upsert(character_collection, {
      points: [
        {
          id: Date.now(), // Use a unique ID for the memory
          vector: encoding,
          payload: memory
        }
      ]
    })
    console.log(`Memory created for character ${character}:`, memory)
  } catch (error) {
    console.error('Error creating memory:', error)
    throw new Error('Failed to create memory')
  }
}

/*  NOTE:
    For sanity reasons while still in development, the 
    'character' parameter will always be 'ohm'
*/
api.post('/api/:character/memories', async (req, res) => {
  const { character } = req.params
  const { input } = req.query
  const { encoding, vectorsize } = req.body

  if (!character || !input) {
    return res.status(400).json({ message: 'Character and input are required' })
  }
  let relevant_memories = []

  try {
    console.log(`Fetching memory for character ${character}`)
    relevant_memories = await qdrant_get_relevant_memories(character, encoding, vectorsize)
    console.log(`Relevant memories for ${character}:`, relevant_memories)
  } catch (error) {}

  const call_response = await llm_call_get_memory_importance(input)
  console.log(`Memory importance for ${character}:`, call_response)
  if (!call_response) {
    return res.status(500).json({ message: 'Failed to fetch memory importance' })
  }

  if (call_response.toLowerCase() === 'yes') {
    // Here you would typically create a memory in a database or in-memory store
    await qdrant_create_memory(character, input, encoding, vectorsize)
    addLog('info', `Memory created for character ${character}: ${input}`)
  }

  // Here you would typically fetch the memory from a database or in-memory store
  const memory = {
    character,
    data: relevant_memories
  }

  return res.status(200).json(memory)
})

const API_PORT = 3001
api.listen(API_PORT, () => {
  console.log(`API server is running at http://localhost:${API_PORT}`)
})

export default api
