"""Screenshots of this computer's screen for Ohm to look at (the vision module, opt-in in the controller).

A screenshot is taken only for the request that needs it, cropped to the window in front (or to what just changed,
see watch.py), and goes straight to the local llama-server as a data URL, with the window's title and the text
Windows' OCR reads on it (ocr.py) in the prompt. Nothing is written to disk or logged.
Screenshots stay on this PC. With OHM_SCREEN_CLOUD=1 (Settings › Integrations) only the screen-description job
sends a cropped screenshot to the screen role's backend (Codex by default); live replies still use the local model.
(That job is describe.py.)
"""
from concurrent.futures import ThreadPoolExecutor

from controller import modules
from modules.vision import ocr, window
from utils.Vision import image_url

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

SCREEN_SIDE = 1600   # bigger than chat pictures so text on the screen stays readable
MIN_REGION = 64      # a crop smaller than this on either side is a minimized or hidden window, not something to see
OCR_WAIT_S = 0.3     # the most a reply waits for OCR after the screenshot is encoded; it takes ~50-180 ms
TEXT_MAX = 300       # characters of OCR text in the prompt line, so a page of text can't crowd out the chat

_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")  # ? one at a time; a slow read never piles up


def on() -> bool:
    return modules.enabled("vision") and ImageGrab is not None


def health() -> tuple[bool, str]:
    if ImageGrab is None:
        return False, "Pillow isn't installed, so the screen can't be captured"
    if not ocr.available():
        return True, "Looks at the screen when someone asks about it, without reading its text: " + ocr.health()[1]
    return True, "Looks at the screen when someone asks about it, and reads the text on it"


def clamp(region, bounds) -> tuple[int, int, int, int] | None:
    """`region` (left, top, right, bottom) cut down to `bounds`, or None when too little of it is left."""
    if not region or not bounds:
        return None
    left, top = max(region[0], bounds[0]), max(region[1], bounds[1])
    right, bottom = min(region[2], bounds[2]), min(region[3], bounds[3])
    if right - left < MIN_REGION or bottom - top < MIN_REGION:
        return None
    return int(left), int(top), int(right), int(bottom)


def window_region(win, screens) -> tuple[int, int, int, int] | None:
    """The window's own rect when it's smaller than the primary screen (a windowed game, a browser), else None
    (full screen: take the whole screen like before). `screens`: window.screens()."""
    if not win or not win.get("rect") or not screens:
        return None
    (bounds, (width, height)), rect = screens, win["rect"]
    if rect[2] - rect[0] >= width and rect[3] - rect[1] >= height:
        return None
    return clamp(rect, bounds)


def grab(region=None, win=None):
    """The screen as a PIL image at full resolution, or None if the vision module is off or capture fails.
    image_url(image, max_side=SCREEN_SIDE) shrinks it for the model; OCR reads it bigger.
    region: (left, top, right, bottom) in screen pixels, any monitor; by default the window in front when it
    doesn't fill the screen, else the whole primary monitor. win: window.active(), when it's already known."""
    if not on():
        return None
    try:
        screens = window.screens()
        region = clamp(region, screens[0] if screens else None) or window_region(win or window.active(), screens)
        if not region:
            return ImageGrab.grab()
        # ? all_screens grabs every monitor and then crops, ~30 ms more, so only when it's off the primary one
        width, height = screens[1]
        off_primary = region[0] < 0 or region[1] < 0 or region[2] > width or region[3] > height
        return ImageGrab.grab(bbox=region, all_screens=off_primary)
    except Exception as e:  # e.g. a locked screen or a secure desktop (UAC prompt)
        print(f"[vision] Couldn't capture the screen: {e}")
        return None


def capture(region=None) -> str | None:
    """The screen (see grab) as a JPEG data URL no bigger than SCREEN_SIDE, or None."""
    image = grab(region)
    return image_url(image, max_side=SCREEN_SIDE) if image is not None else None


def look(region=None) -> tuple[str | None, str]:
    """A screenshot to show the local model (see grab), and the line for the prompt saying what's on it
    (seen_line): (None, "") when there's no screenshot. OCR runs while the screenshot is encoded, and is left
    out if it isn't done OCR_WAIT_S after that."""
    win = window.active()
    image = grab(region, win)
    if image is None:
        return None, ""
    reading = _ocr_pool.submit(ocr.text, image) if ocr.available() else None
    url = image_url(image, max_side=SCREEN_SIDE)
    try:
        lines = reading.result(timeout=OCR_WAIT_S) if reading else []
    except TimeoutError:
        print("[vision] OCR took too long, replying without the text on the screen")
        lines = []
    return url, seen_line(window.describe(win), lines)


def seen_line(app: str, lines) -> str:
    """'On their screen right now: <app>. Text on it: "line" · "line"', just the first part without text,
    or "" when neither is known."""
    text = ""
    for line in lines or []:
        part = f'"{line}"'
        if len(text) + len(part) > TEXT_MAX:
            break
        text = f"{text} · {part}" if text else part
    if app and text:
        return f"On their screen right now: {app}. Text on it: {text}"
    if app:
        return f"On their screen right now: {app}."
    return f"Text on their screen right now: {text}" if text else ""
