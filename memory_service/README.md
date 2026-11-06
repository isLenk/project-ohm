# Memory service (runs on the NAS)

Postgres + pgvector for storage, and a FastAPI app on port 8100 that the PC calls.
Embeddings (all-MiniLM-L6-v2, 384-dim) are created here on the CPU, so the PC doesn't need the model.

## Deploy
1. Copy this folder to the NAS, e.g. `/volume1/docker/memory_service`.
2. `cp .env.example .env` and set `POSTGRES_PASSWORD` and `API_KEY`.
3. `docker compose up -d --build`
4. From the PC: `curl http://<nas-ip>:8100/health`

`db/init/01_schema.sql` only runs when `data/postgres` is empty. To reset everything:
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
| POST | /memories | Store a memory (embedded automatically) |
| POST | /memories/search | Top memories ranked by similarity, importance, recency |
| DELETE | /memories/{id} | Forget a memory |
| POST | /messages | Log a chat/voice turn |
| GET | /messages/recent | Recent history for a character/channel |
| POST | /logs, /metrics | Logs and generation timings |

Interactive docs: `http://<nas-ip>:8100/docs`

## Connecting the PC
Root `.env`: `MEMORY_API_URL=http://<nas-ip>:8100` and `MEMORY_API_KEY=<API_KEY>`.
`controller-mainframe/.env`: the same values as `MAIN_VITE_MEMORY_API_URL` / `MAIN_VITE_MEMORY_API_KEY`.

## Importing old data (once)
`Scripts\python.exe memory_service\migrate.py --dry-run` then without `--dry-run`.
Copies characters from `config/constants.py` and `controller-mainframe/characters.db`, and memories from Qdrant
(`--qdrant http://<host>:6333`, default localhost). Running it twice duplicates memories.
