-- Runs once, on the first start of an empty Postgres data folder.
-- Embeddings are 384-dim (all-MiniLM-L6-v2). Changing models means changing vector(384).

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Identity ──────────────────────────────────────────────────────────────
CREATE TABLE characters (
    slug         text PRIMARY KEY,               -- 'ohm'
    name         text NOT NULL,
    model        text,                           -- LLM model name in Tabby
    personality  text,                           -- system prompt
    configs      jsonb NOT NULL DEFAULT '{}',    -- max_tokens, temperature, ...
    voice        jsonb NOT NULL DEFAULT '{}',    -- {engine, file, speed, ...}
    modules      text[] NOT NULL DEFAULT '{}',   -- enabled modules
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE character_versions (              -- history of personality/config edits
    id           bigserial PRIMARY KEY,
    character    text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    personality  text,
    configs      jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ── People ────────────────────────────────────────────────────────────────
CREATE TABLE users (
    discord_id     text PRIMARY KEY,             -- snowflakes stored as text
    display_name   text,
    preferred_name text,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_facts (                      -- "Lance likes Elden Ring"
    id           bigserial PRIMARY KEY,
    character    text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    user_id      text NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    fact         text NOT NULL,
    embedding    vector(384),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON user_facts (character, user_id);

CREATE TABLE user_relationships (              -- how a character feels about a user
    character    text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    user_id      text NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    affinity     real NOT NULL DEFAULT 0,        -- -1 (hates) .. 1 (loves)
    notes        text,                           -- running jokes, grudges
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character, user_id)
);

-- ── Memory ────────────────────────────────────────────────────────────────
CREATE TABLE memories (
    id               bigserial PRIMARY KEY,
    character        text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    content          text NOT NULL,
    embedding        vector(384) NOT NULL,
    importance       real NOT NULL DEFAULT 0.5,  -- 0..1
    user_ids         text[] NOT NULL DEFAULT '{}',
    kind             text NOT NULL DEFAULT 'event', -- event | summary
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_recalled_at timestamptz
);
CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memories (character);
CREATE INDEX ON memories USING gin (user_ids);

CREATE TABLE lore (                            -- things the character always knows
    id           bigserial PRIMARY KEY,
    character    text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    title        text,
    content      text NOT NULL,
    embedding    vector(384) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON lore USING hnsw (embedding vector_cosine_ops);

CREATE TABLE knowledge (                       -- things every character knows (the server, games, ...)
    id           bigserial PRIMARY KEY,
    title        text,
    content      text NOT NULL,
    embedding    vector(384) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON knowledge USING hnsw (embedding vector_cosine_ops);

-- ── Conversations ─────────────────────────────────────────────────────────
CREATE TABLE sessions (
    id           bigserial PRIMARY KEY,
    character    text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    guild_id     text,
    channel_id   text,
    kind         text NOT NULL DEFAULT 'text',   -- text | voice
    started_at   timestamptz NOT NULL DEFAULT now(),
    ended_at     timestamptz,
    summary_id   bigint REFERENCES memories(id) ON DELETE SET NULL
);

CREATE TABLE messages (
    id             bigserial PRIMARY KEY,
    character      text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE,
    session_id     bigint REFERENCES sessions(id) ON DELETE SET NULL,
    user_id        text REFERENCES users(discord_id) ON DELETE SET NULL,
    role           text NOT NULL,                -- user | assistant
    content        text NOT NULL,
    source         text NOT NULL DEFAULT 'text', -- text | voice
    stt_confidence real,
    guild_id       text,
    channel_id     text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON messages (character, channel_id, created_at DESC);

-- ── Discord ───────────────────────────────────────────────────────────────
CREATE TABLE guild_settings (
    guild_id          text PRIMARY KEY,
    active_character  text REFERENCES characters(slug) ON DELETE SET NULL,
    settings          jsonb NOT NULL DEFAULT '{}' -- prefix, allowed channels, voice on/off
);

CREATE TABLE channel_settings (
    channel_id   text PRIMARY KEY,
    guild_id     text,
    settings     jsonb NOT NULL DEFAULT '{}'      -- mention-only, nsfw, ...
);

-- ── Operations ────────────────────────────────────────────────────────────
CREATE TABLE logs (
    id           bigserial PRIMARY KEY,
    level        text NOT NULL DEFAULT 'info',
    source       text,                            -- bot | tts | stt | controller
    message      text NOT NULL,
    data         jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON logs (created_at DESC);

CREATE TABLE generation_metrics (
    id             bigserial PRIMARY KEY,
    character      text,
    message_id     bigint REFERENCES messages(id) ON DELETE SET NULL,
    model          text,
    prompt_tokens  int,
    output_tokens  int,
    llm_ms         int,
    tts_ms         int,
    stt_ms         int,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE modules (
    name         text PRIMARY KEY,
    enabled      boolean NOT NULL DEFAULT true,
    body         jsonb NOT NULL DEFAULT '{}'      -- contents of body.json
);

CREATE TABLE feedback (
    id           bigserial PRIMARY KEY,
    message_id   bigint NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id      text,
    reaction     text NOT NULL,                   -- 👍 👎 ...
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Starter character so memories can be stored right away.
INSERT INTO characters (slug, name, model) VALUES ('ohm', 'Ohm', 'gemma-4-e4b-uncensored');
