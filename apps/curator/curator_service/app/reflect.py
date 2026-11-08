"""Reflection: Ohm looks back over a stretch of conversation and writes down what he'll want later.

The NAS decides when (enough was said and the chat has gone quiet) and queues a job; this polls for it,
claims it, reads the messages, asks the brain's `reflect` role (Codex by default) once, and writes the
answer into the tables the engine already reads: memories to bring up (topic, joke, opinion, question),
how he gets on with each person, small nudges to his personality traits, and a note to himself when
those have drifted. See Documentation/Curator/reflection.md.
"""

import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

import brain
import requests

from . import llm
from .config import MEMORY_API_KEY, MEMORY_API_URL

POLL_MINUTES = 5            # how often the NAS is asked for a queued job
PAGE = 2000                 # messages per GET /messages, the most the NAS gives
GAP_MINUTES = 30            # a pause longer than this gets a time marker in the transcript
MAX_TRANSCRIPT = 60000      # characters of chat sent; a longer stretch keeps its latest part
PERSONALITY_CHARS = 600     # of the character's personality, for who he is
REPLY_TOKENS = 2500
MAX_PER_KIND = 5            # memories of one kind written per run, so one long night doesn't flood them
DEFAULT_IMPORTANCE = 0.6
DUPLICATE = 0.9             # a note this similar to a memory of the same kind isn't stored again (as the engine)
MAX_AFFINITY_STEP = 0.15    # how far one run can move how he feels about someone
NOTES_CHARS = 300           # relationship notes are kept to about this long, the oldest part dropped
TRAIT_STEP = 0.03           # the most a trait moves in one run, when every line showing it landed (or flopped)
MIN_LINES_FOR_TRAIT = 3     # a trait only moves when this many of his lines showed it
SELF_NOTE_MIN_CHANGE = 0.08 # a trait that moved this much over the history window goes in the note to himself
SELF_NOTE_BIG = 0.15        # under this it's "a bit more"
HISTORY_DAYS = 30
TIMEOUT = (3, 20)
KINDS = {"topics": "topic", "jokes": "joke", "opinions": "opinion", "questions": "question"}


def _list(**props) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": props, "required": list(props)}}


TEXT = {"type": "string"}
NUMBER = {"type": "number"}
NAMES = {"type": "array", "items": {"type": "string"}}
SCHEMA = {"type": "object", "properties": {
    "topics": _list(text=TEXT, people=NAMES, importance=NUMBER),
    "jokes": _list(text=TEXT, people=NAMES),
    "opinions": _list(text=TEXT, importance=NUMBER),
    "questions": _list(text=TEXT, people=NAMES),
    "relationships": _list(person=TEXT, affinity_delta=NUMBER, notes=TEXT),
    "ohm_lines": _list(traits=NAMES, reception=NUMBER, quote=TEXT),
    "summary": TEXT,
}, "required": ["topics", "jokes", "opinions", "questions", "relationships", "ohm_lines", "summary"]}

SYSTEM = """You help NAME, an AI who hangs out with a group of friends on Discord (text and voice), look back on a stretch of conversation and decide what he'll remember.

Who NAME is:
PERSONALITY

His personality traits, each from 0 to 1: TRAITS

How he gets on with people he knows (affinity from -1 to 1, then notes):
PEOPLE

Answer with JSON only, in this shape:
{"topics": [{"text": "ask Kyle how the job interview went", "people": ["Kyle"], "importance": 0.7}],
 "jokes": [{"text": "'the blue one': Lance picked the wrong door three times", "people": ["Lance"]}],
 "opinions": [{"text": "NAME thinks Hollow Knight is overrated: the group argued about it for an hour", "importance": 0.5}],
 "questions": [{"text": "what Kyle does for work", "people": ["Kyle"]}],
 "relationships": [{"person": "Lance", "affinity_delta": 0.1, "notes": "calls him grandpa; the door thing"}],
 "ohm_lines": [{"traits": ["sarcastic", "blunt"], "reception": 0.8, "quote": "..."}],
 "summary": "..."}

- topics: things worth bringing up again later: plans, news, something someone is waiting on or working on. importance from 0 to 1: how much the person would care that he remembered.
- jokes: inside jokes and running bits that started or came back here, with enough said to get the joke weeks later.
- opinions: opinions NAME stated or formed, and why, written as "NAME thinks ...".
- questions: things he'd like to find out about someone, to ask another time.
- relationships: one per person things changed with. affinity_delta from -0.15 to 0.15: warmer is positive, cooler is negative, 0 if only the notes changed. notes: nicknames, running jokes, grudges, in a few words.
- ohm_lines: one per line NAME said in this stretch, in order. traits: which of his traits above it showed. reception from 0 to 1: how the group took it. Laughing, replying to it, agreeing or playing along is high; silence, "shut up", being ignored or the subject changing is low. quote: the start of the line.
- summary: one or two sentences on what happened.

Rules:
- Short and concrete, one sentence each.
- Only things that still make sense weeks later to someone who wasn't there. Name the game, the place, the event.
- Nothing about who owes whom money.
- Nothing private like addresses, phone numbers or health, unless the person said it as a plain fact about themself.
- Call people by the names they go by in the chat.
- Empty lists are fine. Most stretches only have a few things worth keeping."""

