import { useEffect, useRef, useState } from 'react'
import { LuEye, LuEyeOff, LuFolderOpen, LuLock } from 'react-icons/lu'
import { dialog } from '../curator/api'
import { message, useEngine } from '../lib/ohm'

/* Each field is an environment variable the Python side or the curator service reads.
   Saved values go to settings.json and are passed to main.py and curator_service when they start.
   type: text | secret | folder | file | switch (value '1' when on) */
const SECTIONS = [
  {
    id: 'runtime',
    title: 'Model runtime',
    sub: 'Where models run. Which model each character uses is set on its card.',
    fields: [
      [
        'LLM_URL',
        'LLM server',
        'OpenAI-compatible endpoint the bot talks to',
        'text',
        'http://127.0.0.1:5000/v1'
      ],
      [
        'LLAMA_CPP_DIR',
        'llama.cpp folder',
        'Holds bin/llama-server, used by Load in the Engine panel',
        'folder',
        '<repo>/../llama.cpp'
      ],
      [
        'CURATOR_MODELS_DIR',
        'Model library',
        'Where imported .gguf models are kept',
        'folder',
        '<llama.cpp>/models'
      ]
    ]
  },
  {
    id: 'discord',
    title: 'Discord',
    sub: 'The bot account Ohm talks through.',
    fields: [['DISCORD_TOKEN', 'Bot token', 'From the Discord developer portal', 'secret', '']]
  },
  {
    id: 'voice',
    title: 'Voice',
    sub: 'Speech in and out of voice calls. Turn speech to text and text to speech on or off in Modules.',
    fields: [
      [
        'TTS_URL',
        'Text-to-speech server',
        'tts_api/, started with _run_tts_api.cmd',
        'text',
        'http://127.0.0.1:8000'
      ]
    ]
  },
  {
    id: 'memory',
    title: 'Memory',
    sub: 'The memory service on the NAS: characters, memories, the message log and the dashboard’s numbers.',
    fields: [
      ['MEMORY_API_URL', 'Memory service', 'http://<nas-ip>:8100', 'text', ''],
      ['MEMORY_API_KEY', 'API key', 'API_KEY from memory_service/.env', 'secret', '']
    ]
  },
  {
    id: 'curator',
    title: 'Curator & training',
    sub: 'The service that threads imported chats and trains LoRAs. Changes here apply when the app restarts.',
    fields: [
      ['CURATOR_PORT', 'Port', '', 'text', '8200'],
      [
        'CURATOR_DATA_DIR',
        'Data folder',
        'Model registry, adapters and training runs',
        'folder',
        'app data/ohm-curator'
      ],
      [
        'CURATOR_LLM_URL',
        'Model for suggestions',
        'Used to guess reply arrows and pull out memories',
        'text',
        'http://127.0.0.1:5000/v1'
      ],
      ['CURATOR_PYTHON', 'Python', 'The repo’s venv is used when empty', 'file', ''],
      [
        'CURATOR_TRAIN_VRAM_GB',
        'Training VRAM budget (GB)',
        'Free VRAM a training run needs',
        'text',
        '11'
      ],
      [
        'CURATOR_TRAIN_SIMULATE',
        'Simulate training',
        'Fake runs, for testing without a GPU',
        'switch',
        ''
      ]
    ]
  }
] as const

