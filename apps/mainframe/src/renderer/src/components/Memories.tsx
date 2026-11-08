import { useState } from 'react'
import { LuBookA, LuCheck, LuGlobe, LuPencil, LuPlus, LuTrash2, LuUser, LuX } from 'react-icons/lu'
import { Avatar } from '../curator/Avatar'
import { fmtDay, fmtTime, message, nas, personColor, useCharacters, usePoll } from '../lib/ohm'

/* Everything the memory service knows, in three scopes:
   - Character: lore about the character itself (lore table, one character)
   - General knowledge: facts every character knows (knowledge table)
   - Terminology: slang, names and typos from the Curator's General terminology (glossary table). Read-only
     here: each Curator project replaces its set whenever its terms change, so edits belong there
   - People: what was remembered about each person (memories involving them, and user_facts), and every name
     they go by (users.aliases). A message naming them gets these in the prompt (controller/memory.py people_in)
   All of it but the terminology is recalled into prompts by /memories/search; terms that come up in a message
  are added by controller/memory.py glossary_for. */

type Scope =
  | { kind: 'character' }
  | { kind: 'knowledge' }
  | { kind: 'glossary' }
  | { kind: 'person'; id: string; name: string }
type Item = { id: number; text: string; meta: string; when: string }
type GlossaryRow = { term: string; definition: string | null; relation: 'typo' | 'same' | null; related_term: string | null; source: string; updated_at: string }

/** One line per term (the newest if two Curator projects define it), with what it means spelled out. */
function glossaryLines(rows: GlossaryRow[]) {
  const defs = new Map<string, string>()
  for (const r of rows) if (r.definition && !defs.has(r.term)) defs.set(r.term, r.definition)
  const seen = new Set<string>()
  return rows
    .filter((r) => !seen.has(r.term) && !!seen.add(r.term))
    .map((r) => {
      if (!r.related_term) return { term: r.term, meaning: r.definition ?? '', when: r.updated_at }
      const of = defs.get(r.related_term)
      const how = r.relation === 'typo' ? 'Typo of' : 'Same as'
      return { term: r.term, meaning: `${how} “${r.related_term}”${of ? `: ${of}` : ''}`, when: r.updated_at }
    })
}

