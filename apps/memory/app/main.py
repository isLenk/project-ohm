"""Memory API for the virtual assistant. Runs on the NAS next to Postgres.

The PC only sends text; embeddings are created here (CPU, ONNX via fastembed).
Every route except /health requires the X-API-Key header.
"""

import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from fastembed import TextEmbedding
from pgvector.psycopg import register_vector
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

API_KEY = os.environ["API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MODEL_CACHE = os.getenv("MODEL_CACHE", "/models")

# Search ranking: similarity matters most, then importance, then recency.
W_SIM, W_IMPORTANCE, W_RECENCY = 0.7, 0.2, 0.1
RECENCY_HALF_LIFE_DAYS = 30
CANDIDATES = 50  # nearest neighbours fetched before re-ranking

# Reflection: the NAS decides when the PC should look back over a stretch of conversation.
# Voice is most of the traffic, so it goes by how much was said and whether it's over, not a clock.
REFLECT_CHECK_MINUTES = int(os.getenv("REFLECT_CHECK_MINUTES", 10))  # how often to look
REFLECT_MIN_MESSAGES = int(os.getenv("REFLECT_MIN_MESSAGES", 40))  # fewer than this isn't worth a cloud call
REFLECT_QUIET_MINUTES = int(os.getenv("REFLECT_QUIET_MINUTES", 30))  # wait until the conversation is over, so a session is reflected on whole
REFLECT_STALE_MINUTES = int(os.getenv("REFLECT_STALE_MINUTES", 60))  # a running job older than this is retried (the PC probably went off)
REFLECT_RETRY_MINUTES = 60  # after a failed job, wait before queueing the same messages again so a broken worker isn't hammered

# A trait can drift with what happens in chat but never flip into its opposite.
TRAIT_FLOOR, TRAIT_CEILING = 0.1, 0.9

pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row},
    configure=register_vector,
    open=True,
)
embedder = TextEmbedding(EMBED_MODEL, cache_dir=MODEL_CACHE)


def embed(text: str) -> np.ndarray:
    return next(iter(embedder.embed([text]))).astype(np.float32)


def require_key(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key")


app = FastAPI(title="Virtual Assistant Memory API")
auth = [Depends(require_key)]


def run(sql: str, params: Any = None, fetch: str = "all"):
    try:
        with pool.connection() as conn:
            cur = conn.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None
    except errors.ForeignKeyViolation as e:
        raise HTTPException(404, f"Referenced row not found: {e.diag.message_detail}")


# Tables added after the first deploy. db/init only runs on an empty database, so they're made here too.
run(
    "CREATE TABLE IF NOT EXISTS knowledge (id bigserial PRIMARY KEY, title text, content text NOT NULL, "
    "embedding vector(384) NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
    fetch="none",
)
run("CREATE INDEX IF NOT EXISTS knowledge_embedding_idx ON knowledge USING hnsw (embedding vector_cosine_ops)", fetch="none")
run(
    "CREATE TABLE IF NOT EXISTS glossary (source text NOT NULL, term text NOT NULL, definition text, relation text, "
    "related_term text, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, term))",
    fetch="none",
)
# Every name someone goes by, so a message that names them can bring up what's known about them.
run("ALTER TABLE users ADD COLUMN IF NOT EXISTS username text", fetch="none")
run("ALTER TABLE users ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}'", fetch="none")
# Their Discord avatar, for the app's people lists.
run("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url text", fetch="none")
# Reflection jobs, queued here and run by the PC's Curator.
run(
    "CREATE TABLE IF NOT EXISTS reflections (id bigserial PRIMARY KEY, "
    "character text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE, status text NOT NULL DEFAULT 'pending', "
    "from_id bigint NOT NULL DEFAULT 0, to_id bigint NOT NULL, messages int NOT NULL DEFAULT 0, "
    "created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz, "
    "result jsonb, error text)",
    fetch="none",
)
run("CREATE INDEX IF NOT EXISTS reflections_character_status_idx ON reflections (character, status)", fetch="none")
# One open job per character, so the loop and the app's Reflect now can't both queue the same messages.
run(
    "CREATE UNIQUE INDEX IF NOT EXISTS reflections_one_open_idx ON reflections (character) "
    "WHERE status IN ('pending', 'running')",
    fetch="none",
)
# The loop counts a character's messages after an id every few minutes.
run("CREATE INDEX IF NOT EXISTS messages_character_id_idx ON messages (character, id)", fetch="none")
run(
    "CREATE TABLE IF NOT EXISTS traits (character text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE, "
    "trait text NOT NULL, value real NOT NULL, floor real NOT NULL DEFAULT 0.1, ceiling real NOT NULL DEFAULT 0.9, "
    "updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (character, trait))",
    fetch="none",
)
run(
    "CREATE TABLE IF NOT EXISTS trait_history (id bigserial PRIMARY KEY, "
    "character text NOT NULL REFERENCES characters(slug) ON DELETE CASCADE, trait text NOT NULL, value real NOT NULL, "
    "reason text, created_at timestamptz NOT NULL DEFAULT now())",
    fetch="none",
)
run("CREATE INDEX IF NOT EXISTS trait_history_idx ON trait_history (character, trait, created_at DESC)", fetch="none")
# Where Ohm's personality starts. Only seeded while he has no traits, so reflection's changes are kept.
run(
    """
    INSERT INTO traits (character, trait, value)
    SELECT 'ohm', t.trait, t.value
    FROM (VALUES ('sarcastic', 0.6), ('blunt', 0.5), ('wholesome', 0.3), ('chaotic', 0.4),
                 ('curious', 0.4), ('talkative', 0.4)) AS t (trait, value)
    WHERE EXISTS (SELECT 1 FROM characters WHERE slug = 'ohm')
      AND NOT EXISTS (SELECT 1 FROM traits WHERE character = 'ohm')
    ON CONFLICT DO NOTHING
    """,
    fetch="none",
)


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    run("SELECT 1", fetch="one")
    return {"ok": True, "embed_model": EMBED_MODEL}


# ── Characters ──────────────────────────────────────────────────────────────
class Character(BaseModel):
    name: str
    model: Optional[str] = None
    personality: Optional[str] = None
    configs: dict = Field(default_factory=dict)
    voice: dict = Field(default_factory=dict)
    modules: list[str] = Field(default_factory=list)


@app.get("/characters", dependencies=auth)
def list_characters():
    return run("SELECT * FROM characters ORDER BY slug")


@app.get("/characters/{slug}", dependencies=auth)
def get_character(slug: str):
    row = run("SELECT * FROM characters WHERE slug = %s", (slug,), "one")
    if not row:
        raise HTTPException(404, "Character not found")
    return row


@app.put("/characters/{slug}", dependencies=auth)
def upsert_character(slug: str, c: Character):
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO characters (slug, name, model, personality, configs, voice, modules)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, model = EXCLUDED.model,
                personality = EXCLUDED.personality, configs = EXCLUDED.configs,
                voice = EXCLUDED.voice, modules = EXCLUDED.modules, updated_at = now()
            RETURNING *
            """,
            (slug, c.name, c.model, c.personality, Jsonb(c.configs), Jsonb(c.voice), c.modules),
        ).fetchone()
        conn.execute(
            "INSERT INTO character_versions (character, personality, configs) VALUES (%s, %s, %s)",
            (slug, c.personality, Jsonb(c.configs)),
        )
    return row


class CharacterPatch(BaseModel):
    """Partial update: only fields that are sent change. `configs` is merged, not replaced."""
    name: Optional[str] = None
    model: Optional[str] = None
    personality: Optional[str] = None
    configs: Optional[dict] = None
    voice: Optional[dict] = None
    modules: Optional[list[str]] = None


@app.patch("/characters/{slug}", dependencies=auth)
def patch_character(slug: str, c: CharacterPatch):
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE characters SET
                name        = COALESCE(%(name)s, name),
                model       = COALESCE(%(model)s, model),
                personality = COALESCE(%(personality)s, personality),
                configs     = configs || COALESCE(%(configs)s, '{}'::jsonb),
                voice       = COALESCE(%(voice)s, voice),
                modules     = COALESCE(%(modules)s, modules),
                updated_at  = now()
            WHERE slug = %(slug)s
            RETURNING *
            """,
            {
                "slug": slug,
                "name": c.name,
                "model": c.model,
                "personality": c.personality,
                "configs": Jsonb(c.configs) if c.configs is not None else None,
                "voice": Jsonb(c.voice) if c.voice is not None else None,
                "modules": c.modules,
            },
        ).fetchone()
        if not row:
            raise HTTPException(404, "Character not found")
        if c.personality is not None or c.configs is not None:
            conn.execute(
                "INSERT INTO character_versions (character, personality, configs) VALUES (%s, %s, %s)",
                (slug, row["personality"], Jsonb(row["configs"])),
            )
    return row


