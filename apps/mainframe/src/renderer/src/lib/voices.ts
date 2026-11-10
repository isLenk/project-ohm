// characters.voice: { engine: 'chatterbox', file } (no file: built-in) or { engine: 'kokoro', preset }.
// Old cards ({ file } or {}) are Chatterbox.

export type Engine = 'chatterbox' | 'kokoro'

export const ENGINES: { id: Engine; label: string; about: string }[] = [
  {
    id: 'chatterbox',
    label: 'Chatterbox',
    about:
      'Can sigh, laugh and groan, and copy a voice clip. Slower: about 1s before he starts talking while the LLM is busy.'
  },
  {
    id: 'kokoro',
    label: 'Kokoro',
    about:
      'Starts talking about 5x sooner and uses almost no GPU. Its own voices only, and no sighs or laughs.'
  }
]

// Keep in sync with apps/tts/kokoro_engine.py
const KOKORO = {
  af: [
    'heart',
    'alloy',
    'aoede',
    'bella',
    'jessica',
    'kore',
    'nicole',
    'nova',
    'river',
    'sarah',
    'sky'
  ],
  am: ['adam', 'echo', 'eric', 'fenrir', 'liam', 'michael', 'onyx', 'puck', 'santa'],
  bf: ['alice', 'emma', 'isabella', 'lily'],
  bm: ['daniel', 'fable', 'george', 'lewis']
}
const ACCENT = { af: 'American woman', am: 'American man', bf: 'British woman', bm: 'British man' }
export const KOKORO_DEFAULT = 'af_heart'

export const KOKORO_VOICES: { id: string; label: string }[] = Object.entries(KOKORO).flatMap(
  ([prefix, names]) =>
    names.map((n) => ({
      id: `${prefix}_${n}`,
      label: `${n[0].toUpperCase()}${n.slice(1)} (${ACCENT[prefix]})`
    }))
)

export const engineOf = (voice): Engine => (voice?.engine === 'kokoro' ? 'kokoro' : 'chatterbox')

// Clip ('' = built-in) or Kokoro preset
export const voiceName = (voice): string =>
  engineOf(voice) === 'kokoro' ? voice?.preset || KOKORO_DEFAULT : voice?.file || ''

export const sameVoice = (a, b) => engineOf(a) === engineOf(b) && voiceName(a) === voiceName(b)
