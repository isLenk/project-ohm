"""The continuous feed shared by the Threads and Raw views. Pages break at block boundaries."""

import json
import sqlite3
from typing import Optional

from . import conversations
from .errors import ApiError, not_found
from .threads import block_messages, conversation_at, link_row

PREVIEW = 60


def channels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT m.channel_id, COUNT(*) AS messages, "
        "(SELECT channel_name FROM sources s WHERE s.channel_id = m.channel_id LIMIT 1) AS name, "
        "(SELECT guild_name FROM sources s WHERE s.channel_id = m.channel_id LIMIT 1) AS guild "
        "FROM messages m GROUP BY m.channel_id ORDER BY messages DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _link_out(conn, link: Optional[dict], page_ids: Optional[set]) -> Optional[dict]:
    if not link:
        return None
    out = {"parent_id": link["parent_id"], "status": link["status"], "origin": link["origin"], "score": link["score"],
           "parent_ts": None, "parent_preview": None, "parent_author_id": None}
    if link["parent_id"]:
        p = conn.execute("SELECT ts, content_text, author_id FROM messages WHERE id = ?", (link["parent_id"],)).fetchone()
        if p:
            out["parent_author_id"] = p["author_id"]
            if page_ids is None or link["parent_id"] not in page_ids:
                out["parent_ts"] = p["ts"]
                out["parent_preview"] = p["content_text"][:PREVIEW]
    return out


def block_item(conn, block_id: int, page_ids: Optional[set]) -> dict:
    b = conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
    if not b:
        raise not_found("Block")
    color = conn.execute("SELECT color_index FROM authors WHERE id = ?", (b["author_id"],)).fetchone()
    msgs = block_messages(conn, block_id)
    return {
        "type": "block", "id": str(b["id"]), "author_id": b["author_id"], "color_index": color[0] if color else 0,
        "link": _link_out(conn, link_row(conn, "block", str(b["id"])), page_ids),
        "messages": [
            {"id": m["id"], "ts": m["ts"], "content_text": m["content_text"], "excluded": bool(m["excluded"]),
             "exclude_reason": m["exclude_reason"], "mentions": json.loads(m["mentions"] or "[]"),
             "reply_to_id": m["reply_to_id"],
             "link": _link_out(conn, link_row(conn, "message", m["id"]), page_ids)}
            for m in msgs
        ],
    }


def _default_channel(conn) -> Optional[str]:
    row = conn.execute("SELECT channel_id FROM messages GROUP BY channel_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    return row[0] if row else None


def page(conn, channel_id: Optional[str] = None, cursor: Optional[str] = None, direction: str = "down",
         around: Optional[str] = None, limit: int = 60) -> dict:
    channel_id = channel_id or _default_channel(conn)
    empty = {"channel_id": channel_id, "items": [], "authors": {}, "next_cursor": None, "prev_cursor": None}
    if channel_id is None:
        return empty
    limit = max(1, min(limit, 300))
    order = "first_ts, id"

    def blocks_after(ts, bid, n):
        return conn.execute(
            f"SELECT id, first_ts FROM blocks WHERE channel_id = ? AND first_ts >= ? "
            f"AND (first_ts > ? OR (first_ts = ? AND id > ?)) ORDER BY {order} LIMIT ?",
            (channel_id, ts, ts, ts, bid, n),
        ).fetchall()

    def blocks_before(ts, bid, n):
        rows = conn.execute(
            "SELECT id, first_ts FROM blocks WHERE channel_id = ? AND first_ts <= ? "
            "AND (first_ts < ? OR (first_ts = ? AND id < ?)) ORDER BY first_ts DESC, id DESC LIMIT ?",
            (channel_id, ts, ts, ts, bid, n),
        ).fetchall()
        return list(reversed(rows))

    if around:
        m = conn.execute("SELECT block_id, channel_id FROM messages WHERE id = ?", (around,)).fetchone()
        if not m:
            raise not_found("Message")
        channel_id = m["channel_id"]
        anchor = conn.execute("SELECT id, first_ts FROM blocks WHERE id = ?", (m["block_id"],)).fetchone()
        rows = blocks_before(anchor["first_ts"], anchor["id"], limit // 2) + [anchor] + \
            blocks_after(anchor["first_ts"], anchor["id"], limit - limit // 2 - 1)
    elif cursor:
        c = conn.execute("SELECT id, first_ts FROM blocks WHERE id = ?", (int(cursor),)).fetchone()
        if not c:
            raise ApiError(422, "bad_cursor", "Unknown cursor.")
        rows = blocks_after(c["first_ts"], c["id"], limit) if direction == "down" else blocks_before(c["first_ts"], c["id"], limit)
    else:
        rows = conn.execute(f"SELECT id, first_ts FROM blocks WHERE channel_id = ? ORDER BY {order} LIMIT ?", (channel_id, limit)).fetchall()

    if not rows:
        return {**empty, "channel_id": channel_id}

    ids = [r["id"] for r in rows]
    page_ids = {r[0] for r in conn.execute(
        f"SELECT id FROM messages WHERE block_id IN ({','.join('?' * len(ids))})", ids
    )}
    items, author_ids = [], set()
    starting = [dict(r) for r in conn.execute(
        "SELECT * FROM conversations WHERE channel_id = ? AND first_ts >= ? AND first_ts <= ?",
        (channel_id, rows[0]["first_ts"], rows[-1]["first_ts"]),
    )]
    starts = {s["first_message_id"]: s for s in conversations.stats_many(conn, starting)}
    for r in rows:
        item = block_item(conn, r["id"], page_ids)
        first_id = item["messages"][0]["id"] if item["messages"] else None
        if first_id in starts:
            items.append({"type": "conversation_start", "conversation": starts[first_id]})
        conv = conversation_at(conn, channel_id, r["first_ts"])
        item["conversation_id"] = conv["id"] if conv else None
        items.append(item)
        author_ids.add(item["author_id"])
        for link in [item["link"]] + [m["link"] for m in item["messages"]]:
            if link and link["parent_author_id"]:
                author_ids.add(link["parent_author_id"])

    first, last = rows[0], rows[-1]
    has_before = bool(blocks_before(first["first_ts"], first["id"], 1))
    has_after = bool(blocks_after(last["first_ts"], last["id"], 1))
    return {
        "channel_id": channel_id,
        "items": items,
        "authors": authors(conn, author_ids),
        "next_cursor": str(last["id"]) if has_after else None,
        "prev_cursor": str(first["id"]) if has_before else None,
    }


def authors(conn, ids=None) -> dict:
    if ids is not None and not ids:
        return {}
    sql = "SELECT id, name, display_name, avatar_url, is_bot, color_index FROM authors"
    params: tuple = ()
    if ids is not None:
        ids = list(ids)
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    return {r["id"]: dict(r) for r in conn.execute(sql, params)}
