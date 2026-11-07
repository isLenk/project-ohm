"""Import Discord chats into the open project. See Documentation/Curator/import.md.

`run(project, paths, job)` takes files and/or folders. Every file is turned into DiscordChatExporter documents
by formats.py, then everything is written in one transaction: authors, messages (ids already in the project
are skipped), auto-excludes, blocks, native reply links, conversations and reply suggestions.
Reading and parsing happen before the transaction, so the project stays usable while big files load.
"""

import hashlib
import json
import re
from datetime import datetime
from heapq import merge
from pathlib import Path
from typing import Optional

from . import conversations, formats, threads
from .errors import ApiError
from .project import Project, now_ms

NORMAL_TYPES = {"Default", "Reply"}
MENTION = re.compile(r"<(@!?|@&|#)(\d+)>")
CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
URL = re.compile(r"https?://\S+")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ms(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def expand(paths: list[str]) -> list[Path]:
    """Folders become the files directly inside them. Order: speakers, real exports, then JSONL dumps,
    so real exports win over converted copies of the same messages."""
    files: list[Path] = []
    for p in map(Path, paths):
        if not p.exists():
            raise ApiError(404, "not_found", f"{p} does not exist.")
        files += sorted(f for f in p.iterdir() if f.is_file()) if p.is_dir() else [p]
    order = {"speakers": 0, "dce": 1, "jsonl": 2}
    tagged = [(f, formats.kind_of(f)) for f in dict.fromkeys(files)]
    return [f for f, _ in sorted(tagged, key=lambda t: order.get(t[1], 3))]


# ── reading (outside the transaction) ──────────────────────────────────────
class Unit:
    """One channel's worth of messages from one file. Becomes one `sources` row."""

    def __init__(self, kind: str, file_name: str, sha: str, doc: dict):
        self.kind, self.file_name, self.sha = kind, file_name, sha
        self.guild, self.channel = doc["guild"], doc["channel"]
        self.authors: dict[str, dict] = {}
        self.mentioned: dict[str, dict] = {}
        self.roles: dict[str, str] = {}
        self.messages: list[dict] = []
        for m in doc["messages"]:
            a = m["author"]
            self.authors[a["id"]] = a
            for r in a.get("roles") or []:
                self.roles[r["id"]] = r["name"]
            for x in m.get("mentions") or []:
                self.mentioned[x["id"]] = x
            self.messages.append({
                "id": str(m["id"]), "ts": _ms(m["timestamp"]), "edited_ts": _ms(m.get("timestampEdited")),
                "type": m.get("type") or "Default", "content": m.get("content") or "", "author_id": a["id"],
                "is_bot": bool(a.get("isBot")),
                "reply_to": str((m.get("reference") or {}).get("messageId") or "") or None,
                "mentions": [x["id"] for x in m.get("mentions") or []],
                "attachments": [{"type": "attachment", "name": x.get("fileName"), "url": x.get("url") or None,
                                 "size": x.get("fileSizeBytes") or None} for x in m.get("attachments") or []]
                + [{"type": "embed", "title": e.get("title"), "url": e.get("url")} for e in m.get("embeds") or []]
                + [{"type": "sticker", "name": s.get("name")} for s in m.get("stickers") or []],
            })
        # Discord only lets a reply point back in time, but exports are not always sorted.
        self.messages.sort(key=lambda m: (m["ts"], int(m["id"])))


def _project_authors(conn) -> dict[str, dict]:
    return {r["id"]: {"id": r["id"], "name": r["name"], "discriminator": "0000", "nickname": r["display_name"],
                      "color": None, "isBot": bool(r["is_bot"]), "roles": [], "avatarUrl": r["avatar_url"]}
            for r in conn.execute("SELECT * FROM authors")}


def read(project: Project, files: list[Path], note) -> tuple[list[Unit], dict, list[dict]]:
    """Parse every file. Returns (units, speakers, per-file report)."""
    with project.lock:
        known = {r[0] for r in project.conn.execute("SELECT sha256 FROM sources")}
        project_authors = _project_authors(project.conn)
        # A thread's id is its starter message's id, so a thread's parent is that message's channel.
        project_channels = {r["channel_id"]: r["channel_name"] for r in project.conn.execute(
            "SELECT channel_id, channel_name FROM sources")}
    seen_shas = {k.split(":")[0] for k in known}
    units: list[Unit] = []
    speakers: dict[str, dict] = {}
    report: list[dict] = []
    dce_index = {"authors": {}, "guilds": {}, "channels": {}, "thread_parent": {}}

    for f in files:
        kind = formats.kind_of(f)
        entry = {"file": f.name, "kind": kind, "status": "imported", "messages": 0}
        report.append(entry)
        if kind == "conversations":
            entry.update(status="skipped", reason="The Curator builds its own conversations.")
            continue
        if kind == "unknown":
            entry.update(status="skipped", reason="Not a Discord export, messages.jsonl or speakers.json.")
            continue
        if kind == "speakers":
            note(f"Reading {f.name}")
            speakers.update(formats.load_speakers(f))
            entry["people"] = len(speakers)
            continue
        note(f"Hashing {f.name}")
        sha = _sha256(f)
        if sha in seen_shas:
            entry.update(status="skipped", reason="Already imported.")
            continue
        seen_shas.add(sha)
        note(f"Reading {f.name}")
        if kind == "dce":
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ApiError(422, "bad_file", f"{f.name} is not valid JSON: {e}")
            if not formats.is_dce(doc):
                raise ApiError(422, "bad_file", f"{f.name} is not a DiscordChatExporter JSON export.")
            idx = formats.index_dce([doc])
            for k in dce_index:
                dce_index[k].update(idx[k])
            unit = Unit("dce", f.name, sha, doc)
            del doc
            units.append(unit)
            entry["messages"] = len(unit.messages)
        else:  # jsonl
            authors = {**project_authors,
                       **{i: formats.author_from_speaker(i, s) for i, s in speakers.items() if i not in project_authors},
                       **dce_index["authors"]}
            for i, s in speakers.items():  # an alias beats any nickname
                if s.get("alias") and i in authors:
                    authors[i] = {**authors[i], "nickname": s["alias"]}

            def parent(cid: str) -> Optional[dict]:
                if cid in dce_index["thread_parent"]:
                    return dce_index["thread_parent"][cid]
                with project.lock:
                    row = project.conn.execute("SELECT channel_id FROM messages WHERE id = ?", (cid,)).fetchone()
                if row and row[0] != cid:
                    return {"id": row[0], "name": project_channels.get(row[0]) or row[0]}
                return None

            with f.open(encoding="utf-8") as lines:
                docs = formats.jsonl_to_dce(lines, authors, dce_index["guilds"], parent)
            for doc in docs:
                units.append(Unit("jsonl", f.name, f"{sha}:{doc['channel']['id']}", doc))
                entry["messages"] += doc["messageCount"]
            entry["channels"] = len(docs)
    return units, speakers, report


# ── writing (one transaction) ──────────────────────────────────────────────
def _filter_rules(conn) -> list[tuple]:
    out = []
    for r in conn.execute("SELECT id, kind, value FROM filter_rules WHERE enabled = 1"):
        if r["kind"] == "regex":
            try:
                out.append((r["id"], "regex", re.compile(r["value"], re.I)))
            except re.error:
                continue
        else:
            out.append((r["id"], r["kind"], r["value"]))
    return out


def _excluded(m: dict, text: str, author_name: str, rules: list[tuple]) -> Optional[str]:
    if m["is_bot"]:
        return "bot"
    if m["type"] not in NORMAL_TYPES:
        return "system"
    for rid, kind, value in rules:
        hit = (
            (kind == "regex" and value.search(text))
            or (kind == "author" and value in (m["author_id"], author_name))
            or (kind == "min_words" and str(value).isdigit() and len(text.split()) < int(value))
            or (kind == "link_only" and text.strip() and not URL.sub("", text).strip())
        )
        if hit:
            return f"filter:{rid}"
    return None


def _past_textless(reply_to: Optional[str], textless: dict[str, Optional[str]]) -> Optional[str]:
    """A reply to a dropped message replies to whatever that message replied to."""
    seen = set()
    while reply_to in textless and reply_to not in seen:
        seen.add(reply_to)
        reply_to = textless[reply_to]
    return None if reply_to in textless else reply_to


def _display(a: dict) -> str:
    return a.get("nickname") or a.get("name") or a.get("id")


def write(project: Project, units: list[Unit], speakers: dict, progress) -> dict:
    summary = {"source_ids": [], "messages": 0, "duplicates": 0, "empty": 0, "new_authors": 0, "channels": 0,
               "conversations": 0, "excluded": {}, "links": {"discord": 0, "suggested": 0, "root": 0, "unattached": 0}}
    if not units:
        return summary
    settings = project.settings()
    gap = settings["block_gap_ms"]
    with project.tx() as conn:
        existing_ids = {r[0] for r in conn.execute("SELECT id FROM messages")}
        colors = {r[0]: r[1] for r in conn.execute("SELECT id, color_index FROM authors")}
        next_color = max(colors.values(), default=-1) + 1
        rules = _filter_rules(conn)

        # names for <@id>, <@&id>, <#id>
        people: dict[str, dict] = {}
        roles: dict[str, str] = {}
        chans = {r[0]: r[1] for r in conn.execute("SELECT channel_id, channel_name FROM sources")}
        for u in units:
            people.update(u.mentioned)
            people.update(u.authors)
            roles.update(u.roles)
            chans[u.channel["id"]] = u.channel.get("name")
        for i, s in speakers.items():
            if s.get("alias") and i in people:
                people[i] = {**people[i], "nickname": s["alias"]}

        def readable(text: str) -> str:
            def sub(m):
                sigil, i = m.group(1), m.group(2)
                if sigil == "#":
                    return f"#{chans.get(i) or 'channel'}"
                if sigil == "@&":
                    return f"@{roles.get(i) or 'role'}"
                return f"@{_display(people[i]) if i in people else 'unknown'}"
            return CUSTOM_EMOJI.sub(r":\1:", MENTION.sub(sub, text))

        # authors: only people who posted; mentions are just names
        posted = {a_id: people[a_id] for u in units for a_id in u.authors}
        for a_id, a in posted.items():
            display = a.get("nickname") if a.get("nickname") and a.get("nickname") != a.get("name") else None
            if a_id in colors:
                conn.execute(
                    "UPDATE authors SET name = ?, display_name = COALESCE(?, display_name), "
                    "avatar_url = COALESCE(?, avatar_url), is_bot = ? WHERE id = ?",
                    (a.get("name") or a_id, display, a.get("avatarUrl"), int(bool(a.get("isBot"))), a_id))
            else:
                conn.execute(
                    "INSERT INTO authors (id, name, display_name, avatar_url, is_bot, color_index) VALUES (?, ?, ?, ?, ?, ?)",
                    (a_id, a.get("name") or a_id, display, a.get("avatarUrl"), int(bool(a.get("isBot"))), next_color))
                colors[a_id] = next_color
                next_color += 1
                summary["new_authors"] += 1
        for i, s in speakers.items():
            if s.get("alias"):
                conn.execute("UPDATE authors SET display_name = ? WHERE id = ?", (s["alias"], i))

        # messages, grouped by channel
        new_by_channel: dict[str, list[dict]] = {}
        # Messages with no text (only images, attachments or nothing) aren't kept: there's nothing to
        # thread or train on. id -> what it replied to, so replies to it can point past it.
        textless: dict[str, Optional[str]] = {}
        for u in units:
            fresh = []
            for m in u.messages:
                if m["id"] in existing_ids or m["id"] in textless:
                    summary["duplicates"] += 1
                    continue
                m["text"] = readable(m["content"])
                if not m["text"].strip():
                    textless[m["id"]] = m["reply_to"]
                    continue
                existing_ids.add(m["id"])
                fresh.append(m)
            if u.kind == "jsonl" and not fresh:
                continue  # nothing this dump adds for the channel; don't record it as a source
            sid = conn.execute(
                "INSERT INTO sources (kind, file_name, sha256, guild_id, guild_name, channel_id, channel_name, imported_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (u.kind, u.file_name, u.sha, u.guild.get("id"), u.guild.get("name"), u.channel["id"],
                 u.channel.get("name"), now_ms(), len(u.messages))).lastrowid
            summary["source_ids"].append(sid)
            for m in fresh:
                m["source_id"] = sid
                m["exclude_reason"] = _excluded(m, m["text"], posted[m["author_id"]].get("name", ""), rules)
                if m["exclude_reason"]:
                    key = m["exclude_reason"].split(":")[0]
                    summary["excluded"][key] = summary["excluded"].get(key, 0) + 1
            new_by_channel.setdefault(u.channel["id"], []).extend(fresh)
        summary["empty"] = len(textless)
        for fresh in new_by_channel.values():
            for m in fresh:
                m["reply_to"] = _past_textless(m["reply_to"], textless)

        next_block = (conn.execute("SELECT MAX(id) FROM blocks").fetchone()[0] or 0) + 1
        all_ids = existing_ids
        links: list[tuple] = []
        for cid, fresh in new_by_channel.items():
            if not fresh:
                continue
            summary["channels"] += 1
            fresh.sort(key=lambda m: (m["ts"], int(m["id"])))
            old = [dict(r) | {"old": True} for r in conn.execute(
                "SELECT id, ts, author_id, block_id, reply_to_id AS reply_to FROM messages WHERE channel_id = ? "
                "ORDER BY ts, CAST(id AS INTEGER)", (cid,))]
            blocks: dict[int, list] = {}      # new blocks: id -> [author, first_ts, last_ts]
            grown: dict[int, list] = {}       # existing blocks that new messages joined: id -> [min_ts, max_ts]
            starters: dict[int, dict] = {}    # new block -> its first message
            prev = None
            for m in merge(old, fresh, key=lambda m: (m["ts"], int(m["id"]))):
                if m.get("old"):
                    prev = m
                    continue
                join = (prev is not None and prev["author_id"] == m["author_id"] and m["ts"] - prev["ts"] <= gap
                        and (m["reply_to"] is None or prev["reply_to"] == m["reply_to"]))
                if join:
                    m["block_id"] = prev["block_id"]
                    if prev["block_id"] in blocks:
                        blocks[prev["block_id"]][2] = m["ts"]
                    else:
                        g = grown.setdefault(prev["block_id"], [m["ts"], m["ts"]])
                        g[0], g[1] = min(g[0], m["ts"]), max(g[1], m["ts"])
                else:
                    m["block_id"] = next_block
                    blocks[next_block] = [m["author_id"], m["ts"], m["ts"]]
                    starters[next_block] = m
                    next_block += 1
                prev = m
            conn.executemany("INSERT INTO blocks (id, channel_id, author_id, first_ts, last_ts) VALUES (?, ?, ?, ?, ?)",
                             [(bid, cid, a, f, l) for bid, (a, f, l) in blocks.items()])
            conn.executemany("UPDATE blocks SET first_ts = MIN(first_ts, ?), last_ts = MAX(last_ts, ?) WHERE id = ?",
                             [(f, l, bid) for bid, (f, l) in grown.items()])
            conn.executemany(
                "INSERT INTO messages (id, source_id, channel_id, author_id, ts, edited_ts, type, content, content_text, "
                "reply_to_id, mentions, attachments, block_id, excluded, exclude_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(m["id"], m["source_id"], cid, m["author_id"], m["ts"], m["edited_ts"], m["type"], m["content"],
                  m["text"], m["reply_to"], json.dumps(m["mentions"]), json.dumps(m["attachments"]), m["block_id"],
                  int(m["exclude_reason"] is not None), m["exclude_reason"]) for m in fresh])
            # A block only takes a message that replies to the same target as the one before it,
            # so a block's native reply is always its first message's.
            links += [("block", str(bid), m["reply_to"], "accepted", "discord", None, now_ms())
                      for bid, m in starters.items() if m["reply_to"] and m["reply_to"] in all_ids]
            summary["messages"] += len(fresh)

        # After every channel's messages are in: a reply can point into another channel (a thread's parent).
        conn.executemany(
            "INSERT INTO links (unit_kind, unit_id, parent_id, status, origin, score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(unit_kind, unit_id) DO NOTHING", links)
        summary["links"]["discord"] += len(links)

        for cid in (c for c, f in new_by_channel.items() if f):
            progress(0, 0, f"Building conversations in #{chans.get(cid) or cid}")
            summary["conversations"] += conversations.rebuild(conn, cid, settings)["conversations"]
            counts = threads.suggest_all(
                conn, settings, cid, progress=lambda d, t, c=cid: progress(d, t, f"Suggesting replies in #{chans.get(c) or c}"))
            for k, v in counts.items():
                summary["links"][k] += v
    return summary


def run(project: Project, paths: list[str], job=None) -> dict:
    def progress(done, total, note=None):
        if job:
            job.progress(done, total, note)

    files = expand(paths)
    if not files:
        raise ApiError(422, "nothing_to_import", "No files to import.")
    units, speakers, report = read(project, files, lambda n: progress(0, 0, n))
    if not units and not speakers:
        if any(r.get("reason") == "Already imported." for r in report):
            raise ApiError(409, "already_imported", "Everything in that selection is already in this project.", files=report)
        raise ApiError(422, "nothing_to_import", "None of those files are Discord exports or messages.jsonl.", files=report)
    summary = write(project, units, speakers, progress)
    if speakers and not units:
        with project.tx() as conn:
            for i, s in speakers.items():
                if s.get("alias"):
                    conn.execute("UPDATE authors SET display_name = ? WHERE id = ?", (s["alias"], i))
    return {**summary, "files": report}


def listing(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.source_id = s.id) AS imported_messages "
        "FROM sources s ORDER BY s.imported_at, s.id")]
