"""Conversations: bookmarks into the continuous feed, split at silences.

Only conversations can be approved, and only approved, unarchived ones are exported.
"""

import sqlite3
from bisect import bisect_right
from typing import Optional

from .errors import ApiError, not_found
from .project import now_ms
from .threads import block_messages, conversation_at, key, link_row


def get(conn: sqlite3.Connection, conversation_id: int) -> dict:
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not row:
        raise not_found("Conversation")
    return dict(row)


def _snapshot(conn, convs: list[dict]) -> dict[int, dict]:
    """Messages and units for many conversations at once: three range queries per channel.

    Returns {conversation id: {"messages": [...], "units": [...]}}. A message belongs to the
    conversation its ts falls in, a unit to the one its block starts in; blocks never cross a
    conversation boundary, so the two agree.
    """
    out = {c["id"]: {"messages": [], "units": []} for c in convs}
    by_channel: dict[str, list[dict]] = {}
    for c in convs:
        by_channel.setdefault(c["channel_id"], []).append(c)
    for channel_id, cs in by_channel.items():
        cs.sort(key=lambda c: (c["first_ts"], c["id"]))
        starts = [c["first_ts"] for c in cs]
        lo, hi = starts[0], max(c["last_ts"] for c in cs)

        def owner(ts: int) -> Optional[int]:
            i = bisect_right(starts, ts) - 1
            return cs[i]["id"] if i >= 0 and cs[i]["last_ts"] >= ts else None

        msgs = conn.execute(
            "SELECT m.*, b.first_ts AS block_ts FROM messages m JOIN blocks b ON b.id = m.block_id "
            "WHERE m.channel_id = ? AND m.ts >= ? AND m.ts <= ? ORDER BY m.ts, CAST(m.id AS INTEGER)",
            (channel_id, lo, hi),
        ).fetchall()
        block_links = {r["unit_id"]: dict(r) for r in conn.execute(
            "SELECT l.* FROM blocks b JOIN links l ON l.unit_kind = 'block' AND l.unit_id = CAST(b.id AS TEXT) "
            "WHERE b.channel_id = ? AND b.first_ts >= ? AND b.first_ts <= ?", (channel_id, lo, hi),
        )}
        message_links = {r["unit_id"]: dict(r) for r in conn.execute(
            "SELECT l.* FROM messages m JOIN links l ON l.unit_kind = 'message' AND l.unit_id = m.id "
            "WHERE m.channel_id = ? AND m.ts >= ? AND m.ts <= ?", (channel_id, lo, hi),
        )}
        blocks: dict[int, list] = {}
        for m in msgs:
            cid = owner(m["ts"])
            if cid is not None:
                out[cid]["messages"].append(m)
            blocks.setdefault(m["block_id"], []).append(m)
        for bid in sorted(blocks, key=lambda b: (blocks[b][0]["block_ts"], b)):
            bm = blocks[bid]
            cid = owner(bm[0]["block_ts"])
            if cid is None:
                continue
            units = out[cid]["units"]
            own = [m for m in bm if m["id"] not in message_links]
            if own:
                units.append({"kind": "block", "id": str(bid), "author_id": bm[0]["author_id"], "messages": own,
                              "link": block_links.get(str(bid))})
            for m in bm:
                if m["id"] in message_links:
                    units.append({"kind": "message", "id": m["id"], "author_id": m["author_id"], "messages": [m],
                                  "link": message_links[m["id"]]})
    return out


def units_in(conn, conv: dict) -> list[dict]:
    """Every unit in the conversation with its link (or None) and the messages it speaks for."""
    return _snapshot(conn, [conv])[conv["id"]]["units"]


