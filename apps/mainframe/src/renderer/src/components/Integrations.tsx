import { useEffect, useState, type ReactNode } from 'react'
import { LuRefreshCw } from 'react-icons/lu'
import { Spinner } from './Loader'
import { message } from '../lib/ohm'

/* Settings › Integrations: the cloud backends the brain package can hand jobs to, and which
   backend does which job. The fields are ordinary settings (saved with the page's Save button);
   the status, Install and Sign in come from src/main/brain.js. */

type Tool = 'codex' | 'claude'
// Settings.tsx passes its own Field in, so these rows look and save exactly like the other sections
export type FieldFn = (
  key: string,
  label: string,
  hint: string,
  type: string,
  placeholder: string,
  options?: string[]
) => ReactNode

const BACKENDS = ['local', 'qwen', 'deepseek', 'openrouter', 'codex', 'claude']

// [role, label, what it's for, the brain package's default chain]
const ROLES = [
  [
    'CLASSIFY',
    'Classify',
    'Quick calls on each message, like whether Ohm should answer it',
    'local'
  ],
  ['EXTRACT', 'Extract', 'Pulls memories and facts about people out of conversations', 'local'],
  [
    'BRIEF',
    'Brief',
    'Writes the short note Ohm reads before replying: who’s here, what’s going on',
    'codex,local'
  ],
  ['CURATE', 'Curate', 'Suggests reply arrows and memories in the Curator', 'codex,local'],
  [
    'JUDGE',
    'Judge',
    'Scores LoRA replies; Codex here means Ohm stays online during checks',
    'codex,qwen'
  ],
  [
    'REFLECT',
    'Reflect',
    'Looks back over a finished conversation for traits, relationships and new memories',
    'codex,deepseek'
  ],
  ['LABEL', 'Label', 'Names things: threads, topics and training runs', 'codex']
] as const

/** Every key this section edits, so the nav can mark it when something isn't saved yet. */
export const INTEGRATION_KEYS = [
  ...ROLES.map(([role]) => `BRAIN_ROLE_${role}`),
  'BRAIN_CODEX_MODEL',
  'BRAIN_CODEX_REASONING',
  'BRAIN_CLAUDE_MODEL',
  'DEEPSEEK_API_KEY',
  'BRAIN_DEEPSEEK_MODEL',
  'OPENROUTER_API_KEY',
  'BRAIN_OPENROUTER_MODEL',
  'BRAIN_LOCAL_ONLY',
  'BRAIN_DAILY_USD'
]

const TONE = { good: 'bg-good', warn: 'bg-warn', bad: 'bg-bad', off: 'bg-[#4a4d55]' }
const Dot = ({ tone }: { tone: keyof typeof TONE }) => (
  <span className={`size-2 shrink-0 rounded-full ${TONE[tone]}`} />
)

const Integrations = ({
  field,
  values
}: {
  field: FieldFn
  values: Record<string, string | null>
}) => {
  const [status, setStatus] = useState<any>(null)
  const [checking, setChecking] = useState(false)

  // brain status takes a few seconds (it asks each CLI), so it runs on open and on Refresh only
  const refresh = () => {
    setChecking(true)
    Promise.resolve()
      .then(() => window.api.brainStatus())
      .then(setStatus)
      .catch((e) => setStatus({ error: message(e) }))
      .finally(() => setChecking(false))
  }
  useEffect(refresh, [])

  const s = status?.error ? null : status
  // A typo in a chain would quietly fall through to the next backend, so point it out
  const unknown = ROLES.flatMap(([role]) =>
    (values[`BRAIN_ROLE_${role}`] ?? '')
      .split(',')
      .map((b) => b.trim())
      .filter((b) => b && !BACKENDS.includes(b))
  )
  const keys = [
    ['DeepSeek', s?.deepseek?.key],
    ['OpenRouter', s?.openrouter?.key]
  ] as const

  return (
    <>
      <p className="mb-1.5 text-[12.5px] text-muted">
        Ohm picks these up when the engine restarts.
      </p>
      {status?.error && (
        <p className="mt-3 text-[12.5px] text-bad">Couldn’t check the backends: {status.error}</p>
      )}

      <ToolCard
        name="codex"
        title="Codex (ChatGPT subscription)"
        what="Runs jobs on your ChatGPT plan instead of the GPU, so Ohm keeps talking while they run."
        s={s?.codex}
        checking={checking}
        onRefresh={refresh}
      >
        {field('BRAIN_CODEX_MODEL', 'Model', '', 'text', 'default from your Codex config')}
        {field(
          'BRAIN_CODEX_REASONING',
          'Reasoning',
          'More is slower and uses more of the plan',
          'select',
          'medium',
          ['low', 'medium', 'high']
        )}
      </ToolCard>

      <ToolCard
        name="claude"
        title="Claude Code (Claude subscription)"
        what="The same, on your Claude plan. Put claude in a role’s chain to use it."
        s={s?.claude}
        checking={checking}
        onRefresh={refresh}
      >
        {field('BRAIN_CLAUDE_MODEL', 'Model', '', 'text', 'haiku')}
      </ToolCard>

      <Card
        tone={
          !s ? 'off' : keys.every(([, k]) => k) ? 'good' : keys.some(([, k]) => k) ? 'warn' : 'off'
        }
        title="API keys"
        state={
          s
            ? keys.map(([n, k]) => `${n} ${k ? 'set' : 'missing'}`).join(' · ')
            : checking
              ? 'Checking…'
              : ''
        }
        what="Pay-per-use backends, for when the subscriptions are busy or out. They count toward the daily budget."
      >
        {field('DEEPSEEK_API_KEY', 'DeepSeek key', 'From platform.deepseek.com', 'secret', '')}
        {field('BRAIN_DEEPSEEK_MODEL', 'DeepSeek model', '', 'text', 'deepseek-chat')}
        {field('OPENROUTER_API_KEY', 'OpenRouter key', 'From openrouter.ai/keys', 'secret', '')}
        {field(
          'BRAIN_OPENROUTER_MODEL',
          'OpenRouter model',
          '',
          'text',
          s?.openrouter?.model || 'the brain’s default'
        )}
      </Card>

      <h3 className="mt-8 mb-1 text-[14px] font-semibold">Who does what</h3>
      <p className="text-[12.5px] text-muted">
        Each job tries these backends in order and falls back to the next when one fails. Leave a
        row empty for the default. Backends: {BACKENDS.join(', ')}.
      </p>
      {unknown.length > 0 && (
        <p className="mt-2 text-[12.5px] text-warn">
          Not a backend: {[...new Set(unknown)].join(', ')}
        </p>
      )}
      {ROLES.map(([role, label, what, chain]) =>
        field(`BRAIN_ROLE_${role}`, label, what, 'text', chain)
      )}
      {field(
        'BRAIN_LOCAL_ONLY',
        'Local only',
        'Never call a cloud backend; every job runs here',
        'switch',
        ''
      )}
      {field(
        'BRAIN_DAILY_USD',
        'Daily API budget ($)',
        'Paid API calls stop for the day past this. Subscriptions don’t count',
        'number',
        ''
      )}
      <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
        {s && (
          <span>
            Spent today:{' '}
            <span className="font-mono text-[#c9cbd1]">${(s.spent_today_usd ?? 0).toFixed(2)}</span>
          </span>
        )}
        {s?.local && (
          <span className="inline-flex items-center gap-1.5">
            <Dot tone={s.local.up ? 'good' : 'bad'} /> Local model {s.local.up ? 'up' : 'down'}
          </span>
        )}
        {s?.qwen && (
          <span className="inline-flex items-center gap-1.5">
            <Dot tone={s.qwen.model_present ? 'good' : 'off'} /> Qwen judge{' '}
            {s.qwen.model_present ? 'downloaded' : 'not downloaded'}
          </span>
        )}
      </p>
    </>
  )
}

