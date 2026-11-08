"""Memories about each person: proposed from their messages, reviewed by the user, pushed to memory_service.

See Documentation/Curator/memories.md.
"""

import json
import re
from collections import Counter
from typing import Optional

import requests

from . import embeddings, llm, terms
from .config import MEMORY_API_KEY, MEMORY_API_URL
from .errors import ApiError, not_found
from .project import now_ms
from .threads import conversation_sql

MIN_WORDS = 3
DUPLICATE = 0.9
SAME = 0.84  # close enough to show as one memory in the list; below DUPLICATE, so they were stored apart
STYLE_MIN_BLOCKS = 10
STYLE_MIN_SHARE = 0.2

CONTEXT_MESSAGES = 10   # at most this many channel messages before a block go in as context,
CONTEXT_MINUTES = 30    # from at most this long before it
CONTEXT_CHARS = 300
MAX_NOTE = 200
PING = re.compile(r"@\S+")
VAGUE_ENDINGS = {"it", "this", "that", "them", "there", "participate", "join", "help", "come", "go", "too", "so", "in"}

# NAME is swapped for the person's name. Examples with a fixed real name taught the model to write that
# name (and that game) into notes about everyone else.
EXTRACT_SYSTEM = (
    "You decide what's worth remembering long-term about NAME from what NAME said in a group chat.\n"
    "You get the last few messages before theirs as context, then NAME's messages and when they were sent. "
    "Only make notes about NAME, never about anyone in the context; use the context only to understand what "
    "NAME means.\n"
    "First work out what NAME actually means, the way their friends in the chat would read it:\n"
    "- Questions, \"if\" and \"would\" sentences, hypotheticals, jokes, sarcasm and exaggeration are not facts. "
    "\"So if I lived in Paris would you visit?\" does not mean NAME lives in Paris.\n"
    "- A list of names and numbers, a score, or anything about other people is not about NAME.\n"
    "- Pinging people with an amount of money is asking them to pay NAME back, not NAME owing them.\n"
    "- Only note what NAME plainly says about themself.\n"
    "Worth remembering: lasting things such as likes and dislikes, hobbies, games they play, job or school, where "
    "they live, pets, family, partners and friends, plans with a date, life events, and opinions they clearly hold.\n"
    "Not worth remembering: greetings, filler, questions, reactions, jokes, insults, small talk, agreeing or "
    "saying yes or no to something, being free or busy for one event, who owes whom money or splitting a bill, "
    "anything about the conversation itself, and requests to a bot.\n"
    "The note must make sense on its own months later, read by someone who never saw the chat. Say what the "
    "thing is: name the game, the person, the place, the event. Never write \"the situation\", \"the game\", "
    "\"the thing\" or \"being discussed\" for something you could name. If you can't tell what they mean, even "
    "with the context, nothing is worth remembering.\n"
    "If the note is about a particular day, write the date out from when NAME wrote it (\"on Monday, March 2, "
    "2026\"), never \"today\", \"tomorrow\", \"tonight\", \"on Monday\" or \"next week\", and set short_lived to true.\n"
    "Good: \"NAME has two cats called Miso and Tofu.\" "
    "\"NAME is studying nursing at college.\"\n"
    "Bad: \"NAME is down to participate.\" (in what?) \"NAME agrees.\" "
    "\"NAME likes it.\" (likes what?) \"NAME told his mom about the situation.\" (what situation?)\n"
    "Never write \"NAME says that...\" or \"NAME states that...\". If what NAME says is about someone or "
    "something else, nothing is worth remembering about NAME; never turn it into a note about NAME.\n"
    "Reply with JSON only, one object with exactly these keys, in this order:\n"
    '{"meaning": "<what NAME means, in one plain sentence>", '
    '"said_as": "<one word: statement, question, hypothetical, joke or request>", '
    '"note": "<one short third-person sentence that starts with NAME>" or null, '
    '"category": "fact" or "relationship", '
    '"short_lived": true if it only matters around one day (\"was out all day\"), '
    '"mentions_person": "<a person they mention by name who is not in the chat>" or null}\n'
    'Only a statement can be worth remembering. If nothing is, set "note" to null.'
)


def _extract_system(name: str) -> str:
    return EXTRACT_SYSTEM.replace("NAME", name)


