"""Approved conversations → training examples. See Documentation/Curator/training.md section 1."""

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Optional

from . import conversations, embeddings
from .threads import block_messages, link_row, split_ids, unit_of

DUPLICATE = 0.95
SYSTEM = "You are {speaker} in a Discord group chat. Reply as {speaker} would."


def _approved_ranges(conn) -> list[tuple[str, int, int]]:
    return [(r["channel_id"], r["first_ts"], r["last_ts"]) for r in conn.execute(
        "SELECT channel_id, first_ts, last_ts FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL"
    )]


def _in_approved(ranges, msg) -> bool:
    return any(c == msg["channel_id"] and a <= msg["ts"] <= b for c, a, b in ranges)


def _unit_text(conn, kind: str, uid: str) -> tuple[str, list[str]]:
    if kind == "message":
        m = conn.execute("SELECT * FROM messages WHERE id = ?", (uid,)).fetchone()
        msgs = [m] if m and not m["excluded"] else []
    else:
        splits = split_ids(conn, uid)
        msgs = [m for m in block_messages(conn, uid) if m["id"] not in splits and not m["excluded"]]
    return "\n".join(m["content_text"] for m in msgs), [m["id"] for m in msgs]


def _name(conn, author_id: str, cache: dict) -> str:
    if author_id not in cache:
        r = conn.execute("SELECT name, display_name FROM authors WHERE id = ?", (author_id,)).fetchone()
        cache[author_id] = (r["display_name"] or r["name"]) if r else author_id
    return cache[author_id]


def _context(conn, link: Optional[dict], ranges, names: dict, strict: bool) -> list[dict]:
    """Walk parents to the thread start, oldest first, skipping excluded units."""
    chain, seen = [], set()
    parent_id = link["parent_id"] if link else None
    while parent_id:
        p = conn.execute("SELECT * FROM messages WHERE id = ?", (parent_id,)).fetchone()
        if not p or (strict and not _in_approved(ranges, p)):
            break
        kind, uid = unit_of(conn, p)
        if (kind, uid) in seen:
            break
        seen.add((kind, uid))
        text, _ = _unit_text(conn, kind, uid)
        if text:
            chain.append({"speaker": _name(conn, p["author_id"], names), "text": text})
        plink = link_row(conn, kind, uid)
        parent_id = plink["parent_id"] if plink else None
    chain.reverse()
    return chain


def build(project, speakers: Optional[list[str]] = None, strict_context: bool = True) -> dict:
    """Every example plus the counts the Train modal shows."""
    conn = project.conn
    settings = project.settings()
    names: dict = {}
    examples, dropped = [], {"unattached": 0, "excluded": 0, "capped": 0, "duplicates": 0, "bots": 0}
    with project.lock:
        ranges = _approved_ranges(conn)
        convs = conn.execute(
            "SELECT * FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL ORDER BY first_ts"
        ).fetchall()
        bots = {r[0] for r in conn.execute("SELECT id FROM authors WHERE is_bot = 1")}
        for conv in convs:
            for u in conversations.units_in(conn, dict(conv)):
                text, ids = _unit_text(conn, u["kind"], u["id"])
                if not text:
                    dropped["excluded"] += 1
                    continue
                if u["author_id"] in bots:
                    dropped["bots"] += 1
                    continue
                link = u["link"]
                if link is None:
                    dropped["unattached"] += 1
                    continue
                if speakers and u["author_id"] not in speakers:
                    continue
                examples.append({
                    "id": f"{'blk' if u['kind'] == 'block' else 'msg'}:{u['id']}",
                    "speaker": _name(conn, u["author_id"], names), "speaker_id": u["author_id"],
                    "context": _context(conn, link, ranges, names, strict_context),
                    "target": text, "conversation_id": conv["id"], "source_message_ids": ids,
                })

        examples, dupes = _dedupe(project, examples)
        dropped["duplicates"] = dupes
        pre_cap = _shares(examples)
        seed = hashlib.sha256(json.dumps([e["id"] for e in examples]).encode()).hexdigest()
        examples, capped = _cap(examples, settings["author_cap"], seed)
        dropped["capped"] = capped

    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"examples": examples, "jsonl": "\n".join(lines) + ("\n" if lines else ""), "sha256": sha,
            "by_author": pre_cap, "after_cap": _shares(examples), "dropped": dropped,
            "conversations": len(convs)}