@app.delete("/characters/{slug}", dependencies=auth)
def delete_character(slug: str):
    """Deletes the character AND its memories, facts, lore and message history (cascade)."""
    row = run("DELETE FROM characters WHERE slug = %s RETURNING slug", (slug,), "one")
    if not row:
        raise HTTPException(404, "Character not found")
    return {"deleted": slug}


# ── Users & facts ───────────────────────────────────────────────────────────
class User(BaseModel):
    display_name: Optional[str] = None
    preferred_name: Optional[str] = None
    username: Optional[str] = None          # the Discord handle, e.g. "lenk_07"
    aliases: Optional[list[str]] = None     # real name, nicknames. Sent whole: replaces the list
    avatar_url: Optional[str] = None        # their Discord avatar (the server one if they set one)


def _aliases(names: Optional[list[str]]) -> Optional[list[str]]:
    if names is None:
        return None
    out = []
    for n in names:
        n = " ".join(n.split())
        if n and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out


@app.put("/users/{discord_id}", dependencies=auth)
def upsert_user(discord_id: str, u: User):
    return run(
        """
        INSERT INTO users (discord_id, display_name, preferred_name, username, aliases, avatar_url)
        VALUES (%s, %s, %s, %s, COALESCE(%s, '{}'::text[]), %s)
        ON CONFLICT (discord_id) DO UPDATE SET
            display_name = COALESCE(EXCLUDED.display_name, users.display_name),
            preferred_name = COALESCE(EXCLUDED.preferred_name, users.preferred_name),
            username = COALESCE(EXCLUDED.username, users.username),
            aliases = COALESCE(%s, users.aliases),
            avatar_url = COALESCE(EXCLUDED.avatar_url, users.avatar_url),
            last_seen = now()
        RETURNING *
        """,
        (discord_id, u.display_name, u.preferred_name, u.username, _aliases(u.aliases), u.avatar_url,
         _aliases(u.aliases)),
        "one",
    )