def _stats(conv: dict, snap: dict) -> dict:
    msgs = [m for m in snap["messages"] if not m["excluded"]]
    by_author: dict[str, int] = {}
    for m in msgs:
        by_author[m["author_id"]] = by_author.get(m["author_id"], 0) + 1
    total = len(msgs) or 1
    skew = sorted(({"author_id": a, "count": n, "share": round(n / total, 3)} for a, n in by_author.items()),
                  key=lambda s: -s["count"])
    unattached = suggested = roots = 0
    for u in snap["units"]:
        live = [m for m in u["messages"] if not m["excluded"]]
        if not live:
            continue
        if u["link"] is None:
            unattached += 1
        elif u["link"]["status"] == "suggested":
            suggested += 1
        elif u["link"]["status"] == "root":
            roots += 1
    return {
        "id": conv["id"], "channel_id": conv["channel_id"], "first_ts": conv["first_ts"], "last_ts": conv["last_ts"],
        "first_message_id": conv["first_message_id"], "last_message_id": conv["last_message_id"],
        "approved": conv["approved_at"] is not None, "archived": conv["archived_at"] is not None,
        "people": len(by_author), "messages": len(msgs), "skew": skew,
        "unattached": unattached, "suggested": suggested, "threads": roots,
    }


def stats_many(conn, convs: list[dict]) -> list[dict]:
    snaps = _snapshot(conn, convs)
    return [_stats(c, snaps[c["id"]]) for c in convs]


def stats(conn, conv: dict) -> dict:
    return stats_many(conn, [conv])[0]


def listing(conn, filter_: str = "all") -> list[dict]:
    where = {
        "all": "archived_at IS NULL",
        "approved": "approved_at IS NOT NULL AND archived_at IS NULL",
        "archived": "archived_at IS NOT NULL",
    }.get(filter_)
    if where is None:
        raise ApiError(422, "bad_filter", "Filter is all, approved or archived.")
    rows = conn.execute(f"SELECT * FROM conversations WHERE {where} ORDER BY first_ts DESC").fetchall()
    return stats_many(conn, [dict(r) for r in rows])


def approve(conn, conversation_id: int, confirm: bool) -> dict:
    conv = get(conn, conversation_id)
    s = stats(conn, conv)
    if not confirm and (s["suggested"] or s["unattached"]):
        raise ApiError(409, "needs_confirm", "Some replies still need a decision.",
                       suggested=s["suggested"], unattached=s["unattached"])
    for u in units_in(conn, conv):
        if u["link"] and u["link"]["status"] == "suggested":
            conn.execute("UPDATE links SET status = 'accepted', updated_at = ? WHERE id = ?", (now_ms(), u["link"]["id"]))
    conn.execute("UPDATE conversations SET approved_at = ? WHERE id = ?", (now_ms(), conversation_id))
    return stats(conn, get(conn, conversation_id))


def set_flag(conn, conversation_id: int, column: str, on: bool) -> dict:
    get(conn, conversation_id)
    conn.execute(f"UPDATE conversations SET {column} = ? WHERE id = ?", (now_ms() if on else None, conversation_id))
    return stats(conn, get(conn, conversation_id))


def _previous_message(conn, msg):
    return conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? AND ts <= ? AND (ts < ? OR (ts = ? AND CAST(id AS INTEGER) < ?)) "
        "ORDER BY ts DESC, CAST(id AS INTEGER) DESC LIMIT 1",
        (msg["channel_id"], msg["ts"], msg["ts"], msg["ts"], key(msg)[1]),
    ).fetchone()


def _cut_block(conn, msg) -> None:
    """Start a new block at `msg`, copying the block's link to the new half."""
    msgs = block_messages(conn, msg["block_id"])
    tail = [m for m in msgs if key(m) >= key(msg)]
    if len(tail) == len(msgs):
        return
    head = msgs[: len(msgs) - len(tail)]
    cur = conn.execute(
        "INSERT INTO blocks (channel_id, author_id, first_ts, last_ts) VALUES (?, ?, ?, ?)",
        (msg["channel_id"], msg["author_id"], tail[0]["ts"], tail[-1]["ts"]),
    )
    new_id = cur.lastrowid
    conn.executemany("UPDATE messages SET block_id = ? WHERE id = ?", [(new_id, m["id"]) for m in tail])
    conn.execute("UPDATE blocks SET last_ts = ? WHERE id = ?", (head[-1]["ts"], msg["block_id"]))
    link = link_row(conn, "block", str(msg["block_id"]))
    if link:
        conn.execute(
            "INSERT INTO links (unit_kind, unit_id, parent_id, status, origin, score, updated_at) VALUES ('block', ?, ?, ?, ?, ?, ?)",
            (str(new_id), link["parent_id"], link["status"], link["origin"], link["score"], now_ms()),
        )


