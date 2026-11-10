"""Which modules the controller app turned on or off.

The app passes OHM_DISABLED_MODULES (comma separated module folder names, e.g. "stt,tts")
when it starts main.py, so turning a module on or off takes effect on the next start.
A module whose body.json says "default": false is opt-in instead: it only runs when it's
in OHM_ENABLED_MODULES (vision, since it looks at the screen).
"live": true (vision) re-reads the lists from OHM_SETTINGS_FILE when it changes, no restart.
"""

import functools
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
RECHECK_S = 1.0  # settings file mtime check interval


def _split(value: str) -> set[str]:
    return {name.strip() for name in (value or "").split(",") if name.strip()}


def _names(key: str) -> set[str]:
    return _split(os.getenv(key, ""))


DISABLED = _names("OHM_DISABLED_MODULES")
ENABLED = _names("OHM_ENABLED_MODULES")

_lock = threading.Lock()
_live = {"checked": 0.0, "mtime": None, "lists": None}  # lists: (enabled, disabled)


@functools.lru_cache(maxsize=None)
def _body(name: str) -> dict:
    try:
        return json.loads((MODULES_DIR / name / "body.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _opt_in(name: str) -> bool:
    return _body(name).get("default", True) is False


def _live_lists(path: str | None = None):
    """(enabled, disabled) from the settings file, None without one"""
    path = path or os.getenv("OHM_SETTINGS_FILE")
    if not path:
        return None
    with _lock:
        now = time.monotonic()
        if _live["lists"] is not None and now - _live["checked"] < RECHECK_S:
            return _live["lists"]
        _live["checked"] = now
        try:
            mtime = os.path.getmtime(path)
            if mtime != _live["mtime"] or _live["lists"] is None:
                saved = json.loads(Path(path).read_text(encoding="utf-8"))
                # Missing key: keep the startup list
                on = _split(saved["OHM_ENABLED_MODULES"]) if "OHM_ENABLED_MODULES" in saved else ENABLED
                off = _split(saved["OHM_DISABLED_MODULES"]) if "OHM_DISABLED_MODULES" in saved else DISABLED
                _live.update(mtime=mtime, lists=(on, off))
        except (OSError, ValueError):
            return _live["lists"]  # mid-write: keep the last read
        return _live["lists"]


def enabled(name: str) -> bool:
    on, off = ENABLED, DISABLED
    if _body(name).get("live"):
        on, off = _live_lists() or (ENABLED, DISABLED)
    if _opt_in(name):
        return name in on
    return name not in off
