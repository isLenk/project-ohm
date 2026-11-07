"""Remove messages with no text (only images, attachments or nothing) from a project.

The importer stops keeping them; this cleans up projects imported before that. Links and
replies that pointed at a removed message point at whatever it replied to instead.
No app imports: the project migrations call it.
"""

import sqlite3


def _is_textless(text: str) -> bool:
    return not text.strip()


def remove(conn: sqlite3.Connection) -> int:
    """Delete text-less messages and everything that hangs off them. Returns how many went."""
    gone = {r[0] for r in conn.execute(
        "SELECT id, content_text FROM messages WHERE length(trim(content_text)) <= 8"
    ) if _is_textless(r[1])}
    if not gone:
        return 0
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS gone (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.gone")
    conn.executemany("INSERT INTO temp.gone (id) VALUES (?)", [(i,) for i in gone])

    # Where each removed message's thread continues: its own link (or its block's), followed past removed ones.
    def link_parent(mid: str):
        row = conn.execute(
            "SELECT l.parent_id FROM messages m JOIN links l ON "
            "(l.unit_kind = 'message' AND l.unit_id = m.id) OR (l.unit_kind = 'block' AND l.unit_id = CAST(m.block_id AS TEXT)) "
            "WHERE m.id = ? ORDER BY l.unit_kind = 'message' DESC LIMIT 1", (mid,)).fetchone()
        return row[0] if row else None

    def reply_parent(mid: str):
        row = conn.execute("SELECT reply_to_id FROM messages WHERE id = ?", (mid,)).fetchone()
        return row[0] if row else None

    def past(mid, step):
        seen = set()
        while mid in gone and mid not in seen:
            seen.add(mid)
            mid = step(mid)
        return None if mid in gone else mid

    for (kind, unit_id, parent) in conn.execute(
            "SELECT unit_kind, unit_id, parent_id FROM links WHERE parent_id IN (SELECT id FROM temp.gone)").fetchall():
        new = past(parent, link_parent)
        # A unit can't reply to itself.
        own = new == unit_id if kind == "message" else bool(new and conn.execute(
            "SELECT 1 FROM messages WHERE id = ? AND block_id = ?", (new, int(unit_id))).fetchone())
        if new and not own:
            conn.execute("UPDATE links SET parent_id = ? WHERE unit_kind = ? AND unit_id = ?", (new, kind, unit_id))
        else:
            conn.execute("DELETE FROM links WHERE unit_kind = ? AND unit_id = ?", (kind, unit_id))
    for (mid, parent) in conn.execute(
            "SELECT id, reply_to_id FROM messages WHERE reply_to_id IN (SELECT id FROM temp.gone)").fetchall():
        conn.execute("UPDATE messages SET reply_to_id = ? WHERE id = ?", (past(parent, reply_parent), mid))

    g = "(SELECT id FROM temp.gone)"
    conn.execute(f"DELETE FROM links WHERE unit_kind = 'message' AND unit_id IN {g}")
    conn.execute(f"DELETE FROM rejected_suggestions WHERE parent_id IN {g} OR (unit_kind = 'message' AND unit_id IN {g})")
    conn.execute(f"DELETE FROM memory_sources WHERE message_id IN {g}")
    conn.execute(f"DELETE FROM message_embeddings WHERE message_id IN {g}")
    conn.execute(f"DELETE FROM message_similar WHERE message_id IN {g} OR other_id IN {g}")

    # Conversations that started or ended on a removed message start/end on the nearest kept one.
    for c in conn.execute(
            f"SELECT id, channel_id, first_ts, last_ts FROM conversations "
            f"WHERE first_message_id IN {g} OR last_message_id IN {g}").fetchall():
        inside = (f"FROM messages WHERE channel_id = ? AND ts >= ? AND ts <= ? AND id NOT IN {g} ")
        args = (c[1], c[2], c[3])
        first = conn.execute(f"SELECT id, ts {inside} ORDER BY ts, CAST(id AS INTEGER) LIMIT 1", args).fetchone()
        if not first:
            conn.execute("DELETE FROM conversations WHERE id = ?", (c[0],))
            continue
        last = conn.execute(f"SELECT id, ts {inside} ORDER BY ts DESC, CAST(id AS INTEGER) DESC LIMIT 1", args).fetchone()
        conn.execute("UPDATE conversations SET first_message_id = ?, first_ts = ?, last_message_id = ?, last_ts = ? WHERE id = ?",
                     (first[0], first[1], last[0], last[1], c[0]))

    touched = [r[0] for r in conn.execute(f"SELECT DISTINCT block_id FROM messages WHERE id IN {g}")]
    # The foreign key check on each deleted message scans every column that references messages(id).
    # These have no index, so it took ~20 s for 6k messages in a 130k project; with them it's instant.
    fk_columns = [("message_similar", "other_id"), ("conversations", "first_message_id"),
                  ("conversations", "last_message_id"), ("memory_sources", "message_id")]
    for table, column in fk_columns:
        conn.execute(f"CREATE INDEX IF NOT EXISTS textless_{table}_{column} ON {table} ({column})")
    conn.execute(f"DELETE FROM messages WHERE id IN {g}")
    for table, column in fk_columns:
        conn.execute(f"DROP INDEX textless_{table}_{column}")
    conn.execute("DROP TABLE temp.gone")

    # Blocks left with no messages go; the rest shrink to the messages they still hold.
    for bid in touched:
        span = conn.execute("SELECT MIN(ts), MAX(ts) FROM messages WHERE block_id = ?", (bid,)).fetchone()
        if span[0] is None:
            conn.execute("DELETE FROM links WHERE unit_kind = 'block' AND unit_id = ?", (str(bid),))
            conn.execute("DELETE FROM rejected_suggestions WHERE unit_kind = 'block' AND unit_id = ?", (str(bid),))
            conn.execute("DELETE FROM model_link_scans WHERE block_id = ?", (bid,))
            conn.execute("DELETE FROM memory_scans WHERE block_id = ?", (bid,))
            conn.execute("DELETE FROM blocks WHERE id = ?", (bid,))
        else:
            conn.execute("UPDATE blocks SET first_ts = ?, last_ts = ? WHERE id = ?", (span[0], span[1], bid))
    return len(gone)
