"""Client for the memory service running on the NAS (see memory_service/).

Configured through .env:
    MEMORY_API_URL=http://<nas-ip>:8100
    MEMORY_API_KEY=<same API_KEY as memory_service/.env>

Every call fails soft: if the NAS is down or not configured, searches return []
and writes are dropped, so the bot keeps talking (just without memories).
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import brain
import requests
from dotenv import load_dotenv

from controller import modules
from controller.adapters import ui_model

load_dotenv()

TIMEOUT = (2, 5)  # (connect, read) seconds
RETRY_AFTER = 30  # seconds to wait before trying the NAS again after a failure

MIN_SIMILARITY = 0.35   # memories less similar than this to what was said are left out of the prompt
DUPLICATE_SIMILARITY = 0.9  # a new note this close to an existing memory is not stored again
MIN_WORDS = 3           # "okay", "what?", "um yeah" are never worth searching or remembering
HISTORY_MINUTES = 30    # how far back the conversation history goes
HISTORY_LIMIT = 20      # max messages of conversation history per reply
GLOSSARY_TTL = 300      # seconds before the Curator's terms are fetched again
GLOSSARY_LIMIT = 12     # at most this many term lines per prompt
GLOSSARY_WORD = re.compile(r"[a-z][a-z']*[a-z]|[a-z]")
PEOPLE_TTL = 300        # seconds before everyone's names are fetched again (so an alias added in the app shows up)
PEOPLE_LIMIT = 3        # at most this many named people get their notes in one prompt
PERSON_NOTES = 5        # notes per named person
MIN_NAME = 3            # shorter Discord names aren't matched in text ("Al", "j"); an alias set in the app can be 2
RELATIONSHIPS_TTL = 300 # seconds before how he gets on with everyone is fetched again
GET_ON_WELL = 0.4       # affinity (-1..1) at or above this: "you get on well with them"
NOT_A_FAN = -0.4        # at or below this: "you're not their biggest fan"; in between says nothing


def person_names(user: dict) -> list[str]:
    """Every name `user` (a GET /users row) goes by, the one Ohm calls them first, without repeats."""
    names = []
    for key, n in [("name", user.get("name")), ("preferred_name", user.get("preferred_name")),
                   ("display_name", user.get("display_name")), ("username", user.get("username")),
                   *(("alias", a) for a in user.get("aliases") or [])]:
        n = " ".join((n or "").split())
        if not n or n == user.get("user_id") or n.lower() in (o.lower() for o in names):
            continue
        if len(n) < (2 if key == "alias" else MIN_NAME):
            continue
        names.append(n)
    return names


def find_people(text: str, users: list[dict], mentioned: Optional[dict] = None) -> list[dict]:
    """The people `text` names, in the order they come up: [{user_id, name, also: [other names]}].
    A name matches as a whole word, any case ("lenk's" names Lenk, "lenkster" doesn't). `mentioned`
    ({user id: name}) are people known to be meant already (Discord @mentions); they come first, and are
    included even if `users` doesn't have them yet."""
    users = list(users)
    mentioned = {str(k): v for k, v in (mentioned or {}).items()}
    known = {str(u.get("user_id")) for u in users}
    users += [{"user_id": k, "name": v} for k, v in mentioned.items() if k not in known]
    low = (text or "").lower()
    found = {}  # user id -> position of the first mention
    for u in users:
        uid = str(u.get("user_id"))
        if uid in mentioned:
            found[uid] = -1
            continue
        for n in person_names(u):
            m = re.search(r"(?<!\w)" + re.escape(n.lower()) + r"(?!\w)", low)
            if m and m.start() < found.get(uid, len(low) + 1):
                found[uid] = m.start()
    by_id = {str(u.get("user_id")): u for u in users}
    out = []
    for uid in sorted(found, key=found.get):
        names = person_names(by_id[uid])
        if names:
            out.append({"user_id": uid, "name": names[0], "also": names[1:]})
    return out


def relationship_line(person: dict) -> str:
    """One sentence on how he gets on with `person` (from people_in), or "" when there's nothing to say."""
    try:
        affinity = float(person.get("affinity"))
    except (TypeError, ValueError):
        affinity = 0.0
    feeling = ("You get on well with them." if affinity >= GET_ON_WELL else
               "You're not their biggest fan." if affinity <= NOT_A_FAN else "")
    notes = " ".join((person.get("relationship_notes") or "").split())  # kept to one line
    return " ".join(s for s in (feeling, f"Running jokes and grudges: {notes}" if notes else "") if s)


def format_people(people: list[dict]) -> str:
    """The prompt block for people who came up: who they are and what's remembered about them."""
    blocks = []
    for p in people:
        head = p["name"] + (f" (also called {', '.join(p['also'])})" if p.get("also") else "")
        lines = [*(p.get("notes") or []), *([rel] if (rel := relationship_line(p)) else [])]
        notes = "\n".join(f"- {n}" for n in lines) or "- Nothing remembered about them yet."
        blocks.append(f"{head}:\n{notes}")
    return "\n\n".join(blocks)


class MemoryClient:
    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("MEMORY_API_URL") or "").rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = api_key or os.getenv("MEMORY_API_KEY", "")
        self._down_until = 0.0
        self._names = {}  # user id -> name already sent, so it's only sent again when it changes
        self._glossary = None  # the Curator's terms, see glossary()
        self._glossary_at = 0.0
        self._users = None  # everyone's names, see users()
        self._users_at = 0.0
        self._relationships = {}  # character -> (fetched at, {user id: {affinity, notes}}), see relationships()
        # With the memory module off, the NAS is still used for characters and the message log
        # (the dashboard reads it); nothing is recalled into prompts or stored as a memory.
        self.recall = modules.enabled("memory")
        if not self.url:
            print("[memory] MEMORY_API_URL not set; memory is disabled.")
        elif not self.recall:
            print("[memory] Memory module is off; nothing is recalled or remembered.")

    @property
    def enabled(self) -> bool:
        return bool(self.url) and time.monotonic() >= self._down_until

    def reset(self):
        """Forget a past failure and open a fresh connection (the controller's Restart button)."""
        key = self.session.headers.get("X-API-Key", "")
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = key
        self._down_until = 0.0

    def _request(self, method: str, path: str, **kwargs):
        if not self.enabled:
            return None
        try:
            r = self.session.request(method, f"{self.url}{path}", timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r.json()
        except requests.ConnectionError as e:
            self._down_until = time.monotonic() + RETRY_AFTER
            print(f"[memory] NAS unreachable ({e.__class__.__name__}); retrying in {RETRY_AFTER}s.")
        except requests.RequestException as e:
            print(f"[memory] {method} {path} failed: {e}")
        return None

    def _background(self, fn, *args, **kwargs):
        threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()

    # ── Reads ──────────────────────────────────────────────────────────────
    def health(self) -> bool:
        return bool(self._request("GET", "/health"))

    def search(self, character: str, text: str, limit: int = 5, user_id: Optional[str] = None) -> list[dict]:
        body = {"character": character, "text": text, "limit": limit}
        if user_id:
            body["user_id"] = str(user_id)
        return self._request("POST", "/memories/search", json=body) or []

    def people(self, character: str, limit: int = 500) -> list[dict]:
        return self._request("GET", "/people", params={"character": character, "limit": limit}) or []

    def get_character(self, slug: str) -> Optional[dict]:
        return self._request("GET", f"/characters/{slug}")

    def list_characters(self) -> list[dict]:
        return self._request("GET", "/characters") or []

    def recent_messages(self, character: str, channel_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        params = {"character": character, "limit": limit}
        if channel_id:
            params["channel_id"] = str(channel_id)
        return self._request("GET", "/messages/recent", params=params) or []

    def relevant_memories(self, character: str, text: str, limit: int = 5) -> list[dict]:
        """Memories that actually relate to `text` (empty for filler like "okay")."""
        if not self.recall or len(text.split()) < MIN_WORDS:
            return []
        return [m for m in self.search(character, text, limit) if m.get("similarity", 0) >= MIN_SIMILARITY]

    def users(self) -> list[dict]:
        """Everyone the NAS has a name for, with all their names. Cached; refetched every PEOPLE_TTL."""
        now = time.monotonic()
        if self._users is None or now - self._users_at >= PEOPLE_TTL:
            rows = self._request("GET", "/users")
            if rows is not None or self._users is None:
                self._users = rows or []
            self._users_at = now
        return self._users

    def relationships(self, character: str) -> dict:
        """How `character` gets on with each person: {user id: {affinity, notes}}. Cached; refetched every
        RELATIONSHIPS_TTL. A failed fetch keeps the old ones (or {}) and waits a full TTL before retrying."""
        now = time.monotonic()
        at, known = self._relationships.get(character, (0.0, None))
        if known is None or now - at >= RELATIONSHIPS_TTL:
            rows = self._request("GET", "/relationships", params={"character": character})
            if isinstance(rows, list):
                known = {str(r["user_id"]): {"affinity": r.get("affinity"), "notes": r.get("notes")}
                         for r in rows if isinstance(r, dict) and r.get("user_id")}
            self._relationships[character] = (now, known or {})
        return self._relationships[character][1]

    def people_in(self, character: str, text: str, mentioned: Optional[dict] = None) -> list[dict]:
        """People `text` names (by any name they go by), plus `mentioned` ({user id: name}, Discord @mentions),
        each with what's remembered about them, most relevant to `text` first. See find_people."""
        if not self.recall or not text:
            return []
        people = find_people(text, self.users(), mentioned)
        feelings = self.relationships(character) if people else {}
        for p in people[:PEOPLE_LIMIT]:
            p["notes"] = [m["content"] for m in self.search(character, text, PERSON_NOTES, user_id=p["user_id"])]
            r = feelings.get(p["user_id"]) or {}
            p["affinity"], p["relationship_notes"] = r.get("affinity"), r.get("notes")
        return people[:PEOPLE_LIMIT]

    def glossary(self) -> list[dict]:
        """Every term the Curator defined (slang, game names, typos). Cached; refetched every GLOSSARY_TTL."""
        now = time.monotonic()
        if self._glossary is None or now - self._glossary_at >= GLOSSARY_TTL:
            rows = self._request("GET", "/glossary")
            if rows is not None or self._glossary is None:
                self._glossary = rows or []
            self._glossary_at = now  # a failed fetch keeps the old list and waits a full TTL before retrying
        return self._glossary

    def glossary_for(self, text: str, limit: int = GLOSSARY_LIMIT) -> list[str]:
        """Lines explaining the terms that come up in `text`, e.g. '"hll": Hell Let Loose, a WW2 shooter'.
        Same matching as the Curator's (curator_service/app/terms.py glossary_for)."""
        rows = self.glossary()
        if not rows or not text:
            return []
        low = text.lower()
        words = set(GLOSSARY_WORD.findall(low))
        defs = {}
        for r in rows:
            if r.get("definition"):
                defs.setdefault(r["term"], r["definition"])
        lines, seen = [], set()
        for r in rows:
            t = r["term"]
            if t in seen or not ((" " in t and t in low) or t in words or (t + "s") in words):
                continue
            seen.add(t)
            if r.get("related_term"):
                how = "is a typo of" if r.get("relation") == "typo" else "means the same as"
                meaning = defs.get(r["related_term"])
                lines.append(f'"{t}" {how} "{r["related_term"]}"' + (f": {meaning}" if meaning else ""))
            else:
                lines.append(f'"{t}": {r["definition"]}')
            if len(lines) >= limit:
                break
        return lines

    def history(self, character: str, channel_id) -> list[dict]:
        """The recent conversation in a channel, oldest first, as {role, content, time} dicts."""
        if not channel_id:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=HISTORY_MINUTES)
        history = []
        for r in self.recent_messages(character, channel_id, HISTORY_LIMIT):
            created = datetime.fromisoformat(r["created_at"])
            if r.get("content") and created >= cutoff:
                history.append({"role": r["role"], "content": r["content"], "time": created.timestamp()})
        return history

    # ── Writes (sync) ──────────────────────────────────────────────────────
    def add_memory(self, character: str, content: str, importance: float = 0.5,
                   user_ids: Optional[list] = None, kind: str = "event"):
        return self._request("POST", "/memories", json={
            "character": character,
            "content": content,
            "importance": importance,
            "user_ids": [str(u) for u in (user_ids or [])],
            "kind": kind,
        })

    def set_name(self, user_id, name: str, username: Optional[str] = None, avatar_url: Optional[str] = None):
        """Tell the NAS what to call someone (and their Discord handle, so a message using it finds them, and
        their avatar for the app's people lists). Without it, people show up as their Discord id."""
        user_id = str(user_id)
        if not name or self._names.get(user_id) == (name, username, avatar_url):
            return
        body = {"display_name": name, **({"username": username} if username else {}),
                **({"avatar_url": avatar_url} if avatar_url else {})}
        if self._request("PUT", f"/users/{user_id}", json=body) is not None:
            self._names[user_id] = (name, username, avatar_url)

    def log_message(self, character: str, role: str, content: str, user_id=None,
                    source: str = "text", guild_id=None, channel_id=None, display_name=None, username=None,
                    avatar_url=None):
        if user_id and display_name:
            self.set_name(user_id, display_name, username, avatar_url)
        return self._request("POST", "/messages", json={
            "character": character,
            "role": role,
            "content": content,
            "user_id": str(user_id) if user_id else None,
            "source": source,
            "guild_id": str(guild_id) if guild_id else None,
            "channel_id": str(channel_id) if channel_id else None,
        })

    def set_name_async(self, *args, **kwargs):
        if self.enabled:
            self._background(self.set_name, *args, **kwargs)

    def log(self, message: str, level: str = "info", source: str = "bot", data: Optional[dict] = None):
        return self._request("POST", "/logs", json={
            "message": message, "level": level, "source": source, "data": data,
        })

    # ── Writes (fire-and-forget, never block a reply) ──────────────────────
    def log_message_async(self, *args, **kwargs):
        if self.enabled:
            self._background(self.log_message, *args, **kwargs)

    def remember_if_relevant_async(self, model, character: str, speaker: str, text: str, user_ids=None):
        """Have the LLM pull out anything worth keeping about `speaker`, or someone they talk about, from `text`,
        and store it. The note goes under the people it names ("Kyle got a job" under whoever goes by Kyle);
        one that names nobody known goes under `user_ids` (the speaker)."""
        if not self.enabled or not self.recall or len(text.split()) < MIN_WORDS:
            return

        def work():
            try:
                note = model.extract_memory(speaker, text)
                if not note:
                    return
                existing = self.search(character, note, limit=5)
                if any(m.get("similarity", 0) >= DUPLICATE_SIMILARITY for m in existing):
                    print(f"[memory] already known: {note}")
                    return
                named = [p["user_id"] for p in find_people(note, self.users())]
                self.add_memory(character, note, importance=0.6, user_ids=named or user_ids, kind="fact")
                print(f"[memory] stored: {note}")
            except Exception as e:
                print(f"[memory] could not extract/store memory: {e}")

        self._background(work)


def active_character(default: str = "ohm") -> str:
    """Slug of the character to run as.

    OHM_CHARACTER in .env wins; otherwise it's the card in play in the controller
    (configs.ui.status on the NAS), and `default` when there's none or the NAS is down.
    """
    if os.getenv("OHM_CHARACTER"):
        return os.getenv("OHM_CHARACTER")
    for row in memory.list_characters():
        if ((row.get("configs") or {}).get("ui") or {}).get("status") == "Active":
            return row["slug"]
    return default


def load_character(slug: str, fallback: Optional[dict] = None, defaults: Optional[dict] = None) -> dict:
    """Build the model config for `slug` from the NAS, falling back to config/constants.py.

    `fallback` is the local copy of this character, used whole when the NAS doesn't have it.
    `defaults` fills in what a NAS character leaves out (model name, configs).
    Returns the same shape as constants.models[slug] plus its slug, the Curator model it runs on and
    its voice: slug, name, model, api_type, personality, configs, base_model_id, adapter_id,
    voice_engine and voice (see voice_file).
    """
    fallback = fallback or {}
    defaults = {**(defaults or {}), **fallback}
    row = memory.get_character(slug)
    if not row:
        if fallback:
            print(f"[memory] Using character '{slug}' from config/constants.py.")
            return {"slug": slug, "base_model_id": None, "adapter_id": None, "voice": None, "voice_engine": "chatterbox",
                    **fallback}
        raise RuntimeError(f"Character '{slug}' not found on the NAS and no local fallback exists.")

    configs = {k: v for k, v in (row.get("configs") or {}).items() if k != "ui"}  # ui = controller-only fields
    print(f"[memory] Loaded character '{slug}' from the NAS.")
    return {
        "slug": slug,
        "name": row["name"],
        "model": row.get("model") or defaults.get("model"),
        "api_type": defaults.get("api_type", "openai"),
        "personality": row.get("personality") or defaults.get("personality") or f"You are {row['name']}.",
        "configs": configs or defaults.get("configs", {}),
        # configs.ui.model: the Curator base model and LoRA adapter picked on the Characters page
        **ui_model(row),
        "voice": voice_file(row),
        "voice_engine": voice_engine(row),
    }


TTS_ENGINES = ("chatterbox", "kokoro")  # apps/tts/engine.ENGINES


def voice_engine(row: Optional[dict]) -> str:
    """characters.voice.engine; older cards are chatterbox"""
    engine = ((row or {}).get("voice") or {}).get("engine")
    return engine if engine in TTS_ENGINES else "chatterbox"


def voice_file(row: Optional[dict]) -> Optional[str]:
    """Chatterbox clip (voice.file) or Kokoro preset (voice.preset); None for the engine's default"""
    voice = (row or {}).get("voice") or {}
    return (voice.get("preset") if voice_engine(row) == "kokoro" else voice.get("file")) or None


def format_memories(memories: list[dict]) -> str:
    """Turn search results into a prompt-ready bullet list."""
    return "\n".join(f"- {m['content']}" for m in memories)


memory = MemoryClient()

# Mask known names before a cloud model sees them
brain.privacy.add_people(lambda: [person_names(u) for u in memory.users()])
