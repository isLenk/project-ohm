"""App-wide registry of base models, trained adapters and training runs.

Lives in the app data folder, so every project sees every model ever imported.
"""

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from . import schema
from .config import MODELS_DIR, REGISTRY_PATH
from .errors import ApiError, not_found
from .project import now_ms

CHUNK = 64 * 1024 * 1024
QUANT_RE = re.compile(r"(?:^|[-_.])((?:I?Q\d[\w]*)|F16|BF16|F32)(?=\.gguf$)", re.IGNORECASE)

# Known GGUF-only finetunes and the HF weights to train on instead. See training.md section 3.
TRAINABLE_BASES = [
    (re.compile(r"gemma-4-e4b", re.IGNORECASE), "unsloth/gemma-4-E4B-it"),
]


def fingerprint(path: Path) -> str:
    """Hash of size + first and last 64 MB. Full sha256 of a 5 GB GGUF takes too long to do on click."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > CHUNK:
            f.seek(max(size - CHUNK, CHUNK))
            h.update(f.read(CHUNK))
    return h.hexdigest()


def describe_gguf(path: Path) -> dict:
    stem = path.stem
    quant = QUANT_RE.search(path.name)
    name = stem[: quant.start()] if quant else stem
    name = re.sub(r"[-_]+", " ", name).strip()
    hf_path, different = None, False
    for pattern, repo in TRAINABLE_BASES:
        if pattern.search(stem):
            hf_path = repo
            different = repo.split("/")[-1].lower() not in stem.lower()
    return {"name": name, "quant": quant.group(1).upper() if quant else None, "hf_path": hf_path, "different_base": different}


class Registry:
    def __init__(self, path: Path = REGISTRY_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(schema.REGISTRY_SCHEMA)
        self.lock = threading.RLock()

    def all(self, sql: str, params: Any = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Any = ()) -> Optional[dict]:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def run(self, sql: str, params: Any = ()) -> None:
        with self.lock:
            self.conn.execute(sql, params)

    # ── settings ───────────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.run(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ── base models ────────────────────────────────────────────────────────
    def add_base(self, gguf_path: str, name: Optional[str] = None, hf_path: Optional[str] = None) -> dict:
        path = Path(gguf_path)
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise ApiError(422, "not_gguf", f"{gguf_path} is not a .gguf file.")
        if path.name.lower().startswith("mmproj"):
            raise ApiError(422, "not_a_model", "That is a vision projector, not a model.")
        fp = fingerprint(path)
        existing = self.one("SELECT * FROM base_models WHERE fingerprint = ?", (fp,))
        if existing:
            return existing
        d = describe_gguf(path)
        row = {
            "id": str(uuid.uuid4()), "name": name or d["name"], "gguf_path": str(path.resolve()),
            "quant": d["quant"], "hf_path": hf_path or d["hf_path"],
            "different_base": int(bool(d["different_base"] and not hf_path)),
            "fingerprint": fp, "imported_at": now_ms(),
        }
        self.run(
            "INSERT INTO base_models (id, name, gguf_path, quant, hf_path, different_base, fingerprint, imported_at) "
            "VALUES (:id, :name, :gguf_path, :quant, :hf_path, :different_base, :fingerprint, :imported_at)",
            row,
        )
        if not self.get("active"):
            self.put("active", {"base_model_id": row["id"], "adapter_id": None})
        return row

    def update_base(self, model_id: str, name: Optional[str], hf_path: Optional[str]) -> dict:
        row = self.base(model_id)
        if name is not None:
            self.run("UPDATE base_models SET name = ? WHERE id = ?", (name, model_id))
        if hf_path is not None:
            self.run("UPDATE base_models SET hf_path = ?, different_base = 0 WHERE id = ?", (hf_path or None, model_id))
        return self.base(row["id"])

    def base(self, model_id: str) -> dict:
        row = self.one("SELECT * FROM base_models WHERE id = ?", (model_id,))
        if not row:
            raise not_found("Base model")
        return row

    def seed(self) -> None:
        """Register any .gguf already sitting in llama.cpp/models, once."""
        if self.get("seeded") or not MODELS_DIR.is_dir():
            return
        for p in sorted(MODELS_DIR.glob("*.gguf")):
            if not p.name.lower().startswith("mmproj"):
                try:
                    self.add_base(str(p))
                except (ApiError, OSError):
                    pass
        self.put("seeded", True)

    # ── adapters ───────────────────────────────────────────────────────────
    def add_adapter(self, gguf_path: str, base_model_id: str, name: Optional[str] = None,
                    peft_path: Optional[str] = None, run_id: Optional[str] = None) -> dict:
        path = Path(gguf_path)
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise ApiError(422, "not_gguf", "Pick a LoRA adapter converted to .gguf.")
        self.base(base_model_id)
        row = {
            "id": str(uuid.uuid4()), "base_model_id": base_model_id, "name": name or path.stem,
            "gguf_path": str(path.resolve()), "peft_path": peft_path, "run_id": run_id, "created_at": now_ms(),
        }
        self.run(
            "INSERT INTO adapters (id, base_model_id, name, gguf_path, peft_path, run_id, created_at) "
            "VALUES (:id, :base_model_id, :name, :gguf_path, :peft_path, :run_id, :created_at)",
            row,
        )
        return row

    def adapter(self, adapter_id: str) -> dict:
        row = self.one("SELECT * FROM adapters WHERE id = ?", (adapter_id,))
        if not row:
            raise not_found("Adapter")
        return row

    def listing(self) -> dict:
        return {
            "base_models": self.all("SELECT * FROM base_models ORDER BY imported_at"),
            "adapters": self.all("SELECT * FROM adapters ORDER BY created_at DESC"),
            "active": self.get("active", {"base_model_id": None, "adapter_id": None}),
        }


registry: Optional[Registry] = None


def get_registry() -> Registry:
    global registry
    if registry is None:
        registry = Registry()
        registry.seed()
    return registry