const Settings = () => {
  const engine = useEngine()
  const [values, setValues] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [locked, setLocked] = useState<string[]>([])
  const [file, setFile] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [section, setSection] = useState('runtime')
  const refs = useRef<Record<string, HTMLElement | null>>({})

  const load = (s) => {
    setValues(s.values)
    setSaved(s.values)
    setLocked(s.locked)
    setFile(s.file)
  }
  useEffect(() => {
    window.api.getSettings().then(load)
  }, [])

  const dirty = Object.keys(values).some((k) => values[k] !== saved[k])
  const set = (key: string, value: string) => setValues({ ...values, [key]: value })

  const save = async (restart: boolean) => {
    try {
      load(await window.api.saveSettings(values))
      if (restart && engine?.state === 'running') {
        await window.api.restartEngine()
        setNote('Saved. The engine restarted with the new settings.')
      } else
        setNote(engine?.state === 'running' ? 'Saved. Restart the engine to use them.' : 'Saved.')
      setError('')
    } catch (e) {
      setError(message(e))
    }
  }

  const jump = (id: string) => {
    setSection(id)
    refs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="flex min-h-full flex-wrap">
      <nav
        aria-label="Settings sections"
        className="max-w-[250px] flex-[1_1_220px] border-r border-line"
      >
        <div className="flex h-14 items-center border-b border-line px-5">
          <h1 className="text-[15px] font-semibold">Settings</h1>
        </div>
        <div className="py-2">
          {[...SECTIONS, { id: 'app', title: 'App' }].map((s) => (
            <button
              key={s.id}
              onClick={() => jump(s.id)}
              className={`flex h-9.5 w-full cursor-pointer items-center px-5 text-left text-[13.5px] ${
                section === s.id
                  ? 'bg-raised text-gray-100 shadow-[inset_2px_0_0_#ececee]'
                  : 'text-soft hover:bg-[#17181a] hover:text-gray-100'
              }`}
            >
              {s.title}
            </button>
          ))}
        </div>
        <div className="px-5">
          <p className="mt-2 border-t border-line pt-3.5 text-xs leading-relaxed text-muted">
            Each field maps to an environment variable. One that’s already set in the environment
            wins and shows as locked.
          </p>
        </div>
      </nav>

      <main className="flex min-w-0 flex-[999_1_520px] flex-col">
        <header className="sticky top-0 z-10 flex min-h-14 flex-wrap items-center gap-3 border-b border-line bg-page px-8 py-2.5">
          <span className={`text-[12.5px] ${error ? 'text-bad' : 'text-muted'}`}>
            {error || note || 'Changes apply the next time the Python side starts.'}
          </span>
          <span className="flex-1" />
          <button className="btn" disabled={!dirty} onClick={() => setValues(saved)}>
            Discard
          </button>
          <button className="btn btn-primary" disabled={!dirty} onClick={() => save(true)}>
            {engine?.state === 'running' ? 'Save & restart' : 'Save'}
          </button>
        </header>

        <div className="px-8 pb-14">
          <div className="max-w-[840px]">
            {SECTIONS.map((s) => (
              <section
                key={s.id}
                id={s.id}
                ref={(el) => (refs.current[s.id] = el)}
                className="scroll-mt-16 pt-7"
              >
                <h2 className="mb-1 text-[15px] font-semibold">{s.title}</h2>
                <p className="mb-1.5 text-[12.5px] text-muted">{s.sub}</p>
                {s.fields.map(([key, label, hint, type, placeholder]) => (
                  <Field
                    key={key}
                    name={key}
                    label={label}
                    hint={hint}
                    type={type}
                    placeholder={placeholder}
                    value={values[key] ?? ''}
                    locked={locked.includes(key)}
                    onChange={(v) => set(key, v)}
                  />
                ))}
              </section>
            ))}
            <section id="app" ref={(el) => (refs.current.app = el)} className="scroll-mt-16 pt-7">
              <h2 className="mb-1 text-[15px] font-semibold">App</h2>
              <p className="mb-1.5 text-[12.5px] text-muted">This window.</p>
              <Row label="Dashboard refresh" hint="How often logs and modules are polled">
                <span className="justify-self-end font-mono text-[12.5px] text-[#c9cbd1]">
                  every 3 s
                </span>
              </Row>
              <Row label="Settings file" hint="Where these values are saved">
                <span
                  className="justify-self-end truncate font-mono text-xs text-muted"
                  title={file}
                >
                  {file}
                </span>
              </Row>
            </section>
          </div>
        </div>
      </main>
    </div>
  )
}

const Row = ({ label, hint, children, htmlFor = undefined }) => (
  <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,340px)] items-center gap-x-8 gap-y-2 border-b border-[#1c1d20] py-3">
    <label htmlFor={htmlFor} className="flex flex-col gap-1 text-[13.5px]">
      {label}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
    {children}
  </div>
)

const Field = ({ name, label, hint, type, placeholder, value, locked, onChange }) => {
  const [show, setShow] = useState(false)
  const browse = async () => {
    const r = await dialog('showOpenDialog', {
      properties: [type === 'folder' ? 'openDirectory' : 'openFile']
    })
    if (!r.canceled) onChange(r.filePaths[0])
  }
  const note = (
    <span className="flex items-center gap-2 font-mono text-[11px] text-muted">
      {name}
      {locked && (
        <span
          className="inline-flex items-center gap-1 text-warn"
          title="Set in the environment, so it can't be changed here"
        >
          <LuLock className="size-3" /> from environment
        </span>
      )}
    </span>
  )

  if (type === 'switch') {
    const on = value === '1'
    return (
      <Row
        label={label}
        hint={
          <>
            {hint}
            {note}
          </>
        }
      >
        <button
          role="switch"
          aria-checked={on}
          aria-label={label}
          disabled={locked}
          onClick={() => onChange(on ? '' : '1')}
          className={`relative h-5.5 w-10 cursor-pointer justify-self-end rounded-full disabled:opacity-50 ${on ? 'bg-accent' : 'bg-[#3a3c42]'}`}
        >
          <span
            className={`absolute top-[3px] size-4 rounded-full bg-white ${on ? 'left-[21px]' : 'left-[3px]'}`}
          />
        </button>
      </Row>
    )
  }

  return (
    <Row
      label={label}
      hint={
        <>
          {hint && <span>{hint}</span>}
          {note}
        </>
      }
      htmlFor={name}
    >
      <span className="flex gap-1.5">
        <input
          id={name}
          className="field font-mono text-[12.5px] disabled:opacity-60"
          type={type === 'secret' && !show ? 'password' : 'text'}
          placeholder={placeholder}
          value={value}
          disabled={locked}
          onChange={(e) => onChange(e.target.value)}
        />
        {type === 'secret' && (
          <button
            className="btn w-9 shrink-0 px-0"
            aria-label={show ? 'Hide' : 'Show'}
            onClick={() => setShow(!show)}
          >
            {show ? <LuEyeOff /> : <LuEye />}
          </button>
        )}
        {(type === 'folder' || type === 'file') && (
          <button
            className="btn w-9 shrink-0 px-0"
            aria-label="Browse"
            title="Browse"
            disabled={locked}
            onClick={browse}
          >
            <LuFolderOpen />
          </button>
        )}
      </span>
    </Row>
  )
}

export default Settings