_state = {"polling": False, "last_poll_at": None, "running": None, "last_result": None}
_poll_lock = threading.Lock()  # one poll (and so one reflection) at a time


# ── the NAS ─────────────────────────────────────────────────────────────────
def _call(method: str, path: str, params: Optional[dict] = None, body=None):
    r = requests.request(method, f"{MEMORY_API_URL}{path}", params=params, json=body,
                         headers={"X-API-Key": MEMORY_API_KEY}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() if r.content else None


def messages(character: str, after_id: int, before_id: int) -> list[dict]:
    """Every message strictly between the two ids, a page at a time."""
    out = []
    while True:
        page = _call("GET", "/messages", {"character": character, "after_id": after_id, "before_id": before_id,
                                          "limit": PAGE}) or []
        out += page
        if len(page) < PAGE:
            return out
        after_id = page[-1]["id"]


# ── the transcript ──────────────────────────────────────────────────────────
def _when(created_at) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(created_at)).astimezone()
    except (TypeError, ValueError):
        return None


def transcript(rows: list[dict], bot: str = "Ohm") -> str:
    """The chat as "Name: text" lines, grouped by channel, with the time at the start of each and after any
    pause longer than GAP_MINUTES."""
    channels = defaultdict(list)  # dicts keep the order each channel first spoke in
    for r in rows:
        channels[(r.get("source"), r.get("channel_id"))].append(r)
    parts = []
    for (source, channel), group in channels.items():
        lines = [f"### {source or 'chat'} channel {channel or '?'}"]
        last = None
        for r in group:
            when = _when(r.get("created_at"))
            if when and (last is None or (when - last).total_seconds() > GAP_MINUTES * 60):
                lines.append(f"({when:%a %b %d, %H:%M})")
            last = when or last
            who = bot if r.get("role") == "assistant" else (r.get("name") or r.get("user_id") or "Someone")
            text = " ".join((r.get("content") or "").split())
            if text:
                lines.append(f"{who}: {text}")
        parts.append("\n".join(lines))
    text = "\n\n".join(parts)
    if len(text) > MAX_TRANSCRIPT:
        text = text[-MAX_TRANSCRIPT:]
        text = text[text.find("\n") + 1:]
    return text


def system_prompt(character: dict, traits: list[dict], relationships: list[dict]) -> str:
    name = character.get("name") or "Ohm"
    personality = " ".join((character.get("personality") or "").split())[:PERSONALITY_CHARS] or "(not written down)"
    trait_line = ", ".join(f"{t['trait']} {float(t['value']):.2f}" for t in traits) or "(none yet)"
    people = "\n".join(f"- {r.get('name')}: {float(r.get('affinity') or 0):+.2f}" + (f", {r['notes']}" if r.get("notes") else "")
                       for r in relationships) or "(nobody yet)"
    # ? NAME first, so a personality that happens to say NAME isn't changed
    return (SYSTEM.replace("NAME", name).replace("PERSONALITY", personality).replace("TRAITS", trait_line)
            .replace("PEOPLE", people))


# ── people ──────────────────────────────────────────────────────────────────
def name_index(users: list[dict], rows: list[dict]) -> dict:
    """lowercased name -> user id. The names in the chat itself come first, then every name in GET /users
    (preferred, display, username, aliases), like the engine's person_names. The first one seen wins."""
    index = {}
    for r in rows:
        if r.get("role") == "user" and r.get("name") and r.get("user_id"):
            index.setdefault(r["name"].strip().lower(), str(r["user_id"]))
    for u in users:
        for n in [u.get("name"), u.get("preferred_name"), u.get("display_name"), u.get("username"), *(u.get("aliases") or [])]:
            n = " ".join((n or "").split()).lower()
            if n:
                index.setdefault(n, str(u["user_id"]))
    return index


def resolve(names, index: dict) -> list[str]:
    """The user ids of the names the model gave, without repeats; a name nobody goes by is left out."""
    out = []
    for n in names or []:
        uid = index.get(" ".join(str(n).split()).lower().lstrip("@"))
        if uid and uid not in out:
            out.append(uid)
    return out


# ── writing it down ─────────────────────────────────────────────────────────
def _importance(value) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE


def _duplicate(character: str, text: str, kind: str) -> bool:
    found = _call("POST", "/memories/search", body={"character": character, "text": text, "limit": 5}) or []
    return any(m.get("kind") == kind and (m.get("similarity") or 0) >= DUPLICATE for m in found)


