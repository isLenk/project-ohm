# Memories (brain icon)

While reading imports, the Curator proposes things worth remembering about each person. The user keeps, edits or forgets them. Kept memories are pushed to `memory_service` as `user_facts`, which is where the bot already reads per-person facts. They do not go into the LoRA dataset.

## Categories

| Category | Example | Source |
|---|---|---|
| `fact` | "Plays Barotrauma with this group." | per-block LLM extraction |
| `relationship` | "Plays with someone called arnav." | per-block LLM extraction, with a named person |
| `style` | "Sends 2–3 lines in a burst." | statistics over all of the author's messages |

## Extraction (facts and relationships)

Runs as a background job after import, and again for a conversation when it is approved.

1. For each block that is not excluded, by a non-bot author, with at least 3 words in total, newest block first:
2. Call the local LLM (`llama-server :5000`) with the block plus up to 10 channel messages from the 30 minutes before it (and any message it replies to, however old) as context, and the date and time the block was sent ("What KaptainDoosh said on Friday, November 7, 2025, 9:50 PM:"). Context leaves out excluded messages, messages with a file or sticker attached, and ones with no words besides pings; links stay, since a link often says what the replies are about. This is so a reply like "I'm down" can be read as what it agrees to. The prompt asks only for lasting things that make sense on their own, and filters filler, agreeing to something, being free for one event, who owes whom money, and requests to the bot. It spells out misreadings seen in practice: "if I lived in X" is not living in X, a list of names and numbers is not about the speaker, and pinging people with an amount asks them to pay the speaker. A note about a particular day must write the date out instead of "tomorrow" or "on Monday". It returns JSON:

   ```json
   { "meaning": "KaptainDoosh says he plays Barotrauma with the group most weekends.",
     "said_as": "statement",
     "note": "KaptainDoosh plays Barotrauma with the group.",
     "category": "fact",
     "short_lived": false,
     "mentions_person": null }
   ```

   `meaning` comes first so the model works out what was meant before it writes a note. `said_as` is `statement`, `question`, `hypothetical`, `joke` or `request`; only statements are kept. (The model sometimes writes the options as keys, `{"question": true}`, and that is read too.) `short_lived` is true for things that only matter around one day ("was out all day"). `mentions_person` is a name that is not a known author ("arnav").

   Defined terms from the General terminology tab (this project's and other projects') that appear in the block or its context go first (see terminology.md).

3. Drop:
   - `null` notes, and anything not said as a statement.
   - Notes that end on a pronoun or bare verb ("…is down to participate.") or point at something without naming it ("the situation", "something", "the game being discussed", "(likely …)"), because they don't say what they're about. "the game Killing Floor" and "someone called arnav" name it, so they stay.
   - Debts ("owes", "pay back") and reported speech ("ELEVIST states that…"), which the model writes despite the prompt.
   - Notes over 200 characters, which are text copied out of the message.
   - Notes that don't start with the block author's name, since a note is filed under that author and one about someone in the context would end up under the wrong person. The prompt's examples use the author's own name for the same reason: a fixed example name gets copied into everyone's notes.

   A kept note that still says "today", "tonight", "tomorrow", "on Monday" and so on, without a year, gets the day it was said on the end ("ELEVIST is free on Monday (said Friday, November 7, 2025).") and is marked `short_lived`.

   At the start of each run, unreviewed (`new`, unlinked) notes that fail these checks are deleted and their blocks marked unread, so they are asked about again, and unreviewed notes with a relative day get their date the same way. A block is marked in `memory_scans` once the model has answered for it. If the request fails (a timeout, say), the block stays unscanned so the next run retries it. If `llama-server` stops answering, the job stops early and reports `stopped`.
4. **Dedupe:** embed the note (same MiniLM model as `memory_service`). If an existing memory for the same author has cosine ≥ 0.9, add this block's messages to that memory's `memory_sources` instead of creating a new row. The UI shows that as "2 sources".
5. Insert with `status='new'`.

If the LLM is down, the job pauses and retries later. It never blocks the UI.

The job runs in `curator_service`, so it keeps going when the user switches tabs or pages. When the Memories view opens it looks for a running `memories` job in `GET /jobs` and follows it again. It only stops when the user presses **Stop** (`POST /jobs/{id}/cancel`). Blocks it already read stay scanned, so the next run carries on from there. New memories show up while it runs, and the list is ordered newest first (by the latest message each memory came from).

## Style traits

Computed, not generated. Recompute when the author's message set changes. Each trait needs evidence in at least 20% of the author's blocks, and at least 10 blocks in total.

- capitalization habit (first letter upper vs lower)
- average lines per block ("sends 2–3 lines in a burst")
- frequent slang or abbreviations: tokens the author uses at 5× or more the corpus rate, with a minimum count of 3
- mass-ping habit: share of messages with 3 or more mentions

Store each trait as a `memories` row with `category='style'`. Regenerating replaces `new` style rows. It never touches `kept` or `forgotten` rows.

## Reviewing

**Priority.** Each memory gets a priority from its wording (`memories.priority`), so it works for every stored memory without asking the model again. The first rule that matches wins:

