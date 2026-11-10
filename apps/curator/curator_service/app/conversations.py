"""Conversations: bookmarks into the continuous feed, split at silences.

Only conversations can be approved, and only approved, unarchived ones are exported.
"""

import json
import re
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
    # Approving accepts the predicted arrows without asking. Only unattached blocks need a yes,
    # because they're left out of training.
    if not confirm and s["unattached"]:
        raise ApiError(409, "needs_confirm", "Some replies still need a decision.",
                       suggested=s["suggested"], unattached=s["unattached"])
    accepted = []  # what approving changed, so undo can put the suggestions back (POST /links/restore)
    for u in units_in(conn, conv):
        if u["link"] and u["link"]["status"] == "suggested":
            conn.execute("UPDATE links SET status = 'accepted', updated_at = ? WHERE id = ?", (now_ms(), u["link"]["id"]))
            accepted.append({"unit_kind": u["kind"], "unit_id": u["id"],
                             "link": {k: u["link"][k] for k in ("parent_id", "status", "origin", "score")}})
    conn.execute("UPDATE conversations SET approved_at = ? WHERE id = ?", (now_ms(), conversation_id))
    return {**stats(conn, get(conn, conversation_id)), "accepted": accepted}


# ── bulk approval ───────────────────────────────────────────────────────────
# Approving one at a time gave ~270 training replies in an evening; a LoRA wants 1,500 or more.
# So the app proposes conversations that look like real chat, the user skims a sample and
# archives the bad ones, then approves the rest at once.
CRITERIA = {
    "min_messages": 8,             # kept messages
    "min_people": 3,               # people, not bots
    "max_gap_ms": 30 * 60 * 1000,  # longest silence inside it
    "max_media": 0.25,             # share of messages that are only a link, or carry an attachment
    "min_attached": 0.8,           # share of units with an arrow (any status)
    "min_repliers": 2,             # different people who'd get a trainable reply
}
# Checked in this order; a skipped conversation is counted under the first one it fails.
REASONS = {
    "too_short": "too short",
    "few_people": "too few people",
    "long_silence": "long silence inside",
    "mostly_links": "mostly links or attachments",
    "unattached": "too many replies without an arrow",
    "few_repliers": "too few people replying",
}
_LINK_ONLY = re.compile(r"^(\s*<?https?://\S+>?)+\s*$")


def _media(m) -> bool:
    """Only a link (gifs are tenor links), or a file. Link previews ("embed") don't count."""
    if _LINK_ONLY.match(m["content_text"] or ""):
        return True
    att = m["attachments"] or "[]"
    return att != "[]" and any(a.get("type") in ("attachment", "sticker") for a in json.loads(att))


def _replies(snap: dict, bots: set) -> dict[str, int]:
    """Roughly the assistant turns export.build would make from this conversation, per person.

    Each run of one person's messages becomes one Ohm turn when that person is replayed, if its arrow
    points at something said since their last counted turn (or in an earlier conversation, which the
    export carries over). Like the export: bots aren't replayed, unattached messages and most
    one-word replies ("lol") are dropped, and so are later lines answering something else.
    Silences inside the conversation, chunk overlap and the author cap aren't counted.
    """
    from . import export  # export imports this module

    attached, parents = {}, {}
    inside = {m["id"] for m in snap["messages"]}
    for u in snap["units"]:
        for m in u["messages"]:
            attached[m["id"]] = u["link"] is not None
            if u["link"]:
                parents[m["id"]] = u["link"]["parent_id"]
    runs: list[list] = []
    for m in snap["messages"]:
        if m["excluded"]:
            continue
        if runs and runs[-1][0]["author_id"] == m["author_id"]:
            runs[-1].append(m)
        else:
            runs.append([m])
    out: dict[str, int] = {}
    speakers = {r[0]["author_id"] for r in runs}
    heard: dict[str, set] = {a: set() for a in speakers}  # ids said by others since that person's last counted turn
    for run in runs:
        who = run[0]["author_id"]
        ids = {m["id"] for m in run}
        before = heard[who]
        for other in speakers - {who}:
            heard[other] |= ids
        if who in bots:
            continue
        ok = [m for m in run if attached.get(m["id"])]
        if not ok:
            continue
        parent = parents.get(ok[0]["id"])
        if parent is None or (parent in inside and parent not in before):
            continue
        ok = [m for m in ok if parents.get(m["id"]) == parent or parents.get(m["id"]) in ids]
        text = "\n".join(m["content_text"] for m in ok).strip()
        if not text or len(text) > export.MAX_REPLY_CHARS or text.count("\n") + 1 > export.MAX_REPLY_LINES:
            continue
        norm = text.lower().strip(" .!?")
        if (norm in export.SHORT_REPLIES or len(norm) < 2) and export._frac(ok[0]["id"]) >= export.KEEP_SHORT:
            continue
        out[who] = out.get(who, 0) + 1
        heard[who] = set()
    return out


