"""Register NAS names for the brain to mask. Import is enough.
NAS down = no list, so private tasks skip cloud backends."""

import os

import requests

import brain


def nas_people() -> list:
    """Each NAS user's names and aliases"""
    url = os.getenv("MEMORY_API_URL", "").rstrip("/")
    if not url:
        return []
    users = requests.get(f"{url}/users", headers={"X-API-Key": os.getenv("MEMORY_API_KEY", "")}, timeout=5).json()
    return [[u.get("name"), u.get("preferred_name"), u.get("display_name"), u.get("username"), *(u.get("aliases") or [])]
            for u in users]


brain.privacy.add_people(nas_people)