def split(conn, before_message_id: str) -> list[dict]:
    msg = conn.execute("SELECT * FROM messages WHERE id = ?", (before_message_id,)).fetchone()
    if not msg:
        raise not_found("Message")
    conv = conversation_at(conn, msg["channel_id"], msg["ts"])
    if not conv:
        raise not_found("Conversation")
    if conv["first_message_id"] == msg["id"]:
        raise ApiError(422, "already_split", "A conversation already starts here.")
    prev = _previous_message(conn, msg)
    if prev["block_id"] == msg["block_id"]:
        _cut_block(conn, msg)
    conn.execute(
        "UPDATE conversations SET last_message_id = ?, last_ts = ?, manual = 1, approved_at = NULL WHERE id = ?",
        (prev["id"], prev["ts"], conv["id"]),
    )
    cur = conn.execute(
        "INSERT INTO conversations (channel_id, first_message_id, last_message_id, first_ts, last_ts, manual, archived_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (msg["channel_id"], msg["id"], conv["last_message_id"], msg["ts"], conv["last_ts"], conv["archived_at"]),
    )
    return [stats(conn, get(conn, conv["id"])), stats(conn, get(conn, cur.lastrowid))]


def merge_up(conn, conversation_id: int) -> dict:
    conv = get(conn, conversation_id)
    above = conn.execute(
        "SELECT * FROM conversations WHERE channel_id = ? AND first_ts < ? ORDER BY first_ts DESC LIMIT 1",
        (conv["channel_id"], conv["first_ts"]),
    ).fetchone()
    if not above:
        raise ApiError(422, "nothing_above", "This is the first conversation.")
    conn.execute(
        "UPDATE conversations SET last_message_id = ?, last_ts = ?, manual = 1, approved_at = NULL WHERE id = ?",
        (conv["last_message_id"], conv["last_ts"], above["id"]),
    )
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return stats(conn, get(conn, above["id"]))


def rebuild(conn, channel_id: str, settings: dict) -> dict:
    """(Re)derive conversations for a channel from silences, keeping the user's own splits and merges.

    The importer calls this after inserting messages. Changing split_gap_ms calls it too.
    """
    msgs = conn.execute(
        "SELECT id, ts, block_id, channel_id FROM messages WHERE channel_id = ? ORDER BY ts, CAST(id AS INTEGER)",
        (channel_id,),
    ).fetchall()
    if not msgs:
        return {"conversations": 0}
    old = [dict(r) for r in conn.execute("SELECT * FROM conversations WHERE channel_id = ?", (channel_id,))]
    manual = [c for c in old if c["manual"]]

    def inside_manual(m) -> Optional[dict]:
        return next((c for c in manual if c["first_ts"] <= m["ts"] <= c["last_ts"]), None)

    starts = []
    for i, m in enumerate(msgs):
        owner = inside_manual(m)
        if owner:
            if owner["first_message_id"] == m["id"]:
                starts.append(i)
            continue
        if i == 0 or m["ts"] - msgs[i - 1]["ts"] > settings["split_gap_ms"] or inside_manual(msgs[i - 1]):
            starts.append(i)
    starts = sorted(set(starts) | {0})

    previous = {(c["first_message_id"], c["last_message_id"]): c for c in old}
    conn.execute("DELETE FROM conversations WHERE channel_id = ?", (channel_id,))
    for n, s in enumerate(starts):
        e = (starts[n + 1] if n + 1 < len(starts) else len(msgs)) - 1
        first, last = msgs[s], msgs[e]
        if first["block_id"] == msgs[s - 1]["block_id"] and s > 0:
            _cut_block(conn, conn.execute("SELECT * FROM messages WHERE id = ?", (first["id"],)).fetchone())
        same = previous.get((first["id"], last["id"]))
        conn.execute(
            "INSERT INTO conversations (channel_id, first_message_id, last_message_id, first_ts, last_ts, approved_at, archived_at, manual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, first["id"], last["id"], first["ts"], last["ts"],
             same["approved_at"] if same else None, same["archived_at"] if same else None,
             same["manual"] if same else int(inside_manual(first) is not None)),
        )
    return {"conversations": len(starts)}
