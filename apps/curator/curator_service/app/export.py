"""Approved conversations → training examples. See Documentation/Curator/training.md section 1."""

import hashlib
import json
import random
import re
from bisect import bisect_right
from pathlib import Path
from typing import Optional

from . import conversations, embeddings
from .threads import block_messages, link_row, split_ids

DUPLICATE = 0.95
SYSTEM = "You are {speaker} in a Discord group chat. Reply as {speaker} would."


class _Graph:
    """Messages, units and links for the export, read once instead of per unit.

    Everything in approved conversations comes from one snapshot. Parents outside them
    (only reachable with strict_context off) are read from the database and memoized.
    """

    def __init__(self, conn, convs: list[dict]):
        self.conn = conn
        self.snap = conversations._snapshot(conn, convs)
        self.ranges: dict[str, tuple[list[int], list[int]]] = {}
        for c in sorted(convs, key=lambda c: (c["first_ts"], c["id"])):
            starts, ends = self.ranges.setdefault(c["channel_id"], ([], []))
            starts.append(c["first_ts"])
            ends.append(c["last_ts"])
        self.messages: dict[str, dict] = {}
        self.units: dict[tuple[str, str], tuple[str, list[str]]] = {}
        self.links: dict[tuple[str, str], Optional[dict]] = {}
        self.split: set[str] = set()
        for snap in self.snap.values():
            for m in snap["messages"]:
                self.messages[m["id"]] = m
            for u in snap["units"]:
                k = (u["kind"], u["id"])
                live = [m for m in u["messages"] if not m["excluded"]]
                self.units[k] = ("\n".join(m["content_text"] for m in live), [m["id"] for m in live])
                self.links[k] = u["link"]
                if u["kind"] == "message":
                    self.split.add(u["id"])
        self.snapshotted = set(self.messages)  # their split-line status is already known
        self.names: dict[str, str] = {}

    def in_approved(self, msg) -> bool:
        starts, ends = self.ranges.get(msg["channel_id"], ([], []))
        i = bisect_right(starts, msg["ts"]) - 1
        return i >= 0 and ends[i] >= msg["ts"]

    def message(self, message_id: str):
        if message_id not in self.messages:
            self.messages[message_id] = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self.messages[message_id]

    def unit_of(self, msg) -> tuple[str, str]:
        """A split line is its own unit; anything else speaks for its block."""
        if msg["id"] not in self.snapshotted:
            if self.link("message", msg["id"]):
                self.split.add(msg["id"])
            self.snapshotted.add(msg["id"])
        return ("message", msg["id"]) if msg["id"] in self.split else ("block", str(msg["block_id"]))

    def unit_text(self, kind: str, uid: str) -> tuple[str, list[str]]:
        if (kind, uid) not in self.units:
            self.units[(kind, uid)] = _unit_text(self.conn, kind, uid)
        return self.units[(kind, uid)]

    def link(self, kind: str, uid: str) -> Optional[dict]:
        if (kind, uid) not in self.links:
            self.links[(kind, uid)] = link_row(self.conn, kind, uid)
        return self.links[(kind, uid)]

    def name(self, author_id: str) -> str:
        return _name(self.conn, author_id, self.names)


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


def _context(g: _Graph, link: Optional[dict], strict: bool) -> list[dict]:
    """Walk parents to the thread start, oldest first, skipping excluded units."""
    chain, seen = [], set()
    parent_id = link["parent_id"] if link else None
    while parent_id:
        p = g.message(parent_id)
        if not p or (strict and not g.in_approved(p)):
            break
        kind, uid = g.unit_of(p)
        if (kind, uid) in seen:
            break
        seen.add((kind, uid))
        text, _ = g.unit_text(kind, uid)
        if text:
            chain.append({"speaker": g.name(p["author_id"]), "text": text})
        plink = g.link(kind, uid)
        parent_id = plink["parent_id"] if plink else None
    chain.reverse()
    return chain


def build(project, speakers: Optional[list[str]] = None, strict_context: bool = True) -> dict:
    """Every example plus the counts the Train modal shows."""
    conn = project.conn
    settings = project.settings()
    examples, dropped = [], {"unattached": 0, "excluded": 0, "capped": 0, "duplicates": 0, "bots": 0}
    with project.lock:
        convs = [dict(r) for r in conn.execute(
            "SELECT * FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL ORDER BY first_ts"
        ).fetchall()]
        g = _Graph(conn, convs)
        bots = {r[0] for r in conn.execute("SELECT id FROM authors WHERE is_bot = 1")}
        for conv in convs:
            for u in g.snap[conv["id"]]["units"]:
                text, ids = g.unit_text(u["kind"], u["id"])
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
                    "speaker": g.name(u["author_id"]), "speaker_id": u["author_id"],
                    "context": _context(g, link, strict_context),
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


def _target_vectors(project, group: list[dict]):
    """Normalized vectors for each example's target. A one-message target is exactly that
    message's text, so its stored embedding is reused; only longer targets get encoded."""
    import numpy as np

    single = [e["source_message_ids"][0] for e in group if len(e["source_message_ids"]) == 1]
    stored: dict = {}
    for i in range(0, len(single), 500):
        chunk = single[i : i + 500]
        stored.update(project.conn.execute(
            f"SELECT message_id, vector FROM message_embeddings WHERE message_id IN ({','.join('?' * len(chunk))})", chunk
        ).fetchall())
    vecs: list = [None] * len(group)
    todo = []
    for i, e in enumerate(group):
        blob = stored.get(e["source_message_ids"][0]) if len(e["source_message_ids"]) == 1 else None
        if blob is not None:
            vecs[i] = embeddings.from_blob(blob)
        else:
            todo.append(i)
    if todo:
        for i, v in zip(todo, embeddings.encode([group[i]["target"] for i in todo])):
            vecs[i] = v
    return np.vstack(vecs).astype("float32")


def _near_pairs(vecs, threshold: float, chunk: int = 1024):
    """(i, j) with i < j and similarity >= threshold, in the order a nested i, j loop meets them."""
    import numpy as np

    n = len(vecs)
    for start in range(0, n, chunk):
        sims = vecs[start : start + chunk] @ vecs.T
        rows, cols = np.nonzero(sims >= threshold)
        for r, j in zip(rows.tolist(), cols.tolist()):
            if j > start + r:
                yield start + r, j


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
        by_author: dict[str, list] = {}
        for e in out:
            by_author.setdefault(e["speaker_id"], []).append(e)
        drop = set()
        for group in by_author.values():
            if len(group) < 2:
                continue
            for i, j in _near_pairs(_target_vectors(project, group), DUPLICATE):
                if id(group[i]) not in drop and id(group[j]) not in drop:
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
