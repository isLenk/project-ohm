import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import PageHeader from '../components/PageHeader'

const TAG_COLOR = { update: 'text-accent', error: 'text-red-400' }
const time = (t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
// 'input' is what the character heard, 'output' is what it said.
const isChat = (log) => log.log_type === 'input' || log.log_type === 'output'

const Dashboard = () => {
  const [logs, setLogs] = useState([])
  const [modules, setModules] = useState({})
  const [active, setActive] = useState(null)
  const [tab, setTab] = useState('all')

  useEffect(() => {
    const load = () => {
      window.api.getLogs(60).then(setLogs).catch(console.error)
      window.api.getModules().then(setModules)
    }
    load()
    window.api
      .getCharacters()
      .then((list) => setActive(list.find((c) => c.status === 'Active')))
      .catch(console.error)
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  const chat = logs.filter(isChat).reverse()
  const system = logs.filter((l) => !isChat(l) && (tab === 'all' || l.log_type === tab))
  const names = Object.keys(modules)

  return (
    <>
      <PageHeader title="Dashboard" sub={`${names.length} module(s) connected`} />

      <div className="grid gap-5 lg:grid-cols-[2fr_1fr]">
        <section className="panel flex min-h-80 flex-col">
          <h2 className="border-b border-line px-5 py-4 font-semibold">Conversation</h2>
          <div className="flex max-h-[420px] flex-1 flex-col gap-3 overflow-y-auto p-5">
            {chat.length === 0 && <p className="m-auto text-sm text-muted">Nothing said yet.</p>}
            {chat.map((l) => {
              const said = l.log_type === 'output'
              return (
                <div
                  key={l._id}
                  className={`flex max-w-[80%] flex-col gap-1 ${said ? 'items-end self-end' : 'items-start'}`}
                >
                  <span className="text-xs text-muted">
                    {said ? (active?.name ?? 'Character') : 'User'} · {time(l.timestamp)}
                  </span>
                  <p
                    className={`rounded-xl px-3.5 py-2.5 text-sm ${
                      said ? 'rounded-br-sm bg-accent text-ink' : 'rounded-bl-sm bg-raised'
                    }`}
                  >
                    {l.message}
                  </p>
                </div>
              )
            })}
          </div>
        </section>

        <div className="flex flex-col gap-5">
          <section className="panel flex items-center gap-4 p-4">
            <div className="grid h-20 w-16 shrink-0 place-items-center rounded-lg border-2 border-accent bg-ink font-display text-3xl text-accent">
              {active?.name?.[0] ?? '?'}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted">Active character</p>
              <p className="truncate font-display text-xl">{active?.name ?? 'None'}</p>
            </div>
            <Link to="/characters" className="btn bg-raised">
              Change
            </Link>
          </section>

          <section className="panel flex-1 py-2">
            <h2 className="px-5 py-2.5 font-semibold">Modules</h2>
            {names.length === 0 && (
              <p className="px-5 pb-3 text-sm text-muted">None yet. Start the Python side.</p>
            )}
            {names.map((name) => (
              <div key={name} className="flex min-h-11 items-center gap-3 px-5">
                <span className="size-2.5 rounded-full bg-green-400" />
                <span className="flex-1 capitalize">{name}</span>
                {modules[name].required && <span className="text-xs text-muted">required</span>}
              </div>
            ))}
          </section>
        </div>
      </div>

      <section className="panel mt-5 py-2">
        <div className="flex items-center justify-between px-5 py-2.5">
          <h2 className="font-semibold">System log</h2>
          <div className="flex gap-1.5">
            {['all', 'update', 'error'].map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`min-h-8 cursor-pointer rounded-full border px-3 text-xs font-semibold capitalize ${
                  tab === t ? 'border-gray-100 bg-gray-100 text-ink' : 'border-line text-muted'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <ul className="max-h-64 overflow-y-auto font-mono text-[13px]">
          {system.map((l) => (
            <li key={l._id} className="flex gap-3.5 px-5 py-1.5">
              <span className="text-muted">{time(l.timestamp)}</span>
              <span className={`w-14 shrink-0 text-[11px] uppercase ${TAG_COLOR[l.log_type] ?? 'text-muted'}`}>
                {l.log_type ?? 'info'}
              </span>
              <span className="break-all text-gray-200">{l.message}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}

export default Dashboard
