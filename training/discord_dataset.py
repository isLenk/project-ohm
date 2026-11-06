#!/usr/bin/env python3
"""
Discord group chat -> Ohm training data.

  import    DiscordChatExporter JSON -> data/messages.jsonl + data/speakers.json
  segment   messages.jsonl           -> data/conversations.json (auto split; keeps reviewed work)
  validate  check conversations.json after edits in the review UI
  build     reviewed conversations   -> out/train.jsonl + out/val.jsonl (group rotation)

messages.jsonl is read-only source data. conversations.json and speakers.json are
what humans (and the review UI) edit. See training/README.md for the formats.
Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_DATA = HERE / "data"
DEFAULT_OUT = HERE / "out"

STATUSES = {"auto", "reviewed", "rejected"}
CONTENT_TYPES = {"Default", "Reply"}
# Messages with these flags are left out of auto segmentation (a reviewer can still drag them in).
SKIP_FLAGS = {"bot", "system", "command", "empty", "link_only"}
# Messages with these flags may be context, but are never trained on as a reply.
NO_TARGET_FLAGS = SKIP_FLAGS | {"attachment_only"}
DEFAULT_SHORT = "lol,lmao,lmfao,ok,okay,k,kk,ya,ye,yea,yeah,yep,fr,true,same,nah,no,yes,mhm,💀,😂"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

URL_RE = re.compile(r"https?://\S+")
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")
CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")
TIMESTAMP_RE = re.compile(r"<t:\d+(?::\w)?>")
LINK_ONLY_RE = re.compile(r"(\[link\]\s*)+")


# ---------------------------------------------------------------- io helpers

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


FRACTION_RE = re.compile(r"\.(\d+)(?=[+-]\d\d:\d\d$|$)")


def parse_ts(s: str) -> datetime:
    # Exports use 1-7 fractional digits; older Pythons only accept 3 or 6.
    s = s.replace("Z", "+00:00")
    s = FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stable_frac(*parts: str) -> float:
    """Deterministic number in [0, 1) from strings, so reruns give the same result."""
    h = hashlib.md5("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16) / 2**32


def paths(data_dir: Path) -> dict[str, Path]:
    return {
        "messages": data_dir / "messages.jsonl",
        "speakers": data_dir / "speakers.json",
        "conversations": data_dir / "conversations.json",
    }


# -------------------------------------------------------------------- import

def iter_export_files(inputs: list[str]):
    for p in map(Path, inputs):
        if p.is_dir():
            yield from sorted(p.glob("*.json"))
        else:
            yield p


def author_names(a: dict) -> list[str]:
    names = [a.get("name"), a.get("nickname"), a.get("displayName"), a.get("globalName")]
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def clean_text(content: str, names_by_id: dict[str, str]) -> str:
    def user(m):
        name = names_by_id.get(m.group(1))
        return f"@{name}" if name else "@someone"

    t = USER_MENTION_RE.sub(user, content or "")
    t = ROLE_MENTION_RE.sub("@role", t)
    t = CHANNEL_MENTION_RE.sub("#channel", t)
    t = CUSTOM_EMOJI_RE.sub(r":\1:", t)
    t = TIMESTAMP_RE.sub("[time]", t)
    t = URL_RE.sub("[link]", t)
    return t.strip()


def convert_message(m: dict, channel: dict, guild: dict, names_by_id, prefixes: str) -> dict:
    a = m.get("author") or {}
    attachments = m.get("attachments") or []
    stickers = m.get("stickers") or []
    text = clean_text(m.get("content", ""), names_by_id)

    flags = []
    if a.get("isBot"):
        flags.append("bot")
    if m.get("type", "Default") not in CONTENT_TYPES:
        flags.append("system")
    if len(text) > 1 and text[0] in prefixes and text[1].isalnum():
        flags.append("command")
    if text and LINK_ONLY_RE.fullmatch(text):
        flags.append("link_only")
    if not text:
        if attachments:
            name = (attachments[0].get("fileName") or "").lower()
            text = "[image]" if name.endswith(IMAGE_EXT) else "[file]"
            flags.append("attachment_only")
        elif stickers:
            text = "[sticker]"
            flags.append("attachment_only")
        else:
            flags.append("empty")

    reactions = {}
    for r in m.get("reactions") or []:
        emoji = (r.get("emoji") or {}).get("name") or "?"
        reactions[emoji] = reactions.get(emoji, 0) + int(r.get("count") or 0)

    return {
        "id": str(m["id"]),
        "channel_id": str(channel.get("id")),
        "channel_name": channel.get("name"),
        "guild_id": str(guild.get("id")) if guild.get("id") else None,
        "ts": to_iso(parse_ts(m["timestamp"])),
        "author_id": str(a.get("id")),
        "author": a.get("name"),
        "author_display": a.get("nickname") or a.get("name"),
        "text": text,
        "raw_content": m.get("content", ""),
        "reply_to": str((m.get("reference") or {}).get("messageId") or "") or None,
        "mentions": [str(x.get("id")) for x in m.get("mentions") or [] if x.get("id")],
        "reactions": reactions,
        "attachments": [x.get("fileName") for x in attachments],
        "edited": bool(m.get("timestampEdited")),
        "flags": flags,
    }


def cmd_import(a) -> int:
    p = paths(Path(a.data_dir))
    files = list(iter_export_files(a.inputs))
    if not files:
        print("no export files found", file=sys.stderr)
        return 1

    exports = []
    names_by_id: dict[str, str] = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        exports.append((f, data))
        for m in data.get("messages", []):
            for person in [m.get("author") or {}] + list(m.get("mentions") or []):
                if person.get("id") and person.get("name"):
                    names_by_id[str(person["id"])] = person["name"]

    existing = {m["id"]: m for m in read_jsonl(p["messages"])}
    added = updated = 0
    for f, data in exports:
        channel, guild = data.get("channel") or {}, data.get("guild") or {}
        for m in data.get("messages", []):
            rec = convert_message(m, channel, guild, names_by_id, a.command_prefixes)
            if rec["id"] in existing:
                updated += 1
            else:
                added += 1
            existing[rec["id"]] = rec
        print(f"read {f.name}: {len(data.get('messages', []))} messages in #{channel.get('name')}")

    msgs = sorted(existing.values(), key=lambda m: (m["channel_id"], m["ts"], int(m["id"])))
    write_jsonl(p["messages"], msgs)

    # speakers.json: keep any alias/variants a human already edited.
    sp_doc = read_json(p["speakers"], {"schema_version": SCHEMA_VERSION, "speakers": {}})
    speakers = sp_doc["speakers"]
    counts = Counter(m["author_id"] for m in msgs)
    for f, data in exports:
        for m in data.get("messages", []):
            au = m.get("author") or {}
            aid = str(au.get("id"))
            s = speakers.setdefault(aid, {"name": au.get("name"), "alias": None, "variants": []})
            s["name"] = au.get("name")
            s["is_bot"] = bool(au.get("isBot"))
            for n in author_names(au):
                if n not in s["variants"]:
                    s["variants"].append(n)
    for aid, s in speakers.items():
        s["message_count"] = counts.get(aid, 0)
    write_json(p["speakers"], sp_doc)

    flagged = Counter(f for m in msgs for f in m["flags"])
    print(f"messages.jsonl: {len(msgs)} total ({added} new, {updated} refreshed)")
    print(f"speakers.json: {len(speakers)} speakers")
    if flagged:
        print("flags:", ", ".join(f"{k}={v}" for k, v in flagged.most_common()))
    return 0


# ------------------------------------------------------------------- segment

def new_conversation(m: dict, reason: str, gap_before) -> dict:
    return {
        "id": f"{m['channel_id']}-{m['id']}",
        "channel_id": m["channel_id"],
        "status": "auto",
        "message_ids": [],
        "tags": [],
        "notes": "",
        "skip_target": [],
        "edits": {},
        "auto": {
            "start_reason": reason,
            "gap_before_min": None if gap_before is None else round(gap_before, 1),
            "reply_bridges": 0,
            "created_at": now_iso(),
        },
        "suggested_links": [],
    }


def compute_links(convs: list[dict], by_id: dict[str, dict]) -> None:
    """Hints for the UI: replies that point into a different conversation (or none)."""
    owner = {mid: c["id"] for c in convs for mid in c["message_ids"]}
    for c in convs:
        links = []
        for mid in c["message_ids"]:
            r = (by_id.get(mid) or {}).get("reply_to")
            if r and owner.get(r) != c["id"]:
                links.append({"message_id": mid, "reply_to": r, "conversation_id": owner.get(r)})
        c["suggested_links"] = links


def conv_sort_key(c: dict, by_id):
    first = by_id.get(c["message_ids"][0]) if c["message_ids"] else None
    return (first["ts"] if first else "", c["id"])


def cmd_segment(a) -> int:
    p = paths(Path(a.data_dir))
    msgs = read_jsonl(p["messages"])
    if not msgs:
        print("no messages; run import first", file=sys.stderr)
        return 1
    by_id = {m["id"]: m for m in msgs}
    doc = read_json(p["conversations"]) or {
        "schema_version": SCHEMA_VERSION, "updated_at": None, "conversations": [], "excluded": []}

    convs = doc["conversations"]
    if a.reset_auto:
        before = len(convs)
        convs = [c for c in convs if c.get("status") != "auto"]
        print(f"reset: discarded {before - len(convs)} auto conversations, kept {len(convs)} reviewed/rejected")

    assigned = {mid for c in convs for mid in c["message_ids"]}
    excluded = set(doc.get("excluded", []))
    taken_ids = {c["id"] for c in convs}

    by_channel = defaultdict(list)
    for m in msgs:
        if m["id"] in assigned or m["id"] in excluded or set(m["flags"]) & SKIP_FLAGS:
            continue
        by_channel[m["channel_id"]].append(m)

    new = []
    for ch, ms in by_channel.items():
        cur, cur_ids, last_ts = None, set(), None
        for m in ms:
            ts = parse_ts(m["ts"])
            gap = None if last_ts is None else (ts - last_ts).total_seconds() / 60
            reason = "first"
            if cur is not None:
                if len(cur["message_ids"]) >= a.max_messages:
                    cur, reason = None, "max_length"
                elif gap <= a.gap_min:
                    pass
                elif m.get("reply_to") in cur_ids and gap <= a.reply_bridge_min:
                    cur["auto"]["reply_bridges"] += 1
                else:
                    cur, reason = None, "gap"
            if cur is None:
                cur = new_conversation(m, reason, gap)
                while cur["id"] in taken_ids:
                    cur["id"] += "x"
                taken_ids.add(cur["id"])
                cur_ids = set()
                new.append(cur)
            cur["message_ids"].append(m["id"])
            cur_ids.add(m["id"])
            last_ts = ts

    convs.extend(new)
    convs.sort(key=lambda c: conv_sort_key(c, by_id))
    compute_links(convs, by_id)
    doc.update(conversations=convs, excluded=sorted(excluded), updated_at=now_iso(),
               schema_version=SCHEMA_VERSION)
    write_json(p["conversations"], doc)

    sizes = sorted(len(c["message_ids"]) for c in new) or [0]
    in_convs = {i for c in convs for i in c["message_ids"]}
    unassigned = sum(1 for m in msgs if m["id"] not in in_convs)
    print(f"segment: {len(new)} new conversations (sizes min {sizes[0]}, "
          f"median {sizes[len(sizes)//2]}, max {sizes[-1]}); total {len(convs)}")
    print(f"unassigned messages (flagged, excluded or not yet placed): {unassigned}")
    return 0


# ------------------------------------------------------------------ validate

def check_conversations(doc: dict, by_id: dict, fix: bool = False) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version is {doc.get('schema_version')}, expected {SCHEMA_VERSION}")
    excluded = set(doc.get("excluded", []))
    seen_conv, owner = set(), {}
    keep = []
    for c in doc.get("conversations", []):
        cid = c.get("id")
        where = f"conversation {cid}"
        if not cid:
            errors.append("conversation without id")
            continue
        if cid in seen_conv:
            errors.append(f"duplicate conversation id {cid}")
        seen_conv.add(cid)
        if c.get("status") not in STATUSES:
            errors.append(f"{where}: status {c.get('status')!r} not in {sorted(STATUSES)}")
        ids = c.get("message_ids", [])
        if not ids:
            warnings.append(f"{where}: empty" + (" (removed)" if fix else ""))
            if fix:
                continue
        for mid in ids:
            if mid not in by_id:
                errors.append(f"{where}: unknown message {mid}")
            if mid in owner:
                errors.append(f"message {mid} is in both {owner[mid]} and {cid}")
            owner[mid] = cid
            if mid in excluded:
                errors.append(f"{where}: message {mid} is also in excluded")
        if len(set(ids)) != len(ids):
            errors.append(f"{where}: repeats a message id")
        for key in ("skip_target",):
            for mid in c.get(key, []):
                if mid not in ids:
                    errors.append(f"{where}: {key} has {mid}, which isn't in the conversation")
        for mid, txt in (c.get("edits") or {}).items():
            if mid not in ids:
                errors.append(f"{where}: edits has {mid}, which isn't in the conversation")
            if not isinstance(txt, str):
                errors.append(f"{where}: edit for {mid} must be a string")
        known = [i for i in ids if i in by_id]
        chans = {by_id[i]["channel_id"] for i in known}
        if len(chans) > 1:
            warnings.append(f"{where}: spans {len(chans)} channels")
        ordered = sorted(known, key=lambda i: (by_id[i]["ts"], int(i)))
        if ordered != known:
            warnings.append(f"{where}: messages not in time order" + (" (sorted)" if fix else ""))
            if fix:
                c["message_ids"] = ordered + [i for i in ids if i not in by_id]
        keep.append(c)
    if fix:
        doc["conversations"] = keep
    return errors, warnings


def cmd_validate(a) -> int:
    p = paths(Path(a.data_dir))
    by_id = {m["id"]: m for m in read_jsonl(p["messages"])}
    doc = read_json(p["conversations"])
    if doc is None:
        print("no conversations.json; run segment first", file=sys.stderr)
        return 1
    errors, warnings = check_conversations(doc, by_id, fix=a.fix)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    if a.fix and not errors:
        compute_links(doc["conversations"], by_id)
        doc["updated_at"] = now_iso()
        write_json(p["conversations"], doc)
        print("fixed and saved")
    st = Counter(c.get("status") for c in doc["conversations"])
    print(f"{len(doc['conversations'])} conversations ({', '.join(f'{k}={v}' for k, v in st.items())}); "
          f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


# --------------------------------------------------------------------- build

def load_system_prompt(a) -> str:
    if a.system_file:
        return Path(a.system_file).read_text(encoding="utf-8")
    sys.path.insert(0, str(REPO_ROOT))
    from config.constants import models  # same string the bot sends at runtime
    return models[a.character]["personality"]


def make_replacer(mapping: dict[str, str]):
    """Whole-word, case-insensitive name replacement. '@name' keeps its '@'."""
    if not mapping:
        return lambda s: s
    keys = sorted(mapping, key=len, reverse=True)
    rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in keys) + r")(?!\w)", re.IGNORECASE)
    lower = {k.lower(): v for k, v in mapping.items()}
    return lambda s: rx.sub(lambda m: lower[m.group(1).lower()], s)


def truncate_left(text: str, limit: int) -> str:
    """Keep the end of an over-long context turn; the latest lines matter most."""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    return cut[nl + 1:] if 0 <= nl < len(cut) // 2 else cut


def chunk_turns(turns: list[dict], budget: int, overlap_pairs: int) -> list[list[dict]]:
    """turns alternate user/assistant, start with user, end with assistant."""
    out, i, n = [], 0, len(turns)
    while i < n:
        j, used = i, 0
        while j + 1 < n:
            pair = len(turns[j]["content"]) + len(turns[j + 1]["content"])
            if used + pair > budget and j > i:
                break
            used += pair
            j += 2
        out.append(turns[i:j])
        if j >= n:
            break
        i = max(j - 2 * overlap_pairs, i + 2)
    return out


def cap_speaker_share(examples: list[dict], cap: float, seed: int) -> tuple[list[dict], dict]:
    """Limit any one speaker to about `cap` of all assistant turns.

    Works out one turn allowance L with L = cap * sum(min(count, L)), then trims each
    speaker above L in a single pass. Examples are dropped whole, so results are
    approximate on small data; every speaker keeps at least one example.
    """
    def counts(exs):
        c = Counter()
        for e in exs:
            c[e["meta"]["target"]] += e["meta"]["assistant_turns"]
        return c

    before = counts(examples)
    if cap >= 1 or len(before) < 2:
        return examples, {"effective_cap": None, "before": dict(before), "after": dict(before)}
    eff = max(cap, 1 / len(before))
    limit = eff * sum(before.values())
    for _ in range(100):
        nxt = eff * sum(min(v, limit) for v in before.values())
        if abs(nxt - limit) < 1e-6:
            break
        limit = nxt

    rng = random.Random(seed)
    dropped = set()
    for s, v in before.items():
        if v <= limit:
            continue
        mine = [k for k, e in enumerate(examples) if e["meta"]["target"] == s]
        rng.shuffle(mine)
        kept = 0
        for k in mine:
            t = examples[k]["meta"]["assistant_turns"]
            if kept + t <= limit:
                kept += t
            else:
                dropped.add(k)
        if kept == 0:  # everything was bigger than the allowance: keep the smallest
            smallest = min(mine, key=lambda k: examples[k]["meta"]["assistant_turns"])
            dropped.discard(smallest)
    examples = [e for k, e in enumerate(examples) if k not in dropped]
    return examples, {"effective_cap": round(eff, 3), "turn_limit": round(limit, 1),
                      "before": dict(before), "after": dict(counts(examples))}


def cmd_build(a) -> int:
    p = paths(Path(a.data_dir))
    msgs = read_jsonl(p["messages"])
    by_id = {m["id"]: m for m in msgs}
    doc = read_json(p["conversations"])
    if doc is None:
        print("no conversations.json; run segment first", file=sys.stderr)
        return 1
    errors, _ = check_conversations(doc, by_id)
    if errors:
        for e in errors[:20]:
            print("error:", e, file=sys.stderr)
        print("conversations.json has errors; run `validate` and fix them first", file=sys.stderr)
        return 1

    speakers = (read_json(p["speakers"]) or {}).get("speakers", {})
    system = load_system_prompt(a)
    statuses = set(a.status.split(","))
    short_set = {s.strip().lower() for s in a.short_replies.split(",") if s.strip()}
    rename = a.rename_target_to

    def label_of(aid: str) -> str:
        s = speakers.get(aid) or {}
        return s.get("alias") or s.get("name") or (by_id_author.get(aid) or "user")

    by_id_author = {m["author_id"]: m["author"] for m in msgs}
    missing = [s.get("name") for aid, s in speakers.items() if not s.get("alias") and not s.get("is_bot")]
    if a.require_aliases and missing:
        print(f"--require-aliases: no alias for {', '.join(map(str, missing))} in speakers.json", file=sys.stderr)
        return 1

    # Names in message text -> alias (only for speakers that have one).
    base_map = {}
    for aid, s in speakers.items():
        if s.get("alias"):
            for v in s.get("variants", []) + [s.get("name")]:
                if v and len(v) >= 3:
                    base_map[v] = s["alias"]

    targets_filter = None
    if a.voice.startswith("person:"):
        want = a.voice.split(":", 1)[1].lower()
        targets_filter = {aid for aid, s in speakers.items()
                          if want in {aid.lower(), str(s.get("name")).lower(), str(s.get("alias")).lower()}
                          or want in {v.lower() for v in s.get("variants", [])}}
        if not targets_filter:
            print(f"no speaker matches {want!r}", file=sys.stderr)
            return 1
    elif a.voice != "group":
        print("--voice must be 'group' or 'person:<name>'", file=sys.stderr)
        return 1

    stats = Counter()
    examples = []
    budget = a.max_chars - len(system)
    for c in doc["conversations"]:
        if c["status"] not in statuses:
            stats[f"conversations_skipped_{c['status']}"] += 1
            continue
        stats["conversations_used"] += 1
        edits = c.get("edits") or {}
        skip = set(c.get("skip_target") or [])
        cm = sorted((by_id[i] for i in c["message_ids"]), key=lambda m: (m["ts"], int(m["id"])))

        # Merge back-to-back messages from the same person into blocks.
        blocks = []
        for m in cm:
            text = edits.get(m["id"], m["text"])
            if not text.strip():
                continue  # an empty edit removes the message from training
            if blocks and blocks[-1]["author"] == m["author_id"]:
                blocks[-1]["msgs"].append((m, text))
            else:
                blocks.append({"author": m["author_id"], "msgs": [(m, text)]})

        participants = Counter(b["author"] for b in blocks)
        for target in participants:
            if targets_filter is not None and target not in targets_filter:
                continue
            if (speakers.get(target) or {}).get("is_bot"):
                continue
            mapping = dict(base_map)
            if rename:
                s = speakers.get(target) or {}
                for v in s.get("variants", []) + [s.get("name"), s.get("alias")]:
                    if v and len(v) >= 3:
                        mapping[v] = rename
            repl = make_replacer(mapping)

            turns: list[dict] = []

            def add(role, content, ids):
                if turns and turns[-1]["role"] == role:
                    turns[-1]["content"] += "\n" + content
                    turns[-1]["ids"] += ids
                else:
                    turns.append({"role": role, "content": content, "ids": list(ids)})

            for b in blocks:
                if b["author"] == target:
                    ok = [(m, t) for m, t in b["msgs"]
                          if m["id"] not in skip and not set(m["flags"]) & NO_TARGET_FLAGS]
                    if len(ok) < len(b["msgs"]):
                        stats["target_msgs_dropped_flag_or_skip"] += len(b["msgs"]) - len(ok)
                    content = repl("\n".join(t for _, t in ok)).strip()
                    if not content:
                        continue
                    norm = content.lower().strip(" .!?")
                    if (norm in short_set or len(norm) < a.min_reply_chars) and \
                            stable_frac(ok[0][0]["id"], str(a.seed)) >= a.keep_short_frac:
                        stats["target_blocks_dropped_short"] += 1
                        continue
                    if len(content) > a.max_reply_chars:
                        stats["target_blocks_dropped_long"] += 1
                        continue
                    add("assistant", content, [m["id"] for m, _ in ok])
                else:
                    text = repl("\n".join(t for _, t in b["msgs"]))
                    content = a.label.format(name=label_of(b["author"]), text=text)
                    add("user", content, [m["id"] for m, _ in b["msgs"]])

            while turns and turns[0]["role"] == "assistant":
                turns.pop(0)
            while turns and turns[-1]["role"] == "user":
                turns.pop()
            n_asst = sum(t["role"] == "assistant" for t in turns)
            if n_asst < a.min_target_turns:
                stats["rotations_skipped_few_turns"] += 1
                continue
            stats["rotations"] += 1
            for t in turns:
                if t["role"] == "user":
                    t["content"] = truncate_left(t["content"], budget // 2)

            chunks = chunk_turns(turns, budget, a.overlap_pairs)
            for k, ch in enumerate(chunks):
                asst_ids = [i for t in ch if t["role"] == "assistant" for i in t["ids"]]
                score = sum(sum(by_id[i]["reactions"].values()) for i in asst_ids)
                ex = {"messages": [{"role": "system", "content": system}] +
                                  [{"role": t["role"], "content": t["content"]} for t in ch]}
                ex["meta"] = {
                    "conv_id": c["id"],
                    "target": target,
                    "target_label": label_of(target),
                    "chunk": k,
                    "n_chunks": len(chunks),
                    "assistant_turns": sum(t["role"] == "assistant" for t in ch),
                    "assistant_message_ids": asst_ids,
                    "score": score,
                    "tags": c.get("tags", []),
                }
                examples.append(ex)

    examples, cap_info = cap_speaker_share(examples, a.max_speaker_share, a.seed)

    train, val = [], []
    for e in examples:
        (val if stable_frac(e["meta"]["conv_id"], str(a.seed)) < a.val else train).append(e)
    if a.no_meta:
        for e in train + val:
            e.pop("meta", None)

    out = Path(a.out_dir)
    write_jsonl(out / "train.jsonl", train)
    write_jsonl(out / "val.jsonl", val)
    label_cap = lambda d: {label_of(k): v for k, v in (d or {}).items()}
    report = {
        "built_at": now_iso(),
        "args": {k: v for k, v in vars(a).items() if k != "func"},
        "stats": dict(stats),
        "examples": {"train": len(train), "val": len(val)},
        "assistant_turns_per_speaker": {
            "effective_cap": cap_info.get("effective_cap"),
            "before_cap": label_cap(cap_info.get("before")),
            "after_cap": label_cap(cap_info.get("after")),
        },
    }
    write_json(out / "build_report.json", report)
    print(json.dumps(report["stats"], indent=2))
    print(f"train: {len(train)}  val: {len(val)}  -> {out}")
    if cap_info.get("after"):
        print("assistant turns per speaker:", report["assistant_turns_per_speaker"]["after_cap"])
    if stats["conversations_used"] == 0:
        print(f"note: no conversations with status in {sorted(statuses)}. "
              "Review some first, or pass --status auto,reviewed to test.")
    return 0


# ----------------------------------------------------------------------- cli

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA))
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="DiscordChatExporter JSON -> messages.jsonl")
    s.add_argument("inputs", nargs="+", help="export .json files or folders of them")
    s.add_argument("--command-prefixes", default="!/", help="first characters that mark a bot command")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("segment", help="split messages into conversations")
    s.add_argument("--gap-min", type=float, default=15, help="silence that starts a new conversation")
    s.add_argument("--reply-bridge-min", type=float, default=60,
                   help="a reply to the current conversation within this many minutes continues it")
    s.add_argument("--max-messages", type=int, default=200, help="hard split for very long conversations")
    s.add_argument("--reset-auto", action="store_true",
                   help="re-split conversations still marked auto (reviewed/rejected are kept)")
    s.set_defaults(func=cmd_segment)

    s = sub.add_parser("validate", help="check conversations.json after review edits")
    s.add_argument("--fix", action="store_true", help="sort messages by time, drop empty conversations")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("build", help="reviewed conversations -> train.jsonl / val.jsonl")
    s.add_argument("--out-dir", default=str(DEFAULT_OUT))
    s.add_argument("--status", default="reviewed", help="comma list of statuses to use")
    s.add_argument("--voice", default="group", help="'group' (rotation) or 'person:<name>'")
    s.add_argument("--character", default="ohm", help="key in config/constants.py models")
    s.add_argument("--system-file", help="use this system prompt instead of config/constants.py")
    s.add_argument("--label", default="{name}: {text}", help="how other speakers' turns are written")
    s.add_argument("--rename-target-to", default="Ohm",
                   help="replace the target speaker's name in others' messages ('' to disable)")
    s.add_argument("--require-aliases", action="store_true",
                   help="fail unless every speaker has an alias in speakers.json")
    s.add_argument("--min-target-turns", type=int, default=2)
    s.add_argument("--short-replies", default=DEFAULT_SHORT)
    s.add_argument("--min-reply-chars", type=int, default=2)
    s.add_argument("--keep-short-frac", type=float, default=0.1, help="share of short replies to keep")
    s.add_argument("--max-reply-chars", type=int, default=600)
    s.add_argument("--max-chars", type=int, default=6000, help="per example, ~1800 tokens")
    s.add_argument("--overlap-pairs", type=int, default=2, help="user/assistant pairs repeated between chunks")
    s.add_argument("--max-speaker-share", type=float, default=0.25)
    s.add_argument("--val", type=float, default=0.08)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--no-meta", action="store_true", help="omit the meta field")
    s.set_defaults(func=cmd_build)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