def _shares(examples) -> list[dict]:
    counts: dict[str, list] = {}
    for e in examples:
        counts.setdefault(e["speaker_id"], [e["speaker"], 0])[1] += 1
    total = len(examples) or 1
    return sorted(({"author_id": a, "name": n, "count": c, "share": round(c / total, 3)} for a, (n, c) in counts.items()),
                  key=lambda s: -s["count"])


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def _dedupe(project, examples) -> tuple[list, int]:
    """Same author saying (almost) the same thing twice: keep the one with more context."""
    keep: dict[tuple[str, str], dict] = {}
    for e in examples:
        k = (e["speaker_id"], _norm(e["target"]))
        if k not in keep or len(e["context"]) > len(keep[k]["context"]):
            keep[k] = e
    out = [e for e in examples if keep.get((e["speaker_id"], _norm(e["target"]))) is e]
    removed = len(examples) - len(out)
    if embeddings.available() and len(out) > 1:
        import numpy as np

        by_author: dict[str, list] = {}
        for e in out:
            by_author.setdefault(e["speaker_id"], []).append(e)
        drop = set()
        for group in by_author.values():
            if len(group) < 2:
                continue
            vecs = embeddings.encode([g["target"] for g in group])
            sims = vecs @ vecs.T
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if sims[i, j] >= DUPLICATE and id(group[i]) not in drop and id(group[j]) not in drop:
                        loser = group[i] if len(group[i]["context"]) < len(group[j]["context"]) else group[j]
                        drop.add(id(loser))
        out = [e for e in out if id(e) not in drop]
        removed += len(drop)
    return out, removed


def _cap(examples, cap: float, seed: str) -> tuple[list, int]:
    """Downsample anyone above `cap` of all examples until they're at it."""
    if not examples or cap >= 1:
        return examples, 0
    rng = random.Random(seed)
    by_author: dict[str, list] = {}
    for e in examples:
        by_author.setdefault(e["speaker_id"], []).append(e)
    if len(by_author) == 1:
        return examples, 0
    # With n authors nobody can be under 1/n, so a 30% cap on 2 people means 50%.
    cap = max(cap, 1 / len(by_author))
    # Shrink the over-cap authors until everyone fits; this converges to the largest valid set.
    sizes = {a: len(v) for a, v in by_author.items()}
    for _ in range(1000):
        total = sum(sizes.values())
        limit = max(1, int(cap * total))
        over = {a: n for a, n in sizes.items() if n > limit}
        if not over:
            break
        for a in over:
            sizes[a] = limit
    keep_ids = set()
    for a, group in by_author.items():
        chosen = group if sizes[a] >= len(group) else rng.sample(group, sizes[a])
        keep_ids.update(id(e) for e in chosen)
    out = [e for e in examples if id(e) in keep_ids]
    return out, len(examples) - len(out)


def chat_messages(example: dict) -> list[dict]:
    """The prompt format the LoRA is trained on (training.md section 2)."""
    context = "\n".join(f"{c['speaker']}: {c['text']}" for c in example["context"])
    return [
        {"role": "system", "content": SYSTEM.format(speaker=example["speaker"])},
        {"role": "user", "content": context or "(start a new topic)"},
        {"role": "assistant", "content": example["target"]},
    ]


def write(project, path: Path, **kw) -> dict:
    data = build(project, **kw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data["jsonl"], encoding="utf-8")
    return {"path": str(path), "examples": len(data["examples"]), "sha256": data["sha256"]}


def preview(project, **kw) -> dict:
    data = build(project, **kw)
    sample = data["examples"][:3]
    return {"examples": len(data["examples"]), "conversations": data["conversations"], "by_author": data["by_author"],
            "after_cap": data["after_cap"], "dropped": data["dropped"], "sha256": data["sha256"],
            "sample": [chat_messages(e) for e in sample]}
