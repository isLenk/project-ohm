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

1. For each block that is not excluded, by a non-bot author, with at least 3 words in total:
2. Call the local LLM (`llama-server :5000`) with a prompt based on `extract_memory` in `controller/wrappers/openai.py`. That prompt already filters filler and requests to the bot. Extend it to return JSON:

   ```json
   { "note": "KaptainDoosh plays Barotrauma with the group.",
     "category": "fact",
     "short_lived": false,
     "mentions_person": null }
   ```

   `short_lived` is true for things that are only true today ("was out all day"). `mentions_person` is a name that is not a known author ("arnav").

3. Drop `NONE` results. A block is marked in `memory_scans` once the model has answered for it. If the request fails (a timeout, say), the block stays unscanned so the next run retries it. If `llama-server` stops answering, the job stops early and reports `stopped`.
4. **Dedupe:** embed the note (same MiniLM model as `memory_service`). If an existing memory for the same author has cosine ≥ 0.9, add this block's messages to that memory's `memory_sources` instead of creating a new row. The UI shows that as "2 sources".
5. Insert with `status='new'`.

If the LLM is down, the job pauses and retries later. It never blocks the UI.

## Style traits

Computed, not generated. Recompute when the author's message set changes. Each trait needs evidence in at least 20% of the author's blocks, and at least 10 blocks in total.

- capitalization habit (first letter upper vs lower)
- average lines per block ("sends 2–3 lines in a burst")
- frequent slang or abbreviations: tokens the author uses at 5× or more the corpus rate, with a minimum count of 3
- mass-ping habit: share of messages with 3 or more mentions

Store each trait as a `memories` row with `category='style'`. Regenerating replaces `new` style rows. It never touches `kept` or `forgotten` rows.

## Review actions

| UI | API | Effect |
|---|---|---|
| ✓ Keep | `PATCH /memories/{id} {status:'kept'}` | Queued for sync |
| ✎ Edit | `PATCH /memories/{id} {text}` | If already synced, re-sync (delete + add remotely) |
| 🗑 Forget | `PATCH /memories/{id} {status:'forgotten'}` | If synced, delete remotely too |
| 🔗 Link to person | `PATCH /memories/{id} {linked_author_id}` | Connects "arnav" to a known author |
| Keep all new | `POST /people/{id}/memories/keep-all` | Skips `short_lived` ones |

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