def _about(note: str, names) -> bool:
    """A note is filed under the person whose block it came from, so it has to be about them."""
    head = note.lstrip("@ ").lower()
    return any(n and head.startswith(n.lower()) for n in names)


def _drop_misattributed(conn) -> int:
    """Delete unreviewed notes that today's checks would turn down (about someone else, vague, a debt, copied
    text), and mark their blocks unread so the next run asks about them again. Unreviewed notes about "today"
    or "on Monday" get the date they were said. Kept, forgotten and linked notes are the user's."""
    rows = conn.execute(
        "SELECT m.id, m.text, a.name, a.display_name, (SELECT MIN(msg.ts) FROM memory_sources s JOIN messages msg "
        "ON msg.id = s.message_id WHERE s.memory_id = m.id) AS said_ts FROM memories m JOIN authors a ON a.id = m.author_id "
        "WHERE m.status = 'new' AND m.category != 'style' AND m.linked_author_id IS NULL"
    ).fetchall()
    wrong = [r["id"] for r in rows
             if not _about(r["text"], (r["display_name"], r["name"])) or _vague(r["text"])
             or NOT_LASTING.search(r["text"]) or len(r["text"]) > MAX_NOTE]
    for r in rows:
        if r["id"] not in wrong and r["said_ts"] and RELATIVE_DAY.search(r["text"]) and not YEAR.search(r["text"]):
            text = f"{r['text'].rstrip('.')} (said {_when(r['said_ts']).rsplit(',', 1)[0]})."
            conn.execute("UPDATE memories SET text = ?, short_lived = 1, updated_at = ? WHERE id = ?", (text, now_ms(), r["id"]))
    for mid in wrong:
        conn.execute(
            "DELETE FROM memory_scans WHERE block_id IN (SELECT msg.block_id FROM memory_sources s "
            "JOIN messages msg ON msg.id = s.message_id WHERE s.memory_id = ?)", (mid,)
        )
        conn.execute("DELETE FROM memory_sources WHERE memory_id = ?", (mid,))
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    return len(wrong)