def write_notes(character: str, answer: dict, index: dict) -> dict:
    """topics, jokes, opinions and questions as memories of that kind, up to MAX_PER_KIND each. {kind: written}"""
    written = {}
    for key, kind in KINDS.items():
        written[kind] = 0
        for note in answer.get(key) or []:
            if written[kind] >= MAX_PER_KIND:
                break
            text = " ".join(str((note or {}).get("text") or "").split())
            if not text or _duplicate(character, text, kind):
                continue
            _call("POST", "/memories", body={"character": character, "content": text, "kind": kind,
                                             "importance": _importance(note.get("importance", DEFAULT_IMPORTANCE)),
                                             "user_ids": resolve(note.get("people"), index)})
            written[kind] += 1
    return written


def merge_notes(old: Optional[str], new: list[str]) -> str:
    """The old notes with the new ones added, the oldest part dropped past NOTES_CHARS."""
    parts = [p.strip() for p in (old or "").split(";") if p.strip()]
    for n in new:
        n = " ".join(n.split())
        if n and n.lower() not in (p.lower() for p in parts):
            parts.append(n)
    while len(parts) > 1 and len("; ".join(parts)) > NOTES_CHARS:
        parts.pop(0)
    return "; ".join(parts)[-NOTES_CHARS:]


def update_relationships(character: str, changes: list, index: dict, current: list[dict]) -> int:
    """Affinity moved by at most MAX_AFFINITY_STEP per person per run, notes merged. Returns how many changed."""
    deltas, notes = defaultdict(float), defaultdict(list)
    for c in changes or []:
        uid = (resolve([(c or {}).get("person")], index) or [None])[0]
        if not uid:
            continue
        try:
            deltas[uid] += float(c.get("affinity_delta") or 0)
        except (TypeError, ValueError):
            deltas[uid] += 0
        if (c.get("notes") or "").strip():
            notes[uid] += [s for s in str(c["notes"]).split(";")]
    old = {str(r.get("user_id")): r.get("notes") for r in current}
    changed = 0
    for uid, delta in deltas.items():
        delta = round(min(max(delta, -MAX_AFFINITY_STEP), MAX_AFFINITY_STEP), 3)
        merged = merge_notes(old.get(uid), notes[uid])
        body = {}
        if delta:
            body["affinity_delta"] = delta
        if merged != (old.get(uid) or ""):
            body["notes"] = merged
        if body:
            _call("PATCH", f"/relationships/{character}/{uid}", body=body)
            changed += 1
    return changed


def trait_moves(lines: list, traits: list[dict]) -> list[dict]:
    """[{trait, from, to, reason}] for each trait shown in at least MIN_LINES_FOR_TRAIT of his lines: up when the
    lines went down well (mean reception over 0.5), down when they didn't, by at most TRAIT_STEP."""
    receptions = defaultdict(list)
    for line in lines or []:
        try:
            r = min(max(float((line or {}).get("reception")), 0.0), 1.0)
        except (TypeError, ValueError):
            continue
        for t in {str(t).strip().lower() for t in line.get("traits") or []}:
            receptions[t].append(r)
    out = []
    for t in traits:
        got = receptions.get(t["trait"].lower(), [])
        if len(got) < MIN_LINES_FOR_TRAIT:
            continue
        mean = sum(got) / len(got)
        value = float(t["value"])
        to = value + TRAIT_STEP * (mean - 0.5) * 2
        to = min(max(to, float(t.get("floor", 0.0))), float(t.get("ceiling", 1.0)))
        out.append({"trait": t["trait"], "from": round(value, 3), "to": round(to, 3),
                    "reason": f"{len(got)} {t['trait']} lines, reception {mean:.2f}"})
    return out


def self_note(traits: list[dict], history: list[dict], moves: list[dict]) -> Optional[str]:
    """A note to himself on how he's changed, when a trait moved more than SELF_NOTE_MIN_CHANGE since the oldest
    value in the history (or since before this run, for a trait with no history). None when nothing did."""
    oldest = {}
    for h in history:  # oldest first
        oldest.setdefault(h["trait"], float(h["value"]))
    now = {t["trait"]: float(t["value"]) for t in traits}
    now.update({m["trait"]: m["to"] for m in moves})
    parts = []
    for t in traits:
        name = t["trait"]
        change = now[name] - oldest.get(name, float(t["value"]))
        if abs(change) > SELF_NOTE_MIN_CHANGE:
            parts.append(("" if abs(change) >= SELF_NOTE_BIG else "a bit ") + ("more " if change > 0 else "less ") + name)
    if not parts:
        return None
    said = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Lately I've been getting {said} than a month ago."


