"""Approved conversations → training examples, replayed once per person ("rotation").

See Documentation/Curator/training.md sections 1 and 2. The format is the one the bot sends
at runtime (apps/engine/controller/wrappers/openai.py), so the adapter fires on real prompts:

    system:    the character's personality, from the NAS like the bot (config/constants.py if it's down)
    user:      "Name: text" lines for everyone else, back-to-back ones joined
    assistant: the replayed person's replies, with their name in others' messages changed to Ohm

A replayed reply only teaches what the bot can actually do (training.md, section 1):
- no messages that are a link, gif or attachment: the model can't share real ones and would make them up
- only replies to something said since the person's last turn, with that message moved to the end, because the
  bot answers the last line it sees (a topic start, or a reply to something older, teaches it to ignore you)
- no leading @mention of the person being answered: the bot replies with a Discord reply, which pings them
- at most MAX_REPLY_LINES lines, and none that look like another speaker's "Name:" line
- a reply said over REPEAT_CAP times ("bruh") kept about REPEAT_CAP times, so it's no stock answer
- a silence over split_gap_ms starts over: what comes after it only sees what it replies to
"""

import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import requests

from . import conversations, threads
from .config import MEMORY_API_KEY, MEMORY_API_URL, REPO_ROOT

CHARACTER = "ohm"
TARGET_NAME = "Ohm"
SHORT_REPLIES = {"lol", "lmao", "lmfao", "ok", "okay", "k", "kk", "ya", "ye", "yea", "yeah", "yep", "fr", "true",
                 "same", "nah", "no", "yes", "mhm", "💀", "😂"}
KEEP_SHORT = 0.1         # share of short replies kept as targets, so the model still says "lol" sometimes
REPEAT_CAP = 5           # times a common reply ("bruh") is trained at most
MAX_REPLY_CHARS = 600    # longer replies are pastes or rants, not chat
MAX_REPLY_LINES = 2      # longer bursts teach run-on turns that never hand back
OVERLAP_PAIRS = 2        # user/assistant pairs repeated at the start of the next chunk
CHARS_PER_TOKEN = 3      # conservative for chat text; the worker still drops anything over max_len
DEFAULT_MAX_LEN = 2048
URL = re.compile(r"https?://\S+")
LINK_ONLY = re.compile(r"^\s*(?:<?https?://\S+>?\s*)+$")
GIF_SITES = re.compile(r"tenor\.com|giphy\.com|\.gif\b", re.IGNORECASE)


def _seen_as(text: str) -> str:
    """A lone link or gif as a note, since Ohm can't open it"""
    if LINK_ONLY.match(text or ""):
        return "(sent a gif)" if GIF_SITES.search(text) else "(sent a link)"
    return text


def _unseeable(m) -> bool:
    """Blank (sticker, attachment) or only a link"""
    text = (m["content_text"] or "").strip()
    return not text or bool(LINK_ONLY.match(text))


def system_prompt(character: str = CHARACTER) -> str:
    """The same string the bot sends (OpenAIModel uses personality.strip()).

    The bot reads the personality from the NAS and only falls back to config/constants.py, so this does too:
    a personality edited on the Characters page has to be the one the adapter is trained under.
    """
    if MEMORY_API_URL:
        try:
            r = requests.get(f"{MEMORY_API_URL}/characters/{character}", headers={"X-API-Key": MEMORY_API_KEY},
                             timeout=(2, 5))
            if r.ok and (r.json() or {}).get("personality", "").strip():
                return r.json()["personality"].strip()
        except (requests.RequestException, ValueError):
            pass
    engine = str(REPO_ROOT / "apps" / "engine")  # where the bot's config package lives
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from config.constants import models

    return models.get(character, models[CHARACTER])["personality"].strip()


def _media(m) -> bool:
    """A link, gif or attachment. The model can't see or send real ones."""
    return bool(URL.search(m["content_text"]) or json.loads(m["attachments"] or "[]"))


def _unping(names: list[str]):
    """Removes @mentions of these people from the start of a line ("@Lenk @lenk yo" -> "yo")."""
    names = sorted({n for n in names if n}, key=len, reverse=True)
    if not names:
        return lambda s: s
    rx = re.compile(r"^\s*(?:@(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w.])[\s,:]*)+", re.IGNORECASE)
    return lambda s: rx.sub("", s)


def _segments(msgs: list, gap_ms: int) -> list[list]:
    """Split a conversation's messages wherever nobody spoke for more than gap_ms."""
    out: list[list] = []
    for m in msgs:
        if not out or m["ts"] - out[-1][-1]["ts"] > gap_ms:
            out.append([])
        out[-1].append(m)
    return out


