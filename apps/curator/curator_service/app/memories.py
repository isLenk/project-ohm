"""Memories about each person: proposed from their messages, reviewed by the user, pushed to memory_service.

See Documentation/Curator/memories.md.
"""

import json
import re
from collections import Counter
from typing import Optional

import requests

from . import embeddings, llm
from .config import MEMORY_API_KEY, MEMORY_API_URL
from .errors import ApiError, not_found
from .project import now_ms
from .threads import conversation_sql

MIN_WORDS = 3
DUPLICATE = 0.9
STYLE_MIN_BLOCKS = 10
STYLE_MIN_SHARE = 0.2

CONTEXT_MESSAGES = 8
CONTEXT_CHARS = 300
VAGUE_ENDINGS = {"it", "this", "that", "them", "there", "participate", "join", "help", "come", "go", "too", "so", "in"}

EXTRACT_SYSTEM = (
    "You decide what's worth remembering long-term about one person from what they said in a group chat.\n"
    "You get the last few messages before theirs as context, then their messages. Only make notes about the "
    "person whose messages you are given; use the context only to understand what they mean.\n"
    "Worth remembering: lasting things such as likes and dislikes, hobbies, games they play, job or school, where "
    "they live, pets, family, partners and friends, plans with a date, life events, and opinions they clearly hold.\n"
    "Not worth remembering: greetings, filler, questions, reactions, jokes, insults, small talk, agreeing or "
    "saying yes or no to something, being free or busy for one event, anything about the conversation itself, "
    "and requests to a bot.\n"
    "The note must make sense on its own months later, read by someone who never saw the chat. Say what the "
    "thing is: name the game, the person, the place, the event. If you can't tell what they mean, even with the "
    "context, nothing is worth remembering.\n"
    "Good: \"KaptainDoosh plays Barotrauma with the group most weekends.\" "
    "\"KaptainDoosh is studying nursing at college.\"\n"
    "Bad: \"KaptainDoosh is down to participate.\" (in what?) \"KaptainDoosh agrees.\" "
    "\"KaptainDoosh likes it.\" (likes what?) \"KaptainDoosh asked a question.\"\n"
    "Reply with JSON only, one object:\n"
    '{"note": "<one short third-person sentence that starts with their name>", '
    '"category": "fact" or "relationship", '
    '"short_lived": true if it is only true today (\"was out all day\"), '
    '"mentions_person": "<a person they mention by name who is not in the chat>" or null}\n'
    'If nothing is worth remembering reply exactly {"note": null}.'
)


def _vague(note: str) -> bool:
    """Notes that end on a pronoun or a bare verb ("is down to participate") don't say what they're about."""
    words = re.findall(r"[a-z']+", note.lower())
    return len(words) < 3 or words[-1] in VAGUE_ENDINGS


def _prompt(project, b, msgs) -> str:
    """The person's messages, after the few channel messages before them so the model knows what they refer to."""
    before = project.all(
        "SELECT COALESCE(a.display_name, a.name) AS who, m.content_text FROM messages m JOIN authors a ON a.id = m.author_id "
        "WHERE m.channel_id = ? AND m.ts < ? AND m.excluded = 0 AND m.content_text != '' "
        "ORDER BY m.ts DESC, CAST(m.id AS INTEGER) DESC LIMIT ?",
        (b["channel_id"], b["first_ts"], CONTEXT_MESSAGES),
    )
    have = {m["id"] for m in msgs}
    replied = [m["reply_to_id"] for m in msgs if m["reply_to_id"] and m["reply_to_id"] not in have]
    quoted = project.all(
        "SELECT COALESCE(a.display_name, a.name) AS who, m.content_text FROM messages m JOIN authors a ON a.id = m.author_id "
        f"WHERE m.id IN ({','.join('?' * len(replied))})", replied
    ) if replied else []
    lines = [f"{r['who']}: {r['content_text'][:CONTEXT_CHARS]}" for r in reversed(before)]
    lines += [f"{r['who']} (replied to): {r['content_text'][:CONTEXT_CHARS]}" for r in quoted]
    name = b["display_name"] or b["name"]
    context = "\n".join(lines) or "(nothing before this)"
    said = "\n".join(m["content_text"] for m in msgs)
    return f"Context, earlier in the chat:\n{context}\n\nWhat {name} said:\n{said}"


def _memory_out(conn, r) -> dict:
    sources = conn.execute(
        "SELECT m.id, m.ts, m.content_text FROM memory_sources s JOIN messages m ON m.id = s.message_id "
        "WHERE s.memory_id = ? ORDER BY m.ts", (r["id"],)
    ).fetchall()
    out = {k: r[k] for k in r.keys() if k != "embedding"}
    out["short_lived"] = bool(r["short_lived"])
    out["synced"] = r["remote_id"] is not None
    out["sources"] = [dict(s) for s in sources]
    return out


