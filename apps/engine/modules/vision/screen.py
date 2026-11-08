"""Screenshots of this computer's screen for Ohm to look at (the vision module, opt-in in the controller).

A screenshot is taken only for the request that needs it and goes straight to the local llama-server as a
data URL. Nothing is written to disk or logged.
"""
from controller import modules
from utils.Vision import image_url

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

SCREEN_SIDE = 1600  # bigger than chat pictures so text on the screen stays readable


def on() -> bool:
    return modules.enabled("vision") and ImageGrab is not None


def health() -> tuple[bool, str]:
    if ImageGrab is None:
        return False, "Pillow isn't installed, so the screen can't be captured"
    return True, "Looks at the screen when someone asks about it"


def capture() -> str | None:
    """The primary monitor as a JPEG data URL, or None if the vision module is off or capture fails."""
    if not on():
        return None
    try:
        return image_url(ImageGrab.grab(), max_side=SCREEN_SIDE)
    except Exception as e:  # e.g. a locked screen or a secure desktop (UAC prompt)
        print(f"[vision] Couldn't capture the screen: {e}")
        return None
