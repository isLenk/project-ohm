/* The memory service on the NAS (memory_service/), set in Settings › Memory.

   Read on every call so changing the address in Settings works without restarting the app.
   The renderer reaches it through the 'nas' IPC handler, so the API key stays in this process.
*/
import axios from 'axios'
import { setting } from './settings.js'

let client = null
let made = ''

/** An axios instance for the NAS, or null when no address is set. */
export function nas(timeout = 5000) {
  const url = setting('MEMORY_API_URL')
  if (!url) return null
  const key = `${url}|${setting('MEMORY_API_KEY')}|${timeout}`
  if (key !== made) {
    client = axios.create({
      baseURL: url,
      headers: { 'X-API-Key': setting('MEMORY_API_KEY') },
      timeout
    })
    made = key
  }
  return client
}

export const nasUrl = () => setting('MEMORY_API_URL')

/** Turn an axios failure into one sentence for the UI. */
export function nasError(error) {
  const detail = error.response?.data?.detail || error.message
  return new Error(`Memory service (${nasUrl()}): ${detail}`)
}

/** GET/POST/PATCH/DELETE for the renderer. */
export async function nasCall(method, path, body) {
  const api = nas()
  if (!api) throw new Error('Set the memory service address in Settings › Memory.')
  try {
    const { data } = await api.request({ method, url: path, data: body })
    return data
  } catch (e) {
    throw nasError(e)
  }
}
