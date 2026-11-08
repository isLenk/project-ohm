# General terminology (book icon, top of the Memories list)

Words this group uses that Ohm's model may not know: group slang ("ceebs", "zhing"), game and place names ("hll", "ttc"), and typos. Each one can get a definition, or be linked to another word as a **typo of** it or as having the **same meaning**. Definitions go to Ohm, so he understands them when people chat or talk to him, and to the model that reads messages for memories, in this project and every other one.

## Statuses

| Status | Icon | Meaning |
|---|---|---|
| `unknown` | ? | found by the scan, nothing said about it yet |
| `defined` | open book | has a definition |
| `linked` | link | typo of, or same meaning as, another word (`related_term`), which needn't be a term itself ("alc" → "alcohol") |
| `ignored` | crossed-out eye | the user marked it a normal word; the scan leaves it alone |

A term is either defined or linked: saving one clears the other.

## Finding unfamiliar words

**Look for unfamiliar words** (`POST /terms/scan`) reads every message that isn't excluded:

1. Lowercase it and take out links, pings and custom emoji. Split it into words.
2. Skip normal words: ones the embedding model's tokenizer has whole (about 22k), a short list of internet shorthand every model knows ("lmao", "idk"), contractions typed without the apostrophe ("dont"), and people's names. Letters repeated three or more times are cut to two ("lmaooo"). A plural of a normal word is normal.
3. Keep words used at least 4 times by at least 2 people. One-offs are mostly typos nobody needs explained. A plural is filed under its singular when both are used.
4. If `llama-server` is running, ask its tokenizer (`POST /tokenize`) how it splits each word, in one request. A word the model has as one token ("refund", "sushi"), or one token plus an ending ("refunds"), is normal. Slang, names and typos get split up ("ce|eb|s", "air|soft"). This takes out most of the ordinary words the small embedding vocabulary misses. When `llama-server` isn't running, this step is skipped and the list is longer.
5. Store the 3 newest messages using each word as examples.

On a 130k-message project this takes about 4 seconds and finds around 800 terms. Re-scanning updates counts and examples. It adds new terms, and removes detected terms that no longer qualify, unless the user has touched them (defined, linked or marked normal).

## Suggested meanings

**Suggest meanings for N** (`POST /terms/suggest`, a background job like reading memories) asks Ohm's model (`llama-server`) about each unknown term without a suggestion yet, most used first. It gets up to 6 messages using the word, and the words already defined (they say where the group is: "scarbs" is Scarborough, Ontario). It answers in JSON with a `kind`, which is stored in `terms.suggestion`:

| Model says | Stored | Approving it |
|---|---|---|
| `slang`, `name`, `thing` (an abbreviation, place, gear or game) | `{kind: 'definition', definition, label}` | defines the term |
| `typo`, `short` with `same_as` | `{kind: 'typo' \| 'same', related_term}` | links it |
| `normal` | `{kind: 'normal'}` | marks it a normal word |
| `unsure`, or anything unreadable | `{kind: 'unsure'}` | nothing to approve |

Each suggestion also keeps the model's `meaning`, shown next to it so it's quick to judge. A suggestion changes nothing until it's approved: the term stays unknown, and it isn't shared with Ohm or used in prompts.

In the Unknown tab a suggestion shows under the term with a sparkle. ✓ approves it (`POST /terms/{id}/accept`), ✎ opens the editor with it filled in to fix first, ✕ dismisses it (`DELETE /terms/{id}/suggestion`, stored as `unsure` so it isn't asked again). **Approve all N** (`POST /terms/accept-all`) approves every one still waiting, and **Only these** filters the tab to them. The Ctrl+click picker fills in the suggestion too.

About 0.7 seconds a word on Gemma 4 E4B, so a few hundred terms take several minutes. Expect common chat shorthand ("ofc", "alr", "copped") to come out right and local names to come out wrong: it read "ttc" (the Toronto transit) as "transit time" and "hll" (Hell Let Loose) as "hello". So skim before **Approve all**.

## Ctrl+click

Ctrl+click (Cmd+click on a Mac) any word in the Curator (Threads, Raw, Memories, the source panel) to define it. To pick a phrase, highlight it first, then Ctrl+click on it. The picker shows what's already known about the term and saves with `POST /terms`, which adds the term (`origin='manual'`, examples found through full-text search) or updates it.

## In the extraction prompt

For each block, defined and linked terms that appear in the block or its context go before the context:

```
Words this group uses:
"baro" means the same as "barotrauma": A co-op submarine game.
"ceeb": someone who skips plans
```

At most 12 lines. Normal words are left out. Terms other projects shared (below) are used too; when both define a word, this project's definition wins.

## Shared with Ohm

Defined and linked terms are sent to `memory_service` (`PUT /glossary/{source}`, see `apps/memory/README.md`), which every character reads. `source` is `curator-<created_at>` from the project's meta, so it stays the same through renames and Save As. Each push replaces everything that project sent before, so a term that's deleted, cleared or marked normal goes from the bot too.

A push happens in the background after every change (`POST /terms`, `PATCH`, `DELETE`), when a project is opened (for terms defined before this, or while the NAS was down), on **Sync kept**, and on **Share now** in the tab (`POST /terms/share`). It fails soft: if the NAS can't be reached, the tab says so and the next change tries again.

The engine (`controller/memory.py`, `glossary_for`) fetches `GET /glossary` every 5 minutes and matches terms the same way as the extraction prompt, against the message and the last 6 lines of the conversation. Matching lines go into the system prompt of every call to Ohm's model (`controller/wrappers/openai.py`, `create`): replies in text and voice, the web-search check, and the live memory extraction:

```
Words people here use, so you know what they mean:
"hll": Hell Let Loose, a WW2 shooter
```

While reading messages for memories, the Curator also fetches `GET /glossary` once per run and uses other projects' terms.
