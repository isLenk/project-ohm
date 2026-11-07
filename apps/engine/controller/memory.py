"""Client for the memory service running on the NAS (see memory_service/).

Configured through .env:
    MEMORY_API_URL=http://<nas-ip>:8100
    MEMORY_API_KEY=<same API_KEY as memory_service/.env>

Every call fails soft: if the NAS is down or not configured, searches return []
and writes are dropped, so the bot keeps talking (just without memories).
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

from controller import modules

load_dotenv()

TIMEOUT = (2, 5)  # (connect, read) seconds
RETRY_AFTER = 30  # seconds to wait before trying the NAS again after a failure

MIN_SIMILARITY = 0.35   # memories less similar than this to what was said are left out of the prompt
DUPLICATE_SIMILARITY = 0.9  # a new note this close to an existing memory is not stored again
MIN_WORDS = 3           # "okay", "what?", "um yeah" are never worth searching or remembering
HISTORY_MINUTES = 30    # how far back the conversation history goes
HISTORY_LIMIT = 20      # max messages of conversation history per reply


class MemoryClient:
    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or os.getenv("MEMORY_API_URL") or "").rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = api_key or os.getenv("MEMORY_API_KEY", "")
        self._down_until = 0.0
        self._names = {}  # user id -> name already sent, so it's only sent again when it changes
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

    def set_name(self, user_id, name: str):
        """Tell the NAS what to call someone. Without it, people show up as their Discord id."""
        user_id = str(user_id)
        if not name or self._names.get(user_id) == name:
            return
        if self._request("PUT", f"/users/{user_id}", json={"display_name": name}) is not None:
            self._names[user_id] = name

    def log_message(self, character: str, role: str, content: str, user_id=None,
                    source: str = "text", guild_id=None, channel_id=None, display_name=None):
        if user_id and display_name:
            self.set_name(user_id, display_name)
        return self._request("POST", "/messages", json={
            "character": character,
            "role": role,
            "content": content,
            "user_id": str(user_id) if user_id else None,
            "source": source,
            "guild_id": str(guild_id) if guild_id else None,
            "channel_id": str(channel_id) if channel_id else None,
        })

    def log(self, message: str, level: str = "info", source: str = "bot", data: Optional[dict] = None):
        return self._request("POST", "/logs", json={
            "message": message, "level": level, "source": source, "data": data,
        })

    # ── Writes (fire-and-forget, never block a reply) ──────────────────────
    def log_message_async(self, *args, **kwargs):
        if self.enabled:
            self._background(self.log_message, *args, **kwargs)

    def remember_if_relevant_async(self, model, character: str, speaker: str, text: str, user_ids=None):
        """Have the LLM pull out anything worth keeping about `speaker` from `text`, and store it."""
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
                self.add_memory(character, note, importance=0.6, user_ids=user_ids, kind="fact")
                print(f"[memory] stored: {note}")
            except Exception as e:
                print(f"[memory] could not extract/store memory: {e}")

        self._background(work)


def load_character(slug: str, fallback: Optional[dict] = None) -> dict:
    """Build the model config for `slug` from the NAS, falling back to config/constants.py.

    Returns the same shape as constants.models[slug]: name, model, api_type, personality, configs.
    """
    fallback = fallback or {}
    row = memory.get_character(slug)
    if not row:
        if fallback:
            print(f"[memory] Using character '{slug}' from config/constants.py.")
            return fallback
        raise RuntimeError(f"Character '{slug}' not found on the NAS and no local fallback exists.")

    configs = {k: v for k, v in (row.get("configs") or {}).items() if k != "ui"}  # ui = controller-only fields
    print(f"[memory] Loaded character '{slug}' from the NAS.")
    return {
        "name": row["name"],
        "model": row.get("model") or fallback.get("model"),
        "api_type": fallback.get("api_type", "openai"),
        "personality": row.get("personality") or fallback.get("personality", ""),
        "configs": configs or fallback.get("configs", {}),
    }


def format_memories(memories: list[dict]) -> str:
    """Turn search results into a prompt-ready bullet list."""
    return "\n".join(f"- {m['content']}" for m in memories)


memory = MemoryClient()
