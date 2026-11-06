"""Reply links between units, their validation, undo, and the reply suggester.

A unit is a whole block ('block', block id) or a split line ('message', message id).
See Documentation/Curator/threading.md.
"""

import json
import math
import re
import sqlite3
from typing import Callable, Optional

from .errors import ApiError, not_found
from .project import now_ms

CANDIDATE_LIMIT = 50
CANDIDATE_WINDOW_MS = 6 * 60 * 60 * 1000
ROOT_SILENCE_MS = 10 * 60 * 1000

QUESTION_WORDS = {"who", "what", "when", "where", "why", "how", "is", "are", "do", "does", "did", "can",
                  "could", "would", "will", "should", "anyone", "we", "u", "you"}
STOPWORDS = {"the", "and", "for", "you", "that", "this", "with", "are", "was", "but", "not", "have", "just",
             "its", "it's", "i'm", "im", "can", "get", "got", "what", "when", "will", "your", "they", "them",
             "then", "than", "too", "out", "all", "now", "like", "lol", "yeah", "yes"}
WORD_RE = re.compile(r"[a-z0-9']+")


def key(msg) -> tuple[int, int]:
    """Feed order: timestamp, then snowflake id."""
    try:
        return msg["ts"], int(msg["id"])
    except ValueError:
        return msg["ts"], 0


# ── lookups ─────────────────────────────────────────────────────────────────
def conversation_sql(channel: str, ts: str) -> str:
    """SQL for the id of the conversation that starts last at or before `ts` in `channel`.

    Conversations never overlap, so that is the only one that can hold `ts` (check its
    last_ts). Ordering by first_ts with LIMIT 1 is one index seek; the obvious
    `first_ts <= ts AND last_ts >= ts` scans every earlier conversation.
    """
    return (f"(SELECT c_at.id FROM conversations c_at WHERE c_at.channel_id = {channel} AND c_at.first_ts <= {ts} "
            f"ORDER BY c_at.first_ts DESC, c_at.id DESC LIMIT 1)")


def conversation_at(conn: sqlite3.Connection, channel_id: str, ts: int):
    """The conversation holding a message at `ts`, or None."""
    row = conn.execute(
        "SELECT * FROM conversations WHERE channel_id = ? AND first_ts <= ? ORDER BY first_ts DESC, id DESC LIMIT 1",
        (channel_id, ts),
    ).fetchone()
    return row if row and row["last_ts"] >= ts else None


def message(conn: sqlite3.Connection, message_id: str):
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if not row:
        raise ApiError(422, "parent_missing", "That message is not in this project.")
    return row