const Memories = () => {
  const { list: characters, active } = useCharacters()
  const [pick, setPick] = useState<string | null>(null)
  const [scope, setScope] = useState<Scope>({ kind: 'character' })
  const [tab, setTab] = useState<'memories' | 'facts'>('memories')
  const [filter, setFilter] = useState('')
  const [termFilter, setTermFilter] = useState('')
  const [tick, setTick] = useState(0)
  const [error, setError] = useState('')
  const character = characters?.find((c) => c.id === pick) ?? active
  const slug = character?.id

  const people = usePoll(
    () => (slug ? nas.get(`/people?character=${slug}&limit=500`) : Promise.resolve([])),
    30000,
    [slug, tick]
  )
  const counts = usePoll(
    () =>
      slug
        ? Promise.all([
            nas.get(`/lore?character=${slug}`),
            nas.get('/knowledge'),
            // An older memory service has no /glossary.
            nas.get('/glossary').catch(() => [])
          ])
        : Promise.resolve([[], [], []]),
    30000,
    [slug, tick]
  )

  const items = usePoll<Item[]>(
    async () => {
      if (!slug || scope.kind === 'glossary') return []
      if (scope.kind === 'character') {
        return (await nas.get(`/lore?character=${slug}`)).map((r) => ({
          id: r.id,
          text: r.content,
          meta: r.title || 'lore',
          when: r.created_at
        }))
      }
      if (scope.kind === 'knowledge') {
        return (await nas.get('/knowledge')).map((r) => ({
          id: r.id,
          text: r.content,
          meta: r.title || 'knowledge',
          when: r.created_at
        }))
      }
      if (tab === 'facts') {
        return (await nas.get(`/users/${scope.id}/facts?character=${slug}`)).map((r) => ({
          id: r.id,
          text: r.fact,
          meta: 'fact',
          when: r.created_at
        }))
      }
      return (await nas.get(`/memories?character=${slug}&user_id=${scope.id}`)).map((r) => ({
        id: r.id,
        text: r.content,
        meta: r.kind,
        when: r.created_at
      }))
    },
    30000,
    [slug, scope, tab, tick]
  )

  // Where adding, editing and forgetting go for the scope that's showing
  const paths = () => {
    if (scope.kind === 'character')
      return {
        add: [`/lore?character=${slug}`, (t) => ({ content: t })],
        one: (id) => `/lore/${id}`,
        body: (t) => ({ content: t })
      }
    if (scope.kind === 'knowledge')
      return {
        add: ['/knowledge', (t) => ({ content: t })],
        one: (id) => `/knowledge/${id}`,
        body: (t) => ({ content: t })
      }
    if (tab === 'facts') {
      return {
        add: [`/users/${scope.id}/facts`, (t) => ({ character: slug, fact: t })],
        one: (id) => `/users/${scope.id}/facts/${id}`,
        body: (t) => ({ content: t })
      }
    }
    return {
      add: [
        '/memories',
        (t) => ({
          character: slug,
          content: t,
          user_ids: [scope.id],
          kind: 'fact',
          importance: 0.6
        })
      ],
      one: (id) => `/memories/${id}`,
      body: (t) => ({ content: t })
    }
  }

  const act = (fn: () => Promise<any>) =>
    fn()
      .then(() => (setError(''), setTick((t) => t + 1)))
      .catch((e) => setError(message(e)))
  const add = (text: string) =>
    act(() => {
      const [path, body] = paths().add as [string, (t: string) => any]
      return nas.post(path, body(text))
    })
  const save = (id: number, text: string) =>
    act(() => nas.patch(paths().one(id), paths().body(text)))
  const forget = (id: number) => act(() => nas.del(paths().one(id)))

  const shown = (people.data ?? []).filter((p) =>
    [p.name, p.username, ...(p.aliases ?? [])].some((n) => n?.toLowerCase().includes(filter.toLowerCase()))
  )
  const person = scope.kind === 'person' ? people.data?.find((p) => p.user_id === scope.id) : undefined
  // Replaces the whole list. The bot picks it up within 5 minutes (controller/memory.py PEOPLE_TTL)
  const saveAliases = (aliases: string[]) =>
    act(() => nas.put(`/users/${person.user_id}`, { aliases }))
  const [lore, knowledge, glossaryRows] = counts.data ?? [[], [], []]
  const glossary = glossaryLines(glossaryRows ?? [])
  const glossaryShown = glossary.filter(
    (g) => g.term.includes(termFilter.toLowerCase()) || g.meaning.toLowerCase().includes(termFilter.toLowerCase())
  )
  const title =
    scope.kind === 'character'
      ? [
          character?.name ?? 'Character',
          'Character memory',
          'What the character knows about itself. Kept per character, so playing another card switches it.'
        ]
      : scope.kind === 'knowledge'
        ? [
            'General knowledge',
            'Shared by every character',
            'Facts about the server, games and the world that aren’t about one person.'
          ]
        : scope.kind === 'glossary'
          ? [
              'Terminology',
              'Shared by every character',
              'Slang, names and typos people use, from the Curator’s General terminology. When one comes up in a message, its meaning goes into the prompt. Edit them in the Curator; it replaces this list whenever they change.'
            ]
          : [
              scope.name,
              'Person',
              'What was remembered about this person. When a message uses any of their names, this goes into the prompt.'
            ]
  const scopeBtn = (on: boolean) =>
    `flex h-11 w-full cursor-pointer items-center gap-3 px-5 text-left text-[13.5px] ${on ? 'bg-raised text-gray-100 shadow-[inset_2px_0_0_#ececee]' : 'text-[#c9cbd1] hover:bg-[#17181a] hover:text-gray-100'}`

  return (
    <div className="flex min-h-full flex-wrap">
      <nav
        aria-label="Memory scopes"
        className="flex max-w-[300px] flex-[1_1_240px] flex-col border-r border-line"
      >
        <div className="flex h-14 items-center gap-2 border-b border-line px-5">
          <h1 className="flex-1 text-[15px] font-semibold">Memories</h1>
          {characters && characters.length > 1 && (
            <select
              aria-label="Character"
              value={slug ?? ''}
              onChange={(e) => (setPick(e.target.value), setScope({ kind: 'character' }))}
              className="h-7 cursor-pointer rounded-md border border-edge bg-transparent px-1.5 text-xs [&>option]:bg-panel"
            >
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <p className="px-5 pt-4 pb-1 text-[12.5px] leading-relaxed text-muted">
          Everything {character?.name ?? 'the character'} remembers, stored on the NAS.
        </p>

        <div className="px-5 pt-4.5 pb-1.5 text-[11.5px] font-medium text-muted">
          {character?.name ?? 'Character'}
        </div>
        <button
          className={scopeBtn(scope.kind === 'character')}
          onClick={() => setScope({ kind: 'character' })}
        >
          <LuUser className="size-4 text-clay" />
          <span className="flex-1">Character</span>
          <span className="font-mono text-[11.5px] text-muted">{lore.length}</span>
        </button>
        <button
          className={scopeBtn(scope.kind === 'knowledge')}
          onClick={() => setScope({ kind: 'knowledge' })}
        >
          <LuGlobe className="size-4 text-soft" />
          <span className="flex-1">General knowledge</span>
          <span className="font-mono text-[11.5px] text-muted">{knowledge.length}</span>
        </button>
        <button
          className={scopeBtn(scope.kind === 'glossary')}
          onClick={() => setScope({ kind: 'glossary' })}
        >
          <LuBookA className="size-4 text-soft" />
          <span className="flex-1">Terminology</span>
          <span className="font-mono text-[11.5px] text-muted">{glossary.length}</span>
        </button>

        <div className="px-5 pt-5.5 pb-1.5 text-[11.5px] font-medium text-muted">People</div>
        <div className="px-5 pb-2.5">
          <input
            className="field"
            placeholder="Filter people"
            aria-label="Filter people"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-auto pb-4">
          {shown.map((p) => (
            <button
              key={p.user_id}
              className={scopeBtn(scope.kind === 'person' && scope.id === p.user_id)}
              onClick={() => setScope({ kind: 'person', id: p.user_id, name: p.name })}
            >
              <Avatar who={p} tint={personColor(p.user_id)} size={20} />
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              <span className="font-mono text-[11.5px] text-muted">{+p.facts + +p.memories}</span>
            </button>
          ))}
          {people.error && <p className="px-5 text-xs text-bad">{people.error}</p>}
        </div>
      </nav>

      <main className="flex min-w-0 flex-[999_1_520px] flex-col">
        <header className="app-titlebar flex min-h-14 flex-wrap items-center gap-3 border-b border-line px-8 py-2.5">
          <h2 className="text-[15px] font-semibold">{title[0]}</h2>
          <span className="text-[12.5px] text-muted">{title[1]}</span>
        </header>
        <div className="px-8 pb-12">
          <div className="max-w-[860px]">
            <p className="mt-4.5 text-[13px] leading-relaxed text-soft">{title[2]}</p>
            {person && <Names key={person.user_id} person={person} onSave={saveAliases} />}
            {scope.kind === 'person' && (
              <div
                role="tablist"
                aria-label="Memory type"
                className="mt-3 flex gap-5 border-b border-line"
              >
                <button
                  role="tab"
                  aria-selected={tab === 'memories'}
                  className="tab"
                  onClick={() => setTab('memories')}
                >
                  Memories
                </button>
                <button
                  role="tab"
                  aria-selected={tab === 'facts'}
                  className="tab"
                  onClick={() => setTab('facts')}
                >
                  Facts
                </button>
              </div>
            )}
            {(error || items.error) && (
              <p className="mt-3 text-[12.5px] text-bad">{error || items.error}</p>
            )}
            {scope.kind === 'glossary' ? (
              <>
                {counts.error && <p className="mt-3 text-[12.5px] text-bad">{counts.error}</p>}
                <div className="mt-5 border-b border-line pb-4">
                  <input
                    className="field"
                    placeholder={`Search ${glossary.length} words and meanings`}
                    aria-label="Search terminology"
                    value={termFilter}
                    onChange={(e) => setTermFilter(e.target.value)}
                  />
                </div>
                {glossaryShown.map((g) => (
                  <div key={g.term} className="grid grid-cols-[minmax(120px,auto)_minmax(0,1fr)] gap-x-6 border-b border-line py-3">
                    <div className="text-[14.5px] font-semibold">{g.term}</div>
                    <div className="text-[14px] leading-snug text-soft">{g.meaning}</div>
                  </div>
                ))}
                {glossary.length === 0 && (
                  <p className="py-4 text-[13px] text-muted">
                    Nothing yet. Define or approve words in the Curator’s General terminology.
                  </p>
                )}
              </>
            ) : (
              <AddRow onAdd={add} disabled={!slug} />
            )}
            {scope.kind !== 'glossary' && (items.data ?? []).map((m) => (
              <Row
                key={m.id}
                item={m}
                onSave={(t) => save(m.id, t)}
                onForget={() => forget(m.id)}
              />
            ))}
            {scope.kind !== 'glossary' && items.data?.length === 0 && (
              <p className="py-4 text-[13px] text-muted">Nothing here yet.</p>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

/** Every name the person goes by. Discord's are filled in by the bot; the rest (a real name, nicknames)
    are typed in here, and a message using any of them brings up what's remembered about the person. */
const Names = ({ person, onSave }: { person: any; onSave: (aliases: string[]) => void }) => {
  const [text, setText] = useState('')
  const aliases: string[] = person.aliases ?? []
  const discord = [person.display_name, person.username && `@${person.username}`].filter(Boolean)
  const add = (e) => {
    e.preventDefault()
    const name = text.trim().replace(/\s+/g, ' ')
    if (!name) return
    if (!aliases.some((a) => a.toLowerCase() === name.toLowerCase())) onSave([...aliases, name])
    setText('')
  }
  const chip = 'flex h-7 items-center gap-1 rounded border border-edge px-2 text-[12.5px]'
  return (
    <div className="mt-4 border-b border-line pb-4">
      <div className="mb-2 text-[11.5px] font-medium text-muted">Also called</div>
      <div className="flex flex-wrap items-center gap-2">
        {discord.map((n) => (
          <span key={n} className={`${chip} text-muted`} title="From Discord">
            {n}
          </span>
        ))}
        {aliases.map((a) => (
          <span key={a} className={`${chip} text-gray-100`}>
            {a}
            <button
              className="grid size-5 cursor-pointer place-items-center rounded text-muted hover:text-gray-100"
              aria-label={`Remove ${a}`}
              title="Remove"
              onClick={() => onSave(aliases.filter((x) => x !== a))}
            >
              <LuX className="size-3.5" />
            </button>
          </span>
        ))}
        <form onSubmit={add} className="flex min-w-[220px] flex-1 gap-2">
          <input
            className="field h-7"
            placeholder="Add a real name or nickname…"
            aria-label="New name"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="btn h-7 shrink-0" disabled={!text.trim()}>
            <LuPlus /> Add
          </button>
        </form>
      </div>
    </div>
  )
}

const AddRow = ({ onAdd, disabled }) => {
  const [text, setText] = useState('')
  const submit = (e) => {
    e.preventDefault()
    if (!text.trim()) return
    onAdd(text.trim())
    setText('')
  }
  return (
    <form onSubmit={submit} className="mt-5 flex gap-2 border-b border-line pb-4">
      <input
        className="field"
        placeholder="Add a memory…"
        aria-label="New memory"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
      />
      <button className="btn shrink-0" disabled={disabled || !text.trim()}>
        <LuPlus /> Add
      </button>
    </form>
  )
}

const Row = ({ item, onSave, onForget }) => {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(item.text)
  const icon = 'grid size-8 cursor-pointer place-items-center rounded text-soft hover:text-gray-100'
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1.5 border-b border-line py-4">
      {editing ? (
        <textarea
          className="field h-auto resize-y py-2 leading-relaxed"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
        />
      ) : (
        <div className="text-[14.5px] leading-snug">{item.text}</div>
      )}
      <div className="row-span-2 flex items-center gap-1">
        {editing ? (
          <>
            <button
              className={icon}
              aria-label="Save"
              title="Save"
              onClick={() => (onSave(text), setEditing(false))}
            >
              <LuCheck className="size-4 text-good" />
            </button>
            <button
              className={icon}
              aria-label="Cancel"
              title="Cancel"
              onClick={() => (setText(item.text), setEditing(false))}
            >
              <LuX className="size-4" />
            </button>
          </>
        ) : (
          <>
            <button
              className={icon}
              aria-label="Edit"
              title="Edit"
              onClick={() => setEditing(true)}
            >
              <LuPencil className="size-4" />
            </button>
            <button className={icon} aria-label="Forget" title="Forget" onClick={onForget}>
              <LuTrash2 className="size-4" />
            </button>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
        <span className="rounded border border-edge px-1.5 py-0.5 text-[11px] font-medium text-soft">
          {item.meta}
        </span>
        <span className="font-mono">
          {fmtDay(item.when)} {fmtTime(item.when)}
        </span>
      </div>
    </div>
  )
}

export default Memories