@app.get("/users", dependencies=auth)
def list_users():
    """Everyone the NAS has a name for, with every name they go by (the bot matches messages against these)."""
    return run(
        f"""
        SELECT u.discord_id AS user_id, {NAME} AS name, u.display_name, u.preferred_name, u.username, u.aliases,
               u.avatar_url
        FROM users u
        WHERE u.display_name IS NOT NULL OR u.preferred_name IS NOT NULL OR u.username IS NOT NULL
           OR cardinality(u.aliases) > 0
        ORDER BY u.last_seen DESC
        """
    )


class Fact(BaseModel):
    character: str
    fact: str


@app.post("/users/{discord_id}/facts", dependencies=auth)
def add_fact(discord_id: str, f: Fact):
    return run(
        "INSERT INTO user_facts (character, user_id, fact, embedding) VALUES (%s, %s, %s, %s) "
        "RETURNING id, character, user_id, fact, created_at",
        (f.character, discord_id, f.fact, embed(f.fact)),
        "one",
    )


@app.get("/users/{discord_id}/facts", dependencies=auth)
def list_facts(discord_id: str, character: str):
    return run(
        "SELECT id, fact, created_at FROM user_facts WHERE user_id = %s AND character = %s "
        "ORDER BY created_at",
        (discord_id, character),
    )


@app.delete("/users/{discord_id}/facts/{fact_id}", dependencies=auth)
def delete_fact(discord_id: str, fact_id: int):
    run("DELETE FROM user_facts WHERE id = %s AND user_id = %s", (fact_id, discord_id), None)
    return {"deleted": fact_id}


# ── Memories ────────────────────────────────────────────────────────────────
class MemoryIn(BaseModel):
    character: str
    content: str
    importance: float = Field(0.5, ge=0, le=1)
    user_ids: list[str] = Field(default_factory=list)
    kind: str = "event"
    created_at: Optional[datetime] = None  # set when importing old memories


class MemorySearch(BaseModel):
    character: str
    text: str
    limit: int = Field(5, ge=1, le=50)
    user_id: Optional[str] = None  # only memories involving this user


@app.post("/memories", dependencies=auth)
def add_memory(m: MemoryIn):
    return run(
        """
        INSERT INTO memories (character, content, embedding, importance, user_ids, kind, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
        RETURNING id, character, content, importance, user_ids, kind, created_at
        """,
        (m.character, m.content, embed(m.content), m.importance, m.user_ids, m.kind, m.created_at),
        "one",
    )


@app.post("/memories/search", dependencies=auth)
def search_memories(s: MemorySearch):
    params = {
        "q": embed(s.text),
        "c": s.character,
        "u": s.user_id,
        "k": CANDIDATES,
        "n": s.limit,
        "ws": W_SIM,
        "wi": W_IMPORTANCE,
        "wr": W_RECENCY,
        "hl": RECENCY_HALF_LIFE_DAYS,
    }
    rows = run(
        """
        WITH candidates AS (
            (SELECT id, content, importance, user_ids, kind, created_at,
                    1 - (embedding <=> %(q)s) AS similarity
             FROM memories
             WHERE character = %(c)s
               AND (%(u)s::text IS NULL OR %(u)s::text = ANY(user_ids))
             ORDER BY embedding <=> %(q)s
             LIMIT %(k)s)
            UNION ALL
            -- Facts kept for one person (the People tab, the Curator). Their ids are user_facts ids.
            (SELECT id, fact, 0.7, ARRAY[user_id], 'user_fact', created_at, 1 - (embedding <=> %(q)s)
             FROM user_facts
             WHERE character = %(c)s AND embedding IS NOT NULL
               AND (%(u)s::text IS NULL OR user_id = %(u)s::text)
             ORDER BY embedding <=> %(q)s
             LIMIT %(k)s)
            UNION ALL
            -- Lore and shared knowledge never fade, so they rank as important and brand new.
            (SELECT id, content, 0.8, '{}'::text[], 'lore', now(), 1 - (embedding <=> %(q)s)
             FROM lore WHERE character = %(c)s AND %(u)s::text IS NULL
             ORDER BY embedding <=> %(q)s LIMIT 5)
            UNION ALL
            (SELECT id, content, 0.8, '{}'::text[], 'knowledge', now(), 1 - (embedding <=> %(q)s)
             FROM knowledge WHERE %(u)s::text IS NULL
             ORDER BY embedding <=> %(q)s LIMIT 5)
        )
        SELECT *,
               %(ws)s * similarity
             + %(wi)s * importance
             + %(wr)s * power(0.5, extract(epoch FROM now() - created_at) / 86400 / %(hl)s)
               AS score
        FROM candidates
        ORDER BY score DESC
        LIMIT %(n)s
        """,
        params,
    )
    recalled = [r["id"] for r in rows if r["kind"] not in ("lore", "knowledge", "user_fact")]
    if recalled:
        run("UPDATE memories SET last_recalled_at = now() WHERE id = ANY(%s)", (recalled,), fetch="none")
    return rows


@app.delete("/memories/{memory_id}", dependencies=auth)
def delete_memory(memory_id: int):
    run("DELETE FROM memories WHERE id = %s", (memory_id,), fetch="none")
    return {"deleted": memory_id}


@app.get("/memories", dependencies=auth)
def list_memories(character: str, user_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 200):
    """Newest first. With user_id, only memories that involve that person."""
    return run(
        """
        SELECT id, content, importance, user_ids, kind, created_at, last_recalled_at FROM memories
        WHERE character = %s
          AND (%s::text IS NULL OR %s::text = ANY(user_ids))
          AND (%s::text IS NULL OR kind = %s::text)
        ORDER BY created_at DESC LIMIT %s
        """,
        (character, user_id, user_id, kind, kind, min(limit, 1000)),
    )