def link_row(conn: sqlite3.Connection, kind: str, unit_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM links WHERE unit_kind = ? AND unit_id = ?", (kind, str(unit_id))).fetchone()
    return dict(row) if row else None


def block_messages(conn: sqlite3.Connection, block_id) -> list:
    return conn.execute(
        "SELECT * FROM messages WHERE block_id = ? ORDER BY ts, CAST(id AS INTEGER)", (int(block_id),)
    ).fetchall()


def split_ids(conn: sqlite3.Connection, block_id) -> set[str]:
    rows = conn.execute(
        "SELECT l.unit_id FROM links l JOIN messages m ON m.id = l.unit_id "
        "WHERE l.unit_kind = 'message' AND m.block_id = ?", (int(block_id),)
    ).fetchall()
    return {r[0] for r in rows}


def unit_messages(conn: sqlite3.Connection, kind: str, unit_id: str) -> list:
    """The messages a unit speaks for. A block's split lines are their own units."""
    if kind == "message":
        return [message(conn, unit_id)]
    if not conn.execute("SELECT 1 FROM blocks WHERE id = ?", (int(unit_id),)).fetchone():
        raise not_found("Block")
    splits = split_ids(conn, unit_id)
    return [m for m in block_messages(conn, unit_id) if m["id"] not in splits]


def effective_link(conn: sqlite3.Connection, msg) -> Optional[dict]:
    own = link_row(conn, "message", msg["id"])
    return own if own else link_row(conn, "block", str(msg["block_id"]))


def unit_of(conn: sqlite3.Connection, msg) -> tuple[str, str]:
    if link_row(conn, "message", msg["id"]):
        return "message", msg["id"]
    return "block", str(msg["block_id"])


def thread_path(conn: sqlite3.Connection, message_id: str) -> list[dict]:
    """Units from the thread start down to the unit holding `message_id`."""
    path, seen = [], set()
    msg = message(conn, message_id)
    while msg is not None:
        kind, uid = unit_of(conn, msg)
        if (kind, uid) in seen:
            break
        seen.add((kind, uid))
        link = link_row(conn, kind, uid)
        msgs = unit_messages(conn, kind, uid)
        path.append({"unit_kind": kind, "unit_id": uid, "author_id": msg["author_id"],
                     "message_ids": [m["id"] for m in msgs], "link": link})
        if not link or not link["parent_id"]:
            break
        msg = conn.execute("SELECT * FROM messages WHERE id = ?", (link["parent_id"],)).fetchone()
    path.reverse()
    return path


# ── conversations touched by an edit ────────────────────────────────────────
def conversations_of(conn: sqlite3.Connection, msgs) -> list[int]:
    ids = set()
    for m in msgs:
        row = conversation_at(conn, m["channel_id"], m["ts"])
        if row:
            ids.add(row["id"])
    return sorted(ids)


def unapprove(conn: sqlite3.Connection, conversation_ids) -> list[dict]:
    changed = []
    for cid in conversation_ids:
        cur = conn.execute("UPDATE conversations SET approved_at = NULL WHERE id = ? AND approved_at IS NOT NULL", (cid,))
        if cur.rowcount:
            changed.append({"id": cid, "approved": False})
    return changed


# ── validation ──────────────────────────────────────────────────────────────
def validate(conn: sqlite3.Connection, kind: str, unit_id: str, parent_id: str) -> None:
    msgs = block_messages(conn, unit_id) if kind == "block" else [message(conn, unit_id)]
    if not msgs:
        raise not_found("Unit")
    first = msgs[0]
    parent = message(conn, parent_id)
    if parent["channel_id"] != first["channel_id"]:
        raise ApiError(422, "different_channel", "Replies must stay in the same channel.")
    if (kind == "block" and parent["block_id"] == int(unit_id)) or (kind == "message" and parent["id"] == unit_id):
        raise ApiError(422, "parent_in_unit", "A message can't reply to itself.")
    if key(parent) >= key(first):
        raise ApiError(422, "parent_not_earlier", "Replies can only point at earlier messages.")
    own = {m["id"] for m in msgs}
    seen, cur = set(), parent
    while cur is not None and cur["id"] not in seen:
        if cur["id"] in own:
            raise ApiError(422, "cycle", "That would make a loop.")
        seen.add(cur["id"])
        link = effective_link(conn, cur)
        if not link or not link["parent_id"]:
            break
        cur = conn.execute("SELECT * FROM messages WHERE id = ?", (link["parent_id"],)).fetchone()


# ── edits ───────────────────────────────────────────────────────────────────
def _write(conn, kind, unit_id, parent_id, status, origin, score=None) -> None:
    conn.execute(
        "INSERT INTO links (unit_kind, unit_id, parent_id, status, origin, score, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(unit_kind, unit_id) DO UPDATE SET "
        "parent_id = excluded.parent_id, status = excluded.status, origin = excluded.origin, "
        "score = excluded.score, updated_at = excluded.updated_at",
        (kind, str(unit_id), parent_id, status, origin, score, now_ms()),
    )


def _inverse(kind: str, unit_id: str, previous: Optional[dict]) -> dict:
    link = None
    if previous:
        link = {k: previous[k] for k in ("parent_id", "status", "origin", "score")}
    return {"method": "POST", "path": "/links/restore", "body": {"unit_kind": kind, "unit_id": str(unit_id), "link": link}}


def _block_of(conn, kind, unit_id) -> int:
    if kind == "block":
        return int(unit_id)
    return message(conn, unit_id)["block_id"]


def _result(conn, kind, unit_id, previous, extra_blocks=()) -> dict:
    from .feed import block_item  # feed imports this module

    block_id = _block_of(conn, kind, unit_id)
    msgs = block_messages(conn, block_id)
    changed = unapprove(conn, conversations_of(conn, msgs))
    blocks = {block_id, *extra_blocks}
    return {
        "units": [block_item(conn, b, page_ids=None) for b in sorted(blocks)],
        "conversations_changed": changed,
        "inverse": _inverse(kind, unit_id, previous),
        "dirty": True,
    }


def set_parent(conn, kind: str, unit_id: str, parent_id: Optional[str], root: bool = False) -> dict:
    unit_messages(conn, kind, unit_id)  # 404 if the unit doesn't exist
    previous = link_row(conn, kind, unit_id)
    if root:
        _write(conn, kind, unit_id, None, "root", "manual")
    else:
        if not parent_id:
            raise ApiError(422, "parent_missing", "Say which message this replies to.")
        validate(conn, kind, unit_id, parent_id)
        origin = previous["origin"] if previous and previous["status"] == "suggested" and previous["parent_id"] == parent_id else "manual"
        _write(conn, kind, unit_id, parent_id, "accepted", origin)
    return _result(conn, kind, unit_id, previous)


def delete(conn, kind: str, unit_id: str) -> dict:
    previous = link_row(conn, kind, unit_id)
    if not previous:
        raise not_found("Link")
    if previous["status"] == "suggested":
        _remember_rejection(conn, kind, unit_id, previous)
    conn.execute("DELETE FROM links WHERE unit_kind = ? AND unit_id = ?", (kind, str(unit_id)))
    return _result(conn, kind, unit_id, previous)


def _remember_rejection(conn, kind: str, unit_id: str, link: dict) -> None:
    if link["parent_id"]:
        conn.execute(
            "INSERT OR IGNORE INTO rejected_suggestions (unit_kind, unit_id, parent_id, origin) VALUES (?, ?, ?, ?)",
            (kind, str(unit_id), link["parent_id"], link["origin"]),
        )


def accept(conn, kind: str, unit_id: str) -> dict:
    previous = link_row(conn, kind, unit_id)
    if not previous or previous["status"] != "suggested":
        raise ApiError(409, "not_suggested", "That link is not a suggestion.")
    conn.execute("UPDATE links SET status = 'accepted', updated_at = ? WHERE id = ?", (now_ms(), previous["id"]))
    return _result(conn, kind, unit_id, previous)


def reject(conn, kind: str, unit_id: str, settings: dict) -> dict:
    previous = link_row(conn, kind, unit_id)
    if not previous or previous["status"] != "suggested":
        raise ApiError(409, "not_suggested", "That link is not a suggestion.")
    _remember_rejection(conn, kind, unit_id, previous)
    conn.execute("DELETE FROM links WHERE id = ?", (previous["id"],))
    if kind == "block":
        suggest_block(conn, int(unit_id), settings)
    return _result(conn, kind, unit_id, previous)


def restore(conn, kind: str, unit_id: str, link: Optional[dict]) -> dict:
    """Undo: put a unit's link back exactly as it was."""
    unit_messages(conn, kind, unit_id)
    previous = link_row(conn, kind, unit_id)
    if link is None:
        conn.execute("DELETE FROM links WHERE unit_kind = ? AND unit_id = ?", (kind, str(unit_id)))
    else:
        if link.get("parent_id"):
            validate(conn, kind, unit_id, link["parent_id"])
        _write(conn, kind, unit_id, link.get("parent_id"), link["status"], link["origin"], link.get("score"))
        if link["status"] == "suggested":
            conn.execute(
                "DELETE FROM rejected_suggestions WHERE unit_kind = ? AND unit_id = ? AND parent_id = ?",
                (kind, str(unit_id), link.get("parent_id")),
            )
    return _result(conn, kind, unit_id, previous)


# ── suggester ───────────────────────────────────────────────────────────────
def _words(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def _is_question(text: str) -> bool:
    t = text.strip().lower()
    if t.endswith("?"):
        return True
    first = t.split(" ", 1)[0] if t else ""
    return first in QUESTION_WORDS and len(t.split()) >= 3


class ScoreCache:
    """Lookups the suggester repeats for every block. Neighbouring blocks share most of their
    candidates, so one cache across a whole suggest_all run turns each into a dict hit."""

    def __init__(self):
        self.names: dict[str, Optional[re.Pattern]] = {}
        self.words: dict[str, set[str]] = {}
        self.mentions: dict[str, list] = {}
        self.vectors: dict[str, object] = {}

    def name_pattern(self, conn, author_id: str) -> Optional[re.Pattern]:
        if author_id not in self.names:
            row = conn.execute("SELECT name, display_name FROM authors WHERE id = ?", (author_id,)).fetchone()
            names = {n.lower() for n in (row["name"], row["display_name"]) if n} if row else set()
            self.names[author_id] = (re.compile("|".join(rf"\b{re.escape(n)}\b" for n in sorted(names)))
                                     if names else None)
        return self.names[author_id]

    def word_set(self, msg) -> set[str]:
        if msg["id"] not in self.words:
            self.words[msg["id"]] = _words(msg["content_text"])
        return self.words[msg["id"]]

    def mention_ids(self, msg) -> list:
        if msg["id"] not in self.mentions:
            self.mentions[msg["id"]] = json.loads(msg["mentions"] or "[]")
        return self.mentions[msg["id"]]

    def vector(self, conn, message_id: str):
        if message_id not in self.vectors:
            row = conn.execute("SELECT vector FROM message_embeddings WHERE message_id = ?", (message_id,)).fetchone()
            if row:
                import numpy as np

                self.vectors[message_id] = np.frombuffer(row[0], dtype="float32")
            else:
                self.vectors[message_id] = None
        return self.vectors[message_id]


def _unit_vector(conn, msgs, cache: ScoreCache):
    vecs = [v for v in (cache.vector(conn, m["id"]) for m in msgs) if v is not None]
    if not vecs:
        return None
    import numpy as np

    u = np.mean(vecs, axis=0)
    return u, float(np.linalg.norm(u))


def _content_score(conn, unit_words: set[str], unit_vec, cand, cache: ScoreCache) -> float:
    cv = cache.vector(conn, cand["id"]) if unit_vec is not None else None
    if cv is not None:
        import numpy as np

        u, unorm = unit_vec
        denom = float(unorm * np.linalg.norm(cv)) or 1.0
        return max(0.0, min(1.0, float(u @ cv) / denom))
    b = cache.word_set(cand)
    if not unit_words or not b:
        return 0.0
    return len(unit_words & b) / len(unit_words | b)


def score_candidates(conn, block_id: int, cache: Optional[ScoreCache] = None, msgs=None) -> list[tuple[float, dict]]:
    cache = cache or ScoreCache()
    msgs = msgs if msgs is not None else block_messages(conn, block_id)
    if not msgs:
        return []
    first = msgs[0]
    author = first["author_id"]
    text = "\n".join(m["content_text"] for m in msgs)
    mentions = set()
    for m in msgs:
        mentions.update(cache.mention_ids(m))
    rejected = {r[0] for r in conn.execute(
        "SELECT parent_id FROM rejected_suggestions WHERE unit_kind = 'block' AND unit_id = ?", (str(block_id),)
    )}
    cands = conn.execute(
        # The plain `ts <= ?` gives the index an upper bound; without it the OR hides one and
        # SQLite walks back from the end of the channel for every block.
        "SELECT * FROM messages WHERE channel_id = ? AND block_id != ? AND excluded = 0 AND ts >= ? AND ts <= ? "
        "AND (ts < ? OR (ts = ? AND CAST(id AS INTEGER) < ?)) ORDER BY ts DESC, CAST(id AS INTEGER) DESC LIMIT ?",
        (first["channel_id"], block_id, first["ts"] - CANDIDATE_WINDOW_MS, first["ts"], first["ts"], first["ts"],
         int(first["id"]) if first["id"].isdigit() else 0, CANDIDATE_LIMIT),
    ).fetchall()
    if not cands:
        return []
    # Who spoke right before this author's previous block: answering them again is likely.
    answered_before = None
    prev_own = conn.execute(
        "SELECT id, first_ts FROM blocks WHERE channel_id = ? AND author_id = ? AND first_ts < ? "
        "ORDER BY first_ts DESC, id DESC LIMIT 1", (first["channel_id"], author, first["ts"]),
    ).fetchone()
    if prev_own:
        before = conn.execute(
            "SELECT author_id FROM blocks WHERE channel_id = ? AND first_ts < ? ORDER BY first_ts DESC, id DESC LIMIT 1",
            (first["channel_id"], prev_own["first_ts"]),
        ).fetchone()
        answered_before = before["author_id"] if before else None
    lowered = text.lower()
    unit_words = _words(text)
    unit_vec = _unit_vector(conn, msgs, cache)
    named: dict[str, bool] = {}
    out = []
    for distance, c in enumerate(cands):
        if c["id"] in rejected or c["author_id"] == author:
            continue
        score = 0.0
        if c["author_id"] not in named:
            pattern = cache.name_pattern(conn, c["author_id"])
            named[c["author_id"]] = bool(pattern and pattern.search(lowered))
        if c["author_id"] in mentions or named[c["author_id"]]:
            score += 0.35
        if author in cache.mention_ids(c):
            score += 0.20
        if _is_question(c["content_text"]):
            score += 0.10
        score += 0.20 * _content_score(conn, unit_words, unit_vec, c, cache)
        score += 0.15 * math.exp(-distance / 8)
        if answered_before is not None and answered_before == c["author_id"]:
            score += 0.10
        out.append((min(score, 1.0), dict(c)))
    out.sort(key=lambda sc: (sc[0], key(sc[1])), reverse=True)
    return out


def _starts_fresh(conn, first) -> bool:
    conv = conversation_at(conn, first["channel_id"], first["ts"])
    if conv and conv["first_message_id"] == first["id"]:
        return True
    prev = conn.execute(
        "SELECT ts FROM messages WHERE channel_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1", (first["channel_id"], first["ts"])
    ).fetchone()
    return prev is None or first["ts"] - prev[0] >= ROOT_SILENCE_MS


def suggest_block(conn, block_id: int, settings: dict, cache: Optional[ScoreCache] = None,
                  check_link: bool = True) -> Optional[dict]:
    """Guess a parent for a block that has no link yet. Writes 'suggested', 'root', or nothing."""
    if check_link and link_row(conn, "block", str(block_id)):
        return None
    msgs = block_messages(conn, block_id)
    if not msgs or all(m["excluded"] for m in msgs):
        return None
    scored = score_candidates(conn, block_id, cache, msgs)
    best_score, best = scored[0] if scored else (0.0, None)
    if best is not None and best_score >= settings["suggest_high"]:
        _write(conn, "block", block_id, best["id"], "suggested", "suggested", round(best_score, 3))
    elif best_score < settings["suggest_low"] and _starts_fresh(conn, msgs[0]):
        _write(conn, "block", block_id, None, "root", "suggested", round(best_score, 3))
    else:
        return None
    return link_row(conn, "block", str(block_id))


def suggest_all(conn, settings: dict, channel_id: Optional[str] = None,
                progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Run the suggester over every block without a link. The importer calls this after loading messages.

    `progress(done, total)` is called every 500 blocks, e.g. with a job's progress method.
    """
    sql = "SELECT b.id FROM blocks b LEFT JOIN links l ON l.unit_kind = 'block' AND l.unit_id = CAST(b.id AS TEXT) WHERE l.id IS NULL"
    params: tuple = ()
    if channel_id:
        sql += " AND b.channel_id = ?"
        params = (channel_id,)
    counts = {"suggested": 0, "root": 0, "unattached": 0}
    cache = ScoreCache()
    pending = conn.execute(sql + " ORDER BY b.first_ts, b.id", params).fetchall()
    for i, (bid,) in enumerate(pending):
        if progress and i % 500 == 0:
            progress(i, len(pending))
        row = suggest_block(conn, bid, settings, cache, check_link=False)
        counts[row["status"] if row else "unattached"] += 1
    if progress:
        progress(len(pending), len(pending))
    return counts


def _origin_stats(conn, origin: str) -> dict:
    accepted = conn.execute("SELECT COUNT(*) FROM links WHERE origin = ? AND status = 'accepted'", (origin,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM links WHERE origin = ? AND status = 'suggested'", (origin,)).fetchone()[0]
    # Rejections from before project v2 have no origin; they all came from the heuristic suggester.
    rejected = conn.execute(
        "SELECT COUNT(*) FROM rejected_suggestions WHERE COALESCE(origin, 'suggested') = ?", (origin,)
    ).fetchone()[0]
    total = accepted + rejected
    return {"accepted": accepted, "rejected": rejected, "pending": pending,
            "precision": round(accepted / total, 3) if total else None}


def suggester_stats(conn) -> dict:
    """The heuristic suggester's numbers at the top level, the model's under "model"."""
    return {**_origin_stats(conn, "suggested"), "model": _origin_stats(conn, "model")}
