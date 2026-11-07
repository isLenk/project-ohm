# Data model

Two stores:

- **Project file (`*.ohm`)**: a SQLite database holding one curation project. Messages, links, conversations, memories, settings.
- **Registry (`registry.sqlite`)**: app-wide, in the user's app data folder. Base models, trained adapters, training runs. Shared by every project.

`curator_service/app/schema.py` is the source of truth. It adds a few columns and tables this page doesn't show:
- **Project file:**
  - `memories.embedding`
  - `memory_scans`, which records the blocks already read for memories
  - `model_link_scans`, which records the blocks the model has already guessed a reply for
  - `message_similar`, which flags near-duplicates
- **Registry:**
  - `base_models.fingerprint` replaces `sha256`. It is a hash of the size plus the first and last 64 MB, because hashing a whole 5 GB GGUF on click is too slow.
  - `base_models.different_base`
  - `train_runs.run_dir`, `pid`, `adapter_id` and `stopped_services`

## Project file

A `.ohm` file is a plain SQLite 3 database.

```sql
PRAGMA application_id = 0x4F484D31;  -- 'OHM1', lets us reject random .sqlite files
PRAGMA user_version   = 3;           -- schema version, bump on every migration
PRAGMA journal_mode   = WAL;
PRAGMA foreign_keys   = ON;
```

### Saving

The service works on a temp working copy and writes it to the real path on Save.

- **Open:** copy `project.ohm` → `<tmp>/project.work.ohm`, run migrations on the copy, open it.
- **Edit:** every API write commits to the working copy. The UI shows "Unsaved changes" when the working copy is newer than the last save.
- **Save:** `VACUUM INTO '<path>.tmp'`, fsync, then atomic rename over `<path>`. Never write the target in place.
- **Save as:** same, to a new path. The project now points at the new path.
- **Autosave (later):** call Save on a timer or after N writes. No format change needed.
- **Crash recovery:** on open, if a working copy newer than the file exists, offer to restore it.

### Schema

Times are stored as integer milliseconds since the Unix epoch, UTC. Discord IDs (snowflakes) are stored as `TEXT`.

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL            -- JSON
);
-- keys: name, created_at, split_gap_ms (default 1800000),
--       block_gap_ms (default 300000), suggest_high (0.6), suggest_low (0.3),
--       author_cap (0.3), character ('ohm')

CREATE TABLE sources (           -- one row per imported file
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,   -- 'discord_chat_exporter_json'
  file_name     TEXT NOT NULL,
  sha256        TEXT NOT NULL UNIQUE,   -- re-importing the same file is a no-op
  guild_id      TEXT, guild_name   TEXT,
  channel_id    TEXT NOT NULL, channel_name TEXT,
  imported_at   INTEGER NOT NULL,
  message_count INTEGER NOT NULL
);

CREATE TABLE authors (
  id            TEXT PRIMARY KEY,       -- Discord user id
  name          TEXT NOT NULL,          -- username
  display_name  TEXT,                   -- nickname shown in the UI
  avatar_url    TEXT,
  is_bot        INTEGER NOT NULL DEFAULT 0,
  color_index   INTEGER NOT NULL        -- stable palette slot; also the arrow lane
);

CREATE TABLE messages (
  id            TEXT PRIMARY KEY,       -- Discord message id
  source_id     INTEGER NOT NULL REFERENCES sources(id),
  channel_id    TEXT NOT NULL,
  author_id     TEXT NOT NULL REFERENCES authors(id),
  ts            INTEGER NOT NULL,
  edited_ts     INTEGER,
  type          TEXT NOT NULL,          -- 'Default', 'Reply', or a system type
  content       TEXT NOT NULL,          -- as exported, mentions as <@id>
  content_text  TEXT NOT NULL,          -- mentions resolved to @name, for display/search/training
  reply_to_id   TEXT,                   -- Discord's reference; may point outside the import
  mentions      TEXT NOT NULL DEFAULT '[]',   -- JSON array of author ids
  attachments   TEXT NOT NULL DEFAULT '[]',   -- JSON
  block_id      INTEGER NOT NULL REFERENCES blocks(id),
  excluded      INTEGER NOT NULL DEFAULT 0,
  exclude_reason TEXT                   -- 'manual' | 'bot' | 'system' | 'filter:<rule id>'
);
CREATE INDEX messages_feed ON messages (channel_id, ts, CAST(id AS INTEGER));  -- matches the feed's ORDER BY
CREATE INDEX messages_author ON messages (author_id, ts);

CREATE TABLE blocks (
  id            INTEGER PRIMARY KEY,
  channel_id    TEXT NOT NULL,
  author_id     TEXT NOT NULL REFERENCES authors(id),
  first_ts      INTEGER NOT NULL,
  last_ts       INTEGER NOT NULL
);
CREATE INDEX blocks_feed ON blocks (channel_id, first_ts, id);
CREATE INDEX blocks_author ON blocks (channel_id, author_id, first_ts);  -- the suggester's "who spoke last" lookup

-- One row per unit that has a decision. A unit is a block or a split message.
-- No row = unattached.
CREATE TABLE links (
  id            INTEGER PRIMARY KEY,
  unit_kind     TEXT NOT NULL CHECK (unit_kind IN ('block','message')),
  unit_id       TEXT NOT NULL,          -- blocks.id as text, or messages.id
  parent_id     TEXT REFERENCES messages(id),  -- NULL only when status = 'root'
  status        TEXT NOT NULL CHECK (status IN ('accepted','suggested','root')),
  origin        TEXT NOT NULL CHECK (origin IN ('discord','suggested','manual','model')),
  score         REAL,                   -- suggestion confidence (heuristic score), NULL otherwise
  updated_at    INTEGER NOT NULL,
  UNIQUE (unit_kind, unit_id)
);
CREATE INDEX links_parent ON links (parent_id);

