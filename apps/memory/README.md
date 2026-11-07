# Memory service (runs on the NAS)

Postgres + pgvector for storage, and a FastAPI app on port 8100 that the PC calls.
Embeddings (all-MiniLM-L6-v2, 384-dim) are created here on the CPU, so the PC doesn't need the model.

## Deploy
1. Copy this folder to the NAS, e.g. `/volume1/docker/memory_service`.
2. `cp .env.example .env` and set `POSTGRES_PASSWORD` and `API_KEY`.
3. `docker compose up -d --build`
4. From the PC: `curl http://<nas-ip>:8100/health`

`db/init/01_schema.sql` only runs when `data/postgres` is empty. Tables added later (`knowledge`) are also created by the API on start.
After pulling changes to `app/`, rebuild: `docker compose up -d --build api`. To reset everything:
`docker compose down && rm -rf data/postgres && docker compose up -d`.

## API (all routes except /health need header `X-API-Key`)
| Method | Path | Purpose |
|---|---|---|
| GET | /health | DB + model check |
| GET/PUT | /characters, /characters/{slug} | Character profiles (PUT also records a version) |
| PATCH | /characters/{slug} | Partial update; `configs` is merged (used by the controller UI) |
| DELETE | /characters/{slug} | Delete a character **and all its memories/history** |
| PUT | /users/{discord_id} | Create/update a user |
| GET/POST | /users/{discord_id}/facts | Per-character facts about a user |
| DELETE | /users/{discord_id}/facts/{id} | Forget a fact (used by the Curator's Forget) |
| POST | /memories | Store a memory (embedded automatically) |
| POST | /memories/search | Top memories ranked by similarity, importance, recency, plus the character's lore and shared knowledge (`kind` `lore`/`knowledge`) |
| DELETE | /memories/{id} | Forget a memory |
| POST | /messages | Log a chat/voice turn |
| GET | /messages/recent | Recent history for a character/channel |
| POST | /logs, /metrics | Logs and generation timings |
| GET | /memories | List memories for a character, optionally only one person's or one kind |
| PATCH | /memories/{id} | Edit a memory's text or importance (re-embedded) |
| GET/POST | /lore?character=, PATCH/DELETE /lore/{id} | What one character knows about itself |
| GET/POST | /knowledge, PATCH/DELETE /knowledge/{id} | What every character knows. Created on start if the database predates it |
| PATCH | /users/{discord_id}/facts/{id} | Edit a fact |
| GET | /stats | The controller dashboard: counts, reply time, messages per hour, top people |
| GET | /people, /people/{discord_id}/messages | Who talked to a character, and their latest lines with the replies |
| GET | /conversations | Messages per channel, split where the channel went quiet |

Interactive docs: `http://<nas-ip>:8100/docs`

## Connecting the PC
Root `.env`: `MEMORY_API_URL=http://<nas-ip>:8100` and `MEMORY_API_KEY=<API_KEY>`.
`controller-mainframe/.env`: the same values as `MAIN_VITE_MEMORY_API_URL` / `MAIN_VITE_MEMORY_API_KEY`.

## Importing old data (once)
`Scripts\python.exe memory_service\migrate.py --dry-run` then without `--dry-run`.
Copies characters from `config/constants.py` and `controller-mainframe/characters.db`, and memories from Qdrant
(`--qdrant http://<host>:6333`, default localhost). Running it twice duplicates memories.