def get(conn, memory_id: int):
    r = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not r:
        raise not_found("Memory")
    return r


def people(conn) -> list[dict]:
    total = conn.execute("SELECT COUNT(*) FROM messages WHERE excluded = 0").fetchone()[0] or 1
    # Every message sits in exactly one conversation, so the latest one starting at or before it is its own.
    in_convs = dict(conn.execute(
        f"SELECT m.author_id, COUNT(DISTINCT {conversation_sql('m.channel_id', 'm.ts')}) FROM messages m GROUP BY m.author_id"
    ).fetchall())
    rows = conn.execute(
        "SELECT a.*, (SELECT COUNT(*) FROM messages m WHERE m.author_id = a.id AND m.excluded = 0) AS messages "
        "FROM authors a ORDER BY messages DESC"
    ).fetchall()
    out = []
    for r in rows:
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM memories WHERE author_id = ? GROUP BY status", (r["id"],)
        ).fetchall())
        out.append({
            "author_id": r["id"], "name": r["name"], "display_name": r["display_name"], "color_index": r["color_index"],
            "avatar_url": r["avatar_url"], "is_bot": bool(r["is_bot"]), "messages": r["messages"], "conversations": in_convs.get(r["id"], 0),
            "share": round(r["messages"] / total, 3),
            "memories": {"new": counts.get("new", 0), "kept": counts.get("kept", 0), "forgotten": counts.get("forgotten", 0)},
        })
    return out


def listing(conn, author_id: str, status: Optional[str]) -> list[dict]:
    sql = "SELECT * FROM memories WHERE (author_id = ? OR linked_author_id = ?)"
    params: list = [author_id, author_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    # Newest first: by the latest message a memory came from, then by when it was added.
    sql += (" ORDER BY (SELECT MAX(m.ts) FROM memory_sources s JOIN messages m ON m.id = s.message_id "
            "WHERE s.memory_id = memories.id) DESC, created_at DESC")
    rows = conn.execute(sql, params).fetchall()
    return [_memory_out(conn, r) for r in rows]


def add(conn, author_id: str, category: str, text: str, status: str = "new", short_lived: bool = False,
        mentioned_name: Optional[str] = None, source_ids: tuple = (), embedding=None) -> int:
    if category not in ("fact", "relationship", "style"):
        raise ApiError(422, "bad_category", "Category is fact, relationship or style.")
    if not text.strip():
        raise ApiError(422, "empty", "Write something to remember.")
    t = now_ms()
    cur = conn.execute(
        "INSERT INTO memories (author_id, mentioned_name, category, text, status, short_lived, embedding, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (author_id, mentioned_name, category, text.strip(), status, int(short_lived),
         embeddings.to_blob(embedding) if embedding is not None else None, t, t),
    )
    conn.executemany("INSERT OR IGNORE INTO memory_sources VALUES (?, ?)", [(cur.lastrowid, s) for s in source_ids])
    return cur.lastrowid


def update(conn, memory_id: int, text: Optional[str], status: Optional[str], linked_author_id: Optional[str]) -> dict:
    r = get(conn, memory_id)
    if status and status not in ("new", "kept", "forgotten"):
        raise ApiError(422, "bad_status", "Status is new, kept or forgotten.")
    sets, params = ["updated_at = ?"], [now_ms()]
    if text is not None:
        if not text.strip():
            raise ApiError(422, "empty", "A memory can't be empty.")
        sets.append("text = ?")
        params.append(text.strip())
        if r["remote_id"] is not None:
            sets.append("remote_id = NULL")  # re-sync with the new text
            _remote_delete(conn, r)
    if status:
        sets.append("status = ?")
        params.append(status)
        if status != "kept" and r["remote_id"] is not None:
            _remote_delete(conn, r)
            sets.append("remote_id = NULL")
    if linked_author_id is not None:
        sets.append("linked_author_id = ?")
        params.append(linked_author_id or None)
    conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", [*params, memory_id])
    return _memory_out(conn, get(conn, memory_id))


def keep_all(conn, author_id: str) -> int:
    cur = conn.execute(
        "UPDATE memories SET status = 'kept', updated_at = ? WHERE author_id = ? AND status = 'new' AND short_lived = 0",
        (now_ms(), author_id),
    )
    return cur.rowcount


