"""Ctrl+K search. See Documentation/Curator/search.md for the grammar."""

import re
from datetime import datetime
from typing import Optional

from . import embeddings
from .errors import ApiError
from .threads import conversation_at, conversation_sql

REGEX_BUDGET_S = 0.2
ACTIONS = [
    {"id": "train", "label": "Train LoRA on approved conversations"},
    {"id": "approve", "label": "Approve the current conversation"},
    {"id": "save", "label": "Save project"},
    {"id": "export", "label": "Export approved as JSONL"},
    {"id": "memories", "label": "Open memories"},
    {"id": "raw", "label": "Switch to Raw view"},
    {"id": "threads", "label": "Switch to Threads view"},
]
OP_RE = re.compile(r"^(from|is|in|before|after):(.+)$", re.IGNORECASE)
REGEX_TOKEN = re.compile(r"^/(.+)/([imsx]*)$")
TOKEN_RE = re.compile(r'"[^"]*"|/(?:\\.|[^/])+/[imsx]*|\S+')


def parse(q: str) -> dict:
    """Split a query into filters and the remaining text."""
    out = {"authors": [], "is": set(), "in": None, "before": None, "after": None, "regex": None,
           "regex_flags": "", "phrases": [], "terms": []}
    for t in TOKEN_RE.findall(q):
        if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
            if t[1:-1].strip():
                out["phrases"].append(t[1:-1])
            continue
        rx = REGEX_TOKEN.match(t)
        if rx:
            out["regex"], out["regex_flags"] = rx.group(1), rx.group(2)
            continue
        if t.startswith("@") and len(t) > 1:
            out["authors"].append(t[1:])
            continue
        op = OP_RE.match(t)
        if op:
            name, value = op.group(1).lower(), op.group(2)
            if name == "from":
                out["authors"].append(value)
            elif name == "is":
                out["is"].add(value.lower())
            elif name == "in":
                out["in"] = value.lower()
            else:
                out[name] = value
            continue
        out["terms"].append(t)
    return out


def _day_ms(value: str, end: bool) -> int:
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ApiError(422, "bad_date", f"Dates look like 2026-11-05, not {value}.")
    if end:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp() * 1000)  # local time, as the user typed it


def _filters(conn, p: dict) -> tuple[list[str], list]:
    where, params = [], []
    if p["authors"]:
        ors = []
        for a in p["authors"]:
            ors.append("(LOWER(au.name) LIKE ? OR LOWER(COALESCE(au.display_name, '')) LIKE ?)")
            params += [a.lower() + "%", a.lower() + "%"]
        where.append("(" + " OR ".join(ors) + ")")
    if "excluded" not in p["is"]:
        where.append("m.excluded = 0")
    if "similar" in p["is"]:
        where.append("m.id IN (SELECT message_id FROM message_similar)")
    if "unattached" in p["is"]:
        where.append(
            "NOT EXISTS (SELECT 1 FROM links l WHERE l.unit_kind = 'message' AND l.unit_id = m.id) "
            "AND NOT EXISTS (SELECT 1 FROM links l WHERE l.unit_kind = 'block' AND l.unit_id = CAST(m.block_id AS TEXT))"
        )
    if "suggested" in p["is"]:
        where.append(
            "(EXISTS (SELECT 1 FROM links l WHERE l.unit_kind = 'message' AND l.unit_id = m.id AND l.status = 'suggested') "
            "OR (NOT EXISTS (SELECT 1 FROM links l WHERE l.unit_kind = 'message' AND l.unit_id = m.id) "
            "AND EXISTS (SELECT 1 FROM links l WHERE l.unit_kind = 'block' AND l.unit_id = CAST(m.block_id AS TEXT) AND l.status = 'suggested')))"
        )
    if p["in"] in ("approved", "archived"):
        col = "approved_at" if p["in"] == "approved" else "archived_at"
        extra = " AND c.archived_at IS NULL" if p["in"] == "approved" else ""
        where.append(
            f"EXISTS (SELECT 1 FROM conversations c WHERE c.id = {conversation_sql('m.channel_id', 'm.ts')} "
            f"AND c.last_ts >= m.ts AND c.{col} IS NOT NULL{extra})"
        )
    if p["before"]:
        where.append("m.ts <= ?")
        params.append(_day_ms(p["before"], end=True))
    if p["after"]:
        where.append("m.ts >= ?")
        params.append(_day_ms(p["after"], end=False))
    return where, params


def _fts_query(p: dict) -> Optional[str]:
    parts = ['"' + ph.replace('"', '""') + '"' for ph in p["phrases"]]
    terms = [t for t in p["terms"] if t.strip()]
    for i, t in enumerate(terms):
        safe = '"' + t.replace('"', '""') + '"'
        parts.append(safe + ("*" if i == len(terms) - 1 else ""))
    return " ".join(parts) or None


def _spans(text: str, needles: list[str]) -> list[list[int]]:
    spans = []
    low = text.lower()
    for n in needles:
        n = n.lower()
        if not n:
            continue
        start = 0
        while (i := low.find(n, start)) != -1:
            spans.append([i, i + len(n)])
            start = i + len(n)
    return sorted(spans)


