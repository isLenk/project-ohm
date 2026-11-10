"""Every few minutes of chat, one brain call that sums up what's going on and pulls out what's worth remembering.

Turn.commit() hands each user line to note(). Once a channel has BRIEF_EVERY_MESSAGES lines, or its oldest one
has waited BRIEF_EVERY_S, brief() sends them all in one Task (role "brief": Codex, then llama-server) for:
    a 1-2 sentence summary of the chat right now, which current() puts in the reply prompt for a while, and
    memory notes about the people, stored like remember_if_relevant_async does (deduped, under who they name).
If brain gets no answer, the lines go through the old per-message extract_memory instead.
"""

import threading
import time

import brain

from controller.memory import DUPLICATE_SIMILARITY, find_people, memory

BRIEF_EVERY_MESSAGES = 12  # user lines that make a brief; about a minute of a busy call
BRIEF_EVERY_S = 240        # a quieter chat still gets briefed this long after its first line
BRIEF_FRESH_S = 600        # a summary older than this is old news and stays out of the prompt
TIMER_S = 30               # how often the timer looks for a batch that's waited long enough (a call that ended)
NOTE_MAX = 200             # a longer "note" is the model rambling, not a note

SYSTEM = (
    "You read the last few minutes of a group chat and write two things, as JSON.\n\n"
    "\"summary\": 1-2 sentences on what's going on in the chat right now: the topics, who's doing what, "
    "and the mood. It's for the bot, who is in the chat: what the people are on about, not what the bot said "
    "or did.\n\n"
    "\"notes\": what's worth remembering long-term about the people in it. Each is "
    "{\"about\": the person's name, \"note\": one short third-person note}. A note can be about someone who "
    "spoke or someone they talk about.\n"
    "Worth remembering: names (a real name, a nickname), likes and dislikes, plans, job or school, pets, "
    "relationships, life events, opinions they clearly hold.\n"
    "Not worth remembering: greetings, filler, questions, reactions, insults, jokes, small talk, "
    "or anything about the conversation or the bot itself.\n"
    "Requests to the bot are not facts about the person either: \"explain X\", \"how do I Y\", "
    "\"I want you to...\", \"tell me about...\" are not notes, even if they mention a topic.\n"
    "Each note starts with that person's name as the chat gives it, for example: \"Lenk likes apples.\" or "
    "\"Kyle just started a new job.\" (\"I\", \"me\" and \"my\" are whoever said the line.) "
    "Don't repeat a note, and leave the list empty if nothing is worth keeping."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {
            "type": "object", "properties": {"about": {"type": "string"}, "note": {"type": "string"}},
            "required": ["about", "note"]}},
    },
    "required": ["summary", "notes"],
}

clock = time.monotonic  # tests swap it for a fake one

_pending = {}    # channel id -> {"lines": [(speaker, text, user id)], "since": clock(), "model", "character"}
_summaries = {}  # channel id -> (clock() when written, summary)
_lock = threading.Lock()
_timer = None


def note(channel_id, speaker, text, user_id=None, model=None, character=None):
    """One user line said in `channel_id`. Starts a brief in the background once enough have gathered."""
    if not (text or "").strip():
        return
    with _lock:
        batch = _pending.setdefault(channel_id, {"lines": [], "since": clock()})
        batch["lines"].append((speaker, text, user_id))
        batch["model"], batch["character"] = model, character
    _start_timer()
    check(channel_id)


def check(channel_id=None):
    """Brief every channel (or just `channel_id`) whose lines are due. Returns the channels it started."""
    now, due = clock(), []
    with _lock:
        for cid in [channel_id] if channel_id is not None else list(_pending):
            batch = _pending.get(cid)
            if batch and (len(batch["lines"]) >= BRIEF_EVERY_MESSAGES or now - batch["since"] >= BRIEF_EVERY_S):
                due.append((cid, _pending.pop(cid)))  # ? popped here, so a line arriving mid-brief starts the next batch
    for cid, batch in due:
        memory._background(brief, cid, batch)
    return [cid for cid, _ in due]


def current(channel_id) -> str:
    """The latest summary of what's going on in `channel_id`, or "" when there's none from the last BRIEF_FRESH_S."""
    with _lock:
        at, summary = _summaries.get(channel_id, (0.0, ""))
    return summary if summary and clock() - at < BRIEF_FRESH_S else ""


def brief(channel_id, batch):
    """Sum up `batch` (from note()) and store its memory notes. Falls back to one extract_memory per line."""
    lines, model, character = batch["lines"], batch.get("model"), batch.get("character")
    chat = "\n".join(f"{speaker}: {text}" for speaker, text, _ in lines)
    answer = brain.run(brain.Task("brief", SYSTEM, chat, schema=SCHEMA, max_tokens=400))
    if not isinstance(answer, dict):
        print(f"[brief] no answer for #{channel_id}; remembering its {len(lines)} lines one by one")
        if model is not None and character:
            for speaker, text, user_id in lines:
                memory.remember_if_relevant_async(model, character, speaker, text,
                                                  user_ids=[user_id] if user_id else None)
        return

    summary = " ".join(str(answer.get("summary") or "").split())
    if summary:
        with _lock:
            _summaries[channel_id] = (clock(), summary)
    notes = unique_notes(answer.get("notes"))
    stored = [n for n in notes if store(character, n, lines)]
    print(f"[brief] #{channel_id}: {summary or '(no summary)'} | {len(stored)} of {len(notes)} notes stored")
    memory.log(f"Briefed #{channel_id}: {len(lines)} lines → {len(stored)} notes", source="brief",
               data={"summary": summary, "notes": notes})


def unique_notes(notes) -> list[dict]:
    """The usable notes from a brief, each once (same text, any case or spacing)."""
    out, seen = [], set()
    for n in notes if isinstance(notes, list) else []:
        if not isinstance(n, dict):
            continue
        text = " ".join(str(n.get("note") or "").split()).strip('"').strip()
        key = text.lower().rstrip(".")
        if not text or len(text) > NOTE_MAX or key in seen:
            continue
        seen.add(key)
        out.append({"about": " ".join(str(n.get("about") or "").split()), "note": text})
    return out


def store(character, n: dict, lines) -> bool:
    """Save one note like remember_if_relevant_async does: skipped if it's already known, filed under the
    people it names, or else under whoever in `lines` it's about. True when it was stored."""
    if not character or not memory.enabled or not memory.recall:
        return False
    text = n["note"]
    try:
        existing = memory.search(character, text, limit=5)
        if any(m.get("similarity", 0) >= DUPLICATE_SIMILARITY for m in existing):
            print(f"[memory] already known: {text}")
            return False
        named = [p["user_id"] for p in find_people(text, memory.users())]
        about = n["about"].lower()
        speakers = list(dict.fromkeys(uid for speaker, _, uid in lines if uid and speaker.lower() == about))
        memory.add_memory(character, text, importance=0.6, user_ids=named or speakers or None, kind="fact")
        print(f"[memory] stored: {text}")
        return True
    except Exception as e:
        print(f"[memory] could not store a note from the brief: {e}")
        return False


def _start_timer():
    """The background check that briefs a batch nobody added to for BRIEF_EVERY_S (started once)."""
    global _timer
    with _lock:
        if _timer is not None:
            return

        def loop():
            while True:
                time.sleep(TIMER_S)
                try:
                    check()
                except Exception as e:
                    print(f"[brief] timer check failed: {e}")

        _timer = threading.Thread(target=loop, daemon=True, name="brief-timer")
        _timer.start()