def said_as(text: str) -> str:
    """Normalized reply text for REPEAT_CAP"""
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


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


def reply_key(ids: list[str]) -> str:
    """How a trained Ohm turn is known in reply_checks: its message ids, in order."""
    return ",".join(ids)


def _render(lines: list[tuple[str, dict]], people: dict, repl) -> str:
    """Other people's messages as "Name: text", one label per run of the same person."""
    out: list[str] = []
    prev = None
    for author, m in lines:
        if not (m["content_text"] or "").strip():
            continue  # sticker or attachment alone
        text = repl(_seen_as(m["content_text"]))
        out.append(text if author == prev else f"{people[author]['label']}: {text}")
        prev = author
    return "\n".join(out)


def _answered_last(heard: list[tuple[str, dict]], parent: str) -> list[tuple[str, dict]]:
    """`heard` with the message `parent` (and the rest of its block) moved to the end.

    At runtime the bot answers the message that pinged it, which is always the last line it sees.
    A reply to something a few lines up is laid out the same way, so the model learns to answer
    the last line instead of saying whatever fits the chat.
    """
    i = next(k for k, (_, m) in enumerate(heard) if m["id"] == parent)
    j = i + 1
    while j < len(heard) and heard[j][1]["block_id"] == heard[i][1]["block_id"]:
        j += 1
    return heard[:i] + heard[j:] + heard[i:j]