CREATE TABLE rejected_suggestions (     -- so the same guess is never offered twice
  unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, parent_id TEXT NOT NULL,
  origin    TEXT,                       -- who suggested it; NULL for rejections from version 1
  PRIMARY KEY (unit_kind, unit_id, parent_id)
);

CREATE TABLE conversations (
  id            INTEGER PRIMARY KEY,
  channel_id    TEXT NOT NULL,
  first_message_id TEXT NOT NULL REFERENCES messages(id),
  last_message_id  TEXT NOT NULL REFERENCES messages(id),
  first_ts      INTEGER NOT NULL,
  last_ts       INTEGER NOT NULL,
  approved_at   INTEGER,                -- NULL = not approved
  archived_at   INTEGER,                -- NULL = not archived
  manual        INTEGER NOT NULL DEFAULT 0   -- 1 if the user split/merged here
);
CREATE INDEX conversations_ts ON conversations (channel_id, first_ts);

CREATE TABLE filter_rules (             -- "filter out" rules, e.g. regex or bot authors
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,          -- 'regex' | 'author' | 'min_words' | 'link_only'
  value         TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE memories (                 -- candidates; kept ones are also pushed to memory_service
  id            INTEGER PRIMARY KEY,
  author_id     TEXT REFERENCES authors(id),   -- NULL for people only mentioned by name
  mentioned_name TEXT,                  -- e.g. 'arnav' when there is no author row
  category      TEXT NOT NULL CHECK (category IN ('fact','relationship','style')),
  text          TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('new','kept','forgotten')),
  short_lived   INTEGER NOT NULL DEFAULT 0,
  linked_author_id TEXT REFERENCES authors(id), -- set by "link to a person"
  remote_id     INTEGER,                -- user_facts.id in memory_service once synced
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE TABLE memory_sources (
  memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  message_id TEXT NOT NULL REFERENCES messages(id),
  PRIMARY KEY (memory_id, message_id)
);

CREATE TABLE project_runs (             -- which registry runs were trained from this project
  run_id      TEXT PRIMARY KEY,         -- registry.train_runs.id
  dataset_sha TEXT NOT NULL
);

-- Full-text search (search.md)
CREATE VIRTUAL TABLE messages_fts USING fts5(
  content_text, content='messages', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
-- keep in sync with triggers on messages insert/update/delete

-- Similarity (search.md "is:similar")
CREATE TABLE message_embeddings (
  message_id TEXT PRIMARY KEY REFERENCES messages(id),
  vector     BLOB NOT NULL               -- float32[384], all-MiniLM-L6-v2, same model as memory_service
);
```

### Derived values, not stored

Compute these on read. Do not cache them in tables.

- **Effective parent of a message:** the message's own link if it has one, else its block's link.
- **Split line:** a message that has its own `links` row.
- **Thread:** walk effective parents up to a `root`.
- **Conversation stats:** people, message count, author shares, unattached count, suggested count.

### Invariants

The service must reject any write that breaks these. `threading.md` lists the matching error codes.

1. A unit has at most one `links` row.
2. `parent_id` is strictly earlier than the unit's first message (`ts`, then `id` as tiebreak).
3. A parent cannot be inside the same unit.
4. A block-level link cannot point into its own block.
5. Any link change touching a message in an approved conversation clears that conversation's `approved_at`.

## Registry

`registry.sqlite` lives in the app data folder (`%APPDATA%/ohm-curator/` on Windows). It survives across projects, which is why the model dropdown shows "things you've already imported".

```sql
CREATE TABLE base_models (
  id          TEXT PRIMARY KEY,          -- uuid
  name        TEXT NOT NULL,             -- 'Gemma 4 E4B Uncensored'
  gguf_path   TEXT NOT NULL,             -- what llama-server loads
  quant       TEXT,                      -- 'Q5_K_M'
  hf_path     TEXT,                      -- HF weights dir or repo id used for training; NULL = can't train yet
  sha256      TEXT NOT NULL UNIQUE,
  imported_at INTEGER NOT NULL
);

CREATE TABLE adapters (
  id          TEXT PRIMARY KEY,
  base_model_id TEXT NOT NULL REFERENCES base_models(id),
  name        TEXT NOT NULL,
  gguf_path   TEXT NOT NULL,             -- converted adapter for llama-server --lora
  peft_path   TEXT NOT NULL,             -- raw PEFT output
  run_id      TEXT REFERENCES train_runs(id),
  created_at  INTEGER NOT NULL
);

CREATE TABLE train_runs (
  id          TEXT PRIMARY KEY,
  project_path TEXT NOT NULL,
  base_model_id TEXT NOT NULL REFERENCES base_models(id),
  status      TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  stage       TEXT,                      -- see training.md
  config      TEXT NOT NULL,             -- JSON hyperparameters
  dataset_sha TEXT NOT NULL,
  example_count INTEGER NOT NULL,
  started_at  INTEGER, finished_at INTEGER,
  final_loss  REAL,
  log_path    TEXT NOT NULL,
  error       TEXT
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- active model/adapter, recent projects
```

The current setup already has one base model to seed the registry with:

```
llama.cpp/models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf
```
