"""Input formats for the importer. Everything is turned into DiscordChatExporter (DCE) JSON documents,
so the importer has a single parser. See Documentation/Curator/import.md.

Supported files:
- DiscordChatExporter JSON (`--format Json`), one channel or thread per file. The reference format.
- messages.jsonl, a flattened dump (one message per line, all channels). Converted to one DCE document per
  channel. It dropped some fields, so a real export of the same messages always wins (the importer skips
  message ids it already has, and imports DCE files before JSONL ones):
    - edit time: only an `edited` flag survives, so timestampEdited is set to the send time
    - attachment URLs and sizes: only file names survive
    - reaction users and custom emoji images: only emoji -> count survives
    - system message types other than pins and thread starts become "Default"
- speakers.json from the same dump: names and aliases. An alias becomes the person's display name.
- conversations.json from the same dump: skipped. The Curator builds its own conversations from silences.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

SYSTEM_TYPES = {"Pinned a message.": "ChannelPinnedMessage", "Started a thread.": "ThreadCreated"}


def kind_of(path: Path) -> str:
    """'dce' | 'jsonl' | 'speakers' | 'conversations' | 'unknown', by name and a peek at the content."""
    if path.suffix.lower() == ".jsonl":
        return "jsonl"
    if path.suffix.lower() != ".json":
        return "unknown"
    with path.open("rb") as f:
        head = f.read(4096).decode("utf-8", errors="ignore")
    if '"guild"' in head and '"channel"' in head:
        return "dce"
    if '"speakers"' in head:
        return "speakers"
    if '"conversations"' in head:
        return "conversations"
    return "unknown"


def is_dce(doc) -> bool:
    return isinstance(doc, dict) and "guild" in doc and "channel" in doc and "messages" in doc


def load_speakers(path: Path) -> dict[str, dict]:
    """speakers.json -> {author_id: {name, alias, variants, is_bot}}."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): v for k, v in (doc.get("speakers") or {}).items()}


def author_from_speaker(author_id: str, s: dict) -> dict:
    variants = s.get("variants") or []
    nick = s.get("alias") or (variants[-1] if variants else None) or s.get("name")
    return {"id": author_id, "name": s.get("name") or author_id, "discriminator": "0000", "nickname": nick,
            "color": None, "isBot": bool(s.get("is_bot")), "roles": [], "avatarUrl": None}


def _iso(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _author(authors: dict, author_id, name, display, is_bot) -> dict:
    known = authors.get(author_id)
    if known:
        return dict(known)
    return {"id": author_id, "name": name or author_id, "discriminator": "0000", "nickname": display or name,
            "color": None, "isBot": is_bot, "roles": [], "avatarUrl": None}


def _message(m: dict, authors: dict) -> dict:
    flags = set(m.get("flags") or [])
    content = m.get("raw_content") if m.get("raw_content") is not None else m.get("text", "")
    mtype = "Reply" if m.get("reply_to") else "Default"
    if "system" in flags:
        mtype = SYSTEM_TYPES.get(content, "Default")
    ts = _iso(m["ts"])
    out = {
        "id": str(m["id"]), "type": mtype, "timestamp": ts,
        "timestampEdited": ts if m.get("edited") else None,
        "callEndedTimestamp": None, "isPinned": False, "content": content or "",
        "author": _author(authors, str(m["author_id"]), m.get("author"), m.get("author_display"), "bot" in flags),
        "attachments": [{"id": "", "url": "", "fileName": n, "fileSizeBytes": 0} for n in m.get("attachments") or []],
        "embeds": [], "stickers": [],
        "reactions": [{"emoji": {"id": "", "name": e, "code": "", "isAnimated": False, "imageUrl": ""}, "count": n, "users": []}
                      for e, n in (m.get("reactions") or {}).items()],
        "mentions": [_author(authors, str(i), str(i), None, False) for i in m.get("mentions") or []],
        "inlineEmojis": [],
    }
    if m.get("reply_to"):
        out["reference"] = {"type": "Default", "messageId": str(m["reply_to"]), "channelId": m["channel_id"],
                            "guildId": m.get("guild_id")}
    return out


def jsonl_to_dce(lines: Iterable[str], authors: dict, guilds: Optional[dict] = None,
                 thread_parent: Optional[Callable[[str], Optional[dict]]] = None,
                 skip_channels: Iterable[str] = ()) -> list[dict]:
    """Group a messages.jsonl dump by channel and return one DCE document per channel.

    authors: {id: DCE author object}, used so converted messages keep avatars, nicknames and roles.
    guilds: {guild_id: DCE guild object}.
    thread_parent(channel_id) -> DCE channel object of the parent, if that channel is a thread.
    """
    guilds = guilds or {}
    skip = set(skip_channels)
    by_channel: dict[str, list[dict]] = {}
    for line in lines:
        if not line.strip():
            continue
        m = json.loads(line)
        cid = str(m["channel_id"])
        if cid not in skip:
            by_channel.setdefault(cid, []).append(m)
    docs = []
    for cid, msgs in by_channel.items():
        first = msgs[0]
        gid = first.get("guild_id")
        guild = guilds.get(gid) or {"id": gid, "name": "Unknown server", "iconUrl": None}
        parent = thread_parent(cid) if thread_parent else None
        channel = {
            "id": cid, "type": "GuildPublicThread" if parent else "GuildTextChat",
            "categoryId": parent["id"] if parent else None, "category": parent["name"] if parent else None,
            "name": first.get("channel_name") or cid, "topic": None,
        }
        msgs.sort(key=lambda m: (m["ts"], int(m["id"])))
        docs.append({"guild": guild, "channel": channel, "dateRange": {"after": None, "before": None},
                     "exportedAt": datetime.now(timezone.utc).isoformat(),
                     "messages": [_message(m, authors) for m in msgs], "messageCount": len(msgs)})
    return docs


def index_dce(docs: Iterable[dict]) -> dict:
    """Who's who in some DCE documents: authors (newest avatar wins), guilds, channels, thread parents."""
    idx = {"authors": {}, "guilds": {}, "channels": {}, "thread_parent": {}}
    for doc in docs:
        g, c = doc["guild"], doc["channel"]
        idx["guilds"][g["id"]] = g
        idx["channels"][c["id"]] = c
        for m in doc["messages"]:
            for a in [m["author"], *m.get("mentions", [])]:
                idx["authors"][a["id"]] = a
            if m.get("type") == "ThreadCreated":
                # Discord gives a thread the same id as the message that started it.
                idx["thread_parent"][(m.get("reference") or {}).get("channelId") or m["id"]] = c
    return idx