# ── extraction ──────────────────────────────────────────────────────────────
def _similar_existing(conn, author_id: str, vec) -> Optional[int]:
    if vec is None:
        return None
    import numpy as np

    for r in conn.execute("SELECT id, embedding FROM memories WHERE author_id = ? AND embedding IS NOT NULL", (author_id,)):
        other = embeddings.from_blob(r["embedding"])
        if float(np.dot(vec, other)) >= DUPLICATE:
            return r["id"]
    return None


def _same_text(conn, author_id: str, text: str) -> Optional[int]:
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    for r in conn.execute("SELECT id, text FROM memories WHERE author_id = ?", (author_id,)):
        if re.sub(r"\W+", " ", r["text"].lower()).strip() == norm:
            return r["id"]
    return None


def extract(project, job, conversation_id: Optional[int] = None) -> dict:
    """Ask the local LLM about every unscanned block (or one conversation's blocks), newest first."""
    if not llm.up():
        raise ApiError(503, "llm_down", "llama-server isn't running on :5000, so memories can't be extracted.")
    sql = ("SELECT b.id, b.author_id, b.channel_id, b.first_ts, a.name, a.display_name FROM blocks b JOIN authors a ON a.id = b.author_id "
           "LEFT JOIN memory_scans s ON s.block_id = b.id WHERE s.block_id IS NULL AND a.is_bot = 0")
    params: list = []
    if conversation_id is not None:
        conv = project.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if not conv:
            raise not_found("Conversation")
        sql += " AND b.channel_id = ? AND b.first_ts BETWEEN ? AND ?"
        params += [conv["channel_id"], conv["first_ts"], conv["last_ts"]]
    blocks = project.all(sql + " ORDER BY b.first_ts DESC, b.id DESC", params)
    use_vectors = embeddings.available()
    created = merged = failed = 0
    stopped = None
    for i, b in enumerate(blocks):
        if job.cancelled:
            break
        job.progress(i, len(blocks), "reading messages")
        msgs = project.all(
            "SELECT id, content_text, reply_to_id FROM messages WHERE block_id = ? AND excluded = 0 ORDER BY ts", (b["id"],)
        )
        text = "\n".join(m["content_text"] for m in msgs)
        result = None
        if len(text.split()) >= MIN_WORDS:
            try:
                result = llm.chat_json(EXTRACT_SYSTEM, _prompt(project, b, msgs))
            except requests.RequestException:
                # Leave the block unscanned so the next run asks again.
                if not llm.up():
                    stopped = "llama-server stopped answering."
                    break
                failed += 1
                continue
        with project.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO memory_scans (block_id, scanned_at) VALUES (?, ?)", (b["id"], now_ms()))
            if not isinstance(result, dict) or not result.get("note"):
                continue
            note = str(result["note"]).strip().strip('"')[:300]
            if _vague(note):
                continue
            category = "relationship" if result.get("category") == "relationship" else "fact"
            vec = embeddings.encode([note])[0] if use_vectors else None
            existing = _similar_existing(conn, b["author_id"], vec) if use_vectors else _same_text(conn, b["author_id"], note)
            if existing:
                conn.executemany("INSERT OR IGNORE INTO memory_sources VALUES (?, ?)", [(existing, m["id"]) for m in msgs])
                merged += 1
                continue
            forgotten = conn.execute(
                "SELECT 1 FROM memories WHERE author_id = ? AND status = 'forgotten' AND text = ?", (b["author_id"], note)
            ).fetchone()
            if forgotten:
                continue
            mentioned = result.get("mentions_person")
            add(conn, b["author_id"], category, note, short_lived=bool(result.get("short_lived")),
                mentioned_name=str(mentioned) if mentioned else None, source_ids=tuple(m["id"] for m in msgs), embedding=vec)
            created += 1
    job.progress(len(blocks), len(blocks), "done")
    with project.tx() as conn:
        styles = refresh_styles(conn)
    return {"blocks": len(blocks), "created": created, "merged": merged, "failed": failed, "stopped": stopped,
            "style_traits": styles}


# ── style traits ────────────────────────────────────────────────────────────
WORD = re.compile(r"[a-z']+")


