"""Memory API for the virtual assistant. Runs on the NAS next to Postgres.

The PC only sends text; embeddings are created here (CPU, ONNX via fastembed).
Every route except /health requires the X-API-Key header.
"""

import os
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


@app.put("/users/{discord_id}", dependencies=auth)
def upsert_user(discord_id: str, u: User):
    return run(
        """
        INSERT INTO users (discord_id, display_name, preferred_name) VALUES (%s, %s, %s)
        ON CONFLICT (discord_id) DO UPDATE SET
            display_name = COALESCE(EXCLUDED.display_name, users.display_name),
            preferred_name = COALESCE(EXCLUDED.preferred_name, users.preferred_name),
            last_seen = now()
        RETURNING *
        """,
        (discord_id, u.display_name, u.preferred_name),
        "one",
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
    recalled = [r["id"] for r in rows if r["kind"] not in ("lore", "knowledge")]
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


# ── Logs & metrics ──────────────────────────────────────────────────────────
class LogIn(BaseModel):
    message: str
    level: str = "info"
    source: Optional[str] = None
    data: Optional[dict] = None


@app.post("/logs", dependencies=auth)
def add_log(l: LogIn):
    run(
        "INSERT INTO logs (level, source, message, data) VALUES (%s, %s, %s, %s)",
        (l.level, l.source, l.message, Jsonb(l.data) if l.data is not None else None),
        fetch="none",
    )
    return {"ok": True}


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
