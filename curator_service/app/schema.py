"""SQL schemas for the .ohm project file and the app-wide registry.

See Documentation/Curator/data-model.md. Bump PROJECT_VERSION and add a step to
PROJECT_MIGRATIONS whenever the project schema changes.
"""

APPLICATION_ID = 0x4F484D31  # 'OHM1'

PROJECT_VERSION = 1

PROJECT_SCHEMA = """
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE sources (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  file_name     TEXT NOT NULL,
  sha256        TEXT NOT NULL UNIQUE,
  guild_id      TEXT, guild_name TEXT,
  channel_id    TEXT NOT NULL, channel_name TEXT,
  imported_at   INTEGER NOT NULL,
  message_count INTEGER NOT NULL
);

CREATE TABLE authors (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  display_name  TEXT,
  avatar_url    TEXT,
  is_bot        INTEGER NOT NULL DEFAULT 0,
  color_index   INTEGER NOT NULL
);

CREATE TABLE blocks (
  id            INTEGER PRIMARY KEY,
  channel_id    TEXT NOT NULL,
  author_id     TEXT NOT NULL REFERENCES authors(id),
  first_ts      INTEGER NOT NULL,
  last_ts       INTEGER NOT NULL
);
CREATE INDEX blocks_feed ON blocks (channel_id, first_ts, id);

CREATE TABLE messages (
  id            TEXT PRIMARY KEY,
  source_id     INTEGER REFERENCES sources(id),
  channel_id    TEXT NOT NULL,
  author_id     TEXT NOT NULL REFERENCES authors(id),
  ts            INTEGER NOT NULL,
  edited_ts     INTEGER,
  type          TEXT NOT NULL DEFAULT 'Default',
  content       TEXT NOT NULL,
  content_text  TEXT NOT NULL,
  reply_to_id   TEXT,
  mentions      TEXT NOT NULL DEFAULT '[]',
  attachments   TEXT NOT NULL DEFAULT '[]',
  block_id      INTEGER NOT NULL REFERENCES blocks(id),
  excluded      INTEGER NOT NULL DEFAULT 0,
  exclude_reason TEXT
);
CREATE INDEX messages_feed ON messages (channel_id, ts, id);
CREATE INDEX messages_block ON messages (block_id, ts);
CREATE INDEX messages_author ON messages (author_id, ts);

CREATE TABLE links (
  id            INTEGER PRIMARY KEY,
  unit_kind     TEXT NOT NULL CHECK (unit_kind IN ('block','message')),
  unit_id       TEXT NOT NULL,
  parent_id     TEXT REFERENCES messages(id),
  status        TEXT NOT NULL CHECK (status IN ('accepted','suggested','root')),
  origin        TEXT NOT NULL CHECK (origin IN ('discord','suggested','manual')),
  score         REAL,
  updated_at    INTEGER NOT NULL,
  UNIQUE (unit_kind, unit_id)
);
CREATE INDEX links_parent ON links (parent_id);

CREATE TABLE rejected_suggestions (
  unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, parent_id TEXT NOT NULL,
  PRIMARY KEY (unit_kind, unit_id, parent_id)
);

CREATE TABLE conversations (
  id            INTEGER PRIMARY KEY,
  channel_id    TEXT NOT NULL,
  first_message_id TEXT NOT NULL REFERENCES messages(id),
  last_message_id  TEXT NOT NULL REFERENCES messages(id),
  first_ts      INTEGER NOT NULL,
  last_ts       INTEGER NOT NULL,
  approved_at   INTEGER,
  archived_at   INTEGER,
  manual        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX conversations_ts ON conversations (channel_id, first_ts);

CREATE TABLE filter_rules (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  value         TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE memories (
  id            INTEGER PRIMARY KEY,
  author_id     TEXT REFERENCES authors(id),
  mentioned_name TEXT,
  category      TEXT NOT NULL CHECK (category IN ('fact','relationship','style')),
  text          TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('new','kept','forgotten')),
  short_lived   INTEGER NOT NULL DEFAULT 0,
  linked_author_id TEXT REFERENCES authors(id),
  remote_id     INTEGER,
  embedding     BLOB,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX memories_author ON memories (author_id, status);

CREATE TABLE memory_sources (
  memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  message_id TEXT NOT NULL REFERENCES messages(id),
  PRIMARY KEY (memory_id, message_id)
);

CREATE TABLE memory_scans (
  block_id   INTEGER PRIMARY KEY REFERENCES blocks(id),
  scanned_at INTEGER NOT NULL
);

CREATE TABLE project_runs (
  run_id      TEXT PRIMARY KEY,
  dataset_sha TEXT NOT NULL
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content_text, content='messages', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content_text) VALUES (new.rowid, new.content_text);
END;
CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content_text) VALUES ('delete', old.rowid, old.content_text);
END;
CREATE TRIGGER messages_fts_au AFTER UPDATE OF content_text ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content_text) VALUES ('delete', old.rowid, old.content_text);
  INSERT INTO messages_fts(rowid, content_text) VALUES (new.rowid, new.content_text);
END;

CREATE TABLE message_embeddings (
  message_id TEXT PRIMARY KEY REFERENCES messages(id),
  vector     BLOB NOT NULL
);

CREATE TABLE message_similar (
  message_id TEXT PRIMARY KEY REFERENCES messages(id),
  other_id   TEXT NOT NULL REFERENCES messages(id),
  score      REAL NOT NULL
);
"""

# Step n upgrades a file from user_version n to n + 1.
PROJECT_MIGRATIONS: list[str] = []

DEFAULT_SETTINGS = {
    "split_gap_ms": 30 * 60 * 1000,
    "block_gap_ms": 5 * 60 * 1000,
    "suggest_high": 0.6,
    "suggest_low": 0.3,
    "author_cap": 0.3,
    "character": "ohm",
}

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS base_models (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  gguf_path   TEXT NOT NULL,
  quant       TEXT,
  hf_path     TEXT,
  different_base INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT NOT NULL UNIQUE,
  imported_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS adapters (
  id          TEXT PRIMARY KEY,
  base_model_id TEXT NOT NULL REFERENCES base_models(id),
  name        TEXT NOT NULL,
  gguf_path   TEXT NOT NULL,
  peft_path   TEXT,
  run_id      TEXT,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS train_runs (
  id          TEXT PRIMARY KEY,
  project_path TEXT NOT NULL,
  base_model_id TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  stage       TEXT,
  config      TEXT NOT NULL,
  dataset_sha TEXT NOT NULL,
  example_count INTEGER NOT NULL,
  started_at  INTEGER,
  finished_at INTEGER,
  final_loss  REAL,
  run_dir     TEXT NOT NULL,
  pid         INTEGER,
  adapter_id  TEXT,
  stopped_services TEXT NOT NULL DEFAULT '[]',
  error       TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