def _traits(name: str, block_count: int, msgs: list, mine: Counter, everyone: Counter, everyone_total: int) -> list[str]:
    if block_count < STYLE_MIN_BLOCKS or not msgs:
        return []
    traits = []
    starts = [m["content_text"].lstrip()[:1] for m in msgs if m["content_text"].strip()[:1].isalpha()]
    if starts:
        upper = sum(c.isupper() for c in starts) / len(starts)
        if upper >= 0.8:
            traits.append(f"{name} usually starts messages with a capital letter.")
        elif upper <= 0.2:
            traits.append(f"{name} usually types in lowercase.")
    per_block = Counter(m["block_id"] for m in msgs)
    avg = sum(per_block.values()) / len(per_block)
    multi = sum(1 for n in per_block.values() if n >= 2) / len(per_block)
    if avg >= 1.8 and multi >= STYLE_MIN_SHARE:
        traits.append(f"{name} usually sends {round(avg)}–{round(avg) + 1} lines in a row instead of one long message.")
    pings = sum(1 for m in msgs if len(json.loads(m["mentions"] or "[]")) >= 3) / len(msgs)
    if pings >= 0.05:
        traits.append(f"{name} pings the whole group to organize things.")
    mine_total = sum(mine.values())
    others_total = (everyone_total - mine_total) or 1
    mine_total = mine_total or 1
    slang = [w for w, n in mine.most_common(200)
             if n >= 3 and len(w) <= 6 and n / mine_total >= 5 * max(everyone[w] - n, 1) / others_total]
    if slang:
        traits.append(f"{name} often says " + ", ".join(f"“{w}”" for w in slang[:4]) + ".")
    return traits


def refresh_styles(conn) -> int:
    """Re-derive every person's style traits. Reads and tokenizes each message once:
    "everyone else's" word counts are everyone's minus this person's."""
    block_counts = dict(conn.execute("SELECT author_id, COUNT(*) FROM blocks GROUP BY author_id").fetchall())
    by_author: dict[str, list] = {}
    words: dict[str, Counter] = {}
    for m in conn.execute("SELECT author_id, content_text, mentions, block_id FROM messages WHERE excluded = 0"):
        by_author.setdefault(m["author_id"], []).append(m)
        words.setdefault(m["author_id"], Counter()).update(WORD.findall(m["content_text"].lower()))
    everyone: Counter = Counter()
    for c in words.values():
        everyone.update(c)
    everyone_total = sum(everyone.values())
    count = 0
    for a in conn.execute("SELECT id, name, display_name FROM authors WHERE is_bot = 0").fetchall():
        name = a["display_name"] or a["name"]
        conn.execute("DELETE FROM memories WHERE author_id = ? AND category = 'style' AND status = 'new'", (a["id"],))
        kept = {r[0] for r in conn.execute(
            "SELECT text FROM memories WHERE author_id = ? AND category = 'style' AND status != 'new'", (a["id"],)
        )}
        traits = _traits(name, block_counts.get(a["id"], 0), by_author.get(a["id"], []),
                         words.get(a["id"], Counter()), everyone, everyone_total)
        for t in traits:
            if t not in kept:
                add(conn, a["id"], "style", t)
                count += 1
    return count


# ── memory_service sync ─────────────────────────────────────────────────────
def _session() -> requests.Session:
    if not MEMORY_API_URL:
        raise ApiError(503, "memory_service_off", "Set MEMORY_API_URL in the root .env to sync memories.")
    s = requests.Session()
    s.headers["X-API-Key"] = MEMORY_API_KEY
    return s


def _remote_delete(conn, r) -> None:
    if r["remote_id"] is None or not MEMORY_API_URL:
        return
    author = r["author_id"]
    try:
        _session().delete(f"{MEMORY_API_URL}/users/{author}/facts/{r['remote_id']}", timeout=(2, 5))
    except requests.RequestException as e:
        print(f"[curator] could not delete fact {r['remote_id']} remotely: {e}")


def sync(conn, character: str) -> dict:
    s = _session()
    rows = conn.execute(
        "SELECT mem.*, a.name, a.display_name FROM memories mem JOIN authors a ON a.id = COALESCE(mem.linked_author_id, mem.author_id) "
        "WHERE mem.status = 'kept' AND mem.remote_id IS NULL"
    ).fetchall()
    synced, failed = 0, 0
    for r in rows:
        user_id = r["linked_author_id"] or r["author_id"]
        try:
            s.put(f"{MEMORY_API_URL}/users/{user_id}", json={"display_name": r["display_name"] or r["name"]},
                  timeout=(2, 5)).raise_for_status()
            res = s.post(f"{MEMORY_API_URL}/users/{user_id}/facts", json={"character": character, "fact": r["text"]},
                         timeout=(2, 10))
            res.raise_for_status()
            conn.execute("UPDATE memories SET remote_id = ? WHERE id = ?", (res.json()["id"], r["id"]))
            synced += 1
        except requests.RequestException as e:
            print(f"[curator] memory sync failed for {r['id']}: {e}")
            failed += 1
            if isinstance(e, requests.ConnectionError):
                break
    return {"synced": synced, "failed": failed, "remaining": len(rows) - synced}
