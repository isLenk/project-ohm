"""Approved conversations → training examples, replayed once per person ("rotation").

See Documentation/Curator/training.md sections 1 and 2. The format is the one the bot sends
at runtime (apps/engine/controller/wrappers/openai.py), so the adapter fires on real prompts:

    system:    Ohm's personality from apps/engine/config/constants.py
    user:      "Name: text" lines for everyone else, back-to-back ones joined
    assistant: the replayed person's replies, with their name in others' messages changed to Ohm
"""

import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Optional

from . import conversations
from .config import REPO_ROOT

CHARACTER = "ohm"
TARGET_NAME = "Ohm"
SHORT_REPLIES = {"lol", "lmao", "lmfao", "ok", "okay", "k", "kk", "ya", "ye", "yea", "yeah", "yep", "fr", "true",
                 "same", "nah", "no", "yes", "mhm", "💀", "😂"}
KEEP_SHORT = 0.1         # share of short replies kept as targets, so the model still says "lol" sometimes
MAX_REPLY_CHARS = 600    # longer replies are pastes or rants, not chat
OVERLAP_PAIRS = 2        # user/assistant pairs repeated at the start of the next chunk
CHARS_PER_TOKEN = 3      # conservative for chat text; the worker still drops anything over max_len
DEFAULT_MAX_LEN = 2048


def system_prompt() -> str:
    """The same string the bot sends (OpenAIModel uses personality.strip())."""
    engine = str(REPO_ROOT / "apps" / "engine")  # where the bot's config package lives
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from config.constants import models

    return models[CHARACTER]["personality"].strip()


def _frac(*parts: str) -> float:
    """Deterministic number in [0, 1), so reruns keep the same short replies."""
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16) / 2**32


def _replacer(names: list[str]):
    """Whole-word, case-insensitive. '@name' keeps its '@'."""
    names = sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True)
    if not names:
        return lambda s: s
    rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.IGNORECASE)
    return lambda s: rx.sub(TARGET_NAME, s)


def _truncate_left(text: str, limit: int) -> str:
    """Keep the end of an over-long context turn; the latest lines matter most."""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    return cut[nl + 1:] if 0 <= nl < len(cut) // 2 else cut


def _chunk(turns: list[dict], budget: int) -> list[list[dict]]:
    """Split alternating user/assistant turns (user first, assistant last) at pair boundaries."""
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
        i = max(j - 2 * OVERLAP_PAIRS, i + 2)
    return out