const Card = ({
  tone,
  title,
  state,
  what,
  actions = null,
  children
}: {
  tone: keyof typeof TONE
  title: string
  state: ReactNode
  what: string
  actions?: ReactNode
  children: ReactNode
}) => (
  <div className="panel mt-4 px-4 pt-3.5">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Dot tone={tone} />
      <h3 className="text-[14px] font-semibold">{title}</h3>
      <span className="text-xs text-muted">{state}</span>
      <span className="flex-1" />
      {actions}
    </div>
    <p className="mt-1 text-[12.5px] text-muted">{what}</p>
    {/* The last field's divider would sit right on the card's border */}
    <div className="mt-1 [&>div:last-child]:border-b-0">{children}</div>
  </div>
)

/** Codex or Claude Code: whether its CLI is installed and signed in, and buttons to fix either. */
const ToolCard = ({
  name,
  title,
  what,
  s,
  checking,
  onRefresh,
  children
}: {
  name: Tool
  title: string
  what: string
  s: { installed: boolean; version: string | null; signed_in: boolean } | undefined
  checking: boolean
  onRefresh: () => void
  children: ReactNode
}) => {
  const [installing, setInstalling] = useState(false)
  const [line, setLine] = useState('')

  useEffect(
    () => window.api.onInstallOutput?.((tool, text) => tool === name && setLine(text)),
    [name]
  )

  const install = async () => {
    setInstalling(true)
    setLine('Starting npm…')
    try {
      const r = await window.api.installTool(name)
      setLine(
        r.ok ? 'Installed. Sign in next.' : `Install failed: ${r.output.trim().split('\n').pop()}`
      )
    } catch (e) {
      setLine(message(e))
    }
    setInstalling(false)
    onRefresh()
  }
  const signIn = () =>
    window.api
      .openSignIn(name)
      .then(() => setLine('Finish signing in in the terminal window, then press Refresh.'))
      .catch((e) => setLine(message(e)))

  const tone = !s ? 'off' : !s.installed ? 'off' : s.signed_in ? 'good' : 'warn'
  const version = s?.version ? ` · ${s.version}` : ''
  const state = !s
    ? checking
      ? 'Checking…'
      : ''
    : !s.installed
      ? 'Not installed'
      : s.signed_in
        ? `Signed in${version}`
        : `Installed${version} · not signed in`

  return (
    <Card
      tone={tone}
      title={title}
      state={state}
      what={what}
      actions={
        <>
          <button className="btn h-8" disabled={installing} onClick={install}>
            {s?.installed ? 'Reinstall' : 'Install'}
          </button>
          <button
            className="btn h-8"
            disabled={installing || s?.installed === false}
            onClick={signIn}
          >
            Sign in
          </button>
          <button
            className="btn h-8 w-8 px-0"
            aria-label="Refresh"
            title="Check again"
            disabled={checking}
            onClick={onRefresh}
          >
            {checking ? <Spinner className="size-3.5" /> : <LuRefreshCw className="size-3.5" />}
          </button>
        </>
      }
    >
      {line && (
        <p className="mt-2 flex items-center gap-2 font-mono text-[11.5px] text-soft">
          {installing && <Spinner className="size-3.5" />}
          <span className="truncate" title={line}>
            {line}
          </span>
        </p>
      )}
      {children}
    </Card>
  )
}

export default Integrations