# "told his mom about the situation", "interested in the game being discussed": a note that points at
# something instead of naming it. "the game Killing Floor" names it, so a name right after is fine.
VAGUE_PHRASES = re.compile(
    r"\bthe (situation|thing|things|stuff|issue|problem|matter|topic|idea|event|game|place|plan|one)\b(?!\s+[A-Z0-9\"“'])"
    r"|\b(being|they are|they were|was being) (discussed|mentioned|talked about)\b|\bthe conversation\b"
    r"|\((likely|probably|possibly|presumably|maybe)\b|\b(something|someone|somewhere|stuff)\b(?!\s+(called|named)\b)"
)
# Things the model writes down although the prompt says not to.
NOT_LASTING = re.compile(
    r"\bowe[sd]?\b|\b(pay|paid|paying) (\w+ ){0,3}back\b|\bsplit(ting)? the (bill|cost)\b"  # who owes whom
    r"|^\S+(\s\S+)? (states|says|said|mentions|mentioned|explains|notes|claims|points out|clarifies) that\b",  # reported speech
    re.IGNORECASE,
)
# "today", "on Monday": only true around when it was said. The prompt asks for the date written out; when
# the model doesn't, the date goes on the end ("... on Monday (said Friday, November 7, 2025).").
RELATIVE_DAY = re.compile(
    r"\b(today|tonight|tomorrow|tmrw|tmr|yesterday|this (morning|afternoon|evening|weekend|week)|next (week|weekend)"
    r"|(on|this|next) (monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
YEAR = re.compile(r"\b(19|20)\d\d\b")
KINDS = ("statement", "question", "hypothetical", "joke", "request")


def _said_as(result: dict) -> str:
    """How NAME said it. Small models sometimes write the options as keys ({"question": true}) or put a
    quote in said_as, so read both."""
    kind = str(result.get("said_as") or "").strip().lower()
    if kind in KINDS:
        return kind
    flagged = [k for k in KINDS[1:] if result.get(k) is True]
    return flagged[0] if flagged else "statement"


def _vague(note: str) -> bool:
    """Notes that end on a pronoun or a bare verb ("is down to participate"), or point at something without
    naming it ("the situation"), don't say what they're about."""
    words = re.findall(r"[a-z']+", note.lower())
    return len(words) < 3 or words[-1] in VAGUE_ENDINGS or bool(VAGUE_PHRASES.search(note))


def _when(ts: int) -> str:
    """"Friday, November 7, 2025, 9:50 PM" in this machine's time zone, so the model can work out "Monday"."""
    from datetime import datetime

    d = datetime.fromtimestamp(ts / 1000)
    return f"{d:%A, %B} {d.day}, {d.year}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def _readable(r) -> bool:
    """Context that says something in words: not blank, not a file or sticker, not just pinging people.
    Links stay; a store page or video title often says what the replies are about."""
    if any(a.get("type") in ("attachment", "sticker") for a in json.loads(r["attachments"] or "[]")):
        return False
    return bool(re.search(r"[^\W\d_]{2}", PING.sub(" ", r["content_text"])))


def _prompt(project, b, msgs, shared_terms: list = ()) -> str:
    """The person's messages, after what the channel said in the half hour before them, so the model
    knows what they refer to."""
    recent = project.all(
        "SELECT COALESCE(a.display_name, a.name) AS who, m.content_text, m.attachments FROM messages m "
        "JOIN authors a ON a.id = m.author_id "
        "WHERE m.channel_id = ? AND m.ts < ? AND m.ts >= ? AND m.excluded = 0 "
        "ORDER BY m.ts DESC, CAST(m.id AS INTEGER) DESC LIMIT ?",
        (b["channel_id"], b["first_ts"], b["first_ts"] - CONTEXT_MINUTES * 60_000, CONTEXT_MESSAGES * 5),
    )
    before = [r for r in recent if _readable(r)][:CONTEXT_MESSAGES]
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
    prompt = f"Context, earlier in the chat:\n{context}\n\nWhat {name} said on {_when(b['first_ts'])}:\n{said}"
    # Words from the General terminology tab (this project's, then other projects') that come up here,
    # so the model reads slang and typos right.
    with project.lock:
        glossary = terms.glossary_for(project.conn, context + "\n" + said, extra=shared_terms)
    if glossary:
        prompt = "Words this group uses:\n" + "\n".join(glossary) + "\n\n" + prompt
    return prompt


# Priority is read off the wording, so it works for every memory already stored. First match wins.
IMPORTANT = re.compile(
    r"\b(lives?|living|moved|moving|works?|working|job|jobs|intern(ship)?|school|college|university|uni|"
    r"stud(ying|ies|ent)|major(ing)?|degree|graduat\w*|birthday|born|years? old|mom|dad|mother|father|sister|"
    r"brother|siblings?|parents?|family|cousin|son|daughter|girlfriend|boyfriend|gf|bf|wife|husband|partner|"
    r"dating|married|engaged|pregnan\w*|dogs?|cats?|pets?|allerg\w*|vegan|vegetarian|owns?|drives)\b"
)
FLEETING = re.compile(
    r"\b(currently|right now|rn|tonight|today|tomorrow|this (morning|afternoon|evening|weekend)|at the moment|"
    r"soon|later|heading|on (his|her|their) way|will be|in (\d+|a few|about \d+) ?(mins?|minutes|hours?)|"
    r"asking|asked|wondering|ready to|about to|going to|leaving|arriv\w+|eta)\b"
)
INTEREST = re.compile(
    r"\b(likes?|loves?|enjoys?|hates?|dislikes?|favou?rites?|fan|into|prefers?|interested|obsessed|plays?|"
    r"playing|watch(es|ing)?|listens?|reads?|collects?|hobb(y|ies)|mains?|maining)\b"
)
OPINION = re.compile(
    r"\b(thinks?|believes?|feels?|finds|considers?|says?|said|opinion|agrees?|disagrees?|suggests?|wants?|"
    r"wishes|hopes?|would rather|calls?)\b"
)


def priority(text: str, short_lived: bool, category: str = "fact") -> str:
    """important, interest, opinion, other or short_lived."""
    if category == "style":
        return "other"
    t = text.lower()
    if short_lived:
        return "short_lived"
    if IMPORTANT.search(t):
        return "important"
    if FLEETING.search(t):
        return "short_lived"
    if INTEREST.search(t):
        return "interest"
    if OPINION.search(t):
        return "opinion"
    return "other"


# How sure a group of notes is worth keeping, for "Sure bets". Read off what's already stored, like priority.
BASE_CONFIDENCE = {"important": 0.45, "interest": 0.4, "opinion": 0.2, "other": 0.15, "short_lived": 0.0}
# Who someone is, said right after their name ("Lenk lives in Ottawa", "Lenk's dad..."). IMPORTANT alone
# also matches "the mic doesn't work" and "dogs of war", so it takes this, or saying it again, to be sure.
IDENTITY = re.compile(
    r"^(?:[\w.-]+ ){1,3}?(?:lives|works|worked|studies|studied|goes to|attends|graduated|owns|drives|was born|"
    r"is \d+ years old|has (?:a|an|two|three|\d+) (?:dogs?|cats?|pets?|kids?|sons?|daughters?|brothers?|sisters?|"
    r"girlfriend|boyfriend|wife|husband|partner|car|job))\b"
    r"|^[\w.-]+(?: [\w.-]+)?'s (?:birthday|mom|dad|mother|father|parents|sisters?|brothers?|girlfriend|boyfriend|"
    r"wife|husband|partner|dog|cat)\b"
)
# Counted, not guessed, but the slang trait picks up plenty of ordinary words ("go", "who"): check them by eye.
STYLE_CONFIDENCE = 0.5
SURE = 0.6
# Plans, passing states and things said to others, even when "work" or "parents" made it important.
# FLEETING and OPINION count too: "thinks his parents don't get it" is an opinion, not who he is.
UNSETTLED = re.compile(
    r"\b(looking for|looking to|trying to|thinking (about|of)|planning|plans? to|considering|deciding|"
    r"tells?|told|telling|asks?|questioning|suggesting|noticed|forgot|anticipating|willing|waiting|is being|"
    # one evening's plans: "is playing CS with...", "is down to play", "this Saturday"
    r"is (?:playing|watching|hosting|down|up for)|(?:this|on|next) (?:mon|tues|wednes|thurs|fri|satur|sun)day)\b"
)
HEDGED = re.compile(
    r"\b(might|maybe|perhaps|possibly|probably|likely|seems?|seemingly|appears?|apparently|may|could|"
    r"sometimes|unclear|unsure|not sure|or something)\b"
)


def confidence(text: str, priority: str, category: str, said: int, similar: int, unlinked: bool) -> float:
    """0..1. Starts from the priority ("works at", "lives in" count most) and goes up each time it came up
    again: `said` is how many separate times (blocks) it was said, `similar` how many other notes say the
    same. Hedged wording ("might", "seems"), plans and passing states ("looking for", "currently"), very
    short or very long notes, and people nobody linked yet count against it. SURE or more is a sure bet."""
    if category == "style":
        return STYLE_CONFIDENCE
    score = BASE_CONFIDENCE[priority]
    score += 0.12 * min(max(said - 1, 0), 3)
    score += 0.08 * min(similar, 2)
    t = text.lower()
    if priority == "important" and IDENTITY.search(t):
        score += 0.15
    if HEDGED.search(t):
        score -= 0.25
    if UNSETTLED.search(t) or FLEETING.search(t) or OPINION.search(t):
        score -= 0.15
    words = len(t.split())
    if words < 5 or words > 30:
        score -= 0.1
    if unlinked:
        score -= 0.05
    return round(min(max(score, 0.0), 1.0), 2)


def _memory_out(conn, r) -> dict:
    sources = conn.execute(
        "SELECT m.id, m.ts, m.content_text, m.block_id FROM memory_sources s JOIN messages m ON m.id = s.message_id "
        "WHERE s.memory_id = ? ORDER BY m.ts", (r["id"],)
    ).fetchall()
    out = {k: r[k] for k in r.keys() if k != "embedding"}
    out["short_lived"] = bool(r["short_lived"])
    out["priority"] = priority(r["text"], out["short_lived"], r["category"])
    out["synced"] = r["remote_id"] is not None
    out["sources"] = [dict(s) for s in sources]
    return out


def _groups(rows) -> dict[int, int]:
    """Memory id -> id of the first memory (in list order) that says the same thing. Notes are compared
    with the first one of each group, not chained, so a group can't drift from one topic to another."""
    leaders: list[tuple[int, tuple, object]] = []
    out = {}
    for r in rows:
        out[r["id"]] = r["id"]
        if r["embedding"] is None or r["category"] == "style":
            continue
        vec = embeddings.from_blob(r["embedding"])
        key = (r["author_id"], r["status"])  # only notes that could be merged into one another
        for lid, lkey, lvec in leaders:
            if lkey == key and float(vec @ lvec) >= SAME:
                out[r["id"]] = lid
                break
        else:
            leaders.append((r["id"], key, vec))
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
    groups = _groups(rows)
    out = [{**_memory_out(conn, r), "group": groups[r["id"]]} for r in rows]
    _score(out)
    return out


def _score(mems: list[dict]) -> None:
    """Give every memory its group's confidence and whether that makes it a sure bet."""
    members: dict[int, list[dict]] = {}
    for m in mems:
        members.setdefault(m["group"], []).append(m)
    for group in members.values():
        # The wording the list shows for the group, and the one kept. Its own priority counts, not the
        # group's best: a short-lived note with one "important" look-alike is still short-lived.
        lead = max(group, key=lambda m: len(m["sources"]))
        said = len({s["block_id"] for m in group for s in m["sources"]})
        unlinked = lead["category"] == "relationship" and bool(lead["mentioned_name"]) and not lead["linked_author_id"]
        c = confidence(lead["text"], lead["priority"], lead["category"], said, len(group) - 1, unlinked)
        for m in group:
            m["confidence"] = c
            m["sure"] = c >= SURE


def merge(conn, into: int, ids: list[int]) -> dict:
    """Fold notes that say the same thing into one: their messages become its sources, then they go."""
    target = get(conn, into)
    for mid in ids:
        if mid == into:
            continue
        r = get(conn, mid)
        if r["author_id"] != target["author_id"]:
            raise ApiError(422, "other_person", "Only memories about the same person can be merged.")
        _remote_delete(conn, r)
        conn.execute("INSERT OR IGNORE INTO memory_sources SELECT ?, message_id FROM memory_sources WHERE memory_id = ?",
                     (into, mid))
        conn.execute("DELETE FROM memory_sources WHERE memory_id = ?", (mid,))
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (now_ms(), into))
    return _memory_out(conn, get(conn, into))