def _rotation(conv: dict, runs: list[dict], target: str, people: dict, attached: dict, system: str,
              budget: int, dropped: dict) -> list[dict]:
    """One conversation replayed as `target`: their replies become Ohm's turns."""
    repl = _replacer(people[target]["names"])
    turns: list[dict] = []

    def add(role, content, ids):
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + content
            turns[-1]["ids"] += ids
        else:
            turns.append({"role": role, "content": content, "ids": list(ids)})

    for r in runs:
        if r["author"] != target:
            text = repl("\n".join(m["content_text"] for m in r["msgs"]))
            add("user", f"{people[r['author']]['label']}: {text}", [])
            continue
        ok = [m for m in r["msgs"] if attached.get(m["id"])]
        dropped["unattached"] += len(r["msgs"]) - len(ok)
        content = repl("\n".join(m["content_text"] for m in ok)).strip()
        if not content:
            continue
        norm = content.lower().strip(" .!?")
        if (norm in SHORT_REPLIES or len(norm) < 2) and _frac(ok[0]["id"]) >= KEEP_SHORT:
            dropped["short"] += 1
            continue
        if len(content) > MAX_REPLY_CHARS:
            dropped["too long"] += 1
            continue
        add("assistant", content, [m["id"] for m in ok])

    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)  # nothing to reply to yet
    while turns and turns[-1]["role"] == "user":
        turns.pop()
    if not turns:
        return []
    for t in turns:
        if t["role"] == "user":
            t["content"] = _truncate_left(t["content"], budget // 2)

    chunks = _chunk(turns, budget)
    return [{
        "id": f"conv:{conv['id']}:{target}:{k}", "conversation_id": conv["id"],
        "speaker": people[target]["label"], "speaker_id": target, "chunk": k, "chunks": len(chunks),
        "assistant_turns": sum(t["role"] == "assistant" for t in ch),
        "source_message_ids": [i for t in ch for i in t["ids"]],
        "messages": [{"role": "system", "content": system}] + [{"role": t["role"], "content": t["content"]} for t in ch],
    } for k, ch in enumerate(chunks)]


def _people(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT id, name, display_name, is_bot FROM authors"):
        out[r["id"]] = {"label": r["display_name"] or r["name"], "names": [r["name"], r["display_name"]],
                        "bot": bool(r["is_bot"])}
    return out


def build(project, speakers: Optional[list[str]] = None, max_len: int = DEFAULT_MAX_LEN) -> dict:
    """Every example plus the counts the Train modal shows."""
    conn = project.conn
    settings = project.settings()
    system = system_prompt()
    budget = max(1000, max_len * CHARS_PER_TOKEN - len(system))
    examples = []
    dropped = {"excluded": 0, "unattached": 0, "short": 0, "too long": 0, "bots": 0, "capped": 0}
    with project.lock:
        convs = [dict(r) for r in conn.execute(
            "SELECT * FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL ORDER BY first_ts"
        ).fetchall()]
        snap = conversations._snapshot(conn, convs)
        people = _people(conn)
        for conv in convs:
            attached = {m["id"]: u["link"] is not None for u in snap[conv["id"]]["units"] for m in u["messages"]}
            runs: list[dict] = []
            for m in snap[conv["id"]]["messages"]:
                if m["excluded"]:
                    dropped["excluded"] += 1
                    continue
                if m["author_id"] not in people:
                    people[m["author_id"]] = {"label": m["author_id"], "names": [], "bot": False}
                if runs and runs[-1]["author"] == m["author_id"]:
                    runs[-1]["msgs"].append(m)
                else:
                    runs.append({"author": m["author_id"], "msgs": [m]})
            for target in dict.fromkeys(r["author"] for r in runs):
                if people[target]["bot"]:
                    dropped["bots"] += sum(len(r["msgs"]) for r in runs if r["author"] == target)
                    continue
                if speakers and target not in speakers:
                    continue
                examples += _rotation(conv, runs, target, people, attached, system, budget, dropped)

        pre_cap = _shares(examples)
        seed = hashlib.sha256(json.dumps([e["id"] for e in examples]).encode()).hexdigest()
        examples, dropped["capped"] = _cap(examples, settings["author_cap"], seed)

    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"examples": examples, "jsonl": "\n".join(lines) + ("\n" if lines else ""), "sha256": sha,
            "by_author": pre_cap, "after_cap": _shares(examples), "dropped": dropped,
            "conversations": len(convs)}


def _shares(examples) -> list[dict]:
    """Ohm's turns per replayed person: that's what the adapter learns from each of them."""
    counts: dict[str, list] = {}
    for e in examples:
        counts.setdefault(e["speaker_id"], [e["speaker"], 0])[1] += e["assistant_turns"]
    total = sum(c for _, c in counts.values()) or 1
    return sorted(({"author_id": a, "name": n, "count": c, "share": round(c / total, 3)} for a, (n, c) in counts.items()),
                  key=lambda s: -s["count"])


def _cap(examples, cap: float, seed: str) -> tuple[list, int]:
    """Limit anyone to about `cap` of all assistant turns. Returns (kept, examples dropped).

    Finds one turn allowance L with L = cap * sum(min(turns, L)), then keeps a random set of each
    over-allowance person's examples that fits in L. Examples are dropped whole, so it's
    approximate on small data, and everyone keeps at least one example.
    """
    turns: dict[str, int] = {}
    for e in examples:
        turns[e["speaker_id"]] = turns.get(e["speaker_id"], 0) + e["assistant_turns"]
    if cap >= 1 or len(turns) < 2:
        return examples, 0
    cap = max(cap, 1 / len(turns))  # with n people nobody can be under 1/n
    limit = cap * sum(turns.values())
    for _ in range(100):
        nxt = cap * sum(min(v, limit) for v in turns.values())
        if abs(nxt - limit) < 1e-6:
            break
        limit = nxt
    rng = random.Random(seed)
    drop = set()
    for who, total in turns.items():
        if total <= limit:
            continue
        mine = [e for e in examples if e["speaker_id"] == who]
        rng.shuffle(mine)
        kept = 0
        for e in mine:
            if kept + e["assistant_turns"] <= limit:
                kept += e["assistant_turns"]
            else:
                drop.add(id(e))
        if kept == 0:
            drop.discard(id(min(mine, key=lambda e: e["assistant_turns"])))
    out = [e for e in examples if id(e) not in drop]
    return out, len(examples) - len(out)


def write(project, path: Path, **kw) -> dict:
    data = build(project, **kw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data["jsonl"], encoding="utf-8")
    return {"path": str(path), "examples": len(data["examples"]), "sha256": data["sha256"]}


def preview(project, **kw) -> dict:
    data = build(project, **kw)
    return {"examples": len(data["examples"]), "conversations": data["conversations"], "by_author": data["by_author"],
            "after_cap": data["after_cap"], "dropped": data["dropped"], "sha256": data["sha256"],
            "sample": [e["messages"] for e in data["examples"][:3]]}