class Edit(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = Field(None, ge=0, le=1)


@app.patch("/memories/{memory_id}", dependencies=auth)
def edit_memory(memory_id: int, e: Edit):
    row = run(
        """
        UPDATE memories SET content = COALESCE(%s, content), importance = COALESCE(%s, importance),
                            embedding = COALESCE(%s, embedding)
        WHERE id = %s RETURNING id, content, importance, user_ids, kind, created_at
        """,
        (e.content, e.importance, embed(e.content) if e.content else None, memory_id),
        "one",
    )
    if not row:
        raise HTTPException(404, "Memory not found")
    return row


# ── Lore (one character) & knowledge (every character) ──────────────────────
class Note(BaseModel):
    content: str
    title: Optional[str] = None


@app.get("/lore", dependencies=auth)
def list_lore(character: str):
    return run("SELECT id, title, content, created_at FROM lore WHERE character = %s ORDER BY created_at DESC", (character,))


@app.post("/lore", dependencies=auth)
def add_lore(character: str, n: Note):
    return run(
        "INSERT INTO lore (character, title, content, embedding) VALUES (%s, %s, %s, %s) "
        "RETURNING id, title, content, created_at",
        (character, n.title, n.content, embed(n.content)),
        "one",
    )


@app.patch("/lore/{lore_id}", dependencies=auth)
def edit_lore(lore_id: int, n: Note):
    return _edit_note("lore", lore_id, n)


@app.delete("/lore/{lore_id}", dependencies=auth)
def delete_lore(lore_id: int):
    run("DELETE FROM lore WHERE id = %s", (lore_id,), fetch="none")
    return {"deleted": lore_id}


@app.get("/knowledge", dependencies=auth)
def list_knowledge():
    return run("SELECT id, title, content, created_at FROM knowledge ORDER BY created_at DESC")


@app.post("/knowledge", dependencies=auth)
def add_knowledge(n: Note):
    return run(
        "INSERT INTO knowledge (title, content, embedding) VALUES (%s, %s, %s) RETURNING id, title, content, created_at",
        (n.title, n.content, embed(n.content)),
        "one",
    )


@app.patch("/knowledge/{knowledge_id}", dependencies=auth)
def edit_knowledge(knowledge_id: int, n: Note):
    return _edit_note("knowledge", knowledge_id, n)


@app.delete("/knowledge/{knowledge_id}", dependencies=auth)
def delete_knowledge(knowledge_id: int):
    run("DELETE FROM knowledge WHERE id = %s", (knowledge_id,), fetch="none")
    return {"deleted": knowledge_id}


# ── Glossary ────────────────────────────────────────────────────────────────
# Slang, game names and typos a group uses, defined in the Curator's General terminology tab. Every
# character gets them, so the bot reads "hll" or "ceebs" right. Each Curator project is one `source`.
class GlossaryTerm(BaseModel):
    term: str
    definition: Optional[str] = None
    relation: Optional[str] = None  # typo | same, with related_term
    related_term: Optional[str] = None


class GlossaryIn(BaseModel):
    terms: list[GlossaryTerm]


@app.get("/glossary", dependencies=auth)
def list_glossary():
    return run("SELECT term, definition, relation, related_term, source, updated_at FROM glossary ORDER BY term, updated_at DESC")


@app.put("/glossary/{source}", dependencies=auth)
def replace_glossary(source: str, g: GlossaryIn):
    """Replace everything one source (a Curator project) defined, so removed terms go too."""
    rows = [(source, t.term.strip().lower(), t.definition, t.relation, t.related_term)
            for t in g.terms if t.term.strip() and (t.definition or t.related_term)]
    with pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM glossary WHERE source = %s", (source,))
        if rows:
            conn.cursor().executemany(
                "INSERT INTO glossary (source, term, definition, relation, related_term) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (source, term) DO UPDATE SET definition = EXCLUDED.definition, relation = EXCLUDED.relation, "
                "related_term = EXCLUDED.related_term, updated_at = now()",
                rows,
            )
    return {"source": source, "terms": len(rows)}


def _edit_note(table: str, note_id: int, n: Note):
    # `table` is one of two literals above, never user input.
    row = run(
        f"UPDATE {table} SET content = %s, title = COALESCE(%s, title), embedding = %s "
        "WHERE id = %s RETURNING id, title, content, created_at",
        (n.content, n.title, embed(n.content), note_id),
        "one",
    )
    if not row:
        raise HTTPException(404, "Not found")
    return row


@app.patch("/users/{discord_id}/facts/{fact_id}", dependencies=auth)
def edit_fact(discord_id: str, fact_id: int, n: Note):
    row = run(
        "UPDATE user_facts SET fact = %s, embedding = %s WHERE id = %s AND user_id = %s "
        "RETURNING id, fact, created_at",
        (n.content, embed(n.content), fact_id, discord_id),
        "one",
    )
    if not row:
        raise HTTPException(404, "Fact not found")
    return row


# ── Messages ────────────────────────────────────────────────────────────────
class MessageIn(BaseModel):
    character: str
    role: str  # user | assistant
    content: str
    user_id: Optional[str] = None
    session_id: Optional[int] = None
    source: str = "text"  # text | voice
    stt_confidence: Optional[float] = None
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None


@app.post("/messages", dependencies=auth)
def add_message(m: MessageIn):
    if m.user_id:
        run(
            "INSERT INTO users (discord_id) VALUES (%s) "
            "ON CONFLICT (discord_id) DO UPDATE SET last_seen = now()",
            (m.user_id,),
            fetch="none",
        )
    return run(
        """
        INSERT INTO messages (character, session_id, user_id, role, content, source,
                              stt_confidence, guild_id, channel_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (m.character, m.session_id, m.user_id, m.role, m.content, m.source,
         m.stt_confidence, m.guild_id, m.channel_id),
        "one",
    )


@app.get("/messages/recent", dependencies=auth)
def recent_messages(character: str, channel_id: Optional[str] = None, limit: int = 20):
    rows = run(
        """
        SELECT * FROM messages
        WHERE character = %s AND (%s::text IS NULL OR channel_id = %s::text)
        ORDER BY created_at DESC LIMIT %s
        """,
        (character, channel_id, channel_id, min(limit, 200)),
    )
    return list(reversed(rows))  # oldest first, ready for a prompt


@app.get("/messages", dependencies=auth)
def list_messages(character: str, after_id: Optional[int] = None, before_id: Optional[int] = None, limit: int = 500):
    """Messages in the order they were logged, with ids strictly between `after_id` and `before_id`.

    This is what reflection reads. Page through a long stretch by passing the last id back as `after_id`.
    """
    return run(
        f"""
        SELECT m.id, m.role, m.user_id, CASE WHEN m.role = 'user' THEN {NAME} END AS name,
               m.content, m.source, m.channel_id, m.created_at
        FROM messages m LEFT JOIN users u ON u.discord_id = m.user_id
        WHERE m.character = %(c)s
          AND (%(a)s::bigint IS NULL OR m.id > %(a)s::bigint)
          AND (%(b)s::bigint IS NULL OR m.id < %(b)s::bigint)
        ORDER BY m.id LIMIT %(n)s
        """,
        {"c": character, "a": after_id, "b": before_id, "n": min(limit, 2000)},
    )


# ── Activity (the controller's dashboard) ───────────────────────────────────
NAME = "COALESCE(u.preferred_name, u.display_name, u.discord_id)"


@app.get("/stats", dependencies=auth)
def stats(character: str, days: int = 7, tz: str = "UTC"):
    """Counts for the last `days` days, the period before it for comparison, and messages per hour of day.

    Reply time is from when the message Ohm answered was logged to when his reply was, so it
    includes speech-to-text and thinking time but not the time to speak it.
    """
    p = {"c": character, "d": days, "tz": tz}
    totals = run(
        """
        SELECT count(*) FILTER (WHERE role = 'user') AS heard,
               count(*) FILTER (WHERE role = 'assistant') AS replied,
               count(DISTINCT user_id) FILTER (WHERE role = 'user') AS people,
               count(*) FILTER (WHERE source = 'voice') AS voice,
               count(*) AS total
        FROM messages
        WHERE character = %(c)s AND created_at >= now() - make_interval(days => %(d)s)
        """,
        p,
        "one",
    )
    before = run(
        """
        SELECT count(*) AS heard FROM messages
        WHERE character = %(c)s AND role = 'user'
          AND created_at >= now() - make_interval(days => 2 * %(d)s)
          AND created_at < now() - make_interval(days => %(d)s)
        """,
        p,
        "one",
    )
    new_people = run(
        """
        SELECT count(*) AS n FROM users u
        WHERE u.first_seen >= now() - make_interval(days => %(d)s)
          AND EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.discord_id AND m.character = %(c)s)
        """,
        p,
        "one",
    )
    reply = run(
        """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS median_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95_ms
        FROM (
            SELECT extract(epoch FROM created_at - prev_at) * 1000 AS ms
            FROM (
                SELECT role, created_at,
                       lag(role) OVER w AS prev_role, lag(created_at) OVER w AS prev_at
                FROM messages
                WHERE character = %(c)s AND created_at >= now() - make_interval(days => %(d)s)
                WINDOW w AS (PARTITION BY channel_id ORDER BY created_at)
            ) t
            WHERE role = 'assistant' AND prev_role = 'user' AND created_at - prev_at < interval '2 minutes'
        ) r
        """,
        p,
        "one",
    )
    hours = run(
        """
        SELECT extract(hour FROM created_at AT TIME ZONE %(tz)s)::int AS hour,
               count(*) FILTER (WHERE role = 'user') AS heard,
               count(*) FILTER (WHERE role = 'assistant') AS replied
        FROM messages
        WHERE character = %(c)s AND created_at >= now() - make_interval(days => %(d)s)
        GROUP BY 1
        """,
        p,
    )
    top = run(
        f"""
        SELECT m.user_id, {NAME} AS name, count(*) AS messages
        FROM messages m JOIN users u ON u.discord_id = m.user_id
        WHERE m.character = %(c)s AND m.role = 'user' AND m.created_at >= now() - make_interval(days => %(d)s)
        GROUP BY m.user_id, u.discord_id ORDER BY messages DESC LIMIT 5
        """,
        p,
    )
    by_hour = {h["hour"]: h for h in hours}
    return {
        **totals,
        "heard_before": before["heard"],
        "new_people": new_people["n"],
        "reply_ms": {"median": reply["median_ms"], "p95": reply["p95_ms"]},
        "hours": [{"hour": h, "heard": by_hour.get(h, {}).get("heard", 0),
                   "replied": by_hour.get(h, {}).get("replied", 0)} for h in range(24)],
        "top": top,
        "reflections": run(
            "SELECT max(finished_at) FILTER (WHERE status = 'done') AS last_at, "
            "count(*) FILTER (WHERE status = 'pending') AS pending FROM reflections WHERE character = %s",
            (character,), "one"),
    }


@app.get("/people", dependencies=auth)
def people(character: str, days: Optional[int] = None, limit: int = 100):
    """Everyone who has talked to the character, most recent first, with how much Ohm knows about them."""
    return run(
        f"""
        SELECT u.discord_id AS user_id, {NAME} AS name, u.display_name, u.username, u.aliases, u.avatar_url,
               count(m.id) AS messages, max(m.created_at) AS last_seen,
               count(m.id) FILTER (WHERE m.source = 'voice') AS voice,
               (SELECT count(*) FROM user_facts f WHERE f.user_id = u.discord_id AND f.character = %(c)s) AS facts,
               (SELECT count(*) FROM memories x WHERE x.character = %(c)s AND u.discord_id = ANY(x.user_ids)) AS memories
        FROM users u
        JOIN messages m ON m.user_id = u.discord_id AND m.character = %(c)s AND m.role = 'user'
             AND (%(d)s::int IS NULL OR m.created_at >= now() - make_interval(days => %(d)s::int))
        GROUP BY u.discord_id
        ORDER BY last_seen DESC LIMIT %(n)s
        """,
        {"c": character, "d": days, "n": min(limit, 500)},
    )


@app.get("/people/{discord_id}/messages", dependencies=auth)
def person_messages(discord_id: str, character: str, limit: int = 20):
    """The person's latest messages, each followed by the reply Ohm gave to it (if any), oldest first."""
    rows = run(
        """
        WITH mine AS (
            SELECT id, role, content, source, created_at, user_id, channel_id FROM messages
            WHERE character = %(c)s AND user_id = %(u)s ORDER BY created_at DESC LIMIT %(n)s
        )
        SELECT id, role, content, source, created_at, user_id FROM mine
        UNION
        SELECT r.id, r.role, r.content, r.source, r.created_at, r.user_id
        FROM mine CROSS JOIN LATERAL (
            SELECT * FROM messages a
            WHERE a.character = %(c)s AND a.role = 'assistant'
              AND a.channel_id IS NOT DISTINCT FROM mine.channel_id
              AND a.created_at > mine.created_at AND a.created_at < mine.created_at + interval '2 minutes'
            ORDER BY a.created_at LIMIT 1
        ) r
        """,
        {"c": character, "u": discord_id, "n": min(limit, 200)},
    )
    return sorted(rows, key=lambda r: r["created_at"])


@app.get("/conversations", dependencies=auth)
def conversations(character: str, days: int = 7, gap_minutes: int = 30, limit: int = 50):
    """Messages grouped per channel, split wherever the channel went quiet for `gap_minutes`."""
    rows = run(
        """
        WITH m AS (
            SELECT channel_id, user_id, source, created_at,
                   CASE WHEN created_at - lag(created_at) OVER (PARTITION BY channel_id ORDER BY created_at)
                             > make_interval(mins => %(g)s) THEN 1 ELSE 0 END AS starts
            FROM messages
            WHERE character = %(c)s AND created_at >= now() - make_interval(days => %(d)s)
        ), g AS (
            SELECT *, sum(starts) OVER (PARTITION BY channel_id ORDER BY created_at) AS n FROM m
        )
        SELECT channel_id, min(created_at) AS started_at, max(created_at) AS ended_at, count(*) AS messages,
               bool_or(source = 'voice') AS voice, array_remove(array_agg(DISTINCT user_id), NULL) AS user_ids
        FROM g GROUP BY channel_id, n ORDER BY ended_at DESC LIMIT %(l)s
        """,
        {"c": character, "d": days, "g": gap_minutes, "l": min(limit, 500)},
    )
    ids = sorted({u for r in rows for u in r["user_ids"]})
    names = {r["discord_id"]: r["name"] for r in run(
        f"SELECT u.discord_id, {NAME} AS name FROM users u WHERE u.discord_id = ANY(%s)", (ids,)
    )} if ids else {}
    for r in rows:
        r["people"] = [names.get(u, u) for u in r.pop("user_ids")]
    return rows


# ── Logs & metrics ──────────────────────────────────────────────────────────
class LogIn(BaseModel):
    message: str
    level: str = "info"
    source: Optional[str] = None
    data: Optional[dict] = None


def log(message: str, source: Optional[str] = None, level: str = "info", data: Optional[dict] = None):
    run(
        "INSERT INTO logs (level, source, message, data) VALUES (%s, %s, %s, %s)",
        (level, source, message, Jsonb(data) if data is not None else None),
        fetch="none",
    )


@app.post("/logs", dependencies=auth)
def add_log(l: LogIn):
    log(l.message, l.source, l.level, l.data)
    return {"ok": True}


@app.get("/logs", dependencies=auth)
def list_logs(source: Optional[str] = None, level: Optional[str] = None, since: Optional[datetime] = None,
              limit: int = 100):
    """Newest first. `source` is a comma list (brain,mood,reflect), which is how the app shows each feed."""
    sources = [s.strip() for s in source.split(",") if s.strip()] if source else None
    return run(
        """
        SELECT id, level, source, message, data, created_at FROM logs
        WHERE (%(s)s::text[] IS NULL OR source = ANY(%(s)s::text[]))
          AND (%(l)s::text IS NULL OR level = %(l)s::text)
          AND (%(t)s::timestamptz IS NULL OR created_at > %(t)s::timestamptz)
        ORDER BY created_at DESC, id DESC LIMIT %(n)s
        """,
        {"s": sources, "l": level, "t": since, "n": min(limit, 500)},
    )


class MetricIn(BaseModel):
    character: Optional[str] = None
    message_id: Optional[int] = None
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    llm_ms: Optional[int] = None
    tts_ms: Optional[int] = None
    stt_ms: Optional[int] = None


@app.post("/metrics", dependencies=auth)
def add_metric(m: MetricIn):
    run(
        """
        INSERT INTO generation_metrics (character, message_id, model, prompt_tokens,
                                        output_tokens, llm_ms, tts_ms, stt_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (m.character, m.message_id, m.model, m.prompt_tokens, m.output_tokens,
         m.llm_ms, m.tts_ms, m.stt_ms),
        fetch="none",
    )
    return {"ok": True}


# ── Relationships ───────────────────────────────────────────────────────────
class RelationshipPatch(BaseModel):
    affinity_delta: Optional[float] = None  # added to the current affinity (after `affinity`, if both are sent)
    affinity: Optional[float] = None        # set outright
    notes: Optional[str] = None             # replaces the notes


@app.get("/relationships", dependencies=auth)
def list_relationships(character: str):
    """How the character feels about each person, warmest first."""
    return run(
        f"""
        SELECT r.user_id, {NAME} AS name, r.affinity, r.notes, r.updated_at
        FROM user_relationships r JOIN users u ON u.discord_id = r.user_id
        WHERE r.character = %s
        ORDER BY r.affinity DESC, r.updated_at DESC
        """,
        (character,),
    )


@app.patch("/relationships/{character}/{user_id}", dependencies=auth)
def patch_relationship(character: str, user_id: str, p: RelationshipPatch):
    """Creates the row the first time. Affinity stays within -1 (hates) .. 1 (loves)."""
    return run(
        f"""
        WITH r AS (
            INSERT INTO user_relationships (character, user_id, affinity, notes)
            VALUES (%(c)s, %(u)s,
                    greatest(-1, least(1, COALESCE(%(a)s::real, 0) + COALESCE(%(d)s::real, 0))), %(n)s::text)
            ON CONFLICT (character, user_id) DO UPDATE SET
                affinity = greatest(-1, least(1, COALESCE(%(a)s::real, user_relationships.affinity)
                                                 + COALESCE(%(d)s::real, 0))),
                notes = COALESCE(%(n)s::text, user_relationships.notes),
                updated_at = now()
            RETURNING *
        )
        SELECT r.user_id, {NAME} AS name, r.affinity, r.notes, r.updated_at
        FROM r JOIN users u ON u.discord_id = r.user_id
        """,
        {"c": character, "u": user_id, "a": p.affinity, "d": p.affinity_delta, "n": p.notes},
        "one",
    )


# ── Reflection ──────────────────────────────────────────────────────────────
# The NAS queues a job when enough has been said and the chat has gone quiet; the PC's Curator polls
# /reflections/due, claims it, reads /messages, writes memories, relationships and traits, then finishes it.
class ReflectionFinish(BaseModel):
    result: Optional[dict] = None  # counts and a short summary of what it wrote
    error: Optional[str] = None    # set when it failed; the job is marked failed instead of done


def queue_reflection(character: str, min_messages: int, quiet_minutes: Optional[int], retry_minutes: Optional[int]):
    """Queue a job for every message since the last finished reflection. Returns the row, or None when it isn't due.

    The unique index on open jobs means a second job for the same character is simply not inserted.
    """
    return run(
        """
        WITH last AS (
            SELECT COALESCE(max(to_id), 0) AS id FROM reflections WHERE character = %(c)s AND status = 'done'
        )
        INSERT INTO reflections (character, from_id, to_id, messages)
        SELECT %(c)s, last.id, max(m.id), count(m.id)
        FROM last JOIN messages m ON m.character = %(c)s AND m.id > last.id
        GROUP BY last.id
        HAVING count(m.id) >= %(min)s
           AND (%(q)s::int IS NULL OR max(m.created_at) < now() - make_interval(mins => %(q)s::int))
           AND (%(r)s::int IS NULL OR NOT EXISTS (
                SELECT 1 FROM reflections f WHERE f.character = %(c)s AND f.status = 'failed'
                  AND f.finished_at > now() - make_interval(mins => %(r)s::int)))
        ON CONFLICT (character) WHERE status IN ('pending', 'running') DO NOTHING
        RETURNING *
        """,
        {"c": character, "min": max(min_messages, 1), "q": quiet_minutes, "r": retry_minutes},
        "one",
    )


def reflect_tick():
    for row in run(
        "UPDATE reflections SET status = 'pending', started_at = NULL "
        "WHERE status = 'running' AND started_at < now() - make_interval(mins => %s) RETURNING id, character",
        (REFLECT_STALE_MINUTES,),
    ):
        log(f"Reflection {row['id']} for {row['character']} took too long, queued it again", "reflect", "warning",
            {"reflection_id": row["id"], "character": row["character"]})
    for c in run("SELECT slug FROM characters"):
        job = queue_reflection(c["slug"], REFLECT_MIN_MESSAGES, REFLECT_QUIET_MINUTES, REFLECT_RETRY_MINUTES)
        if job:
            log(f"Queued a reflection for {c['slug']}: {job['messages']} new messages", "reflect",
                data={"reflection_id": job["id"], "character": c["slug"], "messages": job["messages"]})


def reflect_loop():
    while True:
        try:
            reflect_tick()
        except Exception as e:  # a bad tick (database restarting, ...) waits for the next one
            print(f"reflect tick failed: {e!r}", flush=True)
            try:
                log(f"Reflection check failed: {e!r}", "reflect", "error")
            except Exception:
                pass
        time.sleep(REFLECT_CHECK_MINUTES * 60)


@app.on_event("startup")
def start_reflect_loop():
    threading.Thread(target=reflect_loop, name="reflect", daemon=True).start()


@app.get("/reflections", dependencies=auth)
def list_reflections(character: Optional[str] = None, limit: int = 20):
    return run(
        "SELECT * FROM reflections WHERE (%(c)s::text IS NULL OR character = %(c)s::text) ORDER BY id DESC LIMIT %(n)s",
        {"c": character, "n": min(limit, 200)},
    )


@app.get("/reflections/due", dependencies=auth)
def due_reflection():
    """The oldest pending job of any character, or null. Looking doesn't claim it."""
    return run("SELECT * FROM reflections WHERE status = 'pending' ORDER BY id LIMIT 1", fetch="one")


@app.post("/reflections/request", dependencies=auth)
def request_reflection(character: str):
    """The app's Reflect now: queue everything since the last reflection, however little, without waiting for quiet."""
    job = queue_reflection(character, 1, None, None)
    if job:
        log(f"Queued a reflection for {character}: {job['messages']} new messages (asked for)", "reflect",
            data={"reflection_id": job["id"], "character": character, "messages": job["messages"]})
        return job
    if run("SELECT 1 FROM reflections WHERE character = %s AND status IN ('pending', 'running')", (character,), "one"):
        raise HTTPException(409, "A reflection is already pending or running")
    raise HTTPException(409, "Nothing new to reflect on")


@app.post("/reflections/{reflection_id}/claim", dependencies=auth)
def claim_reflection(reflection_id: int):
    """Mark a pending job running. `after_id`/`before_id` are the /messages bounds to read (both exclusive)."""
    row = run(
        "UPDATE reflections SET status = 'running', started_at = now() WHERE id = %s AND status = 'pending' RETURNING *",
        (reflection_id,),
        "one",
    )
    if not row:
        if not run("SELECT 1 FROM reflections WHERE id = %s", (reflection_id,), "one"):
            raise HTTPException(404, "Reflection not found")
        raise HTTPException(409, "Reflection is not pending")
    return {**row, "after_id": row["from_id"], "before_id": row["to_id"] + 1}


@app.post("/reflections/{reflection_id}/finish", dependencies=auth)
def finish_reflection(reflection_id: int, f: ReflectionFinish):
    """Done, or failed when `error` is sent. A job put back to pending as stale can still be finished by its worker."""
    row = run(
        """
        UPDATE reflections SET status = %s, finished_at = now(), result = %s, error = %s
        WHERE id = %s AND status IN ('pending', 'running') RETURNING *
        """,
        ("failed" if f.error else "done", Jsonb(f.result) if f.result is not None else None, f.error, reflection_id),
        "one",
    )
    if not row:
        if not run("SELECT 1 FROM reflections WHERE id = %s", (reflection_id,), "one"):
            raise HTTPException(404, "Reflection not found")
        raise HTTPException(409, "Reflection is already finished")
    if f.error:
        log(f"Reflection {reflection_id} for {row['character']} failed: {f.error}", "reflect", "error",
            {"reflection_id": reflection_id, "character": row["character"]})
    else:
        summary = (f.result or {}).get("summary")
        log(f"Reflection {reflection_id} for {row['character']} done" + (f": {summary}" if summary else ""), "reflect",
            data={"reflection_id": reflection_id, "character": row["character"], "result": f.result})
    return row


# ── Traits ──────────────────────────────────────────────────────────────────
# Personality dials (0..1) reflection nudges over time, each kept between its floor and ceiling.
class TraitIn(BaseModel):
    trait: str
    value: float
    reason: Optional[str] = None  # one line on why it moved, shown in the history


class TraitsIn(BaseModel):
    traits: list[TraitIn]


def _traits(character: str):
    return run(
        "SELECT trait, value, floor, ceiling, updated_at FROM traits WHERE character = %s ORDER BY trait",
        (character,),
    )


@app.get("/traits", dependencies=auth)
def get_traits(character: str, days: int = 30):
    return {
        "traits": _traits(character),
        "history": run(
            "SELECT trait, value, reason, created_at FROM trait_history "
            "WHERE character = %s AND created_at >= now() - make_interval(days => %s) ORDER BY created_at",
            (character, days),
        ),
    }


@app.put("/traits/{character}", dependencies=auth)
def put_traits(character: str, t: TraitsIn):
    """Set trait values, clamped to each trait's floor and ceiling. Only values that changed go in the history."""
    try:
        with pool.connection() as conn, conn.transaction():
            for item in t.traits:
                name = item.trait.strip().lower()
                if not name:
                    continue
                old = conn.execute(
                    "SELECT value, floor, ceiling FROM traits WHERE character = %s AND trait = %s FOR UPDATE",
                    (character, name),
                ).fetchone()
                floor, ceiling = (old["floor"], old["ceiling"]) if old else (TRAIT_FLOOR, TRAIT_CEILING)
                value = min(max(item.value, floor), ceiling)
                if old and abs(old["value"] - value) < 1e-4:  # stored as real, so compare loosely
                    continue
                conn.execute(
                    "INSERT INTO traits (character, trait, value, floor, ceiling) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (character, trait) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                    (character, name, value, floor, ceiling),
                )
                conn.execute(
                    "INSERT INTO trait_history (character, trait, value, reason) VALUES (%s, %s, %s, %s)",
                    (character, name, value, item.reason),
                )
    except errors.ForeignKeyViolation:
        raise HTTPException(404, "Character not found")
    return _traits(character)
