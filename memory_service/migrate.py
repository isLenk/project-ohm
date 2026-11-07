"""One-time import of existing data into the NAS memory service. Run from the PC:

    Scripts\\python.exe memory_service\\migrate.py              # characters + Qdrant memories
    Scripts\\python.exe memory_service\\migrate.py --dry-run    # show what would be imported
    Scripts\\python.exe memory_service\\migrate.py --qdrant http://<host>:6333

Imports:
  * config/constants.py characters (personality, model, configs)
  * controller-mainframe/characters.db (nedb) characters not already in constants.py
  * every Qdrant collection named memories_<character> (payload.value), with original timestamps

Uses MEMORY_API_URL / MEMORY_API_KEY from the root .env. Running it twice will duplicate memories.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class Api:
    def __init__(self, url, key, dry_run):
        self.url, self.dry_run = url.rstrip("/"), dry_run
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = key

    def call(self, method, path, **kw):
        if self.dry_run and method != "GET":
            return None
        r = self.s.request(method, self.url + path, timeout=30, **kw)
        r.raise_for_status()
        return r.json()


def load_nedb(path: Path) -> list[dict]:
    """nedb is append-only JSON lines; the last line per _id wins, $$deleted removes it."""
    docs = {}
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        if doc.get("$$deleted"):
            docs.pop(doc.get("_id"), None)
        elif "_id" in doc:
            docs[doc["_id"]] = doc
    return list(docs.values())


def migrate_characters(api: Api) -> set[str]:
    from config import constants

    # Controller UI fields (status, picture, created) go in configs.ui.
    ui_by_slug = {}
    nedb_docs = load_nedb(ROOT / "controller-mainframe" / "characters.db")
    for doc in nedb_docs:
        ui_by_slug[slugify(doc.get("name", ""))] = {
            k: doc[k] for k in ("status", "picture", "created") if k in doc
        }

    done = set()
    for slug, c in constants.models.items():
        body = {
            "name": c["name"],
            "model": c.get("model"),
            "personality": c.get("personality", "").strip(),
            "configs": {**c.get("configs", {}), "ui": ui_by_slug.get(slug, {})},
        }
        print(f"character  {slug:<12} (constants.py)")
        api.call("PUT", f"/characters/{slug}", json=body)
        done.add(slug)

    for doc in nedb_docs:
        slug = slugify(doc.get("name", ""))
        if not slug or slug in done:
            continue
        body = {
            "name": doc["name"],
            "personality": doc.get("description"),
            "configs": {"ui": ui_by_slug[slug]},
        }
        print(f"character  {slug:<12} (characters.db)")
        api.call("PUT", f"/characters/{slug}", json=body)
        done.add(slug)
    return done


def migrate_qdrant(api: Api, qdrant_url: str, characters: set[str]):
    try:
        collections = requests.get(f"{qdrant_url}/collections", timeout=5).json()["result"]["collections"]
    except requests.RequestException as e:
        print(f"Skipping memories: Qdrant not reachable at {qdrant_url} ({e.__class__.__name__}).")
        return

    for col in (c["name"] for c in collections):
        if not col.startswith("memories_"):
            continue
        character = col.removeprefix("memories_")
        if character not in characters:
            print(f"Skipping {col}: no character '{character}' (add it first).")
            continue

        count, offset = 0, None
        while True:
            body = {"limit": 256, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            res = requests.post(f"{qdrant_url}/collections/{col}/points/scroll", json=body, timeout=30).json()["result"]
            for p in res["points"]:
                text = (p.get("payload") or {}).get("value")
                if not text:
                    continue
                created = None
                # Old points used Date.now() (ms) as their id.
                if isinstance(p["id"], int) and p["id"] > 1_000_000_000_000:
                    created = datetime.fromtimestamp(p["id"] / 1000, tz=timezone.utc).isoformat()
                api.call("POST", "/memories", json={
                    "character": character, "content": text, "importance": 0.6, "created_at": created,
                })
                count += 1
            offset = res.get("next_page_offset")
            if offset is None:
                break
        print(f"memories   {character:<12} {count} from {col}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("MEMORY_API_URL"), help="memory service URL")
    ap.add_argument("--key", default=os.getenv("MEMORY_API_KEY"), help="memory service API key")
    ap.add_argument("--qdrant", default="http://localhost:6333", help="old Qdrant URL")
    ap.add_argument("--dry-run", action="store_true", help="read everything, write nothing")
    args = ap.parse_args()

    if not args.url or not args.key:
        sys.exit("Set MEMORY_API_URL and MEMORY_API_KEY in .env (or pass --url/--key).")

    api = Api(args.url, args.key, args.dry_run)
    api.call("GET", "/health")
    if args.dry_run:
        print("DRY RUN: nothing will be written.\n")

    characters = migrate_characters(api)
    migrate_qdrant(api, args.qdrant.rstrip("/"), characters)
    print("\nDone.")


if __name__ == "__main__":
    main()
