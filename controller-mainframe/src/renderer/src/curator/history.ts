import { useCallback, useRef } from 'react'

/** One undoable action. `run` does it and returns the step that reverses it, so undo and redo share code. */
export type Step = { label: string; run: () => Promise<Step | null> }

/** Undo/redo stacks for the curator (Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z). A new action clears redo. */
export function useHistory(onError: (e: unknown) => void, say: (msg: string) => void) {
  const undos = useRef<Step[]>([])
  const redos = useRef<Step[]>([])
  const busy = useRef(false)
  const cb = useRef({ onError, say })
  cb.current = { onError, say }

  const record = useCallback((s: Step) => {
    undos.current.push(s)
    redos.current = []
  }, [])

  const clear = useCallback(() => {
    undos.current = []
    redos.current = []
  }, [])

  const move = useCallback(async (from: { current: Step[] }, to: { current: Step[] }, verb: 'undo' | 'redo') => {
    if (busy.current) return // one at a time: steps depend on the one before
    const step = from.current.pop()
    if (!step) {
      cb.current.say(`Nothing to ${verb}`)
      return
    }
    busy.current = true
    try {
      const back = await step.run()
      if (back) to.current.push(back)
      cb.current.say(`${verb === 'undo' ? 'Undid' : 'Redid'} ${step.label}`)
    } catch (e) {
      cb.current.onError(e) // the step is dropped: the project changed under it and it no longer applies
    } finally {
      busy.current = false
    }
  }, [])

  const undo = useCallback(() => move(undos, redos, 'undo'), [move])
  const redo = useCallback(() => move(redos, undos, 'redo'), [move])
  return { record, clear, undo, redo }
}