def write_self_note(character: str, text: str) -> None:
    """Replaces his previous note to himself."""
    for m in _call("GET", "/memories", {"character": character, "kind": "self"}) or []:
        _call("DELETE", f"/memories/{m['id']}")
    _call("POST", "/memories", body={"character": character, "content": text, "kind": "self",
                                     "importance": DEFAULT_IMPORTANCE, "user_ids": []})


# ── the job ─────────────────────────────────────────────────────────────────
def reflect(job: dict) -> dict:
    """Reflect on one claimed job (the claim's row, with after_id/before_id). Returns the result for /finish."""
    started = time.perf_counter()
    character = job["character"]
    rows = messages(character, job["after_id"], job["before_id"])
    written = {kind: 0 for kind in KINDS.values()} | {"self": 0}
    if not rows:
        return {"summary": "Nothing to reflect on.", "written": written, "relationships": 0, "traits": [],
                "backend": None, "ms": round((time.perf_counter() - started) * 1000)}
    users = _call("GET", "/users") or []
    relationships = _call("GET", "/relationships", {"character": character}) or []
    saved = _call("GET", "/traits", {"character": character, "days": HISTORY_DAYS}) or {}
    traits, history = saved.get("traits") or [], saved.get("history") or []
    who = _call("GET", f"/characters/{character}") or {}
    bot = who.get("name") or "Ohm"

    user = (f'The conversation. Lines are "Name: text", {bot}\'s own are "{bot}: text", and the time is shown '
            f"after a pause.\n\n{transcript(rows, bot)}")
    answer = brain.run(brain.Task(role="reflect", system=system_prompt(who, traits, relationships), user=user,
                                  schema=SCHEMA, max_tokens=REPLY_TOKENS, reasoning="high"))
    if answer is None:
        raise RuntimeError("No brain backend answered the reflection (see [brain] in the Curator log).")

    index = name_index(users, rows)
    index.pop(bot.lower(), None)  # notes about himself aren't about a person
    written.update(write_notes(character, answer, index))
    changed = update_relationships(character, answer.get("relationships"), index, relationships)
    moves = trait_moves(answer.get("ohm_lines"), traits)
    if moves:
        _call("PUT", f"/traits/{character}", body={"traits": [{"trait": m["trait"], "value": m["to"],
                                                               "reason": m["reason"]} for m in moves]})
    note = self_note(traits, history, moves)
    if note:
        write_self_note(character, note)
        written["self"] = 1
    return {"summary": str(answer.get("summary") or "").strip(), "written": written, "relationships": changed,
            "traits": [{"trait": m["trait"], "from": m["from"], "to": m["to"]} for m in moves],
            "backend": llm.brain_backend("reflect"), "ms": round((time.perf_counter() - started) * 1000)}


def run_job(job: dict) -> None:
    """reflect() and tell the NAS how it went. Never raises."""
    try:
        result, error = reflect(job), None
    except Exception as e:
        result, error = None, str(e) or type(e).__name__
        print(f"[reflect] reflection {job.get('id')} for {job.get('character')} failed: {error}")
    try:
        _call("POST", f"/reflections/{job['id']}/finish", body={"result": result} if error is None else {"error": error})
    except requests.RequestException as e:
        print(f"[reflect] could not finish reflection {job.get('id')}: {e}")
    _state["last_result"] = {"id": job.get("id"), "character": job.get("character"), "at": int(time.time() * 1000),
                             "result": result, "error": error}
    if error is None:
        print(f"[reflect] reflection {job['id']} for {job['character']}: {result['summary']}")


def poll_once() -> Optional[dict]:
    """Run the oldest queued reflection, if there is one. Returns the claimed job, or None."""
    if not MEMORY_API_URL or not _poll_lock.acquire(blocking=False):
        return None
    try:
        _state["last_poll_at"] = int(time.time() * 1000)
        due = _call("GET", "/reflections/due")
        if not due:
            return None
        try:
            job = _call("POST", f"/reflections/{due['id']}/claim")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                return None  # someone else took it
            raise
        _state["running"] = job["id"]
        run_job(job)
        return job
    except requests.RequestException as e:
        print(f"[reflect] couldn't reach the memory service: {e}")
        return None
    finally:
        _state["running"] = None
        _poll_lock.release()


def _loop() -> None:
    while True:
        try:
            poll_once()
        except Exception as e:  # a bad poll waits for the next one
            print(f"[reflect] poll failed: {e!r}")
        time.sleep(POLL_MINUTES * 60)


def start() -> None:
    """The poller, on service start. Nothing to poll without the NAS."""
    if not MEMORY_API_URL or _state["polling"]:
        return
    _state["polling"] = True
    threading.Thread(target=_loop, daemon=True, name="reflect").start()


def poll_soon() -> None:
    """One poll now, in the background (the app's button)."""
    threading.Thread(target=poll_once, daemon=True, name="reflect-now").start()


def status() -> dict:
    return dict(_state)
