"""Names for brain.privacy to mask before a cloud model: the project's authors and the NAS's users"""

import brain
import requests

from .config import MEMORY_API_KEY, MEMORY_API_URL
from .project import state

TIMEOUT = (2, 3)


def project_people() -> list[list[str]]:
    """Human authors, display name first"""
    project = state.project
    if project is None:
        return []
    rows = project.all("SELECT name, display_name FROM authors WHERE is_bot = 0")
    return [[r["display_name"], r["name"]] for r in rows]


def nas_people() -> list[list[str]]:
    """NAS users with all their names"""
    if not MEMORY_API_URL:
        return []
    r = requests.get(f"{MEMORY_API_URL}/users", headers={"X-API-Key": MEMORY_API_KEY}, timeout=TIMEOUT)
    r.raise_for_status()
    return [[u.get("preferred_name"), u.get("display_name"), u.get("username"), *(u.get("aliases") or [])]
            for u in r.json() or []]


def register() -> None:
    brain.privacy.add_people(project_people)
    brain.privacy.add_people(nas_people)
