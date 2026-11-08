"""Which modules the controller app turned on or off.

The app passes OHM_DISABLED_MODULES (comma separated module folder names, e.g. "stt,tts")
when it starts main.py, so turning a module on or off takes effect on the next start.
A module whose body.json says "default": false is opt-in instead: it only runs when it's
in OHM_ENABLED_MODULES (vision, since it looks at the screen).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def _names(key: str) -> set[str]:
    return {name.strip() for name in os.getenv(key, "").split(",") if name.strip()}


DISABLED = _names("OHM_DISABLED_MODULES")
ENABLED = _names("OHM_ENABLED_MODULES")


def _opt_in(name: str) -> bool:
    try:
        body = json.loads((MODULES_DIR / name / "body.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return body.get("default", True) is False


def enabled(name: str) -> bool:
    if _opt_in(name):
        return name in ENABLED
    return name not in DISABLED
