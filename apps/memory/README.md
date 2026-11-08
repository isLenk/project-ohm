# Memory service (runs on the NAS)

Postgres + pgvector for storage, and a FastAPI app on port 8100 that the PC calls.
Embeddings (all-MiniLM-L6-v2, 384-dim) are created here on the CPU, so the PC doesn't need the model.

## Deploy
1. Copy this folder to the NAS, e.g. `/volume1/docker/memory_service`.
2. `cp .env.example .env` and set `POSTGRES_PASSWORD` and `API_KEY`.
3. `docker compose up -d --build`
4. From the PC: `curl http://<nas-ip>:8100/health`

`db/init/01_schema.sql` only runs when `data/postgres` is empty. Tables and columns added later (`knowledge`, `glossary`, `users.username`/`aliases`/`avatar_url`, `reflections`, `traits`, `trait_history`) are also created by the API on start.
After pulling changes to `app/`, rebuild: `docker compose up -d --build api`. To reset everything:
`docker compose down && rm -rf data/postgres && docker compose up -d`.

## API (all routes except /health need header `X-API-Key`)
| Method | Path | Purpose |
|---|---|---|
| GET | /health | DB + model check |
| GET/PUT | /characters, /characters/{slug} | Character profiles (PUT also records a version) |
| PATCH | /characters/{slug} | Partial update; `configs` is merged (used by the controller UI) |
| DELETE | /characters/{slug} | Delete a character **and all its memories/history** |
| PUT | /users/{discord_id} | Create/update a user: `display_name`, `preferred_name`, `username`, `avatar_url`, `aliases` (real name, nicknames; sent whole, replaces the list) |
| GET | /users | Everyone with a name, with every name they go by. The bot matches messages against these |
| GET/POST | /users/{discord_id}/facts | Per-character facts about a user |
| DELETE | /users/{discord_id}/facts/{id} | Forget a fact (used by the Curator's Forget) |
| POST | /memories | Store a memory (embedded automatically) |
| POST | /memories/search | Top memories ranked by similarity, importance, recency, plus the person's kept facts, the character's lore and shared knowledge (`kind` `user_fact`/`lore`/`knowledge`). With `user_id`, only that person's memories and facts |
| DELETE | /memories/{id} | Forget a memory |
| POST | /messages | Log a chat/voice turn |
| GET | /messages/recent | Recent history for a character/channel |
| GET | /messages?character=&after_id=&before_id=&limit=500 | Messages in id order (both bounds exclusive, limit up to 2000), with `name` on user rows. What reflection reads |
| POST | /logs, /metrics | Logs and generation timings |
| GET | /logs?source=brain,mood&level=&since=&limit=100 | Logs newest first; `source` is a comma list, limit up to 500. The app's brain, mood and reflection feeds |
| GET | /memories | List memories for a character, optionally only one person's or one kind (`event`, `summary`, and from reflection `topic`, `joke`, `opinion`, `question`, `self`) |
| PATCH | /memories/{id} | Edit a memory's text or importance (re-embedded) |
| GET/POST | /lore?character=, PATCH/DELETE /lore/{id} | What one character knows about itself |
| GET/POST | /knowledge, PATCH/DELETE /knowledge/{id} | What every character knows. Created on start if the database predates it |
| GET | /glossary | Words the groups use (slang, game names, typos) and what they mean. Every character gets them |
| PUT | /glossary/{source} | Replace one Curator project's terms (`{terms: [{term, definition, relation, related_term}]}`) |
| PATCH | /users/{discord_id}/facts/{id} | Edit a fact |
| GET | /stats | The controller dashboard: counts, reply time, messages per hour, top people, `reflections: {last_at, pending}` |
| GET | /people, /people/{discord_id}/messages | Who talked to a character, and their latest lines with the replies |
| GET | /conversations | Messages per channel, split where the channel went quiet |
| GET | /relationships?character= | How the character feels about each person: `user_id`, `name`, `affinity`, `notes`, `updated_at` |
| PATCH | /relationships/{character}/{user_id} | `{affinity_delta?, affinity?, notes?}`. Creates the row; affinity kept within -1..1; `notes` replaces |
| GET | /reflections?character=&limit=20 | Reflection jobs, newest first |
| GET | /reflections/due | The oldest pending job of any character, or `null`. Doesn't claim it |
| POST | /reflections/{id}/claim | Mark it running. Returns the job plus `after_id`/`before_id` for /messages. 409 if not pending |
| POST | /reflections/{id}/finish | `{result?, error?}`: done, or failed when `error` is sent. 409 if already finished |
| POST | /reflections/request?character= | Reflect now: queue everything since the last reflection. 409 if one is open or nothing is new |
| GET | /traits?character=&days=30 | `{traits: [{trait, value, floor, ceiling, updated_at}], history: [{trait, value, reason, created_at}]}` |
| PUT | /traits/{character} | `{traits: [{trait, value, reason}]}`. Clamped to floor/ceiling; changed values go in the history. Returns the traits |

Interactive docs: `http://<nas-ip>:8100/docs`

## Reflection
Every so often the PC's Curator looks back over a stretch of conversation with a cloud model and writes down what it
learned: memories, how Ohm feels about each person, and small shifts in his personality. It runs on the PC (it needs the
signed-in Codex CLI) but the NAS decides when, because it is always on and holds the messages.

**When.** Most of the talking is voice, so it goes by how much was said, not a clock. Every `REFLECT_CHECK_MINUTES` (10)
the API looks at each character: if at least `REFLECT_MIN_MESSAGES` (40) messages (both sides) came in since the last
finished reflection, and the newest is older than `REFLECT_QUIET_MINUTES` (30), so the conversation is over and is
reflected on whole, it queues a job and logs it (`source='reflect'`). A failed job is queued again after an hour.
The four values can be overridden with environment variables of the same name. The app's Reflect now
(`POST /reflections/request`) queues whatever is new, however little, without waiting.

**Jobs.** `pending` → `running` (claimed) → `done` or `failed`. Only one open job per character. A job left `running`
for `REFLECT_STALE_MINUTES` (60) goes back to `pending`, since the PC probably went off mid-job. The Curator polls
`GET /reflections/due`, claims it, reads `GET /messages?character=&after_id=&before_id=` (the bounds come back from the
claim; page with the last id when there are more than 2000), writes what it found, then calls `/finish`.

**What it writes**, all into tables the engine already reads, so Ohm has it at no extra latency:
- `memories` with new kinds: `topic` (something to bring up), `joke` (an inside joke: the quote and who was there),
  `opinion` (what Ohm thinks and why), `question` (something to ask someone), `self` (how Ohm has changed lately).
- `user_relationships`: affinity (-1 hates .. 1 loves) and notes per person, through `PATCH /relationships/...`.
- `traits`: personality dials from 0 to 1 (Ohm starts sarcastic 0.6, blunt 0.5, wholesome 0.3, chaotic 0.4, curious 0.4,
  talkative 0.4), each kept between a floor and ceiling (0.1 .. 0.9) so he can drift but never flip. Every change is kept
  in `trait_history` with a one-line reason.
- `reflections.result`: counts and a short summary, shown in the app's log.

## Connecting the PC
Root `.env`: `MEMORY_API_URL=http://<nas-ip>:8100` and `MEMORY_API_KEY=<API_KEY>`.
The app: the same two values in Settings › Memory (the key is stored encrypted). Don't put them in an `apps/mainframe/.env`; the app no longer reads it, so a key can't end up inside a build.

## Importing old data (once)
`.venv\Scripts\python.exe apps\memory\migrate.py --dry-run` then without `--dry-run`.
Copies characters from `apps/engine/config/constants.py` and `apps/mainframe/characters.db`, and memories from Qdrant
(`--qdrant http://<host>:6333`, default localhost). Running it twice duplicates memories.