def _conversation_id(conn, m) -> Optional[int]:
    row = conversation_at(conn, m["channel_id"], m["ts"])
    return row["id"] if row else None


def run(project, q: str, mode: str = "text", limit: int = 50) -> dict:
    p = parse(q or "")
    if mode == "regex" and not p["regex"] and (p["terms"] or p["phrases"]):
        p["regex"] = " ".join(p["phrases"] + p["terms"])
        p["terms"], p["phrases"] = [], []
    conn = project.conn
    with project.lock:
        where, params = _filters(conn, p)
        base = "FROM messages m JOIN authors au ON au.id = m.author_id"
        messages: list[dict] = []
        if p["regex"]:
            messages = _regex(conn, p, base, where, params, limit)
        else:
            fts = _fts_query(p)
            if fts:
                sql = (f"SELECT m.*, bm25(messages_fts) AS rank {base} JOIN messages_fts f ON f.rowid = m.rowid "
                       f"WHERE messages_fts MATCH ? {''.join(' AND ' + w for w in where)} ORDER BY rank LIMIT ?")
                rows = conn.execute(sql, [fts, *params, limit]).fetchall()
                needles = p["phrases"] + p["terms"]
                messages = [_message_out(conn, r, _spans(r["content_text"], needles)) for r in rows]
            elif where and (p["authors"] or p["is"] or p["in"] or p["before"] or p["after"]):
                sql = f"SELECT m.* {base} WHERE {' AND '.join(where)} ORDER BY m.ts DESC LIMIT ?"
                messages = [_message_out(conn, r, []) for r in conn.execute(sql, [*params, limit]).fetchall()]

        similar = []
        if messages and (p["terms"] or p["phrases"]) and embeddings.available():
            best = messages[0]
            similar = []
            for mid, score in embeddings.nearest(project, best["id"], best["author_id"], 5, 0.6):
                r = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
                if r:
                    similar.append({**_message_out(conn, r, []), "score": round(score, 3)})

        text = " ".join(p["terms"] + p["phrases"]).lower()
        people = []
        if text or p["authors"]:
            needles = [a.lower() for a in p["authors"]] + ([text] if text and not p["authors"] else [])
            matches = []
            for r in conn.execute("SELECT * FROM authors").fetchall():
                names = [(r["name"] or "").lower(), (r["display_name"] or "").lower()]
                if any(n and any(n.startswith(x) or x in n for x in needles) for n in names):
                    matches.append(r)
            if matches:
                total = conn.execute("SELECT COUNT(*) FROM messages WHERE excluded = 0").fetchone()[0] or 1
                people = [_person(conn, r, total) for r in matches[:5]]

        actions = [a for a in ACTIONS if text and text in a["label"].lower()] if text else []
    return {"messages": messages, "similar": similar, "people": people[:5], "actions": actions,
            "query": {"authors": p["authors"], "is": sorted(p["is"]), "regex": p["regex"]}}


def _regex(conn, p, base, where, params, limit) -> list[dict]:
    import time

    try:
        import regex as rx
    except ImportError:  # the `regex` package gives us timeouts; plain re is the fallback
        rx = None
    flags = 0
    for f in p["regex_flags"] or "i":
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}.get(f, 0)
    try:
        pattern = (rx or re).compile(p["regex"], flags)
    except Exception as e:
        raise ApiError(422, "bad_regex", f"That pattern doesn't compile: {e}")
    sql = f"SELECT m.* {base}{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY m.ts DESC"
    deadline = time.monotonic() + REGEX_BUDGET_S * 10
    out = []
    for r in conn.execute(sql, params):
        if time.monotonic() > deadline:
            break
        try:
            found = list(pattern.finditer(r["content_text"], timeout=REGEX_BUDGET_S)) if rx else list(pattern.finditer(r["content_text"]))
        except TimeoutError:
            raise ApiError(422, "bad_regex", "That pattern is too slow. Try something simpler.")
        if found:
            out.append(_message_out(conn, r, [[m.start(), m.end()] for m in found if m.end() > m.start()]))
            if len(out) >= limit:
                break
    return out


def _message_out(conn, r, highlights) -> dict:
    return {"id": r["id"], "author_id": r["author_id"], "ts": r["ts"], "content_text": r["content_text"],
            "highlights": highlights, "conversation_id": _conversation_id(conn, r), "excluded": bool(r["excluded"])}


def _person(conn, r, total: int) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM messages WHERE author_id = ? AND excluded = 0", (r["id"],)).fetchone()[0]
    mems = conn.execute("SELECT COUNT(*) FROM memories WHERE author_id = ? AND status != 'forgotten'", (r["id"],)).fetchone()[0]
    return {"author_id": r["id"], "name": r["name"], "display_name": r["display_name"], "color_index": r["color_index"],
            "messages": count, "share": round(count / total, 3), "memories": mems}