def around(conn, message_id: str, before: int = 8, after: int = 4) -> dict:
    """A message with what the channel said just before and after it, for previewing where a memory came from."""
    m = conn.execute("SELECT id, channel_id, ts FROM messages WHERE id = ?", (message_id,)).fetchone()
    if not m:
        raise not_found("Message")
    cols = ("SELECT m.id, m.ts, m.author_id, m.content_text, m.excluded, m.reply_to_id, m.attachments, "
            "a.name, a.display_name, a.avatar_url, a.color_index, a.is_bot FROM messages m JOIN authors a ON a.id = m.author_id ")
    order = "m.ts, CAST(m.id AS INTEGER)"
    earlier = conn.execute(
        cols + f"WHERE m.channel_id = ? AND (m.ts < ? OR (m.ts = ? AND CAST(m.id AS INTEGER) < CAST(? AS INTEGER))) "
        f"ORDER BY m.ts DESC, CAST(m.id AS INTEGER) DESC LIMIT ?", (m["channel_id"], m["ts"], m["ts"], m["id"], before)
    ).fetchall()
    rest = conn.execute(
        cols + f"WHERE m.channel_id = ? AND (m.ts > ? OR (m.ts = ? AND CAST(m.id AS INTEGER) >= CAST(? AS INTEGER))) "
        f"ORDER BY {order} LIMIT ?", (m["channel_id"], m["ts"], m["ts"], m["id"], after + 1)
    ).fetchall()
    msgs = []
    for r in [*reversed(earlier), *rest]:
        files = [a for a in json.loads(r["attachments"] or "[]") if a.get("type") in ("attachment", "sticker")]
        msgs.append({
            "id": r["id"], "ts": r["ts"], "author_id": r["author_id"], "content_text": r["content_text"],
            "excluded": bool(r["excluded"]), "reply_to_id": r["reply_to_id"], "files": len(files),
            "author": {"name": r["name"], "display_name": r["display_name"], "avatar_url": r["avatar_url"],
                       "color_index": r["color_index"], "is_bot": bool(r["is_bot"])},
        })
    return {"message_id": message_id, "channel_id": m["channel_id"], "messages": msgs}


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