def _score(conv: dict, snap: dict, bots: set, c: dict) -> dict:
    msgs = [m for m in snap["messages"] if not m["excluded"]]
    people = {m["author_id"] for m in msgs if m["author_id"] not in bots}
    by_author: dict[str, int] = {}
    for m in msgs:
        by_author[m["author_id"]] = by_author.get(m["author_id"], 0) + 1
    gap =max((b["ts"] - a["ts"] for a, b in zip(msgs, msgs[1:])), default=0)
    media = sum(_media(m) for m in msgs) / (len(msgs) or 1)
    live = [u for u in snap["units"] if any(not m["excluded"] for m in u["messages"])]
    attached = sum(u["link"] is not None for u in live) / (len(live) or 1)
    replies = _replies(snap, bots)
    fails = [code for code, bad in (
        ("too_short", len(msgs) < c["min_messages"]),
        ("few_people", len(people) < c["min_people"]),
        ("long_silence", gap > c["max_gap_ms"]),
        ("mostly_links", media > c["max_media"]),
        ("unattached", attached < c["min_attached"]),
        ("few_repliers", len(replies) < c["min_repliers"]),
    ) if bad]
    return {
        "id": conv["id"], "channel_id": conv["channel_id"], "first_ts": conv["first_ts"], "last_ts": conv["last_ts"],
        "first_message_id": conv["first_message_id"], "messages": len(msgs), "people": len(people),
        "replies": sum(replies.values()), "repliers": len(replies), "max_gap_ms": gap,
        "media": round(media, 3), "attached": round(attached, 3), "reasons": fails,
        "authors": sorted(by_author, key=lambda a: -by_author[a])[:6],  # most messages first, for avatars
    }


def _bots(conn) -> set:
    return {r[0] for r in conn.execute("SELECT id FROM authors WHERE is_bot")}


def candidates(conn, **criteria) -> dict:
    """Score every conversation that isn't approved or archived against `criteria` (CRITERIA by default).

    Returns {"criteria", "passed": [...], "skipped": {reason: count}, "replies": estimated total
    for the passed ones, "approved": {"conversations", "replies"} for what's already approved}.
    """
    c = {**CRITERIA, **{k: v for k, v in criteria.items() if v is not None}}
    unknown = set(c) - set(CRITERIA)
    if unknown:
        raise ApiError(422, "bad_criteria", f"Unknown criteria: {', '.join(sorted(unknown))}.")
    convs = [dict(r) for r in conn.execute("SELECT * FROM conversations WHERE archived_at IS NULL ORDER BY first_ts")]
    snaps = _snapshot(conn, convs)
    bots = _bots(conn)
    passed, skipped = [], {code: 0 for code in REASONS}
    approved = {"conversations": 0, "replies": 0}
    for conv in convs:
        if conv["approved_at"] is not None:
            approved["conversations"] += 1
            approved["replies"] += sum(_replies(snaps[conv["id"]], bots).values())
            continue
        s = _score(conv, snaps[conv["id"]], bots, c)
        if s["reasons"]:
            skipped[s["reasons"][0]] += 1
        else:
            passed.append(s)
    return {"criteria": c, "passed": passed, "replies": sum(s["replies"] for s in passed),
            "skipped": skipped, "approved": approved}


def _by_ids(conn, ids: list[int]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 500):  # stay under SQLite's parameter limit
        part = ids[i:i + 500]
        out += [dict(r) for r in conn.execute(
            f"SELECT * FROM conversations WHERE id IN ({','.join('?' * len(part))})", part)]
    return out


def approve_many(conn, ids: list[int]) -> dict:
    """approve(confirm=True) for each conversation, with one snapshot for all of them.

    Already approved, archived or missing ones are skipped, so undo only touches what this changed.
    """
    convs = [c for c in _by_ids(conn, list(dict.fromkeys(ids))) if c["approved_at"] is None and c["archived_at"] is None]
    snaps = _snapshot(conn, convs)
    accepted, link_ids = [], []
    for c in convs:
        for u in snaps[c["id"]]["units"]:
            if u["link"] and u["link"]["status"] == "suggested":
                link_ids.append((now_ms(), u["link"]["id"]))
                accepted.append({"unit_kind": u["kind"], "unit_id": u["id"],
                                 "link": {k: u["link"][k] for k in ("parent_id", "status", "origin", "score")}})
    conn.executemany("UPDATE links SET status = 'accepted', updated_at = ? WHERE id = ?", link_ids)
    done = [c["id"] for c in convs]
    conn.executemany("UPDATE conversations SET approved_at = ? WHERE id = ?", [(now_ms(), i) for i in done])
    kept = set(done)
    return {"approved": done, "skipped": [i for i in ids if i not in kept], "accepted": accepted}


def unapprove_many(conn, ids: list[int], accepted: list[dict]) -> dict:
    """Undo approve_many: approval off, and the arrows it accepted back to suggestions.

    An arrow changed since (moved, detached) is left as it is now.
    """
    restored = 0
    for a in accepted:
        link = a.get("link") or {}
        cur = conn.execute(
            "UPDATE links SET status = ?, origin = ?, score = ?, updated_at = ? "
            "WHERE unit_kind = ? AND unit_id = ? AND status = 'accepted' AND parent_id IS ?",
            (link.get("status", "suggested"), link.get("origin", "suggested"), link.get("score"), now_ms(),
             a["unit_kind"], str(a["unit_id"]), link.get("parent_id")),
        )
        restored += cur.rowcount
    changed = [c["id"] for c in _by_ids(conn, list(dict.fromkeys(ids))) if c["approved_at"] is not None]
    conn.executemany("UPDATE conversations SET approved_at = NULL WHERE id = ?", [(i,) for i in changed])
    return {"unapproved": changed, "restored": restored}


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
    manual = sorted((c for c in old if c["manual"]), key=lambda c: (c["first_ts"], c["id"]))
    manual_starts = [c["first_ts"] for c in manual]

    def inside_manual(m) -> Optional[dict]:
        # Manual conversations don't overlap, so only the latest one starting at or before m can hold it.
        i = bisect_right(manual_starts, m["ts"])
        while i > 0:
            i -= 1
            c = manual[i]
            if c["last_ts"] >= m["ts"]:
                return c
            if c["first_ts"] < m["ts"]:
                return None
        return None

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