def _rotation(conv: dict, runs: list[dict], target: str, people: dict, attached: dict, system: str,
              budget: int, dropped: dict, parents: dict, labels: re.Pattern, segment: int = 0,
              checks: Optional[dict] = None, repeats: Optional[dict] = None,
              personal: Optional[set] = None) -> list[dict]:
    """One conversation replayed as `target`: their replies become Ohm's turns.

    parents: {message id: id of the message its reply arrow points to, None for a topic start}.
    A reply is only a target when what it answers is in the context since target's last turn.
    That message is moved to the end, so every Ohm turn answers the last line before it.
    labels: matches a line that starts like another speaker's "Name:" line.
    checks: {reply_key: answers} from the judge (train/checks.py); a turn it says doesn't answer is left out.
    """
    repl = _replacer(people[target]["names"])
    turns: list[dict] = []  # user turns hold "lines": [(author id, message)], rendered at the end

    for r in runs:
        if r.get("context") and r["author"] == target:
            continue  # their own earlier words: Ohm's line from before, not something to answer
        if r["author"] != target:
            if not turns or turns[-1]["role"] != "user":
                turns.append({"role": "user", "lines": [], "ids": []})
            turns[-1]["lines"] += [(r["author"], m) for m in r["msgs"]]
            continue
        ok = [m for m in r["msgs"] if attached.get(m["id"])]
        dropped["unattached"] += len(r["msgs"]) - len(ok)
        media = [m for m in ok if _media(m)]
        dropped["links or media"] += len(media)
        ok = [m for m in ok if not _media(m)]
        if not ok:
            continue
        heard = turns[-1]["lines"] if turns and turns[-1]["role"] == "user" else []
        parent = parents.get(ok[0]["id"])
        said = {m["id"]: a for a, m in heard}
        if parent is None:
            dropped["starts a topic"] += len(ok)  # the bot only ever answers
            continue
        if parent not in said:
            dropped["answers something older"] += len(ok)  # its own earlier line, or before its last turn
            continue
        # Later lines of the run that answer something else (a split line) would be off topic
        mine = {m["id"] for m in ok}
        on = [m for m in ok if parents.get(m["id"]) == parent or parents.get(m["id"]) in mine]
        dropped["answers something older"] += len(ok) - len(on)
        ok = on
        if _unseeable(next(m for _, m in heard if m["id"] == parent)):
            dropped["answers a link or picture"] += len(ok)  # he can't see it
            continue
        who = said[parent]
        names = people[who]["names"] + [people[who]["label"]] if who in people else []
        lines = []
        for m in ok:
            line = _unping(names)(m["content_text"]).strip()
            if line:
                lines.append(line)
        content = repl("\n".join(lines)).strip()
        if not content:
            continue
        if not re.search(r"\w", content):
            dropped["no words"] += 1  # "-_-", "??", emoji alone
            continue
        norm = content.lower().strip(" .!?")
        if (norm in SHORT_REPLIES or len(norm) < 2) and _frac(ok[0]["id"]) >= KEEP_SHORT:
            dropped["short"] += 1
            continue
        times = (repeats or {}).get(said_as(content), 0)
        if times > REPEAT_CAP and _frac(ok[0]["id"], "repeat") >= REPEAT_CAP / times:
            dropped["repeated"] += 1
            continue
        if len(content) > MAX_REPLY_CHARS:
            dropped["too long"] += 1
            continue
        if content.count("\n") + 1 > MAX_REPLY_LINES:
            dropped["too many lines"] += 1
            continue
        if labels.search(content):
            dropped["speaker labels"] += 1
            continue
        key = reply_key([m["id"] for m in ok])
        if (checks or {}).get(key) == 0:
            dropped["doesn't answer (judge)"] += 1
            continue
        if key in (personal or ()):
            dropped["about their own life (judge)"] += 1  # Ohm would claim their life
            continue
        turns[-1]["lines"] = _answered_last(heard, parent)
        turns.append({"role": "assistant", "content": content, "ids": [m["id"] for m in ok]})

    # Every assistant turn follows the user turn holding what it answers, so it never opens a replay
    while turns and turns[-1]["role"] == "user":
        turns.pop()
    if not turns:
        return []
    for t in turns:
        if t["role"] == "user":
            t["content"] = _truncate_left(_render(t["lines"], people, repl), budget // 2)

    chunks = _chunk(turns, budget)
    return [{
        "id": f"conv:{conv['id']}:{segment}:{target}:{k}", "conversation_id": conv["id"], "segment": segment,
        "speaker": people[target]["label"], "speaker_id": target, "chunk": k, "chunks": len(chunks),
        "assistant_turns": sum(t["role"] == "assistant" for t in ch),
        "source_message_ids": [i for t in ch for i in t["ids"]],
        "turn_ids": [t["ids"] for t in ch if t["role"] == "assistant"],
        "messages": [{"role": "system", "content": system}] + [{"role": t["role"], "content": t["content"]} for t in ch],
    } for k, ch in enumerate(chunks)]


def _carried(conn, conv_messages: list, units: list[dict]) -> dict[str, list]:
    """Context for replies whose parent is before `conv_messages`: in an earlier conversation, or before
    a long silence in this one.

    {id of the reply's first kept message: the parent's block up to and including the parent}.
    Without it a reply that opens a conversation has nothing before it and is never trained.
    """
    inside = {m["id"] for m in conv_messages}
    out = {}
    for u in units:
        link = u["link"]
        if not link or not link["parent_id"] or link["parent_id"] in inside:
            continue
        anchor = next((m for m in u["messages"] if not m["excluded"]), None)
        if anchor is None or anchor["id"] not in inside:
            continue
        parent = conn.execute("SELECT * FROM messages WHERE id = ?", (link["parent_id"],)).fetchone()
        if parent is None:
            continue
        ctx = [m for m in threads.block_messages(conn, parent["block_id"])
               if threads.key(m) <= threads.key(parent) and not m["excluded"]]
        if ctx:
            out[anchor["id"]] = ctx
    return out


def _parents(units: list[dict]) -> dict[str, Optional[str]]:
    """{message id: id of the message its unit's reply arrow points to}, None for a topic start."""
    return {m["id"]: u["link"]["parent_id"] for u in units if u["link"] for m in u["messages"]}


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
    system = system_prompt(settings.get("character") or CHARACTER)
    budget = max(1000, max_len * CHARS_PER_TOKEN - len(system))
    gap_ms = settings["split_gap_ms"]
    examples = []
    dropped = {"excluded": 0, "unattached": 0, "starts a topic": 0, "answers something older": 0,
               "doesn't answer (judge)": 0, "about their own life (judge)": 0, "short": 0, "too long": 0, "too many lines": 0, "links or media": 0, "answers a link or picture": 0, "no words": 0,
               "speaker labels": 0, "bots": 0, "repeated": 0, "capped": 0}
    with project.lock:
        convs = [dict(r) for r in conn.execute(
            "SELECT * FROM conversations WHERE approved_at IS NOT NULL AND archived_at IS NULL ORDER BY first_ts"
        ).fetchall()]
        snap = conversations._snapshot(conn, convs)
        checks = {r[0]: r[1] for r in conn.execute("SELECT reply_ids, answers FROM reply_checks")}
        personal = {r[0] for r in conn.execute("SELECT reply_ids FROM reply_checks WHERE personal = 1")}
        asked_both = {r[0] for r in conn.execute("SELECT reply_ids FROM reply_checks WHERE personal IS NOT NULL")}
        people = _people(conn)
        # A reply line that starts like "Lenk:" or "Ohm:" is a transcript, not a message
        names = sorted({n for p in people.values() for n in [p["label"], *p["names"]] if n} | {TARGET_NAME},
                       key=len, reverse=True)
        labels = re.compile(r"(?im)^\s*(?:" + "|".join(re.escape(n) for n in names) + r")\s*:")
        repeats = Counter(said_as(m["content_text"]) for conv in convs for m in snap[conv["id"]]["messages"]
                       if not m["excluded"])
        for conv in convs:
            units = snap[conv["id"]]["units"]
            attached = {m["id"]: u["link"] is not None for u in units for m in u["messages"]}
            parents = _parents(units)
            kept = []
            for m in snap[conv["id"]]["messages"]:
                if m["excluded"]:
                    dropped["excluded"] += 1
                else:
                    kept.append(m)
            for seg, msgs in enumerate(_segments(kept, gap_ms)):
                # After a long silence only what a reply answers comes along, not the whole history
                carried = _carried(conn, msgs, units)
                shown: set[str] = set()
                runs: list[dict] = []
                for m in msgs:
                    # A reply to something earlier gets what it answers right before it, as context only
                    ctx = [c for c in carried.get(m["id"], []) if c["id"] not in shown]
                    if ctx:
                        shown.update(c["id"] for c in ctx)
                        people.setdefault(ctx[0]["author_id"], {"label": ctx[0]["author_id"], "names": [], "bot": False})
                        runs.append({"author": ctx[0]["author_id"], "msgs": ctx, "context": True})
                    if m["author_id"] not in people:
                        people[m["author_id"]] = {"label": m["author_id"], "names": [], "bot": False}
                    if runs and runs[-1]["author"] == m["author_id"] and not runs[-1].get("context"):
                        runs[-1]["msgs"].append(m)
                    else:
                        runs.append({"author": m["author_id"], "msgs": [m]})
                for target in dict.fromkeys(r["author"] for r in runs if not r.get("context")):
                    if people[target]["bot"]:
                        dropped["bots"] += sum(len(r["msgs"]) for r in runs if r["author"] == target and not r.get("context"))
                        continue
                    if speakers and target not in speakers:
                        continue
                    examples += _rotation(conv, runs, target, people, attached, system, budget, dropped,
                                          parents, labels, seg, checks, repeats, personal)

        pre_cap = _shares(examples)
        seed = hashlib.sha256(json.dumps([e["id"] for e in examples]).encode()).hexdigest()
        examples, dropped["capped"] = _cap(examples, settings["author_cap"], seed)
        # Chunks overlap, so a turn can be in two examples; count each once
        keys = {reply_key(ids) for e in examples for ids in e["turn_ids"]}
        tally = {"checked": sum(k in asked_both for k in keys), "unchecked": sum(k not in asked_both for k in keys)}

    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"examples": examples, "jsonl": "\n".join(lines) + ("\n" if lines else ""), "sha256": sha,
            "by_author": pre_cap, "after_cap": _shares(examples), "dropped": dropped,
            "conversations": len(convs), "checked": tally}


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
            "after_cap": data["after_cap"], "dropped": data["dropped"], "sha256": data["sha256"], "checked": data["checked"],
            "sample": [e["messages"] for e in data["examples"][:3]]}


def for_cloud(rows: list[dict], everyone: Optional[list] = None) -> list[dict]:
    """Rows safe to upload: messages and a renumbered conversation id, names (but Ohm's) masked per conversation"""
    import brain

    everyone = [[n for n in p if (n or "").strip().lower() != TARGET_NAME.lower()]
                for p in (brain.privacy.people() if everyone is None else everyone)]
    if not any(everyone):
        raise RuntimeError("No name list is loaded, so names can't be masked; nothing is sent.")
    # Sorted, so held_out() picks the same conversations evaluate.py scores locally
    convs = sorted({r["conversation_id"] for r in rows if r.get("conversation_id") is not None})
    ids = {c: i for i, c in enumerate(convs, 1)}
    masks, out = {}, []
    for r in rows:
        conv = r.get("conversation_id")
        mask = masks.setdefault(conv, brain.privacy.Mask(everyone))
        messages = [{**m, "content": mask.hide(m["content"]) if isinstance(m.get("content"), str) else m.get("content")}
                    for m in r["messages"]]
        out.append({"conversation_id": ids.get(conv), "messages": messages})
    return out
