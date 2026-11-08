"""A one-line description of what's on the screen from a bigger model in the cloud, opt-in (part of the vision module).

Off unless OHM_SCREEN_CLOUD=1 (Settings › Integrations). When it's on and watch.py sees the screen change, at most
every DESCRIBE_EVERY_S, noticed() sends a cropped screenshot to the "screen" role (BRAIN_ROLE_SCREEN, Codex by
default) in the background. current() puts the latest line in the reply prompt for a while, like controller/brief.py
does with its summary. Codex gets the screenshot as a file in its per-call temp folder, deleted after the call.
When it's off nothing is captured for it and nothing leaves the PC; live replies always look at the screen through
the local model (screen.look).
"""
import os
import threading
import time

import brain

from controller.memory import memory
from modules.vision import screen, window
from utils.Vision import image_url

DESCRIBE_EVERY_S = 30    # at most one cloud call this often; a Codex call is ~5 s and ~10k tokens
DESCRIBE_FRESH_S = 120   # an older description is about a screen that's probably moved on
DESCRIBE_SIDE = 1280     # plenty for a big model to read a scoreboard, and a smaller upload
LINE_MAX = 200           # a longer answer is the model rambling, not one line

SYSTEM = (
    "You see a screenshot of someone's computer screen. Say what's on it in ONE line of no more than 25 words, "
    "in the present tense. Be concrete: name the game, app, site or video, and what's happening in it "
    "(the score, the menu, who's winning, what's being read or watched). "
    "Reply with just the line, no quotes and nothing else."
)

clock = time.monotonic  # tests swap it for a fake one

_latest = (0.0, "")    # (clock() when written, the description)
_asked = None          # clock() of the last call started, None before the first
_lock = threading.Lock()


def on() -> bool:
    return os.getenv("OHM_SCREEN_CLOUD") == "1" and screen.on()


def noticed(region=None) -> bool:
    """watch.py saw the screen change (region: where, or None). Starts a description in the background when it's
    on and the last one started at least DESCRIBE_EVERY_S ago. True when it started one."""
    global _asked
    if not on():
        return False
    now = clock()
    with _lock:
        if _asked is not None and now - _asked < DESCRIBE_EVERY_S:
            return False
        _asked = now  # ? set before the call, so a slow one doesn't let the next change start a second
    memory._background(describe, region)
    return True


def current() -> str:
    """The latest description, or "" when there's none from the last DESCRIBE_FRESH_S."""
    with _lock:
        at, line = _latest
    return line if line and clock() - at < DESCRIBE_FRESH_S else ""


def describe(region=None) -> str:
    """Take a screenshot (cropped like screen.grab), have the screen role describe it, and keep the line.
    Returns the line, or "" when there's no screenshot or no answer."""
    global _latest
    win = window.active()
    image = screen.grab(region, win)
    if image is None:
        return ""
    app = window.describe(win)
    user = (f"The window in front is: {app}\n\n" if app else "") + "What's on this screen right now?"
    started = time.perf_counter()
    answer = brain.run(brain.Task(role="screen", system=SYSTEM, user=user, max_tokens=80, local_only=False,
                                  images=[image_url(image, max_side=DESCRIBE_SIDE)]))
    ms = round((time.perf_counter() - started) * 1000)
    line = clean(answer)
    if not line:
        print(f"[vision] No screen description ({ms} ms)")
        return ""
    with _lock:
        _latest = (clock(), line)
    print(f"[vision] On the screen: {line} ({ms} ms)")
    memory.log(line, source="screen", data={"backend": brain.last_backend(), "ms": ms, "app": app})
    return line


def clean(answer) -> str:
    """The model's answer as one line, or "" when it isn't usable."""
    line = " ".join(str(answer or "").split()).strip('"').strip()
    return line if 0 < len(line) <= LINE_MAX else ""
