"""Which LoRA adapter each chat request runs with.

llama-server is started with every adapter for the base loaded (--lora a.gguf,b.gguf), and its
global scales are whatever was last set, which can drift. So every request lists every loaded
adapter with its own scale ("lora": [{"id": 0, "scale": 0}, {"id": 1, "scale": 1}]): the in-play
character's adapter at 1.0 for replies, everything at 0 for utility calls (intent, search, memory).

The character's adapter is configs.ui.model.adapter_id on the NAS, an id in the Curator's registry
(registry.sqlite), which gives the gguf path; llama-server's GET /lora-adapters maps that path to
the id the request needs. Ids can change whenever llama-server restarts, hence the short cache.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import requests

LORA_TTL = 15       # seconds before GET /lora-adapters (and /props) is asked again
CHARACTER_TTL = 30  # seconds before the character's adapter is re-read from the NAS
TIMEOUT = (1, 2)    # llama-server is local; don't hold a reply up for long if it's slow


def curator_data_dir() -> Path:
    """Same folder as the Curator's _app_data() (apps/curator/curator_service/app/config.py)."""
    if os.getenv("CURATOR_DATA_DIR"):
        return Path(os.environ["CURATOR_DATA_DIR"])
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA", Path.home())) / "ohm-curator"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ohm-curator"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ohm-curator"


def ui_model(row: Optional[dict]) -> dict:
    """{"base_model_id", "adapter_id"} from a NAS character row (configs.ui.model); None where unset."""
    model = (((row or {}).get("configs") or {}).get("ui") or {}).get("model") or {}
    return {"base_model_id": model.get("base_model_id"), "adapter_id": model.get("adapter_id")}


def norm_path(path: str, windows: bool = sys.platform == "win32") -> str:
    """Comparable form of a path: llama-server and the registry don't always agree on slashes or case."""
    path = str(path).replace("\\", "/").rstrip("/")
    return path.lower() if windows else path


def lora_list(loaded: list[dict], path: Optional[str]) -> list[dict]:
    """A request's "lora" field: every loaded adapter, 1.0 for the one at `path`, 0 for the rest."""
    want = norm_path(path) if path else None
    return [{"id": a["id"], "scale": 1.0 if want and norm_path(a.get("path", "")) == want else 0.0}
            for a in loaded]


def registry_path(table: str, row_id: Optional[str], registry: Optional[Path] = None) -> Optional[str]:
    """gguf_path of an adapter or base model in the Curator's registry, or None (unknown id, no Curator)."""
    if not row_id:
        return None
    registry = registry or curator_data_dir() / "registry.sqlite"
    if not registry.exists():
        return None
    try:
        # ? Read-only, so the engine can never lock or change the Curator's database
        db = sqlite3.connect(f"{registry.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            row = db.execute(f"SELECT gguf_path FROM {table} WHERE id = ?", (row_id,)).fetchone()
        finally:
            db.close()  # ? `with sqlite3.connect()` only ends the transaction, it doesn't close the file
    except sqlite3.Error as e:
        print(f"[adapters] Couldn't read the Curator registry: {e}")
        return None
    return row[0] if row and row[0] else None


class Adapters:
    """Builds the "lora" field for the in-play character's requests."""

    def __init__(self, character: dict, endpoint: str, fetch_character=None):
        self.slug = character.get("slug")
        self.name = character.get("name") or self.slug
        self.root = endpoint.rstrip("/").removesuffix("/v1")  # /lora-adapters lives at the server root
        self._fetch = fetch_character  # slug -> NAS row (or None), to notice adapter changes
        self._ids = {k: character.get(k) for k in ("base_model_id", "adapter_id")}
        self._paths = None  # {"adapter": gguf, "base": gguf}, resolved from _ids
        self._checked_at = 0.0
        self._server = {}   # GET path -> (fetched at, json or None)
        self._warned = set()

    def lora(self, base: bool = False) -> Optional[list[dict]]:
        """The "lora" field for one request, or None to leave it out (server down or no adapters loaded).
        base=True runs on the plain base model (every adapter at 0)."""
        loaded = self._get("/lora-adapters")
        if not isinstance(loaded, list) or not loaded:
            return None
        if base:
            return lora_list(loaded, None)
        path = self._character_paths()["adapter"]
        if path and not any(norm_path(a.get("path", "")) == norm_path(path) for a in loaded):
            self._warn_once(("missing", norm_path(path)),
                            f"[adapters] {self.name}'s adapter isn't loaded in llama-server ({path}). "
                            "Restart llama-server from the Curator/Characters page; until then replies use the base model.")
            path = None
        return lora_list(loaded, path)

    def _character_paths(self) -> dict:
        """The character's adapter and base gguf paths, re-read from the NAS every CHARACTER_TTL."""
        now = time.monotonic()
        if self._paths is not None and now - self._checked_at < CHARACTER_TTL:
            return self._paths
        self._checked_at = now
        if self._fetch and self.slug and self._paths is not None:
            row = self._fetch(self.slug)
            if row:  # NAS down or character gone: keep what we had
                ids = ui_model(row)
                if ids != self._ids:
                    print(f"[adapters] {self.name}'s model changed on the Characters page: {ids}")
                self._ids = ids
        adapter_id = self._ids.get("adapter_id")
        paths = {"adapter": registry_path("adapters", adapter_id),
                 "base": registry_path("base_models", self._ids.get("base_model_id"))}
        if paths != self._paths:
            print(f"[adapters] {self.name} runs on " + (f"adapter {paths['adapter']}" if paths["adapter"] else "the base model"))
        self._paths = paths
        if adapter_id and not self._paths["adapter"]:
            self._warn_once(("unknown", adapter_id),
                            f"[adapters] {self.name}'s adapter {adapter_id} isn't in the Curator registry; using the base model.")
        self._check_base()
        return self._paths

    def _check_base(self):
        base = self._paths["base"]
        running = (self._get("/props") or {}).get("model_path") if base else None
        if base and running and norm_path(running) != norm_path(base):
            self._warn_once(("base", norm_path(base), norm_path(running)),
                            f"[adapters] llama-server is running {running}, but {self.name} is set to {base}. "
                            "Restart llama-server from the Curator/Characters page.")

    def _get(self, path: str):
        """GET from llama-server, cached LORA_TTL. None if it's down or doesn't have the endpoint."""
        now = time.monotonic()
        at, value = self._server.get(path, (None, None))
        if at is not None and now - at < LORA_TTL:
            return value
        try:
            r = requests.get(self.root + path, timeout=TIMEOUT)
            value = r.json() if r.ok else None
        except (requests.RequestException, ValueError):
            value = None
        self._server[path] = (now, value)
        return value

    def _warn_once(self, key, message: str):
        if key not in self._warned:
            self._warned.add(key)
            print(message)
