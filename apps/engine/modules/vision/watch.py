"""Ohm noticing something new on the screen and saying something about it on his own (part of the vision module).

Every WATCH_EVERY_S a tiny grayscale thumbnail of the screen is compared with the last one he looked at. When enough
of it changed and the call is quiet, he says one line about it with a real screenshot (DiscordClient.make_idle_stream).
Screenshots stay local: this path never leaves the PC. The thumbnails live in memory only, and the screenshot goes
to the local llama-server like every other screen= request.
"""
import asyncio
import time

from modules.mood.mood import mood
from modules.vision import screen

try:
    from PIL import ImageChops, ImageStat
except ImportError:
    ImageChops = ImageStat = None

WATCH_EVERY_S = 5             # how often he glances at the screen
THUMB_SIZE = (64, 36)         # small enough to compare in no time, big enough to tell a new window from a cursor move
CHANGE_THRESHOLD = 25         # mean difference per pixel (0..255) that counts as something new on screen
QUIET_BEFORE_COMMENT_S = 6    # nobody has said anything for this long, so he isn't talking over anyone
COMMENT_COOLDOWN_S = 90       # at most one screen comment this often, so it doesn't turn into a live commentary
ANNOYED_LIMIT = 0.5           # too annoyed (talked over, told to shut up) to pipe up


def health() -> tuple[bool, str]:
    if ImageChops is None:
        return False, "Pillow isn't installed, so the screen can't be watched"
    return True, "Comments on the screen when something new comes up in a quiet call"


def thumbnail(image):
    """A tiny grayscale copy of `image` to compare frames with."""
    return image.convert("L").resize(THUMB_SIZE)


def difference(a, b) -> float:
    """Mean absolute difference per pixel between two thumbnails, 0 (same) to 255."""
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def grab_thumbnail():
    """A thumbnail of the screen right now, or None (vision off, capture failed). Blocking, run it in a thread."""
    if not screen.on():
        return None
    try:
        return thumbnail(screen.ImageGrab.grab())
    except Exception as e:  # e.g. a locked screen or a secure desktop (UAC prompt)
        print(f"[vision] Couldn't glance at the screen: {e}")
        return None


async def watch_worker(voice):
    """Runs alongside DiscordVoice.idle_worker while he's in a call. voice: the DiscordVoice."""
    if not screen.on() or ImageChops is None:
        return
    baseline, last_comment = None, 0.0
    while True:
        await asyncio.sleep(WATCH_EVERY_S)
        if voice.vc is None or not voice.vc.is_connected():
            continue
        thumb = await asyncio.to_thread(grab_thumbnail)
        if thumb is None:
            continue
        if baseline is None:
            baseline = thumb  # the first look is nothing new
            continue
        change = difference(thumb, baseline)
        if change <= CHANGE_THRESHOLD:
            continue
        # ? The new baseline whether or not he says anything, so he never comments on the same frame twice
        baseline = thumb
        now = time.monotonic()
        if voice.is_busy() or voice.sink.anyone_speaking() or now - voice.last_heard < QUIET_BEFORE_COMMENT_S:
            continue
        if now - last_comment < COMMENT_COOLDOWN_S or mood.annoyed > ANNOYED_LIMIT:
            continue
        if not any(not m.bot for m in voice.vc.channel.members):
            continue
        print(f"[vision] Something new on the screen ({change:.0f}), saying something about it")
        last_comment = now
        voice.current_prompt = {}
        voice.response_task = asyncio.create_task(voice.speak_unprompted(about_screen=True))