| Priority | Icon | Matches |
|---|---|---|
| `short_lived` | ⚡ | the model said `short_lived`, or (after the next rule) wording like "currently", "tonight", "heading", "asking", "going to" |
| `important` | ★ | work, school, family, partners, pets, where they live, birthdays, what they own |
| `interest` | ♥ | likes, loves, plays, watches, favourite, prefers |
| `opinion` | quote | thinks, believes, wants, hopes, suggests |
| `other` | dot | anything else |

The list has a tab per priority next to New / Kept / Forgotten, and a search box that matches both the memory and the messages it came from.

**Sure bets.** Each group also gets a `confidence` from 0 to 1, so the ones safe to keep without reading the messages can be kept in one go. It's worked out from what's stored, like priority:

| | |
|---|---|
| start | important 0.45, interest 0.4, opinion 0.2, other 0.15, short-lived 0. The priority of the note the group shows (the one with the most sources), not the group's best |
| who they are | +0.15 for an important note that says it right after the name: "X lives", "works", "studies", "owns", "drives", "has a dog", "is 24 years old", "X's dad" |
| said again | +0.12 for each separate time it was said, up to 3 more. Counted in blocks, not messages: one burst of 4 lines is said once |
| similar notes | +0.08 for each other note in the group, up to 2 |
| hedged | −0.25 for "might", "maybe", "probably", "seems", "apparently" and so on |
| not settled | −0.15 for plans, passing states, opinions and things said to others: the short-lived and opinion wording above, and "looking for", "planning", "tells", "asks", "is playing", "is down", "this Saturday" |
| length | −0.1 under 5 words or over 30 |
| unlinked person | −0.05 for a relationship naming someone not linked to an author yet |

0.6 or more is a sure bet (`sure`). So "Lenk lives in Ottawa" or "Lenk drives a Civic" said once is one, and so is a hobby that comes up three times; "work" or "parents" somewhere else in a note ("the mic doesn't work") needs saying again. Short-lived notes never are. Style traits are 0.5: they're counted, but the slang trait picks up ordinary words ("go", "who"), so they're left to check by eye. On a 1,557-group project this gives 17 sure bets.

The **Sure bets** tab (badge-check icon) lists them most certain first, and they're marked "sure bet" in the other tabs. **Keep N sure bets** keeps just those (`POST /people/{id}/memories/keep-all?sure=true`), merging groups the same way as Keep all new.

**Grouping.** Notes stored apart (cosine below the 0.9 dedupe) can still say the same thing ("getting the alc from Kentucky" / "getting the alcohol from Kentucky"). The listing gives each memory a `group`: the id of the first memory in list order, by the same person and with the same status, whose embedding has cosine ≥ 0.84 with it. Each note is compared with a group's first note only, not chained, so a group can't drift to another topic. The UI shows a group as one row with "N similar". Keep, Forget and Restore on a group first merge it (`POST /memories/{id}/merge`) into the note with the most sources, so it is stored once. Each similar note can also be kept or forgotten on its own, for when the grouping is wrong. **Keep all new** merges the same way and skips groups that are all short-lived.

**Sources.** "open" shows the messages around the source in a panel to the right of the list, with the source highlighted (`GET /messages/{id}/around`). A memory with several sources has a button per source. **Show in Threads** jumps to the message in the Threads view. Hovering "open" shows the few lines before it.

## Review actions

| UI | API | Effect |
|---|---|---|
| ✓ Keep | `PATCH /memories/{id} {status:'kept'}` | Queued for sync |
| ✎ Edit | `PATCH /memories/{id} {text}` | If already synced, re-sync (delete + add remotely) |
| 🗑 Forget | `PATCH /memories/{id} {status:'forgotten'}` | If synced, delete remotely too |
| 🔗 Link to person | `PATCH /memories/{id} {linked_author_id}` | Connects "arnav" to a known author |
| Keep all new | `POST /people/{id}/memories/keep-all` | Merges groups, skips short-lived ones |
| Keep N sure bets | `POST /people/{id}/memories/keep-all?sure=true` | The same, only for sure bets |

Forgotten memories are kept locally so the extractor does not propose them again. Check new notes against forgotten ones with the same cosine ≥ 0.9 rule.

## Sync to memory_service

The memory service runs on the NAS (see `memory_service/README.md`) and is reached with `MEMORY_API_URL` and `MEMORY_API_KEY` from the root `.env`.

For each `kept` memory with `remote_id IS NULL`:

1. `PUT /users/{discord_id}` with `{ display_name }` so the user row exists.
2. `POST /users/{discord_id}/facts` with `{ character, fact: text }`. `character` comes from the project setting (default `ohm`).
3. Store the returned `id` as `remote_id`.

On forget: `memory_service` has no delete route for `user_facts` yet. Add `DELETE /users/{discord_id}/facts/{id}` to `memory_service/app/main.py`, which is a small change.

Style traits: sync them as facts too, phrased as sentences ("KaptainDoosh usually sends 2–3 lines in a row."). This lets the bot talk to people in a way that suits them.

Sync is fail-soft, like `controller/memory.py`. If the NAS is down, leave rows unsynced and retry on the next sync.
