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
            SELECT id, content, importance, user_ids, kind, created_at,
                   1 - (embedding <=> %(q)s) AS similarity
            FROM memories
            WHERE character = %(c)s
              AND (%(u)s::text IS NULL OR %(u)s::text = ANY(user_ids))
            ORDER BY embedding <=> %(q)s
            LIMIT %(k)s
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
    if rows:
        run(
            "UPDATE memories SET last_recalled_at = now() WHERE id = ANY(%s)",
            ([r["id"] for r in rows],),
            fetch="none",
        )
    return rows


@app.delete("/memories/{memory_id}", dependencies=auth)
def delete_memory(memory_id: int):
    run("DELETE FROM memories WHERE id = %s", (memory_id,), fetch="none")
    return {"deleted": memory_id}


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