def keep_all(conn, author_id: str, sure_only: bool = False) -> int:
    """Keep every new memory that isn't short-lived (or only the sure bets), folding ones that say the same
    thing into one first."""
    rows = listing(conn, author_id, "new")
    members: dict[int, list[int]] = {}
    for m in rows:
        if m["author_id"] == author_id:
            members.setdefault(m["group"], []).append(m["id"])
    by_id = {m["id"]: m for m in rows}
    kept = 0
    for ids in members.values():
        if all(by_id[i]["priority"] == "short_lived" for i in ids):
            continue
        if sure_only and not by_id[ids[0]]["sure"]:
            continue
        lead = max(ids, key=lambda i: len(by_id[i]["sources"]))  # the wording that came up most, as the UI shows it
        merge(conn, lead, ids)
        conn.execute("UPDATE memories SET status = 'kept', updated_at = ? WHERE id = ?", (now_ms(), lead))
        kept += 1
    return kept


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
    with project.tx() as conn:
        dropped = _drop_misattributed(conn)
    blocks = project.all(sql + " ORDER BY b.first_ts DESC, b.id DESC", params)
    use_vectors = embeddings.available()
    shared_terms = terms.from_other_projects(project)
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
                result = llm.chat_json(_extract_system(b["display_name"] or b["name"]), _prompt(project, b, msgs, shared_terms))
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
            # Questions, hypotheticals and jokes aren't facts, even when the model wrote a note anyway.
            if _said_as(result) != "statement":
                continue
            note = str(result["note"]).strip().strip('"')
            # Past MAX_NOTE it's text copied out of the message, not a note about the person.
            if len(note) > MAX_NOTE or _vague(note) or NOT_LASTING.search(note) or not _about(note, (b["display_name"], b["name"])):
                continue
            short_lived = bool(result.get("short_lived"))
            if RELATIVE_DAY.search(note) and not YEAR.search(note):
                note = f"{note.rstrip('.')} (said {_when(b['first_ts']).rsplit(',', 1)[0]})."
                short_lived = True
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
            add(conn, b["author_id"], category, note, short_lived=short_lived,
                mentioned_name=str(mentioned) if mentioned else None, source_ids=tuple(m["id"] for m in msgs), embedding=vec)
            created += 1
    job.progress(len(blocks), len(blocks), "done")
    with project.tx() as conn:
        styles = refresh_styles(conn)
    return {"blocks": len(blocks), "created": created, "merged": merged, "failed": failed, "stopped": stopped,
            "style_traits": styles, "dropped": dropped}


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
        "SELECT mem.*, a.name, a.display_name, a.avatar_url FROM memories mem "
        "JOIN authors a ON a.id = COALESCE(mem.linked_author_id, mem.author_id) "
        "WHERE mem.status = 'kept' AND mem.remote_id IS NULL"
    ).fetchall()
    synced, failed, error = 0, 0, None
    for r in rows:
        user_id = r["linked_author_id"] or r["author_id"]
        user = {"display_name": r["display_name"] or r["name"]}
        # The avatar from the export, so the app's people lists show it. An export made with media downloads
        # points at a local file the NAS can't serve, so only web URLs go.
        if (r["avatar_url"] or "").startswith(("http://", "https://")):
            user["avatar_url"] = r["avatar_url"]
        try:
            s.put(f"{MEMORY_API_URL}/users/{user_id}", json=user, timeout=(2, 5)).raise_for_status()
            res = s.post(f"{MEMORY_API_URL}/users/{user_id}/facts", json={"character": character, "fact": r["text"]},
                         timeout=(2, 10))
            res.raise_for_status()
            conn.execute("UPDATE memories SET remote_id = ? WHERE id = ?", (res.json()["id"], r["id"]))
            synced += 1
        except requests.RequestException as e:
            print(f"[curator] memory sync failed for {r['id']}: {e}")
            failed += 1
            status = e.response.status_code if e.response is not None else None
            # A wrong key or an unreachable NAS fails every row the same way, so stop at the first one.
            if status in (401, 403):
                error = "The memory service rejected the API key. Check MEMORY_API_KEY in Settings."
                break
            if isinstance(e, requests.ConnectionError):
                error = f"Could not reach the memory service at {MEMORY_API_URL}."
                break
            error = str(e)
    return {"synced": synced, "failed": failed, "remaining": len(rows) - synced, "error": error}
