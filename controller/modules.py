"""Which modules the controller app turned off.

The app passes OHM_DISABLED_MODULES (comma separated module folder names, e.g. "stt,tts")
when it starts main.py, so turning a module on or off takes effect on the next start.
"""

import os

from dotenv import load_dotenv

load_dotenv()
DISABLED = {name.strip() for name in os.getenv("OHM_DISABLED_MODULES", "").split(",") if name.strip()}


def enabled(name: str) -> bool:
    return name not in DISABLED
